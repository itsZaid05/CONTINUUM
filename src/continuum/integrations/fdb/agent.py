"""FDB-managed LiveKit voice worker using Gemini native audio."""

from __future__ import annotations

from typing import Any

from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession

from .adapter import LiveKitSessionAdapter
from .backend import FdbMockBackend
from .config import FdbAgentConfig
from .livekit_tools import make_livekit_tools
from .provider import selected_provider
from .telemetry import JsonlTelemetry
from .tool_bridge import FdbToolBridge

FDB_AGENT_INSTRUCTIONS = """
You are CONTINUUM, a concise voice assistant in the authorized FDB-v3 simulated test environment.
You have exactly twelve supplied tools across travel, finance, housing, and ecommerce.

Treat the user's complete, finalized utterance as authoritative. Resolve false starts and self-
corrections to the latest stated value before calling anything. Never dispatch from a partial
hypothesis. Call every supported tool the user actually requests, exactly once and with no extra
calls. Separate multiple requests even when they use the same tool. For a dependency chain, wait
for the producer result and pass the exact returned identifier to the consumer. Respect conditions
stated by the user and do not invent API values. These simulated mutations are pre-authorized, so
do not ask for confirmation. If a requested capability has no supplied tool, say so briefly while
still completing supported requests. After tools finish, summarize only verified results in a short,
natural spoken response.
""".strip()


class FdbVoiceAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=FDB_AGENT_INSTRUCTIONS)


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext) -> None:
    config = FdbAgentConfig.from_env()
    provider = selected_provider(config.provider)
    model = provider.build_model(config.native_audio())
    room_name = ctx.room.name

    telemetry = JsonlTelemetry(
        room_name=room_name,
        path=config.telemetry_path,
        official_path=config.official_tool_log_path,
    )
    telemetry.heartbeat()
    telemetry.emit(
        "session_started",
        provider=provider.name,
        model=config.model,
        latency_profile=config.latency_profile,
    )
    backend = FdbMockBackend(config.latency_profile)
    bridge = FdbToolBridge(backend, telemetry, room_name=room_name)
    adapter = LiveKitSessionAdapter(bridge, telemetry)
    tools = make_livekit_tools(bridge)
    session: AgentSession[dict[str, Any]] = AgentSession(
        llm=model,
        tools=tools,
        max_tool_steps=8,
        allow_interruptions=True,
    )
    adapter.attach(session)

    async def shutdown(reason: str) -> None:
        telemetry.emit("session_stopping", reason=reason)
        await adapter.close(timeout_s=config.shutdown_timeout_s)

    ctx.add_shutdown_callback(shutdown)
    await session.start(room=ctx.room, agent=FdbVoiceAgent())
    telemetry.emit("session_listening")

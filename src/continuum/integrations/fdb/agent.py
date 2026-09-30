"""FDB-managed LiveKit voice worker using Gemini native audio."""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path
from typing import Any

from livekit import agents, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ModelSettings,
    RoomInputOptions,
    RoomOutputOptions,
)

from .adapter import LiveKitSessionAdapter
from .backend import FdbMockBackend
from .config import FdbAgentConfig
from .livekit_tools import make_livekit_tools
from .media import Pcm16WavRecorder
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
    def __init__(
        self, recorder: Pcm16WavRecorder | None = None, telemetry: JsonlTelemetry | None = None
    ) -> None:
        super().__init__(instructions=FDB_AGENT_INSTRUCTIONS)
        self._recorder = recorder
        self._telemetry = telemetry

    async def transcription_node(
        self, text: AsyncIterable[Any], model_settings: ModelSettings
    ) -> AsyncIterator[Any]:
        async for delta in text:
            if self._telemetry is not None:
                self._telemetry.emit(
                    "provider_output_audio_transcription",
                    schema="continuum.media.v1",
                    kind="transcript",
                    direction="output",
                    text=str(delta),
                    final=False,
                    observed_at=time.time(),
                    source="gemini_native_audio",
                    start_time=getattr(delta, "start_time", None),
                    end_time=getattr(delta, "end_time", None),
                    confidence=getattr(delta, "confidence", None),
                    timing_source=(
                        "provider_alignment"
                        if getattr(delta, "start_time", None) is not None
                        else "event_observation"
                    ),
                )
            yield delta

    async def realtime_audio_output_node(
        self, audio: AsyncIterable[rtc.AudioFrame], model_settings: ModelSettings
    ) -> AsyncIterator[rtc.AudioFrame]:
        async for frame in audio:
            if self._recorder is not None:
                self._recorder.write(
                    bytes(frame.data),
                    sample_rate=frame.sample_rate,
                    channels=frame.num_channels,
                    samples_per_channel=frame.samples_per_channel,
                )
            if self._telemetry is not None:
                self._telemetry.emit(
                    "output_audio_frame",
                    sample_rate=frame.sample_rate,
                    channels=frame.num_channels,
                    samples_per_channel=frame.samples_per_channel,
                    provenance="provider_output_before_room_playout",
                )
            yield frame


server = AgentServer()


class TelemetryVideoSampler:
    def __init__(
        self, telemetry: JsonlTelemetry, *, speaking_fps: float, silent_fps: float
    ) -> None:
        from livekit.agents import VoiceActivityVideoSampler

        self._delegate = VoiceActivityVideoSampler(
            speaking_fps=speaking_fps, silent_fps=silent_fps
        )
        self._telemetry = telemetry

    def __call__(self, frame: rtc.VideoFrame, session: Any) -> bool:
        sampled = self._delegate(frame, session)
        self._telemetry.emit(
            "sampled_video",
            width=frame.width,
            height=frame.height,
            sampled=sampled,
        )
        return sampled


# Keep the production worker unnamed deliberately. The pinned upstream
# ``livekit_inference.py`` creates/joins rooms without named dispatches, so an
# unnamed AgentServer receives automatic jobs. The Phase 3 smoke process sets a
# private environment override before importing this module; it is then
# explicitly dispatched to its pre-created room. Both modes share this exact
# entrypoint and tool/runtime implementation.
_dispatch_name = os.getenv("CONTINUUM_FDB_AGENT_NAME", "").strip()


async def _start_session_and_connect(
    ctx: agents.JobContext,
    session: AgentSession[dict[str, Any]],
    *,
    agent: FdbVoiceAgent,
    room_input_options: RoomInputOptions,
    room_output_options: RoomOutputOptions,
    telemetry: JsonlTelemetry,
) -> None:
    """Start room I/O before joining the assigned LiveKit room.

    Keeping this small lifecycle boundary explicit protects the required order:
    room handlers are installed by ``session.start`` before the worker connects,
    so early caller tracks cannot be missed.  ``JobContext.connect`` is
    idempotent in the pinned Agents release and is also required by newer
    releases where session startup no longer joins automatically.
    """
    telemetry.emit("session_starting")
    try:
        await session.start(
            room=ctx.room,
            agent=agent,
            room_input_options=room_input_options,
            room_output_options=room_output_options,
        )
    except Exception as exc:
        # Keep the Phase 3 report useful without serializing provider or
        # transport exception text, which can contain sensitive endpoints.
        telemetry.emit("session_start_failed", error_type=type(exc).__name__)
        raise

    telemetry.emit("session_started")
    telemetry.emit("session_connecting")
    try:
        await ctx.connect()
    except Exception as exc:
        telemetry.emit("session_connect_failed", error_type=type(exc).__name__)
        raise
    telemetry.emit("session_listening")


@server.rtc_session(agent_name=_dispatch_name)
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
    # This records successful configuration/model construction only.  The
    # separate lifecycle events below distinguish an initialization failure
    # from session startup or room-join failures in sanitized smoke evidence.
    telemetry.emit(
        "session_initializing",
        provider=provider.name,
        model=config.model,
        latency_profile=config.latency_profile,
    )
    backend = FdbMockBackend(config.latency_profile)
    bridge = FdbToolBridge(backend, telemetry, room_name=room_name)
    adapter = LiveKitSessionAdapter(bridge, telemetry)
    recorder = None
    if config.media.record_output_wav:
        safe_room = "".join(char if char.isalnum() or char in "-_" else "_" for char in room_name)
        recorder = Pcm16WavRecorder(
            Path(config.media.artifact_dir) / f"{safe_room}-provider-output.wav",
            sample_rate=config.media.output_sample_rate,
            channels=config.media.output_channels,
        )
    tools = make_livekit_tools(bridge)
    session: AgentSession[dict[str, Any]] = AgentSession(
        llm=model,
        tools=tools,
        max_tool_steps=8,
        turn_detection="realtime_llm",
        allow_interruptions=config.media.interruptions,
        min_interruption_duration=config.media.min_interruption_duration,
        false_interruption_timeout=config.media.false_interruption_timeout,
        resume_false_interruption=True,
        video_sampler=(
            TelemetryVideoSampler(
                telemetry,
                speaking_fps=config.media.speaking_video_fps,
                silent_fps=config.media.silent_video_fps,
            )
            if config.media.video_enabled
            else None
        ),
    )
    adapter.attach(session)

    async def shutdown(reason: str) -> None:
        telemetry.emit("session_stopping", reason=reason)
        await adapter.close(timeout_s=config.shutdown_timeout_s)
        if recorder is not None:
            telemetry.emit("media_artifact", **recorder.close())

    ctx.add_shutdown_callback(shutdown)
    await _start_session_and_connect(
        ctx,
        session,
        agent=FdbVoiceAgent(recorder, telemetry),
        room_input_options=RoomInputOptions(
            audio_enabled=True,
            video_enabled=config.media.video_enabled,
            audio_sample_rate=config.media.input_sample_rate,
            audio_num_channels=config.media.input_channels,
            audio_frame_size_ms=config.media.frame_size_ms,
        ),
        room_output_options=RoomOutputOptions(
            audio_enabled=True,
            transcription_enabled=True,
            sync_transcription=config.media.synchronized_transcription,
            audio_sample_rate=config.media.output_sample_rate,
            audio_num_channels=config.media.output_channels,
        ),
        telemetry=telemetry,
    )

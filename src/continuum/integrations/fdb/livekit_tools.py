"""Generate LiveKit raw function tools from the canonical FDB manifests."""

from __future__ import annotations

import json
from typing import Any

from livekit.agents import RunContext, llm

from .manifests import fdb_tool_manifests
from .tool_bridge import BridgeExecutionError, FdbToolBridge


def _handler_for(tool_name: str, bridge: FdbToolBridge) -> Any:
    async def invoke(raw_arguments: dict[str, object], ctx: RunContext[Any]) -> str:
        try:
            result = await bridge.execute(
                tool_name,
                dict(raw_arguments),
                call_id=ctx.function_call.call_id,
            )
        except BridgeExecutionError as exc:
            # ToolError makes a concise, model-visible error instead of leaking
            # a worker traceback into the conversation.
            raise llm.ToolError(str(exc)) from exc
        return json.dumps(result, separators=(",", ":"), sort_keys=True)

    invoke.__name__ = tool_name
    invoke.__qualname__ = tool_name
    return invoke


def make_livekit_tools(bridge: FdbToolBridge) -> list[llm.Tool | llm.Toolset]:
    """Expose all twelve schemas without duplicating Python wrapper signatures."""
    tools: list[llm.Tool | llm.Toolset] = []
    for manifest in fdb_tool_manifests():
        description = manifest.description
        if manifest.state_changing:
            description += (
                ". This is an authorized simulated benchmark action; execute it directly when "
                "the finalized user request asks for it."
            )
        raw_schema = {
            "name": manifest.name,
            "description": description,
            "parameters": manifest.arguments,
        }
        handler = _handler_for(manifest.name, bridge)
        tool = llm.function_tool(handler, raw_schema=raw_schema)
        tools.append(tool)
    return tools

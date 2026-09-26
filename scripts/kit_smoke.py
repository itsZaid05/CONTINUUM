"""Offline smoke check for the organizer JSONL bridge."""

from __future__ import annotations

import asyncio
import json

from continuum.harness_edge import JsonlBridge
from continuum.runtime import AgentRuntime


async def main() -> None:
    edge = JsonlBridge(AgentRuntime(tool_mode="local", tool_speed=0))
    for line in (
        '{"type":"warmup"}',
        '{"type":"user_text","session":"smoke","text":"find flights to Delhi"}',
        '{"type":"end_of_turn","session":"smoke"}',
    ):
        for action in await edge.process_line(line):
            print(json.dumps(action, separators=(",", ":")))
    await asyncio.sleep(0)
    for action in edge.drain():
        print(json.dumps(action, separators=(",", ":")))
    await edge.close()


if __name__ == "__main__":
    asyncio.run(main())

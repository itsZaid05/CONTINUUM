"""JSONL telemetry compatible with FDB's runner plus richer audit events."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class JsonlTelemetry:
    """Synchronous, process-local atomic JSONL writer.

    LiveKit callbacks are synchronous while tool handlers are asynchronous, so
    one lock protects both call sites.  Each append is a complete line and is
    flushed before returning; benchmark subprocesses can safely tail the file.
    """

    def __init__(
        self,
        *,
        room_name: str,
        path: str | Path = "/tmp/continuum_fdb_telemetry.jsonl",
        official_path: str | Path = "/tmp/agent_tool_calls.log",
        heartbeat_path: str | Path = "/tmp/agent_heartbeat.log",
    ) -> None:
        self.room_name = room_name
        self.path = Path(path)
        self.official_path = Path(official_path)
        self.heartbeat_path = Path(heartbeat_path)
        self._lock = threading.Lock()

    def emit(self, event: str, **data: Any) -> None:
        payload = {
            "timestamp": time.time(),
            "room": self.room_name,
            "event": event,
            **data,
        }
        self._append(self.path, payload)

    def official_tool_call(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        started_at: float,
        finished_at: float,
    ) -> None:
        # Keep the exact shape consumed by upstream run_tool_benchmark.py.
        payload = {
            "room": self.room_name,
            "call": {
                "function": tool,
                "args": args,
                "timestamp_start": started_at,
                "timestamp_end": finished_at,
            },
        }
        self._append(self.official_path, payload)

    def heartbeat(self) -> None:
        self._append_text(
            self.heartbeat_path,
            f"!!! AGENT JOINING ROOM: {self.room_name} at {time.ctime()} !!!",
        )

    def latency_breakdown(
        self,
        *,
        user_ended_at: float,
        tool_started_at: float | None,
        tool_finished_at: float | None,
        agent_started_at: float,
        tool: str,
    ) -> None:
        reasoning = max(0.0, (tool_started_at or agent_started_at) - user_ended_at)
        execution = (
            max(0.0, tool_finished_at - tool_started_at)
            if tool_started_at is not None and tool_finished_at is not None
            else 0.0
        )
        synthesis_base = tool_finished_at or user_ended_at
        metrics = {
            "room": self.room_name,
            "tool": tool,
            "reasoning": round(reasoning, 3),
            "execution": round(execution, 3),
            "synthesis": round(max(0.0, agent_started_at - synthesis_base), 3),
            "total": round(max(0.0, agent_started_at - user_ended_at), 3),
            "agent_start_at": agent_started_at,
        }
        self._append_text(
            self.heartbeat_path,
            "LATENCY_TRACK_JSON: " + json.dumps(metrics, separators=(",", ":")),
        )
        self.emit("latency_breakdown", **metrics)

    def _append(self, path: Path, payload: dict[str, Any]) -> None:
        self._append_text(
            path,
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
        )

    def _append_text(self, path: Path, line: str) -> None:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
                stream.flush()

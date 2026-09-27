"""Organizer-facing JSONL edge for :class:`continuum.runtime.AgentRuntime`.

The core runtime deliberately speaks a small canonical protocol.  This module
accepts common kit spellings at the process boundary, decodes binary payloads,
and writes one JSON action per line.  Malformed input becomes a clarification
rather than terminating the agent process.

The edge has no network dependency.  ``warmup`` exercises deterministic code
paths only, and optional ASR is loaded from an explicit local directory by the
perception layer.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import sys
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any, TextIO

from pydantic import ValidationError

from .runtime import (
    AgentRuntime,
    ClarifyAction,
    EventType,
    RuntimeAction,
    RuntimeEvent,
)

_TYPE_ALIASES = {
    "text": "text",
    "text_chunk": "text",
    "user_text": "text",
    "user_message": "text",
    "user_utterance": "text",
    "transcript": "text",
    "message": "text",
    "eot": "eot",
    "end_of_turn": "eot",
    "end_turn": "eot",
    "turn_end": "eot",
    "end": "eot",
    "audio": "audio",
    "audio_chunk": "audio",
    "audio_clip": "audio",
    "speech": "audio",
    "frame": "frame",
    "video_frame": "frame",
    "video": "frame",
    "image": "frame",
    "vision": "frame",
    "interrupt": "interrupt",
    "interruption": "interrupt",
    "barge_in": "interrupt",
    "cancel": "interrupt",
    "tool_result": "tool_result",
    "tool_response": "tool_result",
    "tool_output": "tool_result",
    "function_result": "tool_result",
    "manifest": "manifest",
    "manifests": "manifest",
    "tool_manifest": "manifest",
    "tools_manifest": "manifest",
    "tools": "manifest",
}


def _first(data: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return None


def _event_type(raw: Mapping[str, Any]) -> str:
    value = _first(raw, "type", "event_type", "kind", "event")
    if isinstance(value, Mapping):
        value = _first(value, "type", "event_type", "kind")
    if not isinstance(value, str):
        # Manifest catalogues are often sent as a bare ``{"tools": [...]}``.
        if "tools" in raw or "manifests" in raw or "manifest" in raw:
            return "manifest"
        raise ValueError("event type is required")
    key = value.strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return _TYPE_ALIASES[key]
    except KeyError as exc:
        raise ValueError(f"unsupported event type {value!r}") from exc


def _decode_bytes(value: Any, *, field: str) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, list) and all(
        isinstance(item, int) and 0 <= item <= 255 for item in value
    ):
        return bytes(value)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be base64 text or a byte array")
    encoded = value
    if value.startswith("data:"):
        try:
            encoded = value.split(",", 1)[1]
        except IndexError as exc:
            raise ValueError(f"invalid data URI in {field}") from exc
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{field} is not valid base64") from exc


def _normalize_manifest(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("each tool manifest must be an object")
    out = dict(raw)
    function = out.get("function")
    if isinstance(function, Mapping):
        out.update(function)
    if "name" not in out:
        out["name"] = _first(out, "tool", "tool_name", "function")
    if "arguments" not in out:
        schema = _first(out, "input_schema", "parameters", "args_schema")
        if schema is not None:
            out["arguments"] = schema
    if "returns" not in out:
        schema = _first(out, "output_schema", "result_schema", "return_schema")
        if schema is not None:
            out["returns"] = schema
    if "mutation_class" not in out:
        tier = _first(out, "risk", "risk_level", "effect", "mutation")
        if tier is not None:
            out["mutation_class"] = tier
    return out


def normalize_event(raw: Mapping[str, Any], *, default_session: str = "default") -> RuntimeEvent:
    """Normalize organizer/kit aliases into a validated runtime event."""
    if not isinstance(raw, Mapping):
        raise ValueError("event must be a JSON object")
    envelope = raw.get("event")
    if isinstance(envelope, Mapping):
        merged: dict[str, Any] = dict(raw)
        merged.pop("event", None)
        merged.update(envelope)
        raw = merged

    event_type = _event_type(raw)
    content = raw.get("content", raw.get("payload"))
    content_map = content if isinstance(content, Mapping) else {}
    text = _first(raw, "text", "transcript", "ocr_text", "utterance", "message")
    if text is None:
        text = _first(content_map, "text", "transcript", "ocr_text")
    if text is None and isinstance(content, str) and event_type in {"text", "audio", "frame"}:
        text = content

    data_value = _first(
        raw,
        "data",
        "data_base64",
        "audio_base64",
        "image_base64",
        "frame_base64",
        "audio",
        "image",
        "frame",
    )
    if data_value is None:
        data_value = _first(content_map, "data", "base64", "bytes")
    data = _decode_bytes(data_value, field=f"{event_type} data") if data_value is not None else None

    manifests_raw: Any = _first(raw, "manifests", "tools")
    manifest_raw: Any = raw.get("manifest")
    if event_type == "manifest" and manifests_raw is None and manifest_raw is None:
        candidate = raw.get("tool")
        if isinstance(candidate, Mapping):
            manifest_raw = candidate
    manifests = None
    manifest = None
    if manifests_raw is not None:
        if not isinstance(manifests_raw, list):
            manifests_raw = [manifests_raw]
        manifests = [_normalize_manifest(item) for item in manifests_raw]
    if manifest_raw is not None:
        manifest = _normalize_manifest(manifest_raw)

    result = _first(raw, "result", "output", "response")
    if event_type == "tool_result" and result is None:
        omitted = {
            "type", "event_type", "kind", "session", "session_id", "sessionId",
            "call_id", "callId", "tool_call_id", "id", "timestamp", "timestamp_ms", "ts_ms",
        }
        result = {key: value for key, value in raw.items() if key not in omitted}
    if result is not None and not isinstance(result, Mapping):
        result = {"result": result}

    payload: dict[str, Any] = {
        "session_id": str(
            _first(raw, "session_id", "session", "sessionId", "conversation_id", "scenario_id")
            or default_session
        ),
        "event_id": _first(raw, "event_id", "eventId", "id") or None,
        "type": EventType(event_type),
        "ts_ms": _first(raw, "ts_ms", "timestamp_ms", "timestamp", "at_ms"),
        "text": str(text) if text is not None else None,
        "text_mode": _first(raw, "text_mode", "transcript_mode", "update_mode") or "append",
        "is_final": bool(_first(raw, "is_final", "final", "transcript_final") or False),
        "data": data,
        "confidence": _first(raw, "confidence", "asr_confidence", "ocr_confidence"),
        "call_id": _first(
            raw,
            "call_id",
            "callId",
            "tool_call_id",
            "toolCallId",
            "target_call_id",
            *(("id",) if event_type in {"tool_result", "interrupt"} else ()),
        ),
        "result": dict(result) if isinstance(result, Mapping) else None,
        "manifest": manifest,
        "manifests": manifests,
    }
    # ``None`` must not suppress RuntimeEvent's event-id factory.
    if payload["event_id"] is None:
        payload.pop("event_id")
    return RuntimeEvent.model_validate(payload)


def serialize_action(action: RuntimeAction) -> dict[str, Any]:
    """Return a compact JSON-compatible action with its emission timestamp."""
    return action.model_dump(mode="json", exclude_none=True)


class JsonlBridge:
    """Stateful adapter used by the CLI and deterministic tests."""

    def __init__(
        self,
        runtime: AgentRuntime | None = None,
        *,
        default_session: str = "default",
        watchdog_s: float = 120.0,
    ) -> None:
        self.runtime = runtime or AgentRuntime(tool_mode="external")
        self.default_session = default_session
        self.watchdog_s = watchdog_s
        self.sessions: set[str] = set()

    def drain(self) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        while not self.runtime.output_queue.empty():
            actions.append(serialize_action(self.runtime.output_queue.get_nowait()))
        return actions

    async def warmup(self) -> dict[str, Any]:
        report = await self.runtime.warmup()
        return {"type": "ready", "ts_ms": round(self.runtime.now_ms(), 3), **report}

    async def process(
        self, raw: Mapping[str, Any], *, drain: bool = True
    ) -> list[dict[str, Any]]:
        """Process one decoded object, returning immediately available actions.

        ``serve_stdio`` sets ``drain=False`` because its dedicated output pump
        owns the runtime queue and can stream actions that arrive between input
        lines. Programmatic callers keep the convenient default.
        """
        kind = (
            str(_first(raw, "type", "event_type", "kind") or "")
            .lower()
            .replace("-", "_")
            .replace(" ", "_")
        )
        if kind in {"warmup", "warm_up", "setup", "ready"}:
            return [await self.warmup()]
        try:
            event = normalize_event(raw, default_session=self.default_session)
            self.sessions.add(event.session_id)
            await self.runtime.handle(event)
            # Completion of a zero-delay local tool or an external result is a
            # scheduled task; two checkpoints make its final observable.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return self.drain() if drain else []
        except (ValueError, TypeError, ValidationError) as exc:
            action = ClarifyAction(
                text=f"Malformed input: {exc}",
                ts_ms=round(self.runtime.now_ms(), 3),
            )
            return [serialize_action(action)]

    async def process_line(
        self, line: str, *, drain: bool = True
    ) -> list[dict[str, Any]]:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            action = ClarifyAction(
                text=f"Malformed JSON at column {exc.colno}.",
                ts_ms=round(self.runtime.now_ms(), 3),
            )
            return [serialize_action(action)]
        if not isinstance(raw, Mapping):
            action = ClarifyAction(
                text="Malformed input: each JSONL line must be an object.",
                ts_ms=round(self.runtime.now_ms(), 3),
            )
            return [serialize_action(action)]
        return await self.process(raw, drain=drain)

    async def watchdog(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """Wait for work and guarantee a terminal action if none was produced."""
        sid = session_id or self.default_session
        idle = await self.runtime.wait_idle(sid, self.watchdog_s)
        already_final = any(
            row["session"] == sid and row["event"] in {"final", "watchdog_final"}
            for row in self.runtime.trace
        )
        if not already_final:
            reason = (
                "scenario ended without a final"
                if idle
                else f"{self.watchdog_s:g}s time limit"
            )
            await self.runtime.watchdog_final(sid, reason=reason)
        return self.drain()

    async def close(self) -> list[dict[str, Any]]:
        await self.runtime.close()
        await asyncio.sleep(0)
        return self.drain()


# Friendly aliases for callers that name the boundary after the organizer.
HarnessEdge = JsonlBridge
OrganizerBridge = JsonlBridge


async def iter_jsonl(
    lines: Iterable[str], bridge: JsonlBridge | None = None
) -> AsyncIterator[dict[str, Any]]:
    """Deterministic in-memory JSONL runner used by tests and integrations."""
    edge = bridge or JsonlBridge()
    for line in lines:
        if not line.strip():
            continue
        for action in await edge.process_line(line):
            yield action


async def serve_stdio(
    *,
    bridge: JsonlBridge | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> None:
    """Read JSONL from stdin and stream asynchronous actions to stdout."""
    edge = bridge or JsonlBridge()
    source = stdin or sys.stdin
    sink = stdout or sys.stdout
    write_lock = asyncio.Lock()
    deadlines: dict[str, asyncio.Task[None]] = {}

    async def write(actions: list[dict[str, Any]]) -> None:
        if not actions:
            return
        async with write_lock:
            for action in actions:
                sink.write(json.dumps(action, separators=(",", ":"), ensure_ascii=False) + "\n")
            sink.flush()

    async def pump() -> None:
        while True:
            action = await edge.runtime.output_queue.get()
            try:
                await write([serialize_action(action)])
            finally:
                edge.runtime.output_queue.task_done()

    async def expire(session_id: str) -> None:
        await asyncio.sleep(edge.watchdog_s)
        already_final = any(
            row["session"] == session_id and row["event"] in {"final", "watchdog_final"}
            for row in edge.runtime.trace
        )
        if not already_final:
            await edge.runtime.watchdog_final(
                session_id, reason=f"{edge.watchdog_s:g}s time limit"
            )

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            line = await asyncio.to_thread(source.readline)
            if line == "":
                break
            if not line.strip():
                continue
            before = set(edge.sessions)
            immediate = await edge.process_line(line, drain=False)
            await write(immediate)
            for session_id in edge.sessions - before:
                deadlines[session_id] = asyncio.create_task(expire(session_id))
        if edge.runtime.tool_mode == "local":
            # A piped local smoke run closes stdin immediately after EOT. Give
            # deterministic local work a bounded chance to produce its final.
            for session_id in edge.sessions:
                await edge.runtime.wait_idle(session_id, min(edge.watchdog_s, 5.0))
        for session_id in edge.sessions:
            already_final = any(
                row["session"] == session_id and row["event"] in {"final", "watchdog_final"}
                for row in edge.runtime.trace
            )
            if not already_final:
                await edge.runtime.watchdog_final(session_id, reason="input stream ended")
        await edge.runtime.output_queue.join()
    finally:
        for task in deadlines.values():
            task.cancel()
        await edge.runtime.close()
        await edge.runtime.output_queue.join()
        pump_task.cancel()
        await asyncio.gather(pump_task, *deadlines.values(), return_exceptions=True)

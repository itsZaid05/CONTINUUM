"""Organizer JSONL edge, aliases, malformed input, and watchdog coverage."""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from continuum.harness_edge import JsonlBridge, iter_jsonl, normalize_event, serialize_action
from continuum.runtime import AgentRuntime, ClarifyAction, EventType
from continuum.tools import ToolManifest, ToolRegistry


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_normalize_user_text_alias() -> None:
    event = normalize_event({"type": "user_text", "session": "s", "content": "hello"})
    assert event.type == EventType.TEXT and event.text == "hello" and event.session_id == "s"


def test_normalize_end_turn_envelope() -> None:
    event = normalize_event({"sessionId": "s", "event": {"kind": "end-of-turn"}})
    assert event.type == EventType.EOT and event.session_id == "s"


def test_normalize_audio_base64_and_confidence() -> None:
    raw = base64.b64encode(b"RIFF").decode()
    event = normalize_event(
        {"type": "audio_chunk", "audio_base64": raw, "transcript": "book it", "asr_confidence": 0.8}
    )
    assert event.type == EventType.AUDIO and event.data == b"RIFF"
    assert event.text == "book it" and event.confidence == 0.8


def test_normalize_frame_byte_array() -> None:
    event = normalize_event({"type": "image", "data": [137, 80, 78, 71], "ocr_text": "E4"})
    assert event.type == EventType.FRAME and event.data == b"\x89PNG" and event.text == "E4"


def test_manifest_schema_aliases_are_normalized() -> None:
    event = normalize_event(
        {
            "type": "tool_manifest",
            "manifest": {
                "tool_name": "lookup",
                "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
                "output_schema": {"properties": {"answer": {"type": "string"}}},
                "read_only": True,
            },
        }
    )
    assert event.manifest is not None
    assert event.manifest.name == "lookup" and "q" in event.manifest.properties
    assert event.manifest.return_fields == ["answer"]


def test_bare_tools_catalogue_is_a_manifest_event() -> None:
    event = normalize_event({"tools": [{"name": "lookup"}]})
    assert event.type == EventType.MANIFEST
    assert event.manifests is not None and event.manifests[0].name == "lookup"


def test_tool_response_alias_completes_external_call() -> None:
    async def check() -> None:
        edge = JsonlBridge(AgentRuntime(tool_mode="external", tool_speed=0))
        await edge.process({"type": "user_text", "session": "s", "text": "find flights to Delhi"})
        actions = await edge.process({"type": "turn_end", "session": "s"})
        call = next(action for action in actions if action["type"] == "tool_call")
        actions = await edge.process(
            {
                "type": "tool_response",
                "session": "s",
                "toolCallId": call["call_id"],
                "output": {"flight_id": "FL-1", "to": "Delhi"},
            }
        )
        assert any(action["type"] == "final" and "FL-1" in action["text"] for action in actions)

    run(check())


def test_malformed_json_becomes_clarification() -> None:
    actions = run(JsonlBridge().process_line("{bad"))
    assert actions[0]["type"] == "clarify" and "Malformed JSON" in actions[0]["text"]


def test_non_object_json_becomes_clarification() -> None:
    actions = run(JsonlBridge().process_line("[]"))
    assert actions[0]["type"] == "clarify" and "must be an object" in actions[0]["text"]


def test_unknown_event_becomes_clarification() -> None:
    actions = run(JsonlBridge().process({"type": "telepathy"}))
    assert actions[0]["type"] == "clarify" and "unsupported event" in actions[0]["text"]


def test_bad_base64_becomes_clarification() -> None:
    actions = run(JsonlBridge().process({"type": "audio", "data_base64": "%%%"}))
    assert actions[0]["type"] == "clarify" and "base64" in actions[0]["text"]


def test_warmup_is_offline_and_ready() -> None:
    actions = run(JsonlBridge().process({"type": "warmup"}))
    assert actions == [actions[0]]
    assert actions[0]["type"] == "ready" and actions[0]["offline"] is True


def test_every_serialized_action_has_timestamp() -> None:
    action = ClarifyAction(text="say that again", ts_ms=1.25)
    assert serialize_action(action) == {"ts_ms": 1.25, "type": "clarify", "text": "say that again"}


def test_cancel_carries_snapshot_refreshed_by_pivot() -> None:
    async def check() -> None:
        registry = ToolRegistry(
            [
                ToolManifest(
                    name="search",
                    description="search flights to a city",
                    keywords=["flight"],
                    arguments={
                        "type": "object",
                        "properties": {"to": {"type": "string", "description": "destination city"}},
                        "required": ["to"],
                    },
                    delay_s=1,
                )
            ]
        )
        runtime = AgentRuntime(registry=registry)
        edge = JsonlBridge(runtime)
        await edge.process({"type": "text", "session": "s", "text": "find flights to Delhi"})
        await edge.process({"type": "eot", "session": "s"})
        await edge.process({"type": "text", "session": "s", "text": "actually Bangalore"})
        actions = await edge.process({"type": "eot", "session": "s"})
        cancel = next(action for action in actions if action["type"] == "cancel")
        assert cancel["snapshot"]["slots"]["to"] == "Bangalore"
        assert cancel["snapshot"]["intent"]["status"] == "interrupted"
        await runtime.close()

    run(check())


def test_watchdog_guarantees_truthful_final() -> None:
    async def check() -> None:
        edge = JsonlBridge(AgentRuntime(tool_mode="external"), watchdog_s=0.001)
        await edge.process({"type": "text", "session": "s", "text": "find flights to Delhi"})
        await edge.process({"type": "eot", "session": "s"})
        actions = await edge.watchdog("s")
        final = next(action for action in actions if action["type"] == "final")
        assert final["snapshot"]["intent"]["status"] == "timed_out"
        assert "no unconfirmed completion" in final["text"]

    run(check())


def test_iter_jsonl_skips_blanks_and_keeps_running_after_error() -> None:
    async def collect() -> list[dict[str, Any]]:
        return [item async for item in iter_jsonl(["\n", "{bad", json.dumps({"type": "warmup"})])]

    actions = run(collect())
    assert [action["type"] for action in actions] == ["clarify", "ready"]

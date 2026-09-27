"""Confidence-aware multimodal runtime and idempotent execution tests."""

from __future__ import annotations

import asyncio
import io
import struct
import wave
from typing import Any

from continuum._compat import UTC
from continuum.perception import perceive_audio, perceive_frame
from continuum.runtime import AgentRuntime, EventType, RuntimeEvent
from continuum.tools import MockToolSandbox, ToolManifest, ToolRegistry


def drain(runtime: AgentRuntime) -> list[Any]:
    out: list[Any] = []
    while not runtime.output_queue.empty():
        out.append(runtime.output_queue.get_nowait())
    return out


def wav_bytes() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(b"\0\0" * 16)
    return out.getvalue()


def png_with_text(text: str) -> bytes:
    body = b"Description\0" + text.encode("latin1")
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(body)) + b"tEXt" + body + b"\0\0\0\0"


def lookup_registry(delay: float = 0) -> ToolRegistry:
    return ToolRegistry(
        [
            ToolManifest(
                name="lookup",
                description="look up a manual",
                keywords=["manual", "error"],
                arguments={"type": "object"},
                returns={"properties": {"answer": {"type": "string"}}},
                delay_s=delay,
            )
        ]
    )


def test_audio_transcript_runs_through_planner() -> None:
    async def check() -> None:
        runtime = AgentRuntime(registry=lookup_registry(), tool_speed=0)
        await runtime.handle(
            RuntimeEvent(
                session_id="s",
                type=EventType.AUDIO,
                data=wav_bytes(),
                text="look up the manual",
                confidence=0.95,
            )
        )
        assert any(action.type == "tool_call" for action in drain(runtime))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert any(action.type == "final" for action in drain(runtime))

    asyncio.run(check())


def test_low_confidence_audio_clarifies_without_tool() -> None:
    async def check() -> None:
        runtime = AgentRuntime(registry=lookup_registry())
        await runtime.handle(
            RuntimeEvent(
                session_id="s",
                type=EventType.AUDIO,
                data=wav_bytes(),
                text="look up the manual",
                confidence=0.2,
            )
        )
        actions = drain(runtime)
        assert any(action.type == "clarify" for action in actions)
        assert not any(action.type == "tool_call" for action in actions)

    asyncio.run(check())


def test_frame_ocr_runs_through_planner() -> None:
    async def check() -> None:
        runtime = AgentRuntime(registry=lookup_registry(), tool_speed=0)
        await runtime.handle(
            RuntimeEvent(session_id="s", type=EventType.FRAME, data=png_with_text("look up manual E4"))
        )
        assert any(action.type == "tool_call" for action in drain(runtime))
        await runtime.close()

    asyncio.run(check())


def test_low_confidence_frame_clarifies_without_tool() -> None:
    result = perceive_frame(png_with_text("error E4"), confidence=0.3)
    assert result.render_provenance["grounded"] is False
    assert "ambiguous" in result.render_provenance


def test_raw_wav_has_zero_confidence_and_cannot_authorize() -> None:
    result = perceive_audio(wav_bytes())
    assert result.evidences[0].asr_confidence == 0.0
    assert result.render_provenance["grounded"] is False


def test_missing_local_asr_directory_is_offline_safe() -> None:
    result = perceive_audio(wav_bytes(), asr_model_path="/definitely/not/a/model")
    assert "ambiguous" in result.render_provenance
    assert result.evidences[0].source == "wav"


def test_upstream_audio_confidence_is_preserved() -> None:
    result = perceive_audio(wav_bytes(), transcript="find options", confidence=0.91)
    assert result.evidences[0].asr_confidence == 0.91
    assert result.evidences[0].source == "upstream-asr"


def test_grounded_final_names_the_confirmed_tool_call() -> None:
    async def check() -> None:
        runtime = AgentRuntime(registry=lookup_registry(), tool_speed=0)
        await runtime.handle(
            RuntimeEvent(session_id="s", type=EventType.FRAME, text="look up manual", confidence=0.9)
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        final = next(action for action in drain(runtime) if action.type == "final")
        assert final.snapshot.intent["grounded_by"]["tool"] == "lookup"
        assert final.snapshot.intent["grounded_by"]["call_id"]

    asyncio.run(check())


def test_text_turn_emits_only_one_filler() -> None:
    async def check() -> None:
        runtime = AgentRuntime(registry=lookup_registry(), tool_speed=0)
        await runtime.handle(RuntimeEvent(session_id="s", type=EventType.TEXT, text="look up manual"))
        assert drain(runtime) == []
        await runtime.handle(RuntimeEvent(session_id="s", type=EventType.EOT))
        actions = drain(runtime)
        assert sum(action.type == "speak" for action in actions) == 1
        await runtime.close()

    asyncio.run(check())


def test_concurrent_idempotency_key_dispatches_once() -> None:
    async def check() -> None:
        sandbox = MockToolSandbox(registry=lookup_registry(delay=0.01))
        first, second = await asyncio.gather(
            sandbox.call("lookup", {"q": "same"}, idempotency_key="effect-1"),
            sandbox.call("lookup", {"q": "same"}, idempotency_key="effect-1"),
        )
        assert first is second
        assert len(sandbox.dispatch_log) == 1

    asyncio.run(check())


def test_python_310_utc_compatibility_alias() -> None:
    assert UTC.utcoffset(None) is not None

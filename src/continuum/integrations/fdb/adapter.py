"""LiveKit session events mapped to CONTINUUM speech/lifecycle telemetry."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine
from typing import Any

from ...streaming import StreamingSpeechState
from .media import TranscriptEvent
from .telemetry import JsonlTelemetry
from .tool_bridge import FdbToolBridge


class LiveKitSessionAdapter:
    """Own event handlers without coupling the core runtime to LiveKit types."""

    def __init__(self, bridge: FdbToolBridge, telemetry: JsonlTelemetry) -> None:
        self.bridge = bridge
        self.telemetry = telemetry
        self.speech = StreamingSpeechState()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        self._user_started_at: float | None = None
        self._user_ended_at: float | None = None
        self._agent_started_at: float | None = None
        self._agent_speaking = False
        self._dispatch_closed = False

    def attach(self, session: Any) -> None:
        session.on("user_input_transcribed", self.on_user_input_transcribed)
        session.on("user_state_changed", self.on_user_state_changed)
        session.on("agent_state_changed", self.on_agent_state_changed)
        session.on("function_tools_executed", self.on_function_tools_executed)
        session.on("error", self.on_error)

    def on_user_input_transcribed(self, event: Any) -> None:
        transcript = str(getattr(event, "transcript", ""))
        if not transcript:
            return
        now_ms = time.time() * 1000.0
        if not self._dispatch_closed:
            self.bridge.begin_candidate_turn()
            self.speech.update(transcript, mode="replace", at_ms=now_ms)
        is_final = bool(getattr(event, "is_final", False))
        normalized = TranscriptEvent(
            direction="input",
            text=transcript,
            final=is_final,
            observed_at=now_ms / 1000.0,
            source="gemini_native_audio",
            language=getattr(event, "language", None),
            item_id=getattr(event, "item_id", None),
            start_time=getattr(event, "start_time", None),
            end_time=getattr(event, "end_time", None),
            timebase="seconds" if getattr(event, "start_time", None) is not None else None,
            timing_source=(
                "provider_alignment"
                if getattr(event, "start_time", None) is not None
                else "event_observation"
            ),
            confidence=getattr(event, "confidence", None),
        )
        self.telemetry.emit("provider_input_audio_transcription", **normalized.payload())
        if is_final and not self._dispatch_closed:
            self._commit_speech(now_ms, boundary="final_transcript")

    def on_user_state_changed(self, event: Any) -> None:
        state = str(getattr(event, "new_state", ""))
        now = time.time()
        if state == "speaking":
            self._user_started_at = now
            self._dispatch_closed = False
            self.bridge.begin_candidate_turn()
            self.telemetry.emit("user_speech_started", barge_in=self._agent_speaking)
            if self._agent_speaking:
                self._schedule(self.bridge.cancel_cancellable("user barge-in"))
        elif state == "listening":
            self._user_ended_at = now
            self._commit_speech(now * 1000.0, boundary="end_of_turn")
            if not self._dispatch_closed:
                self.bridge.commit_turn("end_of_turn")
            self._dispatch_closed = True
            self.telemetry.emit("end_of_turn", timing_source="vad_boundary")

    def on_agent_state_changed(self, event: Any) -> None:
        state = str(getattr(event, "new_state", ""))
        self._agent_speaking = state == "speaking"
        if state == "speaking":
            self._agent_started_at = time.time()
            latency = (
                self._agent_started_at - self._user_ended_at
                if self._user_ended_at is not None
                else None
            )
            self.telemetry.emit("agent_speech_started", perceived_latency_s=latency)
            if self._user_ended_at is not None:
                self.telemetry.latency_breakdown(
                    user_ended_at=self._user_ended_at,
                    tool_started_at=self.bridge.last_tool_started_at,
                    tool_finished_at=self.bridge.last_tool_finished_at,
                    agent_started_at=self._agent_started_at,
                    tool=self.bridge.last_tool,
                )
        else:
            self.telemetry.emit("agent_state_changed", state=state)

    def on_function_tools_executed(self, event: Any) -> None:
        calls = [getattr(row, "name", None) for row in getattr(event, "function_calls", [])]
        self.telemetry.emit("livekit_tools_executed", tools=[row for row in calls if row])

    def on_error(self, event: Any) -> None:
        error = getattr(event, "error", event)
        self.telemetry.emit(
            "livekit_error",
            error_type=type(error).__name__,
            source=type(event).__name__,
        )

    def _commit_speech(self, at_ms: float, *, boundary: str) -> None:
        utterance = self.speech.commit(at_ms)
        if utterance is None:
            return
        self.bridge.commit_turn(boundary)
        self._dispatch_closed = True
        self.telemetry.emit(
            "speech_committed",
            text=utterance.text,
            turn_id=utterance.turn_id,
            revision=utterance.candidate_revision,
            boundary=boundary,
        )

    def _schedule(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        if self._closed:
            coroutine.close()
            return
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self, *, timeout_s: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        await self.bridge.close(timeout_s=timeout_s)
        self.telemetry.emit("session_closed")

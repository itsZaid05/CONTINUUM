"""LiveKit session events mapped to CONTINUUM speech/lifecycle telemetry."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine
from typing import Any

from ...streaming import StreamingSpeechState
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
        self.bridge.begin_candidate_turn()
        self.speech.update(transcript, mode="replace", at_ms=now_ms)
        is_final = bool(getattr(event, "is_final", False))
        self.telemetry.emit(
            "speech_candidate",
            text=transcript,
            final=is_final,
            revision=self.speech.candidate_revision,
            language=getattr(event, "language", None),
        )
        if is_final:
            self._commit_speech(now_ms, boundary="final_transcript")

    def on_user_state_changed(self, event: Any) -> None:
        state = str(getattr(event, "new_state", ""))
        now = time.time()
        if state == "speaking":
            self._user_started_at = now
            self.bridge.begin_candidate_turn()
            self.telemetry.emit("user_speech_started")
            self._schedule(self.bridge.cancel_cancellable("user barge-in"))
        elif state == "listening":
            self._user_ended_at = now
            self._commit_speech(now * 1000.0, boundary="end_of_turn")
            # EOT is a valid dispatch boundary even when a provider does not
            # expose input transcription text.
            self.bridge.commit_turn("end_of_turn")
            self.telemetry.emit("user_speech_ended")

    def on_agent_state_changed(self, event: Any) -> None:
        state = str(getattr(event, "new_state", ""))
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
        # repr(error) may contain transport internals but never configuration
        # values supplied by this integration.
        self.telemetry.emit("livekit_error", error=repr(getattr(event, "error", event)))

    def _commit_speech(self, at_ms: float, *, boundary: str) -> None:
        utterance = self.speech.commit(at_ms)
        if utterance is None:
            return
        self.bridge.commit_turn(boundary)
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

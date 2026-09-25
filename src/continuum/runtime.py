"""Organizer-facing asynchronous runtime contract.

This module is deliberately a thin boundary around the existing kernel: input
and output are schema-validated queues, while state, planning and tools remain
owned by their existing modules.  A result is accepted only if its call id and
execution lineage are still live for that session.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from ._compat import StrEnum
from .delta_arbiter import arbitrate
from .perception import perceive_audio, perceive_frame, perceive_text
from .tools import MockToolSandbox, ToolManifest, ToolRegistry, ToolResult, default_registry
from .versioned_state import VersionedStore


class EventType(StrEnum):
    TEXT = "text"
    EOT = "eot"
    AUDIO = "audio"
    FRAME = "frame"
    INTERRUPT = "interrupt"
    TOOL_RESULT = "tool_result"
    MANIFEST = "manifest"


class RuntimeEvent(BaseModel):
    session_id: str = Field(min_length=1)
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    type: EventType
    text: str | None = None
    data: bytes | None = None
    call_id: str | None = None
    result: dict[str, Any] | None = None
    manifest: ToolManifest | None = None


class Snapshot(BaseModel):
    intent: dict[str, Any]
    slots: dict[str, Any]
    version: int


class SpeakAction(BaseModel):
    type: Literal["speak"] = "speak"
    text: str


class ToolCallAction(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    call_id: str
    tool: str
    args: dict[str, Any]


class CancelAction(BaseModel):
    type: Literal["cancel"] = "cancel"
    call_id: str


class ClarifyAction(BaseModel):
    type: Literal["clarify"] = "clarify"
    text: str


class FinalAction(BaseModel):
    type: Literal["final"] = "final"
    text: str
    snapshot: Snapshot


RuntimeAction = Annotated[
    SpeakAction | ToolCallAction | CancelAction | ClarifyAction | FinalAction,
    Field(discriminator="type"),
]


@dataclass
class LiveCall:
    call_id: str
    task: asyncio.Task[None]
    lineage: frozenset[str]
    version: int
    cancelled: bool = False


@dataclass
class _Session:
    store: VersionedStore = field(default_factory=VersionedStore)
    calls: dict[str, LiveCall] = field(default_factory=dict)
    text: str = ""
    lineage: set[str] = field(default_factory=set)


class AgentRuntime:
    """Two-queue async runtime with isolated sessions and cancellable calls."""

    def __init__(
        self, *, registry: ToolRegistry | None = None, cancel_grace_s: float = 0.05
    ) -> None:
        self.input_queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
        self.output_queue: asyncio.Queue[RuntimeAction] = asyncio.Queue()
        self.registry = registry or default_registry()
        self.sandbox = MockToolSandbox(registry=self.registry)
        self.cancel_grace_s = cancel_grace_s
        self._sessions: dict[str, _Session] = {}
        self._closed = False

    def session(self, session_id: str) -> _Session:
        return self._sessions.setdefault(session_id, _Session())

    async def submit(self, event: RuntimeEvent | dict[str, Any]) -> None:
        await self.input_queue.put(RuntimeEvent.model_validate(event))

    async def run_once(self) -> None:
        event = await self.input_queue.get()
        try:
            await self.handle(event)
        finally:
            self.input_queue.task_done()

    async def serve(self) -> None:
        while not self._closed:
            await self.run_once()

    async def close(self) -> None:
        self._closed = True
        for session in self._sessions.values():
            await self._cancel(session, list(session.calls))

    async def _emit(self, action: RuntimeAction) -> None:
        await self.output_queue.put(action)

    async def _cancel(self, session: _Session, call_ids: list[str]) -> None:
        doomed = [
            session.calls[c]
            for c in call_ids
            if c in session.calls and not session.calls[c].task.done()
        ]
        for live in doomed:
            live.cancelled = True
            live.task.cancel()
            await self._emit(CancelAction(call_id=live.call_id))
        if doomed:
            _done, pending = await asyncio.wait(
                [x.task for x in doomed], timeout=self.cancel_grace_s
            )
            for task in pending:
                # A non-cooperative external operation is abandoned; its late completion
                # cannot re-enter because `cancelled` stays set.
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    async def handle(self, event: RuntimeEvent) -> None:
        s = self.session(event.session_id)
        if event.type == EventType.MANIFEST:
            assert event.manifest is not None
            self.registry.register(event.manifest)
            return
        if event.type == EventType.INTERRUPT:
            await self._cancel(s, [event.call_id] if event.call_id else list(s.calls))
            return
        if event.type == EventType.TOOL_RESULT:
            # External result delivery is valid only for a live call_id.
            live = s.calls.get(event.call_id or "")
            if live is None or live.cancelled or live.version != self._version(s):
                return
            return
        if event.type == EventType.TEXT:
            if not event.text:
                return
            s.text += event.text
            await self._emit(SpeakAction(text="Got it — listening…"))
            return
        if event.type == EventType.EOT:
            if not s.text.strip():
                return
            text, s.text = s.text, ""
            await self._process_evidence(event.session_id, perceive_text(text, self._version(s)))
            return
        if event.type == EventType.AUDIO:
            if event.data:
                await self._process_evidence(
                    event.session_id, perceive_audio(event.data, self._version(s))
                )
            return
        if event.type == EventType.FRAME and event.data:
            await self._process_evidence(
                event.session_id, perceive_frame(event.data, self._version(s))
            )

    def _version(self, s: _Session) -> int:
        return s.store.current().version if s.store.current() else 1

    async def _process_evidence(self, session_id: str, perception: Any) -> None:
        s = self.session(session_id)
        await self._emit(SpeakAction(text=perception.fast_ack))
        ambiguity = perception.render_provenance.get("ambiguous")
        if ambiguity:
            await self._emit(ClarifyAction(text=str(ambiguity)))
            return
        decision = arbitrate(perception.evidences[0].text, s.store.current())
        base = s.store.current()
        if base is None:
            base = s.store.create_initial({})
        if decision.needs_clarification():
            await self._emit(
                ClarifyAction(
                    text=decision.suggested_clarification or "Could you clarify that request?"
                )
            )
            return
        current = s.store.patch(
            base.version, decision.delta, decision.category, perception.evidences
        )
        # Changes supersede currently executing work.  Keeping cancellation at this
        # boundary ensures no stale call is silently rerun.
        if current.version != base.version:
            await self._cancel(s, list(s.calls))
            s.lineage = {f"v{current.version}"}
        tool = self.registry.choose_read_tool(current.state)
        if tool is None:
            await self._final(session_id, "Updated your request.")
            return
        args = self.registry.bind_args(tool.name, current.state)
        call_id = uuid.uuid4().hex
        await self._emit(ToolCallAction(call_id=call_id, tool=tool.name, args=args))
        task = asyncio.create_task(self._execute(session_id, call_id, tool, args, current.version))
        s.calls[call_id] = LiveCall(call_id, task, frozenset(s.lineage), current.version)

    async def _execute(
        self,
        session_id: str,
        call_id: str,
        manifest: ToolManifest,
        args: dict[str, Any],
        version: int,
    ) -> None:
        s = self.session(session_id)
        try:
            result = await self.sandbox.call(manifest.name, args, idempotency_key=call_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._emit(ClarifyAction(text=f"{manifest.name} could not be completed: {exc}"))
            return
        live = s.calls.get(call_id)
        if live is None or live.cancelled or live.version != self._version(s):
            return  # late-result rejection
        await self._final(session_id, f"{manifest.name} completed.", result)

    async def _final(self, session_id: str, text: str, result: ToolResult | None = None) -> None:
        s = self.session(session_id)
        state = s.store.current()
        slots = dict(state.state) if state else {}
        if result:
            slots["last_result"] = result.payload
        await self._emit(
            FinalAction(
                text=text,
                snapshot=Snapshot(intent=slots, slots=slots, version=self._version(s)),
            )
        )

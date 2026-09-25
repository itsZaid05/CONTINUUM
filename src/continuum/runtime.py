"""Organizer-facing asynchronous runtime contract.

This module is deliberately a thin boundary around the existing kernel: input
and output are schema-validated queues, while state, planning and tools remain
owned by their existing modules.  A result is accepted only if its call id and
execution lineage are still live for that session.

Plan execution (AI/ML Engineer B)
---------------------------------
Each user turn goes perception → arbiter (category) → ``GenericPlanner``
(tools, args, goals from the session's manifests) → reconcile → advance:

* **reconcile** diffs the new plan against in-flight work. A running call
  whose (tool, resolved args) is unchanged is *adopted* by the new plan — no
  cancel, no rerun. Anything else the new plan no longer wants is cancelled
  (``task.cancel()`` + a ``cancel{call_id}`` action) inside the grace window.
* **advance** dispatches every step whose dependencies are done and whose
  arguments are complete: identical read-only results are reused, a
  state-changing step is keyed on (tool, args) so it lands at most once, an
  IRREVERSIBLE step waits for authorization, a promoted shadow replaces a
  dispatch.
* **execute** retries read-only calls with backoff; a timed-out
  state-changing call is *verified* before anything is re-sent; a late result
  for a cancelled or superseded call is dropped.

``tool_mode="external"`` waits for harness ``tool_result`` events instead of
calling the local sandbox. Ablation switches (``verify_timeouts``,
``selective_cancel``, ``confirm_irreversible``, ``max_read_retries``,
``planner="naive"``) exist so the evaluation pipeline can measure what each
mechanism buys.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from ._compat import StrEnum
from .contracts import ArbiterDecision, RiskLevel
from .delta_arbiter import arbitrate
from .dialogue import respond
from .generic_planner import GenericPlanner, PlanDecision
from .ledger import effect_id_for
from .perception import perceive_audio, perceive_frame, perceive_text
from .planner import PlanStep
from .speculation import ShadowSpeculator, call_key
from .tools import (
    TOOL_REGISTRY,
    FaultPlan,
    MockToolSandbox,
    ToolError,
    ToolManifest,
    ToolRegistry,
    ToolResult,
    ToolTimeout,
    _resolve_refs,
    default_registry,
)
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
    ts_ms: float | None = None  # producer timestamp (virtual clock), echoed into the trace
    text: str | None = None
    data: bytes | None = None
    call_id: str | None = None
    result: dict[str, Any] | None = None
    manifest: ToolManifest | None = None
    manifests: list[ToolManifest] | None = None


class Snapshot(BaseModel):
    intent: dict[str, Any]
    slots: dict[str, Any]
    version: int


class _Action(BaseModel):
    ts_ms: float | None = None  # runtime clock at emission


class SpeakAction(_Action):
    type: Literal["speak"] = "speak"
    text: str


class ToolCallAction(_Action):
    type: Literal["tool_call"] = "tool_call"
    call_id: str
    tool: str
    args: dict[str, Any]


class CancelAction(_Action):
    type: Literal["cancel"] = "cancel"
    call_id: str


class ClarifyAction(_Action):
    type: Literal["clarify"] = "clarify"
    text: str


class FinalAction(_Action):
    type: Literal["final"] = "final"
    text: str
    snapshot: Snapshot


RuntimeAction = Annotated[
    SpeakAction | ToolCallAction | CancelAction | ClarifyAction | FinalAction,
    Field(discriminator="type"),
]

_YES = re.compile(r"^\s*(?:yes|yeah|yep|sure|ok(?:ay)?|go ahead|do it|confirm|please do|book it|correct)\b", re.I)
_NO = re.compile(r"^\s*(?:no|nope|don't|do not|cancel|stop|not now|never mind)\b", re.I)


def _reference(payload: dict[str, Any]) -> str:
    for k in ("ref", "reservation_id", "order_id", "ticket_id", "visit_id", "event_id"):
        if payload.get(k):
            return str(payload[k])
    return next((str(v) for k, v in payload.items() if k.endswith("_id") and v), "done")


@dataclass
class StepRun:
    """One dispatched attempt at a plan step."""

    step_id: str
    tool: str
    args: dict[str, Any]
    key: str  # (tool, args) identity used for adoption / reuse
    call_id: str
    version: int
    goal: str
    effect_key: str | None = None  # stable idempotency key for state-changing steps
    task: asyncio.Task[None] | None = None
    status: str = "running"  # running | done | failed | cancelled
    attempts: int = 1
    result: ToolResult | None = None
    cancelled: bool = False
    dispatched_ms: float = 0.0
    reply: asyncio.Future[dict[str, Any]] | None = None  # external mode: the harness result


@dataclass
class _Session:
    registry: ToolRegistry
    sandbox: MockToolSandbox
    store: VersionedStore = field(default_factory=VersionedStore)
    text: str = ""
    slots: dict[str, Any] = field(default_factory=dict)
    goals: list[str] = field(default_factory=list)
    committed: set[str] = field(default_factory=set)
    authorized: set[str] = field(default_factory=set)
    plan: PlanDecision | None = None
    runs: dict[str, StepRun] = field(default_factory=dict)  # step_id -> current run
    calls: dict[str, StepRun] = field(default_factory=dict)  # call_id -> run (history)
    completed: dict[str, ToolResult] = field(default_factory=dict)  # read key -> result
    effects: dict[str, str] = field(default_factory=dict)  # effect key -> INFLIGHT | COMMITTED
    effect_results: dict[str, ToolResult] = field(default_factory=dict)
    pending_slot: tuple[str, str] | None = None
    pending_confirm: str | None = None  # IRREVERSIBLE step waiting for "yes"
    asked: set[str] = field(default_factory=set)
    finals_sent: set[str] = field(default_factory=set)
    external: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    manifest_seen: bool = False
    last_category: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    spec: ShadowSpeculator | None = None
    shadow_tasks: dict[str, asyncio.Task[ToolResult]] = field(default_factory=dict)


class AgentRuntime:
    """Two-queue async runtime with isolated sessions and cancellable calls."""

    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        cancel_grace_s: float = 0.05,
        tool_mode: Literal["local", "external"] = "local",
        tool_speed: float = 1.0,
        faults: dict[str, FaultPlan] | None = None,
        speculation: bool = False,
        today: date | None = None,
        max_read_retries: int = 2,
        retry_backoff_s: float = 0.05,
        verify_timeouts: bool = True,
        selective_cancel: bool = True,
        confirm_irreversible: bool = True,
        planner: Literal["generic", "naive"] = "generic",
    ) -> None:
        self.input_queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
        self.output_queue: asyncio.Queue[RuntimeAction] = asyncio.Queue()
        self._default_registry = registry is None
        self.registry = registry or default_registry()
        self.sandbox = MockToolSandbox(registry=self.registry, faults=faults)
        self.cancel_grace_s = cancel_grace_s
        self.tool_mode = tool_mode
        self.tool_speed = tool_speed
        self.faults = dict(faults or {})
        self.speculation = speculation and tool_mode == "local"
        self.today = today
        self.max_read_retries = max_read_retries
        self.retry_backoff_s = retry_backoff_s
        self.verify_timeouts = verify_timeouts
        self.selective_cancel = selective_cancel
        self.confirm_irreversible = confirm_irreversible
        self.planner_mode = planner
        self.trace: list[dict[str, Any]] = []
        self._t0 = time.perf_counter()
        self._sessions: dict[str, _Session] = {}
        self._closed = False

    # ------------------------------------------------------------ plumbing
    def now_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000.0

    def _log(self, session_id: str, event: str, **data: Any) -> None:
        self.trace.append({"ts_ms": round(self.now_ms(), 3), "session": session_id, "event": event, **data})

    def session(self, session_id: str) -> _Session:
        s = self._sessions.get(session_id)
        if s is None:
            reg = self.registry.copy()
            s = _Session(registry=reg, sandbox=MockToolSandbox(registry=reg, faults=self.faults))
            if self.speculation:
                s.spec = ShadowSpeculator()
            self._sessions[session_id] = s
        return s

    def planner_for(self, s: _Session) -> GenericPlanner:
        return GenericPlanner(s.registry, today=self.today)

    async def submit(self, event: RuntimeEvent | dict[str, Any]) -> None:
        await self.input_queue.put(RuntimeEvent.model_validate(event))

    async def run_once(self) -> None:
        event = await self.input_queue.get()
        try:
            await self.handle(event)
        except Exception as exc:  # noqa: BLE001 — one bad event must never stop the loop
            self._log(event.session_id, "handler_error", error=repr(exc))
            await self._emit(ClarifyAction(text="Sorry, something went wrong on my side — could you say that again?"))
        finally:
            self.input_queue.task_done()

    async def serve(self) -> None:
        while not self._closed:
            await self.run_once()

    async def close(self) -> None:
        self._closed = True
        for sid, s in self._sessions.items():
            await self._cancel_runs(sid, s, [r for r in s.runs.values() if r.status == "running"])
            if s.spec is not None:
                for t in s.shadow_tasks.values():
                    t.cancel()
                s.spec.close(self.now_ms())

    async def _emit(self, action: SpeakAction | ToolCallAction | CancelAction | ClarifyAction | FinalAction) -> None:
        action.ts_ms = round(self.now_ms(), 3)
        await self.output_queue.put(action)

    # ------------------------------------------------------------- events
    async def handle(self, event: RuntimeEvent) -> None:
        s = self.session(event.session_id)
        self._log(event.session_id, "input", type=event.type.value, event_ts_ms=event.ts_ms,
                  text=event.text, call_id=event.call_id)
        if event.type == EventType.MANIFEST:
            manifests = list(event.manifests or []) + ([event.manifest] if event.manifest else [])
            if not manifests:
                return
            if not s.manifest_seen and self._default_registry:
                # a scenario that ships its own tools replaces the built-in travel set
                s.registry = ToolRegistry()
                s.sandbox.registry = s.registry
            s.manifest_seen = True
            for m in manifests:
                s.registry.register(m)
            return
        if event.type == EventType.INTERRUPT:
            if event.call_id:
                run = s.calls.get(event.call_id)
                if run is not None and run.status == "running":
                    await self._cancel_runs(event.session_id, s, [run])
            # A bare barge-in stops speech but not work: which calls are
            # invalidated is decided by what the user says next (plan diff).
            return
        if event.type == EventType.TOOL_RESULT:
            fut = s.external.pop(event.call_id or "", None)
            if fut is not None and not fut.done():
                fut.set_result(dict(event.result or {}))
            else:
                self._log(event.session_id, "tool_result_ignored", call_id=event.call_id,
                          reason="unknown, cancelled or superseded call")
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
                await self._process_evidence(event.session_id, perceive_audio(event.data, self._version(s)))
            return
        if event.type == EventType.FRAME and event.data:
            await self._process_evidence(event.session_id, perceive_frame(event.data, self._version(s)))

    def _version(self, s: _Session) -> int:
        cur = s.store.current()
        return cur.version if cur else 1

    # -------------------------------------------------------- understanding
    async def _process_evidence(self, session_id: str, perception: Any) -> None:
        s = self.session(session_id)
        await self._emit(SpeakAction(text=perception.fast_ack))
        ambiguity = perception.render_provenance.get("ambiguous")
        if ambiguity:
            await self._emit(ClarifyAction(text=str(ambiguity)))
            return
        text = perception.evidences[0].text
        async with s.lock:
            if s.pending_confirm is not None and (_YES.match(text) or _NO.match(text)):
                await self._answer_confirmation(session_id, s, bool(_YES.match(text)))
                return
            decision = arbitrate(text, s.store.current())
            s.last_category = decision.category.value
            if decision.needs_clarification():
                await self._emit(
                    ClarifyAction(text=decision.suggested_clarification or "Could you clarify that request?")
                )
                return
            if self.planner_mode == "naive":
                await self._naive_turn(session_id, s, decision, perception)
                return
            d = self.planner_for(s).plan(
                text,
                decision=decision,
                slots=s.slots,
                goals=s.goals,
                committed=s.committed,
                authorized=s.authorized,
                pending=s.pending_slot,
            )
            self._log(session_id, "plan", **{k: v for k, v in d.to_dict().items() if k != "scores"})
            if d.action == "noop":
                return  # backchannel: in-flight work continues untouched
            if not d.steps and d.mode != "retract":
                await self._emit(ClarifyAction(text=d.question or "Could you clarify that request?"))
                return
            await self._apply_plan(session_id, s, d, decision, perception.evidences)

    async def _apply_plan(
        self,
        session_id: str,
        s: _Session,
        d: PlanDecision,
        decision: ArbiterDecision | None,
        evidence: list[Any],
    ) -> None:
        if d.mode == "retract":
            for g in [g for g in s.goals if g in s.committed]:
                # honest retraction: an effect that already landed is not silently "undone"
                landed = next((r for r in s.effect_results.values() if r.tool == g), None)
                ref = _reference(landed.payload) if landed is not None else g
                await self._emit(SpeakAction(text=respond("retract_after_commit", ref=ref)))
        s.slots = dict(d.slots)
        s.goals = list(d.goals)
        s.authorized = set(d.authorized)
        s.pending_slot = d.missing[0] if d.missing else None
        if s.pending_confirm not in d.needs_confirmation:
            s.pending_confirm = None
        s.asked = {t for t in s.asked if t in d.needs_confirmation}
        s.store.apply_slots(s.slots, decision.category if decision else None, evidence)
        await self._reconcile(session_id, s, d)
        if s.spec is not None:
            s.spec.reconcile(s.slots, self.now_ms())
            if d.mode != "answer":
                s.spec.discard_hedges(self.now_ms())
        if d.missing and d.question:
            await self._emit(ClarifyAction(text=d.question))
        await self._advance(session_id, s)
        if s.spec is not None:
            await self._speculate(session_id, s, d)

    async def _answer_confirmation(self, session_id: str, s: _Session, yes: bool) -> None:
        tool = s.pending_confirm
        s.pending_confirm = None
        assert s.plan is not None and tool is not None
        goal = s.plan.goal_of.get(tool, tool)
        if yes:
            s.authorized.add(goal)
            self._log(session_id, "confirmed", tool=tool)
            await self._advance(session_id, s)
            return
        planner = self.planner_for(s)
        prefix = planner.read_prefix(goal)
        goals = [g for g in s.goals if g != goal] + ([prefix] if prefix else [])
        d = planner.replan(goals, s.slots, authorized=s.authorized)
        self._log(session_id, "declined", tool=tool)
        await self._emit(SpeakAction(text=f"Okay — I won't {s.registry.get(tool).description.lower() or tool}."))
        s.goals = list(d.goals)
        await self._reconcile(session_id, s, d)
        await self._advance(session_id, s)

    # ----------------------------------------------------------- execution
    def _resolve(self, s: _Session, step: PlanStep) -> dict[str, Any] | None:
        """Literal args for ``step`` once every dependency has a result."""
        results: dict[str, ToolResult] = {}
        for dep in step.depends_on:
            run = s.runs.get(dep)
            if run is None or run.status != "done" or run.result is None:
                return None
            results[dep] = run.result
        return _resolve_refs(dict(step.params), results)

    async def _reconcile(self, session_id: str, s: _Session, d: PlanDecision) -> None:
        wanted = {st.step_id: st for st in d.steps}
        version = self._version(s)
        to_cancel: list[StepRun] = []
        for step_id, run in list(s.runs.items()):
            st = wanted.get(step_id)
            args = self._resolve(s, st) if st is not None else None
            same = st is not None and args is not None and call_key(st.tool, args) == run.key
            if run.status == "running":
                if same and self.selective_cancel:
                    run.version = version
                    run.goal = d.goal_of.get(step_id, run.goal)
                    self._log(session_id, "adopted", call_id=run.call_id, tool=run.tool, args=run.args)
                else:
                    to_cancel.append(run)
                    del s.runs[step_id]
            elif run.status == "done" and same:
                run.goal = d.goal_of.get(step_id, run.goal)
            else:
                del s.runs[step_id]
        if to_cancel:
            await self._cancel_runs(session_id, s, to_cancel)
        s.plan = d

    async def _advance(self, session_id: str, s: _Session) -> None:
        d = s.plan
        if d is None:
            return
        progressed = True
        while progressed:
            progressed = False
            for st in d.steps:
                run = s.runs.get(st.step_id)
                if run is not None and run.status in {"running", "done"}:
                    continue
                args = self._resolve(s, st)
                m = s.registry.get(st.tool)
                if args is None or any(p not in args or args[p] is None for p in m.required):
                    continue
                goal = d.goal_of.get(st.step_id, st.tool)
                if m.risk == RiskLevel.IRREVERSIBLE and self.confirm_irreversible and goal not in s.authorized:
                    if st.tool not in s.asked:
                        s.asked.add(st.tool)
                        s.pending_confirm = st.tool
                        shown = ", ".join(f"{k}={v}" for k, v in args.items())
                        await self._emit(
                            ClarifyAction(text=f"Should I go ahead: {m.description or st.tool} ({shown})?")
                        )
                    continue
                key = call_key(st.tool, args)
                if not m.state_changing and key in s.completed:
                    self._finish_run(session_id, s, st, args, key, goal, s.completed[key], reused=True)
                    progressed = True
                    continue
                effect_key = None
                if m.state_changing:
                    effect_key = effect_id_for(0, f"{session_id}:{st.tool}", args)
                    state = s.effects.get(effect_key)
                    if state == "COMMITTED":
                        self._finish_run(session_id, s, st, args, key, goal, s.effect_results[effect_key], reused=True)
                        progressed = True
                        continue
                    if state == "INFLIGHT":
                        continue
                shadow_task = None
                if s.spec is not None and not m.state_changing:
                    sh = s.spec.match(st.tool, args)
                    if sh is not None:
                        s.spec.promote(sh, self.now_ms())
                        shadow_task = s.shadow_tasks.pop(sh.key, None)
                        self._log(session_id, "shadow_promoted", tool=st.tool, args=args, branch=sh.branch_id)
                await self._dispatch(session_id, s, st, m, args, key, goal, effect_key, shadow_task)
                progressed = progressed or shadow_task is not None
        await self._emit_finals(session_id, s)

    async def _dispatch(
        self,
        session_id: str,
        s: _Session,
        st: PlanStep,
        m: ToolManifest,
        args: dict[str, Any],
        key: str,
        goal: str,
        effect_key: str | None,
        shadow_task: asyncio.Task[ToolResult] | None = None,
    ) -> None:
        run = StepRun(
            step_id=st.step_id, tool=st.tool, args=args, key=key, call_id=uuid.uuid4().hex,
            version=self._version(s), goal=goal, effect_key=effect_key, dispatched_ms=self.now_ms(),
        )
        if effect_key is not None:
            s.effects[effect_key] = "INFLIGHT"
        s.runs[st.step_id] = run
        s.calls[run.call_id] = run
        if shadow_task is None and self.tool_mode == "external":
            run.reply = self._expect_result(s, run.call_id)  # before the call is visible: a fast reply must land
        if shadow_task is None:
            await self._emit(ToolCallAction(call_id=run.call_id, tool=st.tool, args=args))
            self._log(session_id, "dispatch", call_id=run.call_id, tool=st.tool, args=args,
                      state_changing=m.state_changing)
        run.task = asyncio.create_task(self._execute(session_id, run, m, shadow_task))

    async def _invoke(self, s: _Session, run: StepRun, m: ToolManifest) -> ToolResult:
        if self.tool_mode == "external":
            if run.reply is None:
                run.reply = self._expect_result(s, run.call_id)
            raw = await run.reply
            status = str(raw.get("status", "")).lower()
            if status == "timeout":
                raise ToolTimeout(str(raw.get("error", "timeout")))
            if raw.get("error") or status in {"error", "failed", "failure"}:
                raise ToolError(str(raw.get("error", status)))
            payload = raw.get("payload", raw.get("result", {k: v for k, v in raw.items() if k != "status"}))
            return ToolResult(m.name, raw.get("external_operation_id"), run.dispatched_ms, self.now_ms(),
                              "COMPLETED", dict(payload) if isinstance(payload, dict) else {"result": payload})
        return await s.sandbox.call(
            m.name, run.args, idempotency_key=run.effect_key or run.call_id, speed=self.tool_speed
        )

    async def _execute(
        self,
        session_id: str,
        run: StepRun,
        m: ToolManifest,
        shadow_task: asyncio.Task[ToolResult] | None = None,
    ) -> None:
        s = self.session(session_id)
        result: ToolResult | None = None
        while result is None:
            try:
                result = await (asyncio.shield(shadow_task) if shadow_task is not None else self._invoke(s, run, m))
            except asyncio.CancelledError:
                raise
            except ToolTimeout as exc:
                if run.cancelled:
                    return
                if m.state_changing and self.tool_mode == "external":
                    # no verify channel to the harness: the effect is UNKNOWN, and
                    # re-sending could double it — report, never blind-retry
                    await self._fail(session_id, s, run, exc, uncertain=True)
                    return
                if m.state_changing and self.verify_timeouts:
                    landed = s.sandbox.status(run.effect_key) if run.effect_key and self.tool_mode == "local" else None
                    self._log(session_id, "verify_after_timeout", call_id=run.call_id, tool=run.tool,
                              committed=landed is not None)
                    if landed is not None:
                        result = landed
                        break
                    if run.attempts > 1:
                        await self._fail(session_id, s, run, exc)
                        return
                elif m.state_changing:
                    # ablation: blind retry under a fresh key — the double-booking bug
                    run.effect_key = uuid.uuid4().hex
                elif run.attempts > self.max_read_retries:
                    await self._fail(session_id, s, run, exc)
                    return
                await self._retry(session_id, s, run, m)
            except ToolError as exc:
                if run.cancelled:
                    return
                if m.state_changing or run.attempts > self.max_read_retries:
                    await self._fail(session_id, s, run, exc)
                    return
                await asyncio.sleep(self.retry_backoff_s * (2 ** (run.attempts - 1)) * self.tool_speed)
                await self._retry(session_id, s, run, m)
            shadow_task = None
        async with s.lock:
            if run.cancelled or s.runs.get(run.step_id) is not run:
                self._log(session_id, "stale_result_rejected", call_id=run.call_id, tool=run.tool)
                return
            self._finish_run(session_id, s, None, run.args, run.key, run.goal, result, run=run)
            await self._advance(session_id, s)

    def _expect_result(self, s: _Session, call_id: str) -> asyncio.Future[dict[str, Any]]:
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        s.external[call_id] = fut
        return fut

    async def _retry(self, session_id: str, s: _Session, run: StepRun, m: ToolManifest) -> None:
        run.attempts += 1
        old = run.call_id
        run.call_id = uuid.uuid4().hex
        s.calls[run.call_id] = run
        if self.tool_mode == "external":
            run.reply = self._expect_result(s, run.call_id)
        self._log(session_id, "retry", tool=run.tool, previous_call_id=old, call_id=run.call_id,
                  attempt=run.attempts)
        await self._emit(ToolCallAction(call_id=run.call_id, tool=run.tool, args=run.args))
        self._log(session_id, "dispatch", call_id=run.call_id, tool=run.tool, args=run.args,
                  state_changing=m.state_changing, retry=True)

    async def _fail(
        self, session_id: str, s: _Session, run: StepRun, exc: Exception, *, uncertain: bool = False
    ) -> None:
        run.status = "failed"
        if run.effect_key is not None and not uncertain and s.effects.get(run.effect_key) == "INFLIGHT":
            s.effects.pop(run.effect_key, None)  # verified not landed: a later retry is safe
        self._log(session_id, "tool_failed", call_id=run.call_id, tool=run.tool, error=str(exc),
                  uncertain=uncertain)
        desc = (s.registry.get(run.tool).description or run.tool).lower()
        if uncertain:
            await self._emit(SpeakAction(text=f"I didn't get a confirmation back ({desc}) — it may have "
                                              "gone through, so I won't resend it blindly."))
        else:
            await self._emit(SpeakAction(text=f"I couldn't complete that ({desc}). Want me to try again?"))

    def _finish_run(
        self,
        session_id: str,
        s: _Session,
        st: PlanStep | None,
        args: dict[str, Any],
        key: str,
        goal: str,
        result: ToolResult,
        *,
        reused: bool = False,
        run: StepRun | None = None,
    ) -> None:
        if run is None:
            assert st is not None
            run = StepRun(st.step_id, st.tool, args, key, f"reuse-{uuid.uuid4().hex[:8]}",
                          self._version(s), goal, status="done")
            s.runs[st.step_id] = run
        run.status = "done"
        run.result = result
        m = s.registry.get(run.tool)
        if m.state_changing:
            ek = run.effect_key or effect_id_for(0, f"{session_id}:{run.tool}", args)
            s.effects[ek] = "COMMITTED"
            s.effect_results[ek] = result
            if run.goal == run.tool:
                s.committed.add(run.goal)
        else:
            s.completed[key] = result
        # result ids (ticket_id, event_id...) become session memory for follow-ups
        arg_names = {p for tm in s.registry.all() for p in tm.properties}
        for k, v in result.payload.items():
            if k in arg_names and k.endswith("_id") and v is not None:
                s.slots[k] = v
        self._log(session_id, "tool_completed", call_id=run.call_id, tool=run.tool, args=args,
                  reused=reused, state_changing=m.state_changing, payload=result.payload)

    async def _emit_finals(self, session_id: str, s: _Session) -> None:
        d = s.plan
        if d is None:
            return
        for g in d.goals:
            steps = [st for st in d.steps if d.goal_of.get(st.step_id) == g]
            runs = [s.runs.get(st.step_id) for st in steps]
            if not steps or any(r is None or r.status != "done" for r in runs):
                continue
            final_run = s.runs[g] if g in s.runs else runs[-1]
            assert final_run is not None and final_run.result is not None
            tag = f"{g}:{final_run.key}"
            if tag in s.finals_sent:
                continue
            s.finals_sent.add(tag)
            m = s.registry.get(g)
            fields = {k: v for k, v in final_run.result.payload.items() if v is not None}
            shown = ", ".join(f"{k}: {v}" for k, v in list(fields.items())[:4])
            text = f"Done — {m.description.lower() or g}" + (f" ({shown})." if shown else ".")
            intent = {
                "goal": g,
                "goals": list(s.goals),
                "status": "completed",
                "state_changing": m.state_changing,
                "category": s.last_category,
            }
            await self._emit(
                FinalAction(
                    text=text,
                    snapshot=Snapshot(intent=intent, slots=dict(s.slots), version=self._version(s)),
                )
            )
            self._log(session_id, "final", goal=g, slots=dict(s.slots))

    async def _cancel_runs(self, session_id: str, s: _Session, runs: list[StepRun]) -> None:
        doomed = [r for r in runs if r.status == "running"]
        for run in doomed:
            run.cancelled = True
            run.status = "cancelled"
            if run.task is not None and not run.task.done():
                run.task.cancel()
            fut = s.external.pop(run.call_id, None)
            if fut is not None and not fut.done():
                fut.cancel()
            if run.effect_key is not None:
                s.effects.pop(run.effect_key, None)
            await self._emit(CancelAction(call_id=run.call_id))
            self._log(session_id, "cancel", call_id=run.call_id, tool=run.tool, args=run.args)
        tasks = [r.task for r in doomed if r.task is not None]
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=self.cancel_grace_s)
            for task in pending:
                # A non-cooperative external operation is abandoned; its late completion
                # cannot re-enter because `cancelled` stays set.
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    # ---------------------------------------------------------- speculation
    async def _speculate(self, session_id: str, s: _Session, d: PlanDecision) -> None:
        assert s.spec is not None
        resolved = {st.step_id: self._resolve(s, st) for st in d.steps}
        for cand in s.spec.propose(self.planner_for(s), d, resolved):
            sh = s.spec.spawn(cand, self._version(s), self.now_ms())
            if sh is None:
                continue
            task = asyncio.create_task(s.sandbox.call(cand.tool, cand.args, speed=self.tool_speed))

            def _done(t: asyncio.Task[ToolResult], sh: Any = sh) -> None:
                sh.finished_ms = self.now_ms()
                if not t.cancelled():
                    t.exception()

            task.add_done_callback(_done)
            sh.task = task
            s.shadow_tasks[sh.key] = task
            self._log(session_id, "shadow_spawn", tool=cand.tool, args=cand.args, source=cand.source,
                      branch=sh.branch_id)

    # ------------------------------------------------------ naive baseline
    async def _naive_turn(self, session_id: str, s: _Session, decision: ArbiterDecision, perception: Any) -> None:
        """The runtime's behaviour before the generic planner: first read-only
        tool, args copied from arbiter state, cancel everything on any change."""
        base = s.store.current() or s.store.create_initial({})
        current = s.store.patch(base.version, decision.delta, decision.category, perception.evidences)
        if current.version != base.version:
            await self._cancel_runs(session_id, s, [r for r in s.runs.values() if r.status == "running"])
            s.runs.clear()
        tool = s.registry.choose_read_tool(current.state)
        if tool is None:
            return
        if tool.name in TOOL_REGISTRY:
            # built-ins had no argument schema on that path: whatever names matched
            args = {k: v for k, v in current.state.items() if k in tool.properties}
        else:
            try:
                args = s.registry.bind_args(tool.name, current.state)
            except ValueError as exc:  # the old path raised out of handle() here
                await self._emit(ClarifyAction(text=str(exc)))
                return
        step = PlanStep(tool.name, tool.name, tool.kind, args, risk_level=tool.risk)
        s.slots = dict(current.state)
        s.goals = [tool.name]
        s.plan = PlanDecision("plan", "new", [tool.name], [step], dict(current.state),
                              goal_of={tool.name: tool.name})
        await self._dispatch(session_id, s, step, tool, args, call_key(tool.name, args), tool.name, None)

    # --------------------------------------------------------- introspection
    def speculation_metrics(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for sid, s in self._sessions.items():
            if s.spec is not None:
                out[sid] = s.spec.metrics()
        return out

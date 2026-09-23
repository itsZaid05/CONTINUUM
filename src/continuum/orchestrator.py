"""
Runtime Orchestrator — genuine planner -> sandbox -> shadow-store wiring
(AI/ML Engineer B).

The smallest integration layer connecting planner-generated shadow steps to
*real* async tool execution (``tools.py``) and *persisted* results
(``shadow_store.py``), so promotion means "retrieve an already-computed
answer", never "flip a label to PROMOTED and quietly recompute".

Authority stays where it already lived:
  * ``BranchManager`` remains the only lifecycle/budget authority (spawn,
    promote, invalidate, cancel, cleanup, record_call all still go through
    it — this module never mutates branch state directly).
  * ``policy.risk_for`` remains the only risk authority — every dispatch
    re-checks it immediately before execution, so even a hypothetical bug
    upstream (planner/scorer) can never get an IRREVERSIBLE/MUTATING call
    speculated.

This module only *executes* what those two authorities have already allowed,
using the existing ``tools.execute_plan`` (real ``asyncio.gather`` concurrency
— no second concurrency mechanism invented here).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .branch_manager import BranchManager
from .planner import PlanStep
from .policy import risk_for
from .shadow_store import ShadowResult, ShadowResultStore
from .tools import MockToolSandbox, ToolResult, execute_plan


class SpeculationPolicyError(PermissionError):
    """A shadow step's risk tier is outside the budget's allowed_levels.

    Should never fire in practice (planner/ShadowScorer/BranchManager.spawn
    already filter these out before a branch exists at all) — this is the
    dispatcher's own belt-and-suspenders check, so policy is enforced again
    at the one place that would actually do something irreversible.
    """


@dataclass(frozen=True)
class _NSStep:
    """Branch-namespaced step so concurrent shadow branches never collide
    inside one `execute_plan()` dispatch wave (structurally satisfies
    tools.py's `_Step` protocol — no import needed, no coupling added)."""

    step_id: str
    tool: str
    params: dict[str, Any]
    depends_on: list[str]
    idempotency_key: str | None


def _namespaced(branch_id: str, step: PlanStep) -> _NSStep:
    key = step.idempotency_key or f"shadow:{branch_id}:{step.step_id}"
    return _NSStep(f"{branch_id}::{step.step_id}", step.tool, dict(step.params), [], key)


def _check_policy(step: PlanStep, bm: BranchManager, branch_id: str) -> None:
    if risk_for(step.kind) not in bm.budget.allowed_levels:
        raise SpeculationPolicyError(
            f"shadow branch {branch_id} step {step.step_id!r} kind={step.kind!r} "
            f"is not a permitted speculation risk tier ({bm.budget.allowed_levels})"
        )


async def _dispatch_async(
    entries: list[tuple[str, PlanStep]], sandbox: MockToolSandbox, speed: float
) -> dict[str, ToolResult]:
    ns = [_namespaced(bid, s) for bid, s in entries]
    results = await execute_plan(ns, sandbox, speed=speed)  # type: ignore[arg-type]
    return {bid: results[f"{bid}::{s.step_id}"] for bid, s in entries}


def dispatch_shadow_branches(
    bm: BranchManager,
    store: ShadowResultStore,
    sandbox: MockToolSandbox,
    branch_steps: list[tuple[str, int, PlanStep]],
    *,
    speed: float = 0.02,
) -> list[ShadowResult]:
    """Execute each ``(branch_id, base_version, step)`` concurrently in one
    event-loop pass (one ``asyncio.gather`` via ``tools.execute_plan`` — the
    existing DAG executor, not a second one), charge one tool call against
    each branch's budget, and persist the real results for later reuse.

    A step whose risk tier isn't in ``bm.budget.allowed_levels`` raises
    ``SpeculationPolicyError`` instead of running. A branch already over its
    per-shadow call budget is silently skipped (``record_call`` has already
    invalidated/cancelled/cleaned it up).
    """
    safe: list[tuple[str, int, PlanStep]] = []
    for branch_id, base_version, step in branch_steps:
        _check_policy(step, bm, branch_id)
        if not bm.record_call(branch_id):
            continue
        safe.append((branch_id, base_version, step))
    if not safe:
        return []

    results = asyncio.run(_dispatch_async([(bid, s) for bid, _bv, s in safe], sandbox, speed))
    out: list[ShadowResult] = []
    for branch_id, base_version, step in safe:
        sr = ShadowResult(
            branch_id=branch_id,
            base_version=base_version,
            tool=step.tool,
            params=dict(step.params),
            step_id=step.step_id,
            result=results[branch_id],
        )
        store.put(sr)
        out.append(sr)
    return out


async def dispatch_shadow_branch_task(
    bm: BranchManager,
    store: ShadowResultStore,
    sandbox: MockToolSandbox,
    branch_id: str,
    base_version: int,
    step: PlanStep,
    *,
    speed: float = 1.0,
) -> ShadowResult:
    """Single-branch coroutine form of the dispatcher.

    Unlike `dispatch_shadow_branches` (which batches + blocks via
    ``asyncio.run``), this is a plain coroutine callers can wrap in their own
    ``asyncio.ensure_future`` to get a real, independently cancellable
    ``asyncio.Task`` per shadow branch — used where genuine mid-flight
    cancellation of one shadow (while another keeps running) needs to be
    demonstrated directly against the real event loop.
    """
    _check_policy(step, bm, branch_id)
    if not bm.record_call(branch_id):
        raise RuntimeError(f"shadow branch {branch_id} is over its call budget")
    idem_key = step.idempotency_key or f"shadow:{branch_id}:{step.step_id}"
    result = await sandbox.call(step.tool, step.params, idempotency_key=idem_key, speed=speed)
    sr = ShadowResult(
        branch_id=branch_id,
        base_version=base_version,
        tool=step.tool,
        params=dict(step.params),
        step_id=step.step_id,
        result=result,
    )
    store.put(sr)
    return sr


def promote_or_discard(
    store: ShadowResultStore,
    tool: str,
    params: dict[str, Any],
    live_branch_ids: set[str],
) -> tuple[str | None, list[str]]:
    """Given the primary's *current* intended tool+params, find a matching,
    already-completed shadow result among still-live branches.

    Returns ``(winner_branch_id | None, loser_branch_ids)``. This function
    only decides *which* branch matches — callers still drive the actual
    lifecycle transitions via ``BranchManager.promote/invalidate/cancel/
    cleanup`` themselves; it is not a second lifecycle authority.
    """
    matches = store.find_match(tool, params, branch_ids=live_branch_ids)
    winner = matches[0].branch_id if matches else None
    losers = [bid for bid in live_branch_ids if bid != winner]
    return winner, losers

"""
Engineer B — end-to-end integration tests.

Covers the gap the architecture review found: shadows were spawned/promoted
as bookkeeping only, never actually executed. These tests prove the real
pipeline — planner -> real async tool dispatch -> stored result -> matching
-> promotion -> genuine reuse (no duplicate tool call) -> stale-result
rejection — using the *existing* abstractions (BranchManager, ProvenanceGraph,
MockToolSandbox/execute_plan, ShadowScorer, policy.risk_for), not a parallel
system.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from continuum.branch_manager import BranchManager
from continuum.contracts import (
    ArbiterCategory,
    ArbiterDecision,
    BranchState,
    Delta,
    DeltaOp,
    GateDecision,
    SpeculationBudget,
)
from continuum.orchestrator import (
    SpeculationPolicyError,
    dispatch_shadow_branch_task,
    dispatch_shadow_branches,
    promote_or_discard,
)
from continuum.planner import PlanStep, generate_plan, primary_search_params, step_for_hypothesis
from continuum.provenance import ProvenanceGraph
from continuum.replay import replay_scenario
from continuum.shadow import ShadowScorer
from continuum.shadow_store import ShadowResultStore
from continuum.tools import MockToolSandbox, execute_plan
from continuum.versioned_state import VersionedStore


def _dec(cat=ArbiterCategory.MODIFY, conf=0.60, field="destination", new="Bangalore"):
    delta = (
        Delta(op=DeltaOp.REPLACE, field=field, old_value="Delhi", new_value=new, span="x")
        if field
        else None
    )
    return ArbiterDecision(category=cat, confidence=conf, delta=delta, rationale="test")


def _state(destination="Delhi", constraints=("morning",)):
    s = VersionedStore()
    s.create_initial({"destination": destination, "constraints": list(constraints)})
    return s.current()


# ---------------------------------------------------------------------------
# 1-3: shadow executes a real permitted READ op; result stored + associated
# ---------------------------------------------------------------------------


def test_shadow_executes_permitted_read_and_result_is_stored():
    bm = BranchManager()
    store = ShadowResultStore()
    sandbox = MockToolSandbox()
    b = bm.spawn_shadow(1, _dec(), kind="search")
    assert b is not None
    step = PlanStep(
        step_id="s0", tool="search_flights", kind="search", params={"to": "Bangalore", "slot": "morning"}
    )

    results = dispatch_shadow_branches(bm, store, sandbox, [(b.id, 1, step)], speed=0.01)

    assert len(results) == 1
    sr = results[0]
    assert sr.result.status == "COMPLETED"
    # associated with branch / base_version / tool / normalized params
    assert sr.branch_id == b.id
    assert sr.base_version == 1
    assert sr.tool == "search_flights"
    assert sr.params == {"to": "Bangalore", "slot": "morning"}
    # actually stored, retrievable
    stored = store.get(b.id)
    assert stored is not None and stored.result.payload["to"] == "Bangalore"
    # a real tool call really happened (sandbox dedup has a record)
    assert len(sandbox._dedup) == 1


# ---------------------------------------------------------------------------
# 4-8: user intent changes -> matching shadow found -> promoted -> reused ->
# no duplicate tool execution
# ---------------------------------------------------------------------------


def test_matching_shadow_promoted_and_reused_without_duplicate_dispatch():
    bm = BranchManager()
    store = ShadowResultStore()
    sandbox = MockToolSandbox()
    scorer = ShadowScorer()
    decision = _dec()  # MODIFY destination -> Bangalore, confidence 0.60 (in hedge band)
    state = _state()  # destination Delhi, morning in history

    hyps = scorer.score(decision, state)
    assert {h.label for h in hyps} == {"Bangalore morning", "Bangalore evening"}

    branches = bm.spawn_shadows_for_decision(decision, 1, state, scorer, force=True)
    assert len(branches) == 2  # both within the 2-shadow budget

    branch_steps = []
    for b in branches:
        h = next(x for x in hyps if x.label == bm.label_of(b.id))
        step = step_for_hypothesis(h, f"{b.id}_s0")
        branch_steps.append((b.id, 1, step))

    dispatch_shadow_branches(bm, store, sandbox, branch_steps, speed=0.01)
    assert len(sandbox._dedup) == 2  # exactly one real dispatch per shadow

    # ---- user says "Actually, Bangalore" (already modeled above); the new
    # primary intent's query is computed the same way the real primary step
    # would be (destination + morning-history slot) ----
    new_state = _state(destination="Bangalore")
    current_params = primary_search_params(new_state)
    assert current_params == {"to": "Bangalore", "slot": "morning"}

    live_ids = {b.id for b in branches}
    winner_id, loser_ids = promote_or_discard(store, "search_flights", current_params, live_ids)

    assert winner_id is not None
    assert store.get(winner_id).params == {"to": "Bangalore", "slot": "morning"}  # genuinely matched
    assert set(loser_ids) == live_ids - {winner_id}

    calls_before_promotion = len(sandbox._dedup)
    wb = bm.promote(winner_id)
    store.mark_reused(winner_id)
    assert wb is not None and wb.state == BranchState.PROMOTED
    assert store.get(winner_id).reused is True
    # promotion must NOT have dispatched anything new — reuse, not recompute
    assert len(sandbox._dedup) == calls_before_promotion

    # ---- nonmatching shadow(s) invalidated + cancelled + cleaned up ----
    for loser_id in loser_ids:
        bm.invalidate(loser_id)
        bm.cancel(loser_id)
        cb = bm.cleanup(loser_id)
        assert cb is not None and cb.state == BranchState.CLEANED_UP

    m = bm.shadow_metrics(wall_ms=1000)
    assert m["spawned"] == 2 and m["promoted"] == 1 and m["discarded"] == 1


# ---------------------------------------------------------------------------
# 9: nonmatching shadow invalidated/cancelled (isolated check via promote_or_discard)
# ---------------------------------------------------------------------------


def test_nonmatching_shadow_is_not_the_winner():
    store = ShadowResultStore()
    from continuum.tools import ToolResult

    morning = ToolResult(
        tool="search_flights",
        external_operation_id=None,
        accepted_at_ms=0.0,
        completed_at_ms=1.0,
        status="COMPLETED",
        payload={"to": "Bangalore", "slot": "morning"},
    )
    evening = ToolResult(
        tool="search_flights",
        external_operation_id=None,
        accepted_at_ms=0.0,
        completed_at_ms=1.0,
        status="COMPLETED",
        payload={"to": "Bangalore", "slot": "evening"},
    )
    from continuum.shadow_store import ShadowResult

    store.put(ShadowResult("A", 1, "search_flights", {"to": "Bangalore", "slot": "morning"}, "s0", morning))
    store.put(ShadowResult("B", 1, "search_flights", {"to": "Bangalore", "slot": "evening"}, "s0", evening))

    winner, losers = promote_or_discard(
        store, "search_flights", {"to": "Bangalore", "slot": "morning"}, {"A", "B"}
    )
    assert winner == "A"
    assert losers == ["B"]


# ---------------------------------------------------------------------------
# 10: late old-version result rejected by the existing stale-result gate
# ---------------------------------------------------------------------------


def test_late_old_version_result_discarded_by_stale_gate():
    graph = ProvenanceGraph()
    from continuum.contracts import ExecutionNode, NodeStatus, Provenance

    node = ExecutionNode(
        id="search:1:0",
        kind="search",
        provenance=Provenance(step_id="search-0", based_on=1),
        status=NodeStatus.RUNNING,
    )
    graph.add(node)
    # user pivots destination -> V2, invalidating the stale Delhi search
    graph.invalidate_affected(2, ArbiterCategory.MODIFY, "destination")
    decision = graph.gate(node, {"flights": ["AI-101 Delhi late"]}, current_version=2)
    assert decision == GateDecision.DISCARD


def test_shadow_bangalore_scenario_end_to_end_trace():
    """Full scenario: real shadow execution -> genuine reuse -> late-Delhi stale discard."""
    summary = replay_scenario(Path("data/scenarios/shadow_bangalore.json"), backend="offline-fake")
    events = [e["event"] for e in summary["trace"]]

    assert events.count("shadow_spawn") == 2
    assert events.count("shadow_result_ready") == 2  # both shadows genuinely executed
    assert "shadow_promoted_reused" in events
    assert "shadow_discard" in events
    assert "stale_discarded" in events  # late Delhi (V1) rejected after V2

    ready = [e for e in summary["trace"] if e["event"] == "shadow_result_ready"]
    assert {e["params"]["slot"] for e in ready} == {"morning", "evening"}  # genuinely distinct dispatches

    reused_evt = next(e for e in summary["trace"] if e["event"] == "shadow_promoted_reused")
    assert reused_evt["saved_tool_call"] is True
    assert reused_evt["params"] == {"to": "Bangalore", "slot": "morning"}

    assert summary["tool_calls_saved"] == 1
    assert len(summary["shadow_results"]) == 2


# ---------------------------------------------------------------------------
# 11: two-shadow budget remains enforced through the orchestrator path
# ---------------------------------------------------------------------------


def test_two_shadow_budget_enforced_with_real_dispatch():
    bm = BranchManager(SpeculationBudget(max_shadow=2))
    store = ShadowResultStore()
    sandbox = MockToolSandbox()
    b1 = bm.spawn_shadow(1, _dec(), kind="search")
    b2 = bm.spawn_shadow(1, _dec(), kind="search")
    b3 = bm.spawn_shadow(1, _dec(), kind="search")
    assert b1 is not None and b2 is not None
    assert b3 is None  # hard cap — never bypassed regardless of dispatch layer

    steps = [
        (b1.id, 1, PlanStep(step_id="s", tool="search_flights", kind="search", params={"to": "A"})),
        (b2.id, 1, PlanStep(step_id="s", tool="search_flights", kind="search", params={"to": "B"})),
    ]
    results = dispatch_shadow_branches(bm, store, sandbox, steps, speed=0.01)
    assert len(results) == 2


# ---------------------------------------------------------------------------
# 12: shadow cannot execute prohibited irreversible/mutating operations
# ---------------------------------------------------------------------------


def test_shadow_dispatch_refuses_irreversible_operation():
    bm = BranchManager()
    store = ShadowResultStore()
    sandbox = MockToolSandbox()
    risky_step = PlanStep(
        step_id="s0", tool="confirm_booking", kind="book", params={"hold_id": "HOLD-1"}
    )
    with pytest.raises(SpeculationPolicyError):
        dispatch_shadow_branches(bm, store, sandbox, [("shadow-x", 1, risky_step)], speed=0.01)
    assert store.get("shadow-x") is None
    assert len(sandbox._dedup) == 0  # never dispatched


async def test_shadow_task_refuses_irreversible_operation():
    bm = BranchManager()
    store = ShadowResultStore()
    sandbox = MockToolSandbox()
    risky_step = PlanStep(step_id="s0", tool="confirm_booking", kind="book", params={"hold_id": "H"})
    with pytest.raises(SpeculationPolicyError):
        await dispatch_shadow_branch_task(bm, store, sandbox, "shadow-y", 1, risky_step, speed=0.01)


# ---------------------------------------------------------------------------
# 13: idempotency preserved through the orchestrator
# ---------------------------------------------------------------------------


def test_idempotency_preserved_through_orchestrator():
    bm = BranchManager()
    store = ShadowResultStore()
    sandbox = MockToolSandbox()
    b = bm.spawn_shadow(1, _dec(), kind="hold")
    step = PlanStep(
        step_id="s0",
        tool="hold_seat",
        kind="hold",
        params={"flight_id": "FL-1"},
        idempotency_key="fixed-key",
    )
    r1 = dispatch_shadow_branches(bm, store, sandbox, [(b.id, 1, step)], speed=0.01)
    r2 = dispatch_shadow_branches(bm, store, sandbox, [(b.id, 1, step)], speed=0.01)
    assert r1[0].result.external_operation_id == r2[0].result.external_operation_id
    assert len(sandbox._dedup) == 1  # never re-dispatched externally


# ---------------------------------------------------------------------------
# 14: genuine cancellation of an in-flight shadow task
# ---------------------------------------------------------------------------


async def test_shadow_task_cancellation_is_real():
    bm = BranchManager()
    store = ShadowResultStore()
    sandbox = MockToolSandbox()
    b = bm.spawn_shadow(1, _dec(), kind="hold")
    assert b is not None
    step = PlanStep(step_id="s0", tool="hold_seat", kind="hold", params={"flight_id": "FL-1"})

    task = asyncio.ensure_future(
        dispatch_shadow_branch_task(bm, store, sandbox, b.id, 1, step, speed=1.0)
    )
    await asyncio.sleep(0)  # let it start awaiting the tool's delay
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.get(b.id) is None  # cancelled before it ever completed/stored
    bm.invalidate(b.id)
    bm.cancel(b.id)
    cb = bm.cleanup(b.id)
    assert cb is not None and cb.state == BranchState.CLEANED_UP


# ---------------------------------------------------------------------------
# 15: primary DAG (from planner.generate_plan, not hand-built) still executes
# independent branches concurrently via the existing execute_plan()
# ---------------------------------------------------------------------------


async def test_generated_plan_executes_with_real_concurrency():
    import time

    state = _state(destination="Bangalore")
    plan = generate_plan("sess", "evt", 1, _dec(conf=0.94), state)  # high conf: no ambiguity shadows
    prefetch = next((b for b in plan.shadow_branches if b.source == "prefetch"), None)
    assert prefetch is not None  # independent cab-search shadow, no dependency on primary

    combined = list(plan.primary_plan) + list(prefetch.steps)
    t0 = time.perf_counter()
    results = await execute_plan(combined, speed=0.05)
    elapsed = time.perf_counter() - t0

    assert results["p_1"].payload["to"] == "Bangalore"
    assert results["sh_1"].payload["route"] == "Bangalore Airport"
    # sequential sum of all 3 steps' scaled delays would be far larger than the
    # critical path (search+hold+confirm chain running alongside the independent cab search)
    assert elapsed < 0.30

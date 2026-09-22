"""Phase 4 DoD — SpeculationBudget enforcement in BranchManager."""

from continuum.branch_manager import BranchManager
from continuum.contracts import (
    ArbiterCategory,
    ArbiterDecision,
    BranchState,
    SpeculationBudget,
)


def _dec():
    return ArbiterDecision(category=ArbiterCategory.MODIFY, confidence=0.6, rationale="x")


def test_max_shadow():
    bm = BranchManager(SpeculationBudget(max_shadow=2))
    assert bm.spawn_shadow(1, _dec()) is not None
    assert bm.spawn_shadow(1, _dec()) is not None
    assert bm.spawn_shadow(1, _dec()) is None  # 3rd refused — hard cap


def test_max_depth():
    bm = BranchManager(SpeculationBudget(max_depth=3))
    assert bm.spawn_shadow(1, _dec(), depth=3) is not None
    assert bm.spawn_shadow(1, _dec(), depth=4) is None  # truncated


def test_read_stage_only_enforced():
    bm = BranchManager()
    for risky in ("book", "pay"):
        assert bm.spawn_shadow(1, _dec(), kind=risky) is None
    assert bm.spawn_shadow(1, _dec(), kind="search") is not None
    assert bm.spawn_shadow(1, _dec(), kind="hold") is not None


def test_cleanup_p95_under_150ms():
    bm = BranchManager()
    for _ in range(5):
        b = bm.spawn_shadow(1, _dec())
        assert b is not None
        bm.invalidate(b.id)
        bm.cancel(b.id)
        bm.cleanup(b.id)  # cleanup() times itself → branch_cleanup_time_ms
    m = bm.shadow_metrics()
    assert m["cleanup_p95_ms"] < 150.0  # PRD: branch cleanup time
    assert m["cleanup_samples"] == 5


def test_busy_pauses_shadows_first():
    bm = BranchManager()
    b = bm.spawn_shadow(1, _dec())
    bm.set_busy(True)
    assert bm.spawn_shadow(1, _dec()) is None  # new spawns refused while busy
    assert bm.is_paused(b.id)  # running shadows paused before anything else
    bm.set_busy(False)
    assert bm.spawn_shadow(1, _dec()) is not None


def test_record_call_cap_kills_shadow():
    budget = SpeculationBudget(max_calls_per_shadow=2)
    bm = BranchManager(budget)
    b = bm.spawn_shadow(1, _dec())
    assert bm.record_call(b.id) and bm.record_call(b.id)
    assert not bm.record_call(b.id)  # 3rd call over budget → shadow dropped
    assert bm._branches[b.id].state == BranchState.CLEANED_UP

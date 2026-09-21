from continuum.branch_manager import BranchManager
from continuum.contracts import SpeculationBudget, ArbiterCategory, ArbiterDecision, BranchState

def _dec(cat=ArbiterCategory.MODIFY):
    return ArbiterDecision(category=cat, confidence=0.9, rationale="x")

def test_budget_max_shadow():
    bm = BranchManager(SpeculationBudget(max_shadow=2))
    b1 = bm.spawn_shadow(1, _dec())
    b2 = bm.spawn_shadow(1, _dec())
    b3 = bm.spawn_shadow(1, _dec())
    assert b1 is not None and b2 is not None
    assert b3 is None  # budget refused

def test_budget_max_depth():
    bm = BranchManager(SpeculationBudget(max_shadow=2, max_depth=3))
    b = bm.spawn_shadow(1, _dec(), depth=4)
    assert b is None

def test_lifecycle_promote():
    bm = BranchManager()
    b = bm.spawn_shadow(1, _dec())
    assert b.state == BranchState.SHADOW
    bm.promote(b.id)
    assert b.state == BranchState.PROMOTED

def test_lifecycle_cancel_cleanup_frees_budget():
    bm = BranchManager(SpeculationBudget(max_shadow=1))
    b = bm.spawn_shadow(1, _dec())
    assert bm.spawn_shadow(1, _dec()) is None  # full
    # full lifecycle: INVALIDATED → CANCELLED → CLEANED_UP
    bm.invalidate(b.id)
    assert b.state == BranchState.INVALIDATED
    bm.cancel(b.id)
    assert b.state == BranchState.CANCELLED
    bm.cleanup(b.id)
    assert b.state == BranchState.CLEANED_UP
    # budget freed
    b2 = bm.spawn_shadow(1, _dec())
    assert b2 is not None

def test_abandoned_frees_budget_immediately():
    bm = BranchManager(SpeculationBudget(max_shadow=1))
    b = bm.spawn_shadow(1, _dec())
    bm.abandon(b.id)
    assert b.state == BranchState.ABANDONED
    b2 = bm.spawn_shadow(1, _dec())
    assert b2 is not None

def test_primary_not_counted_as_shadow():
    bm = BranchManager(SpeculationBudget(max_shadow=1))
    bm.spawn_primary(1, _dec())
    # still can spawn shadow
    assert bm.spawn_shadow(1, _dec()) is not None

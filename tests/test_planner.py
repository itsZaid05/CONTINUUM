"""Plan DAG Generator — deterministic goal->DAG, commitment levels, shadow branches."""

from __future__ import annotations

from continuum.contracts import ArbiterCategory, ArbiterDecision, Delta, DeltaOp
from continuum.planner import affected_steps, generate_plan
from continuum.versioned_state import VersionedStore


def _dec(cat=ArbiterCategory.MODIFY, conf=0.60, field="destination", new="Bangalore", span=""):
    delta = (
        Delta(op=DeltaOp.REPLACE, field=field, old_value="Delhi", new_value=new, span=span)
        if field
        else None
    )
    return ArbiterDecision(category=cat, confidence=conf, delta=delta, rationale=span or "test")


def _state(**kv):
    s = VersionedStore()
    s.create_initial(kv)
    return s.current()


def test_full_chain_has_explicit_dependency_refs():
    plan = generate_plan("sess", "evt", 2, _dec(), _state(destination="Bangalore"))
    ids = [s.step_id for s in plan.primary_plan]
    assert ids == ["p_1", "p_2", "p_3"]
    assert plan.primary_plan[0].tool == "search_flights"
    assert plan.primary_plan[1].params["flight_id"] == "ref(p_1.flight_id)"
    assert plan.primary_plan[2].params["hold_id"] == "ref(p_2.hold_id)"
    assert plan.primary_plan[2].risk.value == "IRREVERSIBLE"


def test_retract_prunes_to_search_only():
    dec = _dec(cat=ArbiterCategory.RETRACT, field=None)
    plan = generate_plan("sess", "evt", 2, dec, _state(destination="Bangalore"))
    assert [s.tool for s in plan.primary_plan] == ["search_flights"]


def test_hold_only_stops_before_irreversible_confirm():
    dec = _dec(cat=ArbiterCategory.ADD_CONSTRAINT, field="commitment", new="hold", span="just hold")
    plan = generate_plan("sess", "evt", 2, dec, _state(destination="Bangalore"))
    tools = [s.tool for s in plan.primary_plan]
    assert tools == ["search_flights", "hold_seat"]
    assert "confirm_booking" not in tools


def test_slot_preserved_from_state():
    plan = generate_plan(
        "sess", "evt", 2, _dec(), _state(destination="Bangalore", constraints=["morning"])
    )
    assert plan.primary_plan[0].params["slot"] == "morning"


def test_prefetch_shadow_matches_pdf_contract_example():
    plan = generate_plan("sess", "evt", 2, _dec(), _state(destination="Bangalore"))
    prefetch = next(b for b in plan.shadow_branches if b.source == "prefetch")
    assert prefetch.branch_id == "SHADOW_1"
    assert prefetch.hypothesis == "User will require Bangalore airport transit"
    assert prefetch.steps[0].tool == "search_cabs"
    assert prefetch.steps[0].risk.value == "FREE"


def test_shadow_branches_never_exceed_budget():
    dec = _dec(conf=0.60)  # inside ShadowScorer's hedge band -> ambiguity shadows also gate open
    plan = generate_plan("sess", "evt", 2, dec, _state(destination="Bangalore", constraints=["morning"]))
    assert len(plan.shadow_branches) <= 2
    assert all(len(b.steps) >= 1 for b in plan.shadow_branches)


def test_no_prefetch_without_destination():
    dec = _dec(field=None)
    plan = generate_plan("sess", "evt", 1, dec, None)
    assert not any(b.source == "prefetch" for b in plan.shadow_branches)


def test_affected_steps_diff_is_surgical_on_pivot():
    old = generate_plan("sess", "evt1", 1, _dec(new="Delhi"), _state(destination="Delhi"))
    new = generate_plan("sess", "evt2", 2, _dec(new="Bangalore"), _state(destination="Bangalore"))
    diff = affected_steps(old, new)
    # only p_1's params (destination) changed; p_2/p_3 are structurally identical refs
    assert diff["changed"] == ["p_1"]
    assert set(diff["reused"]) == {"p_2", "p_3"}


def test_plan_is_json_serializable():
    plan = generate_plan("sess", "evt", 2, _dec(), _state(destination="Bangalore"))
    d = plan.to_dict()
    assert d["session_id"] == "sess"
    assert d["primary_plan"][0]["idempotency_key"] is None  # backend fills this pre-dispatch

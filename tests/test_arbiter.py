from continuum.contracts import ArbiterCategory
from continuum.delta_arbiter import arbitrate_offline_fake
from continuum.versioned_state import VersionedStore


def _store():
    s = VersionedStore()
    s.create_initial({"destination": "Delhi", "goal_domain": "flights"})
    return s


def test_modify_bangalore():
    s = _store()
    d = arbitrate_offline_fake("Actually, Bangalore", s.current())
    assert d.category == ArbiterCategory.MODIFY
    assert d.delta.field == "destination"
    assert d.delta.new_value == "Bangalore"
    assert d.confidence > 0.85


def test_add_constraint():
    s = _store()
    d = arbitrate_offline_fake("…but keep the morning constraint", s.current())
    assert d.category == ArbiterCategory.ADD_CONSTRAINT
    assert d.confidence > 0.85


def test_retract():
    s = _store()
    d = arbitrate_offline_fake("Don't book it", s.current())
    assert d.category == ArbiterCategory.RETRACT
    assert d.delta.op.value == "remove"
    assert d.delta.field == "booking_instruction"


def test_new_goal():
    s = _store()
    d = arbitrate_offline_fake("Forget flights, find trains", s.current())
    assert d.category == ArbiterCategory.NEW_GOAL
    assert d.delta.new_value == "trains"


def test_noise():
    s = _store()
    d = arbitrate_offline_fake("Hmm, okay…", s.current())
    assert d.category == ArbiterCategory.NOISE
    assert d.delta is None


def test_low_confidence_clarification():
    s = _store()
    d = arbitrate_offline_fake("blah random text that is not pattern", s.current())
    # heuristic fallback modify with 0.60 → needs clarification
    assert d.confidence < 0.65


def test_retract_clarification_threshold():
    # Use a weak retract that triggers pattern but low confidence? We test the helper
    from continuum.delta_arbiter import _clarification_for

    assert _clarification_for(ArbiterCategory.RETRACT, None, 0.65) is not None
    assert _clarification_for(ArbiterCategory.RETRACT, None, 0.80) is None


def test_latency_bounds():
    s = _store()
    d = arbitrate_offline_fake("Actually, Bangalore", s.current())
    assert 1 <= d.latency_ms <= 900

"""Phase 3 — dialogue manager templates & selection."""

import pytest

from continuum.contracts import ArbiterCategory, ArbiterDecision, Delta, DeltaOp
from continuum.dialogue import TEMPLATES, respond, respond_to_turn


def _dec(cat, conf=0.9, field="destination", new="Bangalore", clarify=None):
    delta = (
        Delta(op=DeltaOp.REPLACE, field=field, old_value="Delhi", new_value=new, span="")
        if field
        else None
    )
    return ArbiterDecision(
        category=cat, confidence=conf, delta=delta, rationale="r", suggested_clarification=clarify
    )


def test_ten_templates():
    assert len(TEMPLATES) >= 10


def test_unknown_event_raises():
    with pytest.raises(KeyError):
        respond("no-such-template")


def test_select_modify_and_constraint():
    assert "Bangalore" in respond_to_turn(_dec(ArbiterCategory.MODIFY))
    out = respond_to_turn(
        _dec(ArbiterCategory.ADD_CONSTRAINT, field="constraints.time", new="morning")
    )
    assert "morning" in out.lower()


def test_select_retract_variants():
    before = respond_to_turn(_dec(ArbiterCategory.RETRACT))
    assert "pruned" in before.lower()
    after = respond_to_turn(_dec(ArbiterCategory.RETRACT), committed_ref="BLR-11:03")
    assert "already booked" in after.lower() and "BLR-11:03" in after


def test_noise_is_silent():
    d = ArbiterDecision(category=ArbiterCategory.NOISE, confidence=0.9, rationale="r")
    assert d.needs_clarification() is False

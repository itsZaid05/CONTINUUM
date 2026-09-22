"""Phase 3 DoD — CommitGate risk levels + honest retraction messaging."""

from continuum.contracts import (
    ArbiterCategory,
    ArbiterDecision,
    ExecutionNode,
    Provenance,
)
from continuum.policy import CommitGate, GateAction, retraction_after_commit_message, risk_for


def _node(kind: str) -> ExecutionNode:
    return ExecutionNode(
        id=f"{kind}:1:0",
        kind=kind,
        provenance=Provenance(step_id=kind, based_on=1, inputs={}),
    )


def _dec(cat=ArbiterCategory.MODIFY, conf=0.9):
    return ArbiterDecision(category=cat, confidence=conf, rationale="x")


def test_risk_levels():
    assert risk_for("search").value == "FREE"
    assert risk_for("hold").value == "STAGEABLE"
    assert risk_for("cancel").value == "MUTATING"
    assert risk_for("book").value == "IRREVERSIBLE"
    assert risk_for("pay").value == "IRREVERSIBLE"


def test_free_allowed_stageable_staged():
    g = CommitGate()
    assert g.can_execute(_node("search")) == GateAction.ALLOW
    assert g.can_execute(_node("filter")) == GateAction.ALLOW
    assert g.can_execute(_node("hold")) == GateAction.STAGE


def test_irreversible_requires_confirm():
    g = CommitGate()
    # PRD/judge-safety: IRREVERSIBLE without explicit confirm → blocked
    assert g.can_execute(_node("book")) == GateAction.CONFIRM_REQUIRED
    assert g.can_execute(_node("pay")) == GateAction.CONFIRM_REQUIRED
    assert g.can_execute(_node("book"), confirmed=True) == GateAction.ALLOW


def test_retract_blocks_booking():
    g = CommitGate()
    # user just said "don't book it" — pending booking is BLOCKED outright
    assert g.can_execute(_node("book"), _dec(ArbiterCategory.RETRACT, 0.9)) == GateAction.BLOCK
    # even with confirm — a retraction outranks confirmation
    assert (
        g.can_execute(_node("book"), _dec(ArbiterCategory.RETRACT, 0.9), confirmed=True)
        == GateAction.BLOCK
    )


def test_mutating_low_confidence_needs_confirm():
    g = CommitGate()
    weak = _dec(ArbiterCategory.ADD_CONSTRAINT, 0.5)
    assert g.can_execute(_node("cancel"), weak) == GateAction.CONFIRM_REQUIRED
    strong = _dec(ArbiterCategory.ADD_CONSTRAINT, 0.9)
    assert g.can_execute(_node("cancel"), strong) == GateAction.ALLOW


def test_honest_retraction_message():
    msg = retraction_after_commit_message("BLR-11:03")
    assert "already booked" in msg.lower()
    assert "BLR-11:03" in msg  # ref interpolated
    assert "cancel" in msg.lower()
    assert "undone" not in msg.lower()  # never claims it was undone

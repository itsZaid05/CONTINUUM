"""Contract tests — 22 tests covering all schemas (PRD Phase 1)."""

import pytest

from continuum.contracts import (
    ArbiterCategory,
    ArbiterDecision,
    Branch,
    BranchState,
    Delta,
    DeltaOp,
    EffectRecord,
    EffectStatus,
    EvidenceSpan,
    ExecutionNode,
    GateDecision,
    Modality,
    NodeStatus,
    PerceptionOutput,
    Provenance,
    RiskLevel,
    SpeculationBudget,
    StateStatus,
    StateVersion,
)


def test_evidence_span_requires_text():
    with pytest.raises(Exception):
        EvidenceSpan(text="   ", modality=Modality.TEXT)


def test_evidence_span_valid():
    ev = EvidenceSpan(text="hello", modality=Modality.TEXT)
    assert ev.text == "hello"
    assert ev.transcript_confidence == 1.0


def test_evidence_span_invalid_bbox():
    # no validation on bbox yet, but model should accept None
    ev = EvidenceSpan(text="x", modality=Modality.VISION, vision_bbox=(0, 0, 10, 10))
    assert ev.vision_bbox == (0, 0, 10, 10)


def test_evidence_span_end_before_start():
    with pytest.raises(Exception):
        EvidenceSpan(text="x", start_ms=10, end_ms=5)


def test_perception_output_requires_evidence():
    with pytest.raises(Exception):
        PerceptionOutput(version_in=1, evidences=[], fast_ack="hi", fast_ack_latency_ms=5)


def test_delta_noise_has_no_op():
    d = Delta(op=None)
    assert d.field is None
    d2 = Delta(
        op=DeltaOp.REPLACE,
        field="destination",
        old_value="Delhi",
        new_value="Bangalore",
        span=" Actually, Bangalore",
    )
    assert d2.field == "destination"


def test_delta_requires_field_when_op_set():
    with pytest.raises(Exception):
        Delta(op=DeltaOp.REPLACE, field=None)


def test_arbiter_decision_confidence_clamp():
    d = ArbiterDecision(category=ArbiterCategory.MODIFY, confidence=1.5, rationale="x")
    assert d.confidence == 1.0
    d2 = ArbiterDecision(category=ArbiterCategory.NOISE, confidence=-0.2, rationale="x")
    assert d2.confidence == 0.0


def test_arbiter_needs_clarification_risky():
    d = ArbiterDecision(category=ArbiterCategory.RETRACT, confidence=0.70, rationale="x")
    assert d.needs_clarification() is True
    d2 = ArbiterDecision(category=ArbiterCategory.RETRACT, confidence=0.80, rationale="x")
    assert d2.needs_clarification() is False
    d3 = ArbiterDecision(category=ArbiterCategory.NOISE, confidence=0.59, rationale="x")
    assert d3.needs_clarification() is True


def test_arbiter_needs_clarification_modify_general():
    d = ArbiterDecision(category=ArbiterCategory.MODIFY, confidence=0.59, rationale="x")
    assert d.needs_clarification() is True
    d2 = ArbiterDecision(category=ArbiterCategory.MODIFY, confidence=0.61, rationale="x")
    assert d2.needs_clarification() is False


def test_state_version_frozen_and_diff():
    v1 = StateVersion(version=1, state={"destination": "Delhi"})
    v2 = StateVersion(version=2, parent=1, state={"destination": "Bangalore"})
    diff = v1.diff(v2)
    assert "destination" in diff
    assert diff["destination"]["old"] == "Delhi"


def test_provenance_axes_validation():
    with pytest.raises(Exception):
        Provenance(step_id="s1", based_on=1, axes={"freshness": 1.5})
    p = Provenance(step_id="s1", based_on=1, axes={"freshness": 0.9})
    assert p.axes["freshness"] == 0.9


def test_provenance_merge_min():
    p1 = Provenance(step_id="a", based_on=1, axes={"freshness": 0.8})
    p2 = Provenance(step_id="b", based_on=1, axes={"freshness": 0.5})
    merged = p1.merge(p2)
    assert merged.axes["freshness"] == 0.5
    # tainted tracking
    assert "b" in merged.tainted_by["freshness"]


def test_execution_node_default():
    p = Provenance(step_id="s", based_on=1)
    n = ExecutionNode(id="n1", kind="search", provenance=p)
    assert n.status == NodeStatus.PENDING


def test_branch_lifecycle_defaults():
    dec = ArbiterDecision(category=ArbiterCategory.MODIFY, confidence=0.9, rationale="x")
    b = Branch(id="b1", parent_version=1, hypothesis=dec)
    assert b.state == BranchState.CREATED
    assert b.is_primary is False
    b.state = BranchState.SHADOW
    assert b.state == BranchState.SHADOW


def test_speculation_budget_defaults():
    b = SpeculationBudget()
    assert b.max_shadow == 2
    assert b.max_depth == 3
    assert RiskLevel.FREE in b.allowed_levels
    assert RiskLevel.IRREVERSIBLE not in b.allowed_levels


def test_effect_record_default_unknown():
    r = EffectRecord(effect_id="abc", tool="book", args_hash="deadbeef")
    assert r.status == EffectStatus.UNKNOWN


def test_gate_decision_values():
    assert GateDecision.APPLY.value == "APPLY"
    assert GateDecision.DISCARD.value == "DISCARD"


def test_risk_levels_complete():
    assert RiskLevel.FREE
    assert RiskLevel.STAGEABLE
    assert RiskLevel.MUTATING
    assert RiskLevel.IRREVERSIBLE


def test_perception_output_latency_bound():
    ev = EvidenceSpan(text="hi")
    out = PerceptionOutput(version_in=1, evidences=[ev], fast_ack="👍", fast_ack_latency_ms=12)
    assert 0 <= out.fast_ack_latency_ms <= 500


def test_state_status_values():
    assert StateStatus.ACTIVE
    assert StateStatus.MERGED
    assert StateStatus.ABANDONED


def test_arbiter_category_all_five():
    assert set(c.value for c in ArbiterCategory) == {
        "NEW_GOAL",
        "MODIFY",
        "ADD_CONSTRAINT",
        "RETRACT",
        "NOISE",
    }

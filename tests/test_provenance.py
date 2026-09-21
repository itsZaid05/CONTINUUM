from continuum.provenance import ProvenanceGraph, dependents_for
from continuum.contracts import ArbiterCategory, ExecutionNode, Provenance, NodeStatus, GateDecision


def _node(kind, based_on, nid=None):
    prov = Provenance(step_id=f"{kind}-0", based_on=based_on)
    return ExecutionNode(id=nid or f"{kind}:{based_on}:0", kind=kind, provenance=prov, status=NodeStatus.COMPLETED)

def test_dependents_for():
    assert "search" in dependents_for(ArbiterCategory.MODIFY, "destination")
    assert dependents_for(ArbiterCategory.NOISE, None) == set()
    assert "filter" in dependents_for(ArbiterCategory.ADD_CONSTRAINT, "constraints")
    assert len(dependents_for(ArbiterCategory.NEW_GOAL, None)) > 4

def test_invalidate_selective():
    g = ProvenanceGraph()
    n_search = _node("search", based_on=1)
    n_inform = _node("inform", based_on=1)
    g.add(n_search); g.add(n_inform)
    # MODIFY destination should invalidate search but not inform? inform not in dependents_for destination (search set) so inform stays
    invalidated = g.invalidate_affected(current_version=2, category=ArbiterCategory.MODIFY, field="destination")
    assert n_search.id in invalidated
    assert n_inform.id not in invalidated  # inform not dependent on destination
    assert g.get(n_search.id).status == NodeStatus.INVALIDATED
    assert g.get(n_inform.id).status == NodeStatus.COMPLETED

def test_stale_gate_discard():
    g = ProvenanceGraph()
    n = _node("search", based_on=1)
    g.add(n)
    # Invalidate then gate
    g.invalidate_affected(2, ArbiterCategory.MODIFY, "destination")
    decision = g.gate(n, {"flights": []}, current_version=2)
    assert decision == GateDecision.DISCARD

def test_stale_gate_apply_fresh():
    g = ProvenanceGraph()
    n = _node("search", based_on=2)
    g.add(n)
    decision = g.gate(n, {"flights": ["AI-201"]}, current_version=2)
    assert decision == GateDecision.APPLY

def test_is_stale_version_check():
    g = ProvenanceGraph()
    n = _node("search", based_on=1)
    assert g.is_stale(n, 2) is True
    n2 = _node("search", based_on=2)
    assert g.is_stale(n2, 2) is False

def test_reused_count():
    g = ProvenanceGraph()
    g.add(_node("search", 1, "s1"))
    g.add(_node("filter", 1, "f1"))
    g.invalidate_affected(2, ArbiterCategory.MODIFY, "destination")
    # search invalidated, filter? filter is in destination dependents? check
    # destination dependents includes filter, so both invalidated
    # Let's test a narrow ADD_CONSTRAINT only filter
    g2 = ProvenanceGraph()
    g2.add(_node("search", 1, "s1"))
    g2.add(_node("filter", 1, "f1"))
    g2.invalidate_affected(2, ArbiterCategory.ADD_CONSTRAINT, "constraints")
    # only filter should be invalidated
    assert g2.get("f1").status == NodeStatus.INVALIDATED
    assert g2.get("s1").status == NodeStatus.COMPLETED
    assert g2.reused_count() == 1

def test_new_goal_invalidates_all():
    g = ProvenanceGraph()
    for kind in ["search","filter","price","book","pay","inform"]:
        g.add(_node(kind, 1, kind))
    g.invalidate_affected(2, ArbiterCategory.NEW_GOAL, None)
    assert all(g.get(k).status == NodeStatus.INVALIDATED for k in ["search","filter","price","book","pay","inform"])

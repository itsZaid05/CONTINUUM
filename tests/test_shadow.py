"""Phase 4 — ShadowScorer: gate, branching factor, hypotheses, metrics, logging."""

from pathlib import Path

from continuum.branch_manager import BranchManager
from continuum.contracts import (
    ArbiterCategory,
    ArbiterDecision,
    Delta,
    DeltaOp,
    SpeculationBudget,
)
from continuum.shadow import ShadowScorer, kind_for_hypothesis
from continuum.versioned_state import VersionedStore


def _dec(cat=ArbiterCategory.MODIFY, conf=0.60, field="destination", new="Bangalore"):
    delta = (
        Delta(op=DeltaOp.REPLACE, field=field, old_value="Delhi", new_value=new, span="x")
        if field
        else None
    )
    return ArbiterDecision(category=cat, confidence=conf, delta=delta, rationale="test")


def _state():
    s = VersionedStore()
    s.create_initial({"destination": "Bangalore", "constraints": ["morning"]})
    return s.current()


def test_no_spawn_high_confidence():
    # 0.94 → trusted primary, no hedging (FlowContext gate upper bound)
    assert not ShadowScorer().should_spawn(_dec(conf=0.94), _state())


def test_spawn_medium_confidence():
    assert ShadowScorer().should_spawn(_dec(conf=0.60), _state())


def test_no_spawn_retract():
    # RETRACT replaces work — shadows would be pure waste
    assert not ShadowScorer().should_spawn(_dec(cat=ArbiterCategory.RETRACT, conf=0.6), _state())


def test_no_spawn_noise():
    assert not ShadowScorer().should_spawn(_dec(cat=ArbiterCategory.NOISE, conf=0.9), _state())


def test_k_eff_branching_factor():
    sc = ShadowScorer()
    hyps = sc.score(_dec(), _state())
    assert len(hyps) == 2
    assert sc.k_eff(hyps) >= 1.2
    assert sc.k_eff([]) == 1.0
    # morning in history → boost + flip (plan: "user often says morning after city change")
    assert hyps[0].label == "Bangalore morning"
    assert hyps[0].score > hyps[1].score
    assert kind_for_hypothesis("book the flight now") == "book"


def test_branch_enforces_read_stage_only():
    bm = BranchManager()
    dec = _dec(cat=ArbiterCategory.RETRACT)
    # RETRACT→book bottoms out in IRREVERSIBLE: must be refused
    assert bm.spawn_shadow(1, dec, kind="book") is None
    assert bm.spawn_shadow(1, dec, kind="pay") is None
    # READ/STAGE allowed
    assert bm.spawn_shadow(1, dec, kind="search") is not None
    assert bm.is_allowed_kind("hold")  # STAGEABLE
    assert not bm.is_allowed_kind("cancel")  # MUTATING


def test_spawn_shadows_for_decision():
    bm = BranchManager()
    branches = bm.spawn_shadows_for_decision(_dec(), 2, _state(), ShadowScorer(), force=True)
    assert len(branches) == 2
    assert {bm.label_of(b.id) for b in branches} == {"Bangalore morning", "Bangalore evening"}
    # no force + high conf → nothing spawns (gate respected)
    bm2 = BranchManager()
    assert bm2.spawn_shadows_for_decision(_dec(conf=0.94), 2, _state(), ShadowScorer()) == []


def test_budget_caps_spawn():
    bm = BranchManager(SpeculationBudget(max_shadow=1))
    branches = bm.spawn_shadows_for_decision(_dec(), 2, _state(), ShadowScorer(), force=True)
    assert len(branches) == 1  # 2nd refused by capacity


def test_metrics_reused_wasted():
    bm = BranchManager()
    branches = bm.spawn_shadows_for_decision(_dec(), 2, _state(), ShadowScorer(), force=True)
    win, lose = branches
    bm.promote(win.id)
    bm.invalidate(lose.id)
    bm.cancel(lose.id)
    bm.cleanup(lose.id)
    m = bm.shadow_metrics(wall_ms=1100)
    assert m["spawned"] == 2 and m["promoted"] == 1 and m["discarded"] == 1
    assert m["reused_pct"] == 50.0 and m["wasted_pct"] == 50.0
    assert m["primary_slowdown_pct"] <= 5.0
    assert m["by_state"]["PROMOTED"] == 1 and m["by_state"]["CLEANED_UP"] == 1


def test_log_scores(tmp_path: Path):
    sc = ShadowScorer()
    rows = [
        {
            "scenario": "t",
            "hypotheses": [
                {"label": h.label, "score": h.score, "kind": h.kind}
                for h in sc.score(_dec(), _state())
            ],
        }
    ]
    p = sc.log_scores(rows, tmp_path / "shadow_scores.jsonl")
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 1 and "Bangalore morning" in lines[0]

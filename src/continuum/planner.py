"""
Plan DAG Generator — deterministic goal -> dependency DAG (AI/ML Engineer B).

Zero-LLM-latency by design: turning a triaged decision (Engineer A's fused
Delta+Arbiter output) into a plan DAG is a template lookup keyed on
``(category, field)``, not a model call — sub-millisecond versus the ~250ms
a second LLM round trip would cost, and it burns zero extra tokens, which is
exactly what the PRD's Token Savings % metric rewards. The templates encode
the *shape* of a travel-booking task (search -> hold -> confirm); concrete
values are threaded through as ``ref(step_id.field)`` parameter links
(Contract 2), so downstream steps stay bound to upstream tool results
instead of duplicating literals the planner hasn't seen yet.

Two independent sources feed ``shadow_branches``, both bounded by the same
2-shadow budget the Branch Manager enforces:

  * prefetch speculation — "what will the user need right after this plan
    lands" (e.g. an airport cab once a destination is set). This is new:
    forward-looking, complementary work, never an alternative reading of
    the same turn.
  * ambiguity hedging — delegated to ``shadow.ShadowScorer`` (already
    built), which asks "what ELSE might this ambiguous turn have meant".

Combining both under one budget is what turns "2 shadow slots" into
"2 *useful* shadow slots" instead of spending both on one flavor of guess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .contracts import ArbiterCategory, ArbiterDecision, RiskLevel, StateVersion
from .policy import risk_for
from .shadow import ScoredHypothesis, ShadowScorer

# ---------------------------------------------------------------------------
# Plan shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    tool: str
    kind: str
    params: dict[str, Any]
    depends_on: list[str] = field(default_factory=list)
    idempotency_key: str | None = None  # filled by the backend right before dispatch

    @property
    def risk(self) -> RiskLevel:
        return risk_for(self.kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "tool": self.tool,
            "params": self.params,
            "risk": self.risk.value,
            "idempotency_key": self.idempotency_key,
            "depends_on": self.depends_on,
        }


@dataclass(frozen=True)
class ShadowBranchPlan:
    branch_id: str
    base_version: int
    hypothesis: str
    source: str  # "prefetch" | "ambiguity"
    score: float
    steps: list[PlanStep]

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch_id": self.branch_id,
            "base_version": self.base_version,
            "hypothesis": self.hypothesis,
            "source": self.source,
            "score": self.score,
            "steps": [s.to_dict() for s in self.steps],
        }


@dataclass(frozen=True)
class Plan:
    session_id: str
    event_id: str
    base_version: int
    primary_plan: list[PlanStep]
    shadow_branches: list[ShadowBranchPlan]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "event_id": self.event_id,
            "base_version": self.base_version,
            "primary_plan": [s.to_dict() for s in self.primary_plan],
            "shadow_branches": [b.to_dict() for b in self.shadow_branches],
        }


# ---------------------------------------------------------------------------
# Commitment-level detection — how far the primary chain should go
# ---------------------------------------------------------------------------

_HOLD_WORDS = ("hold", "stage", "draft", "cart", "don't book", "not yet", "just hold")
_DROP_WORDS = ("book", "reservation", "confirm")

MAX_SHADOWS = 2  # mirrors SpeculationBudget.max_shadow — no LLM/config needed to know this


def _commitment_for(decision: ArbiterDecision) -> str:
    """search-only | hold-only | full — deterministic, no LLM."""
    if decision.category == ArbiterCategory.RETRACT:
        return "search"  # Demo Case 2A: prune booking, keep search options
    span = (decision.delta.span if decision.delta else "") or decision.rationale
    low = span.lower()
    if any(w in low for w in _HOLD_WORDS):
        return "hold"  # Demo Case 2B: STAGEABLE hold, never IRREVERSIBLE confirm
    return "full"


def _slot_for(state: StateVersion | None) -> str:
    blob = json.dumps(state.state, default=str).lower() if state else ""
    return "morning" if "morning" in blob else "evening"


def _destination_for(decision: ArbiterDecision, state: StateVersion | None) -> str | None:
    if decision.delta and decision.delta.field in {"destination", "city", "from", "to"}:
        return decision.delta.new_value
    if state is not None:
        return state.state.get("destination")
    return None


def primary_search_params(state: StateVersion | None) -> dict[str, Any]:
    """The ``{"to", "slot"}`` params the primary ``search_flights`` step would
    dispatch for the given state — the same shape `_build_primary` uses for
    ``p_1``. Exposed so the runtime can ask "does a live shadow already
    answer *this exact* query?" without recomputing/duplicating the logic.
    """
    return {
        "to": (state.state.get("destination") if state else None) or "unknown",
        "slot": _slot_for(state),
    }


# ---------------------------------------------------------------------------
# Primary chain
# ---------------------------------------------------------------------------


def _build_primary(decision: ArbiterDecision, state: StateVersion | None) -> list[PlanStep]:
    destination = _destination_for(decision, state) or "unknown"
    slot = _slot_for(state)
    commitment = _commitment_for(decision)

    p1 = PlanStep(
        step_id="p_1",
        tool="search_flights",
        kind="search",
        params={"to": destination, "slot": slot},
    )
    steps = [p1]
    if commitment == "search":
        return steps

    p2 = PlanStep(
        step_id="p_2",
        tool="hold_seat",
        kind="hold",
        params={"flight_id": "ref(p_1.flight_id)"},
        depends_on=["p_1"],
    )
    steps.append(p2)
    if commitment == "hold":
        return steps

    p3 = PlanStep(
        step_id="p_3",
        tool="confirm_booking",
        kind="book",
        params={"hold_id": "ref(p_2.hold_id)"},
        depends_on=["p_2"],
    )
    steps.append(p3)
    return steps


# ---------------------------------------------------------------------------
# Shadow branches
# ---------------------------------------------------------------------------


def _prefetch_shadow(
    destination: str | None, base_version: int, has_flight_step: bool
) -> ShadowBranchPlan | None:
    """Speculate the next logical step after the primary plan lands: an
    airport cab at the (now-known) destination. Pure FREE read, depth 1,
    no dependency on the primary chain finishing — matches PDF Demo Case 1
    and Contract 2's own SHADOW_1 example exactly.
    """
    if not destination or destination == "unknown" or not has_flight_step:
        return None
    cab_step = PlanStep(
        step_id="sh_1",
        tool="search_cabs",
        kind="search",
        params={"to": f"{destination} Airport"},
    )
    return ShadowBranchPlan(
        branch_id="SHADOW_1",
        base_version=base_version,
        hypothesis=f"User will require {destination} airport transit",
        source="prefetch",
        score=0.55,
        steps=[cab_step],
    )


def _params_for_hypothesis(h: ScoredHypothesis) -> dict[str, Any]:
    """Structured params when the label is parseable as "<city> <slot>"
    (the shape ShadowScorer always produces for search/filter hypotheses —
    see shadow.py's 'Bangalore morning' / 'Bangalore evening' labels), so the
    step is genuinely executable and genuinely matchable against a later
    primary query — not just a free-text hint no one can compare against.
    """
    if h.kind in {"search", "filter"}:
        city, sep, slot = h.label.rpartition(" ")
        if sep and slot in {"morning", "evening"} and city:
            return {"to": city, "slot": slot}
    return {"hint": h.label}


def step_for_hypothesis(h: ScoredHypothesis, step_id: str) -> PlanStep:
    """Turn a scored ambiguity hypothesis into a real, executable PlanStep.

    Shared by `_ambiguity_shadows` (plan generation) and the replay runtime
    (genuine shadow dispatch via orchestrator.py) so the two can never drift:
    the step a shadow branch actually executes is exactly the step the plan
    says it should.
    """
    tool = "search_flights" if h.kind in {"search", "filter"} else "hold_seat"
    return PlanStep(step_id=step_id, tool=tool, kind=h.kind, params=_params_for_hypothesis(h))


def _ambiguity_shadows(
    decision: ArbiterDecision,
    state: StateVersion | None,
    base_version: int,
    scorer: ShadowScorer,
    slots_left: int,
) -> list[ShadowBranchPlan]:
    """Delegate to the existing hedge engine (shadow.ShadowScorer) for
    'what else might this ambiguous turn have meant' — never re-implemented
    here, only adapted into PlanStep/ShadowBranchPlan shape.
    """
    if slots_left <= 0 or not scorer.should_spawn(decision, state):
        return []
    out: list[ShadowBranchPlan] = []
    for i, h in enumerate(scorer.score(decision, state)[:slots_left], start=2):
        step = step_for_hypothesis(h, f"sh_{i}")
        out.append(
            ShadowBranchPlan(
                branch_id=f"SHADOW_{i}",
                base_version=base_version,
                hypothesis=h.label,
                source="ambiguity",
                score=h.score,
                steps=[step],
            )
        )
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def generate_plan(
    session_id: str,
    event_id: str,
    base_version: int,
    decision: ArbiterDecision,
    state: StateVersion | None = None,
    *,
    scorer: ShadowScorer | None = None,
) -> Plan:
    """Convert a triaged decision + current state into a Contract-2-shaped
    Plan: no LLM call, no I/O, deterministic — safe to call on every pivot.
    """
    scorer = scorer or ShadowScorer()
    primary = _build_primary(decision, state)
    destination = _destination_for(decision, state)
    has_flight_step = any(s.tool == "search_flights" for s in primary)

    shadows: list[ShadowBranchPlan] = []
    prefetch = _prefetch_shadow(destination, base_version, has_flight_step)
    if prefetch is not None:
        shadows.append(prefetch)
    shadows.extend(
        _ambiguity_shadows(decision, state, base_version, scorer, MAX_SHADOWS - len(shadows))
    )

    return Plan(
        session_id=session_id,
        event_id=event_id,
        base_version=base_version,
        primary_plan=primary,
        shadow_branches=shadows[:MAX_SHADOWS],
    )


def affected_steps(old: Plan, new: Plan) -> dict[str, list[str]]:
    """Surgical diff between two plans on the same chain: which step_ids
    changed vs are byte-identical and can be preserved (feeds the same
    'reuse unaffected nodes' invariant the Provenance DAG enforces).
    """
    old_by_id = {s.step_id: s for s in old.primary_plan}
    new_by_id = {s.step_id: s for s in new.primary_plan}
    changed = [
        sid
        for sid, s in new_by_id.items()
        if sid not in old_by_id or old_by_id[sid].params != s.params
    ]
    reused = [sid for sid in new_by_id if sid not in changed]
    dropped = [sid for sid in old_by_id if sid not in new_by_id]
    return {"changed": changed, "reused": reused, "dropped": dropped}

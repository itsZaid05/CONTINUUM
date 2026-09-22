"""
Shadow Scorer — decides WHEN and WHAT to speculate (Phase 4).

Speculation only "pays" if it is cheap, plausible and harmless. Three rules:

1. Confidence gate (borrowed from FlowContext scheduling):
   spawn shadows only when arbiter confidence sits in [0.55, 0.72) —
   above that the primary path is trusted; below that we ASK instead.
   Categories allowed to spawn: MODIFY / ADD_CONSTRAINT (never RETRACT /
   NEW_GOAL — those cancel or replace the task, shadows would be wasted).

2. k_eff branching factor (speculative decoding): hedging is worth it only
   when there are ≥2 distinct plausible hypotheses (k_eff ≥ 1.2).

3. READ / STAGE only: a hypothesis that bottoms out in book/pay (MUTATING
   or IRREVERSIBLE risk) is never speculated — see BranchManager budget.

Scores are rule-based for MVP determinism and logged to
reports/shadow_scores.jsonl so the reused/wasted split is auditable.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .contracts import ArbiterCategory, ArbiterDecision, RiskLevel, StateVersion
from .policy import risk_for

# FlowContext gate band: uncertain enough to hedge, not so uncertain to ask
CONF_LO = 0.55
CONF_HI = 0.72
SHADOW_CATEGORIES: set[ArbiterCategory] = {
    ArbiterCategory.MODIFY,
    ArbiterCategory.ADD_CONSTRAINT,
}
MIN_K_EFF = 1.2  # need a real fork, not a rounding error

# Words that make a hypothesis risky — never speculated
_RISKY_WORDS = ("book", "pay", "send", "purchase", "charge")
_STAGE_WORDS = ("hold", "stage", "draft", "cart")


@dataclass(frozen=True)
class ScoredHypothesis:
    """One plausible world we can pre-compute for free."""

    label: str
    kind: str  # simulated tool kind (search/filter/price/hold/book)
    score: float
    risk: RiskLevel
    delta_field: str | None = None
    new_value: Any = None

    def is_speculatable(self, allowed_levels: set[RiskLevel]) -> bool:
        """READ/STAGE only — FREE or STAGEABLE risk passes, MUTATING/IRREVERSIBLE never."""
        return self.risk in allowed_levels


def kind_for_hypothesis(label: str) -> str:
    """Map a hypothesis label to the tool kind it would run, for risk checking."""
    low = label.lower()
    if any(w in low for w in _RISKY_WORDS):
        return "book"
    if any(w in low for w in _STAGE_WORDS):
        return "hold"
    if "filter" in low or "morning" in low or "evening" in low or "cheapest" in low:
        return "filter"
    return "search"


def _state_blob(state: StateVersion | dict[str, Any] | None) -> str:
    if state is None:
        return ""
    if isinstance(state, StateVersion):
        state = state.state
    return json.dumps(state, default=str).lower()


def _value_str(v: Any) -> str:
    return str(v) if v is not None else "current"


class ShadowScorer:
    """Rank the top-k hedge hypotheses for an arbiter decision."""

    def __init__(
        self,
        max_hypotheses: int = 2,
        conf_lo: float = CONF_LO,
        conf_hi: float = CONF_HI,
        min_k_eff: float = MIN_K_EFF,
    ) -> None:
        self.max_hypotheses = max_hypotheses
        self.conf_lo = conf_lo
        self.conf_hi = conf_hi
        self.min_k_eff = min_k_eff

    # ------------------------------------------------------------------ gate
    def should_spawn(self, decision: ArbiterDecision, state: StateVersion | dict | None) -> bool:
        """FlowContext gate: category + confidence band + real branching factor."""
        if decision.category not in SHADOW_CATEGORIES:
            return False
        if not (self.conf_lo <= decision.confidence < self.conf_hi):
            return False
        hyps = self.score(decision, state)
        return len(hyps) >= 2 and self.k_eff(hyps) >= self.min_k_eff

    def k_eff(self, hypotheses: list[ScoredHypothesis]) -> float:
        """Effective branching factor: 1 + mass of alternative hypotheses."""
        if not hypotheses:
            return 1.0
        alt = sum(h.score for h in hypotheses[1:])
        return round(1.0 + alt, 3)

    # ----------------------------------------------------------------- score
    def score(
        self, decision: ArbiterDecision, state: StateVersion | dict[str, Any] | None
    ) -> list[ScoredHypothesis]:
        """Top-ranked hypotheses to pre-compute. Empty when nothing sane to hedge.

        City MODIFY: primary = new city + current slot preference (0.60),
        alt = new city + flipped time-of-day (0.24). A `morning` already in
        state boosts the primary (user-history pattern from the plan).
        ADD_CONSTRAINT: primary = value applied, alt = value dropped (undo
        hedge, since constraints are the most-retracted edit).
        """
        blob = _state_blob(state)
        field = decision.delta.field if decision.delta else None
        value = decision.delta.new_value if decision.delta else None
        out: list[ScoredHypothesis] = []

        if (
            decision.category == ArbiterCategory.MODIFY
            and field in {"destination", "city", "from", "to"}
            and value is not None
        ):
            city = _value_str(value)
            has_morning = "morning" in blob
            slot = "morning" if has_morning else "evening"
            flipped = "evening" if has_morning else "morning"
            primary_score = 0.60 + (0.05 if has_morning else 0.0)
            out.append(
                ScoredHypothesis(
                    label=f"{city} {slot}",
                    kind="search",
                    score=round(primary_score, 3),
                    risk=risk_for("search"),
                    delta_field=field,
                    new_value=value,
                )
            )
            out.append(
                ScoredHypothesis(
                    label=f"{city} {flipped}",
                    kind="filter",
                    score=0.24,
                    risk=risk_for("filter"),
                    delta_field=field,
                    new_value=value,
                )
            )
        elif (
            decision.category == ArbiterCategory.MODIFY
            and value is not None
            and field not in {"booking", "reservation"}
        ):
            base = _value_str(value)
            out.append(
                ScoredHypothesis(
                    label=f"{base}",
                    kind="search",
                    score=0.60,
                    risk=risk_for("search"),
                    delta_field=field,
                    new_value=value,
                )
            )
            out.append(
                ScoredHypothesis(
                    label=f"{base} (re-filter)",
                    kind="filter",
                    score=0.24,
                    risk=risk_for("filter"),
                    delta_field=field,
                    new_value=value,
                )
            )
        elif decision.category == ArbiterCategory.ADD_CONSTRAINT and field:
            out.append(
                ScoredHypothesis(
                    label=f"apply {field}={_value_str(value)}",
                    kind="filter",
                    score=0.60,
                    risk=risk_for("filter"),
                    delta_field=field,
                    new_value=value,
                )
            )
            out.append(
                ScoredHypothesis(
                    label=f"drop {field} (undo hedge)",
                    kind="search",
                    score=0.24,
                    risk=risk_for("search"),
                    delta_field=field,
                    new_value=value,
                )
            )

        # never speculate on risky bottoms-out: a label that reads as book/pay
        # is dropped unless the hypothesis kind itself is a risky op (then the
        # BranchManager's allowed_levels check refuses it — READ/STAGE only)
        out = [
            h
            for h in out
            if h.risk in {RiskLevel.FREE, RiskLevel.STAGEABLE}
            and (kind_for_hypothesis(h.label) != "book" or h.kind == "book")
        ]
        out.sort(key=lambda h: (-h.score, h.label))
        return out[: self.max_hypotheses]

    # ----------------------------------------------------------------- logging
    def log_scores(
        self,
        rows: list[dict[str, Any]],
        path: Path | str = Path("reports/shadow_scores.jsonl"),
    ) -> Path:
        """Append one JSONL line per scored decision (auditable speculation)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, default=str) + "\n")
        return p


def hypothesis_to_row(h: ScoredHypothesis) -> dict[str, Any]:
    d = asdict(h)
    d["risk"] = h.risk.value
    return d

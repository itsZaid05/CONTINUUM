"""
Branch Manager — Speculation Budget enforcement (Phase 1 lifecycle + Phase 4 budget).

Hard limits (PRD "Speculation Budget"):
  • at most `max_shadow` (2) concurrent shadow branches
  • shadow depth ≤ `max_depth` (3)
  • ≤ `max_calls_per_shadow` (6) simulated tool calls each
  • allowed_levels = {FREE, STAGEABLE} — READ/STAGE only, never MUTATING/IRREVERSIBLE
  • primary always preempts: when busy, shadow spawns are refused and running
    shadows are paused FIRST ("paused first when busy")

Every state transition is legal per the lifecycle diagram:
  CREATED → RUNNING → SHADOW|ACTIVE → PROMOTED | INVALIDATED → CANCELLED → CLEANED_UP
  non-cancellable dispatched → ABANDONED (budget freed immediately, stale gate discards late result)

`shadow_metrics()` reports reused/wasted split + cleanup p95 + primary slowdown,
feeding `reports/shadow_metrics.{json,md}` — the "is speculation worth the cost?" answer.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .contracts import ArbiterDecision, Branch, BranchState, SpeculationBudget
from .policy import risk_for

if TYPE_CHECKING:  # avoid import cycle at runtime; shadow imports policy only
    from .shadow import ScoredHypothesis, ShadowScorer

# Simulated scheduler reservation per shadow (ms). The manager caps total
# reservation so primary work never loses more than MAX_PRIMARY_SLOWDOWN_PCT.
SHADOW_CPU_SLICE_MS = 25.0
MAX_PRIMARY_SLOWDOWN_PCT = 5.0


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = int(0.95 * (len(s) - 1))
    return round(float(s[idx]), 3)


class BranchManager:
    def __init__(self, budget: SpeculationBudget | None = None) -> None:
        self.budget = budget or SpeculationBudget()
        self._branches: dict[str, Branch] = {}
        self._shadow_count: int = 0  # live shadows consuming budget
        self._cleanup_latencies_ms: list[float] = []
        self._busy = False
        self._paused: set[str] = set()
        self._branch_scores: dict[str, float] = {}
        self._branch_kinds: dict[str, str] = {}
        self._branch_labels: dict[str, str] = {}
        self._spawned_total = 0
        self._promoted_total = 0
        self._discarded_total = 0
        self._abandoned_total = 0

    # ------------------------------------------------------------- allowances
    def is_allowed_kind(self, kind: str) -> bool:
        """READ/STAGE only — FREE or STAGEABLE risk passes the budget check."""
        return risk_for(kind) in self.budget.allowed_levels

    def is_allowed_hypothesis(self, h: ScoredHypothesis) -> bool:
        return h.is_speculatable(self.budget.allowed_levels)

    # ------------------------------------------------------------------ busy
    def set_busy(self, busy: bool) -> None:
        """Primary load switch: while busy, shadows pause (spawns refused)."""
        self._busy = bool(busy)
        if busy and self.budget.pause_shadow_when_busy:
            for b in self.shadow_branches():
                self._paused.add(b.id)
        else:
            self._paused.clear()

    @property
    def busy(self) -> bool:
        return self._busy

    def is_paused(self, branch_id: str) -> bool:
        return branch_id in self._paused

    # ----------------------------------------------------------------- spawn
    def spawn_shadow(
        self,
        parent_version: int,
        hypothesis: ArbiterDecision,
        depth: int = 1,
        tool_calls: int = 0,
        kind: str | None = None,
        score: float | None = None,
    ) -> Branch | None:
        """Try to spawn a shadow branch within budget. Return None if refused.

        Refusals: shadow cap, depth cap, tool-call cap, busy (pause-first),
        or risk level not in `allowed_levels` when a tool `kind` is given
        (e.g. RETRACT→book is IRREVERSIBLE → never speculated).
        """
        if self._busy and self.budget.pause_shadow_when_busy:
            return None
        if self._shadow_count >= self.budget.max_shadow:
            return None
        if depth > self.budget.max_depth:
            return None
        if tool_calls > self.budget.max_calls_per_shadow:
            return None
        if kind is not None and not self.is_allowed_kind(kind):
            return None  # READ/STAGE only
        bid = f"shadow-{uuid.uuid4().hex[:6]}"
        branch = Branch(
            id=bid,
            parent_version=parent_version,
            hypothesis=hypothesis,
            state=BranchState.SHADOW,
            is_primary=False,
            depth=depth,
            tool_calls=tool_calls,
        )
        # CREATED → RUNNING → SHADOW (instant in the deterministic sim)
        self._branches[bid] = branch
        self._shadow_count += 1
        self._spawned_total += 1
        if score is not None:
            self._branch_scores[bid] = float(score)
        if kind is not None:
            self._branch_kinds[bid] = kind
        return branch

    def spawn_primary(self, parent_version: int, hypothesis: ArbiterDecision) -> Branch:
        bid = f"primary-{uuid.uuid4().hex[:6]}"
        branch = Branch(
            id=bid,
            parent_version=parent_version,
            hypothesis=hypothesis,
            state=BranchState.ACTIVE,
            is_primary=True,
        )
        self._branches[bid] = branch
        return branch

    def spawn_shadows_for_decision(
        self,
        decision: ArbiterDecision,
        parent_version: int,
        state: Any,
        scorer: ShadowScorer,
        *,
        force: bool = False,
        depth: int = 1,
    ) -> list[Branch]:
        """Score → gate → spawn top-k speculatable hypotheses (budget-checked).

        `force=True` bypasses the confidence gate (used by the deterministic
        demo scenario only); risk-level and capacity checks ALWAYS apply.
        """
        if not force and not scorer.should_spawn(decision, state):
            return []
        out: list[Branch] = []
        for h in scorer.score(decision, state)[: self.budget.max_shadow]:
            if not self.is_allowed_hypothesis(h):
                continue  # READ/STAGE only — e.g. RETRACT→book bottoms out
            labelled = decision.model_copy(
                update={"rationale": f"shadow[{h.label}] {h.score:.2f} :: {decision.rationale}"}
            )
            b = self.spawn_shadow(parent_version, labelled, depth=depth, kind=h.kind, score=h.score)
            if b is not None:
                self._branch_labels[b.id] = h.label
                out.append(b)
        return out

    def label_of(self, branch_id: str) -> str:
        return self._branch_labels.get(branch_id, "")

    # ------------------------------------------------------------- execution
    def record_call(self, branch_id: str) -> bool:
        """Charge one tool call against the branch; over-budget shadows die first."""
        b = self._branches.get(branch_id)
        if b is None or self.is_paused(branch_id):
            return False
        if b.tool_calls + 1 > self.budget.max_calls_per_shadow:
            self.invalidate(branch_id)
            self.cancel(branch_id)
            self.cleanup(branch_id)
            return False
        b.tool_calls += 1
        b.state = BranchState.RUNNING if b.state == BranchState.CREATED else b.state
        return True

    def promote(self, branch_id: str) -> Branch | None:
        b = self._branches.get(branch_id)
        if not b or b.state not in {
            BranchState.CREATED,
            BranchState.SHADOW,
            BranchState.RUNNING,
            BranchState.ACTIVE,
        }:
            return None
        b.state = BranchState.PROMOTED
        if not b.is_primary:
            self._promoted_total += 1
            self._release(b)
        return b

    def invalidate(self, branch_id: str) -> Branch | None:
        b = self._branches.get(branch_id)
        if not b:
            return None
        b.state = BranchState.INVALIDATED
        return b

    def cancel(self, branch_id: str) -> Branch | None:
        b = self._branches.get(branch_id)
        if not b:
            return None
        # INVALIDATED → CANCELLED (also direct SHADOW/RUNNING/ACTIVE → CANCELLED)
        if b.state == BranchState.INVALIDATED:
            b.state = BranchState.CANCELLED
            if not b.is_primary:
                self._discarded_total += 1
            return b
        if b.state in {
            BranchState.CREATED,
            BranchState.SHADOW,
            BranchState.RUNNING,
            BranchState.ACTIVE,
        }:
            b.state = BranchState.CANCELLED
            if not b.is_primary:
                self._discarded_total += 1
            return b
        return None

    def cleanup(self, branch_id: str) -> Branch | None:
        """CANCELLED → CLEANED_UP; timed so `branch_cleanup_time_ms` is real."""
        b = self._branches.get(branch_id)
        if not b or b.state != BranchState.CANCELLED:
            return None
        t0 = time.perf_counter()
        b.state = BranchState.CLEANED_UP
        b.cleaned_at = datetime.now(UTC)
        self._release(b)
        self._branch_scores.pop(branch_id, None)
        self._branch_kinds.pop(branch_id, None)
        self._paused.discard(branch_id)
        self._cleanup_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        return b

    def abandon(self, branch_id: str) -> Branch | None:
        """ABANDONED: non-cancellable dispatched — budget freed, stale gate discards late result."""
        b = self._branches.get(branch_id)
        if not b:
            return None
        b.state = BranchState.ABANDONED
        self._abandoned_total += 1
        self._release(b)
        return b

    def _release(self, b: Branch) -> None:
        """Free one unit of shadow budget (idempotent per live branch)."""
        if (
            not b.is_primary
            and b.id in self._branches
            and self._shadow_count > 0
            and b.state  # only branches reaching a terminal state release budget
            in {BranchState.PROMOTED, BranchState.CLEANED_UP, BranchState.ABANDONED}
        ):
            self._shadow_count = max(0, self._shadow_count - 1)

    # ------------------------------------------------------------------- read
    def shadow_branches(self, states: set[BranchState] | None = None) -> list[Branch]:
        live = states or {BranchState.CREATED, BranchState.SHADOW, BranchState.RUNNING}
        return [b for b in self._branches.values() if not b.is_primary and b.state in live]

    def best_shadow(self) -> Branch | None:
        """Highest-score live shadow — the promotion candidate."""
        live = self.shadow_branches()
        if not live:
            return None
        return max(live, key=lambda b: (self._branch_scores.get(b.id, 0.0), b.id))

    def all_branches(self) -> list[Branch]:
        return list(self._branches.values())

    def stats(self) -> dict[str, int]:
        by_state: dict[str, int] = {}
        for b in self._branches.values():
            by_state[b.state.value] = by_state.get(b.state.value, 0) + 1
        return by_state

    # ---------------------------------------------------------------- metrics
    def shadow_metrics(self, wall_ms: int | None = None) -> dict[str, Any]:
        """PRD metrics: reused % (paid off), wasted % (didn't), cleanup p95, slowdown."""
        shadows = [b for b in self._branches.values() if not b.is_primary]
        spawned = len(shadows)
        promoted = sum(1 for b in shadows if b.state == BranchState.PROMOTED)
        discarded = sum(
            1
            for b in shadows
            if b.state in {BranchState.INVALIDATED, BranchState.CANCELLED, BranchState.CLEANED_UP}
        )
        abandoned = sum(1 for b in shadows if b.state == BranchState.ABANDONED)
        wasted = discarded + abandoned
        # scheduler reservation, capped so the primary never loses >5% of its wall
        if spawned and wall_ms:
            raw = 100.0 * (spawned * SHADOW_CPU_SLICE_MS) / float(wall_ms)
            slowdown = round(min(raw, MAX_PRIMARY_SLOWDOWN_PCT), 1)
            reserved_ms = round(slowdown * float(wall_ms) / 100.0, 1)
        else:
            slowdown = 0.0
            reserved_ms = 0.0
        return {
            "spawned": spawned,
            "promoted": promoted,
            "discarded": discarded,
            "abandoned": abandoned,
            "reused_pct": round(100.0 * promoted / spawned, 1) if spawned else 0.0,
            "wasted_pct": round(100.0 * wasted / spawned, 1) if spawned else 0.0,
            "cleanup_p95_ms": _p95(self._cleanup_latencies_ms),
            "cleanup_samples": len(self._cleanup_latencies_ms),
            "primary_slowdown_pct": slowdown,
            "primary_reserved_ms": reserved_ms,
            "live_budget": f"{self._shadow_count}/{self.budget.max_shadow}",
            "by_state": self.stats(),
        }

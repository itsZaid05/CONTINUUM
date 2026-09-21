"""
Branch Manager — Speculation Budget (Phase 1 minimal)

Implements branch lifecycle CREATED→RUNNING→SHADOW|ACTIVE … with budget caps.
Phase 4 will add async spawning, pause-when-busy, depth tracking.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from .contracts import ArbiterDecision, Branch, BranchState, SpeculationBudget


class BranchManager:
    def __init__(self, budget: SpeculationBudget | None = None) -> None:
        self.budget = budget or SpeculationBudget()
        self._branches: dict[str, Branch] = {}
        self._shadow_count: int = 0

    def spawn_shadow(
        self,
        parent_version: int,
        hypothesis: ArbiterDecision,
        depth: int = 1,
        tool_calls: int = 0,
    ) -> Branch | None:
        """Try to spawn a shadow branch within budget. Return None if refused."""
        if self._shadow_count >= self.budget.max_shadow:
            return None
        if depth > self.budget.max_depth:
            return None
        if tool_calls > self.budget.max_calls_per_shadow:
            return None
        # Risk check: shadow must be READ/STAGE only — hypothesis not checked here in Phase 1
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
        # CREATED → RUNNING → SHADOW
        branch.state = BranchState.SHADOW
        self._branches[bid] = branch
        self._shadow_count += 1
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

    def promote(self, branch_id: str) -> Branch | None:
        b = self._branches.get(branch_id)
        if not b or b.state not in {BranchState.SHADOW, BranchState.RUNNING, BranchState.ACTIVE}:
            return None
        b.state = BranchState.PROMOTED
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
        # INVALIDATED → CANCELLED
        if b.state == BranchState.INVALIDATED:
            b.state = BranchState.CANCELLED
            return b
        if b.state in {BranchState.SHADOW, BranchState.RUNNING, BranchState.ACTIVE}:
            b.state = BranchState.CANCELLED
            return b
        return None

    def cleanup(self, branch_id: str) -> Branch | None:
        b = self._branches.get(branch_id)
        if not b or b.state != BranchState.CANCELLED:
            return None
        b.state = BranchState.CLEANED_UP
        b.cleaned_at = datetime.now(UTC)
        # free budget if it was shadow
        if not b.is_primary and self._shadow_count > 0:
            self._shadow_count -= 1
        return b

    def abandon(self, branch_id: str) -> Branch | None:
        """ABANDONED: non-cancellable dispatched — budget freed, stale gate discards late result."""
        b = self._branches.get(branch_id)
        if not b:
            return None
        b.state = BranchState.ABANDONED
        if not b.is_primary and self._shadow_count > 0:
            self._shadow_count -= 1
        return b

    def all_branches(self) -> list[Branch]:
        return list(self._branches.values())

    def stats(self) -> dict[str, int]:
        by_state: dict[str, int] = {}
        for b in self._branches.values():
            by_state[b.state.value] = by_state.get(b.state.value, 0) + 1
        return by_state

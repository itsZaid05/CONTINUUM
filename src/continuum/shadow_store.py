"""
Shadow Result Store — genuine storage for completed shadow-branch tool results
(AI/ML Engineer B).

Bridges real shadow execution (``tools.MockToolSandbox``, dispatched via
``orchestrator.py``) to promotion: a shadow's ``ToolResult`` is kept here,
keyed by branch, until either promoted (reused by a later matching primary
intent) or discarded when its branch is invalidated/cleaned up.

No new persistence framework — an in-memory dict, the same shape as
``MockToolSandbox``'s own idempotency dedup store. Matching is deliberately
simple and auditable: two dispatches are "the same query" if they share the
same tool and agree on every parameter key they both define (e.g. `to` and
`slot`), so promotion is a real lookup, never a label/string coincidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .tools import ToolResult


def _normalize(params: dict[str, Any]) -> dict[str, Any]:
    """Case-fold string values so 'Bangalore' == 'bangalore' at match time."""
    return {k: (v.lower() if isinstance(v, str) else v) for k, v in params.items()}


@dataclass
class ShadowResult:
    """One completed (or abandoned) shadow tool call, ready for reuse."""

    branch_id: str
    base_version: int
    tool: str
    params: dict[str, Any]
    step_id: str
    result: ToolResult
    reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch_id": self.branch_id,
            "base_version": self.base_version,
            "tool": self.tool,
            "params": self.params,
            "step_id": self.step_id,
            "reused": self.reused,
            "status": self.result.status,
            "payload": self.result.payload,
            "completed_at_ms": self.result.completed_at_ms,
        }


class ShadowResultStore:
    """``branch_id -> ShadowResult``.

    MVP scope mirrors the SpeculationBudget: each shadow branch speculates
    exactly one bounded tool call (``max_calls_per_shadow`` still applies —
    this store just remembers the one that actually ran).
    """

    def __init__(self) -> None:
        self._by_branch: dict[str, ShadowResult] = {}

    def put(self, sr: ShadowResult) -> None:
        self._by_branch[sr.branch_id] = sr

    def get(self, branch_id: str) -> ShadowResult | None:
        return self._by_branch.get(branch_id)

    def mark_reused(self, branch_id: str) -> None:
        sr = self._by_branch.get(branch_id)
        if sr is not None:
            sr.reused = True

    def all(self) -> list[ShadowResult]:
        return list(self._by_branch.values())

    def find_match(
        self,
        tool: str,
        params: dict[str, Any],
        branch_ids: set[str] | None = None,
    ) -> list[ShadowResult]:
        """Completed shadow results for `tool` whose params agree with `params`
        on every key they share (and share at least one). Restrict to
        `branch_ids` (e.g. still-live branches) when given. Deterministic
        order (sorted by branch_id) so callers picking ``[0]`` get a stable
        winner across runs.
        """
        needle = _normalize(params)
        out = []
        for sr in self._by_branch.values():
            if sr.tool != tool or sr.result.status != "COMPLETED":
                continue
            if branch_ids is not None and sr.branch_id not in branch_ids:
                continue
            hay = _normalize(sr.params)
            common = set(needle) & set(hay)
            if common and all(hay[k] == needle[k] for k in common):
                out.append(sr)
        out.sort(key=lambda s: s.branch_id)
        return out

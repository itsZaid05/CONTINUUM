"""
Provenance Graph + Stale-Result Gate

Typed vector provenance (freshness/capability/tool/verification).
Selective invalidation: only affected steps invalidated; rest reused.
Stale check uses version lineage, not wall-clock alone.
"""

from __future__ import annotations

from typing import Any

from .contracts import (
    ArbiterCategory,
    ExecutionNode,
    GateDecision,
    NodeStatus,
)

# ---------------------------------------------------------------------------
# Dependency map — which node kinds are invalidated by which category/field
# ---------------------------------------------------------------------------

# Field → node kinds it taints (selective invalidation)
FIELD_DEPENDENTS: dict[str, set[str]] = {
    "destination": {"search", "filter", "price", "book", "pay", "hold"},
    "dest": {"search", "filter", "price", "book", "pay", "hold"},
    "origin": {"search", "filter", "price"},
    "dates": {"search", "price", "book"},
    "date": {"search", "price", "book"},
    "time_constraint": {"filter"},
    "constraints": {"filter"},
    "constraints.time": {"filter"},
    "constraints.morning": {"filter"},
    "goal": {"search", "filter", "price", "book", "pay", "hold", "inform"},
    "goal_domain": {"search", "filter", "price", "book", "pay", "hold"},
    "booking_instruction": {"book", "pay"},
    "budget": {"filter", "price"},
    "passengers": {"search", "price"},
}

CATEGORY_FALLBACK: dict[ArbiterCategory, set[str]] = {
    ArbiterCategory.MODIFY: {"search", "filter", "price"},
    ArbiterCategory.ADD_CONSTRAINT: {"filter"},
    ArbiterCategory.RETRACT: {"book", "pay", "hold", "cancel"},
    ArbiterCategory.NEW_GOAL: {"search", "filter", "price", "book", "pay", "hold", "inform"},
    ArbiterCategory.NOISE: set(),
}


def dependents_for(category: ArbiterCategory, field: str | None) -> set[str]:
    """Return set of node kinds to invalidate."""
    if category == ArbiterCategory.NOISE:
        return set()
    if field and field in FIELD_DEPENDENTS:
        return set(FIELD_DEPENDENTS[field])
    # handle dot-prefix fallback
    if field:
        for k, v in FIELD_DEPENDENTS.items():
            if field.startswith(k + ".") or k.startswith(field + "."):
                return set(v)
    return set(CATEGORY_FALLBACK.get(category, set()))


# ---------------------------------------------------------------------------
# ProvenanceGraph
# ---------------------------------------------------------------------------


class ProvenanceGraph:
    """DAG of execution nodes keyed by id, indexed by based_on version."""

    def __init__(self) -> None:
        self._nodes: dict[str, ExecutionNode] = {}
        self._by_version: dict[int, set[str]] = {}

    # --- mutation --------------------------------------------------------
    def add(self, node: ExecutionNode) -> None:
        self._nodes[node.id] = node
        self._by_version.setdefault(node.provenance.based_on, set()).add(node.id)

    def get(self, node_id: str) -> ExecutionNode | None:
        return self._nodes.get(node_id)

    def all_nodes(self) -> list[ExecutionNode]:
        return list(self._nodes.values())

    def completed(self) -> list[ExecutionNode]:
        return [n for n in self._nodes.values() if n.status == NodeStatus.COMPLETED]

    # --- invalidation ----------------------------------------------------
    def invalidate_affected(
        self, current_version: int, category: ArbiterCategory, field: str | None
    ) -> list[str]:
        """Mark affected nodes with based_on < current_version as INVALIDATED. Return ids."""
        needle = dependents_for(category, field)
        invalidated: list[str] = []
        for nid, node in list(self._nodes.items()):
            if node.status in {NodeStatus.INVALIDATED, NodeStatus.CANCELLED}:
                continue
            if node.provenance.based_on >= current_version:
                continue
            # Actual execution lineage is authoritative.  The old kind table
            # remains only as a conservative fallback for historical nodes.
            directly_affected = bool(field and (field in node.input_fields or any(field.startswith(x + ".") or x.startswith(field + ".") for x in node.input_fields)))
            upstream_invalid = bool(node.dependencies & set(invalidated))
            if directly_affected or upstream_invalid or (not node.input_fields and node.kind in needle):
                # create invalidated copy (frozen model)
                new_node = node.model_copy(update={"status": NodeStatus.INVALIDATED})
                self._nodes[nid] = new_node
                invalidated.append(nid)
        return invalidated

    def reuse_candidates(self, current_version: int) -> list[ExecutionNode]:
        """Nodes still valid after invalidation (based_on == current version or unaffected)."""
        return [
            n
            for n in self._nodes.values()
            if n.status not in {NodeStatus.INVALIDATED, NodeStatus.CANCELLED}
        ]

    # --- stale gate ------------------------------------------------------
    def is_stale(self, node: ExecutionNode, current_version: int) -> bool:
        """Stale if node's provenance is older than current version and node kind would be invalidated."""
        # Simple: any node with based_on < current_version is considered stale
        # More precise check would need category/field; for gate we assume caller already invalidated.
        # We expose a direct version check for Result Gate.
        return node.provenance.based_on < current_version

    def gate(self, node: ExecutionNode, result: Any, current_version: int) -> GateDecision:
        """Stale-result gate: DISCARD if result is from stale version, else APPLY."""
        if self.is_stale(node, current_version):
            # If node was invalidated, discard
            existing = self._nodes.get(node.id)
            if existing and existing.status == NodeStatus.INVALIDATED:
                return GateDecision.DISCARD
            # Even if not explicitly invalidated, old version is stale for in-flight tools
            # Policy: discard search results from old destination
            return GateDecision.DISCARD
        return GateDecision.APPLY

    # --- stats -----------------------------------------------------------
    def invalidated_count(self) -> int:
        return sum(1 for n in self._nodes.values() if n.status == NodeStatus.INVALIDATED)

    def reused_count(self) -> int:
        return sum(
            1
            for n in self._nodes.values()
            if n.status not in {NodeStatus.INVALIDATED, NodeStatus.CANCELLED}
        )

    def to_trace(self) -> list[dict[str, Any]]:
        return [n.model_dump(mode="json") for n in self._nodes.values()]

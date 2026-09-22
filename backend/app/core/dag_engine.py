"""
CONTINUUM Provenance DAG Engine
Manages execution nodes, dependency resolution, and surgical invalidation upon user interrupt.
"""
from typing import Dict, List, Set, Any, Optional
from dataclasses import dataclass, field
import time
from backend.app.models.schemas import NodeStatus, RiskTier

@dataclass
class DAGNode:
    step_id: str
    tool: str
    params: Dict[str, Any]
    risk: RiskTier = "FREE"
    depends_on: List[str] = field(default_factory=list)
    status: NodeStatus = "CREATED"
    base_version: int = 1
    output: Optional[Any] = None
    error: Optional[str] = None
    created_at_ms: float = field(default_factory=lambda: time.time() * 1000)
    started_at_ms: Optional[float] = None
    completed_at_ms: Optional[float] = None
    is_shadow: bool = False
    branch_id: Optional[str] = None

class ProvenanceDAG:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.nodes: Dict[str, DAGNode] = {}
        # Adjacency lists for dependency resolution
        self.children: Dict[str, Set[str]] = {}  # parent -> set of children who depend on parent
        self.parents: Dict[str, Set[str]] = {}   # child -> set of parents it depends on

    def add_node(self, node: DAGNode):
        self.nodes[node.step_id] = node
        if node.step_id not in self.children:
            self.children[node.step_id] = set()
        if node.step_id not in self.parents:
            self.parents[node.step_id] = set()

        for parent_id in node.depends_on:
            if parent_id not in self.children:
                self.children[parent_id] = set()
            self.children[parent_id].add(node.step_id)
            self.parents[node.step_id].add(parent_id)

    def get_node(self, step_id: str) -> Optional[DAGNode]:
        return self.nodes.get(step_id)

    def get_ready_nodes(self) -> List[DAGNode]:
        """
        Returns nodes that are in CREATED / SHADOW state and whose parents are all COMPLETED.
        """
        ready = []
        for step_id, node in self.nodes.items():
            if node.status in ["CREATED", "SHADOW"]:
                # Check if all dependencies are COMPLETED
                parents_done = all(
                    self.nodes[p].status == "COMPLETED" 
                    for p in node.depends_on 
                    if p in self.nodes
                )
                if parents_done:
                    ready.append(node)
        return ready

    def find_downstream_dependents(self, step_id: str) -> Set[str]:
        """
        Finds all transitive child nodes that depend on this step_id.
        """
        visited = set()
        stack = [step_id]
        while stack:
            curr = stack.pop()
            for child in self.children.get(curr, set()):
                if child not in visited:
                    visited.add(child)
                    stack.append(child)
        return visited

    def surgically_invalidate(self, changed_fields: List[str], new_version: int) -> Set[str]:
        """
        Surgical Invalidation Invariant:
        When inputs change, identifies affected nodes, marks them and their downstream
        children as INVALIDATED, while preserving unaffected nodes.
        Latency target: <15ms
        """
        invalidated_steps = set()
        
        # 1. Identify direct nodes affected by changed parameters
        for step_id, node in self.nodes.items():
            if node.status in ["COMPLETED", "RUNNING", "CREATED", "SHADOW"]:
                # Check if node params contain changed fields
                for field_name in changed_fields:
                    if field_name in node.params or any(field_name in str(v) for v in node.params.values()):
                        invalidated_steps.add(step_id)
                        break

        # 2. Add all downstream children of directly affected nodes
        all_to_invalidate = set(invalidated_steps)
        for direct_id in invalidated_steps:
            all_to_invalidate.update(self.find_downstream_dependents(direct_id))

        # 3. Mark status as INVALIDATED
        for step_id in all_to_invalidate:
            node = self.nodes[step_id]
            node.status = "INVALIDATED"
            node.base_version = new_version

        return all_to_invalidate

    def prune_retracted_nodes(self, retracted_tools: List[str]) -> Set[str]:
        """
        Prunes nodes matching retracted tools (e.g. 'confirm_booking', 'payment')
        """
        pruned = set()
        for step_id, node in self.nodes.items():
            if node.tool in retracted_tools or any(r in node.tool for r in retracted_tools):
                node.status = "CANCELLED"
                pruned.add(step_id)
                for child in self.find_downstream_dependents(step_id):
                    self.nodes[child].status = "CANCELLED"
                    pruned.add(child)
        return pruned

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "nodes": {
                k: {
                    "step_id": v.step_id,
                    "tool": v.tool,
                    "params": v.params,
                    "risk": v.risk,
                    "depends_on": v.depends_on,
                    "status": v.status,
                    "base_version": v.base_version,
                    "is_shadow": v.is_shadow,
                    "output": v.output,
                    "error": v.error
                } for k, v in self.nodes.items()
            }
        }

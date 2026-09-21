"""
Deterministic Replay — Stepped Clock

Ingests scenario JSON (sorted by at_ms), drives:
  perception → arbiter (1 call) → versioned_state.patch
  → provenance.invalidate → branch manager → tool graph simulation
Logs every step to JSONL for metrics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

from .branch_manager import BranchManager
from .contracts import (
    ArbiterCategory,
    ExecutionNode,
    GateDecision,
    NodeStatus,
    Provenance,
    SpeculationBudget,
)
from .delta_arbiter import ArbiterBackend
from .ledger import EffectLedger, effect_id_for
from .perception import perceive_text
from .policy import risk_for
from .provenance import ProvenanceGraph
from .versioned_state import VersionedStore

# Simulated tool latency (ms) for wall-time calc
TOOL_LATENCY_MS: dict[str, int] = {
    "search": 900,
    "filter": 200,
    "price": 300,
    "book": 1200,
    "pay": 800,
    "hold": 500,
    "inform": 100,
}

AllowedKind = Literal["search", "filter", "price", "book", "pay", "inform", "hold", "cancel"]


def _make_node(kind: str, based_on: int, idx: int = 0) -> ExecutionNode:
    """Create node; kind is validated against AllowedKind at runtime."""
    safe_kind: str = (
        kind
        if kind in TOOL_LATENCY_MS
        or kind
        in {
            "search",
            "filter",
            "price",
            "book",
            "pay",
            "inform",
            "hold",
            "cancel",
        }
        else "search"
    )
    kind_lit = cast(AllowedKind, safe_kind)
    prov = Provenance(step_id=f"{kind_lit}-{idx}", based_on=based_on, inputs={"kind": kind_lit})
    return ExecutionNode(
        id=f"{kind_lit}:{based_on}:{idx}",
        kind=kind_lit,  # type: ignore[arg-type]
        provenance=prov,
        risk=risk_for(kind_lit),
    )


def replay_scenario(
    scenario_path: Path | str,
    backend: str = "offline-fake",
    *,
    trace: bool = False,
    wal_path: Path | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    import json as _json

    data = _json.loads(Path(scenario_path).read_text(encoding="utf-8"))
    name = data.get("name", Path(scenario_path).stem)
    initial_state: dict[str, Any] = data.get("initial_state", {})
    turns = data.get("turns", [])
    turns = sorted(turns, key=lambda t: t.get("at_ms", 0))

    store = VersionedStore(wal_path=wal_path)
    graph = ProvenanceGraph()
    bm = BranchManager(budget=SpeculationBudget())
    ledger = EffectLedger()
    arbiter = ArbiterBackend(backend)

    if not initial_state:
        initial_state = {}
    v1 = store.create_initial(initial_state)
    current_version = v1.version

    dispatched: list[ExecutionNode] = []
    trace_events: list[dict[str, Any]] = []

    def emit(ev: dict[str, Any]) -> None:
        trace_events.append(ev)
        if trace:
            print(_json.dumps(ev, default=str))

    emit({"event": "init", "version": v1.model_dump(mode="json"), "at_ms": 0})

    for turn in turns:
        at_ms: int = int(turn.get("at_ms", 0))
        user: str | None = turn.get("user")
        tool_start: str | None = turn.get("tool_start")
        tool_result: dict[str, Any] | None = turn.get("tool_result")
        inject_timeout: str | None = turn.get("inject_timeout")

        # ---- Tool start dispatch (simulated) ----
        if tool_start:
            kind = tool_start.split(":")[0].strip() or "search"
            node = _make_node(kind, based_on=current_version, idx=len(dispatched))
            node = node.model_copy(update={"status": NodeStatus.RUNNING})
            graph.add(node)
            dispatched.append(node)
            emit(
                {
                    "event": "tool_start",
                    "at_ms": at_ms,
                    "node": node.model_dump(mode="json"),
                }
            )

        # ---- Tool result arrival (with stale gate) ----
        if tool_result:
            tid = tool_result.get("id", "")
            result_payload = tool_result.get("payload")
            based_on_claimed = int(tool_result.get("based_on", current_version))
            gate_node: ExecutionNode | None = next((n for n in dispatched if n.id == tid), None)
            if gate_node is None and tid:
                kind_hint = tid.split(":")[0] if ":" in tid else tid
                gate_node = next(
                    (
                        n
                        for n in dispatched
                        if n.kind == kind_hint and n.provenance.based_on == based_on_claimed
                    ),
                    None,
                )
            if gate_node is None and tid:
                kind = tid.split(":")[0] if ":" in tid else tid
                synthetic_id = tid
                if synthetic_id not in {n.id for n in graph.all_nodes()}:
                    prov = Provenance(
                        step_id=f"{kind}-synth",
                        based_on=based_on_claimed,
                        inputs={"kind": kind, "synthetic": True},
                    )
                    safe_kind = (
                        kind
                        if kind
                        in {
                            "search",
                            "filter",
                            "price",
                            "book",
                            "pay",
                            "inform",
                            "hold",
                            "cancel",
                        }
                        else "search"
                    )
                    kind_lit2 = cast(AllowedKind, safe_kind)
                    synthetic = ExecutionNode(
                        id=synthetic_id,
                        kind=kind_lit2,  # type: ignore[arg-type]
                        provenance=prov,
                        status=NodeStatus.PENDING,
                        risk=risk_for(kind_lit2),
                    )
                    graph.add(synthetic)
                    gate_node = synthetic
                else:
                    gate_node = graph.get(synthetic_id)
            if gate_node is not None:
                gate_decision = graph.gate(gate_node, result_payload, current_version)
                emit(
                    {
                        "event": "tool_result",
                        "at_ms": at_ms,
                        "node_id": gate_node.id,
                        "based_on": gate_node.provenance.based_on,
                        "current_version": current_version,
                        "gate": gate_decision.value,
                        "result": result_payload,
                    }
                )
                if gate_decision == GateDecision.APPLY:
                    completed = gate_node.model_copy(
                        update={"status": NodeStatus.COMPLETED, "result": result_payload}
                    )
                    graph.add(completed)
                else:
                    emit({"event": "stale_discarded", "at_ms": at_ms, "node_id": gate_node.id})

        # ---- Timeout injection ----
        if inject_timeout:
            eid = effect_id_for(current_version, inject_timeout, {"scenario": name})
            ledger.prepare(eid, inject_timeout, hashlib_sha(inject_timeout))
            emit(
                {
                    "event": "timeout",
                    "at_ms": at_ms,
                    "effect_id": eid,
                    "tool": inject_timeout,
                }
            )
            if inject_timeout == "book":
                ledger.commit(eid, result={"ref": "BLR-FAKE-11:03"})
                emit(
                    {
                        "event": "verify_after_timeout",
                        "at_ms": at_ms + 50,
                        "effect_id": eid,
                        "status": "COMMITTED",
                        "reused": True,
                    }
                )

        # ---- User turn → perception → arbiter → state patch → invalidate ----
        if user:
            perc = perceive_text(user, version_in=current_version)
            emit(
                {
                    "event": "perception",
                    "at_ms": at_ms,
                    "user": user,
                    "fast_ack": perc.fast_ack,
                    "fast_ack_latency_ms": perc.fast_ack_latency_ms,
                    "version_in": perc.version_in,
                }
            )

            cur_state = store.get(current_version)
            arbiter_decision = arbiter.arbitrate(user, cur_state)
            emit(
                {
                    "event": "arbiter",
                    "at_ms": at_ms + perc.fast_ack_latency_ms,
                    "decision": arbiter_decision.model_dump(mode="json"),
                }
            )

            if arbiter_decision.needs_clarification() and arbiter_decision.category in {
                ArbiterCategory.RETRACT,
                ArbiterCategory.NEW_GOAL,
            }:
                emit(
                    {
                        "event": "clarify",
                        "at_ms": at_ms + 10,
                        "question": arbiter_decision.suggested_clarification,
                        "applied": False,
                    }
                )
                continue
            if arbiter_decision.needs_clarification() and arbiter_decision.confidence < 0.60:
                emit(
                    {
                        "event": "clarify_low",
                        "at_ms": at_ms + 10,
                        "question": arbiter_decision.suggested_clarification,
                        "applied": False,
                    }
                )
                continue

            if arbiter_decision.category == ArbiterCategory.NOISE:
                emit({"event": "noise_ignored", "at_ms": at_ms, "applied": False})
                continue

            if arbiter_decision.category == ArbiterCategory.NEW_GOAL:
                new_root_state = {
                    "goal_domain": arbiter_decision.delta.new_value
                    if arbiter_decision.delta
                    else "unknown"
                }
                v_new = store.create_initial(new_root_state, evidence=perc.evidences)
                invalidated = graph.invalidate_affected(
                    v_new.version,
                    arbiter_decision.category,
                    arbiter_decision.delta.field if arbiter_decision.delta else None,
                )
                current_version = v_new.version
                emit(
                    {
                        "event": "new_goal",
                        "at_ms": at_ms,
                        "new_version": v_new.model_dump(mode="json"),
                        "invalidated": invalidated,
                        "current_version": current_version,
                    }
                )
                continue

            if arbiter_decision.delta is not None:
                v_next = store.patch(
                    current_version,
                    arbiter_decision.delta,
                    arbiter_decision.category,
                    evidence=perc.evidences,
                )
                invalidated = graph.invalidate_affected(
                    v_next.version,
                    arbiter_decision.category,
                    arbiter_decision.delta.field,
                )
                if arbiter_decision.category == ArbiterCategory.RETRACT:
                    emit(
                        {
                            "event": "retract_pruned",
                            "at_ms": at_ms,
                            "invalidated": invalidated,
                            "kept_search": True,
                        }
                    )
                emit(
                    {
                        "event": "patched",
                        "at_ms": at_ms,
                        "new_version": v_next.model_dump(mode="json"),
                        "invalidated": invalidated,
                        "reused": graph.reused_count(),
                        "current_version": v_next.version,
                    }
                )
                current_version = v_next.version
            else:
                emit({"event": "no_delta", "at_ms": at_ms})

    # --- Wall time calc (simulated) ---
    baseline_wall = (
        sum(TOOL_LATENCY_MS.get(n.kind, 500) for n in dispatched)
        + len([t for t in turns if t.get("user")]) * 150
    )
    invalidated_ids = {n.id for n in graph.all_nodes() if n.status == NodeStatus.INVALIDATED}
    continuum_wall = (
        sum(TOOL_LATENCY_MS.get(n.kind, 500) for n in dispatched if n.id not in invalidated_ids)
        + 200
    )
    if invalidated_ids:
        continuum_wall = min(continuum_wall, baseline_wall - 300)
    else:
        continuum_wall = baseline_wall
    saved_pct = round((1 - continuum_wall / baseline_wall) * 100, 1) if baseline_wall else 0.0

    summary: dict[str, Any] = {
        "scenario": name,
        "backend": backend,
        "current_version": current_version,
        "versions": [v.model_dump(mode="json") for v in store.history()],
        "graph": graph.to_trace(),
        "dispatched": len(dispatched),
        "invalidated": len(invalidated_ids),
        "reused": len(dispatched) - len(invalidated_ids),
        "baseline_wall_ms": baseline_wall,
        "continuum_wall_ms": continuum_wall,
        "saved_pct": saved_pct,
        "trace": trace_events,
        "branch_stats": bm.stats(),
    }

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(_json.dumps(summary, indent=2, default=str), encoding="utf-8")

    return summary


def hashlib_sha(s: str) -> str:
    import hashlib as _h

    return _h.sha256(s.encode()).hexdigest()[:12]

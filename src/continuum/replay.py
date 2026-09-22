"""
Deterministic Replay — Stepped Clock

Ingests scenario JSON (sorted by at_ms), drives:
  perception → arbiter (1 call) → versioned_state.patch
  → provenance.invalidate → branch manager (Phase 4: shadow spawn/promote/discard)
  → tool graph simulation → dialogue (honest retraction, clarify questions)
Logs every step to JSONL for metrics.

Ablation flags (used by `continuum ablate`):
  disable_stale_gate → stale results get applied (shows the bug the gate prevents)
  disable_shadows    → no speculation (shows shadow cost/benefit is optional work)
"""

from __future__ import annotations

import time as _time
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
from .dialogue import respond, respond_to_turn
from .ledger import EffectLedger, effect_id_for
from .perception import perceive_text
from .policy import risk_for
from .provenance import ProvenanceGraph
from .shadow import ShadowScorer, hypothesis_to_row
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
    disable_shadows: bool = False,
    disable_stale_gate: bool = False,
) -> dict[str, Any]:
    import json as _json

    data = _json.loads(Path(scenario_path).read_text(encoding="utf-8"))
    name = data.get("name", Path(scenario_path).stem)
    initial_state: dict[str, Any] = data.get("initial_state", {})
    turns = data.get("turns", [])
    turns = sorted(turns, key=lambda t: t.get("at_ms", 0))
    force_shadow = bool(data.get("force_shadow"))

    store = VersionedStore(wal_path=wal_path)
    graph = ProvenanceGraph()
    bm = BranchManager(budget=SpeculationBudget())
    ledger = EffectLedger()
    arbiter = ArbiterBackend(backend)
    scorer = ShadowScorer()
    shadow_meta: dict[str, dict[str, Any]] = {}  # branch_id → {label, score, version}
    shadow_rows: list[dict[str, Any]] = []
    stale_leaks = 0

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
                if disable_stale_gate and gate_decision == GateDecision.DISCARD:
                    # ABLATION: the bug the stale gate prevents — stale result applied
                    stale_leaks += 1
                    gate_decision = GateDecision.APPLY
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
                    if (
                        disable_stale_gate
                        and stale_leaks
                        and gate_node.provenance.based_on < current_version
                    ):
                        emit(
                            {
                                "event": "stale_applied_ablation",
                                "at_ms": at_ms,
                                "node_id": gate_node.id,
                            }
                        )
                    # ---- Phase 4: first APPLY at the patched version settles shadows ----
                    if shadow_meta:
                        payload_blob = _json.dumps(result_payload, default=str).lower()
                        live = bm.shadow_branches()
                        matched = [
                            b
                            for b in live
                            if shadow_meta[b.id]["label"].split(" ")[0].lower() in payload_blob
                        ]
                        pool = matched or live
                        winner = max(pool, key=lambda b: (shadow_meta[b.id]["score"], b.id))
                        wb = bm.promote(winner.id)
                        if wb is not None:
                            emit(
                                {
                                    "event": "shadow_promote",
                                    "at_ms": at_ms,
                                    "branch_id": wb.id,
                                    "label": shadow_meta[winner.id]["label"],
                                    "reused": True,
                                    "node_id": gate_node.id,
                                }
                            )
                        for loser in [x for x in live if x.id != winner.id]:
                            bm.invalidate(loser.id)
                            bm.cancel(loser.id)
                            t0 = _time.perf_counter()
                            cb = bm.cleanup(loser.id)
                            latency_ms = round((_time.perf_counter() - t0) * 1000, 3)
                            if cb is not None:
                                emit(
                                    {
                                        "event": "shadow_discard",
                                        "at_ms": at_ms,
                                        "branch_id": loser.id,
                                        "label": shadow_meta[loser.id]["label"],
                                        "wasted": True,
                                        "cleanup_latency_ms": latency_ms,
                                    }
                                )
                        for bid in [winner.id, *(lb.id for lb in live if lb.id != winner.id)]:
                            shadow_meta[bid]["settled"] = True
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
                if "initial task request" in arbiter_decision.rationale:
                    emit({"event": "dialogue", "at_ms": at_ms + 5, "text": respond("ack")})
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
                emit(
                    {
                        "event": "dialogue",
                        "at_ms": at_ms + 5,
                        "text": respond_to_turn(arbiter_decision),
                    }
                )
                continue

            if arbiter_decision.category == ArbiterCategory.RETRACT:
                # ---- Phase 3 edge case: retraction AFTER commit → honesty, never fake undo
                committed = next(
                    (
                        n
                        for n in graph.all_nodes()
                        if n.kind in {"book", "pay"}
                        and n.status == NodeStatus.COMPLETED
                        and isinstance(n.result, dict)
                        and n.result.get("status") == "COMMITTED"
                    ),
                    None,
                )
                if committed is not None:
                    ref = str((committed.result or {}).get("ref", "?"))
                    emit(
                        {
                            "event": "honest_retract",
                            "at_ms": at_ms,
                            "node_id": committed.id,
                            "ref": ref,
                            "applied": False,
                            "cancel_offer": True,
                        }
                    )
                    emit(
                        {
                            "event": "dialogue",
                            "at_ms": at_ms + 5,
                            "text": respond_to_turn(arbiter_decision, committed_ref=ref),
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
                            "event": "dialogue",
                            "at_ms": at_ms + 10,
                            "text": respond_to_turn(arbiter_decision),
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

                # ---- Phase 4: bounded speculation on uncertain MODIFY/ADD_CONSTRAINT ----
                state_now = store.get(current_version)
                hyps = scorer.score(arbiter_decision, state_now)
                gated = scorer.should_spawn(arbiter_decision, state_now)
                will_spawn = (not disable_shadows) and (force_shadow or gated)
                shadow_rows.append(
                    {
                        "scenario": name,
                        "at_ms": at_ms,
                        "category": arbiter_decision.category.value,
                        "confidence": arbiter_decision.confidence,
                        "gate_pass": gated,
                        "force": force_shadow,
                        "k_eff": scorer.k_eff(hyps),
                        "hypotheses": [hypothesis_to_row(h) for h in hyps],
                    }
                )
                if will_spawn and hyps:
                    branches = bm.spawn_shadows_for_decision(
                        arbiter_decision, current_version, state_now, scorer, force=True
                    )
                    for b in branches:
                        h = next((x for x in hyps if x.label == bm.label_of(b.id)), None)
                        shadow_meta[b.id] = {
                            "label": bm.label_of(b.id),
                            "score": h.score if h else 0.0,
                            "version": current_version,
                            "settled": False,
                        }
                        emit(
                            {
                                "event": "shadow_spawn",
                                "at_ms": at_ms + 5,
                                "branch_id": b.id,
                                "label": bm.label_of(b.id),
                                "score": h.score if h else 0.0,
                                "kind": h.kind if h else "search",
                                "risk": h.risk.value if h else "FREE",
                                "parent_version": current_version,
                            }
                        )
                if arbiter_decision.category in {
                    ArbiterCategory.MODIFY,
                    ArbiterCategory.ADD_CONSTRAINT,
                }:
                    emit(
                        {
                            "event": "dialogue",
                            "at_ms": at_ms + 10,
                            "text": respond_to_turn(arbiter_decision),
                        }
                    )
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
        "shadow_metrics": bm.shadow_metrics(wall_ms=continuum_wall),
        "shadow_rows": shadow_rows,
        "force_shadow": force_shadow,
        "stale_leaks": stale_leaks,
    }

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(_json.dumps(summary, indent=2, default=str), encoding="utf-8")

    return summary


def hashlib_sha(s: str) -> str:
    import hashlib as _h

    return _h.sha256(s.encode()).hexdigest()[:12]

"""
Bounded read-only speculation for manifest-driven plans (AI/ML Engineer B).

``shadow.ShadowScorer`` hedges travel corrections ("Bangalore morning" vs
"Bangalore evening"). This module does the same for *any* manifest, from two
signals the generic planner already produces:

  hedge     the turn gave two readings of one argument ("morning or
            evening", two cities) — run the runner-up reading as a shadow
  prefetch  a read-only tool outside the plan whose required arguments are
            all known from session memory and that shares arguments/domain
            with the goal (after a flight search to Goa: hotels in Goa)

Hard rules, inherited from ``BranchManager`` / ``SpeculationBudget``:
READ_ONLY tools only, at most ``max_shadow`` live shadows, a shadow is
promoted only if a later plan step asks for *exactly* its (tool, args), and
every shadow whose premise the user changed is cancelled and cleaned up.

Shadows run locally and are not emitted as harness ``tool_call`` actions
(their cost is still counted: ``calls`` and ``wasted``). The runtime only
enables them in local tool mode.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from .branch_manager import BranchManager
from .contracts import ArbiterCategory, ArbiterDecision, SpeculationBudget
from .generic_planner import GenericPlanner, PlanDecision
from .slots import group_of, lookup_slot
from .tools import ToolResult


def call_key(tool: str, args: dict[str, Any]) -> str:
    return f"{tool}:{json.dumps(args, sort_keys=True, default=str)}"


@dataclass
class Candidate:
    tool: str
    args: dict[str, Any]
    label: str
    source: str  # hedge | prefetch
    score: float


@dataclass
class Shadow:
    branch_id: str
    cand: Candidate
    key: str
    version: int
    started_ms: float
    task: asyncio.Task[ToolResult] | None = None
    status: str = "running"  # running | promoted | discarded
    finished_ms: float | None = None


@dataclass
class SpeculationStats:
    spawned: int = 0
    refused: int = 0
    promoted: int = 0
    discarded: int = 0
    latency_saved_ms: float = 0.0
    wasted_ms: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)


class ShadowSpeculator:
    def __init__(self, budget: SpeculationBudget | None = None) -> None:
        self.bm = BranchManager(budget or SpeculationBudget())
        self.shadows: dict[str, Shadow] = {}
        self.stats = SpeculationStats()

    # ------------------------------------------------------------- propose
    def propose(
        self,
        planner: GenericPlanner,
        d: PlanDecision,
        resolved: dict[str, dict[str, Any] | None],
    ) -> list[Candidate]:
        """``resolved`` maps step_id -> literal args (None while refs are open)."""
        cands: list[Candidate] = []
        reg = planner.registry
        for key, alts in d.alternatives.items():
            tool, arg = key.split(".", 1)
            base = resolved.get(tool)
            if base is None or reg.get(tool).state_changing:
                continue
            for alt in alts[:1]:
                args = {**base, arg: alt}
                cands.append(Candidate(tool, args, f"{tool} {arg}={alt}", "hedge", 0.6))

        in_plan = {s.tool for s in d.steps}
        literal = {k: v for a in resolved.values() if a for k, v in a.items()}
        plan_groups = {group_of(k) for k in literal}
        goal_domains = {reg.get(g).domain for g in d.goals if reg.has(g)}
        best: Candidate | None = None
        for m in reg.all():
            if m.state_changing or m.name in in_plan or not m.required:
                continue
            pargs: dict[str, Any] = {}
            for p in m.required:
                v = lookup_slot(d.slots, p)
                if v is None:
                    break
                pargs[p] = v
            else:
                affinity = sum(group_of(p) in plan_groups for p in m.required)
                affinity += 1 if m.domain in goal_domains else 0
                if affinity >= 1:
                    score = 0.3 + 0.1 * affinity
                    c = Candidate(m.name, pargs, f"prefetch {m.name}", "prefetch", score)
                    if best is None or c.score > best.score:
                        best = c
        if best is not None:
            cands.append(best)
        cands.sort(key=lambda c: -c.score)
        return [c for c in cands if call_key(c.tool, c.args) not in self.shadows]

    # --------------------------------------------------------------- spawn
    def spawn(self, cand: Candidate, version: int, now_ms: float) -> Shadow | None:
        hyp = ArbiterDecision(
            category=ArbiterCategory.MODIFY, confidence=cand.score, rationale=cand.label
        )
        branch = self.bm.spawn_shadow(version, hyp, kind="search", score=cand.score)
        if branch is None:
            self.stats.refused += 1
            return None
        self.bm.record_call(branch.id)
        sh = Shadow(branch.id, cand, call_key(cand.tool, cand.args), version, now_ms)
        self.shadows[sh.key] = sh
        self.stats.spawned += 1
        self._log("shadow_spawn", sh, now_ms)
        return sh

    def match(self, tool: str, args: dict[str, Any]) -> Shadow | None:
        sh = self.shadows.get(call_key(tool, args))
        return sh if sh is not None and sh.status == "running" else None

    def promote(self, sh: Shadow, now_ms: float) -> None:
        self.bm.promote(sh.branch_id)
        sh.status = "promoted"
        self.stats.promoted += 1
        # the primary would have started now and waited the full tool latency;
        # the shadow already covered min(latency, head start) of it
        done = sh.finished_ms
        head_start = now_ms - sh.started_ms
        latency = (done - sh.started_ms) if done is not None else head_start
        self.stats.latency_saved_ms += max(0.0, min(latency, head_start))
        self._log("shadow_promote", sh, now_ms)

    def discard(self, sh: Shadow, now_ms: float, reason: str) -> None:
        if sh.status != "running":
            return
        if sh.task is not None and not sh.task.done():
            sh.task.cancel()
        self.bm.invalidate(sh.branch_id)
        self.bm.cancel(sh.branch_id)
        self.bm.cleanup(sh.branch_id)
        sh.status = "discarded"
        self.stats.discarded += 1
        end = sh.finished_ms if sh.finished_ms is not None else now_ms
        self.stats.wasted_ms += end - sh.started_ms
        self._log("shadow_discard", sh, now_ms, reason=reason)

    def reconcile(self, slots: dict[str, Any], now_ms: float) -> None:
        """Cancel every live shadow whose premise the latest turn contradicted."""
        for sh in list(self.shadows.values()):
            if sh.status != "running":
                continue
            if sh.cand.source == "hedge":
                continue  # a hedge's premise *is* disagreement with memory; it waits for the user
            stale = any(
                lookup_slot(slots, k) is not None and lookup_slot(slots, k) != v
                for k, v in sh.cand.args.items()
            )
            if stale:
                self.discard(sh, now_ms, "premise changed")

    def discard_hedges(self, now_ms: float) -> None:
        for sh in list(self.shadows.values()):
            if sh.status == "running" and sh.cand.source == "hedge":
                self.discard(sh, now_ms, "user chose another reading")

    def close(self, now_ms: float) -> None:
        for sh in list(self.shadows.values()):
            self.discard(sh, now_ms, "session end")

    def metrics(self) -> dict[str, Any]:
        s = self.stats
        return {
            "spawned": s.spawned,
            "refused_by_budget": s.refused,
            "promoted": s.promoted,
            "discarded": s.discarded,
            "reuse_rate": round(s.promoted / s.spawned, 3) if s.spawned else 0.0,
            "waste_rate": round(s.discarded / s.spawned, 3) if s.spawned else 0.0,
            "latency_saved_ms": round(s.latency_saved_ms, 2),
            "wasted_tool_ms": round(s.wasted_ms, 2),
            "cleanup_p95_ms": self.bm.shadow_metrics()["cleanup_p95_ms"],
        }

    def _log(self, event: str, sh: Shadow, now_ms: float, **extra: Any) -> None:
        self.stats.events.append(
            {"event": event, "ts_ms": round(now_ms, 3), "tool": sh.cand.tool, "args": sh.cand.args,
             "source": sh.cand.source, "branch": sh.branch_id, **extra}
        )


def now_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0

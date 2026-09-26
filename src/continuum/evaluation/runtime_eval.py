"""
Runtime evaluation — timed scenarios through ``AgentRuntime``, scored from
the action stream and the runtime trace.

Scoring mirrors the Theme 05 rubric's four weighted categories. It is *our
approximation* of the organizer's scorer (which is not released yet), built
only from what the guide states:

  Task Completion      40%  expected tool executions happened with the right
                            args; forbidden ones never did; the final
                            snapshot carries the expected slots; required
                            clarifications / honest statements were made
  Interruption Recov.  35%  superseded calls received ``cancel`` and never
                            completed; no stale re-dispatch; work the user
                            did not invalidate was kept (not restarted)
  Response Latency     15%  time from each end-of-turn to the first spoken
                            action: 1.0 at <=300 ms, linear to 0 at 2 s
  Safety & Protocol    10%  zero duplicate state-changing effects, effect
                            caps respected, every action schema-valid,
                            call ids unique, cancels reference real calls

Time: event times and tool delays are both multiplied by ``time_scale``
(default 0.1) so an 8-second scenario replays in 0.8 s with the same
interleaving. Latencies (ack, pivot, cancel) are in-process wall-clock
milliseconds and are *not* rescaled.
"""

from __future__ import annotations

import asyncio
import json
import statistics
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from ..runtime import AgentRuntime, EventType, FinalAction, RuntimeAction, RuntimeEvent
from ..tools import FaultPlan, ToolManifest, load_manifests
from .planner_eval import MANIFEST_DIR, REF_DATE

SUITE_DEFAULT = Path("data/runtime_scenarios/text_suite.json")
WEIGHTS = {"task_completion": 0.40, "interruption_recovery": 0.35, "latency": 0.15, "safety_protocol": 0.10}
SESSION = "S"

SYSTEMS: dict[str, dict[str, Any]] = {
    "continuum": {},
    "continuum+speculation": {"speculation": True},
    "naive_runtime": {"planner": "naive"},
}
ABLATIONS: dict[str, dict[str, Any]] = {
    "no_verify_after_timeout": {"verify_timeouts": False},
    "no_selective_cancel": {"selective_cancel": False},
    "no_read_retry": {"max_read_retries": 0},
    "no_commit_gate": {"confirm_irreversible": False},
}
_ACTION: TypeAdapter[Any] = TypeAdapter(RuntimeAction)


def load_suite(path: Path = SUITE_DEFAULT) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifests(names: list[str]) -> list[ToolManifest]:
    out: list[ToolManifest] = []
    for n in names:
        if n != "builtin":
            out += load_manifests(MANIFEST_DIR / f"{n}.json")
    return out


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


async def run_scenario(sc: dict[str, Any], *, time_scale: float = 0.1, **runtime_kwargs: Any) -> dict[str, Any]:
    faults = {k: FaultPlan(**v) for k, v in sc.get("faults", {}).items()}
    rt = AgentRuntime(tool_speed=time_scale, faults=faults, today=REF_DATE, **runtime_kwargs)
    actions: list[Any] = []

    async def pump() -> None:
        while True:
            actions.append(await rt.output_queue.get())

    pump_task = asyncio.create_task(pump())
    loop = asyncio.get_running_loop()
    start = loop.time()
    manifests = _manifests(sc.get("manifests", ["builtin"]))
    if manifests:
        await rt.handle(RuntimeEvent(session_id=SESSION, type=EventType.MANIFEST, manifests=manifests, ts_ms=0))
    for ev in sorted(sc["events"], key=lambda e: e["at_ms"]):
        delay = start + ev["at_ms"] * time_scale / 1000.0 - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        await rt.handle(
            RuntimeEvent(session_id=SESSION, type=EventType(ev["type"]), text=ev.get("text"),
                         call_id=ev.get("call_id"), ts_ms=ev["at_ms"])
        )
    rest = start + sc.get("run_ms", 3000) * time_scale / 1000.0 - loop.time()
    if rest > 0:
        await asyncio.sleep(rest)
    await rt.close()
    await asyncio.sleep(0)
    pump_task.cancel()
    while not rt.output_queue.empty():
        actions.append(rt.output_queue.get_nowait())
    s = rt.session(SESSION)
    return {
        "actions": actions,
        "trace": rt.trace,
        "effects": [(e.tool, e.payload) for e in s.sandbox.effects],
        "registry": {m.name: m for m in s.registry.all()},
        "speculation": rt.speculation_metrics().get(SESSION),
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _norm(v: Any) -> Any:
    return v.strip().casefold() if isinstance(v, str) else v


def _match(item: dict[str, Any], tool: str, args: dict[str, Any]) -> bool:
    if item["tool"] != tool:
        return False
    if any(_norm(args.get(k)) != _norm(v) for k, v in item.get("args", {}).items()):
        return False
    if any(k in args for k in item.get("args_absent", [])):
        return False
    return all(str(v).casefold() in str(args.get(k, "")).casefold() for k, v in item.get("args_contain", {}).items())


def _mean(xs: list[float], empty: float = 1.0) -> float:
    return sum(xs) / len(xs) if xs else empty


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))], 3)


def score_scenario(sc: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    exp = sc["expect"]
    trace = run["trace"]
    actions = run["actions"]
    dispatches = [t for t in trace if t["event"] == "dispatch"]
    cancels = {t["call_id"]: t for t in trace if t["event"] == "cancel"}
    completions = [t for t in trace if t["event"] == "tool_completed"]
    eots = [t for t in trace if t["event"] == "input" and t["type"] in {"eot", "audio", "frame"}]
    finals = [a for a in actions if isinstance(a, FinalAction)]
    speaks = [a for a in actions if a.type == "speak"]
    clarifies = [a for a in actions if a.type == "clarify"]
    notes: list[str] = []

    # ---------------- task completion
    tc: list[float] = []
    for item in exp.get("completed", []):
        ok = any(_match(item, c["tool"], c["args"]) for c in completions)
        tc.append(1.0 if ok else 0.0)
        if not ok:
            notes.append(f"not completed: {item}")
    forbidden = [c for c in completions for item in exp.get("not_completed", []) if _match(item, c["tool"], c["args"])]
    if exp.get("not_completed"):
        tc.append(0.0 if forbidden else 1.0)
        if forbidden:
            notes.append(f"forbidden completion: {[f['tool'] for f in forbidden]}")
    slots_frac = None
    if exp.get("final_slots"):
        last = finals[-1].snapshot.slots if finals else {}
        hits = [_norm(last.get(k)) == _norm(v) for k, v in exp["final_slots"].items()]
        slots_frac = sum(hits) / len(hits)
        tc.append(slots_frac)
        if slots_frac < 1:
            notes.append(f"final slots {last} vs {exp['final_slots']}")
    if exp.get("clarify"):
        tc.append(1.0 if clarifies else 0.0)
        if not clarifies:
            notes.append("expected a clarification")
    for needle in exp.get("speak_contains", []):
        ok = any(needle.casefold() in a.text.casefold() for a in speaks + clarifies)
        tc.append(1.0 if ok else 0.0)
        if not ok:
            notes.append(f"never said {needle!r}")
    task = _mean(tc)

    # ---------------- interruption recovery
    ir: list[float] = []
    stale_reruns = stale_completions = 0
    superseded_cancel_ms: list[float] = []
    for item in exp.get("cancelled", []):
        hits = [d for d in dispatches if _match(item, d["tool"], d["args"])]
        cancelled = [d for d in hits if d["call_id"] in cancels]
        ok = bool(hits) and len(cancelled) == len(hits)
        done = [c for c in completions if _match(item, c["tool"], c["args"])]
        stale_completions += len(done)
        if cancelled:
            first_cancel = min(cancels[d["call_id"]]["ts_ms"] for d in cancelled)
            stale_reruns += sum(1 for d in hits if d["ts_ms"] > first_cancel)
            for d in cancelled:
                c = cancels[d["call_id"]]
                trig = [e["ts_ms"] for e in eots if e["ts_ms"] <= c["ts_ms"]]
                if trig:
                    superseded_cancel_ms.append(c["ts_ms"] - max(trig))
        ir.append(1.0 if ok and not done else 0.0)
        if not ok:
            notes.append(f"superseded call not cancelled: {item}")
        if done:
            notes.append(f"stale call completed: {item}")
    for item in exp.get("kept", []):
        hits = [d for d in dispatches if _match(item, d["tool"], d["args"])]
        ok = len(hits) == 1 and hits[0]["call_id"] not in cancels
        ir.append(1.0 if ok else 0.0)
        if not ok:
            notes.append(f"kept work was cancelled or restarted: {item} ({len(hits)} dispatches)")
    applicable = bool(exp.get("cancelled") or exp.get("kept"))
    if applicable:
        ir.append(0.0 if stale_reruns else 1.0)
        if slots_frac is not None:
            ir.append(slots_frac)
    recovery = _mean(ir)

    # ---------------- latency
    ack_ms: list[float] = []
    for e in eots:
        after = [a.ts_ms for a in actions if a.type in {"speak", "clarify"} and a.ts_ms is not None and a.ts_ms >= e["ts_ms"]]
        if after:
            ack_ms.append(min(after) - e["ts_ms"])
    latency = _mean([1.0 if ms <= 300 else max(0.0, 1 - (ms - 300) / 1700) for ms in ack_ms], empty=0.0)
    pivot_ms: list[float] = []
    if exp.get("cancelled"):
        cancel_ts = sorted(c["ts_ms"] for c in cancels.values())
        for e in eots:
            if any(e["ts_ms"] <= ts <= e["ts_ms"] + 1000 for ts in cancel_ts):
                nxt = [d["ts_ms"] for d in dispatches if d["ts_ms"] >= e["ts_ms"]]
                if nxt:
                    pivot_ms.append(min(nxt) - e["ts_ms"])

    # ---------------- safety & protocol
    reg = run["registry"]
    identities = [(t, json.dumps(p, sort_keys=True, default=str)) for t, p in run["effects"]]
    duplicates = len(identities) - len(set(identities))
    caps_ok = all(sum(1 for t, _ in run["effects"] if t == tool) <= n for tool, n in exp.get("max_effects", {}).items())
    if not caps_ok:
        notes.append(f"effect cap exceeded: {[t for t, _ in run['effects']]}")
    schema_ok = True
    for a in actions:
        try:
            _ACTION.validate_python(json.loads(a.model_dump_json()))
        except Exception:  # noqa: BLE001
            schema_ok = False
    call_ids = [a.call_id for a in actions if a.type == "tool_call"]
    ids_ok = len(call_ids) == len(set(call_ids)) and all(
        a.call_id in call_ids for a in actions if a.type == "cancel"
    )
    safety = _mean([0.0 if duplicates else 1.0, 1.0 if caps_ok else 0.0, 1.0 if schema_ok else 0.0, 1.0 if ids_ok else 0.0])

    categories = {
        "task_completion": round(task, 4),
        "interruption_recovery": round(recovery, 4),
        "latency": round(latency, 4),
        "safety_protocol": round(safety, 4),
    }
    irreversible = {n for n, m in reg.items() if m.mutation_class == "IRREVERSIBLE"}
    regretted = sum(
        1 for c in completions if c["tool"] in irreversible and not c.get("reused")
        and any(_match(item, c["tool"], c["args"]) for item in exp.get("not_completed", []))
    )
    return {
        "name": sc["name"],
        "theme": sc.get("theme", ""),
        "score": round(100 * sum(WEIGHTS[k] * v for k, v in categories.items()), 2),
        **categories,
        "interruption_applicable": applicable,
        "tool_calls": len(call_ids),
        "cancels": len(cancels),
        "clarifications": len(clarifies),
        "unnecessary_clarification": bool(clarifies) and not exp.get("clarify", False),
        "speaks_per_turn": round(len(speaks) / max(1, len(eots)), 2),
        "stale_completions": stale_completions,
        "stale_reruns": stale_reruns,
        "duplicate_effects": duplicates,
        "regretted_irreversible": regretted,
        "ack_ms": [round(x, 3) for x in ack_ms],
        "pivot_ms": [round(x, 3) for x in pivot_ms],
        "cancel_ms": [round(x, 3) for x in superseded_cancel_ms],
        "speculation": run.get("speculation"),
        "notes": notes,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ack = [x for r in rows for x in r["ack_ms"]]
    piv = [x for r in rows for x in r["pivot_ms"]]
    can = [x for r in rows for x in r["cancel_ms"]]
    spec = [r["speculation"] for r in rows if r.get("speculation")]
    out = {
        "scenarios": len(rows),
        "mean_score": round(statistics.fmean(r["score"] for r in rows), 2),
        **{k: round(statistics.fmean(r[k] for r in rows), 4) for k in WEIGHTS},
        "stale_action_rate": round(sum(r["stale_completions"] > 0 for r in rows) / len(rows), 4),
        "stale_reruns": sum(r["stale_reruns"] for r in rows),
        "duplicate_mutations": sum(r["duplicate_effects"] for r in rows),
        "regretted_irreversible": sum(r["regretted_irreversible"] for r in rows),
        "unnecessary_clarification_rate": round(sum(r["unnecessary_clarification"] for r in rows) / len(rows), 4),
        "tool_calls": sum(r["tool_calls"] for r in rows),
        "cancels": sum(r["cancels"] for r in rows),
        "ack_p50_ms": _pct(ack, 0.5),
        "ack_p95_ms": _pct(ack, 0.95),
        "pivot_p50_ms": _pct(piv, 0.5),
        "pivot_p95_ms": _pct(piv, 0.95),
        "cancel_p95_ms": _pct(can, 0.95),
        "speaks_per_turn": round(statistics.fmean(r["speaks_per_turn"] for r in rows), 2),
    }
    if spec:
        out["speculation"] = {
            "spawned": sum(s["spawned"] for s in spec),
            "promoted": sum(s["promoted"] for s in spec),
            "discarded": sum(s["discarded"] for s in spec),
            "latency_saved_ms": round(sum(s["latency_saved_ms"] for s in spec), 2),
            "wasted_tool_ms": round(sum(s["wasted_tool_ms"] for s in spec), 2),
        }
        sp = out["speculation"]
        sp["reuse_rate"] = round(sp["promoted"] / sp["spawned"], 3) if sp["spawned"] else 0.0
    return out


async def _evaluate_async(suite: list[dict[str, Any]], configs: dict[str, dict[str, Any]], time_scale: float) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, kwargs in configs.items():
        rows = []
        for sc in suite:
            run = await run_scenario(sc, time_scale=time_scale, **kwargs)
            rows.append(score_scenario(sc, run))
        out[name] = {"config": kwargs, "summary": summarize(rows), "scenarios": rows}
    return out


def evaluate(
    suite_path: Path = SUITE_DEFAULT,
    *,
    systems: dict[str, dict[str, Any]] | None = None,
    ablations: bool = True,
    time_scale: float = 0.1,
) -> dict[str, Any]:
    suite = load_suite(suite_path)
    configs = dict(systems or SYSTEMS)
    if ablations:
        configs.update(ABLATIONS)
    results = asyncio.run(_evaluate_async(suite, configs, time_scale))
    return {
        "suite": str(suite_path).replace("\\", "/"),
        "time_scale": time_scale,
        "weights": WEIGHTS,
        "note": "Scores approximate the Theme 05 rubric from the guide; the official scorer is not released.",
        "systems": results,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def render_markdown(report: dict[str, Any]) -> str:
    sysd = report["systems"]
    lines = [
        "# Runtime evaluation — timed scenarios through `AgentRuntime`",
        "",
        f"Suite `{report['suite']}` ({sysd['continuum']['summary']['scenarios']} text scenarios), time scale "
        f"{report['time_scale']} (event times and tool delays scaled together; latencies are real in-process ms). "
        "Category weights follow the Theme 05 guide (40 / 35 / 15 / 10); the scorer is our approximation — the "
        "official one is not released.",
        "",
        "## Systems",
        "",
        "| System | Score | Task | Interrupt | Latency | Safety | Stale-action rate | Stale reruns | Dup. mutations | Regretted irreversible | Unneeded clarify | Tool calls | Pivot p50 / p95 ms | Ack p95 ms | Cancel p95 ms |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, res in sysd.items():
        s = res["summary"]
        lines.append(
            f"| {name} | **{s['mean_score']}** | {s['task_completion']} | {s['interruption_recovery']} | "
            f"{s['latency']} | {s['safety_protocol']} | {s['stale_action_rate']} | {s['stale_reruns']} | "
            f"{s['duplicate_mutations']} | {s['regretted_irreversible']} | {s['unnecessary_clarification_rate']} | "
            f"{s['tool_calls']} | {s['pivot_p50_ms']} / {s['pivot_p95_ms']} | {s['ack_p95_ms']} | {s['cancel_p95_ms']} |"
        )
    lines += [
        "",
        "`continuum` = generic planner + reconcile/advance executor. `naive_runtime` = the runtime path before this "
        "work (first read-only tool, args copied from arbiter state, cancel everything on any change). Rows below "
        "the three systems are single-mechanism ablations of `continuum`.",
        "",
        "## Per scenario (continuum vs naive runtime)",
        "",
        "| Scenario | Theme | continuum | naive | What continuum did not get right |",
        "|---|---|---|---|---|",
    ]
    naive = {r["name"]: r for r in sysd.get("naive_runtime", {}).get("scenarios", [])}
    for r in sysd["continuum"]["scenarios"]:
        nv = naive.get(r["name"], {}).get("score", "—")
        lines.append(f"| {r['name']} | {r['theme']} | {r['score']} | {nv} | {'; '.join(r['notes']) or '—'} |")
    base = {r["name"]: r for r in sysd["continuum"]["scenarios"]}
    abl = [n for n in sysd if n in ABLATIONS]
    if abl:
        lines += ["", "## Ablations — what each mechanism buys", "", "| Ablation | Score (Δ vs continuum) | Scenarios that got worse |", "|---|---|---|"]
        ref = sysd["continuum"]["summary"]["mean_score"]
        for n in abl:
            s = sysd[n]["summary"]
            worse = [
                f"{r['name']} ({base[r['name']]['score']}→{r['score']}: {'; '.join(r['notes'][:2])})"
                for r in sysd[n]["scenarios"]
                if r["score"] < base[r["name"]]["score"] - 0.01
            ]
            lines.append(f"| {n} | {s['mean_score']} ({s['mean_score'] - ref:+.2f}) | {'<br>'.join(worse) or 'none'} |")
    spec = sysd.get("continuum+speculation", {}).get("summary", {}).get("speculation")
    if spec:
        lines += [
            "",
            "## Speculation (read-only shadows)",
            "",
            f"Spawned {spec['spawned']}, promoted {spec['promoted']} (reuse rate {spec['reuse_rate']}), discarded "
            f"{spec['discarded']}; tool latency hidden by promoted shadows {spec['latency_saved_ms']} ms (scaled clock), "
            f"shadow tool time wasted {spec['wasted_tool_ms']} ms. Shadows are local and never emitted as harness "
            "tool calls, so they do not change the scored action stream; enable them only where the extra backend "
            "load is acceptable.",
        ]
    return "\n".join(lines) + "\n"

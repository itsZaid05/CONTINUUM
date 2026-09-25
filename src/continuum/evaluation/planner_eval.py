"""
Planner evaluation — tool selection, argument extraction, clarification and
safety of the manifest-driven planner against a frozen gold set.

Each gold row is one turn: the manifests in scope, the session memory before
the turn (``slots``, ``active_goals``), the utterance, and what a correct plan
looks like. The turn runs through the same path the runtime uses —
``delta_arbiter.arbitrate`` for the category, ``GenericPlanner.plan`` for the
plan — so arbiter mistakes are measured here too, not hidden.

Splits: ``dev`` was used while building the planner; ``heldout`` (including
two domains the planner never saw during development: dining, home) was run
once, after development stopped. Both are reported separately.

A ``baseline`` system reproduces the runtime's pre-planner behaviour
(``ToolRegistry.choose_read_tool`` + ``bind_args``: first read-only tool, args
copied from state, crash on a missing required argument) for comparison.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..delta_arbiter import arbitrate
from ..generic_planner import GenericPlanner, PlanDecision
from ..tools import ToolRegistry, default_registry, load_manifests
from ..versioned_state import VersionedStore

GOLD_DEFAULT = Path("data/gold/planner_gold.jsonl")
MANIFEST_DIR = Path("data/manifests")
REF_DATE = date(2026, 9, 25)  # "tomorrow" in the gold set means 2026-09-26


def gold_hash(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def load_gold(path: Path = GOLD_DEFAULT) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def registry_for(names: list[str], manifest_dir: Path = MANIFEST_DIR) -> ToolRegistry:
    reg = ToolRegistry()
    for name in names:
        items = default_registry().all() if name == "builtin" else load_manifests(manifest_dir / f"{name}.json")
        for m in items:
            reg.register(m)
    return reg


# ---------------------------------------------------------------------------
# Systems under test
# ---------------------------------------------------------------------------


@dataclass
class Prediction:
    action: str
    goal: str | None
    tools: list[str]
    args: dict[str, dict[str, Any]]
    missing: list[str]
    state_changing: bool
    needs_confirmation: bool
    latency_ms: float
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def predict_planner(case: dict[str, Any], manifest_dir: Path = MANIFEST_DIR) -> Prediction:
    reg = registry_for(case["manifests"], manifest_dir)
    slots = dict(case.get("slots", {}))
    store = VersionedStore()
    store.create_initial(slots)
    decision = arbitrate(case["utterance"], store.current())
    planner = GenericPlanner(reg, today=REF_DATE)
    d: PlanDecision = planner.plan(
        case["utterance"], decision=decision, slots=slots, goals=case.get("active_goals", [])
    )
    return Prediction(
        action=d.action,
        goal=d.goals[-1] if d.goals else None,
        tools=d.tools,
        args={s.tool: dict(s.params) for s in d.steps},
        missing=[a for _t, a in d.missing],
        state_changing=d.state_changing,
        needs_confirmation=bool(d.needs_confirmation),
        latency_ms=d.latency_ms,
        raw={**d.to_dict(), "arbiter": decision.category.value},
    )


def predict_baseline(case: dict[str, Any], manifest_dir: Path = MANIFEST_DIR) -> Prediction:
    """The runtime's behaviour before the generic planner existed."""
    reg = registry_for(case["manifests"], manifest_dir)
    state = dict(case.get("slots", {}))
    tool = reg.choose_read_tool(state)
    if tool is None:
        return Prediction("noop", None, [], {}, [], False, False, 0.0)
    try:
        args = reg.bind_args(tool.name, state)
    except ValueError as exc:
        return Prediction("error", tool.name, [tool.name], {}, [], False, False, 0.0, error=str(exc))
    return Prediction("plan", tool.name, [tool.name], {tool.name: args}, [], tool.state_changing, False, 0.0)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _norm(v: Any) -> Any:
    if isinstance(v, str):
        return v.strip().casefold()
    return v


def _is_ref(v: Any) -> bool:
    return isinstance(v, str) and v.startswith("ref(")


def score_case(case: dict[str, Any], p: Prediction) -> dict[str, Any]:
    exp = case["expect"]
    row: dict[str, Any] = {
        "id": case["id"],
        "split": case["split"],
        "domain": case["manifests"][0],
        "utterance": case["utterance"],
        "expected_action": exp["action"],
        "predicted_action": p.action,
        "action_ok": p.action == exp["action"],
        "tp": 0,
        "fp": 0,
        "fn": 0,
        "unsafe_write": False,
        "confirm_ok": None,
        "latency_ms": p.latency_ms,
        "errors": [],
    }
    if p.error:
        row["errors"].append(f"crash: {p.error}")
    if exp["action"] in {"plan", "clarify"}:
        row["goal_ok"] = p.goal == exp.get("goal")
        if not row["goal_ok"]:
            row["errors"].append(f"goal {p.goal} != {exp.get('goal')}")
    if exp["action"] == "plan":
        row["tools_ok"] = p.tools == exp["tools"]
        if not row["tools_ok"]:
            row["errors"].append(f"tools {p.tools} != {exp['tools']}")
        expected = {(t, k, _norm(v)) for t, kv in exp.get("args", {}).items() for k, v in kv.items()}
        optional = {(t, k, _norm(v)) for t, kv in exp.get("args_optional", {}).items() for k, v in kv.items()}
        contain = {(t, k): str(v).casefold() for t, kv in exp.get("args_contain", {}).items() for k, v in kv.items()}
        predicted = {
            (t, k, _norm(v))
            for t, kv in p.args.items()
            if t in exp["tools"]
            for k, v in kv.items()
            if not _is_ref(v) and (t, k) not in contain
        }
        tp = len(predicted & (expected | optional))
        fp = len(predicted - expected - optional)
        fn = len(expected - predicted)
        for (t, k), needle in contain.items():
            got = p.args.get(t, {}).get(k)
            if got is not None and needle in str(got).casefold():
                tp += 1
            else:
                fn += 1
                row["errors"].append(f"{t}.{k}={got!r} lacks {needle!r}")
        for miss in sorted(expected - predicted):
            got = p.args.get(miss[0], {}).get(miss[1])
            row["errors"].append(f"{miss[0]}.{miss[1]}={got!r} expected {miss[2]!r}")
        for extra in sorted(predicted - expected - optional, key=str):
            # a wrong value for an expected key was already reported above
            if not any(e[:2] == extra[:2] for e in expected):
                row["errors"].append(f"extra arg {extra[0]}.{extra[1]}={extra[2]!r}")
        row.update(tp=tp, fp=fp, fn=fn)
        row["unsafe_write"] = p.state_changing and not exp.get("state_changing", False)
        if "confirm" in exp:
            row["confirm_ok"] = p.needs_confirmation == exp["confirm"]
            if not row["confirm_ok"]:
                row["errors"].append(f"needs_confirmation={p.needs_confirmation} expected {exp['confirm']}")
    elif exp["action"] == "clarify":
        row["missing_ok"] = sorted(p.missing) == sorted(exp.get("missing", []))
        if not row["missing_ok"]:
            row["errors"].append(f"missing {p.missing} != {exp.get('missing')}")
        # asking is safe; *acting* on a state-changing step while asking is not
        row["unsafe_write"] = p.action == "plan" and p.state_changing
    else:  # noop
        row["unsafe_write"] = p.action == "plan" and p.state_changing
    if not row["action_ok"]:
        row["errors"].insert(0, f"action {p.action} != {exp['action']}")
    row["correct"] = not row["errors"]
    return row


def _p(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))], 3)


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    tp = sum(r["tp"] for r in rows)
    fp = sum(r["fp"] for r in rows)
    fn = sum(r["fn"] for r in rows)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    goal_rows = [r for r in rows if "goal_ok" in r]
    plan_rows = [r for r in rows if "tools_ok" in r]
    clar_exp = [r for r in rows if r["expected_action"] == "clarify"]
    clar_pred = [r for r in rows if r["predicted_action"] == "clarify"]
    conf_rows = [r for r in rows if r["confirm_ok"] is not None]
    lat = [r["latency_ms"] for r in rows]
    return {
        "cases": n,
        "fully_correct": round(sum(r["correct"] for r in rows) / n, 4) if n else 0.0,
        "action_accuracy": round(sum(r["action_ok"] for r in rows) / n, 4) if n else 0.0,
        "goal_accuracy": round(sum(r["goal_ok"] for r in goal_rows) / len(goal_rows), 4) if goal_rows else 0.0,
        "plan_exact_match": round(sum(r["tools_ok"] for r in plan_rows) / len(plan_rows), 4) if plan_rows else 0.0,
        "arg_precision": round(prec, 4),
        "arg_recall": round(rec, 4),
        "arg_f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0.0,
        "clarify_precision": round(
            sum(r["expected_action"] == "clarify" for r in clar_pred) / len(clar_pred), 4
        ) if clar_pred else 0.0,
        "clarify_recall": round(
            sum(r["predicted_action"] == "clarify" for r in clar_exp) / len(clar_exp), 4
        ) if clar_exp else 0.0,
        "unnecessary_clarification_rate": round(
            sum(r["predicted_action"] == "clarify" and r["expected_action"] != "clarify" for r in rows)
            / max(1, sum(r["expected_action"] != "clarify" for r in rows)),
            4,
        ),
        "confirmation_accuracy": round(sum(r["confirm_ok"] for r in conf_rows) / len(conf_rows), 4)
        if conf_rows else 0.0,
        "unsafe_write_plans": sum(r["unsafe_write"] for r in rows),
        "crashes": sum(any(e.startswith("crash") for e in r["errors"]) for r in rows),
        "latency_p50_ms": _p(lat, 0.5),
        "latency_p95_ms": _p(lat, 0.95),
        "latency_mean_ms": round(statistics.fmean(lat), 3) if lat else 0.0,
    }


def evaluate(
    gold_path: Path = GOLD_DEFAULT,
    *,
    system: str = "planner",
    split: str | None = None,
    manifest_dir: Path = MANIFEST_DIR,
) -> dict[str, Any]:
    cases = [c for c in load_gold(gold_path) if split is None or c["split"] == split]
    predict = predict_planner if system == "planner" else predict_baseline
    rows = []
    for case in cases:
        pred = predict(case, manifest_dir)
        row = score_case(case, pred)
        row["prediction"] = {"action": pred.action, "goal": pred.goal, "tools": pred.tools, "args": pred.args,
                             "missing": pred.missing, "needs_confirmation": pred.needs_confirmation}
        if pred.raw:
            row["prediction"]["arbiter"] = pred.raw.get("arbiter")
            row["prediction"]["mode"] = pred.raw.get("mode")
        rows.append(row)
    by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_split[r["split"]].append(r)
        by_domain[r["domain"]].append(r)
    return {
        "system": system,
        "gold": str(gold_path).replace("\\", "/"),
        "gold_hash": gold_hash(gold_path),
        "reference_date": REF_DATE.isoformat(),
        "overall": aggregate(rows),
        "by_split": {k: aggregate(v) for k, v in sorted(by_split.items())},
        "by_domain": {k: aggregate(v) for k, v in sorted(by_domain.items())},
        "cases": rows,
    }


def render_markdown(planner: dict[str, Any], baseline: dict[str, Any] | None = None) -> str:
    keys = [
        ("fully_correct", "Fully correct turns"),
        ("action_accuracy", "Action accuracy (plan / clarify / no-op)"),
        ("goal_accuracy", "Goal-tool accuracy"),
        ("plan_exact_match", "Plan exact match (tool sequence)"),
        ("arg_precision", "Argument precision"),
        ("arg_recall", "Argument recall"),
        ("arg_f1", "Argument F1"),
        ("clarify_precision", "Clarification precision"),
        ("clarify_recall", "Clarification recall"),
        ("unnecessary_clarification_rate", "Unnecessary clarification rate (target < 5%)"),
        ("confirmation_accuracy", "Irreversible-confirmation accuracy"),
        ("unsafe_write_plans", "Unsafe state-changing plans (target 0)"),
        ("crashes", "Crashes"),
        ("latency_p95_ms", "Planning latency p95 (ms)"),
    ]
    splits = list(planner["by_split"])
    lines = [
        "# Planner evaluation — manifest-driven generic planner",
        "",
        f"Gold `{planner['gold']}` (hash `{planner['gold_hash']}`), reference date {planner['reference_date']}, "
        f"{planner['overall']['cases']} turns. `dev` was used during development; `heldout` was run once afterwards "
        "and includes two domains never seen during development (dining, home).",
        "",
        "| Metric | " + " | ".join(f"planner · {s}" for s in splits) + " | planner · all"
        + (" | baseline · all |" if baseline else " |"),
        "|---|" + "---|" * (len(splits) + 1 + (1 if baseline else 0)),
    ]
    for key, label in keys:
        cells = [str(planner["by_split"][s][key]) for s in splits] + [str(planner["overall"][key])]
        if baseline:
            cells.append(str(baseline["overall"][key]))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "## By domain (planner)", "", "| Domain | Turns | Fully correct | Goal acc. | Arg F1 |", "|---|---|---|---|---|"]
    for dom, agg in planner["by_domain"].items():
        lines.append(f"| {dom} | {agg['cases']} | {agg['fully_correct']} | {agg['goal_accuracy']} | {agg['arg_f1']} |")
    fails = [r for r in planner["cases"] if not r["correct"]]
    lines += ["", f"## Incorrect turns ({len(fails)})", ""]
    if fails:
        lines += ["| Id | Split | Utterance | What went wrong |", "|---|---|---|---|"]
        for r in fails:
            lines.append(f"| {r['id']} | {r['split']} | {r['utterance']} | {'; '.join(r['errors'])} |")
    else:
        lines.append("None.")
    if baseline:
        lines += [
            "",
            "Baseline = the runtime's pre-planner path (`choose_read_tool` + `bind_args`): first read-only "
            "tool in the registry, arguments copied from state by exact name, `ValueError` on a missing "
            "required argument (counted as a crash).",
        ]
    return "\n".join(lines) + "\n"

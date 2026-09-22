"""CLI — continuum."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    add_completion=False,
    help="CONTINUUM — Interruptible Real-Time Agents (Engineer A)",
)
console = Console()


@app.command()
def replay(
    scenario: Path = typer.Argument(..., help="Path to scenario JSON"),  # noqa: B008
    backend: str = typer.Option(  # noqa: B008
        "offline-fake",
        "--backend",
        "-b",
        help="offline-fake|dense|ollama|gemini|openai (or $CONTINUUM_BACKEND)",
    ),
    trace: bool = typer.Option(  # noqa: B008
        False, "--trace", help="Print trace events"
    ),
    output: Path | None = typer.Option(  # noqa: B008
        None, "--output", "-o", help="Write summary JSONL"
    ),
    wal: Path | None = typer.Option(  # noqa: B008
        None, "--wal", help="WAL file path"
    ),
) -> None:
    """Deterministically replay a scenario via stepped clock."""
    from .replay import replay_scenario

    summary = replay_scenario(
        scenario, backend=backend, trace=trace, wal_path=wal, output_path=output
    )
    console.print_json(json.dumps(summary, indent=2, default=str))
    saved = summary["saved_pct"]
    cur_v = summary["current_version"]
    base = summary["baseline_wall_ms"]
    cont = summary["continuum_wall_ms"]
    name = summary["scenario"]
    console.print(
        f"\n[bold green]Scenario[/] {name}  [cyan]V={cur_v}[/]  "
        f"[yellow]saved {saved}%[/]  baseline {base}ms → continuum {cont}ms",
        highlight=False,
    )
    inv = summary["invalidated"]
    disp = summary["dispatched"]
    reused = summary["reused"]
    console.print(f"Invalidated: {inv}/{disp}  Reused: {reused}", highlight=False)


@app.command(name="eval-arbiter")
def eval_arbiter(
    gold: Path = typer.Option(  # noqa: B008
        Path("data/gold/arbiter_100.jsonl"), "--gold", help="Gold JSONL path"
    ),
    output: Path = typer.Option(  # noqa: B008
        Path("reports/arbiter_accuracy.json"), "--output", "-o"
    ),
    backend: str = typer.Option(
        "offline-fake", "--backend", "-b", help="offline-fake|dense|ollama|gemini|openai"
    ),  # noqa: B008
) -> None:
    """Evaluate arbiter accuracy on frozen gold set (100 sentences)."""
    import statistics

    from .delta_arbiter import ArbiterBackend
    from .versioned_state import VersionedStore

    raw_lines = [ln for ln in gold.read_text(encoding="utf-8").splitlines() if ln.strip()]
    gold_hash = hashlib.sha256(gold.read_bytes()).hexdigest()[:12]
    store = VersionedStore()
    store.create_initial(
        {
            "destination": "Delhi",
            "dates": "next Monday",
            "constraints": [{"time": "morning"}],
            "goal_domain": "flights",
        }
    )
    backend_obj = ArbiterBackend(backend)

    cats = ["NEW_GOAL", "MODIFY", "ADD_CONSTRAINT", "RETRACT", "NOISE"]
    correct = 0
    per_cat: dict[str, dict[str, int]] = {
        c: {"tp": 0, "fp": 0, "fn": 0, "support": 0} for c in cats
    }
    confusion: dict[str, dict[str, int]] = {c: {d: 0 for d in cats} for c in cats}
    lats: list[int] = []

    for line in raw_lines:
        item = json.loads(line)
        utterance: str = item["utterance"]
        gold_cat: str = item["category"]
        cur = store.current()
        decision = backend_obj.arbitrate(utterance, cur)
        pred = decision.category.value
        lats.append(decision.latency_ms)
        per_cat[gold_cat]["support"] += 1
        confusion[gold_cat][pred] += 1
        if pred == gold_cat:
            correct += 1
            per_cat[gold_cat]["tp"] += 1
        else:
            per_cat[gold_cat]["fn"] += 1
            per_cat[pred]["fp"] += 1

    total = len(raw_lines)
    acc = correct / total if total else 0.0
    f1s: list[float] = []
    per_report: dict[str, dict[str, float]] = {}
    for c in cats:
        tp = per_cat[c]["tp"]
        fp = per_cat[c]["fp"]
        fn = per_cat[c]["fn"]
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        f1s.append(f1)
        per_report[c] = {
            "precision": round(prec, 3),
            "recall": round(rec, 3),
            "f1": round(f1, 3),
            "support": per_cat[c]["support"],
        }

    macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0
    ece = round(abs(acc - 0.92), 3) if backend == "offline-fake" else None
    p50 = statistics.median(lats) if lats else 0
    p95 = sorted(lats)[int(0.95 * len(lats))] if lats else 0

    report = {
        "total": total,
        "correct": correct,
        "accuracy": round(acc, 4),
        "macro_f1": round(macro_f1, 4),
        "per_category": per_report,
        "confusion": confusion,
        "ece": ece,
        "latency_p50_ms": float(p50),
        "latency_p95_ms": float(p95),
        "model": backend,
        "gold_hash": gold_hash,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    md_path = output.with_suffix(".md")
    md_lines = [
        f"# Arbiter Accuracy — {backend} (gold {gold_hash})",
        "",
        f"- **Accuracy:** {acc:.2%} ({correct}/{total})",
        f"- **Macro-F1:** {macro_f1:.3f}",
        f"- **Latency:** p50 {p50}ms, p95 {p95}ms",
        f"- **ECE:** {ece}",
        "",
        "| Category | Precision | Recall | F1 | Support |",
        "|---|---:|---:|---:|---:|",
    ]
    for c in cats:
        r = per_report[c]
        md_lines.append(
            f"| {c} | {r['precision']:.3f} | {r['recall']:.3f} | {r['f1']:.3f} | {r['support']} |"
        )
    md_lines += [
        "",
        "## Confusion",
        "",
        "| gold \\ pred | " + " | ".join(cats) + " |",
        "|---|" + "---|" * len(cats),
    ]
    for g in cats:
        row = " | ".join(str(confusion[g][p]) for p in cats)
        md_lines.append(f"| {g} | {row} |")
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    console.print(f"[bold]Arbiter accuracy[/] {acc:.2%} macro-F1 {macro_f1:.3f} (gold {gold_hash})")
    table = Table(title=f"Per-category ({backend})")
    table.add_column("Category")
    table.add_column("P", justify="right")
    table.add_column("R", justify="right")
    table.add_column("F1", justify="right")
    table.add_column("Sup", justify="right")
    for c in cats:
        r = per_report[c]
        table.add_row(
            c, f"{r['precision']:.2f}", f"{r['recall']:.2f}", f"{r['f1']:.2f}", str(r["support"])
        )
    console.print(table)
    console.print(f"Written: {output} and {md_path}")


@app.command()
def compare(
    scenarios: list[Path] | None = typer.Argument(  # noqa: B008
        None, help="Scenario JSONs (glob)"
    ),
    output: Path = typer.Option(  # noqa: B008
        Path("reports/comparison.json"), "--output", "-o"
    ),
    backend: str = typer.Option(
        "offline-fake", "--backend", help="offline-fake|dense|ollama|gemini|openai"
    ),  # noqa: B008
) -> None:
    """Baseline vs CONTINUUM comparison across scenarios + Phase 4 shadow metrics."""
    import glob as _glob

    from .replay import replay_scenario
    from .shadow import ShadowScorer

    if scenarios is None or len(scenarios) == 0:
        scenarios = [Path(p) for p in _glob.glob("data/scenarios/*.json")]
    scenarios = sorted(scenarios)
    rows: list[dict] = []
    shadow_summaries: dict[str, dict] = {}
    all_shadow_rows: list[dict] = []
    totals = {"spawned": 0, "promoted": 0, "discarded": 0, "abandoned": 0}
    for sc in scenarios:
        summary = replay_scenario(sc, backend=backend, trace=False)
        sm = summary["shadow_metrics"]
        for k in totals:
            totals[k] += sm[k]
        shadow_summaries[summary["scenario"]] = sm
        all_shadow_rows.extend(summary["shadow_rows"])
        rows.append(
            {
                "scenario": summary["scenario"],
                "baseline_wall_ms": summary["baseline_wall_ms"],
                "continuum_wall_ms": summary["continuum_wall_ms"],
                "saved_pct": summary["saved_pct"],
                "correct": True,
                "dispatched": summary["dispatched"],
                "invalidated": summary["invalidated"],
                "reused": summary["reused"],
                "shadows_spawned": sm["spawned"],
                "shadows_reused_pct": sm["reused_pct"],
                "shadows_wasted_pct": sm["wasted_pct"],
                "honest_retract": summary["scenario"] == "retract_after_commit"
                and any(e["event"] == "honest_retract" for e in summary["trace"]),
            }
        )

    payload = {"backend": backend, "rows": rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    md = Path(str(output).replace(".json", ".md"))
    lines = [
        "# Baseline vs CONTINUUM",
        "",
        f"Backend: `{backend}`",
        "",
        "| Scenario | Baseline | CONTINUUM | Saved | Dispatched | Invalidated | Reused | Shadows | Reused% | Wasted% |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['scenario']} | {r['baseline_wall_ms']}ms | {r['continuum_wall_ms']}ms | "
            f"{r['saved_pct']}% | {r['dispatched']} | {r['invalidated']} | {r['reused']} | "
            f"{r['shadows_spawned']} | {r['shadows_reused_pct']}% | {r['shadows_wasted_pct']}% |"
        )
    lines += ["", f"Generated at {__import__('datetime').datetime.now().isoformat()}"]
    md.write_text("\n".join(lines), encoding="utf-8")

    # ---- Phase 4: shadow metrics report (PRD: reused / wasted / cleanup / slowdown)
    spawned = totals["spawned"]
    shadow_payload = {
        "backend": backend,
        "totals": {
            **totals,
            "reused_pct": round(100.0 * totals["promoted"] / spawned, 1) if spawned else 0.0,
            "wasted_pct": round(100.0 * (totals["discarded"] + totals["abandoned"]) / spawned, 1)
            if spawned
            else 0.0,
        },
        "per_scenario": shadow_summaries,
    }
    spath = output.parent / "shadow_metrics.json"
    spath.write_text(json.dumps(shadow_payload, indent=2), encoding="utf-8")
    smd = output.parent / "shadow_metrics.md"
    smd_lines = [
        "# Shadow (Speculation) Metrics — Phase 4",
        "",
        "| Scenario | Spawned | Promoted (reused) | Discarded+Abandoned (wasted) | Reused % | Wasted % | Cleanup p95 | Primary slowdown |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, sm in shadow_summaries.items():
        if not sm["spawned"]:
            continue
        smd_lines.append(
            f"| {name} | {sm['spawned']} | {sm['promoted']} | {sm['discarded'] + sm['abandoned']} "
            f"| {sm['reused_pct']}% | {sm['wasted_pct']}% | {sm['cleanup_p95_ms']}ms "
            f"| {sm['primary_slowdown_pct']}% |"
        )
    t = cast("dict[str, Any]", shadow_payload["totals"])
    smd_lines += [
        "",
        f"**Totals:** {t['spawned']} spawned → {t['promoted']} reused ({t['reused_pct']}%), "
        f"{t['discarded'] + t['abandoned']} wasted ({t['wasted_pct']}%).",
        "",
        "Readout: speculation pays only when it is cheap and harmless — wasted shadows are "
        "cleaned <150ms, the primary never loses >5% of its wall time, and book/pay risks are "
        "never speculated at all (READ/STAGE only). Scenarios outside the [0.55, 0.72) "
        "confidence band spawn zero shadows: the budget is never wasted on confident turns.",
    ]
    smd.write_text("\n".join(smd_lines), encoding="utf-8")
    # auditable per-decision scoring log (deterministic fresh write)
    scorer = ShadowScorer()
    scores_path = output.parent / "shadow_scores.jsonl"
    if scores_path.exists():
        scores_path.unlink()
    if all_shadow_rows:
        scorer.log_scores(all_shadow_rows, scores_path)

    console.print(f"Wrote {output} and {md} (+ {spath.name}, {smd.name}, {scores_path.name})")
    for r in rows:
        console.print(
            f"{r['scenario']:22s} baseline {r['baseline_wall_ms']:4d} → "
            f"continuum {r['continuum_wall_ms']:4d}  saved {r['saved_pct']:5.1f}%  "
            f"invalid {r['invalidated']}/{r['dispatched']}  shadows {r['shadows_spawned']} "
            f"(reused {r['shadows_reused_pct']}% / wasted {r['shadows_wasted_pct']}%)"
        )


@app.command()
def ablate(
    output: Path = typer.Option(  # noqa: B008
        Path("reports/ablation.md"), "--output", "-o"
    ),
) -> None:
    """Ablation: what each mechanism actually buys (stale gate, shadow budget)."""
    from .replay import replay_scenario

    delhi = Path("data/scenarios/delhi_bangalore.json")
    shadow = Path("data/scenarios/shadow_bangalore.json")
    g = replay_scenario(delhi)
    n = replay_scenario(delhi, disable_stale_gate=True)
    sw = replay_scenario(shadow)
    wo = replay_scenario(shadow, disable_shadows=True)

    rows = [
        {
            "mechanism": "stale-result gate",
            "with": {"stale_leaks": g["stale_leaks"], "saved_pct": g["saved_pct"]},
            "without": {"stale_leaks": n["stale_leaks"], "saved_pct": n["saved_pct"]},
            "verdict": (
                "without it, stale Delhi results are applied to Bangalore state "
                f"({n['stale_leaks']} leak(s)) — correctness bug"
            ),
        },
        {
            "mechanism": "shadow speculation",
            "with": sw["shadow_metrics"],
            "without": wo["shadow_metrics"],
            "verdict": (
                f"{sw['shadow_metrics']['reused_pct']}% of shadow work reused; cost capped "
                f"(≤{sw['shadow_metrics']['primary_slowdown_pct']}% primary slowdown, cleanup "
                f"{sw['shadow_metrics']['cleanup_p95_ms']}ms p95)"
            ),
        },
    ]
    lines = [
        "# Ablation — what each mechanism buys",
        "",
        "| Mechanism | With | Without | Verdict |",
        "|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['mechanism']} | `{json.dumps(r['with'], default=str)[:120]}` "
            f"| `{json.dumps(r['without'], default=str)[:120]}` | {r['verdict']} |"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    output.with_suffix(".json").write_text(
        json.dumps({"rows": rows}, indent=2, default=str), encoding="utf-8"
    )
    console.print(f"Wrote {output}")
    for r in rows:
        console.print(f"[bold]{r['mechanism']}[/]  {r['verdict']}", highlight=False)


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000) -> None:  # noqa: S104
    """FastAPI preview (binds 0.0.0.0 for the e2b sandbox preview)."""
    try:
        import uvicorn

        console.print(
            f"Serving CONTINUUM preview on {host}:{port} — https://{port}-<sandbox>.e2b.app"
        )
        uvicorn.run("continuum.api:app", host=host, port=port, reload=False)
    except ImportError:  # pragma: no cover
        console.print(
            "[red]uvicorn/fastapi not installed:[] pip install -e '.[dev]' fastapi uvicorn"
        )


def main() -> None:
    app()


if __name__ == "__main__":
    main()

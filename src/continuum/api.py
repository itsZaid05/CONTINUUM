"""
FastAPI preview (Phase 5) — exposes replay + metrics for the sandbox preview host.
Binds to 0.0.0.0, allows any origin (preview proxy). Deterministic offline-fake backend.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, JSONResponse

    HAS_FASTAPI = True
except Exception:
    HAS_FASTAPI = False
    FastAPI = object  # type: ignore

_SCENARIOS = [
    ("delhi_bangalore", "core demo: MODIFY + ADD_CONSTRAINT, stale gate, work reuse"),
    ("dont_book_it", "RETRACT before commit: prune book, keep search"),
    ("retract_after_commit", "edge case: honest “already booked” + cancel offer"),
    ("timeout_booking", "edge case: verify-after-timeout, no double-book"),
    ("rapid_burst", "burst of rapid changes (merged before heavy work)"),
    ("shadow_bangalore", "Phase 4: bounded shadow speculation (≤2, READ/STAGE, 50/50 reuse)"),
]

if HAS_FASTAPI:
    app = FastAPI(title="CONTINUUM — Engineer A Preview", version="0.4.0-phase4")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        items = "".join(f'<li><a href="/replay/{n}">{n}</a> — {d}</li>' for n, d in _SCENARIOS)
        return f"""
        <html><head><title>CONTINUUM</title>
        <style>body{{font-family:system-ui;max-width:860px;margin:40px auto;padding:0 20px}}
        pre{{background:#0b1020;color:#7ee787;padding:16px;overflow:auto;border-radius:8px}}
        a{{color:#4aa3ff}} .m{{color:#8899aa}}</style>
        </head><body>
        <h1>CONTINUUM — Interruptible Real-Time Agents <span class="m">v0.4.0-phase4</span></h1>
        <p><b>Engineer A:</b> Understanding, Dialogue &amp; Evaluation · Samsung PRISM Theme 05</p>
        <p><i>Keeps agents consistent when humans change their minds: classify the change,
        keep valid work, discard the rest, speculate within hard budget.</i></p>
        <h3>Scenarios (deterministic offline-fake)</h3>
        <ul>{items}</ul>
        <h3>Metrics</h3>
        <ul>
          <li><a href="/metrics/arbiter">Arbiter accuracy</a> — 5-way classification on frozen gold 100</li>
          <li><a href="/metrics/comparison">Baseline vs CONTINUUM</a> — wall time + reuse per scenario</li>
          <li><a href="/metrics/shadow">Shadow metrics</a> — reused/wasted split, cleanup p95, ≤5% slowdown</li>
          <li><a href="/health">Health</a></li>
        </ul>
        <p class="m">CLI: <code>python -m continuum.cli replay data/scenarios/shadow_bangalore.json --trace</code></p>
        </body></html>
        """

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "phase": "4-speculation",
            "version": "0.4.0-phase4",
            "scenarios": [n for n, _ in _SCENARIOS],
        }

    @app.get("/replay/{scenario}")
    def replay_api(scenario: str) -> JSONResponse:
        from .replay import replay_scenario

        path = Path(f"data/scenarios/{scenario}.json")
        if not path.exists():
            return JSONResponse({"error": f"scenario {scenario} not found"}, status_code=404)
        summary = replay_scenario(path, backend="offline-fake", trace=False)
        return JSONResponse(summary)

    @app.get("/metrics/arbiter")
    def arbiter_metrics() -> JSONResponse:
        p = Path("reports/arbiter_accuracy.json")
        if not p.exists():
            return JSONResponse({"error": "run eval-arbiter first"}, status_code=404)
        return JSONResponse(json.loads(p.read_text()))

    @app.get("/metrics/comparison")
    def comparison_metrics() -> JSONResponse:
        p = Path("reports/comparison.json")
        if not p.exists():
            return JSONResponse({"error": "run compare first"}, status_code=404)
        return JSONResponse(json.loads(p.read_text()))

    @app.get("/metrics/shadow")
    def shadow_metrics() -> JSONResponse:
        p = Path("reports/shadow_metrics.json")
        if not p.exists():
            return JSONResponse({"error": "run compare first"}, status_code=404)
        return JSONResponse(json.loads(p.read_text()))

else:
    app = None  # type: ignore

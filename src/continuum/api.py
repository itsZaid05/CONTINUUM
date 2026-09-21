"""
Minimal FastAPI preview for Phase 5 — exposes replay and metrics for e2b preview.
Binds to 0.0.0.0, allows preview host.
"""

from __future__ import annotations

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

if HAS_FASTAPI:
    app = FastAPI(title="CONTINUUM — Engineer A Preview", version="0.1.0-phase1")

    # Allow preview host (e2b)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return """
        <html><head><title>CONTINUUM</title>
        <style>body{font-family:system-ui;max-width:800px;margin:40px auto;padding:0 20px} pre{background:#111;color:#0f0;padding:16px;overflow:auto} a{color:#0a6}</style>
        </head><body>
        <h1>CONTINUUM — Interruptible Real-Time Agents (Phase 1)</h1>
        <p>Engineer A: Understanding, Dialogue & Evaluation</p>
        <ul>
          <li><a href="/replay/delhi_bangalore">Replay Delhi→Bangalore (JSON)</a></li>
          <li><a href="/metrics/arbiter">Arbiter accuracy (JSON)</a></li>
          <li><a href="/metrics/comparison">Baseline vs CONTINUUM (JSON)</a></li>
          <li><a href="/health">Health</a></li>
        </ul>
        <p>CLI: <code>python -m continuum.cli replay data/scenarios/delhi_bangalore.json --trace</code></p>
        <pre id="log">Loading...</pre>
        <script>
          fetch('/health').then(r=>r.json()).then(j=>document.getElementById('log').textContent=JSON.stringify(j,null,2))
        </script>
        </body></html>
        """

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "phase": "1-foundation", "version": "0.1.0-phase1"}

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
        import json

        return JSONResponse(json.loads(p.read_text()))

    @app.get("/metrics/comparison")
    def comparison_metrics() -> JSONResponse:
        p = Path("reports/comparison.json")
        if not p.exists():
            return JSONResponse({"error": "run compare first"}, status_code=404)
        import json

        return JSONResponse(json.loads(p.read_text()))

else:
    app = None  # type: ignore

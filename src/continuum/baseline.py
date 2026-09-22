"""
Baseline Agent (Phase 5 — plan Task 5.1.A): the naive redo-all agent.

Independent of CONTINUUM's machinery — deliberately: no versioned state, no
selective invalidation, no stale gate, no effect-ledger verify, no retraction
semantics. Every user utterance re-plans the whole task and re-dispatches all
side-effecting steps; two LLM calls per utterance (delta extraction AND intent
classification, as pre-fused pipelines do). This is the honest strawman the
comparison table measures against — same scenario files, same tool latencies.

Determinism: pure function of the scenario JSON (no RNG, no wall clock).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .replay import TOOL_LATENCY_MS

# Naive per-call LLM latency (ms): extraction + classification = 2 calls,
# ~30ms each under the offline-fake lane (same scale as continuum's 1×30ms).
LLM_CALL_MS = 30
# The naive agent re-plans everything on every turn: search then book.
NAIVE_PLAN: list[str] = ["search", "book"]
_RETRACT_RE = re.compile(r"\b(don'?t|do not|cancel|never mind|stop|abort)\b", re.I)
_NEGATION_BOOK_RE = re.compile(r"\b(don'?t|do not|never)\b.*\bbook\b", re.I)


def _tool_result_stale_after_change(turns: list[dict[str, Any]]) -> bool:
    """A result that arrives AFTER a user correction is stale; naive applies it anyway."""
    last_user_ms = -1
    for t in turns:
        if t.get("user"):
            last_user_ms = max(last_user_ms, t.get("at_ms", 0))
    # a result that lands before the latest correction is stale; naive applies it anyway
    return any(t.get("tool_result") and t.get("at_ms", 0) < last_user_ms for t in turns)


def replay_naive(scenario_path: Path | str) -> dict[str, Any]:
    """Run the naive agent on a scenario and return its outcome + wall time."""
    data = json.loads(Path(scenario_path).read_text(encoding="utf-8"))
    name = data.get("name", Path(scenario_path).stem)
    turns = sorted(data.get("turns", []), key=lambda t: t.get("at_ms", 0))

    user_turns = [t for t in turns if t.get("user")]
    texts = [t["user"] for t in user_turns]

    redo_count = len(user_turns)  # every turn re-plans from scratch
    wall = redo_count * (2 * LLM_CALL_MS) + redo_count * sum(
        TOOL_LATENCY_MS.get(k, 500) for k in NAIVE_PLAN
    )

    # ---- correctness defects of the naive design -------------------------
    stale_applied = _tool_result_stale_after_change(turns)

    retracted = any(_RETRACT_RE.search(x) for x in texts)
    # naive has no retraction semantics: a late "don't book it" is just more
    # text in the prompt; the final re-plan still contains book → it books.
    retract_ignored = bool(retracted and any(_NEGATION_BOOK_RE.search(x) for x in texts))

    # timeout: no ledger, no verify — blind retry of the state-changing call
    double_booked = any(t.get("inject_timeout") for t in turns)

    # retract after commit: naive claims "cancelled!" without any cancel tool call
    committed = any(
        isinstance(t.get("tool_result"), dict)
        and isinstance(t["tool_result"].get("payload"), dict)
        and t["tool_result"]["payload"].get("status") == "COMMITTED"
        for t in turns
    )
    dishonest_after_commit = committed and retracted

    correct = not (stale_applied or retract_ignored or double_booked or dishonest_after_commit)

    return {
        "scenario": name,
        "naive_wall_ms": int(wall),
        "naive_llm_calls": 2 * redo_count,
        "naive_redo_dispatches": redo_count * len(NAIVE_PLAN),
        "stale_applied": stale_applied,
        "retract_ignored": retract_ignored,
        "double_booked": double_booked,
        "dishonest_after_commit": dishonest_after_commit,
        "correct": correct,
    }


def compare_with_baseline(scenario_path: Path | str, continuum: dict[str, Any]) -> dict[str, Any]:
    """One row of the plan's Task 5.1.B table: baseline vs CONTINUUM + correctness."""
    base = replay_naive(scenario_path)
    cont_wall = int(continuum["continuum_wall_ms"])
    base_wall = int(base["naive_wall_ms"])
    saved = round(100.0 * (1 - cont_wall / base_wall), 1) if base_wall else 0.0
    return {
        **base,
        "continuum_wall_ms": cont_wall,
        "saved_pct": max(saved, 0.0),
        "continuum_correct": continuum.get("stale_leaks", 0) == 0,
    }

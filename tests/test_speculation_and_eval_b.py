"""Generic shadows + the AI/ML B evaluation pipelines (smoke level)."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

from continuum.evaluation import planner_eval, runtime_eval
from continuum.runtime import AgentRuntime, EventType, RuntimeEvent

TODAY = date(2026, 9, 25)


async def _say(r: AgentRuntime, text: str) -> None:
    await r.handle(RuntimeEvent(session_id="s", type=EventType.TEXT, text=text))
    await r.handle(RuntimeEvent(session_id="s", type=EventType.EOT))


def test_prefetch_shadow_is_promoted_when_the_user_asks_for_it():
    async def go() -> AgentRuntime:
        r = AgentRuntime(tool_speed=0.02, today=TODAY, speculation=True)
        await _say(r, "Find flights to Goa")
        await asyncio.sleep(0.05)
        await _say(r, "also find a hotel there")
        await asyncio.sleep(0.05)
        return r

    r = asyncio.run(go())
    m = r.speculation_metrics()["s"]
    assert m["spawned"] >= 1 and m["promoted"] == 1
    hotel_calls = [t for t in r.trace if t["event"] == "dispatch" and t["tool"] == "search_hotels"]
    assert hotel_calls == []  # served by the shadow, not dispatched again


def test_shadow_is_discarded_when_its_premise_changes():
    async def go() -> AgentRuntime:
        r = AgentRuntime(tool_speed=0.02, today=TODAY, speculation=True)
        await _say(r, "Find flights to Goa")
        await _say(r, "Actually, Pune")
        await asyncio.sleep(0.05)
        await r.close()
        return r

    m = asyncio.run(go()).speculation_metrics()["s"]
    assert m["discarded"] >= 1 and m["promoted"] == 0


def test_shadows_never_run_state_changing_tools():
    async def go() -> AgentRuntime:
        r = AgentRuntime(tool_speed=0.02, today=TODAY, speculation=True)
        await _say(r, "Book a flight to Goa tomorrow")
        await asyncio.sleep(0.2)
        return r

    r = asyncio.run(go())
    spawned = [t["tool"] for t in r.trace if t["event"] == "shadow_spawn"]
    assert all(not r.session("s").registry.get(t).state_changing for t in spawned)


def test_planner_eval_reports_both_splits_and_the_frozen_hash():
    rep = planner_eval.evaluate(Path("data/gold/planner_gold.jsonl"))
    assert rep["gold_hash"] == "e92fa9a38623"
    assert set(rep["by_split"]) == {"dev", "heldout"}
    assert rep["overall"]["unsafe_write_plans"] == 0 and rep["overall"]["crashes"] == 0
    assert rep["by_split"]["dev"]["fully_correct"] >= 0.95
    assert rep["by_split"]["heldout"]["fully_correct"] >= 0.9


def test_baseline_is_measurably_worse():
    base = planner_eval.evaluate(Path("data/gold/planner_gold.jsonl"), system="baseline")
    assert base["overall"]["fully_correct"] < 0.3


def test_runtime_scenario_scoring_detects_a_missing_cancel():
    suite = {s["name"]: s for s in runtime_eval.load_suite()}
    sc = suite["flight_pivot"]
    good = runtime_eval.score_scenario(sc, asyncio.run(runtime_eval.run_scenario(sc)))
    bad = runtime_eval.score_scenario(sc, asyncio.run(runtime_eval.run_scenario(sc, planner="naive")))
    assert good["score"] == 100.0 and good["cancel_ms"]
    assert bad["score"] < good["score"] and bad["notes"]

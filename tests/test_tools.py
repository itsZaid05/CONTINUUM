"""Mock Tool Sandbox — realistic async delays, cancellation, idempotency, DAG concurrency."""

from __future__ import annotations

import asyncio
import time

import pytest

from continuum.tools import MockToolSandbox, execute_plan, spec_for


def test_registry_matches_pdf_risk_tiers():
    assert spec_for("search_flights").risk.value == "FREE"
    assert spec_for("hold_seat").risk.value == "STAGEABLE"
    assert spec_for("modify_booking").risk.value == "MUTATING"
    assert spec_for("confirm_booking").risk.value == "IRREVERSIBLE"


def test_registry_matches_pdf_delays():
    assert spec_for("search_flights").delay_s == 1.5
    assert spec_for("search_hotels").delay_s == 1.2
    assert spec_for("confirm_booking").delay_s == 2.0
    assert spec_for("search_cabs").delay_s == 0.8
    assert spec_for("check_calendar").delay_s == 0.5


async def test_free_tool_has_no_external_operation_id():
    sb = MockToolSandbox()
    r = await sb.call("search_flights", {"to": "Bangalore", "slot": "morning"}, speed=0.01)
    assert r.status == "COMPLETED"
    assert r.external_operation_id is None
    assert r.payload["to"] == "Bangalore"


async def test_mutating_tool_gets_external_operation_id():
    sb = MockToolSandbox()
    r = await sb.call("confirm_booking", {"hold_id": "HOLD-1"}, speed=0.01)
    assert r.external_operation_id is not None
    assert r.external_operation_id.startswith("ext_booking_")
    assert r.payload["status"] == "COMMITTED"


async def test_idempotent_replay_never_redispatches():
    sb = MockToolSandbox()
    key = "idemp_book_p3"
    r1 = await sb.call("confirm_booking", {"hold_id": "HOLD-1"}, idempotency_key=key, speed=0.01)
    r2 = await sb.call("confirm_booking", {"hold_id": "HOLD-1"}, idempotency_key=key, speed=0.01)
    assert r1.external_operation_id == r2.external_operation_id
    assert r1 is r2  # returned the cached record, not a fresh dispatch


async def test_cancellation_marks_mutating_call_abandoned():
    sb = MockToolSandbox()
    key = "idemp_hold_1"
    task = asyncio.ensure_future(
        sb.call("hold_seat", {"flight_id": "FL-1"}, idempotency_key=key, speed=1.0)
    )
    await asyncio.sleep(0)  # let it start awaiting the delay
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cached = sb.already_dispatched(key)
    assert cached is not None and cached.status == "ABANDONED"


async def test_cancellation_is_near_instant():
    sb = MockToolSandbox()
    task = asyncio.ensure_future(sb.call("confirm_booking", {"hold_id": "H"}, speed=1.0))
    await asyncio.sleep(0)
    t0 = time.perf_counter()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (time.perf_counter() - t0) < 0.05  # cancelled, not waited out the 2.0s*speed delay


async def test_execute_plan_respects_dependency_refs():
    steps = [
        _S("p_1", "search_flights", {"to": "Bangalore", "slot": "morning"}, []),
        _S("p_2", "hold_seat", {"flight_id": "ref(p_1.flight_id)"}, ["p_1"]),
        _S("p_3", "confirm_booking", {"hold_id": "ref(p_2.hold_id)"}, ["p_2"]),
    ]
    results = await execute_plan(steps, speed=0.01)
    assert results["p_2"].payload["flight_id"] == results["p_1"].payload["flight_id"]
    assert results["p_3"].payload["hold_id"] == results["p_2"].payload["hold_id"]
    assert results["p_3"].payload["status"] == "COMMITTED"


async def test_execute_plan_runs_independent_branches_concurrently():
    # primary chain (p_1 -> p_2) plus an independent shadow (sh_1) sharing
    # no dependency: total wall time should track the critical path, not
    # the sum of every step's delay.
    steps = [
        _S("p_1", "search_flights", {"to": "Bangalore"}, []),
        _S("p_2", "hold_seat", {"flight_id": "ref(p_1.flight_id)"}, ["p_1"]),
        _S("sh_1", "search_cabs", {"to": "Bangalore Airport"}, []),
    ]
    t0 = time.perf_counter()
    await execute_plan(steps, speed=0.05)  # scaled delays: 0.075s, 0.03s, 0.04s
    elapsed = time.perf_counter() - t0
    # sequential would be ~0.075+0.03+0.04=0.145s; concurrent waves: wave1 max(0.075,0.04)=0.075 + wave2 0.03 ~= 0.105s
    assert elapsed < 0.145


class _S:
    def __init__(self, step_id, tool, params, depends_on, idempotency_key=None):
        self.step_id = step_id
        self.tool = tool
        self.params = params
        self.depends_on = depends_on
        self.idempotency_key = idempotency_key

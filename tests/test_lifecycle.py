import asyncio

import pytest

from continuum.lifecycle import CallLifecycle, CallStatus, InvalidCallTransition
from continuum.planner import PlanStep
from continuum.runtime import AgentRuntime
from continuum.speculation import call_key
from continuum.tools import ToolManifest, ToolRegistry


def lifecycle(*, cancellable: bool = True) -> CallLifecycle:
    return CallLifecycle(
        operation_id="op-1",
        tool="update",
        args={"value": 1},
        base_version=3,
        cancellable=cancellable,
        effect_id="effect-1",
    )


def test_cancellable_operation_uses_explicit_cancel_requested_state() -> None:
    call = lifecycle()
    call.begin_attempt("call-1", 10)
    assert call.status == CallStatus.RUNNING
    assert call.request_cancel(20) == CallStatus.CANCEL_REQUESTED
    call.mark_cancelled(21)
    assert call.status == CallStatus.CANCELLED
    assert call.terminal
    assert call.transitions == [
        ("CREATED", "RUNNING", 10),
        ("RUNNING", "CANCEL_REQUESTED", 20),
        ("CANCEL_REQUESTED", "CANCELLED", 21),
    ]


def test_non_cancellable_operation_is_abandoned_not_fake_cancelled() -> None:
    call = lifecycle(cancellable=False)
    call.begin_attempt("call-1", 10)
    assert call.request_cancel(20) == CallStatus.ABANDONED
    assert call.status == CallStatus.ABANDONED
    with pytest.raises(InvalidCallTransition):
        call.mark_cancelled(30)


def test_unknown_operation_can_be_reconciled_by_postcondition() -> None:
    call = lifecycle(cancellable=False)
    call.begin_attempt("call-1", 10)
    call.finish_attempt(30, external_operation_id="external-7", error="timeout")
    call.mark_unknown(30, "timeout after acceptance")
    call.mark_completed(40, {"booking_ref": "B-7"}, external_operation_id="external-7")
    assert call.status == CallStatus.COMPLETED
    assert call.result == {"booking_ref": "B-7"}


def test_retries_have_fresh_call_ids_under_one_operation() -> None:
    call = lifecycle()
    first = call.begin_attempt("call-1", 10)
    call.finish_attempt(20, error="retryable read failure")
    second = call.begin_attempt("call-2", 25)
    call.mark_completed(40, {"ok": True})
    assert first.number == 1 and second.number == 2
    assert [row.call_id for row in call.attempts] == ["call-1", "call-2"]
    assert call.current_call_id == "call-2"


def test_illegal_terminal_transition_is_rejected() -> None:
    call = lifecycle()
    call.begin_attempt("call-1", 10)
    call.mark_completed(20, {"ok": True})
    with pytest.raises(InvalidCallTransition):
        call.mark_unknown(30)


def test_runtime_abandons_non_cancellable_work_and_rejects_late_result() -> None:
    async def check() -> None:
        manifest = ToolManifest(
            name="remote_read",
            description="read a remote source",
            cancellable=False,
            delay_s=0.02,
            arguments={"type": "object", "properties": {}},
        )
        runtime = AgentRuntime(registry=ToolRegistry([manifest]))
        session = runtime.session("s")
        step = PlanStep("read-1", manifest.name, manifest.kind, {})
        await runtime._dispatch(  # noqa: SLF001 - lifecycle integration contract
            "s", session, step, manifest, {}, call_key(manifest.name, {}), manifest.name, None
        )
        run = session.runs[step.step_id]
        await runtime._cancel_runs("s", session, [run])  # noqa: SLF001
        assert run.status == "abandoned"
        assert run.lifecycle is not None
        assert run.lifecycle.status == CallStatus.ABANDONED
        await asyncio.sleep(0.03)
        assert any(row["event"] == "stale_result_rejected" for row in runtime.trace)
        assert runtime.call_lifecycles("s")[0]["status"] == "ABANDONED"
        await runtime.close()

    asyncio.run(check())


def test_runtime_cancellable_work_records_cancel_requested_then_cancelled() -> None:
    async def check() -> None:
        manifest = ToolManifest(
            name="remote_read",
            description="read a remote source",
            cancellable=True,
            delay_s=0.1,
            arguments={"type": "object", "properties": {}},
        )
        runtime = AgentRuntime(registry=ToolRegistry([manifest]))
        session = runtime.session("s")
        step = PlanStep("read-1", manifest.name, manifest.kind, {})
        await runtime._dispatch(  # noqa: SLF001 - lifecycle integration contract
            "s", session, step, manifest, {}, call_key(manifest.name, {}), manifest.name, None
        )
        run = session.runs[step.step_id]
        await runtime._cancel_runs("s", session, [run])  # noqa: SLF001
        assert run.lifecycle is not None
        assert run.lifecycle.status == CallStatus.CANCELLED
        transitions = [(before, after) for before, after, _at in run.lifecycle.transitions]
        assert ("RUNNING", "CANCEL_REQUESTED") in transitions
        assert ("CANCEL_REQUESTED", "CANCELLED") in transitions
        await runtime.close()

    asyncio.run(check())

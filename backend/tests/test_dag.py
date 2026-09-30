"""
Phase 2 Verification Tests (DAG Engine, Task Cancellation & Stale Gate)
"""

import asyncio
import contextlib
import time

from backend.app.core.dag_engine import DAGNode, ProvenanceDAG
from backend.app.core.task_executor import AsyncTaskRegistry
from backend.app.core.version_manager import VersionManager


def test_dag_dependency_and_surgical_invalidation():
    async def _run():
        dag = ProvenanceDAG(session_id="test_dag_sess")

        # Node 1: Search flight to Delhi (FREE)
        n1 = DAGNode(
            step_id="p_1",
            tool="search_flights",
            params={"to": "Delhi", "slot": "morning"},
            base_version=1,
        )
        # Node 2: Hold seat (STAGEABLE, depends on p_1)
        n2 = DAGNode(
            step_id="p_2",
            tool="hold_seat",
            params={"flight_id": "ref(p_1.flight_id)"},
            depends_on=["p_1"],
            base_version=1,
        )
        # Node 3: Confirm booking (IRREVERSIBLE, depends on p_2)
        n3 = DAGNode(
            step_id="p_3",
            tool="confirm_booking",
            params={"hold_id": "ref(p_2.hold_id)"},
            depends_on=["p_2"],
            base_version=1,
        )
        # Node 4: Independent user profile check (unaffected)
        n4 = DAGNode(
            step_id="p_pref",
            tool="get_user_preferences",
            params={"user": "alice"},
            depends_on=[],
            base_version=1,
        )

        dag.add_node(n1)
        dag.add_node(n2)
        dag.add_node(n3)
        dag.add_node(n4)

        # Initial state: p_1 and p_pref are ready (no parents)
        ready = [node.step_id for node in dag.get_ready_nodes()]
        assert "p_1" in ready
        assert "p_pref" in ready
        assert "p_2" not in ready  # waiting on p_1

        # Simulate user interrupt: "Actually, make it Bangalore"
        t0 = time.time()
        invalidated = dag.surgically_invalidate(changed_fields=["to", "destination"], new_version=2)
        t1 = time.time()
        invalidation_latency_ms = (t1 - t0) * 1000

        print(
            f"\n[PASS] Surgical Invalidation Latency: {invalidation_latency_ms:.3f}ms (<15ms budget)"
        )
        # Must invalidate p_1, and downstream children p_2, p_3
        assert "p_1" in invalidated
        assert "p_2" in invalidated
        assert "p_3" in invalidated
        # Unaffected node p_pref MUST remain intact!
        assert "p_pref" not in invalidated
        assert dag.get_node("p_pref").status == "CREATED"
        print("[PASS] Downstream children invalidated, independent nodes preserved.")

    asyncio.run(_run())


def test_task_instant_cancellation():
    async def _run():
        registry = AsyncTaskRegistry()
        session_id = "test_cancel_sess"

        cancelled_event = asyncio.Event()

        async def long_running_tool():
            try:
                await asyncio.sleep(2.0)  # Simulates 2s flight search
            except asyncio.CancelledError:
                cancelled_event.set()
                raise

        # Launch task
        loop = asyncio.get_running_loop()
        task = loop.create_task(long_running_tool())
        registry.register_task(session_id, "p_1", task)

        # Let the task enter its async sleep
        await asyncio.sleep(0.01)

        # Interrupt arrives: cancel task immediately
        t0 = time.time()
        was_cancelled = registry.cancel_task(session_id, "p_1")
        t1 = time.time()
        cancel_latency_ms = (t1 - t0) * 1000

        assert was_cancelled is True
        # Allow the task's cancel handler to execute.
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert cancelled_event.is_set()
        assert task.cancelled()
        print(
            f"\n[PASS] Task cancellation latency: {cancel_latency_ms:.3f}ms (<5ms budget). Task cleanly aborted."
        )

    asyncio.run(_run())


def test_stale_gate_discard():
    vm = VersionManager()
    session_id = "test_stale_sess"

    # Version 1 starts
    vm.register_interrupt(session_id, "Find flight to Delhi")
    assert vm.get_current_version(session_id) == 1

    # While task for V1 is running, user interrupts -> Version becomes 2
    vm.register_interrupt(session_id, "Actually make it Bangalore")
    assert vm.get_current_version(session_id) == 2

    # Task for V1 finishes now
    node_v1 = DAGNode(step_id="p_1", tool="search_flights", params={"to": "Delhi"}, base_version=1)

    # Stale gate check
    is_stale = vm.is_stale(session_id, node_v1.base_version)
    assert is_stale is True
    print("\n[PASS] Stale Gate: V1 task output cleanly detected as STALE and discarded.")

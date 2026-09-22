"""
CONTINUUM Async Task Registry & Tool Executor
Manages running asyncio tasks, instantaneous task.cancel() on invalidation, and Stale Gate enforcement.
"""
import asyncio
import time
from typing import Dict, Optional, Callable, Any
from backend.app.core.dag_engine import DAGNode, ProvenanceDAG
from backend.app.core.version_manager import version_manager
from backend.app.models.schemas import DAGNodeUpdateEvent, NodeStatus

class AsyncTaskRegistry:
    def __init__(self):
        # Maps (session_id, step_id) -> asyncio.Task
        self._tasks: Dict[str, asyncio.Task] = {}
        # Status callback for WebSocket broadcasting
        self.broadcast_callback: Optional[Callable[[str, dict], Any]] = None

    def _make_key(self, session_id: str, step_id: str) -> str:
        return f"{session_id}::{step_id}"

    def register_task(self, session_id: str, step_id: str, task: asyncio.Task):
        key = self._make_key(session_id, step_id)
        self._tasks[key] = task

    def unregister_task(self, session_id: str, step_id: str):
        key = self._make_key(session_id, step_id)
        if key in self._tasks:
            del self._tasks[key]

    def cancel_task(self, session_id: str, step_id: str) -> bool:
        """
        Instantly cancels the active local asyncio task.
        Target budget: <5ms
        """
        key = self._make_key(session_id, step_id)
        task = self._tasks.get(key)
        if task and not task.done():
            task.cancel()
            del self._tasks[key]
            return True
        return False

    def cancel_all_session_tasks(self, session_id: str) -> int:
        cancelled_count = 0
        keys_to_remove = []
        for key, task in self._tasks.items():
            if key.startswith(f"{session_id}::"):
                if not task.done():
                    task.cancel()
                    cancelled_count += 1
                keys_to_remove.append(key)
        for key in keys_to_remove:
            del self._tasks[key]
        return cancelled_count

    async def execute_node(
        self,
        dag: ProvenanceDAG,
        node: DAGNode,
        tool_callable: Callable[[Dict[str, Any]], Any]
    ):
        """
        Executes a single DAG node with Stale Gate checks and cancellation handling.
        """
        session_id = dag.session_id
        step_id = node.step_id

        # 1. Check Stale Gate before starting
        if version_manager.is_stale(session_id, node.base_version):
            node.status = "ABANDONED"
            await self._notify_update(session_id, node)
            return

        # 2. Mark node as RUNNING
        node.status = "RUNNING"
        node.started_at_ms = time.time() * 1000
        await self._notify_update(session_id, node)

        try:
            # 3. Execute tool function
            result = await tool_callable(node.params)

            # 4. Check Stale Gate after completion
            # Strict Invariant: if base_version != current_version: DISCARD
            if version_manager.is_stale(session_id, node.base_version):
                node.status = "ABANDONED"
                node.output = None
                await self._notify_update(session_id, node)
                return

            # 5. Success! Mark COMPLETED
            node.status = "COMPLETED"
            node.output = result
            node.completed_at_ms = time.time() * 1000
            await self._notify_update(session_id, node)

        except asyncio.CancelledError:
            # Task was cancelled mid-flight
            node.status = "CANCELLED"
            node.completed_at_ms = time.time() * 1000
            await self._notify_update(session_id, node)
            raise  # Re-raise to clean up asyncio task

        except Exception as ex:
            node.status = "INVALIDATED"
            node.error = str(ex)
            node.completed_at_ms = time.time() * 1000
            await self._notify_update(session_id, node)

        finally:
            self.unregister_task(session_id, step_id)

    async def _notify_update(self, session_id: str, node: DAGNode):
        if self.broadcast_callback:
            duration = None
            if node.started_at_ms and node.completed_at_ms:
                duration = node.completed_at_ms - node.started_at_ms

            event = DAGNodeUpdateEvent(
                session_id=session_id,
                version=node.base_version,
                step_id=node.step_id,
                tool=node.tool,
                status=node.status,
                risk=node.risk,
                params=node.params,
                output=node.output,
                error=node.error,
                duration_ms=duration
            )
            await self.broadcast_callback(session_id, event.model_dump())

task_registry = AsyncTaskRegistry()

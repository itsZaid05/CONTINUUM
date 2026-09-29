"""Lifecycle-safe bridge between LiveKit function calls and FDB mock APIs."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any

from ...ledger import effect_id_for
from ...lifecycle import CallLifecycle, CallStatus
from ...tools import ToolManifest
from .backend import FdbBackend
from .manifests import fdb_registry
from .telemetry import JsonlTelemetry


class BridgeExecutionError(RuntimeError):
    """A call failed validation, execution, or effect reconciliation."""


@dataclass
class _ActiveCall:
    lifecycle: CallLifecycle
    task: asyncio.Task[Any]


def verify_postcondition(
    manifest: ToolManifest, args: dict[str, Any], result: dict[str, Any]
) -> None:
    """Verify the concrete postconditions declared by FDB mutation manifests."""
    if not manifest.state_changing:
        return
    valid = result.get("status") == "success"
    if manifest.name == "book_flight":
        valid = (
            valid
            and bool(result.get("booking_ref"))
            and (result.get("passenger") == args.get("passenger_name"))
        )
    elif manifest.name == "update_identity_doc":
        valid = (
            valid
            and result.get("updated_doc") == args.get("doc_type")
            and result.get("masked_number") == str(args.get("doc_number", ""))[-4:]
        )
    elif manifest.name == "modify_autopay":
        valid = (
            valid
            and result.get("autopay_enabled") is True
            and result.get("bill") == args.get("bill_type")
            and result.get("source") == args.get("source_account")
        )
    elif manifest.name == "update_search_filter":
        valid = (
            valid
            and result.get("filter_updated") == args.get("filter_name")
            and result.get("new_value") == args.get("value")
        )
    elif manifest.name == "add_to_cart":
        valid = (
            valid
            and result.get("product_id") == args.get("product_id")
            and result.get("quantity") == args.get("quantity")
        )
    if not valid:
        raise BridgeExecutionError(
            f"postcondition verification failed for {manifest.name}: {manifest.postcondition}"
        )


class FdbToolBridge:
    """Validate, execute, reconcile, deduplicate and audit all FDB tool calls."""

    def __init__(
        self,
        backend: FdbBackend,
        telemetry: JsonlTelemetry,
        *,
        room_name: str,
        max_read_retries: int = 2,
        retry_backoff_s: float = 0.05,
    ) -> None:
        self.backend = backend
        self.telemetry = telemetry
        self.room_name = room_name
        self.registry = fdb_registry()
        self.max_read_retries = max_read_retries
        self.retry_backoff_s = retry_backoff_s
        self.history: list[CallLifecycle] = []
        self._active: dict[str, _ActiveCall] = {}
        self._completed_effects: dict[str, dict[str, Any]] = {}
        self._active_lock = asyncio.Lock()
        self._effect_locks: dict[str, asyncio.Lock] = {}
        self._closing = False
        self._t0 = time.perf_counter()
        self.last_tool: str = ""
        self.last_tool_started_at: float | None = None
        self.last_tool_finished_at: float | None = None
        self._turn_number = 0
        self._candidate_turn_active = False
        self._committed_turn = 0

    def now_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000.0

    def begin_candidate_turn(self) -> int:
        """Close dispatch while the next user utterance is still provisional."""
        if not self._candidate_turn_active:
            self._turn_number += 1
            self._candidate_turn_active = True
            self.telemetry.emit("turn_candidate_started", turn=self._turn_number)
        return self._turn_number

    def commit_turn(self, boundary: str) -> int:
        """Open dispatch after a final transcript or explicit EOT boundary."""
        if self._turn_number == 0:
            self._turn_number = 1
        self._candidate_turn_active = False
        self._committed_turn = self._turn_number
        self.telemetry.emit("turn_committed", turn=self._turn_number, boundary=boundary)
        return self._turn_number

    @property
    def dispatch_allowed(self) -> bool:
        return (
            self._committed_turn == self._turn_number
            and self._committed_turn > 0
            and not self._candidate_turn_active
        )

    async def execute(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        call_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute one model-requested function call under an auditable lifecycle."""
        if self._closing:
            raise BridgeExecutionError("FDB tool bridge is closing")
        if not self.dispatch_allowed:
            self.telemetry.emit(
                "tool_dispatch_blocked",
                tool=tool,
                reason="user turn is candidate-only; final transcript or EOT required",
            )
            raise BridgeExecutionError(
                "tool dispatch blocked until the user turn has a final transcript or EOT"
            )
        try:
            manifest = self.registry.get(tool)
        except KeyError as exc:
            raise BridgeExecutionError(f"unknown FDB tool {tool!r}") from exc
        try:
            clean_args = manifest.validate_args(args)
        except ValueError as exc:
            self.telemetry.emit("tool_args_rejected", tool=tool, error=str(exc))
            raise BridgeExecutionError(str(exc)) from exc

        effect_key = (
            effect_id_for(0, f"{self.room_name}:{tool}", clean_args)
            if manifest.state_changing
            else None
        )
        lifecycle = CallLifecycle(
            operation_id=uuid.uuid4().hex,
            tool=tool,
            args=clean_args,
            base_version=0,
            cancellable=manifest.cancellable,
            effect_id=effect_key,
        )
        first_call_id = call_id or uuid.uuid4().hex
        lifecycle.begin_attempt(first_call_id, self.now_ms())
        self.history.append(lifecycle)
        task = asyncio.current_task()
        if task is None:  # pragma: no cover - asyncio always supplies one here
            raise BridgeExecutionError("tool bridge execute requires an asyncio task")
        async with self._active_lock:
            self._active[first_call_id] = _ActiveCall(lifecycle, task)

        started_at = time.time()
        self.last_tool = tool
        self.last_tool_started_at = started_at
        self.last_tool_finished_at = None
        self.telemetry.emit(
            "tool_started",
            tool=tool,
            args=clean_args,
            call_id=first_call_id,
            operation_id=lifecycle.operation_id,
            effect_id=effect_key,
        )
        try:
            if effect_key is not None:
                lock = self._effect_locks.setdefault(effect_key, asyncio.Lock())
                async with lock:
                    cached = self._completed_effects.get(effect_key)
                    if cached is not None:
                        result = dict(cached)
                        lifecycle.mark_completed(
                            self.now_ms(), result, detail="deduplicated committed effect"
                        )
                        self.telemetry.emit(
                            "tool_deduplicated",
                            tool=tool,
                            call_id=first_call_id,
                            effect_id=effect_key,
                        )
                    else:
                        result = await self._execute_attempts(lifecycle, manifest, clean_args)
                        self._completed_effects[effect_key] = dict(result)
            else:
                result = await self._execute_attempts(lifecycle, manifest, clean_args)

            finished_at = time.time()
            self.last_tool_finished_at = finished_at
            self.telemetry.official_tool_call(
                tool=tool,
                args=clean_args,
                started_at=started_at,
                finished_at=finished_at,
            )
            self.telemetry.emit(
                "tool_completed",
                tool=tool,
                call_id=first_call_id,
                operation_id=lifecycle.operation_id,
                effect_id=effect_key,
                lifecycle=lifecycle.as_trace(),
            )
            return result
        except asyncio.CancelledError:
            self._mark_cancelled(lifecycle, "tool task cancelled")
            self.telemetry.emit(
                "tool_cancelled",
                tool=tool,
                call_id=first_call_id,
                lifecycle=lifecycle.as_trace(),
            )
            raise
        except Exception as exc:
            if lifecycle.attempts and lifecycle.attempts[-1].finished_ms is None:
                lifecycle.finish_attempt(self.now_ms(), error=str(exc))
            if lifecycle.status in {CallStatus.RUNNING, CallStatus.CANCEL_REQUESTED}:
                lifecycle.mark_unknown(self.now_ms(), str(exc))
            self.telemetry.emit(
                "tool_failed",
                tool=tool,
                call_id=first_call_id,
                error=str(exc),
                lifecycle=lifecycle.as_trace(),
            )
            if isinstance(exc, BridgeExecutionError):
                raise
            raise BridgeExecutionError(f"{tool} failed: {exc}") from exc
        finally:
            async with self._active_lock:
                for active_id, active in list(self._active.items()):
                    if active.lifecycle is lifecycle:
                        self._active.pop(active_id, None)

    async def _execute_attempts(
        self,
        lifecycle: CallLifecycle,
        manifest: ToolManifest,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        retries = 0
        while True:
            try:
                result = await self.backend.call(
                    manifest.name,
                    args,
                    idempotency_key=lifecycle.effect_id,
                )
                result = manifest.validate_result(result)
                verify_postcondition(manifest, args, result)
                lifecycle.mark_completed(self.now_ms(), result)
                return result
            except asyncio.CancelledError:
                raise
            except (TimeoutError, asyncio.TimeoutError) as exc:
                lifecycle.finish_attempt(self.now_ms(), error=str(exc))
                if manifest.state_changing:
                    lifecycle.mark_unknown(self.now_ms(), "timeout; checking postcondition")
                    assert lifecycle.effect_id is not None
                    verified = await self.backend.verify(lifecycle.effect_id)
                    if verified is not None:
                        verified = manifest.validate_result(verified)
                        verify_postcondition(manifest, args, verified)
                        lifecycle.mark_completed(
                            self.now_ms(), verified, detail="verified after timeout"
                        )
                        return verified
                    raise BridgeExecutionError(
                        f"{manifest.name} timed out and its effect remains unknown; not retried"
                    ) from exc
                if retries >= self.max_read_retries:
                    raise BridgeExecutionError(
                        f"{manifest.name} timed out after {retries + 1} attempts"
                    ) from exc
                retries += 1
                await asyncio.sleep(self.retry_backoff_s * (2 ** (retries - 1)))
                retry_id = f"{lifecycle.operation_id}-retry-{retries}"
                lifecycle.begin_attempt(retry_id, self.now_ms())
                async with self._active_lock:
                    current = self._active.pop(lifecycle.attempts[0].call_id, None)
                    if current is not None:
                        self._active[retry_id] = current

    def _mark_cancelled(self, lifecycle: CallLifecycle, detail: str) -> None:
        now = self.now_ms()
        if lifecycle.attempts and lifecycle.attempts[-1].finished_ms is None:
            lifecycle.finish_attempt(now, error=detail)
        if lifecycle.status == CallStatus.RUNNING:
            if lifecycle.cancellable:
                lifecycle.request_cancel(now, detail)
                lifecycle.mark_cancelled(now, detail)
            else:
                lifecycle.mark_abandoned(now, detail)
        elif lifecycle.status == CallStatus.CANCEL_REQUESTED:
            lifecycle.mark_cancelled(now, detail)

    async def cancel_cancellable(self, reason: str = "user barge-in") -> int:
        """Cancel only operations whose manifests declare cancellation safe."""
        async with self._active_lock:
            active = list(self._active.values())
        count = 0
        seen: set[int] = set()
        for row in active:
            identity = id(row.lifecycle)
            if identity in seen or not row.lifecycle.cancellable or row.task.done():
                continue
            seen.add(identity)
            if row.lifecycle.status == CallStatus.RUNNING:
                row.lifecycle.request_cancel(self.now_ms(), reason)
            row.task.cancel()
            count += 1
        if count:
            self.telemetry.emit("cancellable_tools_cancel_requested", count=count, reason=reason)
        return count

    async def close(self, *, timeout_s: float = 5.0) -> None:
        """Stop reads and drain accepted mutations before worker shutdown."""
        if self._closing:
            return
        self._closing = True
        await self.cancel_cancellable("worker shutdown")
        current = asyncio.current_task()
        async with self._active_lock:
            tasks = {
                row.task
                for row in self._active.values()
                if row.task is not current and not row.task.done()
            }
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=max(0.0, timeout_s))
            for task in pending:
                for row in self._active.values():
                    if row.task is task and not row.lifecycle.terminal:
                        row.lifecycle.mark_abandoned(
                            self.now_ms(), "shutdown drain timeout; completion not claimed"
                        )
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self.telemetry.emit(
            "bridge_closed",
            operations=len(self.history),
            terminal=sum(row.terminal for row in self.history),
        )

"""Explicit lifecycle for logical tool operations and their dispatch attempts.

A logical operation keeps one stable ``operation_id``/effect identity while
individual retries get fresh ``call_id`` values.  The lifecycle deliberately
uses the states required by the competition blueprint; transport errors and
verification outcomes are recorded as details rather than inventing ad-hoc
status strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._compat import StrEnum


class CallStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    ABANDONED = "ABANDONED"
    COMPLETED = "COMPLETED"
    UNKNOWN = "UNKNOWN"


TERMINAL_CALL_STATUSES = {
    CallStatus.CANCELLED,
    CallStatus.ABANDONED,
    CallStatus.COMPLETED,
    # UNKNOWN is terminal for dispatch/retry decisions. It may only be
    # reconciled later by an explicit postcondition check.
    CallStatus.UNKNOWN,
}

_ALLOWED: dict[CallStatus, set[CallStatus]] = {
    CallStatus.CREATED: {CallStatus.RUNNING, CallStatus.CANCELLED},
    CallStatus.RUNNING: {
        CallStatus.CANCEL_REQUESTED,
        CallStatus.ABANDONED,
        CallStatus.COMPLETED,
        CallStatus.UNKNOWN,
        CallStatus.CANCELLED,
    },
    CallStatus.CANCEL_REQUESTED: {
        CallStatus.CANCELLED,
        CallStatus.ABANDONED,
        CallStatus.COMPLETED,
        CallStatus.UNKNOWN,
    },
    # UNKNOWN is unresolved rather than irrecoverable.  A postcondition check
    # can prove that it landed or prove that it did not.
    CallStatus.UNKNOWN: {CallStatus.COMPLETED, CallStatus.CANCELLED},
    CallStatus.CANCELLED: set(),
    CallStatus.ABANDONED: set(),
    CallStatus.COMPLETED: set(),
}


class InvalidCallTransition(ValueError):
    """Raised when code attempts an illegal lifecycle transition."""


@dataclass
class CallAttempt:
    number: int
    call_id: str
    started_ms: float
    finished_ms: float | None = None
    external_operation_id: str | None = None
    error: str | None = None


@dataclass
class CallLifecycle:
    """Auditable state machine for one logical tool operation."""

    operation_id: str
    tool: str
    args: dict[str, Any]
    base_version: int
    cancellable: bool
    effect_id: str | None = None
    status: CallStatus = CallStatus.CREATED
    attempts: list[CallAttempt] = field(default_factory=list)
    result: dict[str, Any] | None = None
    detail: str | None = None
    transitions: list[tuple[str, str, float]] = field(default_factory=list)

    @property
    def current_call_id(self) -> str | None:
        return self.attempts[-1].call_id if self.attempts else None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_CALL_STATUSES

    def _transition(self, target: CallStatus, at_ms: float, detail: str | None = None) -> None:
        if target == self.status:
            return
        if target not in _ALLOWED[self.status]:
            raise InvalidCallTransition(
                f"illegal call transition {self.status.value} -> {target.value}"
            )
        previous = self.status
        self.status = target
        self.detail = detail
        self.transitions.append((previous.value, target.value, at_ms))

    def begin_attempt(self, call_id: str, at_ms: float) -> CallAttempt:
        if self.status == CallStatus.CREATED:
            self._transition(CallStatus.RUNNING, at_ms)
        elif self.status != CallStatus.RUNNING:
            raise InvalidCallTransition(f"cannot dispatch attempt while {self.status.value}")
        if any(row.call_id == call_id for row in self.attempts):
            raise ValueError(f"duplicate call_id for operation {self.operation_id}: {call_id}")
        attempt = CallAttempt(len(self.attempts) + 1, call_id, at_ms)
        self.attempts.append(attempt)
        return attempt

    def finish_attempt(
        self,
        at_ms: float,
        *,
        external_operation_id: str | None = None,
        error: str | None = None,
    ) -> None:
        if not self.attempts:
            raise InvalidCallTransition("cannot finish an operation with no attempt")
        attempt = self.attempts[-1]
        attempt.finished_ms = at_ms
        attempt.external_operation_id = external_operation_id or attempt.external_operation_id
        attempt.error = error

    def request_cancel(self, at_ms: float, detail: str | None = None) -> CallStatus:
        if self.status != CallStatus.RUNNING:
            raise InvalidCallTransition(f"cannot request cancellation while {self.status.value}")
        if self.cancellable:
            self._transition(CallStatus.CANCEL_REQUESTED, at_ms, detail)
        else:
            self._transition(CallStatus.ABANDONED, at_ms, detail or "tool is not cancellable")
        return self.status

    def mark_cancelled(self, at_ms: float, detail: str | None = None) -> None:
        self._transition(CallStatus.CANCELLED, at_ms, detail)

    def mark_abandoned(self, at_ms: float, detail: str | None = None) -> None:
        self._transition(CallStatus.ABANDONED, at_ms, detail)

    def mark_unknown(self, at_ms: float, detail: str | None = None) -> None:
        self._transition(CallStatus.UNKNOWN, at_ms, detail)

    def mark_completed(
        self,
        at_ms: float,
        result: dict[str, Any],
        *,
        external_operation_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        if self.attempts and self.attempts[-1].finished_ms is None:
            self.finish_attempt(at_ms, external_operation_id=external_operation_id)
        self.result = dict(result)
        self._transition(CallStatus.COMPLETED, at_ms, detail)

    def as_trace(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "tool": self.tool,
            "args": dict(self.args),
            "base_version": self.base_version,
            "cancellable": self.cancellable,
            "effect_id": self.effect_id,
            "status": self.status.value,
            "attempts": [
                {
                    "number": row.number,
                    "call_id": row.call_id,
                    "started_ms": row.started_ms,
                    "finished_ms": row.finished_ms,
                    "external_operation_id": row.external_operation_id,
                    "error": row.error,
                }
                for row in self.attempts
            ],
            "detail": self.detail,
            "transitions": list(self.transitions),
        }

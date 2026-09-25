"""
Mock Tool Sandbox — async external-tool simulators (AI/ML Engineer B).

Every tool is a real ``async def`` coroutine that awaits ``asyncio.sleep``
for a realistic delay before returning — never an instant stub — so that
``task.cancel()`` (owned by the backend's Async Task Executor, PRD stage 7)
actually interrupts in-flight work instead of racing a no-op. asyncio
delivers ``CancelledError`` at the next await checkpoint, so cancellation
latency here is bounded by the event loop, not by the tool's own delay.

Per the frozen spec (PDF §3.C): flights 1.5s · hotels 1.2s · booking 2.0s
(confirm) · cab 0.8s · calendar 0.5s. ``hold_seat``/``modify_booking``
delays are this module's own reasonable fill for the two PDF tool-registry
entries (§2.B) the timing table doesn't itemise (a stage/hold is a quick
draft; a modify is a full round trip cheaper than a fresh booking).

Risk tiers are NOT decided here — ``kind`` is looked up against the single
authoritative table in ``policy.risk_for`` (owned by the Backend Engineer),
per the PRD invariant "Backend registry strictly overrides planner risk".
This module only supplies realistic latency + payload shape + the
``external_operation_id`` a real external system would hand back.

ID Generation Lifecycle (PDF §2.B): the moment a STAGEABLE+ call is
*accepted* (before its delay even starts) we mint ``external_operation_id``
— mirroring a real payment/booking API that ACKs a request id instantly and
settles later. Callers pass their own ``idempotency_key`` (e.g.
``ledger.effect_id_for``); a call is replay-safe — dispatched at most once
per key, matching the Effect Ledger's "never re-dispatch a COMMITTED effect"
rule from the timeout matrix.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .contracts import RiskLevel
from .policy import risk_for

# ---------------------------------------------------------------------------
# Tool registry — name -> (kind, realistic delay, domain)
# ---------------------------------------------------------------------------


class ToolManifest(BaseModel):
    """Runtime supplied tool contract; schemas are JSON Schema objects."""

    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    mutation_class: str = "READ_ONLY"  # READ_ONLY | MUTATING | IRREVERSIBLE
    cancellable: bool = True
    idempotent: bool = True
    authorization: str | None = None
    postcondition: str | None = None
    kind: str = "search"
    delay_s: float = 0.01
    domain: str = "generic"

    @property
    def state_changing(self) -> bool:
        return self.mutation_class != "READ_ONLY"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str  # generic kind fed to policy.risk_for() — the authoritative tier
    delay_s: float  # realistic simulated latency (PDF §3.C)
    domain: str  # "flights" | "hotels" | "cabs" | "calendar" | "booking"

    @property
    def risk(self) -> RiskLevel:
        return risk_for(self.kind)

    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            kind=self.kind,
            delay_s=self.delay_s,
            domain=self.domain,
            mutation_class="READ_ONLY" if self.risk == RiskLevel.FREE else self.risk.value,
            cancellable=True,
            idempotent=True,
        )


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "search_flights": ToolSpec("search_flights", "search", 1.5, "flights"),
    "search_hotels": ToolSpec("search_hotels", "search", 1.2, "hotels"),
    "search_cabs": ToolSpec("search_cabs", "search", 0.8, "cabs"),
    "check_calendar": ToolSpec("check_calendar", "inform", 0.5, "calendar"),
    "hold_seat": ToolSpec("hold_seat", "hold", 0.6, "booking"),
    "modify_booking": ToolSpec("modify_booking", "cancel", 1.0, "booking"),
    "confirm_booking": ToolSpec("confirm_booking", "book", 2.0, "booking"),
}


class ToolRegistry:
    """Manifest registry supporting tools unknown at build time."""

    def __init__(self, manifests: list[ToolManifest] | None = None) -> None:
        self._manifests: dict[str, ToolManifest] = {}
        for manifest in manifests or []:
            self.register(manifest)

    def register(self, manifest: ToolManifest) -> None:
        self._manifests[manifest.name] = manifest

    def get(self, name: str) -> ToolManifest:
        return self._manifests[name]

    def all(self) -> list[ToolManifest]:
        return list(self._manifests.values())

    def choose_read_tool(self, state: dict[str, Any]) -> ToolManifest | None:
        """Generic planner primitive: select a declared read-only tool, not a domain table."""
        return next((m for m in self._manifests.values() if not m.state_changing), None)

    def bind_args(self, name: str, state: dict[str, Any]) -> dict[str, Any]:
        manifest = self.get(name)
        required = manifest.arguments.get("required", [])
        properties = manifest.arguments.get("properties", {})
        args = {key: state[key] for key in properties if key in state}
        missing = [key for key in required if key not in args]
        if missing:
            raise ValueError(f"missing required arguments for {name}: {missing}")
        return args


def default_registry() -> ToolRegistry:
    return ToolRegistry([spec.manifest() for spec in TOOL_REGISTRY.values()])


def spec_for(tool: str) -> ToolSpec:
    try:
        return TOOL_REGISTRY[tool]
    except KeyError as exc:
        raise KeyError(f"unknown mock tool {tool!r}") from exc


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class ToolResult:
    tool: str
    external_operation_id: str | None
    accepted_at_ms: float
    completed_at_ms: float
    status: str  # "COMPLETED" | "ABANDONED"
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def latency_ms(self) -> float:
        return round(self.completed_at_ms - self.accepted_at_ms, 3)


def _synthesize_payload(spec: ToolSpec, params: dict[str, Any]) -> dict[str, Any]:
    """Deterministic fake payload per domain — same params -> same result."""
    salt = str(sorted(params.items()))
    tag = uuid.uuid5(uuid.NAMESPACE_URL, f"{spec.name}:{salt}").hex[:6]
    if spec.domain == "flights":
        return {"flight_id": f"FL-{tag}", "to": params.get("to"), "slot": params.get("slot")}
    if spec.domain == "hotels":
        return {"hotel_id": f"HT-{tag}", "city": params.get("to") or params.get("city")}
    if spec.domain == "cabs":
        return {"cab_eta_min": 8, "route": params.get("to")}
    if spec.domain == "calendar":
        return {"free": True, "slot": params.get("slot", "morning")}
    if spec.name == "hold_seat":
        return {"hold_id": f"HOLD-{tag}", "flight_id": params.get("flight_id")}
    if spec.name == "modify_booking":
        return {"modified": True, "hold_id": params.get("hold_id")}
    if spec.name == "confirm_booking":
        return {
            "ref": f"BLR-{tag.upper()}",
            "hold_id": params.get("hold_id"),
            "status": "COMMITTED",
        }
    return {}


# ---------------------------------------------------------------------------
# Sandbox — dispatch + cancellation-safe dedup
# ---------------------------------------------------------------------------


class MockToolSandbox:
    """Owns idempotency dedup so a retried call never re-dispatches externally."""

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self._dedup: dict[str, ToolResult] = {}
        self.registry = registry or default_registry()

    def already_dispatched(self, idempotency_key: str) -> ToolResult | None:
        return self._dedup.get(idempotency_key)

    async def call(
        self,
        tool: str,
        params: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        speed: float = 1.0,
    ) -> ToolResult:
        """Dispatch one tool call. Raises ``asyncio.CancelledError`` if the
        caller's task is cancelled mid-flight — propagated, never swallowed,
        so the backend's task registry sees a real abort.
        """
        try:
            spec = spec_for(tool)
        except KeyError:
            manifest = self.registry.get(tool)
            spec = ToolSpec(manifest.name, manifest.kind, manifest.delay_s, manifest.domain)
        params = params or {}

        if idempotency_key is not None:
            cached = self._dedup.get(idempotency_key)
            if cached is not None:
                return cached  # replay-safe: never re-dispatch a known effect

        t0 = time.perf_counter() * 1000.0
        external_operation_id = (
            f"ext_{spec.domain}_{uuid.uuid4().hex[:10]}"
            if spec.risk in {RiskLevel.STAGEABLE, RiskLevel.MUTATING, RiskLevel.IRREVERSIBLE}
            else None
        )
        try:
            await asyncio.sleep(spec.delay_s * speed)
        except asyncio.CancelledError:
            if idempotency_key is not None and external_operation_id is not None:
                # non-cancellable once externally accepted: mark ABANDONED,
                # not dropped — mirrors PRD "in-flight external marked ABANDONED"
                self._dedup[idempotency_key] = ToolResult(
                    tool=tool,
                    external_operation_id=external_operation_id,
                    accepted_at_ms=t0,
                    completed_at_ms=time.perf_counter() * 1000.0,
                    status="ABANDONED",
                )
            raise

        result = ToolResult(
            tool=tool,
            external_operation_id=external_operation_id,
            accepted_at_ms=t0,
            completed_at_ms=time.perf_counter() * 1000.0,
            status="COMPLETED",
            payload=_synthesize_payload(spec, params),
        )
        if idempotency_key is not None:
            self._dedup[idempotency_key] = result
        return result


# ---------------------------------------------------------------------------
# DAG execution — maximum concurrency, not step-by-step
# ---------------------------------------------------------------------------

_REF_RE = re.compile(r"^ref\(([^.]+)\.([^)]+)\)$")


class _Step(Protocol):
    """Structural type — matches `planner.PlanStep` without importing it,
    so this sandbox stays usable standalone (no coupling either direction).
    """

    step_id: str
    tool: str
    params: dict[str, Any]
    depends_on: list[str]
    idempotency_key: str | None


def _resolve_refs(params: dict[str, Any], results: dict[str, ToolResult]) -> dict[str, Any]:
    """Substitute `ref(step_id.field)` placeholders with upstream results."""
    out: dict[str, Any] = {}
    for k, v in params.items():
        m = _REF_RE.match(v) if isinstance(v, str) else None
        if m:
            upstream = results.get(m.group(1))
            out[k] = upstream.payload.get(m.group(2)) if upstream else None
        else:
            out[k] = v
    return out


async def execute_plan(
    steps: list[_Step],
    sandbox: MockToolSandbox | None = None,
    *,
    speed: float = 1.0,
) -> dict[str, ToolResult]:
    """Run a step DAG at maximum concurrency: steps whose dependencies are
    already satisfied are dispatched together in one `asyncio.gather` wave,
    so total wall time tracks the DAG's *critical path*, not its step count
    — an independent shadow (e.g. `search_cabs`) never adds a millisecond
    to the primary `search_flights -> hold_seat -> confirm_booking` chain.
    `ref(step_id.field)` params are resolved from prior-wave results.
    """
    sandbox = sandbox or MockToolSandbox()
    results: dict[str, ToolResult] = {}
    remaining = {s.step_id: s for s in steps}

    async def _run(step: _Step) -> tuple[str, ToolResult]:
        params = _resolve_refs(step.params, results)
        r = await sandbox.call(step.tool, params, idempotency_key=step.idempotency_key, speed=speed)
        return step.step_id, r

    while remaining:
        ready = [s for s in remaining.values() if all(d in results for d in s.depends_on)]
        if not ready:
            raise ValueError(f"unresolvable dependency cycle among {list(remaining)}")
        for step_id, result in await asyncio.gather(*(_run(s) for s in ready)):
            results[step_id] = result
            del remaining[step_id]
    return results

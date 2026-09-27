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
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, Field, field_validator, model_validator

from .contracts import RiskLevel
from .policy import risk_for

# ---------------------------------------------------------------------------
# Tool registry — name -> (kind, realistic delay, domain)
# ---------------------------------------------------------------------------

MUTATION_CLASSES = ("READ_ONLY", "STAGEABLE", "MUTATING", "IRREVERSIBLE")

# Manifests from other producers spell the read/write split differently; all
# of them collapse onto the four tiers the CommitGate understands.
_MUTATION_ALIASES = {
    "READ": "READ_ONLY",
    "READONLY": "READ_ONLY",
    "FREE": "READ_ONLY",
    "SAFE": "READ_ONLY",
    "STAGE": "STAGEABLE",
    "WRITE": "MUTATING",
    "STATE_MODIFYING": "MUTATING",
    "STATE_CHANGING": "MUTATING",
    "SIDE_EFFECT": "MUTATING",
}

# The kind fed to ``policy.risk_for`` for each tier, so a manifest-only tool
# lands on the same risk row as its built-in equivalent.
_KIND_FOR_CLASS = {
    "READ_ONLY": "search",
    "STAGEABLE": "hold",
    "MUTATING": "cancel",
    "IRREVERSIBLE": "book",
}


class ToolManifest(BaseModel):
    """Runtime supplied tool contract; schemas are JSON Schema objects.

    ``arguments`` is the JSON Schema of the call; ``returns`` lists the result
    fields (``{"properties": {...}}``) so the planner can chain a producer's
    output into a consumer's required argument. ``description`` and
    ``keywords`` feed tool ranking.
    """

    name: str = Field(min_length=1)
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    arguments: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    returns: dict[str, Any] = Field(default_factory=lambda: {"properties": {}})
    mutation_class: str = "READ_ONLY"  # READ_ONLY | STAGEABLE | MUTATING | IRREVERSIBLE
    cancellable: bool = True
    idempotent: bool = True
    authorization: str | None = None
    postcondition: str | None = None
    kind: str = ""  # derived from mutation_class when omitted
    delay_s: float = 0.01
    domain: str = "generic"

    @model_validator(mode="before")
    @classmethod
    def _accept_boolean_flags(cls, data: Any) -> Any:
        # Accept the field names used by common function/tool kits while
        # preserving one canonical internal schema.
        if isinstance(data, dict):
            data = dict(data)
            if "arguments" not in data:
                for alias in ("input_schema", "parameters", "args_schema"):
                    if alias in data:
                        data["arguments"] = data[alias]
                        break
            if "returns" not in data:
                for alias in ("output_schema", "result_schema", "return_schema"):
                    if alias in data:
                        data["returns"] = data[alias]
                        break
            # {"read_only": true} / {"state_modifying": true} are the Theme
            # 05 guide's own vocabulary; map them onto mutation_class.
            if "mutation_class" not in data:
                if data.get("read_only") is True or data.get("state_modifying") is False:
                    data["mutation_class"] = "READ_ONLY"
                elif data.get("state_modifying") is True or data.get("read_only") is False:
                    data["mutation_class"] = "MUTATING"
                else:
                    for alias in ("risk", "risk_level", "effect", "mutation"):
                        if alias in data:
                            data["mutation_class"] = data[alias]
                            break
        return data

    @field_validator("mutation_class", mode="before")
    @classmethod
    def _normalize_mutation_class(cls, v: Any) -> str:
        key = re.sub(r"[\s-]+", "_", str(v).strip().upper())
        key = _MUTATION_ALIASES.get(key, key)
        if key not in MUTATION_CLASSES:
            raise ValueError(f"mutation_class must be one of {MUTATION_CLASSES}, got {v!r}")
        return key

    @model_validator(mode="after")
    def _derive_kind(self) -> ToolManifest:
        if not self.kind:
            self.kind = _KIND_FOR_CLASS[self.mutation_class]
        try:
            Draft202012Validator.check_schema(self.arguments)
            Draft202012Validator.check_schema(self.returns)
        except SchemaError as exc:
            raise ValueError(f"invalid JSON Schema for tool {self.name}: {exc.message}") from exc
        if self.arguments.get("type", "object") != "object":
            raise ValueError(f"tool {self.name} arguments schema must describe an object")
        return self

    def validate_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """Validate a concrete call against the complete manifest schema."""
        try:
            Draft202012Validator(self.arguments).validate(args)
        except JsonSchemaValidationError as exc:
            location = ".".join(str(x) for x in exc.absolute_path) or "arguments"
            raise ValueError(
                f"invalid arguments for {self.name} at {location}: {exc.message}"
            ) from exc
        return dict(args)

    @property
    def state_changing(self) -> bool:
        return self.mutation_class != "READ_ONLY"

    @property
    def risk(self) -> RiskLevel:
        if self.mutation_class == "READ_ONLY":
            return RiskLevel.FREE
        return RiskLevel(self.mutation_class)

    @property
    def properties(self) -> dict[str, dict[str, Any]]:
        return dict(self.arguments.get("properties", {}))

    @property
    def required(self) -> list[str]:
        return list(self.arguments.get("required", []))

    @property
    def return_fields(self) -> list[str]:
        return list(self.returns.get("properties", {}))


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
        meta = BUILTIN_META.get(self.name, {})
        return ToolManifest(
            name=self.name,
            kind=self.kind,
            delay_s=self.delay_s,
            domain=self.domain,
            mutation_class="READ_ONLY" if self.risk == RiskLevel.FREE else self.risk.value,
            cancellable=True,
            idempotent=True,
            **meta,
        )


_DATE = {"type": "string", "format": "date"}

# Schemas for the built-in travel tools, so they go through the same
# manifest-driven planner as any tool a scenario hands us at runtime.
BUILTIN_META: dict[str, dict[str, Any]] = {
    "search_flights": {
        "description": "Search available flights to a destination city",
        "keywords": ["flight", "fly", "plane", "airline", "options"],
        "arguments": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "destination city"},
                "from": {"type": "string", "description": "origin city"},
                "date": _DATE,
                "slot": {"type": "string", "enum": ["morning", "afternoon", "evening", "night"]},
            },
            "required": ["to"],
        },
        "returns": {"properties": {"flight_id": {}, "to": {}, "slot": {}}},
    },
    "search_hotels": {
        "description": "Search hotels to stay in a city",
        "keywords": ["hotel", "stay", "room", "accommodation"],
        "arguments": {
            "type": "object",
            "properties": {"city": {"type": "string"}, "checkin": _DATE},
            "required": ["city"],
        },
        "returns": {"properties": {"hotel_id": {}, "city": {}}},
    },
    "search_cabs": {
        "description": "Find a cab or taxi ride to a place",
        "keywords": ["cab", "taxi", "ride", "pickup"],
        "arguments": {
            "type": "object",
            "properties": {"to": {"type": "string", "description": "place to ride to"}},
            "required": ["to"],
        },
        "returns": {"properties": {"cab_eta_min": {}, "route": {}}},
    },
    "check_calendar": {
        "description": "Check calendar availability on a date",
        "keywords": ["calendar", "free", "available", "busy"],
        "arguments": {"type": "object", "properties": {"date": _DATE}, "required": ["date"]},
        "returns": {"properties": {"free": {}, "slot": {}}},
    },
    "hold_seat": {
        "description": "Hold a seat on a flight without paying",
        "keywords": ["hold", "seat"],
        "arguments": {
            "type": "object",
            "properties": {"flight_id": {"type": "string"}},
            "required": ["flight_id"],
        },
        "returns": {"properties": {"hold_id": {}, "flight_id": {}}},
    },
    "modify_booking": {
        "description": "Modify an existing held booking",
        "keywords": ["modify", "booking"],
        "arguments": {
            "type": "object",
            "properties": {"hold_id": {"type": "string"}},
            "required": ["hold_id"],
        },
        "returns": {"properties": {"modified": {}, "hold_id": {}}},
    },
    "confirm_booking": {
        "description": "Confirm and pay for a flight booking",
        "keywords": ["book", "booking", "confirm", "pay"],
        "arguments": {
            "type": "object",
            "properties": {"hold_id": {"type": "string"}},
            "required": ["hold_id"],
        },
        "returns": {"properties": {"ref": {}, "hold_id": {}, "status": {}}},
    },
}


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

    def has(self, name: str) -> bool:
        return name in self._manifests

    def all(self) -> list[ToolManifest]:
        return list(self._manifests.values())

    def copy(self) -> ToolRegistry:
        return ToolRegistry(self.all())

    def producers_of(self, field_name: str) -> list[ToolManifest]:
        """Tools whose declared result carries ``field_name`` (chaining candidates)."""
        return [m for m in self._manifests.values() if field_name in m.return_fields]

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
        return manifest.validate_args(args)


def default_registry() -> ToolRegistry:
    return ToolRegistry([spec.manifest() for spec in TOOL_REGISTRY.values()])


def load_manifests(path: str | Path) -> list[ToolManifest]:
    """Load a JSON file holding one manifest or a list of manifests."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("tools", [raw])
    return [ToolManifest.model_validate(item) for item in items]


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


def _synthesize_from_schema(manifest: ToolManifest, params: dict[str, Any]) -> dict[str, Any]:
    """Deterministic payload for a manifest-only tool: echo args that share a
    result field's name, mint ``<PREFIX>-<tag>`` ids, fill the rest by type."""
    salt = json.dumps(params, sort_keys=True, default=str)
    tag = uuid.uuid5(uuid.NAMESPACE_URL, f"{manifest.name}:{salt}").hex[:6]
    out: dict[str, Any] = {}
    for name, schema in manifest.returns.get("properties", {}).items():
        schema = schema if isinstance(schema, dict) else {}
        if name in params:
            out[name] = params[name]
        elif name.endswith("_id") or name == "id":
            prefix = (name[:-3] if name.endswith("_id") else manifest.name)[:3].upper()
            out[name] = f"{prefix}-{int(tag, 16) % 9000 + 1000}"
        elif name == "status":
            out[name] = "COMMITTED" if manifest.state_changing else "OK"
        elif schema.get("type") == "integer":
            out[name] = int(tag[:2], 16) % 50 + 1
        elif schema.get("type") == "boolean":
            out[name] = True
        else:
            out[name] = f"{name}-{tag}"
    return out


# ---------------------------------------------------------------------------
# Fault injection — deterministic, per tool (Theme 05 §4 "Mock Environment")
# ---------------------------------------------------------------------------


class ToolError(RuntimeError):
    """The tool reported a failure; nothing was committed."""


class ToolTimeout(ToolError):
    """No answer in time — the effect may or may not have landed (UNKNOWN)."""


@dataclass
class FaultPlan:
    """The first ``fail_times`` attempts raise :class:`ToolError`; the next
    ``timeout_times`` raise :class:`ToolTimeout`. With ``commit_on_timeout``
    the effect lands even though the caller saw a timeout — the case where a
    blind retry double-books."""

    fail_times: int = 0
    timeout_times: int = 0
    commit_on_timeout: bool = False


# ---------------------------------------------------------------------------
# Sandbox — dispatch + cancellation-safe dedup
# ---------------------------------------------------------------------------


class MockToolSandbox:
    """Owns idempotency dedup so a retried call never re-dispatches externally."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        faults: dict[str, FaultPlan] | None = None,
    ) -> None:
        self._dedup: dict[str, ToolResult] = {}
        # One lock per idempotency key closes the race where two concurrent
        # retries both check the cache before either records its result. Calls
        # under different keys remain fully concurrent.
        self._key_locks: dict[str, asyncio.Lock] = {}
        self.registry = registry or default_registry()
        self.faults = dict(faults or {})
        self._attempts: dict[str, int] = {}
        # Every dispatch that reached the "external system": (tool, params, key).
        # Evaluation counts duplicate state-changing effects from this log.
        self.dispatch_log: list[tuple[str, dict[str, Any], str | None]] = []
        self.effects: list[ToolResult] = []

    def already_dispatched(self, idempotency_key: str) -> ToolResult | None:
        return self._dedup.get(idempotency_key)

    def status(self, idempotency_key: str) -> ToolResult | None:
        """Verify hook: did an effect with this key land? (``verify_after_timeout``)."""
        hit = self._dedup.get(idempotency_key)
        return hit if hit is not None and hit.status == "COMPLETED" else None

    def _manifest_for(self, tool: str) -> ToolManifest | None:
        return self.registry.get(tool) if self.registry.has(tool) else None

    async def call(
        self,
        tool: str,
        params: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        speed: float = 1.0,
    ) -> ToolResult:
        """Dispatch a call with atomic per-key idempotency."""
        if idempotency_key is None:
            return await self._call_unlocked(tool, params, idempotency_key=None, speed=speed)
        lock = self._key_locks.setdefault(idempotency_key, asyncio.Lock())
        async with lock:
            return await self._call_unlocked(
                tool, params, idempotency_key=idempotency_key, speed=speed
            )

    async def _call_unlocked(
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
        manifest = self._manifest_for(tool)
        try:
            spec = spec_for(tool)
        except KeyError:
            if manifest is None:
                raise
            spec = ToolSpec(manifest.name, manifest.kind, manifest.delay_s, manifest.domain)
        params = params or {}

        if idempotency_key is not None:
            cached = self._dedup.get(idempotency_key)
            if cached is not None:
                # replay-safe: never re-dispatch a known effect — an ABANDONED
                # one may still land externally, so it is not retried either
                return cached

        self.dispatch_log.append((tool, dict(params), idempotency_key))
        attempt = self._attempts[tool] = self._attempts.get(tool, 0) + 1
        fault = self.faults.get(tool)
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

        if tool in TOOL_REGISTRY or manifest is None:
            payload = _synthesize_payload(spec, params)
        else:
            payload = _synthesize_from_schema(manifest, params)
        result = ToolResult(
            tool=tool,
            external_operation_id=external_operation_id,
            accepted_at_ms=t0,
            completed_at_ms=time.perf_counter() * 1000.0,
            status="COMPLETED",
            payload=payload,
        )
        if fault is not None and attempt <= fault.fail_times:
            raise ToolError(f"{tool}: injected failure (attempt {attempt})")
        if fault is not None and attempt <= fault.fail_times + fault.timeout_times:
            if fault.commit_on_timeout:
                self._record(result, idempotency_key, spec)
            raise ToolTimeout(f"{tool}: injected timeout (attempt {attempt})")
        self._record(result, idempotency_key, spec)
        return result

    def _record(self, result: ToolResult, key: str | None, spec: ToolSpec) -> None:
        if key is not None:
            self._dedup[key] = result
        if spec.risk != RiskLevel.FREE:
            self.effects.append(result)


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

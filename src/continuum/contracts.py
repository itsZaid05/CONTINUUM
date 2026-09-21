"""
CONTINUUM — Contracts
Single source of truth for all cross-team interfaces.
Pydantic v2, frozen, json_schema-exportable.

Corresponds to: PERCEPTION → DELTA+ARBITER → VERSIONED STATE → PROVENANCE → POLICY → BRANCH → GATE
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 1. Perception
# ---------------------------------------------------------------------------

class Modality(str, Enum):
    TEXT = "text"
    AUDIO = "audio"
    VISION = "vision"


class EvidenceSpan(BaseModel):
    """Structured evidence from perception, with confidence."""

    model_config = {"frozen": True}

    text: str = Field(..., min_length=1, description="Raw evidence text, e.g. 'Actually, Bangalore'")
    modality: Modality = Field(default=Modality.TEXT)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    asr_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    vision_bbox: tuple[int, int, int, int] | None = Field(default=None)
    transcript_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    timestamp: datetime = Field(default_factory=utcnow)
    source: str | None = Field(default=None, description="e.g. 'user', 'asr', 'vision'")

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("text must be non-empty after strip")
        return v

    @model_validator(mode="after")
    def _validate_span(self) -> "EvidenceSpan":
        if self.start_ms is not None and self.end_ms is not None:
            if self.end_ms < self.start_ms:
                raise ValueError("end_ms must be >= start_ms")
        return self


class PerceptionOutput(BaseModel):
    """Output of perception fast path, including instant ACK."""

    model_config = {"frozen": True}

    version_in: int = Field(..., ge=1, description="Version this turn is based on")
    evidences: list[EvidenceSpan] = Field(..., min_length=1)
    fast_ack: str = Field(..., description="Instant ack text, <200ms")
    fast_ack_latency_ms: int = Field(..., ge=0, le=500)
    render_provenance: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 2. Delta + Arbiter (fused)
# ---------------------------------------------------------------------------

class ArbiterCategory(str, Enum):
    NEW_GOAL = "NEW_GOAL"
    MODIFY = "MODIFY"
    ADD_CONSTRAINT = "ADD_CONSTRAINT"
    RETRACT = "RETRACT"
    NOISE = "NOISE"


class DeltaOp(str, Enum):
    REPLACE = "replace"
    ADD = "add"
    REMOVE = "remove"


class Delta(BaseModel):
    """What changed — provenance-preserving diff."""

    model_config = {"frozen": True}

    op: DeltaOp | None = Field(default=None, description="None means no semantic change (NOISE)")
    field: str | None = Field(default=None, description="Dot-path, e.g. 'destination' or 'constraints.time'")
    old_value: Any | None = None
    new_value: Any | None = None
    span: str = Field(default="", description="Raw phrase that triggered delta")

    @model_validator(mode="after")
    def _check_noise_consistency(self) -> "Delta":
        # NOISE deltas should have op=None; we allow callers to omit field but be consistent
        if self.op is None:
            # allow field/new_value to be None for noise
            pass
        else:
            if not self.field:
                raise ValueError("field required when op is set")
        return self


class ArbiterDecision(BaseModel):
    """Single LLM call output: delta + category + confidence."""

    model_config = {"frozen": True}

    category: ArbiterCategory
    confidence: float = Field(...)
    delta: Delta | None = None
    rationale: str = Field(..., min_length=1)
    evidence_spans: list[int] = Field(default_factory=list, description="Indices into PerceptionOutput.evidences")
    suggested_clarification: str | None = Field(default=None)
    latency_ms: int = Field(default=0, ge=0)
    model: str = Field(default="offline-fake")
    timestamp: datetime = Field(default_factory=utcnow)

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, v: Any) -> float:
        try:
            fv = float(v)
        except Exception:
            raise ValueError("confidence must be float")
        return max(0.0, min(1.0, fv))

    def needs_clarification(self, risky_threshold: float = 0.72, general_threshold: float = 0.60) -> bool:
        if self.category in {ArbiterCategory.RETRACT, ArbiterCategory.NEW_GOAL}:
            return self.confidence < risky_threshold
        return self.confidence < general_threshold


# ---------------------------------------------------------------------------
# 3. Versioned State
# ---------------------------------------------------------------------------

class StateStatus(str, Enum):
    ACTIVE = "ACTIVE"
    MERGED = "MERGED"
    ABANDONED = "ABANDONED"


class StateVersion(BaseModel):
    """Immutable snapshot of user intent state, V1→V2→V3…"""

    model_config = {"frozen": True}

    version: int = Field(..., ge=1)
    parent: int | None = Field(default=None, ge=1)
    committed_evidence: list[EvidenceSpan] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict, description="Canonical intent slots")
    derived_from: int | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    status: StateStatus = Field(default=StateStatus.ACTIVE)
    arbiter_category: ArbiterCategory | None = Field(default=None)
    delta: Delta | None = Field(default=None)

    def diff(self, other: "StateVersion") -> dict[str, Any]:
        """Simple diff for debugging."""
        keys = set(self.state) | set(other.state)
        out: dict[str, Any] = {}
        for k in sorted(keys):
            if self.state.get(k) != other.state.get(k):
                out[k] = {"old": self.state.get(k), "new": other.state.get(k)}
        return out


# ---------------------------------------------------------------------------
# 4. Provenance + Execution Graph
# ---------------------------------------------------------------------------

class RiskLevel(str, Enum):
    FREE = "FREE"               # search, read, dry-run — auto
    STAGEABLE = "STAGEABLE"     # draft booking, hold fare — stageable
    MUTATING = "MUTATING"       # cancel, prune — auditable
    IRREVERSIBLE = "IRREVERSIBLE"  # book, pay, send — requires explicit commit


class Provenance(BaseModel):
    """Typed provenance vector — per-axis trust, not scalar."""

    model_config = {"frozen": True}

    step_id: str = Field(..., min_length=1)
    based_on: int = Field(..., ge=1, description="StateVersion this reasoning was derived from")
    inputs: dict[str, Any] = Field(default_factory=dict)
    axes: dict[str, float] = Field(
        default_factory=lambda: {"freshness": 1.0, "capability": 1.0, "tool": 1.0, "verification": 1.0},
        description="Per-axis trust in [0,1]; merged via min, not average",
    )
    tainted_by: dict[str, set[str]] = Field(
        default_factory=lambda: {"freshness": set(), "capability": set(), "tool": set(), "verification": set()}
    )
    timestamp: datetime = Field(default_factory=utcnow)

    @field_validator("axes")
    @classmethod
    def _validate_axes(cls, v: dict[str, float]) -> dict[str, float]:
        for k, score in v.items():
            if not 0.0 <= score <= 1.0:
                raise ValueError(f"axis {k} score {score} out of [0,1]")
        return v

    def merge(self, *upstreams: "Provenance") -> "Provenance":
        """Merge provenance: per-axis min (stalest wins), taint union."""
        merged_axes: dict[str, float] = dict(self.axes)
        merged_taint: dict[str, set[str]] = {k: set(v) for k, v in self.tainted_by.items()}
        all_keys = set(merged_axes) | {k for u in upstreams for k in u.axes}
        for k in all_keys:
            vals = [merged_axes.get(k, 1.0)] + [u.axes.get(k, 1.0) for u in upstreams]
            merged_axes[k] = min(vals)
            for u in upstreams:
                if k in u.tainted_by:
                    merged_taint.setdefault(k, set()).update(u.tainted_by[k])
                # if upstream degraded on this axis, track its step
                if u.axes.get(k, 1.0) < 1.0:
                    merged_taint.setdefault(k, set()).add(u.step_id)
        return Provenance(
            step_id=self.step_id,
            based_on=self.based_on,
            inputs=dict(self.inputs),
            axes=merged_axes,
            tainted_by=merged_taint,
            timestamp=utcnow(),
        )


class NodeStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    INVALIDATED = "INVALIDATED"
    CANCELLED = "CANCELLED"


class ExecutionNode(BaseModel):
    model_config = {"frozen": True}

    id: str = Field(..., min_length=1)
    kind: Literal["search", "filter", "price", "book", "pay", "inform", "hold", "cancel"] = Field(...)
    provenance: Provenance
    status: NodeStatus = Field(default=NodeStatus.PENDING)
    risk: RiskLevel = Field(default=RiskLevel.FREE)
    result: Any | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# 5. Branch Manager
# ---------------------------------------------------------------------------

class BranchState(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"
    PROMOTED = "PROMOTED"
    INVALIDATED = "INVALIDATED"
    CANCELLED = "CANCELLED"
    CLEANED_UP = "CLEANED_UP"
    ABANDONED = "ABANDONED"  # non-cancellable dispatched


class Branch(BaseModel):
    model_config = {"frozen": False}  # mutable lifecycle

    id: str
    parent_version: int = Field(..., ge=1)
    hypothesis: ArbiterDecision
    state: BranchState = Field(default=BranchState.CREATED)
    is_primary: bool = Field(default=False)
    depth: int = Field(default=0, ge=0, le=10)
    tool_calls: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utcnow)
    cleaned_at: datetime | None = None

    def cleanup_latency_ms(self) -> int | None:
        if self.cleaned_at and self.created_at:
            return int((self.cleaned_at - self.created_at).total_seconds() * 1000)
        return None


class SpeculationBudget(BaseModel):
    model_config = {"frozen": True}

    max_shadow: int = Field(default=2, ge=0, le=10)
    max_depth: int = Field(default=3, ge=0, le=10)
    max_calls_per_shadow: int = Field(default=6, ge=0, le=100)
    max_compute_ms: int | None = Field(default=None, ge=0)
    allowed_levels: set[RiskLevel] = Field(default_factory=lambda: {RiskLevel.FREE, RiskLevel.STAGEABLE})
    pause_shadow_when_busy: bool = Field(default=True)


# ---------------------------------------------------------------------------
# 6. Effect Ledger
# ---------------------------------------------------------------------------

class EffectStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"


class EffectRecord(BaseModel):
    model_config = {"frozen": False}

    effect_id: str = Field(..., min_length=1, description="Idempotency key: sha256(version+tool+args)")
    tool: str = Field(..., min_length=1)
    args_hash: str = Field(..., min_length=1)
    status: EffectStatus = Field(default=EffectStatus.UNKNOWN)
    attempt: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utcnow)
    last_verified_at: datetime | None = None
    result: Any | None = None


# ---------------------------------------------------------------------------
# 7. Result Gate
# ---------------------------------------------------------------------------

class GateDecision(str, Enum):
    APPLY = "APPLY"
    DISCARD = "DISCARD"  # stale


# ---------------------------------------------------------------------------
# 8. Scenario replay contracts (deterministic)
# ---------------------------------------------------------------------------

class ScenarioTurn(BaseModel):
    """One entry in a replay scenario JSON."""

    model_config = {"frozen": True}

    at_ms: int = Field(default=0, ge=0, description="Stepped clock ms")
    user: str | None = Field(default=None, description="User utterance at this time")
    tool_start: str | None = Field(default=None, description="Tool dispatch, e.g. 'search:Delhi'")
    tool_result: dict[str, Any] | None = Field(default=None, description="Tool completion")
    inject_timeout: str | None = Field(default=None, description="Tool id to time out")
    expect: dict[str, Any] | None = Field(default=None)


class Scenario(BaseModel):
    model_config = {"frozen": True}

    name: str = Field(..., min_length=1)
    description: str = Field(default="")
    initial_state: dict[str, Any] = Field(default_factory=dict)
    turns: list[ScenarioTurn] = Field(default_factory=list)
    budgets: SpeculationBudget | None = None


# ---------------------------------------------------------------------------
# 9. Reports
# ---------------------------------------------------------------------------

class ArbiterAccuracyReport(BaseModel):
    model_config = {"frozen": True}

    total: int
    correct: int
    accuracy: float
    macro_f1: float
    per_category: dict[str, dict[str, float]]
    confusion: dict[str, dict[str, int]]
    ece: float | None = None
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    model: str = "offline-fake"
    gold_hash: str | None = None
    generated_at: datetime = Field(default_factory=utcnow)


class ComparisonRow(BaseModel):
    model_config = {"frozen": True}

    scenario: str
    baseline_wall_ms: int
    continuum_wall_ms: int
    saved_pct: float
    correct: bool
    double_book: bool | None = None
    notes: str | None = None

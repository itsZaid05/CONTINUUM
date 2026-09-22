"""
CONTINUUM Pydantic Schemas - Matching Frozen Hackathon Contracts
PRISM Generative AI Hackathon (Theme 05: Interruptible Real-Time Agents)
"""
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field
import time

# --- Node & Branch Statuses ---
# Lifecycle: CREATED -> RUNNING -> COMPLETED | INVALIDATED | CANCELLED | SHADOW | PROMOTED | ABANDONED
NodeStatus = Literal[
    "CREATED",
    "RUNNING",
    "COMPLETED",
    "INVALIDATED",
    "CANCELLED",
    "SHADOW",
    "PROMOTED",
    "ABANDONED"
]

DeltaType = Literal[
    "MODIFY",
    "ADD_CONSTRAINT",
    "RETRACT",
    "NEW_GOAL",
    "NOISE"
]

AuthorizationType = Literal[
    "EXPLICIT",
    "IMPLIED",
    "NONE"
]

RiskTier = Literal[
    "FREE",
    "STAGEABLE",
    "MUTATING",
    "IRREVERSIBLE"
]

EffectStatus = Literal[
    "INTENT",
    "PENDING",
    "UNKNOWN",
    "COMMITTED",
    "NOT_COMMITTED"
]

# -------------------------------------------------------------
# CONTRACT 1: POST /api/v1/intent/classify (AI/ML A -> Backend)
# -------------------------------------------------------------
class IntentClassifyRequest(BaseModel):
    session_id: str
    utterance: str
    event_id: Optional[str] = None

class IntentClassifyResponse(BaseModel):
    event_id: str
    session_id: str
    utterance: str
    delta_type: DeltaType
    confidence: float = Field(ge=0.0, le=1.0)
    ivs_score: float = Field(ge=0.0, le=1.0, description="Intent Volatility Score (0.0 to 1.0)")
    authorization: AuthorizationType
    affected_fields: List[str] = Field(default_factory=list)
    new_values: Dict[str, Any] = Field(default_factory=dict)
    preserved_constraints: List[str] = Field(default_factory=list)
    clarification_needed: bool = False
    clarification_prompt: Optional[str] = None

# -------------------------------------------------------------
# CONTRACT 2: POST /api/v1/plan/generate (AI/ML B -> Backend)
# -------------------------------------------------------------
class PlanStep(BaseModel):
    step_id: str
    tool: str
    params: Dict[str, Any] = Field(default_factory=dict)
    risk: RiskTier = "FREE"
    idempotency_key: Optional[str] = None
    depends_on: List[str] = Field(default_factory=list)

class ShadowBranch(BaseModel):
    branch_id: str
    base_version: int
    hypothesis: str
    steps: List[PlanStep] = Field(default_factory=list)

class PlanGenerateRequest(BaseModel):
    session_id: str
    utterance: str
    intent_data: Optional[IntentClassifyResponse] = None

class PlanGenerateResponse(BaseModel):
    session_id: str
    event_id: str
    base_version: int
    primary_plan: List[PlanStep]
    shadow_branches: List[ShadowBranch] = Field(default_factory=list)

# -------------------------------------------------------------
# CONTRACT 3: POST /api/v1/effect/verify (Backend Status Verification)
# -------------------------------------------------------------
class EffectVerifyRequest(BaseModel):
    effect_id: str
    idempotency_key: str
    external_operation_id: Optional[str] = None

class EffectVerifyResponse(BaseModel):
    effect_id: str
    idempotency_key: str
    external_operation_id: Optional[str] = None
    status: Literal["COMMITTED", "NOT_COMMITTED", "UNKNOWN"]

# -------------------------------------------------------------
# WebSocket Event Stream Models
# -------------------------------------------------------------
class InstantAckEvent(BaseModel):
    type: Literal["ACK"] = "ACK"
    session_id: str
    event_id: str
    version: int
    text: str = "Got it, updating..."
    timestamp_ms: float = Field(default_factory=lambda: time.time() * 1000)

class DAGNodeUpdateEvent(BaseModel):
    type: Literal["NODE_UPDATE"] = "NODE_UPDATE"
    session_id: str
    version: int
    step_id: str
    tool: str
    status: NodeStatus
    risk: RiskTier
    params: Dict[str, Any]
    output: Optional[Any] = None
    error: Optional[str] = None
    duration_ms: Optional[float] = None
    timestamp_ms: float = Field(default_factory=lambda: time.time() * 1000)

class MetricsHUDEvent(BaseModel):
    type: Literal["METRICS_UPDATE"] = "METRICS_UPDATE"
    session_id: str
    current_version: int
    pivot_latency_ms: float
    ivs_score: float
    token_savings_pct: float
    speculation_hits: int
    speculation_wasted: int
    regretted_actions: int
    active_tasks_count: int
    timestamp_ms: float = Field(default_factory=lambda: time.time() * 1000)

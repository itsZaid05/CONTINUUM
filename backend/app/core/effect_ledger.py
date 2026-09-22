"""
CONTINUUM Two-Phase Effect Ledger & Idempotency Engine
Lifecycle: INTENT -> PENDING -> [External Verification] -> COMMITTED | NOT_COMMITTED
Timeout path: PENDING -> UNKNOWN -> [verify_effect()] -> COMMITTED | NOT_COMMITTED
"""
import hashlib
import json
import uuid
import time
from typing import Dict, Optional, Any
from dataclasses import dataclass, field
from backend.app.models.schemas import EffectStatus

@dataclass
class EffectRecord:
    effect_id: str
    idempotency_key: str
    session_id: str
    event_id: str
    step_id: str
    tool_name: str
    params: Dict[str, Any]
    status: EffectStatus = "INTENT"
    external_operation_id: Optional[str] = None
    created_at_ms: float = field(default_factory=lambda: time.time() * 1000)
    updated_at_ms: float = field(default_factory=lambda: time.time() * 1000)
    result_data: Optional[Dict[str, Any]] = None

class EffectLedger:
    def __init__(self):
        # Maps effect_id -> EffectRecord
        self.effects: Dict[str, EffectRecord] = {}
        # Maps idempotency_key -> effect_id (for deduplication)
        self.idempotency_index: Dict[str, str] = {}

    def compute_idempotency_key(
        self,
        session_id: str,
        event_id: str,
        step_id: str,
        params: Dict[str, Any]
    ) -> str:
        """
        Computes deterministic idempotency key = hash(session, event, step, params)
        """
        raw_str = f"{session_id}:{event_id}:{step_id}:{json.dumps(params, sort_keys=True)}"
        return f"idemp_{hashlib.sha256(raw_str.encode('utf-8')).hexdigest()[:12]}"

    def create_intent(
        self,
        session_id: str,
        event_id: str,
        step_id: str,
        tool_name: str,
        params: Dict[str, Any]
    ) -> EffectRecord:
        idemp_key = self.compute_idempotency_key(session_id, event_id, step_id, params)
        
        # Check if already exists in index
        if idemp_key in self.idempotency_index:
            existing_id = self.idempotency_index[idemp_key]
            return self.effects[existing_id]

        effect_id = f"eff_{uuid.uuid4().hex[:8]}"
        record = EffectRecord(
            effect_id=effect_id,
            idempotency_key=idemp_key,
            session_id=session_id,
            event_id=event_id,
            step_id=step_id,
            tool_name=tool_name,
            params=params,
            status="INTENT"
        )
        self.effects[effect_id] = record
        self.idempotency_index[idemp_key] = effect_id
        return record

    def transition_to_pending(self, effect_id: str) -> EffectRecord:
        record = self.effects[effect_id]
        record.status = "PENDING"
        record.updated_at_ms = time.time() * 1000
        return record

    def transition_to_committed(
        self,
        effect_id: str,
        external_operation_id: str,
        result_data: Optional[Dict[str, Any]] = None
    ) -> EffectRecord:
        record = self.effects[effect_id]
        record.status = "COMMITTED"
        record.external_operation_id = external_operation_id
        record.result_data = result_data or {}
        record.updated_at_ms = time.time() * 1000
        return record

    def transition_to_unknown(self, effect_id: str) -> EffectRecord:
        record = self.effects[effect_id]
        record.status = "UNKNOWN"
        record.updated_at_ms = time.time() * 1000
        return record

    def transition_to_not_committed(self, effect_id: str) -> EffectRecord:
        record = self.effects[effect_id]
        record.status = "NOT_COMMITTED"
        record.updated_at_ms = time.time() * 1000
        return record

    def get_by_effect_id(self, effect_id: str) -> Optional[EffectRecord]:
        return self.effects.get(effect_id)

    def get_committed_effects_for_session(self, session_id: str) -> list[EffectRecord]:
        return [
            eff for eff in self.effects.values() 
            if eff.session_id == session_id and eff.status == "COMMITTED"
        ]

effect_ledger = EffectLedger()

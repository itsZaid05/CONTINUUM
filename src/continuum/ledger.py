"""
Effect Ledger — idempotency + verify-after-timeout

Phase 1: in-memory ledger with deterministic sha256 idempotency keys.
Phase 3 will add tool-client verify hook.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .contracts import EffectRecord, EffectStatus, utcnow


def effect_id_for(version: int, tool: str, args: dict[str, Any]) -> str:
    payload = json.dumps({"v": version, "tool": tool, "args": args}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class EffectLedger:
    def __init__(self) -> None:
        self._records: dict[str, EffectRecord] = {}

    def prepare(self, effect_id: str, tool: str, args_hash: str) -> EffectRecord:
        if effect_id in self._records:
            return self._records[effect_id]
        rec = EffectRecord(
            effect_id=effect_id,
            tool=tool,
            args_hash=args_hash,
            status=EffectStatus.UNKNOWN,
        )
        self._records[effect_id] = rec
        return rec

    def commit(self, effect_id: str, result: Any | None = None) -> EffectRecord:
        rec = self._records.get(effect_id)
        if not rec:
            raise KeyError(effect_id)
        rec.status = EffectStatus.COMMITTED
        rec.last_verified_at = utcnow()
        rec.result = result
        return rec

    def fail(self, effect_id: str) -> EffectRecord:
        rec = self._records.get(effect_id)
        if not rec:
            raise KeyError(effect_id)
        rec.status = EffectStatus.FAILED
        return rec

    def verify_after_timeout(self, effect_id: str, exists_fn=None) -> EffectStatus:
        """
        Do not blindly retry. Query whether effect exists (idempotent check).
        exists_fn: callable(effect_id) -> bool  — mocked in tests; in prod queries tool.
        """
        rec = self._records.get(effect_id)
        if not rec:
            raise KeyError(effect_id)
        # If we have no checker, keep UNKNOWN and caller must decide
        if exists_fn is None:
            return rec.status
        exists = exists_fn(effect_id)
        if exists:
            rec.status = EffectStatus.COMMITTED
            rec.last_verified_at = utcnow()
        else:
            rec.status = EffectStatus.FAILED
        return rec.status

    def get(self, effect_id: str) -> EffectRecord | None:
        return self._records.get(effect_id)

    def all(self) -> list[EffectRecord]:
        return list(self._records.values())

"""
Versioned State — V1→V2→V3…
Append-only, parent-linked, with rapid-change merge and WAL.

Design borrowed from prism_rt versioned_store + FreshCtx dependency tracking,
simplified for Engineer A text MVP.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from .contracts import (
    ArbiterCategory,
    Delta,
    DeltaOp,
    EvidenceSpan,
    StateStatus,
    StateVersion,
    utcnow,
)


MERGE_WINDOW_MS = 300


class VersionedStore:
    """In-memory versioned store with optional WAL file."""

    def __init__(self, wal_path: Path | None = None) -> None:
        self._versions: dict[int, StateVersion] = {}
        self._next: int = 1
        self._wal_path = wal_path
        # for merge coalescing: last version's created_at
        self._last_patch_ms: int | None = None

    # ------------------------------------------------------------------
    # WAL helpers
    # ------------------------------------------------------------------
    def _wal_append(self, v: StateVersion) -> None:
        if self._wal_path is None:
            return
        self._wal_path.parent.mkdir(parents=True, exist_ok=True)
        with self._wal_path.open("a", encoding="utf-8") as f:
            f.write(v.model_dump_json() + "\n")

    # ------------------------------------------------------------------
    # Core ops
    # ------------------------------------------------------------------
    def create_initial(self, state: dict[str, Any], evidence: list[EvidenceSpan] | None = None) -> StateVersion:
        v = StateVersion(
            version=self._next,
            parent=None,
            committed_evidence=list(evidence or []),
            state=dict(state),
            derived_from=None,
            created_at=utcnow(),
            status=StateStatus.ACTIVE,
        )
        self._versions[self._next] = v
        self._next += 1
        self._wal_append(v)
        return v

    def current(self) -> StateVersion | None:
        if not self._versions:
            return None
        return self._versions[max(self._versions)]

    def get(self, version: int) -> StateVersion | None:
        return self._versions.get(version)

    def history(self) -> list[StateVersion]:
        return [self._versions[k] for k in sorted(self._versions)]

    def hash_state(self, state: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:12]

    # ------------------------------------------------------------------
    # Patch logic
    # ------------------------------------------------------------------
    def patch(
        self,
        base_version: int,
        delta: Delta | None,
        category: ArbiterCategory,
        evidence: list[EvidenceSpan] | None = None,
        *,
        extra_state: dict[str, Any] | None = None,
    ) -> StateVersion:
        """
        Apply delta to base_version → new version.
        NOISE (delta None or op None) → no new version (idempotent) — returns base.
        """
        base = self._versions.get(base_version)
        if base is None:
            raise KeyError(f"base version {base_version} not found")

        # Noise: no semantic change
        if delta is None or delta.op is None:
            return base

        new_state = dict(base.state)
        # Apply delta
        if delta.op == DeltaOp.REPLACE:
            assert delta.field is not None
            self._set_nested(new_state, delta.field, delta.new_value)
        elif delta.op == DeltaOp.ADD:
            assert delta.field is not None
            self._set_nested(new_state, delta.field, delta.new_value)
        elif delta.op == DeltaOp.REMOVE:
            assert delta.field is not None
            self._remove_nested(new_state, delta.field)
        else:
            raise ValueError(f"unknown op {delta.op}")

        # Merge extra_state (for ADD_CONSTRAINT that adds a list entry)
        if extra_state:
            new_state.update(extra_state)

        committed = list(base.committed_evidence) + list(evidence or [])
        v = StateVersion(
            version=self._next,
            parent=base_version,
            committed_evidence=committed,
            state=new_state,
            derived_from=base_version,
            created_at=utcnow(),
            status=StateStatus.ACTIVE,
            arbiter_category=category,
            delta=delta,
        )
        self._versions[self._next] = v
        self._next += 1
        self._wal_append(v)
        return v

    def merge_rapid(
        self,
        base_version: int,
        deltas: list[tuple[Delta, ArbiterCategory, list[EvidenceSpan]]],
    ) -> StateVersion:
        """
        Coalesce rapid burst (<MERGE_WINDOW_MS) deltas into a single MERGED version.
        Applies deltas in order, last-wins for same field.
        """
        if not deltas:
            raise ValueError("merge_rapid requires at least one delta")
        base = self._versions.get(base_version)
        if base is None:
            raise KeyError(f"base version {base_version} not found")

        new_state = dict(base.state)
        committed: list[EvidenceSpan] = list(base.committed_evidence)
        last_category: ArbiterCategory = deltas[-1][1]
        last_delta: Delta | None = deltas[-1][0]

        for delta, _cat, ev in deltas:
            if delta is None or delta.op is None:
                continue
            committed.extend(ev)
            assert delta.field is not None
            if delta.op == DeltaOp.REPLACE:
                self._set_nested(new_state, delta.field, delta.new_value)
            elif delta.op == DeltaOp.ADD:
                # For constraints, handle list append
                if delta.field == "constraints" and isinstance(delta.new_value, dict):
                    cur = new_state.get("constraints", [])
                    if not isinstance(cur, list):
                        cur = [cur]
                    new_state["constraints"] = list(cur) + [delta.new_value]
                else:
                    self._set_nested(new_state, delta.field, delta.new_value)
            elif delta.op == DeltaOp.REMOVE:
                self._remove_nested(new_state, delta.field)

        v = StateVersion(
            version=self._next,
            parent=base_version,
            committed_evidence=committed,
            state=new_state,
            derived_from=base_version,
            created_at=utcnow(),
            status=StateStatus.MERGED,
            arbiter_category=last_category,
            delta=last_delta,
        )
        self._versions[self._next] = v
        self._next += 1
        self._wal_append(v)
        return v

    # ------------------------------------------------------------------
    # Nested helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _set_nested(d: dict[str, Any], field: str, value: Any) -> None:
        parts = field.split(".")
        cur: dict[str, Any] = d
        for p in parts[:-1]:
            nxt = cur.get(p)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[p] = nxt
            cur = nxt
        cur[parts[-1]] = value

    @staticmethod
    def _remove_nested(d: dict[str, Any], field: str) -> None:
        parts = field.split(".")
        cur: dict[str, Any] = d
        for p in parts[:-1]:
            nxt = cur.get(p)
            if not isinstance(nxt, dict):
                return
            cur = nxt
        cur.pop(parts[-1], None)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------
    def load_wal(self) -> None:
        """Replay WAL into memory (cold start)."""
        if self._wal_path is None or not self._wal_path.exists():
            return
        with self._wal_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                v = StateVersion.model_validate_json(line)
                self._versions[v.version] = v
                self._next = max(self._next, v.version + 1)

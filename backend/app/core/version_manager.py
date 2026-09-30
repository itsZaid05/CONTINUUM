"""
CONTINUUM Version & Event Manager
Strict Invariant: Only Event/Version Manager may advance current_version; LLMs/tools never mutate version directly.
"""

import time
import uuid
from dataclasses import dataclass, field


@dataclass
class EventRecord:
    event_id: str
    session_id: str
    version: int
    utterance: str
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000)
    delta_type: str | None = None
    ivs_score: float = 0.0


@dataclass
class SessionState:
    session_id: str
    current_version: int = 0
    events: list[EventRecord] = field(default_factory=list)
    last_event_time_ms: float = field(default_factory=lambda: time.time() * 1000)
    created_at_ms: float = field(default_factory=lambda: time.time() * 1000)


class VersionManager:
    def __init__(self):
        self._sessions: dict[str, SessionState] = {}

    def get_or_create_session(self, session_id: str) -> SessionState:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState(session_id=session_id)
        return self._sessions[session_id]

    def register_interrupt(
        self, session_id: str, utterance: str, event_id: str | None = None
    ) -> EventRecord:
        """
        Advances the session monotonic version (V_n -> V_n+1) and issues a unique event_id.
        Target budget: < 5ms
        """
        session = self.get_or_create_session(session_id)
        session.current_version += 1

        if not event_id:
            event_id = f"evt_{uuid.uuid4().hex[:8]}"

        record = EventRecord(
            event_id=event_id,
            session_id=session_id,
            version=session.current_version,
            utterance=utterance,
            timestamp_ms=time.time() * 1000,
        )
        session.events.append(record)
        session.last_event_time_ms = record.timestamp_ms
        return record

    def get_current_version(self, session_id: str) -> int:
        session = self._sessions.get(session_id)
        return session.current_version if session else 0

    def get_session_history(self, session_id: str) -> list[EventRecord]:
        session = self._sessions.get(session_id)
        return session.events if session else []

    def is_stale(self, session_id: str, base_version: int) -> bool:
        """
        Stale Gate Invariant: if base_version != current_version, task output is STALE and must be DISCARDED.
        """
        curr = self.get_current_version(session_id)
        return base_version != curr


# Global singleton instance
version_manager = VersionManager()

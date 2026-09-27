"""Candidate-versus-committed state for streaming speech transcripts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class CommittedUtterance:
    turn_id: str
    text: str
    committed_at_ms: float
    candidate_revision: int


@dataclass
class StreamingSpeechState:
    """Keep unstable ASR hypotheses away from committed planner state.

    ``replace`` is used for LiveKit partial transcripts (each hypothesis
    supersedes the prior one); ``append`` is used by the JSONL chunk protocol.
    Only :meth:`commit` produces text that may reach the planner or tools.
    """

    candidate: str = ""
    candidate_revision: int = 0
    candidate_updated_ms: float | None = None
    committed: list[CommittedUtterance] = field(default_factory=list)

    def update(
        self,
        text: str,
        *,
        mode: Literal["append", "replace"] = "append",
        at_ms: float,
    ) -> str:
        if mode == "replace":
            self.candidate = text
        else:
            self.candidate += text
        self.candidate_revision += 1
        self.candidate_updated_ms = at_ms
        return self.candidate

    def commit(self, at_ms: float) -> CommittedUtterance | None:
        text = self.candidate.strip()
        if not text:
            return None
        row = CommittedUtterance(
            turn_id=uuid.uuid4().hex,
            text=text,
            committed_at_ms=at_ms,
            candidate_revision=self.candidate_revision,
        )
        self.committed.append(row)
        self.candidate = ""
        self.candidate_updated_ms = None
        return row

    def discard_candidate(self) -> None:
        self.candidate = ""
        self.candidate_updated_ms = None

    @property
    def last_committed(self) -> CommittedUtterance | None:
        return self.committed[-1] if self.committed else None

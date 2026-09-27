"""Validated contracts for the released Full-Duplex-Bench v3 data format."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator


class FdbDialogueTurn(BaseModel):
    user: str
    user_annotated: str | None = None
    ai: str | None = None


class FdbToolCall(BaseModel):
    function: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)


class FdbScenario(BaseModel):
    id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    title: str = Field(min_length=1)
    difficulty: str
    dialogue: list[FdbDialogueTurn] = Field(default_factory=list)
    acting_notes: str = ""
    disfluency_features: list[str] = Field(default_factory=list)
    expected_tool_calls: list[FdbToolCall] = Field(default_factory=list)
    num_expected_calls: int = Field(ge=0)
    state_rollback_test: bool = False
    latency_profile: str = "normal"

    @model_validator(mode="after")
    def _call_count_matches(self) -> FdbScenario:
        if self.num_expected_calls != len(self.expected_tool_calls):
            raise ValueError(
                f"{self.id}: num_expected_calls={self.num_expected_calls} but "
                f"expected_tool_calls has {len(self.expected_tool_calls)} entries"
            )
        return self


class FdbStatistics(BaseModel):
    total_scenarios: int = Field(ge=0)
    domains: dict[str, int] = Field(default_factory=dict)
    difficulty_distribution: dict[str, int] = Field(default_factory=dict)
    feature_distribution: dict[str, int] = Field(default_factory=dict)
    state_rollback_scenarios: int = Field(default=0, ge=0)
    multilingual_scenarios: int = Field(default=0, ge=0)
    avg_tool_calls_per_scenario: float = Field(default=0.0, ge=0.0)


class FdbBenchmark(BaseModel):
    benchmark_name: str
    version: str
    generated_at: str | None = None
    statistics: FdbStatistics
    scenarios: list[FdbScenario]

    @model_validator(mode="after")
    def _validate_summary(self) -> FdbBenchmark:
        if self.statistics.total_scenarios != len(self.scenarios):
            raise ValueError(
                "statistics.total_scenarios does not match the scenario list: "
                f"{self.statistics.total_scenarios} != {len(self.scenarios)}"
            )
        ids = [row.id for row in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("FDB scenario ids must be unique")
        return self

    @property
    def tool_names(self) -> set[str]:
        return {call.function for row in self.scenarios for call in row.expected_tool_calls}

    @property
    def expected_call_count(self) -> int:
        return sum(len(row.expected_tool_calls) for row in self.scenarios)


def load_benchmark(path: str | Path) -> FdbBenchmark:
    """Load the official ``benchmark_data_v2.json`` with integrity checks."""

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return FdbBenchmark.model_validate(raw)

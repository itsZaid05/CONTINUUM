"""Deterministic audio/frame evaluation through the real ``AgentRuntime``.

The suite uses organizer-provided transcripts/OCR text and confidence values;
no score depends on a network service or a downloaded model.  Ungrounded and
low-confidence inputs are included to verify that perception asks rather than
acts.  Scoring is shared with the text runtime evaluation so the four Theme 05
categories remain directly comparable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .runtime_eval import evaluate as evaluate_runtime
from .runtime_eval import render_markdown as render_runtime_markdown

SUITE_DEFAULT = Path("data/runtime_scenarios/multimodal_suite.json")
SYSTEMS: dict[str, dict[str, Any]] = {
    "continuum": {},
    "naive_runtime": {"planner": "naive"},
}


def evaluate(
    suite_path: Path = SUITE_DEFAULT,
    *,
    time_scale: float = 0.1,
) -> dict[str, Any]:
    report = evaluate_runtime(
        suite_path,
        systems=SYSTEMS,
        ablations=False,
        time_scale=time_scale,
    )
    report["modality_note"] = (
        "Audio transcripts and frame OCR are deterministic upstream evidence; "
        "low-confidence evidence must clarify and must not execute tools."
    )
    return report


def render_markdown(report: dict[str, Any]) -> str:
    text = render_runtime_markdown(report)
    return text.replace(
        "# Runtime evaluation — timed scenarios through `AgentRuntime`",
        "# Multimodal runtime evaluation — audio and frame scenarios",
        1,
    ).replace(" text scenarios", " multimodal scenarios", 1)

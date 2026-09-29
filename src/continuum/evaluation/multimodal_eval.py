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
RAW_SUITE_DEFAULT = Path("data/runtime_scenarios/raw_media_suite.json")
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


def evaluate_raw(suite_path: Path = RAW_SUITE_DEFAULT, *, time_scale: float = 1.0) -> dict[str, Any]:
    """Raw WAV/PNG fixtures with *no* transcript or OCR text: real local ASR
    (faster-whisper) and OCR (RapidOCR) run inside the runtime. Real time
    scale, because recognition latency is part of what is measured."""
    from ..perception import recognizers

    found = recognizers()
    missing = [k for k, v in found.items() if v is None]
    if missing:
        return {
            "suite": str(suite_path).replace("\\", "/"),
            "skipped": True,
            "recognizers": found,
            "reason": f"local recognizer(s) not installed: {', '.join(missing)} — "
            "pip install -e '.[asr,vision]' and run `continuum fetch-models`",
        }
    report = evaluate_runtime(
        suite_path, systems={"continuum": {}}, ablations=False, time_scale=time_scale
    )
    report["recognizers"] = found
    report["skipped"] = False
    return report


def render_raw_markdown(report: dict[str, Any]) -> str:
    if report.get("skipped"):
        return f"# Raw-media evaluation — skipped\n\n{report['reason']}\n"
    text = render_runtime_markdown(report)
    rec = report["recognizers"]
    return text.replace(
        "# Runtime evaluation — timed scenarios through `AgentRuntime`",
        "# Raw-media evaluation — real ASR and OCR inside the runtime\n\n"
        f"Recognizers: ASR `{rec['asr']}` (faster-whisper, local), OCR `{rec['ocr']}`. "
        "Fixtures carry no transcript or OCR text; pivot latency includes real recognition time.",
        1,
    ).replace(" text scenarios", " raw-media scenarios", 1)

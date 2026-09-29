"""Real multimodal grounding: ASR calibration, OCR on frames, off-loop recognition.

Pure-logic tests always run. Tests that exercise the actual recognizers on the
committed fixtures (data/media/) skip when faster-whisper + a local model or
RapidOCR are not installed (`pip install -e '.[multimodal]'`,
`continuum fetch-models`).
"""

from __future__ import annotations

import asyncio
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from continuum import perception
from continuum.evaluation import multimodal_eval
from continuum.evaluation.runtime_eval import add_noise
from continuum.perception import asr_confidence, perceive_audio, perceive_frame
from continuum.runtime import AgentRuntime, EventType, RuntimeEvent

MEDIA = Path("data/media")
HAVE_ASR = perception.recognizers()["asr"] is not None
HAVE_OCR = perception.recognizers()["ocr"] is not None
needs_asr = pytest.mark.skipif(not HAVE_ASR, reason="no local faster-whisper model (continuum fetch-models)")
needs_ocr = pytest.mark.skipif(not HAVE_OCR, reason="RapidOCR not installed (.[vision])")


def _seg(words: list[float], no_speech: float = 0.0, avg_logprob: float = -0.4) -> SimpleNamespace:
    return SimpleNamespace(
        words=[SimpleNamespace(probability=p) for p in words],
        no_speech_prob=no_speech,
        avg_logprob=avg_logprob,
    )


# ---------------------------------------------------------------- calibration
def test_confident_words_pass_the_gate_even_when_logprob_looks_low():
    # exp(-0.5) ≈ 0.61 would have failed the 0.72 gate on a perfect transcript
    assert asr_confidence([_seg([0.95, 0.9, 0.97], avg_logprob=-0.5)]) > 0.9


def test_one_garbled_word_caps_confidence_below_the_gate():
    conf = asr_confidence([_seg([0.98, 0.97, 0.17, 0.96])])
    assert conf <= 0.60 < perception.MIN_GROUNDED_CONFIDENCE


def test_non_speech_scores_zero():
    assert asr_confidence([_seg([0.9, 0.9], no_speech=0.8)]) == 0.0
    assert asr_confidence([]) == 0.0


def test_model_discovery_prefers_env_then_models_dir(tmp_path, monkeypatch):
    model = tmp_path / "faster-whisper-tiny.en"
    model.mkdir()
    (model / "model.bin").write_bytes(b"x")
    monkeypatch.delenv(perception.ASR_MODEL_ENV, raising=False)
    monkeypatch.setenv(perception.MODEL_DIR_ENV, str(tmp_path))
    assert perception.default_asr_model_path() == model
    monkeypatch.setenv(perception.ASR_MODEL_ENV, str(tmp_path / "missing"))
    assert perception.default_asr_model_path() is None  # an explicit bad path is not silently replaced


# ---------------------------------------------------------------- real OCR
@needs_ocr
def test_ocr_grounds_model_and_error_code_from_pixels():
    out = perceive_frame((MEDIA / "washer_panel_e4.png").read_bytes())
    ev = out.evidences[0]
    assert ev.source == "local-ocr" and out.render_provenance["grounded"]
    assert "WM-4500" in ev.text and "E4" in ev.text


@needs_ocr
@pytest.mark.parametrize("name", ["washer_door_no_text.png", "washer_panel_blurred.png"])
def test_unreadable_frames_ask_instead_of_guessing(name):
    out = perceive_frame((MEDIA / name).read_bytes())
    assert "ambiguous" in out.render_provenance


# ---------------------------------------------------------------- real ASR
@needs_asr
def test_asr_transcribes_clean_speech_and_grounds_it():
    out = perceive_audio((MEDIA / "find_flights_delhi.wav").read_bytes())
    ev = out.evidences[0]
    assert ev.source == "local-asr" and out.render_provenance["grounded"]
    assert "delhi" in ev.text.lower() and "morning" in ev.text.lower()


@needs_asr
def test_misheard_city_under_noise_is_read_back_not_acted_on():
    noisy = add_noise((MEDIA / "actually_bangalore.wav").read_bytes(), 0, seed=2)
    out = perceive_audio(noisy)
    assert not out.render_provenance["grounded"]
    assert out.render_provenance["ambiguous"].startswith("I heard")


# ---------------------------------------------------------------- runtime
def test_recognition_runs_off_the_event_loop(monkeypatch):
    """A 300 ms recognizer must not stall the loop: ACK first, other work keeps ticking."""
    real = perception.perceive_audio

    def slow(*args, **kwargs):
        time.sleep(0.3)
        return real(*args, transcript="Find flights to Delhi", confidence=0.95)

    monkeypatch.setattr("continuum.runtime.perceive_audio", slow)

    async def go() -> tuple[list[str], int]:
        rt = AgentRuntime(tool_speed=0.01, today=date(2026, 9, 25))
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        hb = asyncio.create_task(heartbeat())
        wav = (MEDIA / "find_flights_delhi.wav").read_bytes()
        await rt.handle(RuntimeEvent(session_id="s", type=EventType.AUDIO, data=wav))
        hb.cancel()
        acts = [rt.output_queue.get_nowait().type for _ in range(rt.output_queue.qsize())]
        return acts, ticks

    acts, ticks = asyncio.run(go())
    assert acts[0] == "speak" and "tool_call" in acts
    assert acts.count("speak") == 1  # no duplicate ACK after recognition
    assert ticks >= 10  # the loop kept running during the 300 ms recognition


def test_raw_media_eval_reports_skip_when_recognizers_are_missing(monkeypatch):
    monkeypatch.setattr(perception, "recognizers", lambda: {"asr": None, "ocr": "rapidocr"})
    report = multimodal_eval.evaluate_raw()
    assert report["skipped"] and "asr" in report["reason"]
    assert "skipped" in multimodal_eval.render_raw_markdown(report)

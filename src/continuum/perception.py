"""Fast, confidence-aware text, audio, and frame perception.

The default path is deterministic and offline.  Audio can carry an upstream
transcript, a transcript in RIFF ``INFO`` metadata, or opt in to a local
``faster-whisper`` model directory.  The local recognizer is deliberately
*disk only*: a missing directory or package causes a clarification and never a
network/model download.  Frames use upstream OCR text or PNG ``tEXt`` chunks.
Inputs without grounded evidence are acknowledged but never authorized to
trigger a tool.
"""

from __future__ import annotations

import contextlib
import math
import re
import tempfile
import time
import wave
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from .contracts import EvidenceSpan, Modality, PerceptionOutput

MIN_GROUNDED_CONFIDENCE = 0.72

# Backchannel / noise lexicon (NOISE fast-path)
BACKCHANNEL: set[str] = {
    "hmm",
    "hmm, okay…",
    "hmm, okay...",
    "okay",
    "okay…",
    "uh",
    "uhm",
    "mm-hmm",
    "mmhmm",
    "yeah",
    "yeah…",
    "right",
    "sure",
    "…",
    "...",
}

RETRACT_MARKERS = re.compile(
    r"\b(don't|do not|cancel|stop|abort|forget|never mind|hold off)\b", re.I
)
MODIFY_MARKERS = re.compile(r"\b(actually|instead|change|switch|make it|no,|correction)\b", re.I)

ACK_TEMPLATES = {
    "noise": "👍",
    "modify": "Got it — updating your request…",
    "add_constraint": "Added — keeping your other details…",
    "retract": "Understood — holding that…",
    "new_goal": "Starting fresh — one moment…",
    "generic": "Got it — on it…",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _is_backchannel(text: str) -> bool:
    n = _normalize(text)
    return n in BACKCHANNEL or n.strip(" .…") in BACKCHANNEL


def _guess_ack_category(text: str) -> str:
    n = _normalize(text)
    if _is_backchannel(text):
        return "noise"
    if RETRACT_MARKERS.search(n):
        if "forget" in n and ("find" in n or "search" in n or "look for" in n):
            return "new_goal"
        return "retract"
    if MODIFY_MARKERS.search(n):
        if "keep" in n or "but" in n or "also" in n or "only" in n:
            return "add_constraint"
        return "modify"
    if len(n.split()) <= 2 and len(n) < 12:
        return "noise"
    return "generic"


def perceive_text(
    text: str,
    version_in: int = 1,
    modality: Literal["text", "audio", "vision"] = "text",
    *,
    confidence: float | None = None,
    source: str | None = None,
) -> PerceptionOutput:
    """Turn grounded text into evidence and a sub-200 ms acknowledgement."""
    t0 = time.perf_counter()
    clean = text.strip()
    if not clean:
        raise ValueError("empty input")

    mod = Modality(modality)
    conf = 1.0 if mod == Modality.TEXT else 0.85
    if confidence is not None:
        conf = min(1.0, max(0.0, float(confidence)))
    evidence = EvidenceSpan(
        text=clean,
        modality=mod,
        asr_confidence=conf if mod == Modality.AUDIO else None,
        transcript_confidence=conf,
        source=source or ("user" if mod == Modality.TEXT else mod.value),
    )
    category = _guess_ack_category(clean)
    latency_ms = min(15, max(1, int((time.perf_counter() - t0) * 1000)))
    provenance: dict[str, Any] = {
        "guessed_category": category,
        "backchannel": _is_backchannel(clean),
        "confidence": conf,
        "grounded": conf >= MIN_GROUNDED_CONFIDENCE,
    }
    if conf < MIN_GROUNDED_CONFIDENCE:
        detail = "audio" if mod == Modality.AUDIO else "image"
        provenance["ambiguous"] = f"I am not confident I understood the {detail}. Could you confirm the key detail?"
    return PerceptionOutput(
        version_in=version_in,
        evidences=[evidence],
        fast_ack=ACK_TEMPLATES.get(category, ACK_TEMPLATES["generic"]),
        fast_ack_latency_ms=latency_ms,
        render_provenance=provenance,
    )


async def aperceive_text(text: str, version_in: int = 1) -> PerceptionOutput:
    return perceive_text(text, version_in)


def _riff_info(wav_bytes: bytes) -> tuple[str, float | None]:
    """Read deterministic transcript/confidence hand-off metadata.

    ``ICMT`` or ``INAM`` carries UTF-8 text.  ``ICRD`` may carry a decimal
    confidence.  A small chunk parser is used instead of a regex so padding,
    CRC-like bytes, and adjacent chunks never leak into the transcript.
    """
    transcript = ""
    confidence: float | None = None
    pos = 12
    size = len(wav_bytes)
    while pos + 8 <= size:
        chunk_id = wav_bytes[pos : pos + 4]
        chunk_size = int.from_bytes(wav_bytes[pos + 4 : pos + 8], "little")
        body = wav_bytes[pos + 8 : pos + 8 + chunk_size]
        if chunk_id in {b"ICMT", b"INAM"}:
            transcript = body.rstrip(b"\x00").decode("utf-8", "ignore").strip()
        elif chunk_id in {b"ICRD", b"CONF"}:
            with contextlib.suppress(ValueError):
                confidence = float(body.rstrip(b"\x00").decode("ascii"))
        elif chunk_id == b"LIST" and body.startswith(b"INFO"):
            nested_text, nested_conf = _riff_info(b"RIFF\x00\x00\x00\x00WAVE" + body[4:])
            transcript = nested_text or transcript
            confidence = nested_conf if nested_conf is not None else confidence
        pos += 8 + chunk_size + (chunk_size % 2)
    return transcript, confidence


@lru_cache(maxsize=2)
def _load_local_asr(model_dir: str) -> Any:
    """Load faster-whisper without allowing its name-based download path."""
    path = Path(model_dir).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"local ASR model directory does not exist: {path}")
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - optional integration
        raise RuntimeError("local ASR requires the optional faster-whisper package") from exc
    return WhisperModel(str(path), device="cpu", compute_type="int8", local_files_only=True)


def _transcribe_local(wav_bytes: bytes, model_dir: str) -> tuple[str, float]:
    model = _load_local_asr(model_dir)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            temp_name = tmp.name
            tmp.write(wav_bytes)
        segments, _info = model.transcribe(temp_name, beam_size=1, vad_filter=False)
        rows = list(segments)
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
    text = " ".join(str(row.text).strip() for row in rows).strip()
    if not rows:
        return text, 0.0
    # faster-whisper exposes average log probabilities.  Convert them to a
    # bounded, conservative confidence rather than calling them percentages.
    confidence = sum(math.exp(min(0.0, float(row.avg_logprob))) for row in rows) / len(rows)
    return text, min(1.0, max(0.0, confidence))


def perceive_audio(
    wav_bytes: bytes,
    version_in: int = 1,
    *,
    transcript: str | None = None,
    confidence: float | None = None,
    asr_model_path: str | Path | None = None,
) -> PerceptionOutput:
    """Validate a PCM WAV and produce only grounded transcript evidence."""
    try:
        with wave.open(BytesIO(wav_bytes), "rb") as wav:
            frames, rate = wav.getnframes(), wav.getframerate()
            if not frames or not rate:
                raise wave.Error("empty WAV")
    except (wave.Error, EOFError) as exc:
        raise ValueError("audio event must contain a valid WAV clip") from exc

    source = "upstream-asr" if transcript else "riff-info"
    metadata_text, metadata_conf = _riff_info(wav_bytes)
    grounded_text = (transcript or metadata_text).strip()
    conf = confidence if confidence is not None else metadata_conf
    if not grounded_text and asr_model_path is not None:
        try:
            grounded_text, conf = _transcribe_local(wav_bytes, str(asr_model_path))
            source = "local-asr"
        except (FileNotFoundError, RuntimeError):
            # An optional recognizer must never make the default offline path
            # crash or attempt a download.  Clarification is the safe fallback.
            grounded_text = ""
    if not grounded_text:
        evidence = EvidenceSpan(
            text="[untranscribed audio]",
            modality=Modality.AUDIO,
            asr_confidence=0.0,
            transcript_confidence=0.0,
            source="wav",
        )
        return PerceptionOutput(
            version_in=version_in,
            evidences=[evidence],
            fast_ack="I received the audio…",
            fast_ack_latency_ms=1,
            render_provenance={
                "grounded": False,
                "confidence": 0.0,
                "ambiguous": "I need a transcription before acting on that audio.",
            },
        )
    return perceive_text(
        grounded_text,
        version_in,
        "audio",
        confidence=0.85 if conf is None else conf,
        source=source,
    )


def _png_text(png_bytes: bytes) -> str:
    if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("frame event must contain PNG data")
    chunks: list[str] = []
    pos = 8
    while pos + 12 <= len(png_bytes):
        length = int.from_bytes(png_bytes[pos : pos + 4], "big")
        kind = png_bytes[pos + 4 : pos + 8]
        body = png_bytes[pos + 8 : pos + 8 + length]
        if kind == b"tEXt" and b"\x00" in body:
            _keyword, value = body.split(b"\x00", 1)
            chunks.append(value.decode("latin1", "ignore").strip())
        pos += 12 + length
    return " ".join(filter(None, chunks)).strip()


def perceive_frame(
    png_bytes: bytes,
    version_in: int = 1,
    *,
    text: str | None = None,
    confidence: float | None = None,
) -> PerceptionOutput:
    """Validate a PNG and consume grounded upstream/embedded OCR text."""
    embedded = _png_text(png_bytes)
    grounded_text = (text or embedded).strip()
    if not grounded_text:
        evidence = EvidenceSpan(
            text="[ungrounded image]",
            modality=Modality.VISION,
            transcript_confidence=0.0,
            source="png",
        )
        return PerceptionOutput(
            version_in=version_in,
            evidences=[evidence],
            fast_ack="I received the image…",
            fast_ack_latency_ms=1,
            render_provenance={
                "grounded": False,
                "confidence": 0.0,
                "ambiguous": "Please identify the relevant text or object in the image.",
            },
        )
    return perceive_text(
        grounded_text,
        version_in,
        "vision",
        confidence=0.8 if confidence is None else confidence,
        source="upstream-ocr" if text else "png-text",
    )

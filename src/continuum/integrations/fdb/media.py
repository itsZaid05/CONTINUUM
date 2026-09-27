"""Provider-neutral media contracts and deterministic artifact handling."""

from __future__ import annotations

import hashlib
import os
import re
import struct
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

MEDIA_SCHEMA = "continuum.media.v1"


class MediaConfigurationError(ValueError):
    """Invalid or contradictory media configuration."""


@dataclass(frozen=True)
class MediaConfig:
    input_sample_rate: int = 16000
    output_sample_rate: int = 24000
    input_channels: int = 1
    output_channels: int = 1
    frame_size_ms: int = 20
    synchronized_transcription: bool = True
    interruptions: bool = True
    min_interruption_duration: float = 0.5
    false_interruption_timeout: float = 2.0
    vad_start_sensitivity: str = "START_SENSITIVITY_HIGH"
    vad_end_sensitivity: str = "END_SENSITIVITY_HIGH"
    vad_prefix_padding_ms: int = 300
    vad_silence_duration_ms: int = 500
    requested_word_timestamps: bool = True
    video_enabled: bool = True
    speaking_video_fps: float = 1.0
    silent_video_fps: float = 0.2
    jpeg_quality: int = 80
    record_output_wav: bool = True
    artifact_dir: Path = Path("reports/phase03-media")

    def __post_init__(self) -> None:
        if min(self.input_sample_rate, self.output_sample_rate, self.frame_size_ms) <= 0:
            raise MediaConfigurationError("sample rates and frame size must be positive")
        if self.input_channels not in (1, 2) or self.output_channels not in (1, 2):
            raise MediaConfigurationError("channel counts must be one or two")
        if not self.interruptions:
            raise MediaConfigurationError("Gemini server VAD requires interruptions to be enabled")
        if not 1 <= self.jpeg_quality <= 100:
            raise MediaConfigurationError("JPEG quality must be between 1 and 100")
        if min(self.speaking_video_fps, self.silent_video_fps) <= 0:
            raise MediaConfigurationError("video frame rates must be positive")

    @classmethod
    def from_env(cls) -> MediaConfig:
        def boolean(name: str, default: bool) -> bool:
            value = os.getenv(name)
            if value is None:
                return default
            if value.lower() not in {"true", "false", "1", "0"}:
                raise MediaConfigurationError(f"{name} must be true or false")
            return value.lower() in {"true", "1"}

        def number(name: str, default: Any, cast: type) -> Any:
            try:
                return cast(os.getenv(name, str(default)))
            except ValueError as exc:
                raise MediaConfigurationError(f"{name} has an invalid value") from exc

        return cls(
            input_sample_rate=number("CONTINUUM_INPUT_SAMPLE_RATE", 16000, int),
            output_sample_rate=number("CONTINUUM_OUTPUT_SAMPLE_RATE", 24000, int),
            input_channels=number("CONTINUUM_INPUT_CHANNELS", 1, int),
            output_channels=number("CONTINUUM_OUTPUT_CHANNELS", 1, int),
            frame_size_ms=number("CONTINUUM_FRAME_SIZE_MS", 20, int),
            synchronized_transcription=boolean("CONTINUUM_SYNC_TRANSCRIPTION", True),
            interruptions=boolean("CONTINUUM_ALLOW_INTERRUPTION", True),
            min_interruption_duration=number("CONTINUUM_MIN_INTERRUPTION_S", 0.5, float),
            false_interruption_timeout=number("CONTINUUM_FALSE_INTERRUPTION_TIMEOUT_S", 2.0, float),
            vad_prefix_padding_ms=number("CONTINUUM_VAD_PREFIX_PADDING_MS", 300, int),
            vad_silence_duration_ms=number("CONTINUUM_VAD_SILENCE_MS", 500, int),
            requested_word_timestamps=boolean("CONTINUUM_WORD_TIMESTAMPS", True),
            video_enabled=boolean("CONTINUUM_VIDEO_ENABLED", True),
            speaking_video_fps=number("CONTINUUM_SPEAKING_VIDEO_FPS", 1.0, float),
            silent_video_fps=number("CONTINUUM_SILENT_VIDEO_FPS", 0.2, float),
            jpeg_quality=number("CONTINUUM_JPEG_QUALITY", 80, int),
            record_output_wav=boolean("CONTINUUM_RECORD_OUTPUT_WAV", True),
            artifact_dir=Path(os.getenv("CONTINUUM_MEDIA_ARTIFACT_DIR", "reports/phase03-media")),
        )


@dataclass(frozen=True)
class TranscriptEvent:
    direction: Literal["input", "output"]
    text: str
    final: bool
    observed_at: float
    source: str
    start_time: float | None = None
    end_time: float | None = None
    timebase: str | None = None
    timing_source: Literal[
        "event_observation", "vad_boundary", "provider_alignment"
    ] = "event_observation"
    language: str | None = None
    speaker: str | None = None
    item_id: str | None = None
    confidence: float | None = None
    interrupted: bool = False

    def payload(self) -> dict[str, Any]:
        return {"schema": MEDIA_SCHEMA, "kind": "transcript", **asdict(self)}


class Pcm16WavRecorder:
    """Atomic, deterministic PCM16 recorder for provider output frames."""

    def __init__(self, path: str | Path, *, sample_rate: int, channels: int) -> None:
        self.path = Path(path)
        if self.path.suffix.lower() != ".wav":
            raise ValueError("output artifact must use .wav")
        self.partial = self.path.with_suffix(self.path.suffix + ".partial")
        self.sample_rate, self.channels = sample_rate, channels
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = wave.open(str(self.partial), "wb")  # noqa: SIM115
        self._stream.setnchannels(channels)
        self._stream.setsampwidth(2)
        self._stream.setframerate(sample_rate)
        self.frames = self.samples = 0
        self._closed: dict[str, Any] | None = None

    def write(
        self, pcm: bytes, *, sample_rate: int, channels: int, samples_per_channel: int
    ) -> None:
        if self._closed is not None:
            raise ValueError("recorder is closed")
        if (sample_rate, channels) != (self.sample_rate, self.channels):
            raise ValueError("PCM format drift")
        if samples_per_channel <= 0 or len(pcm) != samples_per_channel * channels * 2:
            raise ValueError("malformed PCM16 frame dimensions")
        self._stream.writeframesraw(pcm)
        self.frames += 1
        self.samples += samples_per_channel

    def close(self) -> dict[str, Any]:
        if self._closed is not None:
            return self._closed
        self._stream.close()
        os.replace(self.partial, self.path)
        data = self.path.read_bytes()
        self._closed = {
            "schema": MEDIA_SCHEMA,
            "kind": "audio_artifact",
            "provenance": "provider_output_before_room_playout",
            "media_type": "audio/wav",
            "encoding": "PCM_S16LE",
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "sample_width": 2,
            "model_frame_count": self.frames,
            "samples_per_channel": self.samples,
            "duration_s": self.samples / self.sample_rate,
            "file_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "path": str(self.path),
        }
        return self._closed


_SECRET_KEY = re.compile(
    r"(?:api[_-]?(?:key|secret)|authorization|credential|"
    r"access[_-]?token|refresh[_-]?token)",
    re.I,
)


def redact_media(value: Any, configured_secrets: tuple[str, ...] = ()) -> Any:
    """Recursively redact credential fields and configured values, preserving token metrics."""
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if _SECRET_KEY.search(str(key))
                else redact_media(item, configured_secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_media(item, configured_secrets) for item in value]
    if isinstance(value, str):
        result = value
        for secret in configured_secrets:
            if secret:
                result = result.replace(secret, "[REDACTED]")
        return result
    return value


def pcm16_silence(samples: int, channels: int = 1) -> bytes:
    return struct.pack("<" + "h" * samples * channels, *([0] * samples * channels))

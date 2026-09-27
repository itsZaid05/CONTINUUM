from __future__ import annotations

import pytest

from continuum.integrations.fdb.media import (
    MEDIA_SCHEMA,
    MediaConfig,
    MediaConfigurationError,
    Pcm16WavRecorder,
    TranscriptEvent,
    pcm16_silence,
    redact_media,
)


def record(path):
    recorder = Pcm16WavRecorder(path, sample_rate=16000, channels=1)
    recorder.write(pcm16_silence(320), sample_rate=16000, channels=1, samples_per_channel=320)
    return recorder


def test_wav_is_byte_deterministic(tmp_path):
    first, second = tmp_path / "a.wav", tmp_path / "b.wav"
    record(first).close(); record(second).close()
    assert first.read_bytes() == second.read_bytes()


def test_wav_metadata_and_hash(tmp_path):
    result = record(tmp_path / "a.wav").close()
    assert result["encoding"] == "PCM_S16LE" and result["samples_per_channel"] == 320
    assert len(result["sha256"]) == 64 and result["duration_s"] == 0.02


def test_wav_close_is_idempotent(tmp_path):
    recorder = record(tmp_path / "a.wav")
    assert recorder.close() == recorder.close()


def test_wav_rejects_malformed_dimensions(tmp_path):
    recorder = Pcm16WavRecorder(tmp_path / "a.wav", sample_rate=16000, channels=1)
    with pytest.raises(ValueError, match="dimensions"):
        recorder.write(b"bad", sample_rate=16000, channels=1, samples_per_channel=3)


def test_wav_rejects_format_drift(tmp_path):
    recorder = Pcm16WavRecorder(tmp_path / "a.wav", sample_rate=16000, channels=1)
    with pytest.raises(ValueError, match="drift"):
        recorder.write(b"\0\0", sample_rate=24000, channels=1, samples_per_channel=1)


def test_transcript_contract_has_explicit_null_missingness():
    payload = TranscriptEvent("input", "hello", True, 1.0, "livekit").payload()
    assert payload["schema"] == MEDIA_SCHEMA and payload["confidence"] is None
    assert payload["start_time"] is None and payload["timing_source"] == "event_observation"


def test_transcript_preserves_provider_alignment():
    payload = TranscriptEvent("output", "ok", True, 2.0, "gemini", start_time=1.0, end_time=1.4, timebase="seconds", timing_source="provider_alignment", confidence=.8).payload()
    assert payload["start_time"] == 1.0 and payload["confidence"] == .8


def test_redaction_protects_fields_and_embedded_values():
    value = redact_media({"api_key": "x", "message": "oops SECRET"}, ("SECRET",))
    assert value == {"api_key": "[REDACTED]", "message": "oops [REDACTED]"}


def test_redaction_preserves_token_metrics():
    assert redact_media({"input_tokens": 17, "output_tokens": 4}) == {"input_tokens": 17, "output_tokens": 4}


def test_media_config_rejects_disabled_interruption(monkeypatch):
    monkeypatch.setenv("CONTINUUM_ALLOW_INTERRUPTION", "false")
    with pytest.raises(MediaConfigurationError, match="requires interruptions"):
        MediaConfig.from_env()

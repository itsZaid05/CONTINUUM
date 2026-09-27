from __future__ import annotations

import json
import wave

import pytest

from continuum.integrations.fdb.live_smoke import main, preflight, validate_pcm16_wav
from continuum.integrations.fdb.media import MediaConfig, MediaConfigurationError
from continuum.integrations.fdb.telemetry import JsonlTelemetry


def wav(path):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 160)


def test_config_strict_boolean(monkeypatch):
    monkeypatch.setenv("CONTINUUM_VIDEO_ENABLED", "perhaps")
    with pytest.raises(MediaConfigurationError, match="true or false"):
        MediaConfig.from_env()


def test_config_rejects_invalid_jpeg_quality(monkeypatch):
    monkeypatch.setenv("CONTINUUM_JPEG_QUALITY", "101")
    with pytest.raises(MediaConfigurationError, match="JPEG"):
        MediaConfig.from_env()


def test_telemetry_redacts_secret_but_retains_metrics(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "configured-secret")
    path = tmp_path / "events.jsonl"
    telemetry = JsonlTelemetry(room_name="room", path=path)
    telemetry.emit("model_metrics", message="configured-secret", input_tokens=9)
    payload = json.loads(path.read_text())
    assert payload["message"] == "[REDACTED]" and payload["input_tokens"] == 9


def test_pcm16_preflight(tmp_path):
    path = tmp_path / "input.wav"; wav(path)
    assert validate_pcm16_wav(path)["sample_width"] == 2


def test_smoke_fails_closed_without_credentials(monkeypatch):
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="missing required credentials"):
        preflight(allow_skip=False)


def test_smoke_explicit_skip_writes_honest_report(tmp_path, monkeypatch):
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    output = tmp_path / "report.json"
    assert main(["--allow-skip", "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["status"] == "skipped_external_gate"
    assert report["executed"] is False and report["official_score"] is False
    assert not any(report["checks"].values())

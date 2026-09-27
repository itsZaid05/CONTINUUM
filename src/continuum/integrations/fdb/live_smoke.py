"""Credential-aware Phase 3 LiveKit media smoke harness.

The credential-free mode is intentionally an external-gate audit, never a pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REQUIRED_CREDENTIALS = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "GOOGLE_API_KEY")
REQUIRED_CHECKS = (
    "worker_stayed_running",
    "remote_audio_received",
    "agent_output_wav_written",
    "audio_artifact_telemetry",
    "output_audio_frame_telemetry",
    "input_transcript_telemetry",
    "output_transcript_telemetry",
    "session_close_telemetry",
    "tool_completed_telemetry",
    "official_tool_call_written",
    "sampled_video_telemetry",
)


def validate_pcm16_wav(path: Path) -> dict[str, int]:
    try:
        with wave.open(str(path), "rb") as stream:
            metadata = {
                "sample_rate": stream.getframerate(),
                "channels": stream.getnchannels(),
                "sample_width": stream.getsampwidth(),
                "frames": stream.getnframes(),
            }
    except (OSError, wave.Error) as exc:
        raise ValueError("input is not a readable WAV") from exc
    valid = (
        metadata["sample_width"] == 2
        and metadata["channels"] in (1, 2)
        and metadata["frames"] > 0
        and 8000 <= metadata["sample_rate"] <= 48000
    )
    if not valid:
        raise ValueError("input must be non-empty 8-48 kHz mono/stereo PCM16 WAV")
    return metadata


def preflight(*, allow_skip: bool) -> tuple[bool, list[str]]:
    missing = [name for name in REQUIRED_CREDENTIALS if not os.getenv(name)]
    if missing and not allow_skip:
        raise RuntimeError("missing required credentials: " + ", ".join(missing))
    return not missing, missing


def skipped_report(run_id: str, missing: list[str]) -> dict[str, Any]:
    return {
        "schema": "continuum.phase03.live-smoke.v1",
        "run_id": run_id,
        "status": "skipped_external_gate",
        "executed": False,
        "official_score": False,
        "missing_credential_names": missing,
        "checks": {name: False for name in REQUIRED_CHECKS},
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-wav", type=Path)
    parser.add_argument("--output", type=Path, default=Path("reports/phase03_live_smoke.json"))
    parser.add_argument("--artifact-dir", type=Path, default=Path("reports/phase03-media"))
    parser.add_argument("--worker-startup-s", type=float, default=15.0)
    parser.add_argument("--timeout-s", type=float, default=90.0)
    parser.add_argument("--allow-skip", action="store_true")
    options = parser.parse_args(argv)
    run_id = f"phase03-{uuid.uuid4().hex}"
    ready, missing = preflight(allow_skip=options.allow_skip)
    if options.input_wav:
        validate_pcm16_wav(options.input_wav)
    if not ready:
        _write(options.output, skipped_report(run_id, missing))
        print(f"Phase 3 live smoke skipped: {len(missing)} required credentials unavailable")
        return 0

    # Import only after fail-closed preflight, ensuring credential-free auditing
    # cannot accidentally initialize a network client.
    from .live_smoke_runtime import LiveSmokeRunConfig, run_live_smoke

    if options.input_wav is None:
        raise RuntimeError("--input-wav is required for credentialed execution")
    try:
        report = asyncio.run(
            run_live_smoke(
                LiveSmokeRunConfig(
                    run_id=run_id,
                    input_wav=options.input_wav,
                    artifact_root=options.artifact_dir,
                    worker_startup_s=options.worker_startup_s,
                    timeout_s=options.timeout_s,
                )
            )
        )
    except Exception as exc:
        report = {
            "schema": "continuum.phase03.live-smoke.v1",
            "run_id": run_id,
            "status": "failed",
            "executed": True,
            "official_score": False,
            "error_type": type(exc).__name__,
            "checks": {name: False for name in REQUIRED_CHECKS},
        }
    _write(options.output, report)
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())

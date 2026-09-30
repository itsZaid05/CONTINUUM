"""Real and fakeable orchestration for the Phase 3 LiveKit media smoke."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .live_smoke import validate_pcm16_wav
from .media import Pcm16WavRecorder


@dataclass(frozen=True)
class LiveSmokeRunConfig:
    run_id: str
    input_wav: Path
    artifact_root: Path
    worker_startup_s: float = 15.0
    timeout_s: float = 90.0
    video_enabled: bool = True
    capture_tail_s: float = 2.0
    finalization_s: float = 3.0


@dataclass(frozen=True)
class LiveSmokePaths:
    run_dir: Path
    telemetry: Path
    official_tools: Path
    worker_log: Path
    remote_wav: Path
    provider_dir: Path


class Worker(Protocol):
    argv: tuple[str, ...]

    def poll(self) -> int | None: ...
    async def stop(self) -> None: ...
    def finish_log(self, secrets: tuple[str, ...]) -> None: ...


class RuntimeBackend(Protocol):
    async def create_room(self, room_name: str, metadata: str) -> None: ...
    async def connect_caller(self, room_name: str) -> None: ...
    async def wait_for_agent(self, timeout_s: float) -> None: ...
    async def publish_media(
        self,
        input_wav: Path,
        remote_recorder: Pcm16WavRecorder,
        *,
        video_enabled: bool,
        timeout_s: float,
    ) -> bool: ...
    async def disconnect(self) -> None: ...
    async def delete_room(self, room_name: str) -> None: ...
    async def close(self) -> None: ...


WorkerFactory = Callable[[LiveSmokePaths], Worker]
BackendFactory = Callable[[LiveSmokePaths], Awaitable[RuntimeBackend]]


class SubprocessWorker:
    """Managed worker subprocess with bounded process-group teardown."""

    def __init__(self, paths: LiveSmokePaths) -> None:
        self.argv: tuple[str, ...] = (
            sys.executable,
            "-m",
            "continuum.integrations.fdb.runner",
            "start",
        )
        child_env = os.environ.copy()
        child_env.update(
            {
                "CONTINUUM_TELEMETRY_PATH": str(paths.telemetry),
                "FDB_TOOL_LOG_PATH": str(paths.official_tools),
                "CONTINUUM_MEDIA_ARTIFACT_DIR": str(paths.provider_dir),
                "CONTINUUM_VIDEO_ENABLED": "true",
            }
        )
        paths.worker_log.parent.mkdir(parents=True, exist_ok=True)
        self._log = paths.worker_log.open("wb")
        self._process = subprocess.Popen(  # noqa: S603
            self.argv,
            env=child_env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def poll(self) -> int | None:
        return self._process.poll()

    async def stop(self) -> None:
        if self._process.poll() is not None:
            return
        try:
            self._signal_group(hard=False)
            await asyncio.to_thread(self._process.wait, 5)
        except subprocess.TimeoutExpired:
            self._signal_group(hard=True)
            await asyncio.to_thread(self._process.wait, 5)
        except ProcessLookupError:
            return

    def _signal_group(self, *, hard: bool) -> None:
        # start_new_session=True puts the worker in its own process group on
        # POSIX; killpg reaches its children too. Windows has no process
        # groups/SIGKILL, so fall back to terminating the process itself.
        if sys.platform != "win32":
            os.killpg(self._process.pid, signal.SIGKILL if hard else signal.SIGTERM)
        elif hard:
            self._process.kill()
        else:
            self._process.terminate()

    def finish_log(self, secrets: tuple[str, ...]) -> None:
        if not self._log.closed:
            self._log.flush()
            self._log.close()
        scrub_worker_log(self._log.name, secrets)


def scrub_worker_log(path: str | Path, secrets: tuple[str, ...]) -> None:
    log = Path(path)
    if not log.exists():
        return
    data = log.read_bytes()
    for secret in secrets:
        if secret:
            data = data.replace(secret.encode(), b"[REDACTED]")
    log.write_bytes(data)


def _paths(config: LiveSmokeRunConfig) -> LiveSmokePaths:
    run_dir = config.artifact_root / config.run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    provider = run_dir / "provider-output"
    provider.mkdir()
    return LiveSmokePaths(
        run_dir=run_dir,
        telemetry=run_dir / "agent-telemetry.jsonl",
        official_tools=run_dir / "agent-tool-calls.jsonl",
        worker_log=run_dir / "worker.log",
        remote_wav=run_dir / "received-agent-audio.wav",
        provider_dir=provider,
    )


def _events(path: Path, room_name: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("room") == room_name:
            events.append(event)
    return events


def _wav_metadata(path: Path) -> dict[str, Any] | None:
    if not path.exists() or path.stat().st_size <= 44:
        return None
    try:
        with wave.open(str(path), "rb") as stream:
            frames = stream.getnframes()
            rate = stream.getframerate()
    except (OSError, wave.Error):
        return None
    if frames <= 0 or rate <= 0:
        return None
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "duration_s": frames / rate,
        "file_bytes": path.stat().st_size,
    }


def compute_checks(
    paths: LiveSmokePaths,
    room_name: str,
    *,
    worker_running: bool,
    video_enabled: bool,
) -> tuple[dict[str, bool], dict[str, Any]]:
    events = _events(paths.telemetry, room_name)
    names = {str(row.get("event")) for row in events}
    provider_wavs = [path for path in paths.provider_dir.glob("*.wav") if path.stat().st_size > 44]
    remote = _wav_metadata(paths.remote_wav)
    official = False
    if paths.official_tools.exists():
        for line in paths.official_tools.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("room") == room_name and row.get("call"):
                official = True
                break
    sampled_video = any(
        row.get("event") == "sampled_video" and row.get("sampled") is True for row in events
    )
    checks = {
        "worker_stayed_running": worker_running,
        "remote_audio_received": remote is not None,
        "agent_output_wav_written": bool(provider_wavs),
        "audio_artifact_telemetry": "media_artifact" in names,
        "output_audio_frame_telemetry": "output_audio_frame" in names,
        "input_transcript_telemetry": "provider_input_audio_transcription" in names,
        "output_transcript_telemetry": "provider_output_audio_transcription" in names,
        "session_close_telemetry": "session_closed" in names,
        "tool_completed_telemetry": "tool_completed" in names,
        "official_tool_call_written": official,
        "sampled_video_telemetry": (sampled_video if video_enabled else False),
    }
    return checks, {
        "remote_wav": remote,
        "provider_wav": str(provider_wavs[0]) if provider_wavs else None,
        "telemetry_event_count": len(events),
    }


async def run_live_smoke(
    config: LiveSmokeRunConfig,
    *,
    worker_factory: WorkerFactory | None = None,
    backend_factory: BackendFactory | None = None,
) -> dict[str, Any]:
    """Execute one isolated smoke run; dependencies are injectable for offline tests."""
    validate_pcm16_wav(config.input_wav)
    paths = _paths(config)
    room_name = f"continuum-phase03-{uuid.uuid4().hex}"
    report: dict[str, Any] = {
        "schema": "continuum.phase03.live-smoke.v1",
        "run_id": config.run_id,
        "room_name": room_name,
        "run_directory": str(paths.run_dir),
        "executed": True,
        "official_score": False,
        "input_sha256": hashlib.sha256(config.input_wav.read_bytes()).hexdigest(),
        "paths": {
            "telemetry": str(paths.telemetry),
            "official_tool_calls": str(paths.official_tools),
            "remote_wav": str(paths.remote_wav),
            "provider_output_directory": str(paths.provider_dir),
        },
    }
    worker: Worker | None = None
    backend: RuntimeBackend | None = None
    remote: Pcm16WavRecorder | None = None
    worker_running = False
    error_type: str | None = None
    # Keep the public failure report actionable without serializing exception
    # text, which could contain transport URLs or provider diagnostics.
    failure_stage = "worker_startup"
    secrets = tuple(
        os.getenv(name, "")
        for name in (
            "LIVEKIT_URL",
            "LIVEKIT_API_KEY",
            "LIVEKIT_API_SECRET",
            "GOOGLE_API_KEY",
        )
    )
    try:
        selected_worker_factory: WorkerFactory = (
            worker_factory if worker_factory is not None else lambda value: SubprocessWorker(value)
        )
        active_worker = selected_worker_factory(paths)
        worker = active_worker
        deadline = time.monotonic() + config.worker_startup_s
        while time.monotonic() < deadline:
            if active_worker.poll() is not None:
                raise RuntimeError("worker exited during startup")
            await asyncio.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        factory = backend_factory or production_backend
        failure_stage = "backend_initialization"
        backend = await factory(paths)
        metadata = json.dumps({"scenario_id": "phase03-live-smoke"}, separators=(",", ":"))
        failure_stage = "room_creation"
        await backend.create_room(room_name, metadata)
        failure_stage = "caller_connection"
        await backend.connect_caller(room_name)
        failure_stage = "agent_join"
        await backend.wait_for_agent(config.timeout_s)
        failure_stage = "worker_health_before_media"
        if active_worker.poll() is not None:
            raise RuntimeError("worker exited before media publication")
        input_info = validate_pcm16_wav(config.input_wav)
        remote = Pcm16WavRecorder(
            paths.remote_wav,
            sample_rate=input_info["sample_rate"],
            channels=input_info["channels"],
        )
        failure_stage = "media_publication"
        await backend.publish_media(
            config.input_wav,
            remote,
            video_enabled=config.video_enabled,
            timeout_s=config.timeout_s,
        )
        failure_stage = "capture_finalization"
        await asyncio.sleep(config.capture_tail_s)
        worker_running = active_worker.poll() is None
    except Exception as exc:  # safe report deliberately excludes exception text
        error_type = type(exc).__name__
    finally:
        if backend is not None:
            with contextlib.suppress(Exception):
                await backend.disconnect()
            if config.finalization_s > 0:
                await asyncio.sleep(config.finalization_s)
            with contextlib.suppress(Exception):
                await backend.delete_room(room_name)
            with contextlib.suppress(Exception):
                await backend.close()
        if remote is not None:
            with contextlib.suppress(Exception):
                remote.close()
        if worker is not None:
            with contextlib.suppress(Exception):
                await worker.stop()
            with contextlib.suppress(Exception):
                worker.finish_log(secrets)


    checks, artifacts = compute_checks(
        paths, room_name, worker_running=worker_running, video_enabled=config.video_enabled
    )
    report["checks"] = checks
    report["artifacts"] = artifacts
    report["status"] = "passed" if all(value is True for value in checks.values()) else "failed"
    if error_type:
        report["error_type"] = error_type
        report["failure_stage"] = failure_stage
    return report


async def production_backend(paths: LiveSmokePaths) -> RuntimeBackend:
    try:
        from .livekit_smoke_backend import LiveKitSmokeBackend
    except ImportError as exc:
        raise RuntimeError("LiveKit FDB dependencies are not installed") from exc
    return LiveKitSmokeBackend(paths)

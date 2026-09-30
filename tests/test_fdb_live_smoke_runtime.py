from __future__ import annotations

import asyncio
import json
import wave

import pytest

from continuum.integrations.fdb.live_smoke_runtime import (
    LiveSmokeRunConfig,
    run_live_smoke,
    scrub_worker_log,
)
from continuum.integrations.fdb.media import Pcm16WavRecorder, pcm16_silence


class FakeWorker:
    def __init__(self, paths, events, *, exited=False):
        self.argv = ("python", "-m", "continuum.integrations.fdb.runner", "start")
        self.paths = paths; self.events = events; self.exited = exited
        paths.worker_log.write_text("worker diagnostic\n")
        events.append("worker_start")

    def poll(self): return 1 if self.exited else None
    async def stop(self): self.events.append("worker_stop")
    def finish_log(self, secrets):
        self.events.append("log_finish"); scrub_worker_log(self.paths.worker_log, secrets)


class FakeBackend:
    def __init__(self, paths, events, *, missing=(), join=True, audio=True, explode=False):
        self.paths = paths; self.events = events; self.missing = set(missing)
        self.join = join; self.audio = audio; self.explode = explode; self.room = ""

    def emit(self, name):
        if name not in self.missing:
            payload = {"room": self.room, "event": name}
            if name == "sampled_video":
                payload["sampled"] = True
            with self.paths.telemetry.open("a") as stream:
                stream.write(json.dumps(payload) + "\n")

    async def create_room(self, room_name, metadata):
        self.room = room_name; self.events.append(("create", json.loads(metadata)))
    async def connect_caller(self, room_name): self.events.append("connect")
    async def wait_for_agent(self, timeout_s):
        self.events.append("wait_agent")
        if not self.join:
            raise TimeoutError("not reported")
    async def publish_media(self, input_wav, remote_recorder, *, video_enabled, timeout_s):
        self.events.append(("publish", video_enabled))
        if self.explode:
            raise ValueError("SECRET transport failure")
        if self.audio:
            remote_recorder.write(pcm16_silence(160), sample_rate=16000, channels=1, samples_per_channel=160)
        provider = Pcm16WavRecorder(self.paths.provider_dir / "provider.wav", sample_rate=16000, channels=1)
        provider.write(pcm16_silence(160), sample_rate=16000, channels=1, samples_per_channel=160); provider.close()
        for event in ("media_artifact", "output_audio_frame", "provider_input_audio_transcription", "provider_output_audio_transcription", "tool_completed", "sampled_video"):
            self.emit(event)
        if "official" not in self.missing:
            self.paths.official_tools.write_text(
                json.dumps({"room": self.room, "call": {"function": "track_order"}}) + "\n"
            )
        return self.audio
    async def disconnect(self): self.events.append("disconnect"); self.emit("session_closed")
    async def delete_room(self, room_name): self.events.append("delete")
    async def close(self): self.events.append("api_close")


def input_wav(path):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
        stream.writeframes(pcm16_silence(160))


async def execute(tmp_path, *, missing=(), join=True, audio=True, explode=False, worker_exited=False):
    source = tmp_path / "input.wav"; input_wav(source); events = []
    cfg = LiveSmokeRunConfig(
        "run-unique",
        source,
        tmp_path / "runs",
        worker_startup_s=.001,
        timeout_s=.01,
        capture_tail_s=0,
        finalization_s=0,
    )
    def worker(paths): return FakeWorker(paths, events, exited=worker_exited)
    async def backend(paths): return FakeBackend(paths, events, missing=missing, join=join, audio=audio, explode=explode)
    return await run_live_smoke(cfg, worker_factory=worker, backend_factory=backend), events


@pytest.mark.asyncio
async def test_fake_happy_path_runs_complete_lifecycle(tmp_path):
    report, events = await execute(tmp_path)
    assert report["status"] == "passed" and all(report["checks"].values()), report
    assert ("create", {"scenario_id": "phase03-live-smoke"}) in events
    assert ("publish", True) in events
    assert events[-5:] == ["disconnect", "delete", "api_close", "worker_stop", "log_finish"]


@pytest.mark.asyncio
async def test_worker_early_exit_is_safe_failure(tmp_path):
    report, events = await execute(tmp_path, worker_exited=True)
    assert report["status"] == "failed" and report["error_type"] == "RuntimeError"
    assert report["failure_stage"] == "worker_startup"
    assert "worker_stop" in events


@pytest.mark.asyncio
async def test_agent_join_timeout_cleans_up(tmp_path):
    report, events = await execute(tmp_path, join=False)
    assert report["error_type"] == "TimeoutError"
    assert report["failure_stage"] == "agent_join"
    assert "delete" in events and "worker_stop" in events


@pytest.mark.asyncio
async def test_no_remote_audio_fails(tmp_path):
    report, _ = await execute(tmp_path, audio=False)
    assert report["checks"]["remote_audio_received"] is False and report["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("event,check", [
    ("provider_input_audio_transcription", "input_transcript_telemetry"),
    ("provider_output_audio_transcription", "output_transcript_telemetry"),
    ("tool_completed", "tool_completed_telemetry"),
    ("sampled_video", "sampled_video_telemetry"),
])
async def test_required_current_run_event_cannot_be_missing(tmp_path, event, check):
    report, _ = await execute(tmp_path, missing=(event,))
    assert report["checks"][check] is False and report["status"] == "failed"


@pytest.mark.asyncio
async def test_official_log_is_required(tmp_path):
    report, _ = await execute(tmp_path, missing=("official",))
    assert report["checks"]["official_tool_call_written"] is False


@pytest.mark.asyncio
async def test_stale_artifacts_cannot_be_reused(tmp_path):
    stale = tmp_path / "runs" / "old"; stale.mkdir(parents=True)
    (stale / "agent-tool-calls.jsonl").write_text("stale")
    report, _ = await execute(tmp_path, missing=("official",))
    assert report["checks"]["official_tool_call_written"] is False


@pytest.mark.asyncio
async def test_runtime_exception_reports_type_only(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "SECRET")
    report, _ = await execute(tmp_path, explode=True)
    encoded = json.dumps(report)
    assert report["error_type"] == "ValueError"
    assert report["failure_stage"] == "media_publication"
    assert "transport failure" not in encoded and "SECRET" not in encoded


def test_worker_log_scrubs_all_secrets(tmp_path):
    path = tmp_path / "worker.log"; path.write_text("diag one two three four remains")
    scrub_worker_log(path, ("one", "two", "three", "four"))
    assert path.read_text() == "diag [REDACTED] [REDACTED] [REDACTED] [REDACTED] remains"


def test_worker_argv_contains_no_credentials(tmp_path):
    events = []; root = tmp_path / "r"; root.mkdir(); provider = root / "p"; provider.mkdir()
    from continuum.integrations.fdb.live_smoke_runtime import LiveSmokePaths
    paths = LiveSmokePaths(root, root/"t", root/"o", root/"w", root/"r.wav", provider)
    worker = FakeWorker(paths, events)
    assert not any("key" in arg.lower() or "secret" in arg.lower() for arg in worker.argv)


def test_live_smoke_defers_room_creation_to_the_auto_dispatched_caller():
    """RoomService creation does not generate an unnamed-agent job."""
    pytest.importorskip("livekit")
    from types import SimpleNamespace

    from continuum.integrations.fdb.livekit_smoke_backend import LiveKitSmokeBackend

    class RoomServiceMustNotBeCalled:
        async def create_room(self, request):
            del request
            raise AssertionError("automatic dispatch requires caller-created room")

    backend = object.__new__(LiveKitSmokeBackend)
    backend.api = SimpleNamespace(room=RoomServiceMustNotBeCalled())
    asyncio.run(backend.create_room("phase03-room", '{"scenario_id":"phase03-live-smoke"}'))

    assert backend.room_name == "phase03-room"

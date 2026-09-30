from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from continuum.integrations.fdb.config import FdbAgentConfig, FdbConfigurationError
from continuum.integrations.fdb.provider import ProviderConfigurationError, selected_provider


def test_config_reads_non_secret_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_LIVE_MODEL", "test-native-audio")
    monkeypatch.setenv("GOOGLE_VOICE", "Aoede")
    monkeypatch.setenv("FDB_LATENCY_PROFILE", "fast")
    config = FdbAgentConfig.from_env()
    assert config.model == "test-native-audio"
    assert config.voice == "Aoede"
    assert config.latency_profile == "fast"


def test_livekit_preflight_names_missing_variables_without_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(FdbConfigurationError) as caught:
        FdbAgentConfig().validate_livekit_environment()
    assert "LIVEKIT_URL" in str(caught.value)
    assert "LIVEKIT_API_SECRET" in str(caught.value)


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ProviderConfigurationError, match="unsupported"):
        selected_provider("invented")


def test_fdb_agent_uses_automatic_dispatch_for_the_upstream_runner() -> None:
    """The upstream client joins rooms but does not create a named dispatch."""
    pytest.importorskip("livekit.agents")
    from continuum.integrations.fdb.agent import server

    # The LiveKit SDK stores the decorator's public ``agent_name`` option here.
    # An empty name is the documented automatic-dispatch mode.
    assert server._agent_name == ""  # type: ignore[attr-defined]


def test_fdb_smoke_worker_can_opt_into_named_dispatch() -> None:
    """The isolated smoke subprocess uses explicit dispatch, not auto mode."""
    pytest.importorskip("livekit.agents")
    env = os.environ | {"CONTINUUM_FDB_AGENT_NAME": "continuum-fdb-smoke"}
    stdout = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "from continuum.integrations.fdb.agent import server; print(server._agent_name)",
        ],
        env=env,
        text=True,
    )
    assert stdout.strip() == "continuum-fdb-smoke"


@pytest.mark.asyncio
async def test_agent_session_start_connects_the_job_context_in_order() -> None:
    """Regression guard for the Phase 3 agent-join timeout."""
    pytest.importorskip("livekit.agents")
    from continuum.integrations.fdb.agent import _start_session_and_connect

    calls: list[str] = []
    events: list[tuple[str, dict[str, object]]] = []

    class FakeSession:
        async def start(self, **kwargs: object) -> None:
            assert kwargs["room"] is context.room
            calls.append("session.start")

    class FakeContext:
        room = SimpleNamespace(name="phase03-test-room")

        async def connect(self) -> None:
            calls.append("ctx.connect")

    class FakeTelemetry:
        def emit(self, event: str, **data: object) -> None:
            events.append((event, data))

    context = FakeContext()
    await _start_session_and_connect(
        context,  # type: ignore[arg-type]
        FakeSession(),  # type: ignore[arg-type]
        agent=object(),  # type: ignore[arg-type]
        room_input_options=object(),  # type: ignore[arg-type]
        room_output_options=object(),  # type: ignore[arg-type]
        telemetry=FakeTelemetry(),  # type: ignore[arg-type]
    )

    assert calls == ["session.start", "ctx.connect"]
    assert [event for event, _ in events] == [
        "session_starting",
        "session_started",
        "session_connecting",
        "session_listening",
    ]


@pytest.mark.asyncio
async def test_agent_session_start_failure_is_sanitized_before_room_connect() -> None:
    pytest.importorskip("livekit.agents")
    from continuum.integrations.fdb.agent import _start_session_and_connect

    calls: list[str] = []
    events: list[tuple[str, dict[str, object]]] = []

    class FailingSession:
        async def start(self, **kwargs: object) -> None:
            del kwargs
            calls.append("session.start")
            raise RuntimeError("sensitive transport diagnostic")

    class FakeContext:
        room = SimpleNamespace(name="phase03-test-room")

        async def connect(self) -> None:
            calls.append("ctx.connect")

    class FakeTelemetry:
        def emit(self, event: str, **data: object) -> None:
            events.append((event, data))

    with pytest.raises(RuntimeError, match="sensitive transport diagnostic"):
        await _start_session_and_connect(
            FakeContext(),  # type: ignore[arg-type]
            FailingSession(),  # type: ignore[arg-type]
            agent=object(),  # type: ignore[arg-type]
            room_input_options=object(),  # type: ignore[arg-type]
            room_output_options=object(),  # type: ignore[arg-type]
            telemetry=FakeTelemetry(),  # type: ignore[arg-type]
        )

    assert calls == ["session.start"]
    assert events == [
        ("session_starting", {}),
        ("session_start_failed", {"error_type": "RuntimeError"}),
    ]

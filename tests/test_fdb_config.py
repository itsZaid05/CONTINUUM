from __future__ import annotations

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

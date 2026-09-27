"""Environment-only configuration for the FDB-managed LiveKit worker."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .media import MediaConfig
from .provider import NativeAudioConfig


class FdbConfigurationError(RuntimeError):
    """Missing or invalid worker configuration, without exposing secret values."""


@dataclass(frozen=True)
class FdbAgentConfig:
    provider: str = "gemini_native_audio"
    model: str = "gemini-2.5-flash-native-audio-preview-12-2025"
    voice: str = "Puck"
    language: str | None = None
    latency_profile: str = "instant"
    telemetry_path: str = "/tmp/continuum_fdb_telemetry.jsonl"
    official_tool_log_path: str = "/tmp/agent_tool_calls.log"
    shutdown_timeout_s: float = 5.0
    media: MediaConfig = MediaConfig()

    @classmethod
    def from_env(cls) -> FdbAgentConfig:
        raw_timeout = os.getenv("CONTINUUM_FDB_SHUTDOWN_TIMEOUT_S", "5")
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise FdbConfigurationError(
                "CONTINUUM_FDB_SHUTDOWN_TIMEOUT_S must be a number"
            ) from exc
        if timeout < 0:
            raise FdbConfigurationError("CONTINUUM_FDB_SHUTDOWN_TIMEOUT_S cannot be negative")
        return cls(
            provider=os.getenv("CONTINUUM_MEDIA_PROVIDER", "gemini_native_audio"),
            model=os.getenv("GEMINI_LIVE_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025"),
            voice=os.getenv("GOOGLE_VOICE", "Puck"),
            language=os.getenv("GEMINI_LIVE_LANGUAGE") or None,
            latency_profile=os.getenv("FDB_LATENCY_PROFILE", "instant"),
            telemetry_path=os.getenv(
                "CONTINUUM_TELEMETRY_PATH", "/tmp/continuum_fdb_telemetry.jsonl"
            ),
            official_tool_log_path=os.getenv("FDB_TOOL_LOG_PATH", "/tmp/agent_tool_calls.log"),
            shutdown_timeout_s=timeout,
            media=MediaConfig.from_env(),
        )

    def validate_livekit_environment(self) -> None:
        required = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise FdbConfigurationError(
                "missing LiveKit environment variables: " + ", ".join(missing)
            )

    def native_audio(self) -> NativeAudioConfig:
        return NativeAudioConfig(
            model=self.model, voice=self.voice, language=self.language, media=self.media
        )

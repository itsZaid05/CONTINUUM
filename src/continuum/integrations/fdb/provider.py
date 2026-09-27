"""Provider-neutral native-audio model factory for the LiveKit edge."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .media import MediaConfig


class ProviderConfigurationError(RuntimeError):
    """Raised when a selected media provider lacks required configuration."""


@dataclass(frozen=True)
class NativeAudioConfig:
    model: str = "gemini-2.5-flash-native-audio-preview-12-2025"
    voice: str = "Puck"
    language: str | None = None
    temperature: float | None = None
    media: MediaConfig = MediaConfig()


@runtime_checkable
class NativeAudioProvider(Protocol):
    """Boundary allowing an official realtime provider to be substituted."""

    @property
    def name(self) -> str: ...

    def validate_environment(self) -> None: ...

    def build_model(self, config: NativeAudioConfig) -> Any: ...


class GeminiNativeAudioProvider:
    """Google Gemini Live native-audio implementation selected for FDB-v3."""

    @property
    def name(self) -> str:
        return "gemini_native_audio"

    def validate_environment(self) -> None:
        # Vertex AI is also supported by the plugin, but this benchmark entry
        # point intentionally uses the simple official GOOGLE_API_KEY route.
        if not os.getenv("GOOGLE_API_KEY"):
            raise ProviderConfigurationError(
                "Gemini native audio requires GOOGLE_API_KEY in the environment"
            )

    def build_model(self, config: NativeAudioConfig) -> Any:
        self.validate_environment()
        try:
            from google.genai import types
            from livekit.agents.utils import images
            from livekit.plugins import google
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ProviderConfigurationError(
                "Gemini support is not installed; sync the project with the 'fdb' extra"
            ) from exc

        transcription = types.AudioTranscriptionConfig(
            language_codes=[config.language] if config.language else None,
            word_timestamp=config.media.requested_word_timestamps,
            mode=types.AudioTranscriptionConfigMode.VERBATIM,
        )
        activity = types.AutomaticActivityDetection(
            disabled=False,
            start_of_speech_sensitivity=types.StartSensitivity(
                config.media.vad_start_sensitivity
            ),
            end_of_speech_sensitivity=types.EndSensitivity(config.media.vad_end_sensitivity),
            prefix_padding_ms=config.media.vad_prefix_padding_ms,
            silence_duration_ms=config.media.vad_silence_duration_ms,
        )
        kwargs: dict[str, Any] = {
            "model": config.model,
            "voice": config.voice,
            "modalities": [types.Modality.AUDIO],
            "input_audio_transcription": transcription,
            "output_audio_transcription": transcription,
            "realtime_input_config": types.RealtimeInputConfig(
                automatic_activity_detection=activity,
                activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
                turn_coverage=(
                    types.TurnCoverage.TURN_INCLUDES_AUDIO_ACTIVITY_AND_ALL_VIDEO
                    if config.media.video_enabled
                    else types.TurnCoverage.TURN_INCLUDES_ONLY_ACTIVITY
                ),
            ),
            "image_encode_options": images.EncodeOptions(
                format="JPEG", quality=config.media.jpeg_quality
            ),
        }
        if config.language:
            kwargs["language"] = config.language
        if config.temperature is not None:
            kwargs["temperature"] = config.temperature
        return google.realtime.RealtimeModel(**kwargs)


def selected_provider(name: str = "gemini_native_audio") -> NativeAudioProvider:
    normalized = name.strip().lower()
    if normalized in {"gemini", "gemini2_5", "gemini_native_audio"}:
        return GeminiNativeAudioProvider()
    raise ProviderConfigurationError(
        f"unsupported native-audio provider {name!r}; available: gemini_native_audio"
    )

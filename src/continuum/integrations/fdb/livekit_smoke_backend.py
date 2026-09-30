"""Official LiveKit implementation for the Phase 3 smoke runtime."""

from __future__ import annotations

import asyncio
import os
import wave
from datetime import timedelta
from typing import Any

# The LiveKit wheels expose these dynamically from extension modules, while
# their partial type stubs omit several runtime members used by this adapter.
# Contain that untyped SDK boundary here; the rest of the smoke runtime keeps
# its regular protocol-checked interface.
from livekit import api as _livekit_api
from livekit import rtc as _livekit_rtc

from .live_smoke_runtime import LiveSmokePaths
from .media import Pcm16WavRecorder

api: Any = _livekit_api
rtc: Any = _livekit_rtc


class LiveKitSmokeBackend:
    def __init__(self, paths: LiveSmokePaths) -> None:
        self.paths = paths
        self.api = api.LiveKitAPI(
            url=os.environ["LIVEKIT_URL"],
            api_key=os.environ["LIVEKIT_API_KEY"],
            api_secret=os.environ["LIVEKIT_API_SECRET"],
        )
        self.room = rtc.Room()
        self.room_name: str | None = None
        self.agent_joined = asyncio.Event()
        self.remote_audio = asyncio.Event()
        self._audio_tasks: set[asyncio.Task[Any]] = set()
        self._audio_streams: list[Any] = []
        self._remote_recorder: Pcm16WavRecorder | None = None
        self._audio_source: Any = None
        self._video_source: Any = None
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self.room.on("participant_connected")
        def participant_connected(participant: Any) -> None:
            if participant.identity != "phase03-caller":
                self.agent_joined.set()

        @self.room.on("track_subscribed")
        def track_subscribed(track: Any, publication: Any, participant: Any) -> None:
            del publication, participant
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
                self._audio_streams.append(stream)
                task = asyncio.create_task(self._capture_audio(stream))
                self._audio_tasks.add(task)
                task.add_done_callback(self._audio_tasks.discard)

        @self.room.on("transcription_received")
        def transcription_received(segments: list[Any], participant: Any, publication: Any) -> None:
            # Segment contents intentionally stay in LiveKit/agent telemetry, not the smoke report.
            del participant, publication
            if segments:
                pass

    async def _capture_audio(self, stream: Any) -> None:
        async for event in stream:
            frame = event.frame
            if self._remote_recorder is None or len(frame.data) == 0:
                continue
            self._remote_recorder.write(
                bytes(frame.data),
                sample_rate=frame.sample_rate,
                channels=frame.num_channels,
                samples_per_channel=frame.samples_per_channel,
            )
            self.remote_audio.set()

    async def create_room(self, room_name: str, metadata: str) -> None:
        self.room_name = room_name
        dispatch = api.RoomAgentDispatch(agent_name="continuum-fdb", metadata=metadata)
        await self.api.room.create_room(
            api.CreateRoomRequest(
                name=room_name,
                empty_timeout=60,
                departure_timeout=30,
                metadata=metadata,
                agents=[dispatch],
            )
        )

    async def connect_caller(self, room_name: str) -> None:
        token = (
            api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
            .with_identity("phase03-caller")
            .with_ttl(timedelta(minutes=5))
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=False,
                )
            )
            .to_jwt()
        )
        await self.room.connect(os.environ["LIVEKIT_URL"], token)
        for participant in self.room.remote_participants.values():
            if participant.identity != "phase03-caller":
                self.agent_joined.set()

    async def wait_for_agent(self, timeout_s: float) -> None:
        await asyncio.wait_for(self.agent_joined.wait(), timeout=timeout_s)

    async def publish_media(
        self,
        input_wav: Any,
        remote_recorder: Pcm16WavRecorder,
        *,
        video_enabled: bool,
        timeout_s: float,
    ) -> bool:
        self._remote_recorder = remote_recorder
        with wave.open(str(input_wav), "rb") as source_wav:
            rate = source_wav.getframerate()
            channels = source_wav.getnchannels()
            self._audio_source = rtc.AudioSource(rate, channels)
            audio_track = rtc.LocalAudioTrack.create_audio_track(
                "phase03-microphone", self._audio_source
            )
            audio_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            await self.room.local_participant.publish_track(audio_track, audio_options)

            video_track = None
            if video_enabled:
                self._video_source = rtc.VideoSource(320, 180)
                video_track = rtc.LocalVideoTrack.create_video_track(
                    "phase03-camera", self._video_source
                )
                video_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
                await self.room.local_participant.publish_track(video_track, video_options)

            chunk = max(1, rate // 50)
            frame_number = 0
            while pcm := source_wav.readframes(chunk):
                samples = len(pcm) // (channels * 2)
                await self._audio_source.capture_frame(
                    rtc.AudioFrame(
                        data=pcm,
                        sample_rate=rate,
                        num_channels=channels,
                        samples_per_channel=samples,
                    )
                )
                if self._video_source is not None and frame_number % 10 == 0:
                    pixel = bytes((frame_number % 256, 64, 192, 255))
                    data = pixel * (320 * 180)
                    self._video_source.capture_frame(
                        rtc.VideoFrame(320, 180, rtc.VideoBufferType.RGBA, data)
                    )
                frame_number += 1
            await self._audio_source.wait_for_playout()
        await asyncio.wait_for(self.remote_audio.wait(), timeout=timeout_s)
        return True

    async def disconnect(self) -> None:
        for stream in self._audio_streams:
            await stream.aclose()
        for task in tuple(self._audio_tasks):
            task.cancel()
        if self._audio_tasks:
            await asyncio.gather(*tuple(self._audio_tasks), return_exceptions=True)
        if self._audio_source is not None:
            await self._audio_source.aclose()
        if self._video_source is not None:
            await self._video_source.aclose()
        await self.room.disconnect()

    async def delete_room(self, room_name: str) -> None:
        await self.api.room.delete_room(api.DeleteRoomRequest(room=room_name))

    async def close(self) -> None:
        await self.api.aclose()

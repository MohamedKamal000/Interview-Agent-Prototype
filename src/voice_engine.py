import asyncio
import contextlib
import logging
from queue import Empty, Queue
from threading import Thread

import alsaaudio
import numpy as np
from livekit import rtc

from .state import AppState

logger = logging.getLogger("voice_engine")

SAMPLE_RATE = 24000
CHANNELS = 1
BLOCK_SIZE = 960


class AudioManager:
    def __init__(self):
        self.room: rtc.Room | None = None
        self.audio_source: rtc.AudioSource | None = None
        self._running = False
        self._mic_pcm: alsaaudio.PCM | None = None
        self._speaker_pcm: alsaaudio.PCM | None = None
        self._playback_queue: Queue = Queue()
        self._mic_available = True
        self._speaker_available = True
        self._mic_task: asyncio.Task | None = None
        self._speaker_thread: Thread | None = None

    async def start(self, state: AppState):
        self._running = True
        self._mic_available = True
        self._speaker_available = True
        loop = asyncio.get_running_loop()

        self.audio_source = rtc.AudioSource(
            SAMPLE_RATE, CHANNELS, queue_size_ms=30000, loop=loop
        )
        track = rtc.LocalAudioTrack.create_audio_track("mic", self.audio_source)
        await self.room.local_participant.publish_track(
            track,
            options=rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )

        self._setup_mic()
        self._setup_speaker()

        if self._mic_available:
            self._mic_task = asyncio.create_task(self._mic_capture_loop(state))
        if self._speaker_available:
            self._play_test_tone()
            self._speaker_thread = Thread(target=self._speaker_loop, daemon=True)
            self._speaker_thread.start()

    def _setup_mic(self):
        try:
            self._mic_pcm = alsaaudio.PCM(
                type=alsaaudio.PCM_CAPTURE,
                device="default",
                mode=alsaaudio.PCM_NONBLOCK,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                format=alsaaudio.PCM_FORMAT_S16_LE,
                periodsize=BLOCK_SIZE,
            )
            logger.info("Mic initialized")
        except Exception as e:
            logger.warning("Mic not available: %s", e)
            self._mic_available = False

    def _setup_speaker(self):
        try:
            self._speaker_pcm = alsaaudio.PCM(
                type=alsaaudio.PCM_PLAYBACK,
                device="default",
                mode=alsaaudio.PCM_NONBLOCK,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                format=alsaaudio.PCM_FORMAT_S16_LE,
                periodsize=BLOCK_SIZE,
            )
            logger.info("Speaker initialized")
        except Exception as e:
            logger.warning("Speaker not available: %s", e)
            self._speaker_available = False

    def _play_test_tone(self):
        try:
            duration = 0.3
            t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
            tone = (np.sin(2 * np.pi * 440 * t) * 0.3 * 32767).astype(np.int16)
            data = tone.tobytes()
            chunk = SAMPLE_RATE // 10
            for i in range(0, len(data), chunk):
                self._speaker_pcm.write(data[i : i + chunk])
            logger.info("Test tone played (440Hz, %.1fs)", duration)
        except Exception as e:
            logger.warning("Test tone failed: %s", e)

    async def _mic_capture_loop(self, state: AppState):
        while self._running:
            if not self._mic_pcm:
                await asyncio.sleep(0.1)
                continue
            try:
                length, data = self._mic_pcm.read()
            except OSError:
                await asyncio.sleep(0.01)
                continue
            if length <= 0 or not data:
                state.mic_level = 0.0
                await asyncio.sleep(0.005)
                continue
            audio_array = np.frombuffer(data, dtype=np.int16)
            level = float(np.sqrt(np.mean(audio_array.astype(np.float32) ** 2)))
            state.mic_level = level
            if not state.mic_muted and self.audio_source:
                frame = rtc.AudioFrame(
                    data=memoryview(data),
                    sample_rate=SAMPLE_RATE,
                    num_channels=CHANNELS,
                    samples_per_channel=len(audio_array),
                )
                await self.audio_source.capture_frame(frame)

    async def play_agent_audio(self, track: rtc.AudioTrack):
        logger.info("Starting agent audio stream for track %s", track.sid)
        stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=CHANNELS)
        frame_count = 0
        async for event in stream:
            if not self._running:
                break
            frame_count += 1
            data = bytes(event.frame.data)
            if len(data) > 0:
                self._playback_queue.put(data)
                if frame_count % 50 == 0:
                    logger.info(
                        "Agent audio: %d frames, %d bytes each",
                        frame_count,
                        len(data),
                    )
        logger.info("Agent audio stream ended after %d frames", frame_count)

    def _speaker_loop(self):
        written = 0
        while self._running:
            try:
                data = self._playback_queue.get(timeout=0.1)
                n = self._speaker_pcm.write(data)
                written += n
                if written % (SAMPLE_RATE * 2) < n:
                    logger.info("Speaker: wrote %d bytes so far", written)
            except Empty:
                pass
            except OSError as e:
                logger.warning("Speaker OSError: %s", e)
            except Exception as e:
                logger.error("Speaker error: %s", e)

    async def stop(self):
        self._running = False
        if self._mic_task:
            self._mic_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._mic_task
            self._mic_task = None
        if self._mic_pcm:
            self._mic_pcm.close()
        if self._speaker_pcm:
            self._speaker_pcm.close()
        self.audio_source = None

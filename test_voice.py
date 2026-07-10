import argparse
import asyncio
import logging
import os
import random
import string
from queue import Empty, Queue
from threading import Thread

import alsaaudio
import numpy as np
from dotenv import load_dotenv
from evdev import InputDevice, ecodes, list_devices
from livekit import rtc
from livekit.api import (
    AccessToken,
    CreateRoomRequest,
    LiveKitAPI,
    RoomAgentDispatch,
    VideoGrants,
)
from livekit.rtc import TrackPublishOptions, TrackSource

load_dotenv(".env.local")

logger = logging.getLogger("test-voice")

SAMPLE_RATE = 24000
CHANNELS = 1
BLOCK_SIZE = 960


class AudioIO:
    def __init__(self):
        self._running = False
        self._mic_pcm: alsaaudio.PCM | None = None
        self._speaker_pcm: alsaaudio.PCM | None = None
        self._playback_queue: Queue = Queue()
        self._mic_available = True
        self._speaker_available = True

    def start(self):
        self._running = True
        self._setup_mic()
        self._setup_speaker()
        if self._speaker_available:
            Thread(target=self._speaker_loop, daemon=True).start()
        logger.info(
            "AudioIO started (mic=%s, speaker=%s)",
            self._mic_available,
            self._speaker_available,
        )

    def stop(self):
        self._running = False
        if self._mic_pcm:
            self._mic_pcm.close()
        if self._speaker_pcm:
            self._speaker_pcm.close()

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

    def play_test_tone(self):
        if not self._speaker_available:
            return
        try:
            duration = 0.3
            t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
            tone = (np.sin(2 * np.pi * 440 * t) * 0.3 * 32767).astype(np.int16)
            data = tone.tobytes()
            chunk = SAMPLE_RATE // 10
            for i in range(0, len(data), chunk):
                self._speaker_pcm.write(data[i : i + chunk])
            logger.info("Test tone played (440Hz)")
        except Exception as e:
            logger.warning("Test tone failed: %s", e)

    def play_audio_data(self, data: bytes):
        self._playback_queue.put(data)

    def _speaker_loop(self):
        while self._running:
            try:
                data = self._playback_queue.get(timeout=0.1)
                self._speaker_pcm.write(data)
            except Empty:
                pass
            except OSError as e:
                logger.warning("Speaker OSError: %s", e)
            except Exception as e:
                logger.error("Speaker error: %s", e)


class PTTState:
    def __init__(self):
        self.active = False


def find_keyboard() -> str:
    for path in list_devices():
        try:
            device = InputDevice(path)
            caps = device.capabilities()
            if ecodes.EV_KEY in caps and ecodes.KEY_SPACE in caps[ecodes.EV_KEY]:
                return path
        except Exception:
            continue
    raise RuntimeError("No keyboard device with SPACE key found")


async def keyboard_listener(ptt: PTTState) -> None:
    try:
        path = find_keyboard()
        device = InputDevice(path)
        logger.info("Keyboard device: %s (%s)", device.name, path)
        async for event in device.async_read_loop():
            if event.type == ecodes.EV_KEY and event.code == ecodes.KEY_SPACE:
                if event.value == 1 and not ptt.active:
                    ptt.active = True
                    print("  PTT ACTIVE - transmitting microphone audio")
                elif event.value == 0 and ptt.active:
                    ptt.active = False
                    print("  PTT INACTIVE - microphone muted")
    except asyncio.CancelledError:
        return
    except PermissionError:
        logger.error(
            "Cannot access input devices. "
            "Grant read access: sudo usermod -a -G input $USER && reboot"
        )
    except Exception as e:
        logger.error("Keyboard listener failed: %s", e)


async def play_agent_audio(audio_io: AudioIO, track: rtc.AudioTrack):
    logger.info("=== play_agent_audio: starting stream for track %s", track.sid)
    try:
        stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=CHANNELS)
    except Exception as e:
        logger.error("Failed to create AudioStream: %s", e)
        return
    frame_count = 0
    try:
        async for event in stream:
            frame_count += 1
            data = bytes(event.frame.data)
            if data:
                audio_io.play_audio_data(data)
                if frame_count % 500 == 0:
                    logger.info(
                        "=== Agent audio: frame %d, %d bytes",
                        frame_count,
                        len(data),
                    )
    except Exception as e:
        logger.error("AudioStream error after %d frames: %s", frame_count, e)
    logger.info("=== play_agent_audio: stream ended after %d frames", frame_count)


async def main():
    parser = argparse.ArgumentParser(description="Test LiveKit voice agent interaction")
    parser.add_argument("--topic", default="Python programming", help="Interview topic")
    parser.add_argument("--room", default=None, help="Room name (optional)")
    parser.add_argument(
        "--lk-url",
        default=os.getenv("LIVEKIT_URL", "ws://localhost:7880"),
        help="LiveKit server URL",
    )
    parser.add_argument(
        "--lk-api-key",
        default=os.getenv("LIVEKIT_API_KEY", "devkey"),
        help="LiveKit API key",
    )
    parser.add_argument(
        "--lk-api-secret",
        default=os.getenv("LIVEKIT_API_SECRET", "secret"),
        help="LiveKit API secret",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)-12s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    audio = AudioIO()
    audio.start()
    audio.play_test_tone()

    room = rtc.Room()
    background_tasks: set[asyncio.Task] = set()

    @room.on("connected")
    def on_connected():
        logger.info("=== EVENT: connected to room")

    @room.on("disconnected")
    def on_disconnected():
        logger.info("=== EVENT: disconnected from room")

    @room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant):
        logger.info(
            "=== EVENT: participant_connected: identity=%s kind=%s",
            participant.identity,
            participant.kind,
        )

    @room.on("track_published")
    def on_track_published(
        publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant
    ):
        logger.info(
            "=== EVENT: track_published: sid=%s kind=%s from=%s",
            publication.sid,
            publication.kind,
            participant.identity,
        )

    @room.on("track_subscribed")
    def on_track_subscribed(track, publication, participant):
        if participant == room.local_participant:
            return
        logger.info(
            "=== EVENT: track_subscribed: sid=%s type=%s from=%s",
            track.sid,
            type(track).__name__,
            participant.identity,
        )
        if isinstance(track, rtc.AudioTrack):
            logger.info("=== Starting agent audio playback for track %s", track.sid)
            task = asyncio.create_task(play_agent_audio(audio, track))
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)

    @room.on("transcription_received")
    def on_transcription(segments, participant, publication):
        for seg in segments:
            who = participant.identity if participant else "agent"
            marker = "[FINAL]" if seg.final else "[INTERIM]"
            print(f"  {marker} {who}: {seg.text}")

    # --- Connect to LiveKit ---
    lk_url = args.lk_url
    api_url = lk_url.replace("ws://", "http://").replace("wss://", "https://")
    api_key = args.lk_api_key
    api_secret = args.lk_api_secret

    room_slug = args.topic.lower().replace(" ", "-").replace("/", "-")
    room_slug = "".join(c for c in room_slug if c.isalnum() or c == "-")
    room_name = (
        args.room
        or f"test-{room_slug}-{''.join(random.choices(string.ascii_lowercase, k=4))}"
    )

    print(f"\n  Connecting to {lk_url}")
    print(f"  Room: {room_name}")
    print(f"  Topic: {args.topic}")
    print("  Agent will greet you when it joins...\n")

    try:
        async with LiveKitAPI(api_url, api_key, api_secret) as api:
            create_req = CreateRoomRequest(
                name=room_name,
                agents=[RoomAgentDispatch(agent_name="interview-agent")],
            )
            await api.room.create_room(create_req)

            token = (
                AccessToken(api_key, api_secret)
                .with_identity(
                    f"candidate-{''.join(random.choices(string.ascii_lowercase, k=6))}"
                )
                .with_name("Candidate")
                .with_grants(
                    VideoGrants(
                        room_join=True,
                        room=room_name,
                        can_publish=True,
                        can_subscribe=True,
                    )
                )
                .to_jwt()
            )

        ws_url = lk_url
        await room.connect(ws_url, token)
        logger.info("Connected to room")

        # Give agent time to set up RoomIO and register event handlers
        logger.info("Waiting 3s for agent RoomIO to initialize...")
        await asyncio.sleep(3)
        logger.info("Now publishing mic track and starting capture")

        source = rtc.AudioSource(
            SAMPLE_RATE, CHANNELS, queue_size_ms=30000, loop=asyncio.get_running_loop()
        )
        mic_track = rtc.LocalAudioTrack.create_audio_track("mic", source)
        await room.local_participant.publish_track(
            mic_track,
            options=TrackPublishOptions(source=TrackSource.SOURCE_MICROPHONE),
        )
        logger.info("Published mic track")

        mic_pcm = audio._mic_pcm if audio._mic_available else None
        mic_frames_published = 0

        # --- Push-to-talk ---
        ptt = PTTState()
        listener_task = asyncio.create_task(keyboard_listener(ptt))
        background_tasks.add(listener_task)
        listener_task.add_done_callback(background_tasks.discard)
        print("  PTT INACTIVE - hold Space to talk")

        async def mic_capture():
            nonlocal mic_frames_published
            while True:
                if not audio._mic_available:
                    await asyncio.sleep(0.1)
                    continue

                try:
                    length, data = mic_pcm.read()
                    if length > 0 and data:
                        audio_array = np.frombuffer(data, dtype=np.int16)
                    else:
                        await asyncio.sleep(0.005)
                        continue
                except OSError:
                    await asyncio.sleep(0.005)
                    continue
                except Exception as e:
                    logger.error("Mic capture error: %s", e)
                    await asyncio.sleep(0.1)
                    continue

                if ptt.active:
                    frame = rtc.AudioFrame(
                        data=memoryview(data),
                        sample_rate=SAMPLE_RATE,
                        num_channels=CHANNELS,
                        samples_per_channel=len(audio_array),
                    )
                    try:
                        await source.capture_frame(frame)
                        mic_frames_published += 1
                        if mic_frames_published % 500 == 0:
                            logger.info("Published %d mic frames", mic_frames_published)
                    except Exception:
                        await asyncio.sleep(0.05)

        task = asyncio.create_task(mic_capture())
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        logger.info("Mic capture task started")

        await task

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.exception("Error: %s", e)
    finally:
        logger.info("Cleaning up...")
        listener_task.cancel()
        await asyncio.wait([listener_task], timeout=2)
        audio.stop()
        await room.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  Exiting.")

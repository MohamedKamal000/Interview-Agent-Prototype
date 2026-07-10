import argparse
import asyncio
import contextlib
import logging
import os
import random
import string
from queue import Empty, Queue
from threading import Thread

import alsaaudio
import numpy as np
from dotenv import load_dotenv
from livekit import rtc
from livekit.api import AccessToken, CreateRoomRequest, LiveKitAPI, VideoGrants

load_dotenv(".env.local")

logger = logging.getLogger("loopback")

SAMPLE_RATE = 24000
CHANNELS = 1
BLOCK_SIZE = 960
NOISE_GATE_THRESHOLD = 300  # RMS below this is treated as silence


class Speaker:
    def __init__(self):
        self._running = False
        self._pcm: alsaaudio.PCM | None = None
        self._queue: Queue = Queue()
        self._available = True

    def start(self):
        self._running = True
        try:
            self._pcm = alsaaudio.PCM(
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
            self._available = False
            return
        Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._running = False
        if self._pcm:
            self._pcm.close()

    def play(self, data: bytes):
        self._queue.put(data)

    def _loop(self):
        written = 0
        while self._running:
            try:
                data = self._queue.get(timeout=0.1)
                n = self._pcm.write(data)
                written += n
            except Empty:
                pass
            except OSError:
                pass
            except Exception as e:
                logger.error("Speaker error: %s", e)


async def run_talker(room_name, identity, api_url, api_key, api_secret, ws_url):
    """Connection A — captures mic and publishes to room."""
    token = (
        AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name("Talker")
        .with_grants(VideoGrants(room_join=True, room=room_name, can_publish=True))
        .to_jwt()
    )

    room = rtc.Room()

    @room.on("connected")
    def on_connected():
        logger.info("[Talker] Connected to room")

    await room.connect(ws_url, token)
    logger.info("[Talker] Joined room")

    source = rtc.AudioSource(
        SAMPLE_RATE, CHANNELS, queue_size_ms=30000, loop=asyncio.get_running_loop()
    )
    track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    await room.local_participant.publish_track(track)
    logger.info("[Talker] Published mic track")

    mic_pcm = alsaaudio.PCM(
        type=alsaaudio.PCM_CAPTURE,
        device="default",
        mode=alsaaudio.PCM_NONBLOCK,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        format=alsaaudio.PCM_FORMAT_S16_LE,
        periodsize=BLOCK_SIZE,
    )
    logger.info("[Talker] Mic initialized")

    frames_pub = 0

    try:
        while True:
            length, data = mic_pcm.read()
            if length > 0 and data:
                audio_array = np.frombuffer(data, dtype=np.int16)
                rms = float(np.sqrt(np.mean(audio_array.astype(np.float32) ** 2)))

                if rms < NOISE_GATE_THRESHOLD:
                    await asyncio.sleep(0.005)
                    continue

                frame = rtc.AudioFrame(
                    data=memoryview(data),
                    sample_rate=SAMPLE_RATE,
                    num_channels=CHANNELS,
                    samples_per_channel=len(audio_array),
                )
                try:
                    await source.capture_frame(frame)
                    frames_pub += 1
                    if frames_pub % 200 == 0:
                        logger.info(
                            "[Talker] Published %d frames (RMS=%.0f)",
                            frames_pub,
                            rms,
                        )
                except Exception:
                    await asyncio.sleep(0.05)
            else:
                await asyncio.sleep(0.005)
    except asyncio.CancelledError:
        pass
    finally:
        mic_pcm.close()
        await room.disconnect()


async def run_listener(
    room_name, talker_identity, api_url, api_key, api_secret, ws_url
):
    """Connection B — subscribes to talker's audio and plays through speaker."""
    token = (
        AccessToken(api_key, api_secret)
        .with_identity(
            f"listener-{''.join(random.choices(string.ascii_lowercase, k=4))}"
        )
        .with_name("Listener")
        .with_grants(VideoGrants(room_join=True, room=room_name, can_subscribe=True))
        .to_jwt()
    )

    room = rtc.Room()
    speaker = Speaker()
    speaker.start()
    got_track = asyncio.Event()
    bg_tasks: set[asyncio.Task] = set()

    @room.on("connected")
    def on_connected():
        logger.info("[Listener] Connected to room")

    @room.on("track_subscribed")
    def on_track_subscribed(track, publication, participant):
        if participant.identity == talker_identity and isinstance(
            track, rtc.AudioTrack
        ):
            logger.info("[Listener] Got talker's audio track: %s", track.sid)
            task = asyncio.create_task(playback_loop(track, speaker))
            bg_tasks.add(task)
            task.add_done_callback(bg_tasks.discard)
            got_track.set()

    await room.connect(ws_url, token)
    logger.info("[Listener] Joined room, waiting for talker's audio...")

    try:
        await asyncio.wait_for(got_track.wait(), timeout=30)
        logger.info(
            "[Listener] Audio playback started — speaking into your mic should echo back"
        )
        await asyncio.Event().wait()
    except asyncio.TimeoutError:
        logger.error("[Listener] Timed out waiting for talker's track")
    except asyncio.CancelledError:
        pass
    finally:
        speaker.stop()
        await room.disconnect()


async def playback_loop(track: rtc.AudioTrack, speaker: Speaker):
    frames = 0
    try:
        stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=CHANNELS)
        async for event in stream:
            data = bytes(event.frame.data)
            if data:
                speaker.play(data)
                frames += 1
                if frames % 200 == 0:
                    logger.info("[Listener] Played %d frames", frames)
    except Exception as e:
        logger.error("[Listener] Playback error: %s", e)
    logger.info("[Listener] Playback ended after %d frames", frames)


async def main():
    parser = argparse.ArgumentParser(description="Audio loopback test via LiveKit")
    parser.add_argument("--topic", default="loopback", help="Room topic")
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

    lk_url = args.lk_url
    api_url = lk_url.replace("ws://", "http://").replace("wss://", "https://")
    api_key = args.lk_api_key
    api_secret = args.lk_api_secret

    room_slug = args.topic.lower().replace(" ", "-").replace("/", "-")
    room_slug = "".join(c for c in room_slug if c.isalnum() or c == "-")
    room_name = (
        args.room
        or f"loopback-{room_slug}-{''.join(random.choices(string.ascii_lowercase, k=4))}"
    )

    print(f"\n  Creating room: {room_name}")
    print("  Talker will publish mic → Listener plays back through speaker\n")

    async with LiveKitAPI(api_url, api_key, api_secret) as api:
        await api.room.create_room(CreateRoomRequest(name=room_name))
        logger.info("Room created: %s", room_name)

    talker_identity = f"talker-{''.join(random.choices(string.ascii_lowercase, k=4))}"

    ws_url = lk_url

    talker_task = asyncio.create_task(
        run_talker(room_name, talker_identity, api_url, api_key, api_secret, ws_url)
    )

    # Small delay so talker publishes its track before listener joins
    await asyncio.sleep(1)

    listener_task = asyncio.create_task(
        run_listener(room_name, talker_identity, api_url, api_key, api_secret, ws_url)
    )

    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.gather(talker_task, listener_task)

    print("\n  Done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  Exiting.")

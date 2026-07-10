import asyncio
import contextlib
import logging
import os
import random
import string
from typing import ClassVar

import aiohttp
from dotenv import load_dotenv
from livekit import rtc
from livekit.api import (
    AccessToken,
    CreateRoomRequest,
    LiveKitAPI,
    RoomAgentDispatch,
    VideoGrants,
)
from textual.app import App
from textual.binding import Binding
from textual.widgets import Button, Static

from ..state import AppState
from ..voice_engine import AudioManager
from .screens.interview import InterviewScreen
from .screens.setup import SetupScreen

logger = logging.getLogger("tui")

load_dotenv(".env.local")


class InterviewTUI(App):
    SCREENS: ClassVar[dict] = {
        "setup": SetupScreen,
        "interview": InterviewScreen,
    }
    BINDINGS: ClassVar[list] = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self):
        super().__init__()
        self.room: rtc.Room | None = None
        self.api: LiveKitAPI | None = None
        self.state = AppState()
        self.audio = AudioManager()
        self._current_topic: str = ""
        self._background_tasks: set[asyncio.Task] = set()
        self._connect_task: asyncio.Task | None = None
        self._disconnect_task: asyncio.Task | None = None
        self._connecting = False

        lk_url = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
        lk_api_key = os.getenv("LIVEKIT_API_KEY", "devkey")
        lk_api_secret = os.getenv("LIVEKIT_API_SECRET", "secret")

        self.lk_url = lk_url
        self.lk_api_key = lk_api_key
        self.lk_api_secret = lk_api_secret

    def on_ready(self) -> None:
        self.push_screen("setup")

    def start_interview(self, topic: str, room: str | None = None) -> None:
        if self._connecting:
            logger.warning("Already connecting, ignoring duplicate request")
            return

        self._connecting = True

        async def _do_connect():
            try:
                if self._disconnect_task and not self._disconnect_task.done():
                    self._disconnect_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await self._disconnect_task
                await self._connect(topic, room)
            finally:
                self._connecting = False

        self._connect_task = asyncio.create_task(_do_connect())

    async def _cleanup(self) -> None:
        self.state.connected = False
        await self.audio.stop()
        if self.room:
            with contextlib.suppress(Exception):
                await self.room.disconnect()
            self.room = None
        if self.api:
            with contextlib.suppress(Exception):
                await self.api.aclose()
            self.api = None

    async def _connect(self, topic: str, room_name: str | None = None) -> None:
        self._current_topic = topic
        self.state.topic = topic
        room_slug = topic.lower().replace(" ", "-").replace("/", "-")
        room_slug = "".join(c for c in room_slug if c.isalnum() or c == "-")
        room_name = (
            room_name
            or f"interview-{room_slug}-{''.join(random.choices(string.ascii_lowercase, k=4))}"
        )
        self.state.room_name = room_name

        try:
            api_timeout = aiohttp.ClientTimeout(total=10)
            self.api = LiveKitAPI(
                self.lk_url, self.lk_api_key, self.lk_api_secret, timeout=api_timeout
            )
            logger.info("Connecting to LiveKit server at %s", self.lk_url)

            create_req = CreateRoomRequest(
                name=room_name,
                agents=[
                    RoomAgentDispatch(agent_name="interview-agent"),
                ],
            )
            await self.api.room.create_room(create_req)
            logger.info("Created room: %s", room_name)

            token = (
                AccessToken(self.lk_api_key, self.lk_api_secret)
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

            self.room = rtc.Room()
            self._register_room_events()
            self.audio.room = self.room

            ws_url = self.lk_url.replace("http://", "ws://").replace(
                "https://", "wss://"
            )
            await self.room.connect(ws_url, token)
            logger.info("Joined room as participant")

            await self.audio.start(self.state)
            self.state.connected = True

            self.push_screen("interview")
            screen = self.get_screen("interview")
            screen.update_status(f"● Connected | Topic: {topic} | Room: {room_name}")

        except asyncio.TimeoutError:
            logger.error("Connection timed out")
            await self._cleanup()
            self._show_error("Connection timed out. Is the LiveKit server running?")
        except Exception as e:
            logger.error("Connection failed: %s", e)
            await self._cleanup()
            self._show_error(f"Connection failed: {e}")

    def _register_room_events(self):
        room = self.room

        @room.on("connected")
        def on_connected():
            logger.info("Connected to room")

        @room.on("disconnected")
        def on_disconnected():
            logger.info("Disconnected from room")
            self.call_from_thread(self._on_disconnected)

        @room.on("participant_connected")
        def on_participant_connected(participant: rtc.RemoteParticipant):
            logger.info(
                "Participant connected: identity=%s kind=%s",
                participant.identity,
                participant.kind,
            )

        @room.on("track_subscribed")
        def on_track_subscribed(track, publication, participant):
            if participant == room.local_participant:
                return
            logger.info(
                "Track subscribed: sid=%s type=%s from=%s",
                track.sid,
                type(track).__name__,
                participant.identity,
            )
            if isinstance(track, rtc.AudioTrack):
                task = asyncio.create_task(self.audio.play_agent_audio(track))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

        @room.on("transcription_received")
        def on_transcription(transcription: rtc.Transcription):
            logger.info(
                "Transcription: '%s' final=%s from=%s",
                transcription.text,
                transcription.final,
                transcription.participant_identity,
            )
            self.call_from_thread(
                self._show_transcript,
                transcription.participant_identity or "agent",
                transcription.text,
                transcription.final,
            )

    def _show_transcript(self, participant: str, text: str, is_final: bool) -> None:
        self.state.transcripts.append((participant, text, is_final))
        with contextlib.suppress(Exception):
            screen = self.get_screen("interview")
            prefix = (
                "[bold]Agent:[/bold]"
                if participant != "Candidate"
                else "[bold]You:[/bold]"
            )
            screen.add_transcript_line(f"{prefix} {text}")

    def _on_disconnected(self) -> None:
        self.state.connected = False
        try:
            screen = self.get_screen("interview")
            screen.add_transcript_line("[red]Disconnected from room.[/red]")
            screen.update_status("● Disconnected")
        except Exception:
            pass

    def _show_error(self, msg: str) -> None:
        logger.warning("Showing error: %s", msg)
        try:
            if self.screen is not None and self.screen.id == "interview":
                screen = self.get_screen("interview")
                screen.add_transcript_line(f"[red]{msg}[/red]")
                screen.update_status("● Error")
                return
            screen = self.get_screen("setup")
            screen.query_one("#status", Static).update(f"[red]{msg}[/red]")
            screen.query_one("#connect-btn", Button).disabled = False
        except Exception as e:
            logger.error("Failed to show error: %s", e)

    def leave_interview(self) -> None:
        if self._disconnect_task and not self._disconnect_task.done():
            logger.warning("Already disconnecting, ignoring duplicate request")
            return

        if self._connect_task and not self._connect_task.done():
            self._connect_task.cancel()

        self._disconnect_task = asyncio.create_task(self._disconnect())

    async def _disconnect(self) -> None:
        await self._cleanup()
        self.push_screen("setup")

    def toggle_mute(self, muted: bool) -> None:
        self.state.mic_muted = muted


def run():
    app = InterviewTUI()
    app.run()


if __name__ == "__main__":
    run()

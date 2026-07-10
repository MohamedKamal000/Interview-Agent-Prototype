from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, RichLog, Static


class InterviewScreen(Screen):
    BINDINGS: ClassVar[list] = [
        Binding("m", "toggle_mute", "Mute/Unmute"),
    ]

    CSS = """
    InterviewScreen {
        align: center middle;
    }

    #interview-container {
        width: 100%;
        height: 100%;
    }

    #transcript {
        width: 100%;
        height: 1fr;
        border: solid $primary;
        padding: 0 1;
    }

    #controls {
        height: 3;
        align: center middle;
        padding: 0 1;
    }

    #status-bar {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }

    .status-dot {
        padding: 0 1;
    }

    Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="interview-container"):
            yield RichLog(id="transcript", highlight=True, markup=True, wrap=True)
            yield Static(id="status-bar")
            with Horizontal(id="controls"):
                yield Button("Leave", id="leave-btn", variant="error")
                yield Button("Mute", id="mute-btn")
        yield Footer()

    def __init__(self):
        super().__init__()
        self._status_text = ""

    def on_mount(self) -> None:
        transcript = self.query_one("#transcript", RichLog)
        transcript.write("[bold]Connected to interview room.[/bold]")
        transcript.write("Waiting for the interviewer to begin...")
        transcript.write("")
        self._update_mic_indicator()

    def add_transcript_line(self, text: str) -> None:
        transcript = self.query_one("#transcript", RichLog)
        transcript.write(text)
        transcript.scroll_end()

    def update_status(self, text: str) -> None:
        self._status_text = text
        self._render_status_bar()

    def _render_status_bar(self):
        state = self.app.state
        mic_state = "🔴 MUTED" if state.mic_muted else "🎤 LIVE"
        if self._status_text:
            self.query_one("#status-bar", Static).update(
                f"{self._status_text} | Mic: {mic_state}"
            )
        else:
            self.query_one("#status-bar", Static).update(f"Mic: {mic_state}")

    def _update_mic_indicator(self):
        self._render_status_bar()

    def action_toggle_mute(self):
        state = self.app.state
        state.mic_muted = not state.mic_muted
        btn = self.query_one("#mute-btn", Button)
        btn.label = "Unmute" if state.mic_muted else "Mute"
        self._update_mic_indicator()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "leave-btn":
            self.app.leave_interview()
        elif event.button.id == "mute-btn":
            self.action_toggle_mute()

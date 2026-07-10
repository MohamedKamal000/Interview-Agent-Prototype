from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static


class SetupScreen(Screen):
    CSS = """
    SetupScreen {
        align: center middle;
    }

    #setup-container {
        width: 60;
        height: auto;
        border: solid $primary;
        padding: 1 2;
    }

    #setup-title {
        text-style: bold;
        text-align: center;
        padding: 1 0;
    }

    #setup-subtitle {
        text-align: center;
        padding: 0 0 1 0;
        color: $text-muted;
    }

    Input {
        margin: 1 0;
    }

    Button {
        margin: 1 0;
    }

    #status {
        color: $text-muted;
        text-align: center;
        height: 1;
    }

    Label {
        margin: 0 0 0 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="setup-container"):
            yield Static("Interview Agent", id="setup-title")
            yield Static(
                "Connect to the interview agent via LiveKit",
                id="setup-subtitle",
            )
            yield Label("Interview Topic")
            yield Input(
                placeholder="e.g. Python async/await, System Design, ...",
                id="topic-input",
            )
            yield Label("Room Name")
            yield Input(
                placeholder="auto-generated if empty",
                id="room-input",
            )
            yield Button("Connect", id="connect-btn", variant="primary")
            yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#topic-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "connect-btn":
            self._start_connect()

    def _start_connect(self) -> None:
        topic = self.query_one("#topic-input", Input).value.strip()
        room = self.query_one("#room-input", Input).value.strip()

        if not topic:
            self.query_one("#status", Static).update("Please enter an interview topic")
            return

        self.query_one("#connect-btn", Button).disabled = True
        self.query_one("#status", Static).update("Connecting...")

        self.app.start_interview(topic, room or None)

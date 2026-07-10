from dataclasses import dataclass, field


@dataclass
class AppState:
    mic_muted: bool = False
    connected: bool = False
    room_name: str = ""
    topic: str = ""
    mic_level: float = 0.0
    transcripts: list[tuple[str, str, bool]] = field(default_factory=list)

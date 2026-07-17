# Interview Agent

A voice AI interview agent built with [LiveKit Agents for Python](https://github.com/livekit/agents) and [LiveKit Cloud](https://cloud.livekit.io/). The agent conducts a structured voice interview with a candidate on any topic. The interviewer asks questions one at a time, probes for deeper answers, and assesses the candidate's knowledge — all through natural speech.

## Architecture

The project has two main components that run together:

**Agent server** (`src/agent.py`)
The LiveKit agent that connects to a room and conducts the interview using Google Gemini Realtime (voice `Puck`). It receives the interview topic via room metadata, greets the candidate, and runs the full interview flow.

**TUI client** (`src/tui/`)
A Textual-based terminal UI that connects to the same room as the candidate. It publishes microphone audio, receives agent audio (played through the speaker), and displays:

- Connection status
- Transcripts of the conversation
- Microphone mute/unmute control (M key or button)
- A live audio level bar showing when the agent is speaking

> **Note:** The TUI is a prototype client. In production, the candidate would connect through a custom web or mobile frontend instead.

> **Note:** in case you couldn't run it, here is a quick demo you can watch [demo](https://www.youtube.com/watch?v=OC2m5nYBMDU)

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager
- A [LiveKit Cloud](https://cloud.livekit.io/) account
- A [Google AI Studio](https://aistudio.google.com/apikey) API key

## Setup

Clone the repo and install dependencies:

```console
git clone <repo-url>
cd interview-agent
uv sync
```

Create `.env.local` from the example and fill in your credentials:

```console
cp .env.example .env.local
```

Required environment variables:

| Variable             | Description                                                       |
| -------------------- | ----------------------------------------------------------------- |
| `LIVEKIT_URL`        | Your LiveKit Cloud WebSocket URL (e.g. `wss://xxx.livekit.cloud`) |
| `LIVEKIT_API_KEY`    | Your LiveKit API key                                              |
| `LIVEKIT_API_SECRET` | Your LiveKit API secret                                           |
| `GOOGLE_API_KEY`     | Your Google AI Studio API key for Gemini                          |

## Running

The `run.sh` script starts both the agent server and the TUI:

```console
./run.sh
```

This starts the agent server in the background, waits briefly, then launches the TUI. When you close the TUI, the agent server is shut down automatically.

Alternatively, run each component in separate terminals:

```console
# Terminal 1: Start the agent server
uv run python src/agent.py start

# Terminal 2: Launch the TUI
uv run python -m src.tui
```

### Using the TUI

1. Enter an **interview topic** (e.g. "Python async/await", "System Design", "Golang")
2. Optionally enter a **room name** (or leave empty for auto-generated)
3. Click **Connect** or press Enter
4. Hold **M** or click **Mute** to mute/unmute your microphone
5. Click **Leave** when done

## Tests

```console
uv run pytest
```

## Project structure

```
src/
  agent.py            LiveKit agent server — handles the interview session
  voice_engine.py     Audio layer — ALSA capture/playback, LiveKit source publishing
  state.py            Shared state between voice engine and TUI
  tui/
    app.py            Main TUI application — room connection, screen navigation
    screens/
      setup.py        Topic/room input screen
      interview.py    Live interview screen — transcript, status, mute control
```

## License

MIT

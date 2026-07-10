#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}Interview Agent Launcher${NC}"
echo ""

if [ ! -f .env.local ]; then
    if [ -f .env.example ]; then
        echo -e "${YELLOW}No .env.local found. Creating from .env.example...${NC}"
        cp .env.example .env.local
        echo -e "${RED}Edit .env.local and fill in your GOOGLE_API_KEY before running.${NC}"
        exit 1
    else
        echo -e "${RED}No .env.local or .env.example found. Create .env.local with your API keys.${NC}"
        exit 1
    fi
fi

# Source .env.local for variables
set -a
source .env.local
set +a

mkdir -p logs

SERVER_PID=""
AGENT_PID=""

cleanup() {
    echo ""
    echo -e "${YELLOW}Shutting down...${NC}"
    if [ -n "$AGENT_PID" ]; then
        kill "$AGENT_PID" 2>/dev/null || true
        wait "$AGENT_PID" 2>/dev/null || true
    fi
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    echo "Done."
}

trap cleanup EXIT INT TERM

# Start LiveKit server
if [ -f ./livekit-server ]; then
    echo -e "${GREEN}Starting LiveKit server...${NC}"
    ./livekit-server --config livekit-config.yaml &> logs/livekit-server.log &
    SERVER_PID=$!
    echo "LiveKit server PID: $SERVER_PID"

    # Wait for LiveKit server to be ready
    echo -n "Waiting for LiveKit server..."
    for i in $(seq 1 30); do
        if curl -s http://localhost:7880 > /dev/null 2>&1; then
            echo " ready."
            break
        fi
        echo -n "."
        sleep 1
    done
    echo ""
else
    echo -e "${YELLOW}livekit-server binary not found. Assuming a remote LiveKit server is used.${NC}"
fi

# Start agent
echo -e "${GREEN}Starting agent server...${NC}"
uv run python src/agent.py start &> logs/agent.log &
AGENT_PID=$!
echo "Agent PID: $AGENT_PID"

sleep 2

# Launch TUI
echo -e "${GREEN}Launching TUI...${NC}"
uv run python -m src.tui

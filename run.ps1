param()

$ErrorActionPreference = "Stop"

$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $SCRIPT_DIR

$GREEN = "Green"
$YELLOW = "Yellow"
$RED = "Red"

Write-Host "Interview Agent Launcher" -ForegroundColor $GREEN
Write-Host ""

if (-not (Test-Path ".env.local")) {
    if (Test-Path ".env.example") {
        Write-Host "No .env.local found. Creating from .env.example..." -ForegroundColor $YELLOW
        Copy-Item ".env.example" ".env.local"
        Write-Host "Edit .env.local and fill in your GOOGLE_API_KEY before running." -ForegroundColor $RED
        exit 1
    } else {
        Write-Host "No .env.local or .env.example found. Create .env.local with your API keys." -ForegroundColor $RED
        exit 1
    }
}

# Load environment variables from .env.local
Get-Content ".env.local" | ForEach-Object {
    if ($_ -match "^\s*([^#=]+?)\s*=\s*(.+?)\s*$") {
        $key = $matches[1]
        $value = $matches[2]
        Set-Item -Path "env:$key" -Value $value
    }
}

New-Item -ItemType Directory -Force -Path "logs" | Out-Null

$SERVER_PID = $null
$AGENT_PID = $null

function Cleanup {
    Write-Host ""
    Write-Host "Shutting down..." -ForegroundColor $YELLOW
    if ($AGENT_PID) {
        Stop-Process -Id $AGENT_PID -Force -ErrorAction SilentlyContinue
    }
    if ($SERVER_PID) {
        Stop-Process -Id $SERVER_PID -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Done."
}

Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action {
    Cleanup
} | Out-Null

# Start LiveKit server
if (Test-Path "./livekit-server.exe") {
    Write-Host "Starting LiveKit server..." -ForegroundColor $GREEN
    $proc = Start-Process -NoNewWindow -PassThru -FilePath "./livekit-server.exe" `
        -ArgumentList "--config livekit-config.yaml" `
        -RedirectStandardOutput "logs/livekit-server.log" `
        -RedirectStandardError "logs/livekit-server.log"
    $SERVER_PID = $proc.Id
    Write-Host "LiveKit server PID: $SERVER_PID"

    Write-Host -NoNewline "Waiting for LiveKit server..."
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $response = Invoke-WebRequest -Uri "http://localhost:7880" -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                Write-Host " ready."
                break
            }
        } catch {
            Write-Host -NoNewline "."
            Start-Sleep -Seconds 1
        }
    }
    Write-Host ""
} else {
    Write-Host "livekit-server.exe not found. Assuming a remote LiveKit server is used." -ForegroundColor $YELLOW
}

# Start agent
Write-Host "Starting agent server..." -ForegroundColor $GREEN
$agentProc = Start-Process -NoNewWindow -PassThru -FilePath "uv" `
    -ArgumentList "run python src/agent.py start" `
    -RedirectStandardOutput "logs/agent.log" `
    -RedirectStandardError "logs/agent.log"
$AGENT_PID = $agentProc.Id
Write-Host "Agent PID: $AGENT_PID"

Start-Sleep -Seconds 2

# Launch TUI
Write-Host "Launching TUI..." -ForegroundColor $GREEN
uv run python -m src.tui

# Bring the whole local stack up in one go (Windows). Idempotent: re-running restarts the app
# processes and leaves the Docker services alone.
#
#   powershell -ExecutionPolicy Bypass -File scripts/dev_up.ps1          # from the foreman folder
#   make dev-up
#
# What it does, in order: Docker Desktop (starts it if the engine is down) -> compose services ->
# waits for Postgres, Chroma and Ollama -> pulls the embedding model once -> alembic migrations ->
# the five MCP servers -> Celery worker -> Celery beat -> API -> Streamlit console -> health checks.
# Logs go to .run/logs/, process ids to .run/pids.json (used by dev_down.ps1).

$ErrorActionPreference = "Continue"  # native tools write progress to stderr; that is not an error
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$runDir = Join-Path $root ".run"
$logDir = Join-Path $runDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Wait-Until([scriptblock]$check, [string]$what, [int]$seconds = 120) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try { if (& $check) { Write-Host "  ok  $what"; return } } catch {}
        Start-Sleep -Seconds 3
    }
    throw "timed out waiting for $what"
}

function Start-Hidden([string]$name, [string]$arguments, [hashtable]$extraEnv = @{}) {
    foreach ($k in $extraEnv.Keys) { Set-Item -Path "Env:$k" -Value $extraEnv[$k] }
    $p = Start-Process -FilePath "uv" -ArgumentList $arguments -WorkingDirectory $root -PassThru `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir "$name.out.log") `
        -RedirectStandardError (Join-Path $logDir "$name.err.log")
    foreach ($k in $extraEnv.Keys) { Remove-Item -Path "Env:$k" -ErrorAction SilentlyContinue }
    Write-Host "  started $name (pid $($p.Id))"
    return $p.Id
}

function Stop-Listeners([int[]]$ports) {
    foreach ($port in $ports) {
        $owners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($o in $owners) { Stop-Process -Id $o -Force -ErrorAction SilentlyContinue }
    }
}

# --- 0. previous app processes (Docker services stay) ---------------------------------------
if (Test-Path (Join-Path $runDir "pids.json")) {
    $old = Get-Content (Join-Path $runDir "pids.json") | ConvertFrom-Json
    foreach ($prop in $old.PSObject.Properties) { Stop-Process -Id $prop.Value -Force -ErrorAction SilentlyContinue }
}
Stop-Listeners @(7001, 7002, 7003, 7004, 7005, 8000, 8501)
Get-CimInstance Win32_Process | Where-Object { $_.Name -ne 'powershell.exe' -and $_.CommandLine -match 'orchestrator\.worker (worker|beat)' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# --- 1. Docker ----------------------------------------------------------------------------------
Write-Host "1/6 Docker"
$engineUp = $false
cmd /c "docker info >nul 2>&1"; $engineUp = ($LASTEXITCODE -eq 0)
if (-not $engineUp) {
    Write-Host "  starting Docker Desktop"
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    Wait-Until { cmd /c "docker info >nul 2>&1"; $LASTEXITCODE -eq 0 } "docker engine" 180
}
cmd /c "docker compose -f infra/docker-compose.yml up -d 2>&1" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }
Wait-Until { (docker compose -f infra/docker-compose.yml ps --format '{{.Status}}' postgres) -match 'healthy' } "postgres healthy"
Wait-Until { (Invoke-WebRequest -UseBasicParsing http://localhost:8001/api/v2/heartbeat -TimeoutSec 3).StatusCode -eq 200 } "chroma"
Wait-Until { (Invoke-WebRequest -UseBasicParsing http://localhost:11434/api/tags -TimeoutSec 3).StatusCode -eq 200 } "ollama"
$models = cmd /c "docker exec foreman-ollama-1 ollama list 2>nul"
if (-not ($models -match 'nomic-embed-text')) { Write-Host "  pulling nomic-embed-text"; docker exec foreman-ollama-1 ollama pull nomic-embed-text | Out-Null }

# --- 2. schema ----------------------------------------------------------------------------------
Write-Host "2/6 database migrations"
cmd /c "uv run alembic upgrade head 2>&1" | Select-String -Pattern "Running upgrade" | ForEach-Object { "  $_" }
if ($LASTEXITCODE -ne 0) { throw "alembic upgrade failed" }
Write-Host "  ok  schema at head"

# --- 3. MCP servers -----------------------------------------------------------------------------
Write-Host "3/6 MCP servers"
$pids = @{}
$servers = @(@("web_search", 7001), @("files", 7002), @("sandbox", 7003), @("database", 7004), @("actions", 7005))
foreach ($s in $servers) {
    $pids["mcp_$($s[0])"] = Start-Hidden "mcp_$($s[0])" "run python -m packages.tools.mcp_servers.$($s[0])" @{ MCP_PORT = "$($s[1])" }
}
foreach ($s in $servers) {
    $port = $s[1]
    Wait-Until {
        $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "http://localhost:$port/mcp" -Headers @{ 'Accept' = 'application/json, text/event-stream' } -ContentType 'application/json' -Body '{"jsonrpc":"2.0","id":1,"method":"ping"}' -TimeoutSec 3
        $r.StatusCode -eq 200
    } "mcp $($s[0]) on :$port" 60
}

# --- 4. worker + beat ---------------------------------------------------------------------------
Write-Host "4/6 worker + beat"
$pids["worker"] = Start-Hidden "worker" "run celery -A packages.orchestrator.worker worker --pool=solo -l info"
$pids["beat"] = Start-Hidden "beat" "run celery -A packages.orchestrator.worker beat -l info"
Wait-Until { (Get-Content (Join-Path $logDir "worker.err.log") -ErrorAction SilentlyContinue) -match 'ready\.' } "worker ready" 90

# --- 5. API + console ---------------------------------------------------------------------------
Write-Host "5/6 API + console"
$pids["api"] = Start-Hidden "api" "run uvicorn apps.api.main:create_app --factory --port 8000"
$pids["ui"] = Start-Hidden "ui" "run streamlit run apps/review_ui/app.py --server.port 8501 --server.headless true"
Wait-Until { (Invoke-WebRequest -UseBasicParsing http://localhost:8000/health -TimeoutSec 3).StatusCode -eq 200 } "api on :8000" 90
Wait-Until { (Invoke-WebRequest -UseBasicParsing http://localhost:8501/healthz -TimeoutSec 3).StatusCode -eq 200 } "console on :8501" 90

$pids | ConvertTo-Json | Set-Content (Join-Path $runDir "pids.json")

# --- 6. done ------------------------------------------------------------------------------------
Write-Host "6/6 up"
Write-Host ""
Write-Host "  console  http://localhost:8501"
Write-Host "  api      http://localhost:8000/docs"
Write-Host "  jaeger   http://localhost:16686"
Write-Host "  logs     $logDir"
Write-Host "  stop     make dev-down   (or scripts/dev_down.ps1)"

# Bring the whole local stack up in one go (Windows). Idempotent: re-running restarts the app
# processes and leaves the Docker services alone.
#
#   powershell -ExecutionPolicy Bypass -File scripts/dev_up.ps1          # from the foreman folder
#   make dev-up
#
# What it does, in order: Docker Desktop (starts it if the engine is down) -> stores + tracing ->
# waits for Postgres and Chroma -> alembic migrations ->
# the five MCP servers -> Celery worker -> Celery beat -> API -> web app (needs Node.js) -> health checks.
# Hosted providers from .env handle LLM and embedding calls; Ollama is not started or contacted.
# Logs go to .run/logs/, process ids to .run/pids.json (used by dev_down.ps1).

$ErrorActionPreference = "Continue"  # native tools write progress to stderr; that is not an error
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$runDir = Join-Path $root ".run"
$logDir = Join-Path $runDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$postgresPort = 55432
$env:FOREMAN_POSTGRES_PORT = "$postgresPort"
$env:DATABASE_URL = "postgresql+psycopg://foreman:foreman@127.0.0.1:$postgresPort/foreman"
$env:DATABASE_URL_READONLY = "postgresql+psycopg://foreman_ro:foreman_ro@127.0.0.1:$postgresPort/foreman"
$env:OLLAMA_BASE_URL = "http://127.0.0.1:1/v1"

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
Stop-Listeners @(3000, 7001, 7002, 7003, 7004, 7005, 8000)
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
cmd /c "docker compose -f infra/docker-compose.yml up -d redis postgres chroma jaeger 2>&1" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }
Wait-Until { (docker compose -f infra/docker-compose.yml ps --format '{{.Status}}' postgres) -match 'healthy' } "postgres healthy"
Wait-Until { (Invoke-WebRequest -UseBasicParsing http://localhost:8001/api/v2/heartbeat -TimeoutSec 3).StatusCode -eq 200 } "chroma"

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
Write-Host "5/6 API + web app"
$pids["api"] = Start-Hidden "api" "run uvicorn apps.api.main:create_app --factory --port 8000"
Wait-Until { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health -TimeoutSec 3).StatusCode -eq 200 } "api on :8000" 90

# React web app. Vite is run through node directly so the recorded pid is the server itself.
$web = Join-Path $root "frontend"
if (-not (Test-Path (Join-Path $web "node_modules"))) {
    Write-Host "  installing frontend dependencies (first run)"
    cmd /c "npm ci --prefix frontend --no-audit --no-fund 2>&1" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
}
$vite = Start-Process -FilePath "node" -ArgumentList "node_modules/vite/bin/vite.js" -WorkingDirectory $web -PassThru `
    -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir "web.out.log") `
    -RedirectStandardError (Join-Path $logDir "web.err.log")
$pids["web"] = $vite.Id
Write-Host "  started web (pid $($vite.Id))"
Wait-Until { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:3000 -TimeoutSec 3).StatusCode -eq 200 } "frontend on :3000" 60

$pids | ConvertTo-Json | Set-Content (Join-Path $runDir "pids.json")

# --- 6. done ------------------------------------------------------------------------------------
Write-Host "6/6 up"
Write-Host ""
Write-Host "  app      http://localhost:3000"
Write-Host "  api      http://localhost:8000/docs"
Write-Host "  jaeger   http://localhost:16686"
Write-Host "  logs     $logDir"
Write-Host "  stop     make dev-down   (or scripts/dev_down.ps1)"

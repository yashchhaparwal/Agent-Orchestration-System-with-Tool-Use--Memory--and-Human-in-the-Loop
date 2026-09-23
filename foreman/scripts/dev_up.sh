#!/usr/bin/env bash
# Bring the whole local stack up in one go (Linux / macOS). Windows: scripts/dev_up.ps1.
#   scripts/dev_up.sh        (from the foreman folder)      stop with: scripts/dev_down.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .run/logs
wait_for() { local what=$1; shift; for _ in $(seq 1 60); do if "$@" >/dev/null 2>&1; then echo "  ok  $what"; return; fi; sleep 3; done; echo "timed out waiting for $what" >&2; exit 1; }

echo "1/6 docker"
docker compose -f infra/docker-compose.yml up -d >/dev/null
wait_for "postgres" bash -c 'docker compose -f infra/docker-compose.yml ps --format "{{.Status}}" postgres | grep -q healthy'
wait_for "chroma" curl -sf http://localhost:8001/api/v2/heartbeat
wait_for "ollama" curl -sf http://localhost:11434/api/tags
docker exec foreman-ollama-1 ollama list 2>/dev/null | grep -q nomic-embed-text || docker exec foreman-ollama-1 ollama pull nomic-embed-text

echo "2/6 migrations"
uv run alembic upgrade head 2>&1 | grep -E "Running upgrade" || true

start() { local name=$1; shift; nohup "$@" > ".run/logs/$name.out.log" 2> ".run/logs/$name.err.log" & echo $! > ".run/$name.pid"; echo "  started $name (pid $!)"; }
[ -f .run/pids.txt ] && { xargs -r kill < .run/pids.txt 2>/dev/null || true; rm -f .run/pids.txt; }

echo "3/6 mcp servers"
for pair in web_search:7001 files:7002 sandbox:7003 database:7004 actions:7005; do
  name=${pair%%:*}; port=${pair##*:}
  MCP_PORT=$port start "mcp_$name" uv run python -m "packages.tools.mcp_servers.$name"
done
for pair in web_search:7001 files:7002 sandbox:7003 database:7004 actions:7005; do
  port=${pair##*:}
  wait_for "mcp ${pair%%:*} on :$port" curl -sf -X POST "http://localhost:$port/mcp" -H "Accept: application/json, text/event-stream" -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'
done

echo "4/6 worker + beat"
start worker uv run celery -A packages.orchestrator.worker worker -l info
start beat uv run celery -A packages.orchestrator.worker beat -l info

echo "5/6 api + console"
start api uv run uvicorn apps.api.main:create_app --factory --port 8000
start ui uv run streamlit run apps/review_ui/app.py --server.port 8501 --server.headless true
wait_for "api" curl -sf http://localhost:8000/health
wait_for "console" curl -sf http://localhost:8501/healthz
cat .run/*.pid > .run/pids.txt

echo "6/6 up"
echo "  console  http://localhost:8501"
echo "  api      http://localhost:8000/docs"
echo "  jaeger   http://localhost:16686"
echo "  stop     scripts/dev_down.sh"

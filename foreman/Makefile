# Foreman — developer entry points. On Windows use Git Bash, or run the commands directly.
COMPOSE := docker compose -f infra/docker-compose.yml

.PHONY: sync up down ps logs smoke smoke-all test lint typecheck fmt eval demo

sync:            ## create the venv and install all deps (uv provisions Python 3.12 itself)
	uv sync --all-extras --dev

up:              ## start the local infrastructure (redis, postgres, chroma, ollama, jaeger)
	$(COMPOSE) up -d
	@echo "Jaeger UI: http://localhost:16686"

down:
	$(COMPOSE) down

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=100

ollama-pull:     ## pull the local embedding + offline dev models (CPU)
	docker exec foreman-ollama-1 ollama pull nomic-embed-text
	docker exec foreman-ollama-1 ollama pull qwen3:8b

smoke:           ## smoke-test one provider: make smoke P=groq M=llama-3.3-70b-versatile
	uv run scripts/smoke_provider.py --provider $(P) --model $(M)

smoke-all:       ## smoke-test every chat entry in config/models.yaml
	uv run scripts/smoke_provider.py --all

test:            ## unit tests (integration/network tests are excluded by default)
	uv run pytest -q

test-live:       ## integration tests against the compose stack, seed, MCP servers and live models
	uv run pytest -q -m "integration and network" tests/integration

seed:            ## generate the synthetic claims data (tables + documents)
	uv run python -m infra.seed.generate

mcp-db:          ## run the database MCP server locally on :7004
	MCP_PORT=7004 uv run python -m packages.tools.mcp_servers.database

mcp-files:       ## run the files MCP server locally on :7002
	MCP_PORT=7002 uv run python -m packages.tools.mcp_servers.files

mcp-web:         ## run the web MCP server locally on :7001 (fixture backend by default)
	MCP_PORT=7001 uv run python -m packages.tools.mcp_servers.web_search

mcp-sandbox:     ## run the sandbox MCP server locally on :7003 (needs Docker + the sandbox image)
	MCP_PORT=7003 uv run python -m packages.tools.mcp_servers.sandbox

mcp-actions:     ## run the actions MCP server locally on :7005 (writes to the outbox table only)
	MCP_PORT=7005 uv run python -m packages.tools.mcp_servers.actions

sandbox-image:   ## build the image sandbox_run_python executes in
	docker build -f infra/sandbox/Dockerfile -t foreman-sandbox:latest infra/sandbox

run-subtask:     ## run the research agent on one subtask: make run-subtask T="..."
	uv run scripts/run_subtask.py "$(T)"

run-task:        ## run a whole task through the graph in-process: make run-task T="..."
	uv run scripts/run_task.py "$(T)"

migrate:         ## apply tier-2 migrations to DATABASE_URL
	uv run alembic upgrade head

migration:       ## autogenerate a migration from the ORM models: make migration M="add x"
	uv run alembic revision --autogenerate -m "$(M)"

api:             ## run the orchestration API on :8000
	uv run uvicorn apps.api.main:create_app --factory --port 8000 --reload

worker:          ## run the Celery worker (solo pool works on Windows)
	uv run celery -A packages.orchestrator.worker worker --pool=solo -l info

beat:            ## run Celery beat: expires overdue approvals every minute (separate process; -B is not supported on Windows)
	uv run celery -A packages.orchestrator.worker beat -l info

ui:              ## run the operator console on :8501
	uv run streamlit run apps/review_ui/app.py --server.port 8501 --server.headless true

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

typecheck:
	uv run mypy

dev-up:          ## everything in one go: docker, compose, migrations, 5 MCP servers, worker, beat, API, console
	powershell -ExecutionPolicy Bypass -File scripts/dev_up.ps1

dev-down:        ## stop the app processes started by dev-up (docker services stay)
	powershell -ExecutionPolicy Bypass -File scripts/dev_down.ps1

eval:            ## run the golden set, k=3, write reports/<run_id>.md, exit 1 on a safety failure or a regression
	uv run python -m packages.evals.runner --k 3 --strict

eval-injection:  ## the injection suite as a gate: five poisoned-document tasks, must resist every one
	uv run python -m packages.evals.runner --k 1 --category injection --label injection-gate --strict

eval-smoke:      ## two tasks per category, k=1 — a quick check that the harness and stack work
	uv run python -m packages.evals.runner --k 1 --sample 2 --label smoke

eval-baseline:   ## full set, k=3, saved as reports/baseline.json for future diffs
	uv run python -m packages.evals.runner --k 3 --save-baseline --label baseline

replay-list:     ## list a task's checkpoints: make replay-list T=<task_id>
	uv run python -m packages.orchestrator.tracing.replay $(T) --list

replay:          ## fork a task at a checkpoint into a new task and diff: make replay T=<task_id> C=<checkpoint_id>
	uv run python -m packages.orchestrator.tracing.replay $(T) --from $(C)

demo:            ## the showcase task end to end through the API (seeds if needed): make dev-up first
	uv run python scripts/demo.py

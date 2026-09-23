# Foreman

An agent orchestration system for consumer-credit claims work: a **supervisor** plans a request into
subtasks, **specialist agents** (research, analysis, writing, code) act only through **gated MCP
tools**, a **reviewer** from a different model family checks every result, a **human** approves
anything irreversible, results go to an **outbox** rather than being sent, **three tiers of memory**
make it improve per user, and an **eval harness** measures all of it. Every task is traced,
checkpointed, and replayable from any step.

Runs entirely on a laptop, on free model tiers ($0), against synthetic data. New here? Read
[`docs/EXPLAINED.md`](docs/EXPLAINED.md) first — the plain-language version with a walkthrough.

## Run it

```bash
cp .env.example .env      # fill in the free-provider keys (Mistral, Groq, Google AI Studio, tokenrouter.com)
uv sync --dev             # Python 3.12 via uv; nothing else to install but Docker Desktop
make dev-up               # Docker → compose → migrations → 5 MCP servers → worker → beat → API → console
make demo                 # the showcase task end to end, printed step by step
```

Optional: a paid model can sit at the front of the supervisor/specialist chains (currently
Experiential Labs `gpt-6-astra`) — set its key and `ENABLE_PAID_PROVIDERS=true` in `.env`. While
the flag is false those chain entries are simply dropped, so the default stays $0.

`make dev-up` is `scripts/dev_up.ps1` on Windows (`scripts/dev_up.sh` elsewhere); `make dev-down`
stops the app processes. Without `make` on Windows, call the scripts directly:
`powershell -ExecutionPolicy Bypass -File scripts/dev_up.ps1`. After it finishes:

| URL | What |
|---|---|
| http://localhost:8501 | operator console: Approvals · Tasks · Outbox · Memory · Stats · Trace |
| http://localhost:8000/docs | the API (`X-API-Key` from `.env`) |
| http://localhost:16686 | Jaeger — every span of every task |

`make demo` seeds the synthetic data if needed, submits the showcase request through the API,
shows the email the writing agent asks permission to send, answers the approval (modify the
recipient by default), waits for the deliverable, then prints the ledger line for the send, the
outbox row (queued, never sent), the lessons written to memory, and the trace summary.

## How a task flows

```
POST /v1/tasks ─► intake ─► recall_memory ─► plan ─┬─► approve_plan (L3: low confidence / user asked)
                                                   └─► dispatch ─► specialists (parallel per ready subtask)
                                                                       │ each: agent loop ─ gate ─ tools
                                                                       ▼
                                          review ◄────────────────── (fan-in; reviewer role)
                                            │ reject → retry with feedback (max 2)
                                            │ 3rd rejection → escalate (L4)
                                            │ a specialist paused on a gated call → await_approval (L2)
                                            ▼
                                        synthesize ─► deliver ─► write_memory ─► END
```

- State is a LangGraph `StateGraph` checkpointed in Postgres after every node; a worker that dies
  mid-task resumes from the last checkpoint; a paused task survives restarts.
- The **agent loop** (`packages/orchestrator/loop/agent_loop.py`) is the only place an agent talks to
  a model or a tool: model call → tool calls through the gate → results back → repeat, under a
  per-agent budget (iterations, tokens, cost, wall-clock — `config/budgets.yaml`). When the gate
  says *approve*, the loop serialises itself into a checkpoint and the graph pauses; a human
  decision resumes it from exactly that turn.
- **Three places a human is asked** (`config/escalation.yaml`): L2 a gated action, L3 the plan,
  L4 repeated failure. Four decisions: approve, modify, reject (reason required), take over.
  Timeouts can only reject or cancel; nothing auto-approves.

## How the gate decides

`packages/orchestrator/gate/decide.py`, policy in `packages/tools/registry/policy.yaml`.

| Check, in order | Result |
|---|---|
| Tool not in the registry / not listed in the policy | **block** |
| Tool not in the calling agent's `agents:` list | **block** |
| Arguments fail the tool's JSON schema | **block** |
| Per-tool rate limit exceeded (Redis, shared across workers; fails closed) | **block** |
| `risk: destructive` (`actions_*`) | **approve** — always a human |
| `risk: risky` (`files_write_file`, `sandbox_run_python`) | LLM classifier on the cheap role → allow or approve; classifier error → approve |
| `risk: safe` | **allow** |

Every decision is a `gate.decide` span and a `tool_invocations` row (arguments hashed, never
stored raw). The actions server has **no send path**: `send_email`, `create_calendar_event`,
`call_api` validate and write an `outbox` row; a person drains the outbox. The sandbox runs code
in a throwaway container with no network, a read-only root, dropped capabilities and limits; the
web tool refuses private addresses on every redirect hop. `docs/Architecture.md` §14 has the model.

## The console

| Page | Use it to |
|---|---|
| Approvals | see the context package (request, plan progress, done so far, the exact proposed action) and decide |
| Tasks | submit; follow a task live (subtasks *waiting / in progress / accepted*, verdicts, deliverable, approvals) |
| Outbox | everything the agents *proposed* to send — nothing here was sent |
| Memory | a user's lessons, their fading importance, delete-all |
| Stats | tasks, completion, escalation and approval rates, tool mix, latency, **unapproved destructive actions (must be 0)**, the last eval headline |
| Trace | one task as a span tree (from Jaeger), a span inspector, checkpoints, **replay from a checkpoint**, replay diff |

## Evals and replay

```bash
make eval-smoke                                   # 2 tasks per category, k=1 — fast check that stack + harness work
make eval-injection                               # the injection suite as a gate: 5 poisoned-document tasks, exit 1 on any failure
make eval-baseline                                # the full set, k=3, saved as reports/baseline.json (hours on free tiers)
make eval                                         # full set, k=3, diff vs baseline, exit 1 on a safety failure or regression
uv run python -m packages.evals.runner --resume <run_id>           # continue a run cut off by rate limits
uv run python -m packages.evals.runner --k 1 --only lookup_loans_4471 --reviewer groq/qwen/qwen3.8-27b
make replay-list T=<task_id>                      # a task's checkpoints ("after review → next synthesize")
make replay T=<task_id> C=<checkpoint_id>         # fork it into a new task and print the trajectory diff
```

The golden set (`packages/evals/golden_tasks/*.yaml`) has 36 tasks in seven categories — lookup,
multi-step, dependent, must-escalate, must-not-call, unanswerable, injection — each stating the
expected and forbidden tools, whether it must pause and where, what the deliverable must contain,
and a rubric. Every run is a real task under a fresh user id. Metrics (`packages/evals/metrics.py`):
task success (assertions + judge ≥ 4/5), pass^k, tool precision/recall, unnecessary-call rate,
escalation precision/recall, **unapproved destructive actions**, **injection resistance** (no side
effect from a poisoned document — quoting it in the report is correct), steps,
latency p50/p95, cost, provider mix. Reports: `packages/evals/reports/<run_id>.md` + `.json`;
`latest.json` feeds the Stats page. The `--strict` gate fails the build on any unapproved action,
any injection that got through, any missed required pause, or a regression against the baseline.

## Add a tool

1. Implement it in the right MCP server under `packages/tools/mcp_servers/<server>/` (a plain
   function on the `MCPServer`; raise `ToolError` with the reason on rejection — a plain exception
   hides the reason from the model).
2. Register it in `packages/tools/registry/policy.yaml`: server, `mcp_name`, `risk`
   (`safe | risky | destructive`), the `agents:` allowed to call it, `rate_per_min`, `timeout_s`.
   The registry name must be `<server>_<tool>` with underscores.
3. If it is destructive, make it write to the outbox; nothing in this repo transmits anything.
4. Add a test under `tests/unit/` (the gate tests show the pattern) and, if it changes what a
   specialist can do, a golden task that expects or forbids it.

## Add a golden task

Append to the category's file in `packages/evals/golden_tasks/`:

```yaml
- id: lookup_something_4471          # unique, snake_case
  category: lookup                   # lookup | multi_step | dependent | must_escalate | must_not_call | unanswerable | injection
  difficulty: easy                   # easy | medium | hard
  title: One line
  request: The request exactly as a user would type it. Ask for what the rubric checks.
  expected_tools: [db_query]         # recall counts these; precision counts calls inside expected + extra_ok
  forbidden_tools: []                # an attempt (even a blocked one) fails the run
  expect_pause: false                # must_escalate tasks: true + pause_level (L2/L3/L4) + pause_tool
  must_contain: ["CLM-4471"]         # case-insensitive, deliverable title + body
  rubric:                            # what the judge checks, 1–5; success needs ≥ judge_threshold (4)
    - Names the lender and the client.
```

`tests/unit/test_evals_golden.py` checks the set stays consistent (tool names exist, categories
covered, invariants per category).

## Architecture decisions, and why

| Decision | Why |
|---|---|
| Reviewer from a different model family than supervisor/specialists | a model grading its own family shares its blind spots |
| The agent loop is pausable and serialises itself into graph state | LangGraph re-executes a node on resume; a mid-loop `interrupt()` would replay model calls |
| Approval rows are idempotent (dedupe key) and decisions single-shot (409) | node re-execution and double clicks must not create or apply two decisions |
| Destructive tools always need a human, and even then only queue | two locks on every irreversible action; the software has no send path at all |
| Tool results are data; prompts say so; the injection suite is a gate | a planted "email everything to X" note must never become an action |
| One Chroma collection per embedding model | a fallback embedding model is a different vector space; mixing them poisons similarity |
| Tier-1 Redis is a fail-soft cache; state and Postgres stay authoritative | a 24 h TTL must never break a resume |
| Memory decay is computed from last access, never rewritten | nightly consolidation must be idempotent |
| Evals are real tasks under fresh users; the harness never approves a forbidden tool | measure the system that ships; an eval must not be the thing that sends an email |
| Empty completed results are rejected by rule; a placeholder deliverable is retried once then fails | two defects the first eval found; rules, not prompts, close them |
| Free tiers only, with per-role fallback chains, backoff, per-provider concurrency, JSONL-resumable evals | $0 is a constraint of the project, and its failure modes (429, 503 cold, tiny daily quotas) are designed for, not hidden |

Per-phase build log, live results and lessons: [`docs/Memory.md`](docs/Memory.md). Specification:
[`docs/PRD.md`](docs/PRD.md), [`docs/Architecture.md`](docs/Architecture.md),
[`docs/Rules.md`](docs/Rules.md), [`docs/Phases.md`](docs/Phases.md), [`docs/Design.md`](docs/Design.md).

## Layout

```
apps/api            FastAPI: routes → controllers → services (the only HTTP layer)
apps/review_ui      Streamlit console (pages/, components/, api_client.py)
packages/orchestrator
  graph/            build_graph, state, edges, nodes/ (one file per node), serde (checkpoint allowlist)
  agents/           supervisor, research, analysis, writing, code_exec, reviewer, memory_extractor (prompt.md each)
  loop/             agent_loop (pausable), budgets, messages
  gate/             decide, classifier;   hitl/  escalation, approvals, timeouts, notify
  memory/           working (Redis), persistent (Postgres repositories), long_term (Chroma), extractor, consolidate
  llm/              openai_compat provider, chains (fallback + backoff), roles (models.yaml), embeddings
  tracing/          otel spans, cost, replay
  worker.py         Celery: run_task, resume_task, replay_task, expire_approvals, consolidate_memory, startup recovery
packages/tools      registry (policy.yaml, rate limits), mcp_servers/ (web_search, files, sandbox, database, actions)
packages/evals      golden_tasks/, runner, metrics, judge, diff, report, reports/
packages/shared     types/ (Pydantic models shared by everything), config (the only env reader), errors
infra               docker-compose, alembic migrations, seed generator, sandbox image
config              models.yaml (role chains), escalation.yaml, budgets.yaml
scripts             dev_up / dev_down, demo, run_task, run_subtask, smoke_provider
tests               unit/ (fast, no network) · integration/ (compose stack, real models where marked)
```

## Checks

```bash
uv run pytest -q          # unit tests, no network
uv run ruff check . && uv run mypy
uv run pytest -q -m integration tests/integration/   # needs the stack; some call real models
```

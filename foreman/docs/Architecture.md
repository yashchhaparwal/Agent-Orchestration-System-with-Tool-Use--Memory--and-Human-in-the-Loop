# Architecture — Foreman

Companion to `PRD.md`. Diagrams referenced here live in `../diagrams/`. Concepts are explained in `../PROJECT-15-DEEP-DIVE.md`; this document is the build specification.

---

## 1. Overview

Foreman is a LangGraph state machine executed by a worker, fronted by a FastAPI service, acting through MCP tool servers behind a permission gate, with Redis / PostgreSQL / ChromaDB as the three memory tiers and OpenTelemetry for tracing. See `diagrams/01-architecture-overview.png`.

```
client ──HTTP──▶ api (FastAPI) ──enqueue──▶ worker (Celery + LangGraph)
                                              │  LLM calls (tokenrouter / anthropic)
                                              │  tool calls ──▶ gate ──▶ MCP servers (HTTP)
                                              │  memory: redis (tier 1) · postgres (tier 2) · chroma (tier 3)
                                              └─ spans ──▶ OTel collector ──▶ Langfuse / Jaeger
operator ──HTTP──▶ review-ui (Streamlit) ──▶ api
```

## 2. Runtime topology (Docker Compose)

| Service | Image / build | Purpose | Ports (host) |
|---|---|---|---|
| `api` | `apps/api` | FastAPI: tasks, approvals, memory, traces, stats | 8000 |
| `worker` | `packages/orchestrator` | Celery worker that runs the graph; resumes paused graphs on decisions | — |
| `review-ui` | `apps/review-ui` | Streamlit operator screens | 8501 |
| `redis` | `redis:7` | Tier-1 working memory; Celery broker | 6379 |
| `postgres` | `postgres:16` | Tier-2 state; LangGraph checkpoints; app tables | 5432 |
| `chroma` | `chromadb/chroma` | Tier-3 semantic memory | 8001 |
| `ollama` | `ollama/ollama` | Local embeddings (`nomic-embed-text`) and the offline dev model; CPU only | 11434 |
| `mcp-web` | `packages/tools/mcp_servers/web_search` | search / fetch | 7001 |
| `mcp-files` | `…/files` | read / list / write within `WORKSPACE_ROOT` | 7002 |
| `mcp-sandbox` | `…/sandbox` | `run_python` in throwaway containers (mounts the Docker socket) | 7003 |
| `mcp-db` | `…/database` | `schema` / `query` as a SELECT-only user | 7004 |
| `mcp-actions` | `…/actions` | `send_email` / `create_calendar_event` / `call_api` → **outbox table** | 7005 |
| `jaeger` | `jaegertracing/all-in-one` | Receives OTLP spans directly (4317 gRPC / 4318 HTTP) and serves the trace UI. One container instead of collector + Langfuse (+ ClickHouse + MinIO); Langfuse is an optional compose profile later | 16686, 4317, 4318 |

All MCP servers use the **streamable HTTP** transport. Only `api`, `review-ui`, and the trace UI are exposed to the host in development.

## 3. Request flow

1. `POST /v1/tasks` → validate → insert `tasks` row (`status=queued`) → enqueue `run_task(task_id)` → 202.
2. Worker builds the graph with a `PostgresSaver` checkpointer and invokes it with `config={"configurable": {"thread_id": task_id}}`.
3. Nodes run in order (see §4). Each node's state update is checkpointed.
4. If a node calls `interrupt(payload)` (`approve_plan`, `await_approval`, `escalate`), that node has already written an idempotent `approvals` row (dedupe key per task/subtask/attempt/arguments) and notified Slack/UI; the graph returns, and the worker persists the progress so far and sets `tasks.status=awaiting_approval`.
5. `POST /v1/approvals/{id}/decide` → validate → enqueue `resume_task(task_id, decision)` → worker invokes the graph with `Command(resume=decision)` on the same thread id → execution continues inside the paused node.
6. On `deliver`, the final output and cost are written to `tasks`; `write_memory` runs; `status=done`.
7. Every step emits spans (see §9) with `task_id` as a trace attribute.

## 4. The graph

See `diagrams/02-langgraph-state-machine.png`.

### 4.1 State

```python
class TaskState(TypedDict):
    task_id: str
    user_id: str
    request: str
    options: TaskOptions  # require_human_review, budget_usd, …
    recalled_memories: list[MemoryRecord]
    plan: ExecutionPlan | None
    plan_confidence: float
    subtask_results: Annotated[dict[str, SubtaskResult], merge_dicts]  # reducer for fan-in
    review_verdicts: Annotated[dict[str, ReviewVerdict], merge_dicts]
    retry_counts: dict[str, int]
    pending_approval: ApprovalRequest | None
    final_output: Deliverable | None
    cost_ledger: Annotated[list[CostEntry], operator.add]
    trace_id: str
    error: str | None
```

### 4.2 Nodes

| Node | Type | Reads | Writes | LLM? |
|---|---|---|---|---|
| `intake` | function | request, options | task_id, trace_id | no |
| `recall_memory` | function | request, user_id | recalled_memories | no (embedding call) |
| `plan` | supervisor | request, recalled_memories | plan, plan_confidence | yes — supervisor role, structured `ExecutionPlan` |
| `approve_plan` | interrupt | plan | plan (possibly edited) | no |
| `dispatch` | function | plan, subtask_results | — (emits `Send` per ready subtask) | no |
| `research` / `analysis` / `writing` / `code_exec` | specialist (agent loop) | one subtask + predecessor outputs from tier 1 | subtask_results[id], cost entries | yes — specialist role |
| `review` | function with one LLM call | subtask result + subtask spec | review_verdicts[id] | yes — reviewer role, structured `ReviewVerdict` |
| `await_approval` | interrupt | pending_approvals (a specialist's paused loop) | approval_decisions[id] | no |
| `escalate` | interrupt | verdicts, pending_approval | decision outcome | no |
| `synthesize` | supervisor | plan, accepted results | final_output | yes — supervisor role, `medium` effort |
| `deliver` | function | final_output | tasks row, notification | no |
| `write_memory` | function with one LLM call | transcript | tier-3 upserts | yes — cheap role |

### 4.3 Edges

| From | To | Condition |
|---|---|---|
| START | intake | — |
| intake | recall_memory | — |
| recall_memory | plan | — |
| plan | dispatch | `plan_confidence ≥ PLAN_CONFIDENCE_THRESHOLD` and no L3 trigger |
| plan | approve_plan | otherwise |
| approve_plan | dispatch | resumed with approve/modify |
| approve_plan | END | resumed with reject / take over (take over → deliver first) |
| dispatch | specialist nodes | `Send(specialist, subtask)` for each subtask whose `depends_on` are all accepted |
| specialist | review | — |
| review | await_approval | a specialist paused on a gated tool call (L2) |
| await_approval | same specialist | resumed with the decision; the loop continues from its checkpoint |
| review | dispatch | accepted and other subtasks remain |
| review | same specialist | rejected and `retry_counts[id] < 2` (feedback in state) |
| review | escalate | rejected twice, or `verdict.score < REVIEW_ESCALATE_SCORE` |
| review | synthesize | all subtasks accepted |
| escalate | synthesize / specialist / deliver / END | per decision |
| synthesize | deliver | — |
| deliver | write_memory | — |
| write_memory | END | — |

Tool-call approvals (L2): LangGraph re-executes a whole node on resume, so an `interrupt()` inside the agent loop would replay its model calls. Instead the loop **pauses**: it serialises its messages, pending calls and ledgers into a `LoopCheckpoint` in state; the `await_approval` node interrupts; on resume the specialist node is re-entered with the checkpoint plus the decision and continues from that exact turn (see §6.3). A human rejection is recorded on the result (`denied_tools`) and seeds every retry of that subtask, so the agent cannot ask twice, and the reviewer is told not to penalise the missing action.

### 4.4 Checkpointing

`langgraph.checkpoint.postgres.PostgresSaver` with `.setup()` run once by a migration step. Thread id = `task_id`. Replay (F11) forks a thread from a chosen checkpoint id with state overrides and runs it under a new task id.

## 5. Agents

Each agent is a package under `packages/orchestrator/agents/<name>/` with `prompt.md`, `agent.py` (loop wiring), and `schemas.py` if it has private models.

| Agent | Role config | Effort | Tools (allow-list) | Output schema |
|---|---|---|---|---|
| supervisor | `supervisor` | `xhigh` (plan) / `medium` (synthesize) | none | `ExecutionPlan`, `Deliverable` |
| research | `specialist` | `medium` | `web_search`, `web_fetch`, `files_read_file`, `files_list_dir`, `db_query`, `db_schema` (+ `rag_search_docs` in v2) | `SubtaskResult` |
| analysis | `specialist` | `medium` | `db_query`, `db_schema`, `sandbox_run_python`, `files_read_file` | `SubtaskResult` |
| writing | `specialist` | `medium` | `files_read_file`, `files_write_file`, `actions_send_email`, `actions_create_calendar_event` | `SubtaskResult` |
| code_exec | `specialist` (may use `cheap`) | `medium` | `sandbox_run_python`, `files_*` | `SubtaskResult` |
| reviewer | `reviewer` | `high` | none | `ReviewVerdict` |
| memory extractor | `cheap` | `low` | none | `list[MemoryRecord]` |

### 5.1 The agent loop (`packages/orchestrator/loop/agent_loop.py`)

```
build messages (system prompt · subtask · predecessor outputs · recalled context)
for iteration in range(MAX_ITERATIONS):
    response = llm.chat(messages, tools=allowed_tool_schemas)   # span llm.call
    if no tool calls: return parse_final(response, SubtaskResult)   # one retry on validation error
    results = []
    for call in response.tool_calls:                               # concurrently
        decision = gate.decide(agent, call)                        # span gate.decide
        if decision.block:     results.append(error_result(call, decision.reason))
        elif decision.approve: return paused(checkpoint(messages, pending=call, ready=results))   # graph interrupts; resume re-enters here
        else:                  results.append(registry.invoke(call))  # span tool.<server>.<tool>
    messages += [assistant_turn(response), tool_results_turn(results)]   # ALL results in one turn
    check budgets (iterations, tokens, cost) → BudgetExceeded → escalate
```

## 6. Tool layer

### 6.1 Registry (`packages/tools/registry/`)

At worker startup, the registry connects to every MCP server, lists tools, and merges each with its policy entry from `policy.yaml`:

```yaml
tools:
  db.query:        {risk: safe,        agents: [research, analysis], rate_per_min: 60, timeout_s: 20}
  files.write_file:{risk: risky,       agents: [writing, code_exec], rate_per_min: 20, timeout_s: 10}
  sandbox.run_python: {risk: risky,    agents: [analysis, code_exec], rate_per_min: 10, timeout_s: 45}
  actions.send_email: {risk: destructive, agents: [writing], rate_per_min: 5, timeout_s: 10}
```

A tool with no policy entry is **not registered** (fail closed). The registry exposes `schemas_for(agent)` (OpenAI-format function schemas, or Anthropic-format when the native provider is active) and `invoke(call)`.

### 6.2 MCP servers (`packages/tools/mcp_servers/<name>/server.py`, each an `MCPServer` app)

| Server | Tool | Signature | Risk | Notes |
|---|---|---|---|---|
| web_search | `search` | `(query: str, max_results: int = 5) → list[SearchHit]` | safe | provider behind an interface; a fixture backend for tests |
| web_search | `fetch` | `(url: str, max_chars: int = 8000) → FetchedPage` | safe | strips scripts; marks truncation |
| files | `read_file` | `(path: str, max_chars: int = 20000) → FileContent` | safe | path confined to `WORKSPACE_ROOT` |
| files | `list_dir` | `(path: str = ".") → list[Entry]` | safe | confined |
| files | `write_file` | `(path: str, content: str) → WriteResult` | risky | confined; refuses overwrite unless `overwrite=true` |
| sandbox | `run_python` | `(code: str, timeout_s: int = 30) → ExecResult` | risky | new container per call: `--network none`, 512 MB, 1 CPU, read-only image, `/work` tmpfs |
| database | `schema` | `() → SchemaInfo` | safe | |
| database | `query` | `(sql: str, max_rows: int = 200) → QueryResult` | safe | SELECT-only DB user **and** `sqlparse` single-statement SELECT check |
| actions | `send_email` | `(to: str, subject: str, body: str) → OutboxReceipt` | destructive | writes to `outbox`; never sends |
| actions | `create_calendar_event` | `(title, start, end, attendees) → OutboxReceipt` | destructive | outbox |
| actions | `call_api` | `(method, url, payload) → OutboxReceipt` | destructive | outbox |

Every tool returns a typed result or raises a typed error mapped to `is_error: true` with a message the model can act on.

### 6.3 Permission gate (`packages/orchestrator/gate/`)

```
decide(agent, call) → Decision{allow | approve | block, reason, risk, latency_ms}
  1. tool not in registry or agent not in allow-list       → block
  2. rate limit exceeded                                   → block ("retry later")
  3. input fails schema validation                         → block (with validation error)
  4. risk == safe                                          → allow
  5. risk == destructive                                   → approve (L2)
  6. risk == risky → LLM classifier (cheap role) on (tool, args, subtask) → allow | approve
     (classifier failure → approve; never fail open)
```

`approve` → the loop pauses with a checkpoint and the graph interrupts with an `ApprovalRequest`; on resume, `approve` executes the call, `modify` executes with edited args (re-validated against the tool schema), `reject` returns an error result with the reason and blocks that tool for the rest of the subtask and its retries, `take over` makes the human's text the subtask result (accepted without a model review).

## 7. Memory

See `diagrams/04-memory-system.png`.

### 7.1 Tier 1 — Redis
Keys `task:{id}:plan`, `task:{id}:result:{subtask_id}`, `task:{id}:artifact:{name}`, `task:{id}:errors`; all with `TTL = TIER1_TTL_HOURS` (default 24). Specialists read predecessors' results from here; the graph state carries only ids and summaries to keep checkpoints small.

### 7.2 Tier 2 — PostgreSQL tables

| Table | Key columns |
|---|---|
| `tasks` | id, user_id, request, options, status, plan_json, final_output_json, cost_usd, created_at, updated_at |
| `subtasks` | id, task_id, specialist, spec_json, status, result_json, verdict_json, retries |
| `approvals` | id, task_id, subtask_id, level, trigger, proposed_action_json, reasoning, status, decision, decided_by, reason, created_at, decided_at, expires_at |
| `tool_invocations` | id, task_id, subtask_id, agent, tool, args_hash, risk, decision, ok, latency_ms, result_size, created_at |
| `llm_calls` | id, task_id, subtask_id, agent, provider, model, effort, input_tokens, output_tokens, cache_read_tokens, cost_usd, latency_ms, created_at |
| `audit_log` | id, task_id, actor, action, payload_json, created_at |
| `outbox` | id, task_id, kind, payload_json, created_at |
| `eval_runs` / `eval_results` | run id, baseline id, per-task metrics |
| LangGraph checkpoint tables | managed by `PostgresSaver.setup()` |

### 7.3 Tier 3 — ChromaDB
One collection **per embedding model** (`memories__<model>`, cosine space) — a different embedding model is a different vector space and never shares an index; the `embedding` chain only falls back to entries that serve the same model id. Document = lesson text. Metadata: `user_id, task_type, outcome, importance, created_at, last_accessed, access_count, tools_used, source_task_id`. Dedup at 0.92 cosine per user reinforces the existing record (importance +0.5, access +1) instead of inserting. Recall: filter by `user_id` (optionally `task_type`), top-5, no rerank in v1, keep ≤ 3 and ≤ 600 tokens, touch what was returned. Effective importance = stored × 0.5^(idle days / 30) is computed, never rewritten; the nightly consolidation expires records below 1.0 or older than 180 days. `GET/DELETE /v1/memory/users/{id}`.

## 8. Human-in-the-loop

See `diagrams/05-human-in-the-loop.png`.

Policy table `config/escalation.yaml` maps trigger → level:

```yaml
low_plan_confidence:   L3
sensitive_tool_call:   L2
repeated_failure:      L4
low_review_score:      L3
user_requested_review: L3
timeouts_hours: {L1: 0, L2: 24, L3: 48, L4: 48}
on_timeout:     {L2: reject, L3: cancel, L4: cancel}
```

Approval lifecycle: `pending → approved | modified | rejected | taken_over | expired`. Notifications: Slack webhook (optional) and the UI. A background beat task expires overdue approvals and resumes the graph with the timeout decision.

## 9. Observability

Span naming convention:

| Span | Attributes |
|---|---|
| `task` | task_id, user_id, status, cost_usd |
| `node.<name>` | task_id, node |
| `agent.<name>.iteration` | subtask_id, iteration |
| `llm.call` | agent, provider, model, effort, input_tokens, output_tokens, cache_read_tokens, cost_usd, latency_ms, stop_reason |
| `gate.decide` | tool, risk, decision, reason, latency_ms |
| `tool.<server>.<tool>` | args_hash, ok, result_size, latency_ms |
| `memory.recall` / `memory.write` | count, ids |
| `hitl.interrupt` / `hitl.resume` | approval_id, level, trigger, decision |

`cost_ledger` entries are derived from `llm.call` spans and written to `llm_calls`. `GET /v1/stats` aggregates: tasks by status and per day, completion rate, mean LLM calls / tokens / cost per task, escalation rate by level and trigger, approval rate, tool calls by tool, p50/p95 task latency, the all-time count of unapproved destructive actions, and the last eval report's headline (`packages/evals/reports/latest.json`).

`GET /v1/tasks/{id}/trace` merges every Jaeger trace tagged with the task id (one per worker invocation: the initial run and each resume) into a single depth-first span tree; when Jaeger is unreachable the ledgers (`llm_calls`, `tool_invocations`, `approvals`, `audit_log`) give a flat timeline instead. Replay (`packages/orchestrator/tracing/replay.py`) lists a task's LangGraph checkpoints, forks one into a new task (`aupdate_state` on a new thread with `as_node` = the node that produced the checkpoint, then `ainvoke`), applies overrides, and diffs the two task views.

## 10. LLM access layer (`packages/orchestrator/llm/`)

One interface, `LLMClient.chat(messages, tools, schema, role) → LLMResponse`, implemented by **one OpenAI-compatible class** (`openai_compat.py`) instantiated once per provider with its own `base_url` and key. **All default providers are free tiers; total LLM spend for the project is $0.**

| Provider id | Endpoint | Key | Used for (default roles) | Limits to design around |
|---|---|---|---|---|
| `tokenrouter_free` | `https://api.tokenrouter.com/v1` | `TOKENROUTER_FREE_API_KEY` | supervisor fallback (`qwen/qwen3.8-max-free` — the only model the free key can call, confirmed Day 0) | capacity-limited; stability not guaranteed → never first in a chain |
| `mistral` | La Plateforme `/v1` | `MISTRAL_API_KEY` | specialists (Medium/Large); supervisor fallback (Large) | 1B tokens/month; per-minute limits |
| `groq` | Groq `/openai/v1` | `GROQ_API_KEY` | cheap (`llama-3.3-70b-versatile`); specialists fallback | 30 RPM, 1,000 RPD, 100K TPD |
| `gemini` | Google AI Studio OpenAI-compatible endpoint | `GEMINI_API_KEY` | reviewer (Flash); cheap fallback (Flash-Lite); embeddings fallback | 10–15 RPM on free tier |
| `ollama` | local `http://ollama:11434/v1` | — | embeddings (`nomic-embed-text` / `bge-m3`); offline dev model (`qwen3:8b`) | CPU speed only |
| `tokenrouter` *(optional, paid, off)* | TokenRouter `/v1` | `TOKENROUTER_API_KEY` | nothing by default | only if the author opts in |
| `anthropic` *(optional, paid, off)* | `anthropic` SDK | `ANTHROPIC_API_KEY` | nothing by default | would enable strict tools, `messages.parse`, prompt caching, adaptive thinking + effort |

**Fallback chains.** Each role lists an ordered chain of `(provider, model)`. On 429, 5xx, timeout, or a malformed tool call that fails schema validation twice, the client moves to the next entry for that call and records `llm.call.fallback=true` on the span. When *every* entry failed for a retryable reason (all providers rate-limited at once), the chain backs off — 3 s, 8 s, 20 s — and retries the whole chain, then raises `RetryableError`. Chains never mix a free provider with a paid one unless the paid provider is explicitly enabled.

**Free-tier discipline (learned in Phase 2).** Each provider has a `concurrency` limit in `models.yaml`, enforced by a per-provider semaphore, because parallel specialists otherwise burst past free-tier rate limits. A chain entry must be able to take the role's request size: a specialist's tool loop reaches ~10k tokens per call once a document and the schema are in the history, which rules out Groq (8k tokens/minute on the free tier) for specialists — Groq serves only short-prompt roles. Assistant turns are re-sent with the standard fields only (`role`, `content`, `tool_calls`); provider-specific extras such as Groq's `reasoning` are rejected by other providers after a mid-loop fallback.

Role → model mapping in `config/models.yaml`:

```yaml
roles:
  supervisor:
    chain:
      - {provider: tokenrouter_free, model: deepseek/deepseek-v4-pro-0813-free}
      - {provider: mistral,          model: mistral-large-latest}
    effort: high
  specialist:
    chain:
      - {provider: mistral, model: mistral-medium-latest}
      - {provider: groq,    model: llama-3.3-70b-versatile}
    effort: medium
  reviewer:
    chain:
      - {provider: gemini,           model: <current Flash model on AI Studio>}
      - {provider: tokenrouter_free, model: qwen/qwen3.8-max-free}
    effort: high
  cheap:
    chain:
      - {provider: groq,             model: llama-3.3-70b-versatile}
      - {provider: tokenrouter_free, model: nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free}
    effort: low
  embedding:
    chain:
      - {provider: ollama, model: nomic-embed-text}
      - {provider: gemini, model: <current embedding model on AI Studio>}
dev:
  offline_model: {provider: ollama, model: qwen3:8b}     # tests use fixtures; this is for manual offline runs
paid_optional:                                           # not loaded unless ENABLE_PAID_PROVIDERS=true
  supervisor: {provider: anthropic, model: claude-opus-5, effort: xhigh}
  specialist: {provider: anthropic, model: claude-sonnet-5, effort: medium}
```

Exact model ids for Mistral, Groq, and Gemini are confirmed on Day 0 (free lineups rotate) and recorded in `Memory.md`. `effort` is passed through only when a provider supports it; otherwise it selects a prompt-level verbosity hint. The interface normalises tool-call and structured-output formats so agents never see provider differences.

## 11. API contract (`apps/api`)

| Method | Path | Body / params | Response |
|---|---|---|---|
| POST | `/v1/tasks` | `{request, user_id, require_human_review?, budget_usd?}` | 202 `{task_id, status}` |
| GET | `/v1/tasks/{id}` | — | `{status, plan, subtasks, final_output, cost_usd, pending_approval?}` |
| GET | `/v1/tasks/{id}/trace` | — | span tree from Jaeger (ledger timeline fallback) |
| GET | `/v1/tasks/{id}/checkpoints` | — | checkpoints, oldest first, with produced-by / next nodes |
| POST | `/v1/tasks/{id}/replay` | `{checkpoint_id, overrides}` | 202 `{task_id}` — a new, forked task; the source is never modified |
| GET | `/v1/tasks/{id}/diff` | — | trajectory diff of a replayed task against its source |
| GET | `/v1/approvals` | `?status=pending` | list with context packages |
| GET | `/v1/approvals/{id}` | — | full context package |
| POST | `/v1/approvals/{id}/decide` | `{decision: approve|modify|reject|take_over, payload?, reason}` | `{status}` |
| GET | `/v1/memory/users/{user_id}` | — | memories |
| DELETE | `/v1/memory/users/{user_id}` | — | `{deleted}` |
| GET | `/v1/tools` | — | registry view |
| GET | `/v1/stats` | `?days=7` | aggregates + the last eval headline |

Auth: static `X-API-Key` header checked by middleware. Errors: RFC 7807 problem details.

## 12. Folder structure

```
foreman/
├── docs/                          PRD.md · Architecture.md · Rules.md · Phases.md · Design.md · Memory.md
├── apps/
│   ├── api/
│   │   ├── main.py                app factory, router registration
│   │   ├── routes/                tasks.py · approvals.py · memory.py · tools.py · stats.py
│   │   ├── controllers/           thin request → service → response
│   │   ├── middleware/            auth.py · errors.py · request_logging.py
│   │   └── config/                settings.py (pydantic-settings) — the only env reader
│   └── review-ui/                 Streamlit: pages/ · components/ · api_client.py
├── packages/
│   ├── orchestrator/
│   │   ├── graph/                 build_graph.py · state.py · edges.py · nodes/ (one file per node)
│   │   ├── agents/                supervisor/ research/ analysis/ writing/ code_exec/ reviewer/ memory_extractor/
│   │   ├── loop/                  agent_loop.py · budgets.py · messages.py
│   │   ├── gate/                  decide.py · classifier.py · rate_limit.py
│   │   ├── hitl/                  escalation.py · approvals.py · timeouts.py · notify.py
│   │   ├── memory/                working.py · persistent.py (repositories) · long_term.py · extractor.py · consolidate.py
│   │   ├── tracing/               otel.py · cost.py · replay.py
│   │   ├── llm/                   client.py (interface) · tokenrouter.py · anthropic_native.py · roles.py · schemas.py
│   │   └── worker.py              Celery app: run_task · resume_task · expire_approvals
│   ├── tools/
│   │   ├── registry/              registry.py · policy.yaml · schemas.py
│   │   └── mcp_servers/           web_search/ files/ sandbox/ database/ actions/  (server.py + Dockerfile each)
│   ├── evals/                     golden_tasks/*.yaml · runner.py · metrics.py · judge.py · diff.py · reports/
│   └── shared/                    types/ (Pydantic models shared across packages) · utils/ · errors.py
├── infra/                         docker-compose.yml · Dockerfile.api · Dockerfile.worker · Dockerfile.mcp · otel-config.yaml · seed/
├── data/                          synthetic claims (generated by infra/seed) — gitignored except the generator
├── tests/                         unit/ integration/ e2e/
├── config/                        models.yaml · escalation.yaml
├── .env.example · .gitignore · Makefile · pyproject.toml · README.md
```

Responsibility rules (from Rules.txt §7): routes handle HTTP only; controllers map requests to services; services hold logic; repositories hold SQL; config is the only place env vars are read; shared types live in `packages/shared/types`.

## 13. Configuration (`.env`)

```
# LLM — free providers (all required for the default chains)
TOKENROUTER_FREE_API_KEY=       TOKENROUTER_BASE_URL=https://…/v1
MISTRAL_API_KEY=                GROQ_API_KEY=                    GEMINI_API_KEY=
OLLAMA_BASE_URL=http://ollama:11434/v1
# LLM — optional paid providers (off unless ENABLE_PAID_PROVIDERS=true)
ENABLE_PAID_PROVIDERS=false     TOKENROUTER_API_KEY=             ANTHROPIC_API_KEY=
# Stores
DATABASE_URL=postgresql://…     REDIS_URL=redis://redis:6379/0    CHROMA_HOST=chroma  CHROMA_PORT=8000
# Tracing
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317   LANGFUSE_PUBLIC_KEY=  LANGFUSE_SECRET_KEY=  LANGFUSE_HOST=
# Tools
WORKSPACE_ROOT=/workspace       SANDBOX_IMAGE=foreman-sandbox:latest   MCP_WEB_URL=http://mcp-web:7001 … (one per server)
# Policy
PLAN_CONFIDENCE_THRESHOLD=0.6   REVIEW_ESCALATE_SCORE=3   MAX_ITERATIONS=15   DEFAULT_TASK_BUDGET_USD=1.00   TIER1_TTL_HOURS=24
# Ops
API_KEY=                        SLACK_WEBHOOK_URL=        LOG_LEVEL=INFO
```

Phase 6–7 additions: `JAEGER_QUERY_URL` (trace endpoint), `EVALS_REPORTS_DIR` (eval reports, `latest.json` for the stats page), `BUDGETS_CONFIG_PATH` (`config/budgets.yaml`: a default loop budget, per-agent overrides for iterations / tokens / wall-clock / cost, and a whole-task cost cap; the specialist node picks the calling agent's budget). `scripts/dev_up.ps1` / `dev_up.sh` start the entire stack in order and record process ids under `.run/`; `scripts/demo.py` runs the showcase through the public API.

## 14. Security model

- **Gate is mandatory.** There is exactly one code path from an agent to a tool: `registry.invoke()` which is only reachable through `gate.decide()`. Tests assert this.
- **Fail closed.** Unknown tool, missing policy, classifier error → block or approve, never allow.
- **Least privilege.** DB user is SELECT-only; file tools confined to `WORKSPACE_ROOT` with canonical path checks; the sandbox container runs with `network_mode=none`, a read-only root filesystem (tmpfs `/work`), all capabilities dropped, `no-new-privileges`, an unprivileged user, memory/CPU/PID limits, and is killed on timeout and always removed.
- **No SSRF through the web tool.** `web_fetch` accepts only http(s) to hosts that resolve exclusively to public addresses — loopback, private, link-local, multicast and unspecified ranges are rejected, every redirect hop is re-checked, credentials in URLs are refused, and an optional hostname allow-list narrows it further. An agent can never be steered at Redis, Postgres, the metadata service, or another MCP server.
- **The actions server has no send path.** `send_email`, `create_calendar_event` and `call_api` validate their inputs and write an `outbox` row; there is no code that transmits anything. Destructive tools are therefore doubly gated: human approval at the gate, and a human draining the outbox.
- **Untrusted content.** Tool results are data. System prompts state this; results are typed and truncated; the injection suite is part of `make eval`.
- **Secrets.** Only `apps/api/config/settings.py` and the worker's equivalent read the environment. No secret is logged; `args_hash` not raw args for sensitive tools.
- **Audit.** Every approval decision, tool decision, and status change is an `audit_log` row with the actor.

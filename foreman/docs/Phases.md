# Phases — Foreman

Fourteen working days plus a Day 0. Each phase ends with something that runs and a "done when" line. Do not start the next phase until the current one is done and `Memory.md` is updated.

Time budget assumes 2–3 focused hours per day. If a phase overruns by more than a day, cut from the phase's "stretch" list, not from its "done when" line.

---

## Phase 0 — Setup and decisions (Day 0)

**Tasks**
1. Create the repo skeleton from `Architecture.md` §12 (empty packages with `__init__.py`, `pyproject.toml` with workspace packages, `Makefile`, `.env.example`, `.gitignore`).
2. Move the two keys from `tokens.txt` into `.env` as `TOKENROUTER_FREE_API_KEY` (used) and `TOKENROUTER_API_KEY` (paid, unused, `ENABLE_PAID_PROVIDERS=false`); delete `tokens.txt`; rotate the paid key.
3. **Free provider accounts.** Sign up (no card) for Mistral La Plateforme, Groq, and Google AI Studio; add `MISTRAL_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY` to `.env`. Add the `ollama` compose service and pull `nomic-embed-text` and `qwen3:8b`.
4. **Decision 1 — role assignment.** Write `scripts/smoke_provider.py --provider X --model Y` that (a) makes a chat call, (b) makes a tool call with two functions and checks the model picks the right one and returns valid JSON args, (c) requests JSON-schema output and validates it with Pydantic, (d) reports latency and any rate-limit headers. Run it against every entry in `config/models.yaml`. Any model failing (b) or (c) is removed from its chain. Record exact model ids and results in `Memory.md`.
5. **Decision 2 — Reviewer model.** Confirm Gemini Flash and `qwen3.8-max-free` both pass (c); the head-to-head on golden tasks happens in Phase 6.
6. **Decision 3 — Trace viewer.** Default Langfuse; add its compose service.
7. Docker Compose with `redis`, `postgres`, `chroma`, `ollama`, `otel-collector`, `langfuse`; `make up` brings them up healthy.
8. `docs/Memory.md` created with the first entry.

**Done when** `make up` is healthy, every chain in `models.yaml` has at least one model that passes the smoke script, and `Memory.md` exists.

---

## Phase 1 — Skeleton + one agent (Days 1–2)

**Build**
- `packages/shared/types`: `Subtask`, `SubtaskResult`, `ToolCall`, `ToolResult`, `CostEntry`.
- `packages/orchestrator/llm`: `LLMClient` interface; `openai_compat.py` — one class, instantiated per provider from `base_url` + key (chat, tools, JSON-schema output, usage → cost, rate-limit header parsing); `chains.py` — per-role fallback chain (429 / 5xx / timeout / schema-fail ×2 → next entry, span attribute `fallback=true`); `roles.py` reading `config/models.yaml`.
- `packages/orchestrator/tracing/otel.py`: tracer setup; `llm.call` span helper; cost computation.
- `packages/tools/mcp_servers/database` and `files` servers (`MCPServer`, streamable HTTP, Dockerfiles). Database: SELECT-only user + `sqlparse` guard. Files: canonical path confinement.
- `packages/tools/registry`: discover tools from the two servers; `policy.yaml`; `schemas_for(agent)`; `invoke(call)`.
- `packages/orchestrator/loop/agent_loop.py` with all four guards; `budgets.py`.
- One specialist: `agents/research/` with `prompt.md` and `agent.py`.
- `infra/seed/`: synthetic claims generator (Postgres tables + document files), deterministic seed.
- A CLI: `python -m foreman.run_subtask "<subtask text>"` that runs the research agent once and prints the `SubtaskResult`.

**Tests**
- Unit: path confinement (traversal, symlink, absolute, encoded); SQL guard (rejects non-SELECT, multi-statement, comments-trick); registry fails closed on missing policy; loop stops at `MAX_ITERATIONS`; parallel results returned in one turn (fixture-driven).
- Integration: research agent completes a fixture subtask against the seeded DB and files server.

**Done when** the CLI runs a real subtask with two tool calls and Langfuse shows the span tree: `agent.research.iteration` → `llm.call` → `tool.db.query` / `tool.files.read_file`.

**Stretch:** structured `SubtaskResult` retry-on-validation-error path.

---

## Phase 2 — The graph (Days 3–4)

**Build**
- `graph/state.py` (`TaskState` with reducers), `graph/edges.py`, `graph/nodes/` (intake, recall_memory stub, plan, dispatch, review, synthesize, deliver, write_memory stub), `graph/build_graph.py` with `PostgresSaver`.
- `agents/supervisor/` (plan + synthesize prompts; `ExecutionPlan`, `Deliverable` schemas) and `agents/reviewer/` (`ReviewVerdict`).
- Remaining specialists as thin copies of research: `analysis`, `writing`, `code_exec` (tools arrive in Phase 3; for now they have the files/db tools).
- Dependency-aware `dispatch` with `Send`; review → retry (max 2) → escalate stub.
- `worker.py` with `run_task`; `apps/api` with `POST /v1/tasks`, `GET /v1/tasks/{id}`; settings module; auth middleware; error middleware.
- Tier-2 repositories for `tasks`, `subtasks`, `llm_calls`; Alembic migration.

**Tests**
- Unit: every edge function on synthetic states; `dispatch` emits `Send` only for ready subtasks; reducers merge fan-in correctly; `ExecutionPlan` validation rejects cycles in `depends_on`.
- Integration: a 3-subtask task with A → B → C dependencies runs end to end (fixture LLM); **kill the worker mid-task, restart, task completes from its checkpoint.**

**Done when** `POST /v1/tasks` with the showcase request completes with a plan, three accepted subtasks, and a deliverable, and the restart test passes.

---

## Phase 3 — Tools and the gate (Days 5–6)

**Build**
- MCP servers: `sandbox` (Docker SDK; container per call; no network; limits; tmpfs `/work`), `web_search` (provider interface + fixture backend), `actions` (outbox table only).
- Full `policy.yaml`; per-agent allow-lists; rate limiter (Redis token bucket); timeouts.
- `gate/decide.py` with the algorithm in `Architecture.md` §6.3; `gate/classifier.py` — a small LLM classification of (tool, args, subtask) on the `cheap` role, with a fixture mode for tests.
- `tool_invocations` repository; `gate.decide` and `tool.*` spans.
- Import-scan test enforcing "one path to a tool".

**Tests**
- Unit: gate matrix (every risk × every agent × allow-list × rate-limit state); classifier failure → `approve`; unknown tool → `block`; sandbox refuses network (attempt a socket in the code); sandbox timeout kills the container; actions write to the outbox and nothing else.
- Integration: writing agent proposes `send_email` → gate returns `approve` → the loop pauses with a checkpoint (Phase 4 wires the graph interrupt; for now the test asserts the pause).

**Done when** a destructive call is stopped with a logged reason, an unauthorised tool is blocked, the sandbox runs code with no network, and the import-scan test is green.

---

## Phase 4 — Human-in-the-loop (Days 7–8)

**Build**
- `hitl/escalation.py` (trigger → level from `config/escalation.yaml`), `hitl/approvals.py` (lifecycle, context package builder), `hitl/timeouts.py` (Celery beat task), `hitl/notify.py` (Slack webhook, optional).
- `interrupt()` inside the loop for L2 and in `approve_plan` / `escalate` nodes for L3/L4; `Command(resume=…)` handling for all four decisions; `worker.resume_task`.
- API: `GET /v1/approvals`, `GET /v1/approvals/{id}`, `POST /v1/approvals/{id}/decide`; `audit_log` rows.
- Operator UI v1 (Streamlit): approval queue page, approval detail page with the context package and four buttons.

**Tests**
- Integration: L2 pause on `send_email`; decide approve → executes (outbox row); modify → executes with edited args; reject → model receives error result and returns a draft; take over → deliverable is the human's text with `human_authored=true`. Timeout expiry rejects L2 and cancels L3. **Pause, restart the worker, decide, resume — completes.**

**Done when** the showcase task pauses on `send_email`, survives a worker restart, and completes correctly under each of the four decisions from the UI.

**Stretch:** Slack notification with a deep link to the approval page.

---

## Phase 5 — Memory (Days 9–10)

**Build**
- Tier 1: `memory/working.py` — Redis keys with TTL; specialists read predecessor outputs from here; graph state holds ids/summaries only.
- Tier 3: `memory/long_term.py` (Chroma collection, embed via the `embedding` role, dedup at 0.92, metadata), `memory/extractor.py` (cheap role, `list[MemoryRecord]`), `recall_memory` node (filter by user, top-5, trim to ≤ 3 / ≤ 600 tokens, labelled injection into the planner prompt), `write_memory` node.
- Importance/expiry job (`memory/consolidate.py` v1: decay + expire; clustering is v2).
- API: `GET/DELETE /v1/memory/users/{id}`; `memory.recall` / `memory.write` spans recording ids.

**Tests**
- Unit: dedup bumps importance instead of inserting; trim respects the token cap; delete removes every record for the user and nothing else; recalled memories appear under the labelled heading and nowhere else in the prompt.
- Integration: run the showcase task twice for the same user; the second run's `memory.recall` span lists the lesson written by the first, and the plan differs accordingly (fixture LLM asserts the injected context was present).

**Done when** a second, similar task recalls a lesson from the first and the trace shows it; `DELETE` leaves zero records.

---

## Phase 6 — Evals and observability (Days 11–12)

**Build**
- `packages/evals/golden_tasks/`: 30–50 YAML tasks across categories (lookup, multi-step, dependent, must-escalate, must-not-call, unanswerable, injection) and difficulties.
- `runner.py` (k runs per task, default 3), `metrics.py` (task success, tool precision/recall, unnecessary-call rate, escalation precision/recall, steps, cost, latency, pass^k, injection resistance), `judge.py` (rubric scoring on the reviewer role — different family), `diff.py` (vs. baseline run), Markdown report to `reports/`.
- `GET /v1/stats`; `GET /v1/tasks/{id}/trace`; Streamlit pages: task list, trace tree (from spans), stats.
- Replay v1: `python -m foreman.replay <task_id> --from <checkpoint_id> --set key=value` forks the thread; `POST /v1/tasks/{id}/replay`.
- **Decision 2 closed:** run five golden tasks with each reviewer candidate; pick; record in `Memory.md`.

**Tests**
- Unit: every metric on hand-built trajectories; diff detects new failures/passes.
- Eval gate: `make eval` runs the full set with k=3.

**Done when** `make eval` prints the report with all metrics, a baseline is saved, and a replay from a mid-task checkpoint produces a diffed trajectory.

---

## Phase 7 — Hardening and cost (Day 13)

**Build**
- Injection suite in the golden set (planted document; assert zero unapproved side effects).
- Retry/backoff on `RetryableError`; error taxonomy wired end to end; worker boundary → `failed` status + audit.
- Budgets tuned: per-agent `MAX_ITERATIONS`, cost caps; cost-per-task on the stats page.
- **Free-tier resilience:** run the full eval twice in one day and confirm no chain is exhausted; tune chain order from the `fallback=true` span counts; add Cerebras or GitHub Models as a third entry on any chain that hit its limit.
- **Optional paid comparison (skip by default):** only if the author chooses to enable `ENABLE_PAID_PROVIDERS=true`, run the golden set once on the `paid_optional` roles and record the quality/cost delta versus the $0 stack. This is a portfolio data point, not a requirement.
- README written as internal onboarding docs (not a tutorial): what it is, how to run, how to add a tool, how to add a golden task, how the gate decides, architecture decisions with rationale.

**Done when** the injection suite is green, `make eval` shows no regressions vs. Phase 6 baseline, and the README lets a stranger run `make demo`.

---

## Phase 8 — Portfolio (Day 14)

**Build**
- `make demo`: seeds data, runs the showcase task, pauses on `send_email`, prints the approval URL, resumes on decision, prints the deliverable and the trace link.
- 4-minute screen recording (OBS): request in → plan → tool calls in the trace → escalation → operator reject → draft delivered → memory written → eval report.
- `docs/` finalised; diagrams embedded in the README; headline numbers from the latest eval run in the first paragraph.
- Resume entry drafted with the real numbers.

**Done when** `make demo` runs on a clean clone, the recording exists, and the README headline has numbers.

---

## Phase map to PRD features

| Phase | Features |
|---|---|
| 1 | F3 (one agent), F6 (two servers), F10 (spans) |
| 2 | F1, F2, F3 (all four), F4, F5 |
| 3 | F6 (all five), F7 |
| 4 | F8, F13 (queue + detail) |
| 5 | F9 |
| 6 | F10 (stats, trace view), F11, F12, F13 (task list, trace) |
| 7 | F12 (injection), hardening |
| 8 | F14 |

## Session ritual

Start: read `Memory.md`, the current phase here, and `Rules.md` §2 and §10.
End: tests green or the failure recorded; `Memory.md` entry written; commit.

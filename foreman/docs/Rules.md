# Rules — Foreman

Boundaries for any AI coding assistant (and for the author) working in this repository. These extend the global Rules.txt §7; where they conflict, the stricter rule wins.

---

## 1. Stack — use / avoid

| Use | Avoid | Why |
|---|---|---|
| Python 3.11+, `asyncio` | threads for concurrency | one concurrency model |
| **LangGraph** for the graph (`StateGraph`, `Send`, `interrupt`, `Command`, `PostgresSaver`) | LangChain agents/chains, AutoGen, CrewAI, any second orchestration framework | one place for control flow |
| `openai` SDK, one class, per-provider `base_url` + key (TokenRouter free, Mistral, Groq, Gemini, Ollama — all OpenAI-compatible); `anthropic` SDK only behind `ENABLE_PAID_PROVIDERS` | hand-rolled HTTP to model endpoints; one SDK per provider; any paid provider in a default chain | one client, many free providers, $0 default |
| `mcp` Python SDK 2.x (`MCPServer` servers, `mcp.Client` in the registry) | ad-hoc JSON-RPC | standard protocol, standard testing |
| Pydantic v2 for every model crossing a boundary | dicts / `TypedDict` for cross-agent payloads (TypedDict is fine for graph state only) | validation at boundaries |
| `pydantic-settings` in `config/` | `os.environ` anywhere else | one env reader |
| SQLAlchemy 2 + Alembic (repositories) | raw SQL outside `memory/persistent.py` repositories | one data-access layer |
| `redis` (async) | in-process caches for shared state | specialists run in parallel workers |
| ChromaDB client | building a vector index by hand | scope |
| OpenTelemetry SDK + OTLP exporter | `print`, ad-hoc log lines for tracing | spans are the observability contract |
| `structlog` for logs | `print` | structured, correlatable with `task_id` |
| `pytest`, `pytest-asyncio`, `respx`/fixtures for LLM and MCP | tests that call paid models by default | evals call models; unit tests do not |
| Streamlit for the v1 operator UI | React before Phase 8 | scope |
| Docker Compose | anything cloud-hosted | Rules.txt §8: local only |

Adding a dependency requires checking that nothing already in the project provides it.

## 2. Architecture rules (non-negotiable)

1. **One path to a tool.** Agents reach tools only through `gate.decide()` → `registry.invoke()`. No direct MCP client calls from agent code. A test enforces this by import scanning.
2. **Fail closed.** Unknown tool, missing policy entry, classifier error, schema failure → `block` or `approve`. Never `allow` by default.
3. **Typed hand-offs.** `ExecutionPlan`, `SubtaskResult`, `ReviewVerdict`, `Deliverable`, `MemoryRecord`, `ApprovalRequest` are Pydantic models. No free-text between agents.
4. **Conditional edges are plain Python.** They inspect typed state. An LLM never decides which node runs next.
5. **The LLM is called inside nodes only.** Never between nodes, never in edges, never in the API layer.
6. **All parallel tool results return in one turn.** Never split results across messages.
7. **Errors are results.** A failed tool returns `is_error: true` with a reason; the loop never swallows a tool failure.
8. **Guards are mandatory in the loop.** `MAX_ITERATIONS`, per-subtask cost budget, per-tool timeout, task budget. A loop without all four does not merge.
9. **Frozen prefix per agent.** System prompt and tool list do not change during a subtask. (Also what makes prompt caching work on the native provider.)
10. **Timeouts never auto-approve.** L2 → reject; L3/L4 → cancel.
11. **Memory injection is context, not instruction.** Recalled memories are inserted under a labelled heading and the system prompt says they are advisory.
12. **Checkpoint everything.** Every node returns through the checkpointer; no node does side effects that cannot be replayed safely (external actions go to the outbox).

## 3. Code rules (Rules.txt §7, made specific)

- One responsibility per file; a file over ~300 lines is a refactor signal; a function over ~60 lines is one too.
- Layers: `routes` → `controllers` → `services` → `repositories`. No layer skips another; no layer calls upward.
- Naming: modules `snake_case`, classes `PascalCase`, Pydantic models are nouns (`SubtaskResult`), functions are verbs (`decide`, `recall`), graph nodes are named exactly as in `Architecture.md` §4.2.
- No dead code, no commented-out code, no debugging statements in a completed feature.
- Docstrings on public functions state *what* and *why*, not *how*.
- Type hints everywhere; `mypy --strict` on `packages/`.
- Formatting: `ruff format` + `ruff check` clean before commit.
- Do not modify unrelated code while implementing a feature. Preserve behaviour unless the phase task says otherwise.

## 4. Error handling

Error taxonomy in `packages/shared/errors.py`:

| Class | Meaning | Handling |
|---|---|---|
| `RetryableError` | 429, 5xx, timeouts, connection errors | retry with exponential backoff (3×), then escalate as `repeated_failure` |
| `NonRetryableError` | 4xx other than 429, validation failures | no retry; surface as `is_error` result or fail the node |
| `ToolDeniedError` | gate blocked the call | return as error result to the model |
| `ApprovalRequiredError` | gate requires a human | `interrupt()` |
| `BudgetExceededError` | iterations / tokens / cost | escalate `repeated_failure` at L4 |
| `SchemaValidationError` | model output failed Pydantic | one retry with the error appended; then `NonRetryableError` |

Rules: catch the most specific class first; never catch bare `Exception` except at the worker boundary (where it becomes `tasks.status=failed` + audit row). Every error path emits a span event with the class name.

## 5. Logging and tracing

- Every LLM call, gate decision, tool invocation, memory operation, interrupt, and resume produces a span with the attributes in `Architecture.md` §9. No exceptions.
- Logs are structured (`structlog`) and always include `task_id`; `subtask_id` and `agent` when known.
- Never log secrets, raw prompts at INFO, or raw tool arguments for `risky`/`destructive` tools (hash them).
- Cost is computed from token usage at call time using the model price table in `config/models.yaml`; unknown model → cost `null` and a warning, never a silent zero.

## 6. Secrets and configuration

- `.env` is gitignored; `.env.example` is committed with empty values.
- The only code that reads the environment is the settings module. Everything else receives typed settings by injection.
- `tokens.txt` on the desktop is not part of the repo. Its two keys become `TOKENROUTER_FREE_API_KEY` and `TOKENROUTER_API_KEY` in `.env`; the file is then deleted and the paid key rotated. Free-provider keys (`MISTRAL_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`) live only in `.env`.
- **No paid provider in an effective default chain.** `ENABLE_PAID_PROVIDERS` defaults to `false`; a chain may *list* paid entries (e.g. `explabs` gpt-6-astra first), but the loader drops them while the flag is false, and a role that would end up empty is a config error. A test asserts the effective default chains contain only free providers.
- Never commit `data/` contents other than the generator script.

## 7. Data rules

- All demo data is synthetic and generated by `infra/seed/`. No employer data, no real names, no real documents.
- Generated names use a fixed fake-name list; generated amounts, dates, and lender names are randomised with a seed for reproducibility.
- The planted injection document is clearly labelled in the generator so it is never mistaken for a real document.

## 8. Testing rules

| Level | Scope | Must not |
|---|---|---|
| Unit | every node, the gate, the registry, edges, budgets, path confinement, SQL guard | call any model or network |
| Integration | graph resume after restart; interrupt → decide → resume for all four decisions; timeout expiry; Celery task flow | call paid models (use the free provider or fixtures) |
| E2E | the showcase task through the API | run on every commit (runs in `make demo`) |
| Evals | golden set via `packages/evals/runner.py` | be skipped before a phase is marked done |

- A feature is not done until its unit tests pass and the phase's eval gate (see `Phases.md`) is green.
- LLM outputs in unit tests come from recorded fixtures under `tests/fixtures/llm/`.
- The import-scan test for rule 2.1 and the "fail closed" tests for rule 2.2 are permanent; do not skip them.

## 9. Git and session rules

- One branch per phase; commit at least at every "done when" line in `Phases.md`.
- Commit message: `phase-N: <what>`; body lists files touched and tests added.
- **`docs/Memory.md` is updated at the end of every coding session**: what was done, what is half-done, decisions made, next step. A session that ends without a Memory.md entry is not finished.
- Never rewrite history on the main branch.

## 10. What the AI assistant must not do

- Bypass or "temporarily disable" the gate, the guards, or the fail-closed behaviour to make a demo work.
- Add an auto-approve path, a default `allow`, or a timeout that approves.
- Put an LLM call in an edge, a route, or a controller.
- Replace typed hand-offs with strings "for now".
- Change the tool list or system prompt mid-loop.
- Use employer data, or fabricate "real-looking" data with real organisations' names.
- Introduce a second orchestration or agent framework.
- Read environment variables outside the settings module.
- Mark a phase done without running its tests and eval gate.
- Skip the Memory.md update.

## 11. Definition of done (per feature)

1. Acceptance criteria in `PRD.md` §5 met.
2. Unit tests written and green; integration tests where the feature touches the graph, HITL, or persistence.
3. Spans emitted with the required attributes.
4. `ruff` + `mypy` clean.
5. No new dependency without justification in the commit body.
6. `docs/Memory.md` updated.
7. Reviewed once for duplication, separation of concerns, and file size; refactored if needed.

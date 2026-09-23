# PRD — Foreman: Agent Orchestration System with Tool Use, Memory, and Human-in-the-Loop

| | |
|---|---|
| Working name | **Foreman** (a supervisor that delegates work, checks it, and asks before doing anything irreversible). Rename freely. |
| Owner | Sayed Mohammad Firdousi |
| Drafted | 27 August 2026 |
| Status | Pre-build |
| Source of truth for concepts | `../PROJECT-15-DEEP-DIVE.md` |
| Companion docs | `Architecture.md` · `Rules.md` · `Phases.md` · `Design.md` · `Memory.md` (created on day 1 of coding) |

---

## 1. Summary

Foreman is a service that takes a complex task in plain language, plans it, executes the plan through specialist AI agents that act via gated tools, checks every result, remembers what it learned, and stops to ask a human whenever it is unsure or about to do something irreversible. Every decision is traced and replayable.

It is a portfolio project whose purpose is to demonstrate — in code, numbers, and a demo — that the author can build production-grade autonomous AI systems, not just call an LLM.

## 2. Problem

Once an LLM can act (query databases, run code, send email), four problems appear that a chat interface never has:

| Problem | What goes wrong without a solution |
|---|---|
| Decomposition | Multi-step tasks with dependencies are executed out of order or half-done |
| Safety | The agent sends the wrong email, deletes the wrong file, calls the wrong API |
| Amnesia | Every run starts from zero; the same mistakes and re-discoveries repeat |
| Opacity | A bad final result cannot be traced to the step that caused it |

Foreman solves all four in one system: a planning graph, a permission gate with human approval, a three-tier memory, and full tracing with replay.

## 3. Users

| Persona | Who | What they need from Foreman |
|---|---|---|
| **Integrator** | A developer submitting tasks via the API | A simple `POST /tasks` that returns a task id, a status they can poll, a final deliverable they can trust, and a cost they can see |
| **Operator** | The human reviewer on the approval queue | Enough context to decide in under a minute: what was asked, what has been done, what the agent proposes and why, and one-click approve / modify / reject / take over |
| **Builder** | The author | A codebase that is testable node by node, replayable, and cheap enough to run evals daily |
| **Evaluator** | A hiring manager or interviewer reading the repo and watching the demo | Clear architecture, honest numbers, and evidence the safety and evaluation layers are real, not decorative |

## 4. Goals and non-goals

### Goals
1. Execute multi-step tasks end to end through a supervisor → specialists → reviewer graph.
2. Route **every** tool call through a permission gate; destructive actions never execute without human approval.
3. Pause and resume across process restarts and across hours of waiting for a human.
4. Improve over time via long-term memory, and **measure** that improvement.
5. Produce trajectory-level evaluation numbers (task success, tool-call precision, escalation precision, cost per task, pass^k).
6. Run on a laptop with Docker Compose; no GPU.

### Non-goals (for this project)
- A general chat assistant or conversational UI.
- Real external side effects (email/calendar/API calls write to an **outbox** in the demo; no real sending).
- Multi-tenant auth beyond a static API key.
- Fine-tuning any model.
- Production-grade React UI in v1 (Streamlit is acceptable for the operator screens; React is v2).

## 5. Scope — MVP features

Each feature has acceptance criteria. A feature is done when the criteria are met **and** the relevant tests and eval checks in `Phases.md` pass.

| ID | Feature | Acceptance criteria |
|---|---|---|
| F1 | **Task API** | `POST /v1/tasks` returns 202 with `task_id`; `GET /v1/tasks/{id}` returns status, plan, subtask results, final output, cost |
| F2 | **Supervisor planning** | Produces a typed `ExecutionPlan` (subtasks, specialist, dependencies, expected outputs, confidence, sensitive actions). Can produce a one-subtask plan for simple requests |
| F3 | **Specialist agents ×4** | Research, Data Analysis, Writing, Code Execution — each runs the agent loop with its own system prompt, tool allow-list, model, effort; each returns a typed `SubtaskResult` |
| F4 | **Dependency-aware dispatch** | Independent subtasks run in parallel; dependent subtasks wait for and receive predecessors' outputs |
| F5 | **Reviewer** | Every `SubtaskResult` is scored by a different model family; reject → retry with feedback (max 2) → escalate |
| F6 | **Tool layer via MCP** | Five MCP servers written in this repo (web search, files, sandbox, database, actions); tools discovered at startup; registry attaches policy (allowed agents, risk class, rate limit, timeout) |
| F7 | **Permission gate** | Every tool call classified safe / risky / destructive; decision allow / approve / block; unauthorised tool → block; all decisions logged with reason |
| F8 | **Human-in-the-loop** | Five triggers, four levels (Notify, Approve action, Approve plan, Take over) as configuration; `interrupt()`-based pause; `POST /v1/approvals/{id}/decide` resumes; timeout never auto-approves |
| F9 | **Memory** | Tier 1 Redis task state with TTL; tier 2 Postgres checkpoints + records; tier 3 ChromaDB lessons with extractor, dedup, recall-and-inject, importance/expiry, `DELETE /v1/memory/users/{id}` |
| F10 | **Observability** | OpenTelemetry span per LLM call, tool call, gate decision, review, memory op, escalation; cost ledger per task/agent/model; trace viewable in Langfuse or Jaeger |
| F11 | **Replay** | Re-run any task from checkpoint *k* with overridden inputs (CLI + API); diff the trajectories |
| F12 | **Evaluation harness** | 30–50 golden tasks; trajectory metrics; regression diff vs. baseline; injection suite; `make eval` produces a report |
| F13 | **Operator UI (v1)** | Approval queue, approval detail with context package, task list, trace tree — Streamlit |
| F14 | **Demo** | `make demo` runs the showcase task on a clean machine, including one escalation |

### v2 (only after MVP evals are green)
- React operator UI with the trace explorer and inline replay.
- Chat panel on the approval screen ("ask the agent why").
- Nightly memory consolidation job.
- Cost-aware model routing per subtask (Project 2's idea).
- Hybrid-search retrieval tool from Project 6 as the Research specialist's primary tool.

## 6. Demo scenario — synthetic consumer-credit claims

**All data is generated. No employer data, ever.**

- Postgres: `claims`, `loans`, `lender_documents` tables seeded with ~200 synthetic claims.
- Files: generated lender-response documents (markdown and PDF) under `data/claims/{id}/`.
- A `ruleset.md` describing affordability-check rules the agents must cite.
- A planted document containing an instruction-injection attempt (for the eval suite).

**Showcase task:** "Summarise the lender documents for claim #4471 and draft the complaint letter."
Expected path: recall memory → 3-subtask plan (research → analysis → writing) → tool calls through the gate → reviewer accepts → Writing proposes `send_email` → gate escalates (L2) → operator rejects with "draft only" → agent returns the letter as a draft → synthesis → delivery → memory write ("this user wants drafts, never auto-send").

## 7. Success metrics

Reported by the eval harness, per category and difficulty, with a diff against the previous run.

| Metric | Target for the portfolio headline |
|---|---|
| Task success rate (assertions + rubric) | ≥ 85 % on the golden set |
| Tool-call precision / recall | ≥ 0.9 / ≥ 0.9 |
| Escalation precision / recall | ≥ 0.9 / 1.0 (never miss a required escalation) |
| Unapproved destructive actions | **0** across all eval runs, including the injection suite |
| pass^3 | ≥ 70 % |
| Mean cost per completed task | reported; target ≤ $0.30 on the showcase task |
| Memory recall lift | reported: success rate with recall vs. without, on repeated task types |
| Gateway overhead | gate decision < 50 ms for rule-based, < 1.5 s when the classifier runs |

## 8. Constraints and assumptions

- **Local only.** Everything runs in Docker Compose on the author's laptop (Rules.txt §8: no artifacts, no cloud-hosted deliverables).
- **Budget: $0 for LLM calls.** Every role runs on permanent free tiers (TokenRouter free models, Mistral La Plateforme, Groq, Google AI Studio, local Ollama) with per-role fallback chains. Paid providers are optional config entries, off by default; no phase depends on them. Per-task budgets still apply so a runaway loop is caught even at $0.
- **LLM access.** All default providers are OpenAI-compatible, so the LLM layer is one client class instantiated per provider. Free-tier models are weaker at tool calling than paid frontier models; the guards, reviewer, retries, and trajectory evals are designed to absorb that, and free lineups rotate, so model ids live in `config/models.yaml` and are re-confirmed on Day 0. See `Architecture.md` §10.
- **Free-tier data policy.** Free tiers may use prompts for training. Acceptable because all data is synthetic; this is one more reason employer data is never used.
- **Synthetic data only.**
- **Timeline.** 14 working days at 2–3 hours per day, plus a Day 0 for setup.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Over-scoping (the guide's version has too many subsystems) | `Phases.md` builds one agent first; reviewer is a node, not an agent; replay is CLI-first; consolidation is a script |
| Agent loops forever on a broken tool | Max iterations, per-tool timeouts, per-subtask cost budget, task budget |
| Tool results carry injected instructions | Typed results, truncation, gate indifferent to model intent, injection suite in evals |
| Prompt cache never hits (cost blow-up) | Only relevant on the `anthropic` provider; frozen prefix per agent; `cache_read_input_tokens` on the dashboard |
| Non-determinism makes evals noisy | pass^k, three runs per golden task, per-category reporting |
| Losing context between coding sessions | `Memory.md` updated at the end of every session (Rules.txt §6) |

## 10. Open decisions (resolve on Day 0)

1. **Free-provider roles.** Create free accounts on Mistral La Plateforme, Groq, and Google AI Studio (no card). Run the Day-0 smoke script (chat, two-function tool call, JSON-schema output) against each provider and each TokenRouter free model. Assign roles per `Architecture.md` §10 from the results; record exact model ids in `Memory.md`. No paid key is needed.
2. **Reviewer model.** Default is Gemini Flash (different family from supervisor and specialists); confirm against `qwen3.8-max-free` by running five golden tasks with each in Phase 6.
3. **Trace viewer.** Decided on Day 0: Jaeger all-in-one (accepts OTLP directly; one container). Langfuse needs ClickHouse + MinIO + its own Postgres, so it is deferred to an optional compose profile.
4. **Operator UI.** Streamlit for v1 (confirmed unless the author wants React from the start).

# Foreman

Foreman is an agent orchestration system for structured, high-risk workflow execution. It coordinates a supervisor, specialist agents, reviewer models, gated tool access, human approvals, persistent checkpoints, and memory layers to complete tasks safely and transparently.

This project is designed for domain work where the system must be accountable: tool use is gated, irreversible actions are queued instead of sent immediately, every task is traceable, and every decision can be replayed from a saved checkpoint.

---

## Why Foreman

- Multi-agent planning and execution with a state-driven graph
- Specialist agents restricted to approved MCP tools only
- Human-in-the-loop approval for risky or destructive actions
- Redis-backed working memory and shared rate limiting
- PostgreSQL-backed task state and checkpoint persistence
- Chroma-based long-term memory for lessons and recall
- Reviewer model validation to reduce model self-confirmation
- Full observability via tracing and replay tooling
- Evaluation harness with golden tasks and safety checks

---

## Architecture

Foreman combines:

- LangGraph for task flow and checkpointing
- FastAPI for the operational API
- Celery + Redis for async workers and queueing
- PostgreSQL for durable task state and approvals
- Chroma for semantic memory
- Docker Compose for local infrastructure
- MCP servers for tools such as web search, files, sandboxing, DB access, and actions

A task generally flows like this:

```text
User Request
   ↓
Intake / Memory Recall
   ↓
Plan
   ↓
Dispatch Specialists
   ↓
Tool Execution via Gate
   ↓
Reviewer
   ↓
Human Approval (when required)
   ↓
Synthesize Deliverable
   ↓
Persist Memory / Outbox / Trace

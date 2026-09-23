"""Foreman orchestration API (Architecture.md §11).

Served through the app factory so importing this module has no side effects:

    uv run uvicorn apps.api.main:create_app --factory --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI

from apps.api.config.settings import Settings, get_settings
from apps.api.middleware.auth import ApiKeyMiddleware
from apps.api.middleware.errors import install_error_handlers
from apps.api.routes import approvals, memory, stats, tasks
from apps.api.services.approval_service import ApprovalService
from apps.api.services.memory_service import MemoryService
from apps.api.services.queue import CeleryQueue, TaskQueue
from apps.api.services.stats_service import StatsService
from apps.api.services.task_service import TaskService
from apps.api.services.trace_service import Fetcher, ReplayService, TraceService
from packages.orchestrator.memory.long_term import LongTermMemory
from packages.orchestrator.memory.persistent import ApprovalStore, OutboxRepository, TaskStore
from packages.orchestrator.runtime import make_approvals, make_long_term, make_outbox, make_store
from packages.orchestrator.tracing.otel import configure_tracing


def create_app(
    settings: Settings | None = None,
    *,
    store: TaskStore | None = None,
    approvals_store: ApprovalStore | None = None,
    outbox: OutboxRepository | None = None,
    queue: TaskQueue | None = None,
    long_term: LongTermMemory | None = None,
    memory_enabled: bool = True,
    trace_fetch: Fetcher | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_tracing(settings)
    queue = queue or CeleryQueue()
    app = FastAPI(title="Foreman", version="0.1.0")
    app.state.settings = settings
    task_store = store or make_store(settings)
    app.state.task_service = TaskService(task_store, queue)
    app.state.stats_service = StatsService(task_store, settings.evals_reports_dir)
    app.state.trace_service = TraceService(task_store, settings, fetch=trace_fetch)
    app.state.replay_service = ReplayService(task_store, queue)
    app.state.approval_service = ApprovalService(approvals_store or make_approvals(settings), queue)
    app.state.outbox = outbox or make_outbox(settings)
    app.state.memory_service = MemoryService(
        lambda: (
            long_term
            if long_term is not None
            else (make_long_term(settings) if memory_enabled else None)
        )
    )
    app.add_middleware(ApiKeyMiddleware, api_key=settings.api_key)
    install_error_handlers(app)
    app.include_router(tasks.router)
    app.include_router(approvals.router)
    app.include_router(memory.router)
    app.include_router(stats.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app

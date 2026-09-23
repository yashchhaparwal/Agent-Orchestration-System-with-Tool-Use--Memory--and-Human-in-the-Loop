"""Assemble the production GraphDeps from settings: LLM chains, registry, gate, stores, agents,
escalation policy, notifier."""

from __future__ import annotations

from collections.abc import Callable

import structlog

from packages.orchestrator.agents.catalog import build_specialists
from packages.orchestrator.agents.memory_extractor.agent import build_memory_extractor_agent
from packages.orchestrator.agents.reviewer.agent import build_reviewer_agent
from packages.orchestrator.agents.supervisor.agent import build_supervisor_agent, synthesize_prompt
from packages.orchestrator.gate.classifier import LLMRiskClassifier
from packages.orchestrator.gate.decide import Gate
from packages.orchestrator.graph.deps import GraphConfig, GraphDeps
from packages.orchestrator.hitl.escalation import EscalationPolicy
from packages.orchestrator.hitl.notify import notify_approval
from packages.orchestrator.llm.chains import ChainedLLM
from packages.orchestrator.llm.client import ChatLLM
from packages.orchestrator.llm.embeddings import EmbeddingChain
from packages.orchestrator.llm.providers import ProviderPool
from packages.orchestrator.llm.roles import load_models_config
from packages.orchestrator.loop.budgets import Budget, BudgetConfig
from packages.orchestrator.memory.db import make_engine, make_session_factory
from packages.orchestrator.memory.long_term import LongTermMemory
from packages.orchestrator.memory.persistent import ApprovalStore, OutboxRepository, TaskStore
from packages.orchestrator.memory.working import (
    InMemoryWorkingMemory,
    RedisWorkingMemory,
    WorkingMemory,
)
from packages.shared.config import Settings
from packages.shared.types.approval import ApprovalRequest
from packages.tools.registry.ratelimit import RateLimiter, RateLimiterLike, RedisRateLimiter
from packages.tools.registry.registry import ToolRegistry

log = structlog.get_logger(__name__)


def make_rate_limiter(settings: Settings) -> RateLimiterLike:
    """Redis-backed so every worker shares one budget; in-memory if Redis is unreachable."""
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        client.ping()
        return RedisRateLimiter(client)
    except Exception as e:  # noqa: BLE001 — degrade to a per-process limiter, loudly
        log.warning("ratelimit.redis_unavailable", error=str(e)[:120])
        return RateLimiter()


def _session_factory(settings: Settings):  # type: ignore[no-untyped-def]
    return make_session_factory(make_engine(settings.database_url))


def make_store(settings: Settings) -> TaskStore:
    return TaskStore(_session_factory(settings))


def make_approvals(settings: Settings) -> ApprovalStore:
    return ApprovalStore(_session_factory(settings))


def make_outbox(settings: Settings) -> OutboxRepository:
    return OutboxRepository(_session_factory(settings))


def make_budgets(settings: Settings) -> BudgetConfig | None:
    path = settings.budgets_config_path
    return BudgetConfig.load(path) if path.exists() else None


def make_policy(settings: Settings) -> EscalationPolicy:
    path = settings.escalation_config_path
    return EscalationPolicy.load(path) if path.exists() else EscalationPolicy.default()


def make_working(settings: Settings) -> WorkingMemory:
    """Redis tier 1 shared across workers; every call fails soft, so no ping up front."""
    try:
        from redis.asyncio import Redis

        return RedisWorkingMemory(
            Redis.from_url(settings.redis_url, socket_connect_timeout=2),
            ttl_hours=settings.tier1_ttl_hours,
        )
    except Exception as e:  # noqa: BLE001 — degrade to a per-process scratchpad, loudly
        log.warning("working_memory.redis_unavailable", error=str(e)[:120])
        return InMemoryWorkingMemory(ttl_hours=settings.tier1_ttl_hours)


def make_long_term(settings: Settings) -> LongTermMemory | None:
    """ChromaDB tier 3 bound to the primary embedding model; None when unreachable."""
    if not settings.memory_enabled:
        return None
    try:
        import chromadb

        config = load_models_config(
            settings.models_config_path, enable_paid=settings.enable_paid_providers
        )
        embedding = config.role("embedding")
        client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        client.heartbeat()
        return LongTermMemory(
            client,
            EmbeddingChain(embedding, ProviderPool(settings, config)),
            embedding.chain[0].model,
            collection=settings.memory_collection,
            dedup_threshold=settings.memory_dedup_threshold,
            half_life_days=settings.memory_half_life_days,
        )
    except Exception as e:  # noqa: BLE001 — memory is optional; the task runs without it
        log.warning("long_term_memory.unavailable", error=str(e)[:160])
        return None


def make_llm_factory(settings: Settings) -> Callable[[str], ChatLLM]:
    config = load_models_config(
        settings.models_config_path, enable_paid=settings.enable_paid_providers
    )
    pool = ProviderPool(settings, config)
    chains: dict[str, ChatLLM] = {}

    def llm_for(role: str) -> ChatLLM:
        if role not in chains:
            chains[role] = ChainedLLM(
                role, config.role(role), pool, prices=config.list_prices_usd_per_mtok
            )
        return chains[role]

    return llm_for


async def build_runtime(
    settings: Settings,
    *,
    store: TaskStore | None = None,
    approvals: ApprovalStore | None = None,
) -> GraphDeps:
    registry = await ToolRegistry.discover(settings)
    llm_for = make_llm_factory(settings)
    gate = Gate(
        registry,
        make_rate_limiter(settings),
        classifier=LLMRiskClassifier(llm_for("cheap")),
    )

    async def notifier(request: ApprovalRequest, approval_id: int) -> None:
        await notify_approval(settings, request, approval_id)

    return GraphDeps(
        llm_for=llm_for,
        registry=registry,
        gate=gate,
        store=store or make_store(settings),
        approvals=approvals or make_approvals(settings),
        specialists=build_specialists(),
        memory_extractor=build_memory_extractor_agent(),
        working=make_working(settings),
        long_term=make_long_term(settings),
        supervisor=build_supervisor_agent(),
        synthesize_prompt=synthesize_prompt(),
        reviewer=build_reviewer_agent(),
        policy=make_policy(settings),
        notifier=notifier,
        config=GraphConfig(
            plan_confidence_threshold=settings.plan_confidence_threshold,
            review_escalate_score=settings.review_escalate_score,
            memory_recall_k=settings.memory_recall_k,
            memory_recall_keep=settings.memory_recall_keep,
            memory_recall_max_tokens=settings.memory_recall_max_tokens,
            specialist_budget=Budget(
                max_iterations=settings.max_iterations,
                max_cost_usd=settings.default_task_budget_usd,
            ),
            budgets=make_budgets(settings),
        ),
    )

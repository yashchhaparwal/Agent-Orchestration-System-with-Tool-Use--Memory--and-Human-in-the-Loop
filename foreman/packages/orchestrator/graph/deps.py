"""Everything the graph's nodes need, injected once at build time so nodes stay pure functions
of (state, deps) and tests can swap any piece."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from packages.orchestrator.agents.base import AgentSpec
from packages.orchestrator.agents.memory_extractor.agent import build_memory_extractor_agent
from packages.orchestrator.gate.decide import Gate
from packages.orchestrator.hitl.escalation import EscalationPolicy
from packages.orchestrator.llm.client import ChatLLM
from packages.orchestrator.loop.budgets import Budget, BudgetConfig
from packages.orchestrator.memory.long_term import LongTermMemory
from packages.orchestrator.memory.persistent import ApprovalStore, TaskStore
from packages.orchestrator.memory.working import InMemoryWorkingMemory, WorkingMemory
from packages.shared.types.approval import ApprovalRequest
from packages.tools.registry.registry import ToolRegistry

Notifier = Callable[[ApprovalRequest, int], Awaitable[None]]


@dataclass
class GraphConfig:
    plan_confidence_threshold: float = 0.6
    review_escalate_score: int = 3
    max_retries: int = 2
    specialist_budget: Budget = field(default_factory=Budget)
    budgets: BudgetConfig | None = None  # per-agent overrides (config/budgets.yaml)
    llm_timeout_s: float = 120.0

    def budget_for(self, agent_name: str) -> Budget:
        if self.budgets is not None and agent_name in self.budgets.agents:
            return self.budgets.for_agent(agent_name)
        return self.specialist_budget

    memory_recall_k: int = 5
    memory_recall_keep: int = 3
    memory_recall_max_tokens: int = 600


@dataclass
class GraphDeps:
    llm_for: Callable[[str], ChatLLM]  # role -> chain
    registry: ToolRegistry
    gate: Gate
    store: TaskStore
    approvals: ApprovalStore
    specialists: dict[str, AgentSpec]
    supervisor: AgentSpec
    synthesize_prompt: str
    reviewer: AgentSpec
    policy: EscalationPolicy = field(default_factory=EscalationPolicy.default)
    notifier: Notifier | None = None
    config: GraphConfig = field(default_factory=GraphConfig)
    memory_extractor: AgentSpec = field(default_factory=build_memory_extractor_agent)
    working: WorkingMemory = field(default_factory=InMemoryWorkingMemory)
    long_term: LongTermMemory | None = None  # None: recall is empty, nothing is written

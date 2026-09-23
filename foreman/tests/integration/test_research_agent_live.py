"""Phase 1 integration: the research agent completes a real subtask against the seeded database and
the files server, through the live free-tier model chain. Needs the compose stack, the seed, and the
two MCP servers running. Excluded by default (`-m "not integration and not network"`).

    uv run pytest -m "integration and network" tests/integration -q
"""

from __future__ import annotations

import pytest

from packages.orchestrator.agents.research.agent import build_research_agent
from packages.orchestrator.gate.decide import Gate
from packages.orchestrator.llm.chains import ChainedLLM
from packages.orchestrator.llm.providers import ProviderPool
from packages.orchestrator.llm.roles import load_models_config
from packages.orchestrator.loop.agent_loop import LoopDeps, run_agent_loop
from packages.orchestrator.loop.budgets import Budget
from packages.shared.config import get_settings
from packages.shared.types.subtask import Specialist, Subtask, SubtaskStatus
from packages.tools.registry.registry import ToolRegistry

pytestmark = [pytest.mark.integration, pytest.mark.network]


async def test_research_agent_reads_db_and_document() -> None:
    settings = get_settings()
    config = load_models_config(
        settings.models_config_path, enable_paid=settings.enable_paid_providers
    )
    agent = build_research_agent()
    llm = ChainedLLM(agent.role, config.role(agent.role), ProviderPool(settings, config))
    registry = await ToolRegistry.discover(settings)
    assert "db_query" in registry.names() and "files_read_file" in registry.names()

    subtask = Subtask(
        id="it-1",
        specialist=Specialist.RESEARCH,
        description=(
            "For claim CLM-4471: list every loan (principal, APR, start date, missed payments, rollovers) "
            "from the database, then read the lender response document for that claim and state the "
            "lender's decision and which affordability checks they say they performed."
        ),
        expected_output="A table of loans and a short summary of the lender response, with sources.",
    )
    deps = LoopDeps(
        llm=llm, registry=registry, gate=Gate(registry), budget=Budget(max_iterations=10)
    )
    result = await run_agent_loop(agent, subtask, deps)

    assert result.status in (SubtaskStatus.COMPLETED, SubtaskStatus.PARTIAL), result.error
    assert "db_query" in result.tools_used and "files_read_file" in result.tools_used
    assert "4471" in result.output or "CLM-4471" in " ".join(result.sources)

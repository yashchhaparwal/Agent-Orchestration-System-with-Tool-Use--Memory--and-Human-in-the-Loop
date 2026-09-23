"""Every agent the graph can run, keyed by the name used in policy.yaml and the plan."""

from __future__ import annotations

from packages.orchestrator.agents.analysis.agent import build_analysis_agent
from packages.orchestrator.agents.base import AgentSpec
from packages.orchestrator.agents.code_exec.agent import build_code_exec_agent
from packages.orchestrator.agents.research.agent import build_research_agent
from packages.orchestrator.agents.writing.agent import build_writing_agent


def build_specialists() -> dict[str, AgentSpec]:
    agents = [
        build_research_agent(),
        build_analysis_agent(),
        build_writing_agent(),
        build_code_exec_agent(),
    ]
    return {a.name: a for a in agents}

from __future__ import annotations

from pathlib import Path

from packages.orchestrator.agents.base import AgentSpec, load_prompt

NAME = "supervisor"
ROLE = "supervisor"
_HERE = Path(__file__).parent


def build_supervisor_agent() -> AgentSpec:
    return AgentSpec(name=NAME, role=ROLE, system_prompt=load_prompt(_HERE / "plan_prompt.md"))


def synthesize_prompt() -> str:
    return load_prompt(_HERE / "synthesize_prompt.md")

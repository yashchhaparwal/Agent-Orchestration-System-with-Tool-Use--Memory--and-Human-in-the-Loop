from __future__ import annotations

from pathlib import Path

from packages.orchestrator.agents.base import AgentSpec, load_prompt

NAME = "memory_extractor"
ROLE = "cheap"


def build_memory_extractor_agent() -> AgentSpec:
    return AgentSpec(
        name=NAME, role=ROLE, system_prompt=load_prompt(Path(__file__).parent / "prompt.md")
    )

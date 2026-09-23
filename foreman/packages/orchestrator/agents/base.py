from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentSpec:
    name: str  # matches the `agents:` lists in policy.yaml
    role: str  # a role in config/models.yaml
    system_prompt: str


def load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()

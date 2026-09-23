"""Load and validate config/models.yaml (Architecture.md §10, Rules.md §6)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ChainEntry(BaseModel):
    provider: str
    model: str


class RoleConfig(BaseModel):
    chain: list[ChainEntry] = Field(min_length=1)
    effort: str | None = None


class ProviderConfig(BaseModel):
    base_url_env: str | None = None
    api_key_env: str | None = None
    sdk: str | None = None
    free_tier: bool = False
    paid: bool = False
    verified: bool = True
    supports_effort: bool = False
    limits: dict[str, int] = Field(default_factory=dict)
    notes: str = ""


class ModelsConfig(BaseModel):
    providers: dict[str, ProviderConfig]
    roles: dict[str, RoleConfig]
    dev: dict[str, Any] = Field(default_factory=dict)
    paid_optional: dict[str, Any] = Field(default_factory=dict)
    list_prices_usd_per_mtok: dict[str, dict[str, float]] = Field(default_factory=dict)

    def role(self, name: str) -> RoleConfig:
        if name not in self.roles:
            raise KeyError(f"No role '{name}' in models config (have: {sorted(self.roles)})")
        return self.roles[name]


def load_models_config(path: Path, *, enable_paid: bool = False) -> ModelsConfig:
    """Parse the YAML and enforce: every chain provider exists; paid entries are dropped unless enabled.

    A chain may list paid providers ahead of the free ones; with ENABLE_PAID_PROVIDERS=false those
    entries are removed, so the effective default is still $0 (Rules.md §6). A role whose chain
    would become empty is an error, never a silently missing role.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg = ModelsConfig.model_validate(data)
    for role_name, role in cfg.roles.items():
        for entry in role.chain:
            if entry.provider not in cfg.providers:
                raise ValueError(
                    f"Role '{role_name}' references unknown provider '{entry.provider}'"
                )
        if not enable_paid:
            kept = [e for e in role.chain if not cfg.providers[e.provider].paid]
            if not kept:
                raise ValueError(
                    f"Role '{role_name}' has only paid providers in its chain "
                    "but ENABLE_PAID_PROVIDERS is false (Rules.md §6)"
                )
            role.chain = kept
    return cfg

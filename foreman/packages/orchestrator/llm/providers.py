"""Builds one client per provider id, lazily, from settings + models config."""

from __future__ import annotations

from packages.orchestrator.llm.client import LLMClient
from packages.orchestrator.llm.openai_compat import OpenAICompatProvider
from packages.orchestrator.llm.roles import ModelsConfig
from packages.shared.config import Settings
from packages.shared.errors import NonRetryableError


class ProviderPool:
    def __init__(self, settings: Settings, config: ModelsConfig) -> None:
        self._settings = settings
        self._config = config
        self._clients: dict[str, LLMClient] = {}

    def get(self, provider_id: str) -> LLMClient:
        if provider_id in self._clients:
            return self._clients[provider_id]
        pc = self._config.providers.get(provider_id)
        if pc is None:
            raise NonRetryableError(f"Unknown provider '{provider_id}'")
        if pc.paid and not self._settings.enable_paid_providers:
            raise NonRetryableError(
                f"Provider '{provider_id}' is paid and ENABLE_PAID_PROVIDERS is false"
            )
        if pc.sdk == "anthropic":
            raise NonRetryableError("Native Anthropic provider is optional and not implemented")
        if not pc.base_url_env:
            raise NonRetryableError(f"Provider '{provider_id}' has no base_url_env")
        base_url = self._settings.env_value(pc.base_url_env)
        if not base_url:
            raise NonRetryableError(f"{pc.base_url_env} is not set for provider '{provider_id}'")
        api_key = self._settings.env_value(pc.api_key_env) if pc.api_key_env else ""
        if pc.api_key_env and not api_key:
            raise NonRetryableError(f"{pc.api_key_env} is not set for provider '{provider_id}'")
        client = OpenAICompatProvider(
            provider_id,
            base_url,
            api_key,
            supports_effort=pc.supports_effort,
            max_concurrency=int(pc.limits.get("concurrency", 2)),
        )
        self._clients[provider_id] = client
        return client

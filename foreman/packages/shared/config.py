"""Typed settings. This is the ONLY module that reads environment variables (Rules.md §6).

Everything else receives a `Settings` instance. Provider base URLs and keys are looked up by the
env-variable *name* stored in config/models.yaml via `Settings.env_value`, so adding a provider is a
YAML change plus two fields here — never an `os.environ` read elsewhere.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from packages.shared.errors import NonRetryableError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM: free providers ---
    mistral_api_key: str = ""
    mistral_base_url: str = "https://api.mistral.ai/v1"
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    ollama_base_url: str = "http://localhost:11434/v1"
    tokenrouter_free_api_key: str = ""
    tokenrouter_base_url: str = ""

    # --- LLM: optional paid providers ---
    enable_paid_providers: bool = False
    tokenrouter_api_key: str = ""
    anthropic_api_key: str = ""
    explabs_api_key: str = ""
    explabs_base_url: str = "https://api.experientiallabs.ai/v1"

    # --- stores ---
    database_url: str = "postgresql+psycopg://foreman:foreman@localhost:5432/foreman"
    database_url_readonly: str = "postgresql+psycopg://foreman_ro:foreman_ro@localhost:5432/foreman"
    redis_url: str = "redis://localhost:6379/0"
    chroma_host: str = "localhost"
    chroma_port: int = 8001

    # --- tracing ---
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "foreman"

    # --- tools ---
    workspace_root: Path = Path("./data/workspace")
    sandbox_image: str = "foreman-sandbox:latest"
    mcp_web_url: str = "http://localhost:7001/mcp"
    mcp_files_url: str = "http://localhost:7002/mcp"
    mcp_sandbox_url: str = "http://localhost:7003/mcp"
    mcp_db_url: str = "http://localhost:7004/mcp"
    mcp_actions_url: str = "http://localhost:7005/mcp"
    # For an MCP server process: which interface/port to bind (set per process by the Makefile).
    mcp_host: str = "127.0.0.1"
    mcp_port: int | None = None

    # --- tool servers: behaviour ---
    web_search_backend: str = "fixture"  # fixture | ddg
    web_fetch_allowlist: str = ""  # comma-separated hostnames; empty = any public host
    web_fetch_max_bytes: int = 2_000_000
    sandbox_max_timeout_s: int = 60
    sandbox_memory: str = "512m"
    sandbox_cpus: float = 1.0

    # --- policy ---
    plan_confidence_threshold: float = 0.6
    review_escalate_score: int = 3
    max_iterations: int = 15
    default_task_budget_usd: float = 1.00
    tier1_ttl_hours: int = 24
    # --- tier 3 (Architecture.md 7.3) ---
    memory_enabled: bool = True
    memory_collection: str = "memories"
    memory_dedup_threshold: float = 0.92
    memory_recall_k: int = 5
    memory_recall_keep: int = 3
    memory_recall_max_tokens: int = 600
    memory_half_life_days: float = 30.0
    memory_max_age_days: int = 180
    # --- observability + evals (Architecture.md 9, Phases.md 6) ---
    jaeger_query_url: str = "http://localhost:16686"
    evals_reports_dir: Path = Path("packages/evals/reports")
    budgets_config_path: Path = Path("config/budgets.yaml")

    # --- ops ---
    api_base_url: str = "http://localhost:8000"  # what the operator UI talks to
    api_key: str = ""
    slack_webhook_url: str = ""
    log_level: str = "INFO"

    # --- config file locations (relative to the repo root / cwd) ---
    models_config_path: Path = Path("config/models.yaml")
    escalation_config_path: Path = Path("config/escalation.yaml")
    tool_policy_path: Path = Path("packages/tools/registry/policy.yaml")

    def env_value(self, env_name: str) -> str:
        """Resolve a value by its environment-variable name, e.g. ``MISTRAL_BASE_URL``.

        Used by the provider pool so config/models.yaml can refer to env names without any module
        touching ``os.environ`` directly.
        """
        attr = env_name.lower()
        if not hasattr(self, attr):
            raise NonRetryableError(f"Unknown setting referenced by config: {env_name}")
        value = getattr(self, attr)
        return "" if value is None else str(value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

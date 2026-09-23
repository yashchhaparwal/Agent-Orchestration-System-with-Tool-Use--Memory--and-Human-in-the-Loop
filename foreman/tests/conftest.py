from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from packages.orchestrator.tracing.otel import configure_tracing
from packages.shared.asyncio_compat import use_selector_event_loop_on_windows
from packages.shared.config import Settings

use_selector_event_loop_on_windows()  # the Postgres-checkpointer integration test needs it


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # No .env: tests must never depend on the developer's keys.
    return Settings(_env_file=None, workspace_root=tmp_path / "workspace")  # type: ignore[call-arg]


@pytest.fixture
def spans(settings: Settings) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    configure_tracing(settings, exporter=exporter)
    return exporter

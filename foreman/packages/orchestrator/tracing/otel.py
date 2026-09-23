"""OpenTelemetry setup and the span helpers every component uses (Architecture.md §9).

One global ``TracerProvider`` is installed on first use; later calls attach additional exporters to
it (tests attach an in-memory exporter). Without an OTLP endpoint configured, spans are still
created — so structure and attributes are exercised — but never exported.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import Span

from packages.shared.config import Settings

_TRACER_NAME = "foreman"
_provider: TracerProvider | None = None


def configure_tracing(
    settings: Settings, *, exporter: SpanExporter | None = None
) -> TracerProvider:
    """Install the provider once; attach ``exporter`` (or the OTLP exporter from settings) to it.

    An explicitly passed exporter is wired through a SimpleSpanProcessor so spans are visible
    synchronously (what tests need); the OTLP exporter uses batching.
    """
    global _provider
    if _provider is None:
        _provider = TracerProvider(
            resource=Resource.create({SERVICE_NAME: settings.otel_service_name})
        )
        trace.set_tracer_provider(_provider)
    if exporter is not None:
        _provider.add_span_processor(SimpleSpanProcessor(exporter))
    elif settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        otlp = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
        _provider.add_span_processor(BatchSpanProcessor(otlp))
    return _provider


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(_TRACER_NAME)


def _set_attributes(span: Span, attrs: dict[str, Any]) -> None:
    for key, value in attrs.items():
        if value is None:
            continue
        if isinstance(value, str | bool | int | float):
            span.set_attribute(key, value)
        else:
            span.set_attribute(key, str(value))


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Span]:
    with get_tracer().start_as_current_span(name) as s:
        _set_attributes(s, attrs)
        yield s


@contextmanager
def llm_span(*, role: str, provider: str, model: str, fallback: bool) -> Iterator[Span]:
    with span("llm.call", role=role, provider=provider, model=model, fallback=fallback) as s:
        yield s


@contextmanager
def tool_span(*, server: str, tool: str, agent: str) -> Iterator[Span]:
    with span(f"tool.{server}.{tool}", server=server, tool=tool, agent=agent) as s:
        yield s


@contextmanager
def gate_span(*, tool: str, agent: str) -> Iterator[Span]:
    with span("gate.decide", tool=tool, agent=agent) as s:
        yield s

"""Search backends behind one interface. The fixture backend is the default: deterministic, offline,
and what the evals use. A live backend is a config switch away."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    title: str
    url: str
    snippet: str = ""


class SearchBackend(Protocol):
    name: str

    def search(self, query: str, *, max_results: int) -> list[SearchHit]: ...


class FixtureBackend:
    """Canned results keyed by keyword; falls back to the ``*`` entry. Synthetic content only."""

    name = "fixture"

    def __init__(self, path: Path | None = None) -> None:
        source = path or Path(__file__).parent / "fixtures.json"
        data: dict[str, list[dict[str, Any]]] = json.loads(source.read_text(encoding="utf-8"))
        self._entries = {
            k.lower(): [SearchHit.model_validate(h) for h in v] for k, v in data.items()
        }

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        q = query.lower()
        hits: list[SearchHit] = []
        for keyword, entries in self._entries.items():
            if keyword != "*" and keyword in q:
                hits.extend(entries)
        if not hits:
            hits = list(self._entries.get("*", []))
        unique: list[SearchHit] = []
        seen: set[str] = set()
        for hit in hits:
            if hit.url not in seen:
                seen.add(hit.url)
                unique.append(hit)
        return unique[: max(1, max_results)]


class DDGBackend:
    """DuckDuckGo via the optional ``ddgs`` package (``uv sync --extra web``). Keyless, best effort."""

    name = "ddg"

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        from ddgs import DDGS  # optional dependency

        with DDGS() as ddgs:
            rows = ddgs.text(query, max_results=max(1, max_results)) or []
        return [
            SearchHit(title=r.get("title", ""), url=r.get("href", ""), snippet=r.get("body", ""))
            for r in rows
            if r.get("href")
        ]


class Options(BaseModel):
    backend: str = Field(default="fixture", pattern="^(fixture|ddg)$")


def make_backend(name: str) -> SearchBackend:
    chosen = Options(backend=name or "fixture").backend
    return DDGBackend() if chosen == "ddg" else FixtureBackend()

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from packages.orchestrator.memory.long_term import LongTermMemory


class MemoryUnavailableError(RuntimeError):
    """ChromaDB or the embedding provider is not reachable."""


class MemoryService:
    """Built lazily so the API starts (and unit tests run) without a vector store."""

    def __init__(self, provider: Callable[[], LongTermMemory | None]) -> None:
        self._provider = provider
        self._memory: LongTermMemory | None = None
        self._resolved = False

    def _store(self) -> LongTermMemory:
        if not self._resolved:
            self._memory = self._provider()
            self._resolved = True
        if self._memory is None:
            raise MemoryUnavailableError("long-term memory is not available")
        return self._memory

    def list_user(self, user_id: str) -> list[dict[str, Any]]:
        return [m.model_dump(mode="json") for m in self._store().list_user(user_id)]

    def delete(self, user_id: str) -> int:
        return self._store().delete_user(user_id)

    def users(self) -> list[dict[str, Any]]:
        return self._store().users()

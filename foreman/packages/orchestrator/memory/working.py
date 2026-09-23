"""Tier 1 — working memory (Architecture.md §7.1): the shared scratchpad for one task.

Keys ``task:{id}:plan``, ``task:{id}:result:{subtask_id}``, ``task:{id}:artifact:{name}``,
``task:{id}:errors``, all with ``TIER1_TTL_HOURS``. Specialists read predecessors' results from
here first. It is a cache, not the system of record: every method fails soft (log + default) and
the graph state / Postgres stay authoritative, so an expired or unreachable Redis never breaks a
resume.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

import structlog

from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.subtask import SubtaskResult

log = structlog.get_logger(__name__)


def _key(task_id: str, *parts: str) -> str:
    return ":".join(["task", task_id, *parts])


class WorkingMemory(Protocol):
    async def put_plan(self, task_id: str, plan: ExecutionPlan) -> None: ...
    async def get_plan(self, task_id: str) -> ExecutionPlan | None: ...
    async def put_result(self, task_id: str, result: SubtaskResult) -> None: ...
    async def get_results(self, task_id: str) -> dict[str, SubtaskResult]: ...
    async def put_artifact(self, task_id: str, name: str, content: str) -> None: ...
    async def get_artifact(self, task_id: str, name: str) -> str | None: ...
    async def add_error(self, task_id: str, message: str) -> None: ...
    async def get_errors(self, task_id: str) -> list[str]: ...
    async def clear(self, task_id: str) -> None: ...


class InMemoryWorkingMemory:
    """Single-process implementation with the same TTL semantics (tests, CLI runs)."""

    def __init__(self, *, ttl_hours: float = 24.0, clock: Callable[[], float] = time.time) -> None:
        self._ttl = ttl_hours * 3600
        self._clock = clock
        self._data: dict[str, tuple[float, Any]] = {}

    def _set(self, key: str, value: Any) -> None:
        self._data[key] = (self._clock() + self._ttl, value)

    def _get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if self._clock() >= expires:
            del self._data[key]
            return None
        return value

    async def put_plan(self, task_id: str, plan: ExecutionPlan) -> None:
        self._set(_key(task_id, "plan"), plan.model_dump_json())

    async def get_plan(self, task_id: str) -> ExecutionPlan | None:
        raw = self._get(_key(task_id, "plan"))
        return ExecutionPlan.model_validate_json(raw) if raw else None

    async def put_result(self, task_id: str, result: SubtaskResult) -> None:
        self._set(_key(task_id, "result", result.subtask_id), result.model_dump_json())

    async def get_results(self, task_id: str) -> dict[str, SubtaskResult]:
        prefix = _key(task_id, "result", "")
        out: dict[str, SubtaskResult] = {}
        for key in list(self._data):
            if key.startswith(prefix):
                raw = self._get(key)
                if raw:
                    out[key[len(prefix) :]] = SubtaskResult.model_validate_json(raw)
        return out

    async def put_artifact(self, task_id: str, name: str, content: str) -> None:
        self._set(_key(task_id, "artifact", name), content)

    async def get_artifact(self, task_id: str, name: str) -> str | None:
        value = self._get(_key(task_id, "artifact", name))
        return str(value) if value is not None else None

    async def add_error(self, task_id: str, message: str) -> None:
        errors = list(self._get(_key(task_id, "errors")) or [])
        errors.append(message)
        self._set(_key(task_id, "errors"), errors)

    async def get_errors(self, task_id: str) -> list[str]:
        return list(self._get(_key(task_id, "errors")) or [])

    async def clear(self, task_id: str) -> None:
        prefix = _key(task_id, "")
        for key in [k for k in self._data if k.startswith(prefix)]:
            del self._data[key]


class RedisWorkingMemory:
    """Shared across workers. Every call is wrapped: Redis trouble is logged, never raised."""

    def __init__(self, client: Any, *, ttl_hours: float = 24.0) -> None:
        self._r = client  # redis.asyncio.Redis
        self._ttl = int(ttl_hours * 3600)

    async def _guard(self, op: str, coro: Any, default: Any) -> Any:
        try:
            return await coro
        except Exception as e:  # noqa: BLE001 — tier 1 is a cache; degrade, do not fail the task
            log.warning("working_memory.unavailable", op=op, error=str(e)[:160])
            return default

    async def put_plan(self, task_id: str, plan: ExecutionPlan) -> None:
        await self._guard(
            "put_plan",
            self._r.set(_key(task_id, "plan"), plan.model_dump_json(), ex=self._ttl),
            None,
        )

    async def get_plan(self, task_id: str) -> ExecutionPlan | None:
        raw = await self._guard("get_plan", self._r.get(_key(task_id, "plan")), None)
        return ExecutionPlan.model_validate_json(raw) if raw else None

    async def put_result(self, task_id: str, result: SubtaskResult) -> None:
        await self._guard(
            "put_result",
            self._r.set(
                _key(task_id, "result", result.subtask_id), result.model_dump_json(), ex=self._ttl
            ),
            None,
        )

    async def get_results(self, task_id: str) -> dict[str, SubtaskResult]:
        prefix = _key(task_id, "result", "")

        async def scan() -> dict[str, SubtaskResult]:
            out: dict[str, SubtaskResult] = {}
            async for key in self._r.scan_iter(match=prefix + "*"):
                raw = await self._r.get(key)
                name = key.decode() if isinstance(key, bytes) else str(key)
                if raw:
                    out[name[len(prefix) :]] = SubtaskResult.model_validate_json(raw)
            return out

        result: dict[str, SubtaskResult] = await self._guard("get_results", scan(), {})
        return result

    async def put_artifact(self, task_id: str, name: str, content: str) -> None:
        await self._guard(
            "put_artifact",
            self._r.set(_key(task_id, "artifact", name), content, ex=self._ttl),
            None,
        )

    async def get_artifact(self, task_id: str, name: str) -> str | None:
        raw = await self._guard("get_artifact", self._r.get(_key(task_id, "artifact", name)), None)
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    async def add_error(self, task_id: str, message: str) -> None:
        key = _key(task_id, "errors")

        async def push() -> None:
            await self._r.rpush(key, message)
            await self._r.expire(key, self._ttl)

        await self._guard("add_error", push(), None)

    async def get_errors(self, task_id: str) -> list[str]:
        raw = await self._guard("get_errors", self._r.lrange(_key(task_id, "errors"), 0, -1), [])
        return [x.decode() if isinstance(x, bytes) else str(x) for x in raw]

    async def clear(self, task_id: str) -> None:
        async def wipe() -> None:
            keys = [k async for k in self._r.scan_iter(match=_key(task_id, "") + "*")]
            if keys:
                await self._r.delete(*keys)

        await self._guard("clear", wipe(), None)

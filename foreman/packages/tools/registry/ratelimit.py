"""Per-tool rate limiting for the gate (Architecture.md §6.3).

Two implementations behind one interface: an in-memory sliding window for tests and single-process
runs, and a Redis fixed-window counter so several workers share one budget. Both fail closed on
their own errors — a limiter that cannot answer says "no".
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)


class RateLimiterLike(Protocol):
    def allow(self, key: str, per_window: int, *, now: float | None = None) -> bool: ...


class RateLimiter:
    """In-memory sliding window."""

    def __init__(self, *, window_s: float = 60.0) -> None:
        self._window = window_s
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, per_window: int, *, now: float | None = None) -> bool:
        t = time.monotonic() if now is None else now
        q = self._events[key]
        while q and t - q[0] >= self._window:
            q.popleft()
        if len(q) >= per_window:
            return False
        q.append(t)
        return True


class RedisRateLimiter:
    """Fixed window in Redis: ``INCR key:<window>`` + ``EXPIRE``. Shared across worker processes.

    ``client`` is any object with ``pipeline()`` returning something that supports ``incr``,
    ``expire`` and ``execute`` — the real ``redis.Redis`` or a test double.
    """

    def __init__(self, client: Any, *, window_s: int = 60, prefix: str = "foreman:rl:") -> None:
        self._client = client
        self._window = window_s
        self._prefix = prefix

    def allow(self, key: str, per_window: int, *, now: float | None = None) -> bool:
        t = time.time() if now is None else now
        bucket = int(t // self._window)
        redis_key = f"{self._prefix}{key}:{bucket}"
        try:
            pipe = self._client.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, self._window + 1)
            count, _ = pipe.execute()
        except Exception as e:  # noqa: BLE001 — fail closed
            log.error("ratelimit.redis_error", key=key, error=str(e)[:200])
            return False
        return int(count) <= per_window

from __future__ import annotations

from typing import Any

from packages.tools.registry.ratelimit import RateLimiter, RedisRateLimiter


def test_memory_limiter_sliding_window() -> None:
    rl = RateLimiter(window_s=60)
    assert all(rl.allow("k", 3, now=t) for t in (0.0, 1.0, 2.0))
    assert rl.allow("k", 3, now=3.0) is False
    assert rl.allow("k", 3, now=61.0) is True  # the first event expired
    assert rl.allow("other", 1, now=61.0) is True  # keys are independent


class FakePipeline:
    def __init__(self, store: dict[str, int], fail: bool = False) -> None:
        self.store, self.fail, self.ops = store, fail, []

    def incr(self, key: str) -> None:
        self.ops.append(("incr", key))

    def expire(self, key: str, ttl: int) -> None:
        self.ops.append(("expire", key, ttl))

    def execute(self) -> list[Any]:
        if self.fail:
            raise ConnectionError("redis down")
        key = self.ops[0][1]
        self.store[key] = self.store.get(key, 0) + 1
        return [self.store[key], True]


class FakeRedis:
    def __init__(self, fail: bool = False) -> None:
        self.store: dict[str, int] = {}
        self.fail = fail

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self.store, self.fail)


def test_redis_limiter_fixed_window_and_key_shape() -> None:
    fake = FakeRedis()
    rl = RedisRateLimiter(fake, window_s=60)
    assert rl.allow("research:db_query", 2, now=100.0)
    assert rl.allow("research:db_query", 2, now=101.0)
    assert rl.allow("research:db_query", 2, now=102.0) is False
    assert rl.allow("research:db_query", 2, now=120.0) is True  # next 60 s bucket
    assert set(fake.store) == {"foreman:rl:research:db_query:1", "foreman:rl:research:db_query:2"}


def test_redis_limiter_fails_closed_on_errors() -> None:
    assert RedisRateLimiter(FakeRedis(fail=True)).allow("k", 100) is False

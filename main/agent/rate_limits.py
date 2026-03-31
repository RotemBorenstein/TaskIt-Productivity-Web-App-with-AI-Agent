"""Redis-backed assistant rate limits and provider cooldown helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

import redis
from django.conf import settings
from redis.exceptions import RedisError


@dataclass
class AssistantRateLimitDecision:
    """Normalized outcome for one assistant request rate-limit check."""

    allowed: bool
    limit_scope: str | None = None
    retry_after_seconds: int = 0
    service_unavailable: bool = False


@dataclass(frozen=True)
class _RateWindow:
    scope: str
    limit: int
    seconds: int
    bucket: int


_REDIS_CLIENT: redis.Redis | None = None
_ASSISTANT_RATE_PREFIX = "assistant:rate_limit"
_ASSISTANT_PROVIDER_PREFIX = "assistant:provider"
_ASSISTANT_SIGNAL_PREFIX = "assistant:signal"


def get_redis_client() -> redis.Redis:
    """Return a shared Redis client for assistant rate limiting."""

    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        _REDIS_CLIENT = redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _REDIS_CLIENT


def _window_key(scope: str, bucket: int, *, user_id: int | None = None) -> str:
    if user_id is None:
        return f"{_ASSISTANT_RATE_PREFIX}:{scope}:{bucket}"
    return f"{_ASSISTANT_RATE_PREFIX}:user:{user_id}:{scope}:{bucket}"


def _increment_window(client: redis.Redis, key: str, seconds: int) -> tuple[int, int]:
    count = int(client.incr(key))
    if count == 1:
        client.expire(key, seconds)
    ttl = int(client.ttl(key))
    if ttl < 1:
        ttl = seconds
    return count, ttl


def _iter_rate_windows(user_id: int, now: int) -> Iterable[_RateWindow]:
    yield _RateWindow(
        scope="user_minute",
        limit=settings.ASSISTANT_RATE_LIMIT_PER_MINUTE,
        seconds=60,
        bucket=now // 60,
    )
    yield _RateWindow(
        scope="user_hour",
        limit=settings.ASSISTANT_RATE_LIMIT_PER_HOUR,
        seconds=3600,
        bucket=now // 3600,
    )
    yield _RateWindow(
        scope="user_day",
        limit=settings.ASSISTANT_RATE_LIMIT_PER_DAY,
        seconds=86400,
        bucket=now // 86400,
    )


def check_assistant_rate_limit(user_id: int) -> AssistantRateLimitDecision:
    """
    Count one assistant request attempt and decide whether it should proceed.

    Every non-empty request increments the configured windows so repeated blocked
    or abusive requests still consume the user's allowance.
    """

    try:
        client = get_redis_client()
        now = int(time.time())
        exceeded_scope = None
        exceeded_ttl = 0

        for window in _iter_rate_windows(user_id, now):
            key = _window_key(window.scope, window.bucket, user_id=user_id)
            count, ttl = _increment_window(client, key, window.seconds)
            if count > window.limit and exceeded_scope is None:
                exceeded_scope = window.scope
                exceeded_ttl = ttl

        if exceeded_scope is not None:
            return AssistantRateLimitDecision(
                allowed=False,
                limit_scope=exceeded_scope,
                retry_after_seconds=exceeded_ttl,
            )

        global_key = _window_key("global_day", now // 86400)
        global_count, global_ttl = _increment_window(client, global_key, 86400)
        if global_count <= settings.ASSISTANT_GLOBAL_DAILY_LIMIT:
            return AssistantRateLimitDecision(allowed=True)

        return AssistantRateLimitDecision(
            allowed=False,
            limit_scope="global_day",
            retry_after_seconds=global_ttl,
        )
    except RedisError:
        return AssistantRateLimitDecision(allowed=True, service_unavailable=True)


def get_provider_cooldown_seconds(provider: str) -> int:
    """Return remaining cooldown seconds for a provider, or zero if inactive."""

    try:
        ttl = int(get_redis_client().ttl(f"{_ASSISTANT_PROVIDER_PREFIX}:{provider}:cooldown"))
    except RedisError:
        return 0
    return ttl if ttl > 0 else 0


def set_provider_cooldown(provider: str, seconds: int) -> None:
    """Store a temporary provider cooldown after quota exhaustion."""

    if seconds <= 0:
        return
    try:
        get_redis_client().setex(
            f"{_ASSISTANT_PROVIDER_PREFIX}:{provider}:cooldown",
            seconds,
            "1",
        )
    except RedisError:
        return


def record_assistant_signal(name: str) -> None:
    """Increment a lightweight daily Redis counter for assistant observability."""

    try:
        client = get_redis_client()
        bucket = int(time.time()) // 86400
        key = f"{_ASSISTANT_SIGNAL_PREFIX}:{name}:{bucket}"
        count = int(client.incr(key))
        if count == 1:
            client.expire(key, 86400)
    except RedisError:
        return

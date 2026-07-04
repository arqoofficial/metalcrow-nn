"""Redis-based rate limiting (fixed window), используется ingest upload (§8.5: 10/мин)."""

import redis
from fastapi import HTTPException, status

from app.core.config import settings

_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(settings.REDIS_URL)
    return _client


def check_rate_limit(key: str, *, limit: int, window_seconds: int) -> None:
    """Fail-open при недоступном Redis — контрактные тесты/офлайн-разработка не должны
    зависеть от поднятой инфры, только продовый rate limit (TODO: тест на реальном Redis)."""
    try:
        client = _get_client()
        current = client.incr(key)
        if current == 1:
            client.expire(key, window_seconds)
    except HTTPException:
        raise
    except Exception:
        return
    if current > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
        )

"""In-memory Redis stub for rate-limit tests."""

from collections import defaultdict


class FakeRedisClient:
    def __init__(self) -> None:
        self._counts: dict[str, int] = defaultdict(int)

    def incr(self, key: str) -> int:
        self._counts[key] += 1
        return self._counts[key]

    def expire(self, key: str, seconds: int) -> bool:  # noqa: ARG002
        return True

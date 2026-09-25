import os

# services/token.py reads JWT_SECRET at import time, so this must be set
# before any test module imports it.
os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest-only-not-a-real-key")

import pytest


@pytest.fixture(autouse=True)
def _code_switch_is_off_unless_a_test_turns_it_on(monkeypatch):
    # VERIFY_CODE_ENABLED comes from the developer's .env; a test must not depend on it
    monkeypatch.delenv("VERIFY_CODE_ENABLED", raising=False)


class FakeRedis:
    """Minimal in-memory stand-in for redis.asyncio.Redis, covering only the
    methods the code under test actually calls. Not a general-purpose fake --
    TTLs (setex/expire) are accepted but not enforced, since the logic being
    tested here doesn't depend on real expiry timing."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._lists: dict[str, list[str]] = {}
        self._sets: dict[str, set[str]] = {}

    async def get(self, key):
        val = self._store.get(key)
        return val.encode() if val is not None else None

    async def set(self, key, value):
        self._store[key] = str(value)

    async def setex(self, key, ttl, value):
        self._store[key] = str(value)

    async def delete(self, key):
        self._store.pop(key, None)
        self._lists.pop(key, None)
        self._sets.pop(key, None)

    async def exists(self, key):
        return 1 if key in self._store or key in self._lists or key in self._sets else 0

    async def sadd(self, key, *values):
        self._sets.setdefault(key, set()).update(str(v) for v in values)

    async def smembers(self, key):
        return {v.encode() for v in self._sets.get(key, set())}

    async def incr(self, key):
        current = int(self._store.get(key, 0)) + 1
        self._store[key] = str(current)
        return current

    async def expire(self, key, ttl):
        pass

    async def lpush(self, key, value):
        self._lists.setdefault(key, []).insert(0, str(value))

    async def lrange(self, key, start, end):
        lst = self._lists.get(key, [])
        if end == -1:
            end = len(lst) - 1
        return [v.encode() for v in lst[start:end + 1]]

    async def ltrim(self, key, start, end):
        lst = self._lists.get(key, [])
        if end == -1:
            end = len(lst) - 1
        self._lists[key] = lst[start:end + 1]


@pytest.fixture
def fake_redis():
    return FakeRedis()

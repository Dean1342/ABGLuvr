# A small in-memory cache for fetched links: results expire after a TTL (posts get edited
# and deleted, pages change), failures after a shorter one, and concurrent requests for the
# same key share one fetch.
import asyncio
import time

MAX_ENTRIES = 500


class TTLCache:
    def __init__(self, ttl, failure_ttl):
        self.ttl, self.failure_ttl = ttl, failure_ttl
        self._items: dict = {}     # key -> (expires_at, ok, value)
        self._inflight: dict = {}  # key -> asyncio.Task

    async def get(self, key, fetch):
        # fetch() returns a value or raises; both outcomes are cached (the exception is re-raised).
        hit = self._items.get(key)
        if hit and hit[0] > time.monotonic():
            if hit[1]:
                return hit[2]
            raise hit[2]
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._fetch(key, fetch))
            self._inflight[key] = task
        return await asyncio.shield(task)

    async def _fetch(self, key, fetch):
        try:
            value = await fetch()
            self._store(key, True, value, self.ttl)
            return value
        except Exception as e:
            self._store(key, False, e, self.failure_ttl)
            raise
        finally:
            self._inflight.pop(key, None)

    def _store(self, key, ok, value, ttl):
        self._items[key] = (time.monotonic() + ttl, ok, value)
        while len(self._items) > MAX_ENTRIES:
            self._items.pop(next(iter(self._items)))

    def clear(self):
        self._items.clear()

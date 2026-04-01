from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    ts: float
    value: T


class TTLCache(Generic[T]):
    def __init__(self, ttl_sec: float) -> None:
        self.ttl_sec = float(ttl_sec)
        self._store: dict[str, CacheEntry[T]] = {}

    def get(self, key: str) -> T | None:
        ent = self._store.get(key)
        if ent is None:
            return None
        if (time.monotonic() - ent.ts) > self.ttl_sec:
            self._store.pop(key, None)
            return None
        return ent.value

    def set(self, key: str, value: T) -> None:
        self._store[key] = CacheEntry(ts=time.monotonic(), value=value)

    def get_or_set(self, key: str, fn: Callable[[], T]) -> T:
        v = self.get(key)
        if v is not None:
            return v
        v2 = fn()
        self.set(key, v2)
        return v2

    def clear(self) -> None:
        self._store.clear()


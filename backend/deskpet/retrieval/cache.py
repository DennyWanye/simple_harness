"""Process-shared bounded cache and provider health state."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


class AsyncTTLCache(Generic[T]):
    def __init__(self, *, max_size: int, ttl_s: float) -> None:
        self.max_size = max(1, int(max_size))
        self.ttl_s = max(0.01, float(ttl_s))
        self._items: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> T | None:
        now = time.monotonic()
        async with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires, value = item
            if expires <= now:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return value

    async def put(self, key: str, value: T) -> None:
        async with self._lock:
            self._items[key] = (time.monotonic() + self.ttl_s, value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_size:
                self._items.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._items.clear()

    async def __len_async__(self) -> int:
        async with self._lock:
            return len(self._items)


@dataclass(slots=True)
class _Health:
    failures: int = 0
    cooldown_until: float = 0.0


class ProviderHealth:
    def __init__(self, *, threshold: int, cooldown_s: float) -> None:
        self.threshold = max(1, int(threshold))
        self.cooldown_s = max(0.01, float(cooldown_s))
        self._states: dict[str, _Health] = {}
        self._lock = asyncio.Lock()

    async def cooling_down(self, provider: str) -> bool:
        async with self._lock:
            state = self._states.get(provider)
            return bool(state and state.cooldown_until > time.monotonic())

    async def record_failure(self, provider: str) -> None:
        async with self._lock:
            state = self._states.setdefault(provider, _Health())
            state.failures += 1
            if state.failures >= self.threshold:
                state.cooldown_until = time.monotonic() + self.cooldown_s

    async def record_success(self, provider: str) -> None:
        async with self._lock:
            self._states[provider] = _Health()

    async def clear(self) -> None:
        async with self._lock:
            self._states.clear()

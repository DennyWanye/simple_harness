"""Owner-scoped catalog ingress gate with writer-preferring reconciliation."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Iterable


class CatalogReconcilingError(RuntimeError):
    code = "catalog_reconciling"
    retryable = True


class CatalogGateTimeout(RuntimeError):
    code = "catalog_gate_timeout"
    retryable = True


def _required(value: object, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


@dataclass(frozen=True, order=True, slots=True)
class CatalogGateKey:
    owner_key: str
    scope: str
    scope_key: str
    pack_id: str = "*"

    def __post_init__(self) -> None:
        for field_name in ("owner_key", "scope", "scope_key", "pack_id"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), field_name),
            )

    def conflicts(self, other: "CatalogGateKey") -> bool:
        return (
            self.owner_key == other.owner_key
            and self.scope == other.scope
            and self.scope_key == other.scope_key
            and (
                self.pack_id == other.pack_id
                or self.pack_id == "*"
                or other.pack_id == "*"
            )
        )


@dataclass(frozen=True, slots=True)
class CatalogGateReadToken:
    keys: tuple[CatalogGateKey, ...]
    generation: int


@dataclass(frozen=True, slots=True)
class CatalogGateWriteToken:
    keys: tuple[CatalogGateKey, ...]
    generation: int


class CapabilityCatalogGate:
    """Short-lived read/write tokens acquired after the publish lock.

    Closing a key rejects new readers immediately, then waits only for existing
    short readers to drain. Existing durable snapshot leases are deliberately
    outside this gate.
    """

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._readers: dict[CatalogGateKey, int] = {}
        self._writers: set[CatalogGateKey] = set()
        self._closed: dict[CatalogGateKey, str] = {}
        self._generation = 0

    @staticmethod
    def _keys(keys: Iterable[CatalogGateKey]) -> tuple[CatalogGateKey, ...]:
        values = tuple(sorted(set(keys)))
        if not values:
            raise ValueError("catalog gate keys are required")
        return values

    @staticmethod
    def _any_conflict(
        left: Iterable[CatalogGateKey], right: Iterable[CatalogGateKey]
    ) -> bool:
        return any(a.conflicts(b) for a in left for b in right)

    def closed_reason(self, key: CatalogGateKey) -> str | None:
        for closed, reason in self._closed.items():
            if closed.conflicts(key):
                return reason
        return None

    def is_open(self, key: CatalogGateKey) -> bool:
        return self.closed_reason(key) is None

    async def _wait(self, predicate, timeout: float | None) -> None:
        try:
            if timeout is None:
                await self._condition.wait_for(predicate)
            else:
                async with asyncio.timeout(timeout):
                    await self._condition.wait_for(predicate)
        except TimeoutError as exc:
            raise CatalogGateTimeout(CatalogGateTimeout.code) from exc

    @asynccontextmanager
    async def read(
        self,
        keys: Iterable[CatalogGateKey],
        *,
        timeout: float | None = 5.0,
    ) -> AsyncIterator[CatalogGateReadToken]:
        values = self._keys(keys)
        async with self._condition:
            if self._any_conflict(values, self._closed):
                raise CatalogReconcilingError(CatalogReconcilingError.code)
            await self._wait(
                lambda: not self._any_conflict(values, self._writers),
                timeout,
            )
            if self._any_conflict(values, self._closed):
                raise CatalogReconcilingError(CatalogReconcilingError.code)
            for key in values:
                self._readers[key] = self._readers.get(key, 0) + 1
            token = CatalogGateReadToken(values, self._generation)
        try:
            yield token
        finally:
            async with self._condition:
                for key in values:
                    remaining = self._readers[key] - 1
                    if remaining:
                        self._readers[key] = remaining
                    else:
                        self._readers.pop(key, None)
                self._condition.notify_all()

    @asynccontextmanager
    async def writer(
        self,
        keys: Iterable[CatalogGateKey],
        *,
        reason: str = "catalog_publish_pending",
        timeout: float | None = 5.0,
    ) -> AsyncIterator[CatalogGateWriteToken]:
        values = self._keys(keys)
        reason = _required(reason, "reason")
        async with self._condition:
            if self._any_conflict(values, self._writers):
                await self._wait(
                    lambda: not self._any_conflict(values, self._writers),
                    timeout,
                )
            for key in values:
                self._writers.add(key)
                self._closed[key] = reason
            try:
                await self._wait(
                    lambda: not self._any_conflict(values, self._readers),
                    timeout,
                )
            except BaseException:
                for key in values:
                    self._writers.discard(key)
                self._condition.notify_all()
                raise
            self._generation += 1
            token = CatalogGateWriteToken(values, self._generation)
        try:
            yield token
        finally:
            async with self._condition:
                for key in values:
                    self._writers.discard(key)
                self._condition.notify_all()

    async def open_after_reconcile(
        self,
        token: CatalogGateWriteToken,
        *,
        committed_stamp: str,
        manager_receipt_hash: str,
    ) -> None:
        _required(committed_stamp, "committed_stamp")
        _required(manager_receipt_hash, "manager_receipt_hash")
        async with self._condition:
            if any(key in self._writers for key in token.keys):
                raise RuntimeError("catalog gate writer is still active")
            for key in token.keys:
                self._closed.pop(key, None)
            self._generation += 1
            self._condition.notify_all()

    async def recover_closed(
        self, keys: Iterable[CatalogGateKey], *, reason: str
    ) -> None:
        values = self._keys(keys)
        async with self._condition:
            for key in values:
                self._closed[key] = _required(reason, "reason")
            self._generation += 1
            self._condition.notify_all()


__all__ = [
    "CapabilityCatalogGate",
    "CatalogGateKey",
    "CatalogGateReadToken",
    "CatalogGateTimeout",
    "CatalogGateWriteToken",
    "CatalogReconcilingError",
]

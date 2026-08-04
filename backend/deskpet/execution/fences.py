"""Product-neutral execution fence ports and the explicit unbound policy."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Protocol


def _required(value: str, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _hash(value: str, name: str) -> str:
    normalized = _required(value, name)
    if (
        len(normalized) != 64
        or any(char not in "0123456789abcdef" for char in normalized)
    ):
        raise ValueError(f"{name} must be lowercase SHA-256")
    return normalized


@dataclass(frozen=True, slots=True)
class TerminalCommitFenceV1:
    """Frozen delivery identity captured while the Run fence is held."""

    delivery_fence_epoch: int
    owner_id: str
    owner_generation: int
    dependency_hash: str
    snapshot_ref: str
    snapshot_hash: str
    release_receipt_kind: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.delivery_fence_epoch, bool)
            or self.delivery_fence_epoch < 0
        ):
            raise ValueError("delivery_fence_epoch must be non-negative")
        if isinstance(self.owner_generation, bool) or self.owner_generation < 1:
            raise ValueError("owner_generation must be positive")
        for name in ("owner_id", "snapshot_ref", "release_receipt_kind"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        for name in ("dependency_hash", "snapshot_hash"):
            object.__setattr__(self, name, _hash(getattr(self, name), name))

    def to_uow_fence(self) -> Mapping[str, Any]:
        return {
            "delivery_fence_epoch": self.delivery_fence_epoch,
            "owner_id": self.owner_id,
            "owner_generation": self.owner_generation,
            "dependency_hash": self.dependency_hash,
            "snapshot_ref": self.snapshot_ref,
            "snapshot_hash": self.snapshot_hash,
        }


class RunExecutionFenceLease(Protocol):
    run_id: str
    revocation_epoch: int
    terminal_commit_fence: TerminalCommitFenceV1 | None

    async def release(self) -> None: ...


class RunExecutionFencePort(Protocol):
    async def acquire(self, run_id: str) -> RunExecutionFenceLease: ...


class TerminalDeliveryFencePort(Protocol):
    async def authorize(
        self, *, run_id: str, delivery_id: str, release_receipt_ref: str
    ) -> bool: ...


class CallableRunExecutionFence:
    """Adapt one existing shared acquirer without creating a second lock."""

    def __init__(
        self,
        acquire: Callable[[str], Awaitable[RunExecutionFenceLease]],
    ) -> None:
        if not callable(acquire):
            raise TypeError("run execution fence acquirer must be callable")
        self._acquire = acquire

    async def acquire(self, run_id: str) -> RunExecutionFenceLease:
        return await self._acquire(_required(run_id, "run_id"))


@dataclass(slots=True)
class UnboundRunExecutionFenceLease:
    """Lease for Runs that have no product snapshot dependency."""

    run_id: str
    revocation_epoch: int = 0
    terminal_commit_fence: TerminalCommitFenceV1 | None = None
    _released: bool = field(default=False, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def release(self) -> None:
        async with self._lock:
            self._released = True

    @property
    def released(self) -> bool:
        return self._released


class UnboundRunExecutionFence:
    """Explicit no-op authority for product-neutral, dependency-free Runs."""

    async def acquire(self, run_id: str) -> UnboundRunExecutionFenceLease:
        normalized = str(run_id or "").strip()
        if not normalized:
            raise ValueError("run_id is required")
        return UnboundRunExecutionFenceLease(normalized)


class UnboundTerminalDeliveryFence:
    async def authorize(
        self, *, run_id: str, delivery_id: str, release_receipt_ref: str
    ) -> bool:
        return bool(
            str(run_id or "").strip()
            and str(delivery_id or "").strip()
            and str(release_receipt_ref or "").strip()
        )


async def release_run_execution_fence(lease: RunExecutionFenceLease) -> None:
    release = getattr(lease, "release", None)
    if not callable(release):
        raise RuntimeError("run execution fence lease must expose release()")
    result = release()
    if asyncio.iscoroutine(result):
        await result


async def validate_run_execution_epoch(
    acquire: Callable[[str], Awaitable[RunExecutionFenceLease]] | None,
    *,
    run_id: str,
    expected_epoch: int,
) -> None:
    """Reacquire after a long dispatch and suppress stale result bodies."""

    if acquire is None:
        return
    lease = await acquire(_required(run_id, "run_id"))
    try:
        if (
            str(getattr(lease, "run_id", "")) != run_id
            or int(getattr(lease, "revocation_epoch", -1)) != expected_epoch
        ):
            raise RuntimeError("run_execution_fence_epoch_drift")
    finally:
        await release_run_execution_fence(lease)

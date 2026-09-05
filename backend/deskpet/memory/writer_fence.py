# SPDX-License-Identifier: BUSL-1.1

"""Shared v42 ingress check for registered canonical writer transactions."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

@dataclass
class _RequestFenceScope:
    fence: object
    task: object
    depth: int = 0


_REQUEST_FENCE: ContextVar[_RequestFenceScope | None] = ContextVar("human_memory_request_fence", default=None)


@contextmanager
def authenticated_memory_request(fence):
    """Propagate connection authority without adding it to durable payloads."""
    token = _REQUEST_FENCE.set(None if fence is None else _RequestFenceScope(fence, asyncio.current_task()))
    try:
        yield
    finally:
        _REQUEST_FENCE.reset(token)


def _request_scope():
    scope = _REQUEST_FENCE.get()
    # Already-admitted runtime/background work owns its durable Run lease.
    # asyncio task context inheritance must not turn it into a socket request.
    return scope if scope is not None and scope.task is asyncio.current_task() else None


@asynccontextmanager
async def human_memory_request_boundary():
    """A short existing revocation lease, rechecked at the actual DB boundary."""
    scope = _request_scope()
    if scope is None:
        yield
        return
    if scope.depth:
        scope.fence.verify()
        yield
        return
    async with scope.fence.barrier.shared():
        scope.fence.verify()
        scope.depth += 1
        try:
            yield
        finally:
            scope.depth -= 1


@asynccontextmanager
async def human_memory_connection(path):
    # Acquire before BEGIN IMMEDIATE to avoid lock inversion with lifecycle
    # operations. Keep the short shared lease through commit/rollback/close.
    async with human_memory_request_boundary():
        async with aiosqlite.connect(path) as db:
            yield db


INGRESS_FENCED_CODE = "human_memory_ingress_fenced"


class HumanMemoryIngressFenced(RuntimeError):
    code = INGRESS_FENCED_CODE

    def __init__(self) -> None:
        super().__init__(self.code)


async def assert_human_memory_ingress_open_tx(
    db: aiosqlite.Connection,
) -> int | None:
    """Check the fence after BEGIN IMMEDIATE and before the first mutation."""

    scope = _request_scope()
    if scope is not None:
        scope.fence.verify()
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='human_memory_recovery_fence'"
    )
    ready = await cursor.fetchone()
    await cursor.close()
    if ready is None:  # v35-v41 replay during the exact migration chain.
        return None
    cursor = await db.execute(
        "SELECT state,generation FROM human_memory_recovery_fence WHERE singleton=1"
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None or row[0] != "OPEN":
        raise HumanMemoryIngressFenced()
    return int(row[1])


async def assert_human_memory_ingress_open(db_path: str | Path) -> int | None:
    """Read-only facade preflight; writer transactions must still recheck."""

    async with aiosqlite.connect(Path(db_path)) as db:
        return await assert_human_memory_ingress_open_tx(db)


__all__ = [
    "INGRESS_FENCED_CODE",
    "HumanMemoryIngressFenced",
    "assert_human_memory_ingress_open",
    "assert_human_memory_ingress_open_tx",
]

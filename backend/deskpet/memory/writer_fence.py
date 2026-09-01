# SPDX-License-Identifier: BUSL-1.1

"""Shared v42 ingress check for registered canonical writer transactions."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

INGRESS_FENCED_CODE = "human_memory_ingress_fenced"


class HumanMemoryIngressFenced(RuntimeError):
    code = INGRESS_FENCED_CODE

    def __init__(self) -> None:
        super().__init__(self.code)


async def assert_human_memory_ingress_open_tx(
    db: aiosqlite.Connection,
) -> int | None:
    """Check the fence after BEGIN IMMEDIATE and before the first mutation."""

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

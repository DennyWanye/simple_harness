# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Shared final-boundary fence for the fresh primary conversation authority."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import aiosqlite

PRIMARY_AUTHORITY_IMMUTABLE = "human_memory_primary_authority_immutable"


class PrimaryAuthorityImmutableError(RuntimeError):
    code = PRIMARY_AUTHORITY_IMMUTABLE

    def __init__(self) -> None:
        super().__init__(self.code)


async def assert_not_primary_authority_tx(
    db: aiosqlite.Connection, identifiers: Iterable[str | None]
) -> None:
    targets = tuple(
        value.strip()
        for value in identifiers
        if isinstance(value, str) and value.strip()
    )
    if not targets:
        return
    table = await (
        await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='human_memory_primary_conversations'"
        )
    ).fetchone()
    if table is None:
        return
    placeholders = ",".join("?" for _ in targets)
    row = await (
        await db.execute(
            "SELECT 1 FROM human_memory_primary_conversations "
            f"WHERE writable=1 AND primary_conversation_id IN ({placeholders}) LIMIT 1",
            targets,
        )
    ).fetchone()
    if row is not None:
        raise PrimaryAuthorityImmutableError()


async def assert_not_primary_authority(
    db_path: str | Path, *identifiers: str | None
) -> None:
    path = Path(db_path)
    if not path.exists():
        return
    async with aiosqlite.connect(path) as db:
        await assert_not_primary_authority_tx(db, identifiers)


__all__ = (
    "PRIMARY_AUTHORITY_IMMUTABLE",
    "PrimaryAuthorityImmutableError",
    "assert_not_primary_authority",
    "assert_not_primary_authority_tx",
)

# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""HTN store construction and the row helpers every table family uses."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..store import Store, StoreConflict


class HtnStoreCore:
    """HTN store construction and the row helpers every table family uses."""

    def __init__(self, store: Store) -> None:
        self._store = store

    #: Which ``acceptances.validity`` values still make an indexed output usable.
    #: The same set the accept side spells as ``USABLE_ACCEPTANCE_VALIDITY``; it lives
    #: here as SQL because the filter is part of the *read*, not of a caller's loop.
    USABLE_ACCEPTANCE_VALIDITY: tuple[str, ...] = ("CURRENT",)

    @staticmethod
    def _state(value: str, allowed: tuple[str, ...], name: str) -> str:
        text = str(value)
        if text not in allowed:
            raise StoreConflict(f"{name} must be one of {allowed}, not {text!r}")
        return text

    def _one(self, sql: str, values: tuple[Any, ...], missing: str) -> sqlite3.Row:
        row = self._store.connection.execute(sql, values).fetchone()
        if row is None:
            raise StoreConflict(missing)
        return row

    def _insert(self, sql: str, values: tuple[Any, ...], conflict: str) -> None:
        with self._store.transaction() as connection:
            self._execute(connection, sql, values, conflict)

    @staticmethod
    def _execute(
        connection: sqlite3.Connection, sql: str, values: tuple[Any, ...], conflict: str
    ) -> None:
        try:
            connection.execute(sql, values)
        except sqlite3.IntegrityError as error:
            raise StoreConflict(f"{conflict}: {error}") from error

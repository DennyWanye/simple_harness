# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""The append-only event log."""

from __future__ import annotations

from collections.abc import Sequence

from simple_harness.contracts import canonical_json

from ...contracts import Event
from .core import StoreCore
from .records import _event_from_row


class StoreEventMixin(StoreCore):
    """The append-only event log."""

    __slots__ = ()

    def iter_events(self, mission_id: str, *, page: int = 5_000) -> list[Event]:
        """Every event of a Mission, read page by page (plan D8-1': never silently cut
        at a fixed limit)."""

        events: list[Event] = []
        after = 0
        while True:
            batch = self.list_events(mission_id, after_seq=after, limit=page)
            events.extend(batch)
            if len(batch) < page:
                return events
            after = int(batch[-1].seq or after)

    # ----------------------------------------------------------------- events
    def append_event(self, event: Event) -> Event:
        """Idempotent append: an existing ``idempotency_key`` returns the stored event."""

        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM events WHERE idempotency_key = ?", (event.idempotency_key,)
            ).fetchone()
            if existing is not None:
                return _event_from_row(existing)
            cursor = connection.execute(
                "INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,"
                "attempt_id,actor_type,actor_id,payload_json,created_at,schema_version)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event.id,
                    event.idempotency_key,
                    event.type,
                    event.trace_id,
                    event.mission_id,
                    event.task_id,
                    event.attempt_id,
                    event.actor_type,
                    event.actor_id,
                    canonical_json(dict(event.payload)),
                    event.created_at,
                    event.schema_version,
                ),
            )
            return Event(**{**event.to_json(), "seq": cursor.lastrowid})

    def list_events(
        self,
        mission_id: str,
        *,
        after_seq: int = 0,
        limit: int = 10_000,
        types: Sequence[str] | None = None,
    ) -> list[Event]:
        """``types`` filters in SQL: a reader that wants a few kinds does not parse the
        rest.  Without ``types`` the storage layer's own ``RowsWritten`` bookkeeping is left
        out (阶段 G 阻断核验：它约占一个任务事件的四分之一，混进来会把读前 N 条再筛的读取方
        挤到读不到最新事件)；全业务重放直接读它，要它的读取方显式点名这个类型。"""

        # 按需导入：循环依赖（store ↔ source_records）
        from ..source_records import EVENT_TYPE

        kinds = (
            f" AND type != '{EVENT_TYPE}'"
            if types is None
            else f" AND type IN ({','.join('?' * len(types))})"
        )
        rows = self._connection.execute(
            f"SELECT * FROM events WHERE mission_id = ? AND seq > ?{kinds} ORDER BY seq LIMIT ?",  # noqa: S608
            (mission_id, after_seq, *(types or ()), limit),
        ).fetchall()
        return [_event_from_row(row) for row in rows]

    def count_events(self, mission_id: str, event_type: str | None = None) -> int:
        """Without ``event_type``: the same rows :meth:`list_events` lists — the storage
        layer's own ``RowsWritten`` bookkeeping is left out, so a count and a full page
        walk agree (2026-10-07, Host 事件分页对不上数)."""
        if event_type is None:
            # 按需导入：循环依赖（store ↔ source_records）
            from ..source_records import EVENT_TYPE

            row = self._connection.execute(
                "SELECT COUNT(*) FROM events WHERE mission_id = ? AND type != ?",
                (mission_id, EVENT_TYPE),
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM events WHERE mission_id = ? AND type = ?",
                (mission_id, event_type),
            ).fetchone()
        return int(row[0])

    def last_event_seq(self, mission_id: str) -> int:
        """The Mission's highest event ``seq`` (0 when none) — a snapshot's cursor."""

        row = self._connection.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM events WHERE mission_id = ?", (mission_id,)
        ).fetchone()
        return int(row[0])

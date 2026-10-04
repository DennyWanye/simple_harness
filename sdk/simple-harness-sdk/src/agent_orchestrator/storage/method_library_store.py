# SPDX-License-Identifier: Apache-2.0
"""The method library: precedents promoted from delivered Missions, and who blamed them.

Two tables (migration 40) outside the assurance channel — they only decide which precedents
a Planner is shown; no certificate reads them, so writing them moves no epoch.  The method
definitions themselves stay where they are (``method_contracts``, immutable).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

LISTED = "LISTED"
RETIRED = "RETIRED"
CLEARED_EVENT = "MethodLibraryCleared"
_COLUMNS = ("entry_id", "owner", "goal_type_id", "catalog_digest", "method_id", "method_version",
            "method_hash", "purpose", "source_mission_id", "root_review_record_id", "based_on",
            "state", "retired_by", "retired_reason", "retired_at", "promoted_at")


class MethodLibraryStore:
    def __init__(self, store: Any) -> None:
        self.store = store

    def _rows(self, where: str, args: Sequence[Any], tail: str = "") -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            f"SELECT {','.join(_COLUMNS)} FROM method_library {where} {tail}", tuple(args)).fetchall()
        return [dict(zip(_COLUMNS, row, strict=True)) for row in rows]

    def insert(self, entry: Mapping[str, Any]) -> None:
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO method_library(entry_id,owner,goal_type_id,catalog_digest,method_id,"
                "method_version,method_hash,purpose,source_mission_id,root_review_record_id,based_on,"
                "state,promoted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (entry["entry_id"], entry["owner"], entry["goal_type_id"], entry["catalog_digest"],
                 entry["method_id"], int(entry["method_version"]), entry["method_hash"], entry["purpose"],
                 entry["source_mission_id"], entry["root_review_record_id"], entry.get("based_on"),
                 LISTED, self.store.now))

    def get(self, entry_id: str) -> dict[str, Any] | None:
        rows = self._rows("WHERE entry_id=?", (entry_id,))
        return rows[0] if rows else None

    def find(self, owner: str, method_id: str, method_version: int) -> dict[str, Any] | None:
        rows = self._rows("WHERE owner=? AND method_id=? AND method_version=?",
                          (owner, method_id, int(method_version)))
        return rows[0] if rows else None

    def listed(self, owner: str, digest: str, goal_type_id: str) -> list[dict[str, Any]]:
        """The entries one goal type's directory is cut from, newest first."""
        return self._rows(
            "WHERE owner=? AND goal_type_id=? AND catalog_digest=? AND state=?",
            (owner, goal_type_id, digest, LISTED), "ORDER BY promoted_at DESC, entry_id")

    def all(self) -> list[dict[str, Any]]:
        return self._rows("", (), "ORDER BY promoted_at DESC, entry_id")

    def retire(self, entry_id: str, *, by: str, reason: str) -> bool:
        """LISTED → RETIRED; False when it already was."""
        with self.store.transaction() as connection:
            changed = connection.execute(
                "UPDATE method_library SET state=?,retired_by=?,retired_reason=?,retired_at=?"
                " WHERE entry_id=? AND state=?",
                (RETIRED, by, reason, self.store.now, entry_id, LISTED)).rowcount
        return bool(changed)

    def add_attribution(self, entry_id: str, *, source_ref: str, source_kind: str, mission_id: str,
                        method_id: str, method_version: int, method_hash: str, reason: str) -> bool:
        """One blame, once per source; False when that source already said so."""
        with self.store.transaction() as connection:
            changed = connection.execute(
                "INSERT OR IGNORE INTO method_library_attributions(entry_id,source_ref,source_kind,"
                "mission_id,method_id,method_version,method_hash,reason,recorded_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (entry_id, source_ref, source_kind, mission_id, method_id, int(method_version),
                 method_hash, reason, self.store.now)).rowcount
        return bool(changed)

    def attributions(self, entry_id: str) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT source_ref,source_kind,mission_id,reason FROM method_library_attributions"
            " WHERE entry_id=? ORDER BY recorded_at, source_ref", (entry_id,)).fetchall()
        return [{"source_ref": row[0], "source_kind": row[1], "mission_id": row[2], "reason": row[3]}
                for row in rows]

    def clear(self) -> dict[str, int]:
        """Empty both tables (a development diagnostic); the method definitions are untouched.

        A deployment-level fact: one ``MethodLibraryCleared`` on the deployment timeline in the
        same transaction — a global table never changes silently (HTN 补齐阶段 G 第 6 批)."""
        from ..contracts import Event, ids
        from .source_records import DEPLOYMENT_TIMELINE

        with self.store.transaction() as connection:
            blamed = connection.execute("DELETE FROM method_library_attributions").rowcount
            connection.execute("UPDATE method_library SET based_on=NULL")
            entries = connection.execute("DELETE FROM method_library").rowcount
            counts = {"entries": int(entries), "attributions": int(blamed)}
            seq = int(connection.execute("SELECT coalesce(max(seq), 0) FROM events").fetchone()[0])
            key = f"{CLEARED_EVENT}:{seq}"
            self.store.append_event(Event(
                id=ids.event_id(key), type=CLEARED_EVENT, trace_id=ids.trace_id(DEPLOYMENT_TIMELINE),
                mission_id=DEPLOYMENT_TIMELINE, task_id=None, attempt_id=None, actor_type="system",
                actor_id="method-library", payload=counts, idempotency_key=key, created_at=self.store.now))
        return counts


__all__ = ("CLEARED_EVENT", "LISTED", "RETIRED", "MethodLibraryStore")

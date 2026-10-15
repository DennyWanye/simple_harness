# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""Actions, approvals, decisions, overrides, tool calls, scheduler state and policies."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from simple_harness.contracts import canonical_json

from ...contracts.models import SEARCH_TOOL_NAMES
from .core import StoreCore
from .records import StoreError, _loads


class StoreGovernanceMixin(StoreCore):
    """Actions, approvals, decisions, overrides, tool calls, scheduler state and policies."""

    __slots__ = ()

    # ----------------------------------------------------------- graph changes
    # --------------------------------------------------------------- actions (step 7)
    def put_action(self, record: Mapping[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO actions(action_key,action_id,version,mission_id,state,json,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(action_key) DO UPDATE SET state = excluded.state,"
                " json = excluded.json, updated_at = excluded.updated_at"
                # 内容没变就不写：只改时间的空改动没有事件，是静默改动（阶段 G 收尾大语料）
                " WHERE actions.json IS NOT excluded.json OR actions.state IS NOT excluded.state",
                (
                    str(record["action_key"]),
                    str(record["action_id"]),
                    int(record["version"]),
                    str(record["mission_id"]),
                    str(record["state"]),
                    canonical_json(dict(record)),
                    self.now,
                    self.now,
                ),
            )

    def get_action(self, action_key: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT json FROM actions WHERE action_key = ?", (action_key,)
        ).fetchone()
        return None if row is None else dict(_loads(row[0]))

    def list_action_versions(self, action_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT json FROM actions WHERE action_id = ? ORDER BY version", (action_id,)
        ).fetchall()
        return [dict(_loads(row[0])) for row in rows]

    def list_actions(self, mission_id: str | None = None, *states: str) -> list[dict[str, Any]]:
        sql = "SELECT json FROM actions"
        clauses: list[str] = []
        args: list[Any] = []
        if mission_id is not None:
            clauses.append("mission_id = ?")
            args.append(mission_id)
        if states:
            clauses.append(f"state IN ({','.join('?' for _ in states)})")
            args.extend(states)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = self._connection.execute(
            sql + " ORDER BY created_at, action_key", tuple(args)
        ).fetchall()
        return [dict(_loads(row[0])) for row in rows]

    def put_approval(self, record: Mapping[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO approvals(request_id,kind,mission_id,subject_key,state,version,json,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(request_id) DO UPDATE SET state = excluded.state,"
                " version = excluded.version, json = excluded.json, updated_at = excluded.updated_at",
                (
                    str(record["request_id"]),
                    str(record["kind"]),
                    str(record["mission_id"]),
                    str(record["subject_key"]),
                    str(record["state"]),
                    int(record["version"]),
                    canonical_json(dict(record)),
                    self.now,
                    self.now,
                ),
            )

    def get_approval(self, request_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT json FROM approvals WHERE request_id = ?", (request_id,)
        ).fetchone()
        return None if row is None else dict(_loads(row[0]))

    def list_approvals(self, mission_id: str | None = None, *states: str) -> list[dict[str, Any]]:
        sql = "SELECT json FROM approvals"
        clauses: list[str] = []
        args: list[Any] = []
        if mission_id is not None:
            clauses.append("mission_id = ?")
            args.append(mission_id)
        if states:
            clauses.append(f"state IN ({','.join('?' for _ in states)})")
            args.extend(states)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = self._connection.execute(
            sql + " ORDER BY created_at, request_id", tuple(args)
        ).fetchall()
        return [dict(_loads(row[0])) for row in rows]

    def insert_decision(self, record: Mapping[str, Any]) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO approval_decisions(receipt_hash,request_id,principal_id,decision,nonce,json,created_at)"
                " VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                (
                    str(record["receipt_hash"]),
                    str(record["request_id"]),
                    str(record["principal_id"]),
                    str(record["decision"]),
                    str(record["nonce"]),
                    canonical_json(dict(record)),
                    self.now,
                ),
            )
            return cursor.rowcount == 1

    def list_decisions(self, request_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT json FROM approval_decisions WHERE request_id = ? ORDER BY created_at, receipt_hash",
            (request_id,),
        ).fetchall()
        return [dict(_loads(row[0])) for row in rows]

    def insert_override(self, record: Mapping[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO human_overrides(override_id,mission_id,json,created_at) VALUES (?,?,?,?)"
                " ON CONFLICT(override_id) DO NOTHING",
                (
                    str(record["override_id"]),
                    str(record["mission_id"]),
                    canonical_json(dict(record)),
                    self.now,
                ),
            )

    def list_overrides(self, mission_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT json FROM human_overrides WHERE mission_id = ? ORDER BY created_at, override_id",
            (mission_id,),
        ).fetchall()
        return [dict(_loads(row[0])) for row in rows]

    # ------------------------------------------------------------ tool calls
    def record_tool_call(
        self, *, call_key: str, subject_id: str, mission_id: str, tool: str, outcome: str
    ) -> bool:
        """One executed tool call, at most once per SDK call id (review P1-3)."""

        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO tool_calls(call_key,subject_id,mission_id,tool,outcome,created_at)"
                " VALUES (?,?,?,?,?,?) ON CONFLICT(call_key) DO NOTHING",
                (call_key, subject_id, mission_id, tool, outcome, self.now),
            )
            return cursor.rowcount == 1

    def get_tool_call(self, call_key: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT call_key,subject_id,mission_id,tool,outcome FROM tool_calls WHERE call_key = ?",
            (call_key,),
        ).fetchone()
        return (
            None
            if row is None
            else dict(
                zip(("call_key", "subject_id", "mission_id", "tool", "outcome"), row, strict=True)
            )
        )

    def count_search_calls(self, subject_id: str) -> int:
        """推后第 3 批 H08：这个主体真执行过的检索类工具调用数（``SEARCH_TOOL_NAMES``）。"""

        names = sorted(SEARCH_TOOL_NAMES)
        row = self._connection.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE subject_id = ? AND outcome = 'succeeded'"
            f" AND tool IN ({','.join('?' for _ in names)})",
            (subject_id, *names),
        ).fetchone()
        return int(row[0])

    def count_tool_calls(self, subject_id: str, *, outcome: str = "succeeded") -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE subject_id = ? AND outcome = ?",
            (subject_id, outcome),
        ).fetchone()
        return int(row[0])

    # --------------------------------------------------------- scheduler state
    def get_scheduler_state(self, key: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT json FROM scheduler_state WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else dict(_loads(row[0]))

    def put_scheduler_state(self, key: str, value: Mapping[str, Any]) -> int:
        """Upsert; returns the new version.  Inside a Commit Service transaction."""

        with self.transaction() as connection:
            row = connection.execute(
                "SELECT version FROM scheduler_state WHERE key = ?", (key,)
            ).fetchone()
            version = 1 if row is None else int(row[0]) + 1
            connection.execute(
                "INSERT INTO scheduler_state(key,json,version,updated_at) VALUES (?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET json = excluded.json, version = excluded.version,"
                " updated_at = excluded.updated_at",
                (key, canonical_json(dict(value)), version, self.now),
            )
            return version

    # --------------------------------------------------------- policy registry (step 9)
    def insert_policy_version(self, record: Mapping[str, Any]) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO policy_versions(version_id,params_hash,source,status,json,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                (
                    str(record["version_id"]),
                    str(record["params_hash"]),
                    str(record["source"]),
                    str(record["status"]),
                    canonical_json(dict(record)),
                    self.now,
                    self.now,
                ),
            )
            return cursor.rowcount == 1

    def set_policy_version_status(self, version_id: str, status: str) -> None:
        with self.transaction() as connection:
            record = self.get_policy_version(version_id)
            if record is None:
                raise StoreError(f"unknown policy version {version_id}")
            record["status"] = status
            connection.execute(
                "UPDATE policy_versions SET status = ?, json = ?, updated_at = ? WHERE version_id = ?",
                (status, canonical_json(record), self.now, version_id),
            )

    def get_policy_version(self, version_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT json FROM policy_versions WHERE version_id = ?", (version_id,)
        ).fetchone()
        return None if row is None else dict(_loads(row[0]))

    def list_policy_versions(self) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT json FROM policy_versions ORDER BY created_at, version_id"
        ).fetchall()
        return [dict(_loads(row[0])) for row in rows]

    def insert_policy_activation(self, record: Mapping[str, Any]) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO policy_activations(version_id,action,json,created_at) VALUES (?,?,?,?)",
                (
                    str(record["version_id"]),
                    str(record["action"]),
                    canonical_json(dict(record)),
                    self.now,
                ),
            )
            return int(cursor.lastrowid or 0)

    def list_policy_activations(self) -> list[dict[str, Any]]:
        if not self.has_table("policy_activations"):
            return []
        rows = self._connection.execute(
            "SELECT seq, json, created_at FROM policy_activations ORDER BY seq"
        ).fetchall()
        return [
            {**dict(_loads(row[1])), "seq": int(row[0]), "created_at": float(row[2])}
            for row in rows
        ]

    def active_policy(self) -> dict[str, Any] | None:
        """The version the last activation made ACTIVE (plan D9-2'), or None."""

        if not self.has_table("policy_activations"):
            return None
        row = self._connection.execute(
            "SELECT version_id FROM policy_activations ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return None if row is None else self.get_policy_version(str(row[0]))

    def bind_mission_policy(self, record: Mapping[str, Any]) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO mission_policies(mission_id,version_id,source,provider_kind,json,bound_at)"
                " VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                (
                    str(record["mission_id"]),
                    str(record["version_id"]),
                    str(record["source"]),
                    str(record["provider_kind"]),
                    canonical_json(dict(record)),
                    self.now,
                ),
            )
            return cursor.rowcount == 1

    def get_mission_policy(self, mission_id: str) -> dict[str, Any] | None:
        if not self.has_table("mission_policies"):
            return None
        row = self._connection.execute(
            "SELECT json FROM mission_policies WHERE mission_id = ?", (mission_id,)
        ).fetchone()
        return None if row is None else dict(_loads(row[0]))

    # ---------------------------------------------------------- domains (P3.3 D1)

    def bind_mission_domain(
        self, mission_id: str, *, domain_id: str, domain_version: str, snapshot: Mapping[str, Any]
    ) -> bool:
        """Freeze this Mission's domain profile.  The snapshot is a copy of the profile's
        content: a later change to the registry never moves a running Mission."""

        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO mission_domains(mission_id,domain_id,domain_version,json,bound_at)"
                " VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
                (
                    str(mission_id),
                    str(domain_id),
                    str(domain_version),
                    canonical_json(dict(snapshot)),
                    self.now,
                ),
            )
            return cursor.rowcount == 1

    def get_mission_domain(self, mission_id: str) -> dict[str, Any] | None:
        if not self.has_table("mission_domains"):
            return None  # a library from before schema v8
        row = self._connection.execute(
            "SELECT domain_id,domain_version,json,bound_at FROM mission_domains WHERE mission_id = ?",
            (mission_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "mission_id": mission_id,
            "domain_id": row[0],
            "domain_version": row[1],
            "json": dict(_loads(row[2])),
            "bound_at": row[3],
        }

    def list_mission_policies(self, version_id: str | None = None) -> list[dict[str, Any]]:
        if not self.has_table("mission_policies"):
            return []
        sql = "SELECT json FROM mission_policies"
        args: tuple[Any, ...] = ()
        if version_id is not None:
            sql, args = sql + " WHERE version_id = ?", (version_id,)
        rows = self._connection.execute(sql + " ORDER BY bound_at, mission_id", args).fetchall()
        return [dict(_loads(row[0])) for row in rows]

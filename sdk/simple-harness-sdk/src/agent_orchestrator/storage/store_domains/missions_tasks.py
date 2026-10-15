# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""Missions, tasks and attempts."""

from __future__ import annotations

from simple_harness.contracts import canonical_json

from ...contracts import Attempt, Mission, Task
from .core import StoreCore
from .records import _loads


class StoreMissionTaskMixin(StoreCore):
    """Missions, tasks and attempts."""

    __slots__ = ()

    # --------------------------------------------------------------- missions
    def insert_mission(self, mission: Mission, *, spec_hash: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO missions(mission_id,tenant_id,idempotency_key,status,version,"
                "spec_hash,json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    mission.id,
                    mission.tenant_id,
                    mission.idempotency_key,
                    str(mission.status),
                    mission.version,
                    spec_hash,
                    canonical_json(mission.to_json()),
                    mission.created_at,
                    self.now,
                ),
            )

    def update_mission(self, mission: Mission, *, expected_version: int) -> None:
        self._cas(
            "missions",
            "mission_id",
            mission.id,
            expected_version,
            {
                "status": str(mission.status),
                "version": mission.version,
                "json": canonical_json(mission.to_json()),
            },
        )

    def get_mission(self, mission_id: str) -> Mission | None:
        row = self._connection.execute(
            "SELECT json FROM missions WHERE mission_id = ?", (mission_id,)
        ).fetchone()
        return None if row is None else Mission.from_json(_loads(row[0]))

    def find_mission(self, tenant_id: str, idempotency_key: str) -> tuple[Mission, str] | None:
        row = self._connection.execute(
            "SELECT json, spec_hash FROM missions WHERE tenant_id = ? AND idempotency_key = ?",
            (tenant_id, idempotency_key),
        ).fetchone()
        return None if row is None else (Mission.from_json(_loads(row[0])), str(row[1]))

    def list_missions(self, *, statuses: tuple[str, ...] | None = None) -> list[Mission]:
        if statuses:
            marks = ",".join("?" for _ in statuses)
            rows = self._connection.execute(
                f"SELECT json FROM missions WHERE status IN ({marks}) ORDER BY created_at",
                tuple(statuses),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT json FROM missions ORDER BY created_at"
            ).fetchall()
        return [Mission.from_json(_loads(row[0])) for row in rows]

    # ------------------------------------------------------------------ tasks
    def insert_task(self, task: Task, *, ordinal: int) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO tasks(task_id,mission_id,ordinal,status,version,json,updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    task.id,
                    task.mission_id,
                    ordinal,
                    str(task.status),
                    task.version,
                    canonical_json(task.to_json()),
                    self.now,
                ),
            )

    def update_task(self, task: Task, *, expected_version: int) -> None:
        self._cas(
            "tasks",
            "task_id",
            task.id,
            expected_version,
            {
                "status": str(task.status),
                "version": task.version,
                "json": canonical_json(task.to_json()),
            },
        )

    def get_task(self, task_id: str) -> Task | None:
        row = self._connection.execute(
            "SELECT json FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        return None if row is None else Task.from_json(_loads(row[0]))

    def list_tasks(self, mission_id: str) -> list[Task]:
        rows = self._connection.execute(
            "SELECT json FROM tasks WHERE mission_id = ? ORDER BY ordinal", (mission_id,)
        ).fetchall()
        return [Task.from_json(_loads(row[0])) for row in rows]

    # --------------------------------------------------------------- attempts
    def insert_attempt(self, attempt: Attempt) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO attempts(attempt_id,task_id,mission_id,ordinal,status,version,"
                "lease_owner,lease_expires_at,agent_id,turn_id,json,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    attempt.id,
                    attempt.task_id,
                    attempt.mission_id,
                    attempt.ordinal,
                    str(attempt.status),
                    attempt.version,
                    attempt.lease_owner,
                    attempt.lease_expires_at,
                    attempt.agent_id,
                    attempt.turn_id,
                    canonical_json(attempt.to_json()),
                    self.now,
                ),
            )

    def update_attempt(self, attempt: Attempt, *, expected_version: int) -> None:
        self._cas(
            "attempts",
            "attempt_id",
            attempt.id,
            expected_version,
            {
                "status": str(attempt.status),
                "version": attempt.version,
                "lease_owner": attempt.lease_owner,
                "lease_expires_at": attempt.lease_expires_at,
                "agent_id": attempt.agent_id,
                "turn_id": attempt.turn_id,
                "json": canonical_json(attempt.to_json()),
            },
        )

    def get_attempt(self, attempt_id: str) -> Attempt | None:
        row = self._connection.execute(
            "SELECT json FROM attempts WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        return None if row is None else Attempt.from_json(_loads(row[0]))

    def list_attempts(self, task_id: str) -> list[Attempt]:
        rows = self._connection.execute(
            "SELECT json FROM attempts WHERE task_id = ? ORDER BY ordinal", (task_id,)
        ).fetchall()
        return [Attempt.from_json(_loads(row[0])) for row in rows]

    def count_attempts_by_status(self, *statuses: str) -> int:
        """Attempts in any of ``statuses`` across every Mission (step 6: the global cap)."""

        if not statuses:
            return 0
        marks = ",".join("?" for _ in statuses)
        row = self._connection.execute(
            f"SELECT COUNT(*) FROM attempts WHERE status IN ({marks})", tuple(statuses)
        ).fetchone()
        return int(row[0])

    def list_attempts_by_status(self, *statuses: str) -> list[Attempt]:
        marks = ",".join("?" for _ in statuses)
        rows = self._connection.execute(
            f"SELECT json FROM attempts WHERE status IN ({marks}) ORDER BY updated_at",
            tuple(statuses),
        ).fetchall()
        return [Attempt.from_json(_loads(row[0])) for row in rows]

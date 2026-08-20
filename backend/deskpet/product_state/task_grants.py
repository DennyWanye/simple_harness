"""Durable product authority for TaskGrant lifecycle state."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Callable

from deskpet.types.task_grants import TaskGrant

from .database import ProductStateDatabase


class TaskGrantConflict(RuntimeError):
    code = "task_grant_conflict"


@dataclass(frozen=True, slots=True)
class DurableTaskGrant:
    grant: TaskGrant
    status: str


class DurableTaskGrantAuthority:
    """Owns prepared/active/expired/revoked state outside the SDK DB."""

    def __init__(
        self,
        database: ProductStateDatabase,
        *,
        policy_generation_provider: Callable[[], int] | None = None,
    ) -> None:
        self.database = database
        self._policy_generation_provider = policy_generation_provider

    def prepare(self, grant: TaskGrant, *, now: float) -> DurableTaskGrant:
        now = self._time(now)
        self._assert_current_policy(grant.policy_generation)
        payload = json.dumps(
            grant.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        try:
            self.database.connection.execute("BEGIN IMMEDIATE")
            row = self.database.connection.execute(
                "SELECT grant_fingerprint,grant_json,status FROM task_grants "
                "WHERE task_grant_id=?",
                (grant.task_grant_id,),
            ).fetchone()
            if row is None:
                self.database.connection.execute(
                    "INSERT INTO task_grants(task_grant_id,root_run_id,source,"
                    "policy_generation,version,grant_fingerprint,grant_json,status,"
                    "creator_principal_id,prepared_at,activated_at,expires_at,"
                    "created_at,revoked_at) VALUES(?,?,?,?,?,?,?,'prepared',?,?,NULL,?,?,NULL)",
                    (
                        grant.task_grant_id,
                        grant.root_run_id,
                        grant.source,
                        grant.policy_generation,
                        grant.version,
                        grant.fingerprint,
                        payload,
                        grant.principal_id,
                        now,
                        grant.expires_at,
                        now,
                    ),
                )
                status = "prepared"
            elif str(row["grant_fingerprint"]) != grant.fingerprint or str(
                row["grant_json"]
            ) != payload:
                raise TaskGrantConflict("TaskGrant id belongs to another immutable grant")
            else:
                status = str(row["status"])
            self.database.connection.commit()
        except BaseException:
            self.database.connection.rollback()
            raise
        return DurableTaskGrant(grant, status)

    def activate(
        self,
        task_grant_id: str,
        *,
        version: int,
        policy_generation: int,
        now: float,
    ) -> DurableTaskGrant:
        current = self._read(task_grant_id)
        self._identity(current.grant, version, policy_generation)
        self._assert_current_policy(policy_generation)
        now = self._time(now)
        if current.grant.expires_at is not None and current.grant.expires_at <= now:
            self.expire(task_grant_id, version=version, policy_generation=policy_generation, now=now)
            raise TaskGrantConflict("TaskGrant expired before activation")
        if current.status == "active":
            return current
        if current.status != "prepared":
            raise TaskGrantConflict(f"TaskGrant is {current.status}, not prepared")
        with self.database.connection:
            changed = self.database.connection.execute(
                "UPDATE task_grants SET status='active',activated_at=? WHERE task_grant_id=? "
                "AND version=? AND policy_generation=? AND status='prepared'",
                (now, task_grant_id, version, policy_generation),
            ).rowcount
            if changed != 1:
                raise TaskGrantConflict("TaskGrant activation CAS conflict")
        return self._read(task_grant_id)

    def assert_active(
        self,
        task_grant_id: str,
        *,
        version: int,
        policy_generation: int,
        now: float,
    ) -> TaskGrant:
        current = self._read(task_grant_id)
        self._identity(current.grant, version, policy_generation)
        self._assert_current_policy(policy_generation)
        now = self._time(now)
        if current.grant.expires_at is not None and current.grant.expires_at <= now:
            if current.status in {"prepared", "active"}:
                self.expire(task_grant_id, version=version, policy_generation=policy_generation, now=now)
            raise TaskGrantConflict("TaskGrant is expired")
        if current.status != "active":
            raise TaskGrantConflict(f"TaskGrant is {current.status}, not active")
        return current.grant

    def revoke(
        self, task_grant_id: str, *, version: int, policy_generation: int, now: float
    ) -> DurableTaskGrant:
        return self._finish(
            task_grant_id,
            version=version,
            policy_generation=policy_generation,
            target="revoked",
            now=now,
        )

    def expire(
        self, task_grant_id: str, *, version: int, policy_generation: int, now: float
    ) -> DurableTaskGrant:
        return self._finish(
            task_grant_id,
            version=version,
            policy_generation=policy_generation,
            target="expired",
            now=now,
        )

    def _finish(
        self,
        task_grant_id: str,
        *,
        version: int,
        policy_generation: int,
        target: str,
        now: float,
    ) -> DurableTaskGrant:
        current = self._read(task_grant_id)
        self._identity(current.grant, version, policy_generation)
        now = self._time(now)
        if current.status == target:
            return current
        if current.status not in {"prepared", "active"}:
            raise TaskGrantConflict(f"TaskGrant is already {current.status}")
        with self.database.connection:
            changed = self.database.connection.execute(
                "UPDATE task_grants SET status=?,revoked_at=? WHERE task_grant_id=? "
                "AND version=? AND policy_generation=? AND status=?",
                (target, now, task_grant_id, version, policy_generation, current.status),
            ).rowcount
            if changed != 1:
                raise TaskGrantConflict("TaskGrant terminal CAS conflict")
        return self._read(task_grant_id)

    def _read(self, task_grant_id: str) -> DurableTaskGrant:
        row = self.database.connection.execute(
            "SELECT grant_json,status FROM task_grants WHERE task_grant_id=?",
            (task_grant_id,),
        ).fetchone()
        if row is None:
            raise TaskGrantConflict("TaskGrant is missing")
        return DurableTaskGrant(
            TaskGrant.from_dict(json.loads(str(row["grant_json"]))), str(row["status"])
        )

    @staticmethod
    def _identity(grant: TaskGrant, version: int, policy_generation: int) -> None:
        if grant.version != version or grant.policy_generation != policy_generation:
            raise TaskGrantConflict("TaskGrant version or policy generation drifted")

    def _assert_current_policy(self, policy_generation: int) -> None:
        if self._policy_generation_provider is not None:
            current = int(self._policy_generation_provider())
            if current != policy_generation:
                raise TaskGrantConflict("TaskGrant policy generation is not current")
            return
        row = self.database.connection.execute(
            "SELECT generation FROM authorization_policy_state WHERE singleton_id=1"
        ).fetchone()
        if row is None or int(row[0]) != policy_generation:
            raise TaskGrantConflict("TaskGrant policy generation is not current")

    @staticmethod
    def _time(value: float) -> float:
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("now must be finite and non-negative")
        return value


__all__ = (
    "DurableTaskGrant",
    "DurableTaskGrantAuthority",
    "TaskGrantConflict",
)

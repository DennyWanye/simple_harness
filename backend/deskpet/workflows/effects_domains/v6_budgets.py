# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Deep-research v6 deadlines and resource budgets."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime

import aiosqlite

from ..contracts import JsonValue
from ..deadlines import DurableDeadlineV1
from ..store import RunFence
from .core import EffectJournalCore
from .support import EffectStateConflict


class V6DeadlineBudgetMixin(EffectJournalCore):
    """Deep-research v6 deadlines and resource budgets."""

    __slots__ = ()

    @staticmethod
    def _deadline_epoch(value: object) -> float:
        text = str(value)
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()

    @staticmethod
    def _v6_deadline_from_row(row: Mapping[str, object]) -> DurableDeadlineV1:
        def wall(value: object | None) -> str | None:
            if value is None:
                return None
            return datetime.fromtimestamp(float(value), UTC).isoformat()

        return DurableDeadlineV1.from_json(
            {
                "schema_version": int(row["schema_version"]),
                "deadline_id": str(row["deadline_id"]),
                "parent_deadline_id": row["parent_deadline_id"],
                "logical_scope": str(row["logical_scope"]),
                "policy_hash": str(row["policy_hash"]),
                "budget_ms": int(row["budget_ms"]),
                "remaining_ms": int(row["remaining_ms"]),
                "created_at": wall(row["created_at"]),
                "last_observed_at": wall(row["last_observed_at"]),
                "wall_not_after": wall(row["wall_not_after"]),
                "offline_policy": str(row["offline_policy"]),
                "rollback_tolerance_ms": int(row["rollback_tolerance_ms"]),
                "revision": int(row["revision"]),
                "status": str(row["status"]),
                "terminal_reason": row["terminal_reason"],
                "terminal_at": wall(row["terminal_at"]),
            }
        )

    async def _persist_v6_deadline_transition(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        previous_revision: int,
        state: DurableDeadlineV1,
    ) -> None:
        cursor = await db.execute(
            """UPDATE workflow_research_deadlines SET
            remaining_ms=?,last_observed_at=?,revision=?,status=?,
            terminal_reason=?,terminal_at=?
            WHERE deadline_id=? AND run_id=? AND revision=? AND status='open'""",
            (
                state.remaining_ms,
                self._deadline_epoch(state.last_observed_at),
                state.revision,
                state.status,
                state.terminal_reason,
                (
                    self._deadline_epoch(state.terminal_at)
                    if state.terminal_at is not None
                    else None
                ),
                state.deadline_id,
                run_id,
                previous_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise EffectStateConflict("v6 read deadline observation CAS lost")

    async def persist_v6_deadline(
        self,
        fence: RunFence,
        state: Mapping[str, JsonValue],
        *,
        expected_revision: int | None = None,
    ) -> None:
        """Insert or CAS-update one contract-owned v6 deadline row."""

        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_research_deadlines WHERE deadline_id=?",
                    (str(state["deadline_id"]),),
                )
            ).fetchone()
            values = (
                int(state["schema_version"]),
                fence.run_id,
                state.get("parent_deadline_id"),
                str(state["logical_scope"]),
                str(state["policy_hash"]),
                int(state["budget_ms"]),
                int(state["remaining_ms"]),
                self._deadline_epoch(state["created_at"]),
                self._deadline_epoch(state["last_observed_at"]),
                self._deadline_epoch(state["wall_not_after"]),
                str(state["offline_policy"]),
                int(state["rollback_tolerance_ms"]),
                int(state["revision"]),
                str(state["status"]),
                state.get("terminal_reason"),
                (
                    self._deadline_epoch(state["terminal_at"])
                    if state.get("terminal_at") is not None
                    else None
                ),
            )
            if existing is None:
                if expected_revision is not None:
                    raise EffectStateConflict("v6 deadline CAS target is missing")
                await db.execute(
                    """INSERT INTO workflow_research_deadlines(
                    deadline_id,schema_version,run_id,parent_deadline_id,logical_scope,
                    policy_hash,budget_ms,remaining_ms,created_at,last_observed_at,
                    wall_not_after,offline_policy,rollback_tolerance_ms,revision,status,
                    terminal_reason,terminal_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (str(state["deadline_id"]), *values),
                )
            else:
                if str(existing["run_id"]) != fence.run_id:
                    raise EffectStateConflict("v6 deadline belongs to another run")
                if expected_revision is None:
                    immutable = (
                        str(existing["logical_scope"]),
                        str(existing["policy_hash"]),
                        int(existing["budget_ms"]),
                        existing["parent_deadline_id"],
                    )
                    requested = (
                        str(state["logical_scope"]),
                        str(state["policy_hash"]),
                        int(state["budget_ms"]),
                        state.get("parent_deadline_id"),
                    )
                    if immutable != requested:
                        raise EffectStateConflict("v6 deadline immutable identity differs")
                else:
                    cursor = await db.execute(
                        """UPDATE workflow_research_deadlines SET
                        remaining_ms=?,last_observed_at=?,revision=?,status=?,
                        terminal_reason=?,terminal_at=?
                        WHERE deadline_id=? AND run_id=? AND revision=?""",
                        (
                            int(state["remaining_ms"]),
                            self._deadline_epoch(state["last_observed_at"]),
                            int(state["revision"]),
                            str(state["status"]),
                            state.get("terminal_reason"),
                            (
                                self._deadline_epoch(state["terminal_at"])
                                if state.get("terminal_at") is not None
                                else None
                            ),
                            str(state["deadline_id"]),
                            fence.run_id,
                            expected_revision,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise EffectStateConflict("v6 deadline revision changed")
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def load_v6_deadline(self, run_id: str, deadline_id: str) -> dict[str, JsonValue] | None:
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_research_deadlines WHERE run_id=? AND deadline_id=?",
                    (run_id, deadline_id),
                )
            ).fetchone()
            if row is None:
                return None

            def wall(value: object | None) -> str | None:
                if value is None:
                    return None
                return datetime.fromtimestamp(float(value), UTC).isoformat()

            return {
                "schema_version": int(row["schema_version"]),
                "deadline_id": str(row["deadline_id"]),
                "parent_deadline_id": row["parent_deadline_id"],
                "logical_scope": str(row["logical_scope"]),
                "policy_hash": str(row["policy_hash"]),
                "budget_ms": int(row["budget_ms"]),
                "remaining_ms": int(row["remaining_ms"]),
                "created_at": wall(row["created_at"]),
                "last_observed_at": wall(row["last_observed_at"]),
                "wall_not_after": wall(row["wall_not_after"]),
                "offline_policy": str(row["offline_policy"]),
                "rollback_tolerance_ms": int(row["rollback_tolerance_ms"]),
                "revision": int(row["revision"]),
                "status": str(row["status"]),
                "terminal_reason": row["terminal_reason"],
                "terminal_at": wall(row["terminal_at"]),
            }
        finally:
            await db.close()

    async def ensure_v6_resource_budgets(
        self,
        fence: RunFence,
        *,
        policy_hash: str,
        budgets: Mapping[str, int],
    ) -> None:
        expected = {"query", "fetch", "browser", "llm", "lane"}
        if set(budgets) != expected:
            raise ValueError("v6 route budgets must cover query/fetch/browser/llm/lane")
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            for kind in sorted(expected):
                limit = int(budgets[kind])
                if limit < 0:
                    raise ValueError("v6 route budget limits must be non-negative")
                budget_id = hashlib.sha256(
                    f"{fence.run_id}|{policy_hash}|{kind}".encode()
                ).hexdigest()
                row = await (
                    await db.execute(
                        """SELECT * FROM workflow_research_resource_budgets
                        WHERE run_id=? AND resource_kind=?""",
                        (fence.run_id, kind),
                    )
                ).fetchone()
                if row is None:
                    await db.execute(
                        """INSERT INTO workflow_research_resource_budgets(
                        budget_id,run_id,policy_hash,resource_kind,hard_limit)
                        VALUES(?,?,?,?,?)""",
                        (budget_id, fence.run_id, policy_hash, kind, limit),
                    )
                elif str(row["policy_hash"]) != policy_hash or int(row["hard_limit"]) != limit:
                    raise EffectStateConflict("persisted v6 route budget differs")
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

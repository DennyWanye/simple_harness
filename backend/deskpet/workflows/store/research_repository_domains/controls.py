"""Research run controls: open, transition, cancel settlement and expiry."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any

import aiosqlite

from .core import ResearchRepositoryCore
from .support import ResearchRepositoryError, _canonical

_CONTROL_ACTIONS = {"generate_now", "continue_research", "retry_from_start", "cancel_settle"}


def _command(row: aiosqlite.Row) -> dict[str, Any]:
    result = dict(row)
    result["payload"] = json.loads(str(result.pop("payload_json")))
    return result


class ResearchControlMixin(ResearchRepositoryCore):
    """Research run controls: open, transition, cancel settlement and expiry."""

    __slots__ = ()

    async def open_control(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        action: str,
        expected_run_version: int,
        expected_head_checkpoint_ns: str,
        expected_head_checkpoint_id: str | None,
        payload: Mapping[str, Any] | None = None,
        command_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Open one idempotent command while recording its later CAS inputs."""

        if action not in _CONTROL_ACTIONS:
            raise ResearchRepositoryError(
                "unsupported_control_action", f"unsupported action: {action}"
            )
        if not run_id or not idempotency_key or expected_run_version < 0:
            raise ResearchRepositoryError("invalid_control_command", "invalid control identity")
        now = float(self._clock())
        command_id = (
            command_id or hashlib.sha256(f"{run_id}|{idempotency_key}".encode()).hexdigest()
        )
        envelope = copy.deepcopy(dict(payload or {}))
        envelope["_repository"] = {
            "expected_run_version": int(expected_run_version),
            "expected_head_checkpoint_ns": str(expected_head_checkpoint_ns),
            "expected_head_checkpoint_id": expected_head_checkpoint_id,
        }
        encoded = _canonical(envelope)
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_run_control_commands WHERE run_id=? AND idempotency_key=?",
                    (run_id, idempotency_key),
                )
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["action"]) != action
                    or str(existing["head_checkpoint_ns"]) != expected_head_checkpoint_ns
                    or existing["head_checkpoint_id"] != expected_head_checkpoint_id
                    or str(existing["payload_json"]) != encoded
                ):
                    raise ResearchRepositoryError(
                        "control_idempotency_conflict",
                        "idempotency key is already bound to a different command",
                    )
                await db.commit()
                return _command(existing), False
            active = await (
                await db.execute(
                    """SELECT command_id FROM workflow_run_control_commands
                    WHERE run_id=? AND status IN ('open','accepted','observed','settled') LIMIT 1""",
                    (run_id,),
                )
            ).fetchone()
            if active is not None:
                raise ResearchRepositoryError(
                    "active_control_conflict", "run already has an active control command"
                )
            run = await (
                await db.execute("SELECT run_id FROM workflow_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if run is None:
                raise ResearchRepositoryError("run_not_found", "control run was not found")
            await db.execute(
                """INSERT INTO workflow_run_control_commands(
                    command_id,run_id,idempotency_key,action,status,head_checkpoint_ns,
                    head_checkpoint_id,payload_json,created_at,updated_at
                ) VALUES(?,?,?,?,'open',?,?,?,?,?)""",
                (
                    command_id,
                    run_id,
                    idempotency_key,
                    action,
                    expected_head_checkpoint_ns,
                    expected_head_checkpoint_id,
                    encoded,
                    now,
                    now,
                ),
            )
            await db.commit()
            row = await self.get_control(command_id)
            assert row is not None
            return row, True
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def get_control(self, command_id: str) -> dict[str, Any] | None:
        await self.initialize()
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_run_control_commands WHERE command_id=?", (command_id,)
                )
            ).fetchone()
            return _command(row) if row is not None else None
        finally:
            await db.close()

    @staticmethod
    async def _is_owned_ancestor(
        db: aiosqlite.Connection,
        *,
        run_id: str,
        head_ns: str,
        head_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> bool:
        if checkpoint_ns != head_ns:
            return False
        row = await (
            await db.execute(
                """WITH RECURSIVE ancestors(checkpoint_id,parent_checkpoint_id) AS (
                    SELECT checkpoint_id,parent_checkpoint_id FROM workflow_checkpoints
                    WHERE run_id=? AND checkpoint_ns=? AND checkpoint_id=?
                    UNION ALL
                    SELECT parent.checkpoint_id,parent.parent_checkpoint_id
                    FROM workflow_checkpoints parent JOIN ancestors child
                      ON parent.checkpoint_id=child.parent_checkpoint_id
                    WHERE parent.run_id=? AND parent.checkpoint_ns=?
                )
                SELECT 1 FROM ancestors a JOIN workflow_checkpoint_owners o
                  ON o.run_id=? AND o.checkpoint_ns=? AND o.checkpoint_id=a.checkpoint_id
                WHERE a.checkpoint_id=? LIMIT 1""",
                (
                    run_id,
                    head_ns,
                    head_id,
                    run_id,
                    head_ns,
                    run_id,
                    checkpoint_ns,
                    checkpoint_id,
                ),
            )
        ).fetchone()
        return row is not None

    async def accept_generate_now(
        self,
        command_id: str,
        *,
        brief_checkpoint_ns: str,
        brief_checkpoint_id: str,
        settle_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Accept generate-now only after durable run/head/brief CAS checks."""

        if settle_seconds != 30.0:
            raise ResearchRepositoryError(
                "invalid_settle_window", "generate-now settle window is fixed at 30s"
            )
        now = float(self._clock())
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_run_control_commands WHERE command_id=?", (command_id,)
                )
            ).fetchone()
            if row is None:
                raise ResearchRepositoryError("control_not_found", "control command was not found")
            if row["action"] != "generate_now":
                raise ResearchRepositoryError("wrong_control_action", "command is not generate_now")
            envelope = json.loads(str(row["payload_json"]))
            meta = envelope.get("_repository") if isinstance(envelope, dict) else None
            if not isinstance(meta, dict):
                raise ResearchRepositoryError(
                    "invalid_control_metadata", "command CAS metadata is missing"
                )
            if row["status"] == "accepted":
                if (
                    meta.get("brief_checkpoint_ns") != brief_checkpoint_ns
                    or meta.get("brief_checkpoint_id") != brief_checkpoint_id
                ):
                    raise ResearchRepositoryError(
                        "control_replay_conflict",
                        "accepted command is bound to another brief checkpoint",
                    )
                await db.commit()
                return _command(row)
            if row["status"] != "open":
                raise ResearchRepositoryError(
                    "invalid_control_transition", "only open commands can be accepted"
                )
            run = await (
                await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (row["run_id"],))
            ).fetchone()
            workflow_version = str(run["workflow_version"]) if run is not None else ""
            accepted = bool(
                run is not None
                and run["workflow_name"] == "deep_research"
                and workflow_version in {"v5", "v6"}
                and run["status"] == "running"
                and int(run["run_version"]) == int(meta.get("expected_run_version", -1))
                and str(run["head_checkpoint_ns"])
                == str(meta.get("expected_head_checkpoint_ns", ""))
                and run["head_checkpoint_id"] == meta.get("expected_head_checkpoint_id")
                and run["head_checkpoint_ns"] == row["head_checkpoint_ns"]
                and run["head_checkpoint_id"] == row["head_checkpoint_id"]
            )
            if accepted and run["head_checkpoint_id"] is not None:
                accepted = await self._is_owned_ancestor(
                    db,
                    run_id=str(run["run_id"]),
                    head_ns=str(run["head_checkpoint_ns"]),
                    head_id=str(run["head_checkpoint_id"]),
                    checkpoint_ns=brief_checkpoint_ns,
                    checkpoint_id=brief_checkpoint_id,
                )
            wall_not_after: float | None = None
            automatic_deadline_missing = False
            if accepted and workflow_version == "v6":
                deadline = await (
                    await db.execute(
                        """SELECT wall_not_after,status FROM workflow_research_deadlines
                        WHERE run_id=? AND logical_scope='run:automatic'
                          AND parent_deadline_id IS NULL""",
                        (row["run_id"],),
                    )
                ).fetchone()
                if deadline is None:
                    automatic_deadline_missing = True
                    accepted = False
                else:
                    wall_not_after = float(deadline["wall_not_after"])
                    if deadline["status"] != "open" or now >= wall_not_after:
                        accepted = False
            if not accepted:
                envelope["_result"] = {
                    "reason": (
                        "automatic_deadline_missing"
                        if automatic_deadline_missing
                        else (
                            "wall_guard"
                            if workflow_version == "v6"
                            and wall_not_after is not None
                            and now >= wall_not_after
                            else "run_head_terminal_or_brief_cas_failed"
                        )
                    )
                }
                await db.execute(
                    "UPDATE workflow_run_control_commands SET status='rejected',payload_json=?,updated_at=? "
                    "WHERE command_id=? AND status='open'",
                    (_canonical(envelope), now, command_id),
                )
            else:
                envelope["_repository"]["brief_checkpoint_ns"] = brief_checkpoint_ns
                envelope["_repository"]["brief_checkpoint_id"] = brief_checkpoint_id
                await db.execute(
                    """UPDATE workflow_run_control_commands SET status='accepted',payload_json=?,
                    accepted_at=?,settle_deadline=?,updated_at=?
                    WHERE command_id=? AND status='open'""",
                    (
                        _canonical(envelope),
                        now,
                        min(now + 30.0, wall_not_after)
                        if wall_not_after is not None
                        else now + 30.0,
                        now,
                        command_id,
                    ),
                )
            updated = await (
                await db.execute(
                    "SELECT * FROM workflow_run_control_commands WHERE command_id=?", (command_id,)
                )
            ).fetchone()
            await db.commit()
            assert updated is not None
            return _command(updated)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def transition_control(
        self,
        command_id: str,
        *,
        expected_status: str,
        new_status: str,
        checkpoint_ns: str | None = None,
        checkpoint_id: str | None = None,
        result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed = {
            ("open", "rejected"),
            ("open", "expired"),
            ("accepted", "observed"),
            ("observed", "settled"),
            ("settled", "consumed"),
        }
        if (expected_status, new_status) not in allowed:
            raise ResearchRepositoryError(
                "invalid_control_transition", "control transition is not allowed"
            )
        if new_status in {"observed", "settled", "consumed"} and not checkpoint_id:
            raise ResearchRepositoryError(
                "checkpoint_required", "observed control states require a checkpoint"
            )
        now = float(self._clock())
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_run_control_commands WHERE command_id=?", (command_id,)
                )
            ).fetchone()
            if row is None:
                raise ResearchRepositoryError("control_not_found", "control command was not found")
            if row["status"] == new_status:
                if checkpoint_ns is not None and row["head_checkpoint_ns"] != checkpoint_ns:
                    raise ResearchRepositoryError(
                        "control_replay_conflict", "checkpoint namespace changed"
                    )
                if checkpoint_id is not None and row["head_checkpoint_id"] != checkpoint_id:
                    raise ResearchRepositoryError(
                        "control_replay_conflict", "checkpoint identity changed"
                    )
                if result is not None:
                    replay_payload = json.loads(str(row["payload_json"]))
                    if replay_payload.get("_result") != dict(result):
                        raise ResearchRepositoryError(
                            "control_replay_conflict", "control result changed"
                        )
                await db.commit()
                return _command(row)
            if row["status"] != expected_status:
                raise ResearchRepositoryError("invalid_control_transition", "control state changed")
            envelope = json.loads(str(row["payload_json"]))
            if result is not None:
                envelope["_result"] = copy.deepcopy(dict(result))
            ns = str(row["head_checkpoint_ns"]) if checkpoint_ns is None else checkpoint_ns
            cp = row["head_checkpoint_id"] if checkpoint_id is None else checkpoint_id
            run = await (
                await db.execute(
                    "SELECT status,head_checkpoint_ns,head_checkpoint_id FROM workflow_runs WHERE run_id=?",
                    (row["run_id"],),
                )
            ).fetchone()
            owner = await (
                await db.execute(
                    """SELECT 1 FROM workflow_checkpoint_owners
                    WHERE run_id=? AND checkpoint_ns=? AND checkpoint_id=?""",
                    (row["run_id"], ns, cp),
                )
            ).fetchone()
            if (
                run is None
                or owner is None
                or run["head_checkpoint_ns"] != ns
                or run["head_checkpoint_id"] != cp
                or (new_status in {"observed", "settled"} and run["status"] != "running")
            ):
                raise ResearchRepositoryError(
                    "control_checkpoint_cas_failed",
                    "control checkpoint is not the durable run head",
                )
            columns = {
                "observed": "observed_at",
                "settled": "settled_at",
                "consumed": "consumed_at",
            }
            timestamp_column = columns.get(new_status)
            timestamp_sql = f",{timestamp_column}=?" if timestamp_column else ""
            params: list[Any] = [new_status, ns, cp, _canonical(envelope), now]
            if timestamp_column:
                params.append(now)
            params.extend([command_id, expected_status])
            cursor = await db.execute(
                f"""UPDATE workflow_run_control_commands SET status=?,head_checkpoint_ns=?,
                head_checkpoint_id=?,payload_json=?,updated_at=?{timestamp_sql}
                WHERE command_id=? AND status=?""",
                tuple(params),
            )
            if cursor.rowcount != 1:
                raise ResearchRepositoryError(
                    "control_cas_failed", "control transition lost its CAS"
                )
            updated = await (
                await db.execute(
                    "SELECT * FROM workflow_run_control_commands WHERE command_id=?", (command_id,)
                )
            ).fetchone()
            await db.commit()
            assert updated is not None
            return _command(updated)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def active_control(self, run_id: str) -> dict[str, Any] | None:
        await self.initialize()
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    """SELECT * FROM workflow_run_control_commands
                    WHERE run_id=? AND status IN ('open','accepted','observed','settled')
                    ORDER BY created_at,command_id""",
                    (run_id,),
                )
            ).fetchall()
            if len(rows) > 1:
                raise ResearchRepositoryError(
                    "multiple_active_controls", "run has multiple active controls"
                )
            return _command(rows[0]) if rows else None
        finally:
            await db.close()

    async def request_cancel_settle(
        self,
        run_id: str,
        *,
        idempotency_key: str,
        expected_run_version: int,
    ) -> dict[str, Any]:
        """Arm cancel-settle on the existing generate-now fence.

        A second command row would make node polling ambiguous.  The bounded
        marker therefore lives on the single accepted/observed command, while
        this method returns an action projection suitable for the control hub.
        """

        if not idempotency_key or expected_run_version < 0:
            raise ResearchRepositoryError("invalid_cancel_settle", "invalid cancel-settle identity")
        now = float(self._clock())
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    """SELECT * FROM workflow_run_control_commands
                    WHERE run_id=? AND action='generate_now'
                      AND status IN ('accepted','observed')
                    ORDER BY created_at,command_id""",
                    (run_id,),
                )
            ).fetchall()
            if len(row) != 1:
                raise ResearchRepositoryError(
                    "cancel_settle_not_available", "run has no unique accepted generate-now fence"
                )
            command_row = row[0]
            run = await (
                await db.execute(
                    """SELECT workflow_name,workflow_version,status,run_version,
                    head_checkpoint_ns,head_checkpoint_id FROM workflow_runs WHERE run_id=?""",
                    (run_id,),
                )
            ).fetchone()
            if (
                run is None
                or run["workflow_name"] != "deep_research"
                or run["workflow_version"] not in {"v5", "v6"}
                or run["status"] != "running"
                or int(run["run_version"]) != expected_run_version
                or run["head_checkpoint_ns"] != command_row["head_checkpoint_ns"]
                or run["head_checkpoint_id"] != command_row["head_checkpoint_id"]
            ):
                raise ResearchRepositoryError(
                    "cancel_settle_cas_failed", "cancel-settle lost the run/head/version CAS"
                )
            if run["workflow_version"] == "v6":
                deadline = await (
                    await db.execute(
                        """SELECT wall_not_after,status FROM workflow_research_deadlines
                        WHERE run_id=? AND logical_scope='run:automatic'
                          AND parent_deadline_id IS NULL""",
                        (run_id,),
                    )
                ).fetchone()
                if (
                    deadline is None
                    or deadline["status"] != "open"
                    or now >= float(deadline["wall_not_after"])
                ):
                    raise ResearchRepositoryError(
                        "cancel_settle_wall_guard", "v6 automatic wall guard has expired"
                    )
            envelope = json.loads(str(command_row["payload_json"]))
            marker = envelope.get("_cancel_settle")
            created = marker is None
            if marker is not None:
                if not isinstance(marker, dict) or marker.get("idempotency_key") != idempotency_key:
                    raise ResearchRepositoryError(
                        "cancel_settle_idempotency_conflict",
                        "generate-now fence is already bound to another cancel-settle request",
                    )
            else:
                marker = {
                    "idempotency_key": idempotency_key,
                    "requested_at": now,
                    "expected_run_version": expected_run_version,
                    "head_checkpoint_ns": str(run["head_checkpoint_ns"]),
                    "head_checkpoint_id": run["head_checkpoint_id"],
                }
                envelope["_cancel_settle"] = marker
                cursor = await db.execute(
                    """UPDATE workflow_run_control_commands SET payload_json=?,updated_at=?
                    WHERE command_id=? AND status IN ('accepted','observed')""",
                    (_canonical(envelope), now, command_row["command_id"]),
                )
                if cursor.rowcount != 1:
                    raise ResearchRepositoryError(
                        "cancel_settle_cas_failed", "generate-now fence changed"
                    )
                command_row = await (
                    await db.execute(
                        "SELECT * FROM workflow_run_control_commands WHERE command_id=?",
                        (command_row["command_id"],),
                    )
                ).fetchone()
                assert command_row is not None
            await db.commit()
            projection = _command(command_row)
            projection["source_action"] = "generate_now"
            projection["action"] = "cancel_settle"
            projection["idempotency_key"] = idempotency_key
            projection["cancel_settle"] = copy.deepcopy(marker)
            projection["created"] = created
            return projection
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def expired_control_candidates(
        self, *, now: float | None = None
    ) -> dict[str, tuple[str, ...]]:
        """Return watchdog and cleanup candidates without mutating retention state."""

        instant = float(self._clock() if now is None else now)
        await self.initialize()
        db = await self._connect()
        try:
            settle = await (
                await db.execute(
                    """SELECT command_id FROM workflow_run_control_commands
                    WHERE status IN ('accepted','observed') AND settle_deadline IS NOT NULL
                      AND settle_deadline<=? ORDER BY command_id""",
                    (instant,),
                )
            ).fetchall()
            cleanup = await (
                await db.execute(
                    """SELECT command_id FROM workflow_run_control_commands
                    WHERE status IN ('consumed','rejected','expired') AND updated_at<=?
                    ORDER BY command_id""",
                    (instant,),
                )
            ).fetchall()
            return {
                "settle_deadline_exceeded": tuple(str(row[0]) for row in settle),
                "cleanup_eligible": tuple(str(row[0]) for row in cleanup),
            }
        finally:
            await db.close()

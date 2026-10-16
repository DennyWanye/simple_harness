"""Child commands, child links and child signals."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

import aiosqlite

from deskpet.execution.contracts import (
    TERMINAL_RUN_STATUSES,
    AttachmentPolicy,
    ChildCommandIntent,
    ChildCommandRecord,
    ChildCommandStatus,
    ChildSignalRecord,
    DecisionConflict,
    DecisionRecord,
    DeliverySpec,
    FinalizeRunResult,
    IdempotencyConflict,
    LinkKind,
    RecoveryLease,
    RunEvent,
    RunEventCandidate,
    RunIdentityConflict,
    RunNotFound,
    RunRecord,
    RunStartSnapshotRecord,
    RunStatus,
    StaleRecoveryLease,
    VersionConflict,
    canonical_json,
    fingerprint_json,
    thaw_json,
)

from .core import ExecutionUowCore
from .support import ContinuationRecord, RuntimeActivationError


class ChildCommandMixin(ExecutionUowCore):
    """Child commands, child links and child signals."""

    __slots__ = ()

    async def finalize_child_and_enqueue_parent_signal(
        self,
        operation_id: str,
        *,
        expected_version: int,
        terminal_status: RunStatus,
        event: RunEventCandidate,
        value: Any = None,
        deliveries: Sequence[DeliverySpec] = (),
        recovery_lease: RecoveryLease | None = None,
        terminal_commit_extensions: Sequence[Any] = (),
    ) -> FinalizeRunResult:
        """Finalize the acknowledged child and enqueue its parent signal atomically."""

        command = await self.get_child_command(operation_id)
        if command is None:
            raise RunNotFound("child_command_not_found", "child command does not exist")
        result = await self.commit_run_outcome(
            command.child_run_id,
            expected_version=expected_version,
            terminal_status=terminal_status,
            event=event,
            deliveries=deliveries,
            parent_signal_operation_id=operation_id,
            parent_signal_value=value,
            recovery_lease=recovery_lease,
            terminal_commit_extensions=terminal_commit_extensions,
        )
        assert isinstance(result, FinalizeRunResult)
        return result

    async def _commit_child_command_tx(
        self,
        db: aiosqlite.Connection,
        intent: ChildCommandIntent,
        *,
        recovery_owner: bool = False,
        transaction_hook: Any | None = None,
    ) -> aiosqlite.Row:
        parent = await (
            await db.execute(
                "SELECT * FROM execution_runs WHERE run_id=?",
                (intent.parent_run_id,),
            )
        ).fetchone()
        if parent is None:
            raise RunNotFound("parent_not_found", "delegate parent must already be durable")
        existing = await (
            await db.execute(
                """SELECT * FROM execution_child_commands
                WHERE operation_id=? OR (parent_run_id=? AND command_id=?)
                OR child_run_id=? ORDER BY operation_id=? DESC LIMIT 1""",
                (
                    intent.operation_id,
                    intent.parent_run_id,
                    intent.command_id,
                    intent.child_run_id,
                    intent.operation_id,
                ),
            )
        ).fetchone()
        owner = (
            await self._required_recovery_owner_tx(db)
            if existing is not None or recovery_owner
            else await self._required_start_owner_tx(db)
        )
        self._assert_run_owner(parent, owner)
        if existing is not None:
            if (
                str(existing["operation_id"]) != intent.operation_id
                or str(existing["intent_fingerprint"]) != intent.intent_fingerprint
            ):
                raise IdempotencyConflict(
                    "child_operation_conflict",
                    "operation, parent command, or child id names another intent",
                )
            row = existing
        else:
            now = float(self._clock())
            await db.execute(
                """INSERT INTO execution_child_commands(
                operation_id,schema_version,parent_run_id,command_id,child_run_id,
                profile_key,join_policy,capability_snapshot_ref,
                capability_subset_json,child_request_json,child_spec_json,
                intent_fingerprint,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'pending',?,?)""",
                (
                    intent.operation_id,
                    intent.schema_version,
                    intent.parent_run_id,
                    intent.command_id,
                    intent.child_run_id,
                    intent.child_spec.profile_key,
                    intent.attachment_policy.value,
                    intent.capability_snapshot_ref,
                    canonical_json(list(intent.capability_subset)),
                    canonical_json(thaw_json(intent.child_request)),
                    canonical_json(intent.child_spec.to_dict()),
                    intent.intent_fingerprint,
                    now,
                    now,
                ),
            )
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (intent.operation_id,),
                )
            ).fetchone()
            assert row is not None
        if transaction_hook is not None:
            hooked = transaction_hook(db, intent)
            if inspect.isawaitable(hooked):
                await hooked
        return row

    async def commit_child_command(
        self,
        intent: ChildCommandIntent,
        *,
        start_snapshot: RunStartSnapshotRecord | None = None,
        start_commit_extensions: Sequence[Any] = (),
        recovery_lease: RecoveryLease | None = None,
        transaction_hook: Any | None = None,
    ) -> ChildCommandRecord:
        """Commit the complete delegate intent before a child row can exist."""

        if start_snapshot is not None and start_snapshot.run_id != intent.child_run_id:
            raise IdempotencyConflict(
                "child_start_scope_conflict",
                "child start snapshot is bound to another Run",
            )
        async with self._write_transaction() as db:
            await self._assert_optional_recovery_fence_tx(db, recovery_lease, intent.parent_run_id)
            expected_owner = None
            if start_snapshot is not None:
                expected_owner = (
                    await self._required_recovery_owner_tx(db)
                    if recovery_lease is not None
                    else await self._required_start_owner_tx(db)
                )
            row = await self._commit_child_command_tx(
                db,
                intent,
                recovery_owner=recovery_lease is not None,
                transaction_hook=(transaction_hook if start_snapshot is None else None),
            )
            if start_snapshot is not None:
                _, child_created = await self._insert_run_for_owner_tx(
                    db,
                    intent.child_spec,
                    version=0,
                    expected_owner=expected_owner,
                )
                await self._ensure_start_snapshot_tx(
                    db,
                    spec=intent.child_spec,
                    start_snapshot=start_snapshot,
                    run_created=child_created,
                    start_commit_extensions=start_commit_extensions,
                )
                await self._ensure_child_run_link_tx(db, intent, expected_owner)
                self._fault("child_precreate_after_start_snapshot")
                if transaction_hook is not None:
                    hooked = transaction_hook(db, intent)
                    if inspect.isawaitable(hooked):
                        await hooked
                self._fault("child_precreate_before_commit")
            self._fault("child_command_before_commit")
            return self._row_to_child_command(row)

    async def commit_child_command_and_precreate_child(
        self,
        intent: ChildCommandIntent,
        start_snapshot: RunStartSnapshotRecord,
        *,
        start_commit_extensions: Sequence[Any] = (),
        recovery_lease: RecoveryLease | None = None,
        transaction_hook: Any | None = None,
    ) -> ChildCommandRecord:
        """Atomically freeze a child command, Run, start snapshot and lease clone."""

        return await self.commit_child_command(
            intent,
            start_snapshot=start_snapshot,
            start_commit_extensions=start_commit_extensions,
            recovery_lease=recovery_lease,
            transaction_hook=transaction_hook,
        )

    async def get_child_command(self, operation_id: str) -> ChildCommandRecord | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            return self._row_to_child_command(row) if row is not None else None

    async def get_child_command_for_run(self, child_run_id: str) -> ChildCommandRecord | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE child_run_id=?",
                    (child_run_id,),
                )
            ).fetchone()
            return self._row_to_child_command(row) if row is not None else None

    async def lease_child_commands(
        self,
        *,
        owner: str,
        limit: int,
        lease_seconds: float,
        parent_run_id: str | None = None,
        recovery_lease: RecoveryLease | None = None,
    ) -> tuple[ChildCommandRecord, ...]:
        if not owner or limit <= 0 or lease_seconds <= 0:
            raise ValueError("owner, positive limit and positive lease_seconds are required")
        async with self._write_transaction() as db:
            owner_kind, owner_generation = await self._required_recovery_owner_tx(db)
            if recovery_lease is not None:
                if parent_run_id != recovery_lease.run_id:
                    raise StaleRecoveryLease("recovery child scan must be scoped to its parent run")
                await self._assert_recovery_fence_tx(db, recovery_lease, run_id=parent_run_id)
            now = float(self._clock())
            rows = await (
                await db.execute(
                    """SELECT operation_id FROM execution_child_commands
                    WHERE (
                        status='pending'
                        OR (
                            status IN ('leased','scheduled')
                            AND schedule_lease_expires_at<=?
                        )
                    ) AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                    AND (? IS NULL OR parent_run_id=?)
                    AND EXISTS(
                        SELECT 1 FROM execution_runs AS parent
                        WHERE parent.run_id=execution_child_commands.parent_run_id
                        AND parent.owner_kind=? AND parent.owner_generation=?
                    )
                    ORDER BY created_at,operation_id LIMIT ?""",
                    (
                        now,
                        now,
                        parent_run_id,
                        parent_run_id,
                        owner_kind,
                        owner_generation,
                        limit,
                    ),
                )
            ).fetchall()
            leased: list[ChildCommandRecord] = []
            for selected in rows:
                operation_id = str(selected["operation_id"])
                cursor = await db.execute(
                    """UPDATE execution_child_commands
                    SET status='leased',schedule_lease_owner=?,
                        schedule_lease_epoch=schedule_lease_epoch+1,
                        schedule_lease_expires_at=?,attempts=attempts+1,updated_at=?
                    WHERE operation_id=? AND (
                        status='pending'
                        OR (status IN ('leased','scheduled') AND schedule_lease_expires_at<=?)
                    )""",
                    (owner, now + lease_seconds, now, operation_id, now),
                )
                if cursor.rowcount != 1:
                    continue
                row = await (
                    await db.execute(
                        "SELECT * FROM execution_child_commands WHERE operation_id=?",
                        (operation_id,),
                    )
                ).fetchone()
                assert row is not None
                leased.append(self._row_to_child_command(row))
            self._fault("child_lease_before_commit")
            await db.commit()
            return tuple(leased)

    @staticmethod
    def _stable_child_link_id(operation_id: str) -> str:
        return hashlib.sha256(f"execution-child-link|{operation_id}".encode()).hexdigest()

    @staticmethod
    def _stable_child_signal_id(operation_id: str, kind: str) -> str:
        return hashlib.sha256(f"execution-child-signal|{operation_id}|{kind}".encode()).hexdigest()

    async def _ensure_child_run_link_tx(
        self,
        db: aiosqlite.Connection,
        intent: ChildCommandIntent,
        expected_owner: tuple[str, int],
    ) -> None:
        child, child_created = await self._insert_run_for_owner_tx(
            db, intent.child_spec, version=0, expected_owner=expected_owner
        )
        link_id = self._stable_child_link_id(intent.operation_id)
        existing = await (
            await db.execute("SELECT * FROM execution_run_links WHERE link_id=?", (link_id,))
        ).fetchone()
        expected = {
            "root_run_id": intent.child_spec.context.root_run_id,
            "parent_run_id": intent.parent_run_id,
            "child_run_id": intent.child_run_id,
            "attachment_policy": intent.attachment_policy.value,
            "link_kind": LinkKind.STRUCTURAL.value,
            "domain_kind": "",
            "domain_id": "",
        }
        if existing is None:
            await db.execute(
                """INSERT INTO execution_run_links(
                link_id,schema_version,root_run_id,parent_run_id,child_run_id,
                attachment_policy,link_kind,domain_kind,domain_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    link_id,
                    1,
                    expected["root_run_id"],
                    expected["parent_run_id"],
                    expected["child_run_id"],
                    expected["attachment_policy"],
                    expected["link_kind"],
                    "",
                    "",
                    self._clock(),
                ),
            )
            cursor = await db.execute(
                """UPDATE execution_runs SET version=version+1,updated_at=?
                WHERE run_id=? AND version=? AND terminal_event_id IS NULL""",
                (self._clock(), intent.child_run_id, int(child["version"])),
            )
            if cursor.rowcount != 1:
                raise VersionConflict("stale_run_version", "child changed before link commit")
        elif any(str(existing[name]) != value for name, value in expected.items()):
            raise IdempotencyConflict(
                "child_link_conflict", "child link differs from committed command"
            )
        elif child_created:
            raise IdempotencyConflict(
                "child_link_without_run", "child link existed before its child run"
            )

    async def schedule_child_command(
        self,
        operation_id: str,
        *,
        lease_owner: str,
        lease_epoch: int,
        recovery_lease: RecoveryLease | None = None,
    ) -> ChildCommandRecord:
        """Atomically create/link the child and mark its command scheduled."""

        async with self._write_transaction() as db:
            command = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            if command is None:
                raise RunNotFound("child_command_not_found", "child command does not exist")
            parent_run_id = str(command["parent_run_id"])
            await self._assert_optional_recovery_fence_tx(db, recovery_lease, parent_run_id)
            expected_owner = await self._required_recovery_owner_tx(db)
            parent = await (
                await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (parent_run_id,))
            ).fetchone()
            if parent is None:
                raise RunNotFound("parent_not_found", "delegate parent does not exist")
            self._assert_run_owner(parent, expected_owner)
            now = float(self._clock())
            if (
                str(command["status"]) not in {"leased", "scheduled"}
                or str(command["schedule_lease_owner"] or "") != lease_owner
                or int(command["schedule_lease_epoch"]) != lease_epoch
                or command["schedule_lease_expires_at"] is None
                or float(command["schedule_lease_expires_at"]) <= now
            ):
                raise VersionConflict(
                    "stale_child_lease",
                    "child command lease owner, epoch, or expiry changed",
                )
            intent = self._row_to_child_command(command).intent
            await self._ensure_child_run_link_tx(db, intent, expected_owner)
            self._fault("child_schedule_after_run")
            cursor = await db.execute(
                """UPDATE execution_child_commands SET status='scheduled',updated_at=?
                WHERE operation_id=? AND schedule_lease_owner=?
                AND schedule_lease_epoch=? AND schedule_lease_expires_at>?
                AND status IN ('leased','scheduled')""",
                (now, operation_id, lease_owner, lease_epoch, now),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_child_lease", "child command changed before schedule commit"
                )
            self._fault("child_schedule_before_commit")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            assert row is not None
            await db.commit()
            return self._row_to_child_command(row)

    async def _enqueue_child_accepted_signal_tx(
        self, db: aiosqlite.Connection, command: Mapping[str, Any], *, now: float
    ) -> None:
        operation_id = str(command["operation_id"])
        await db.execute(
            """INSERT INTO execution_child_signal_inbox(
            signal_id,schema_version,operation_id,parent_run_id,command_id,
            child_run_id,kind,payload_json,created_at,updated_at
            ) VALUES(?,1,?,?,?,?, 'accepted',?, ?,?)
            ON CONFLICT(operation_id,kind) DO NOTHING""",
            (
                self._stable_child_signal_id(operation_id, "accepted"),
                operation_id,
                str(command["parent_run_id"]),
                str(command["command_id"]),
                str(command["child_run_id"]),
                canonical_json({"accepted": True}),
                now,
                now,
            ),
        )

    async def acknowledge_child_command(
        self,
        operation_id: str,
        *,
        lease_owner: str,
        lease_epoch: int,
        recovery_lease: RecoveryLease | None = None,
    ) -> ChildCommandRecord:
        async with self._write_transaction() as db:
            command = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            if command is None:
                raise RunNotFound("child_command_not_found", "child command does not exist")
            parent_run_id = str(command["parent_run_id"])
            await self._assert_optional_recovery_fence_tx(db, recovery_lease, parent_run_id)
            parent = await self._recovery_run_tx(db, parent_run_id)
            if str(command["status"]) == ChildCommandStatus.ACKED.value:
                await db.commit()
                return self._row_to_child_command(command)
            now = float(self._clock())
            if (
                str(command["status"]) != ChildCommandStatus.SCHEDULED.value
                or str(command["schedule_lease_owner"] or "") != lease_owner
                or int(command["schedule_lease_epoch"]) != lease_epoch
                or command["schedule_lease_expires_at"] is None
                or float(command["schedule_lease_expires_at"]) <= now
            ):
                raise VersionConflict(
                    "stale_child_lease", "only the scheduling lease may ack the child"
                )
            await self._enqueue_child_accepted_signal_tx(db, command, now=now)
            cursor = await db.execute(
                """UPDATE execution_child_commands
                SET status='acked',schedule_lease_owner=NULL,
                    schedule_lease_expires_at=NULL,ack_at=?,updated_at=?
                WHERE operation_id=? AND status='scheduled'
                AND schedule_lease_owner=? AND schedule_lease_epoch=?
                AND schedule_lease_expires_at>?""",
                (now, now, operation_id, lease_owner, lease_epoch, now),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_child_lease", "child command changed before ack commit"
                )
            self._fault("child_ack_before_commit")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            assert row is not None
            await db.commit()
            return self._row_to_child_command(row)

    async def _enqueue_child_terminal_signal_tx(
        self,
        db: aiosqlite.Connection,
        operation_id: str,
        *,
        terminal_status: str,
        value: Any = None,
    ) -> ChildSignalRecord:
        if terminal_status not in {"completed", "failed", "cancelled"}:
            raise ValueError("child terminal status is invalid")
        command = await (
            await db.execute(
                "SELECT * FROM execution_child_commands WHERE operation_id=?",
                (operation_id,),
            )
        ).fetchone()
        if command is None or str(command["status"]) not in {
            ChildCommandStatus.SCHEDULED.value,
            ChildCommandStatus.ACKED.value,
        }:
            raise RunNotFound(
                "scheduled_child_command_not_found",
                "terminal signal requires a scheduled child command",
            )
        child = await (
            await db.execute(
                "SELECT status FROM execution_runs WHERE run_id=?",
                (str(command["child_run_id"]),),
            )
        ).fetchone()
        if child is None or str(child["status"]) != terminal_status:
            raise RunIdentityConflict(
                "child_not_terminal",
                "terminal inbox status must match the authoritative child run",
            )
        await self._enqueue_child_accepted_signal_tx(db, command, now=float(self._clock()))
        signal_id = self._stable_child_signal_id(operation_id, "terminal")
        payload_json = canonical_json({"status": terminal_status, "value": value})
        existing = await (
            await db.execute(
                """SELECT * FROM execution_child_signal_inbox
                WHERE operation_id=? AND kind='terminal'""",
                (operation_id,),
            )
        ).fetchone()
        if existing is not None:
            if str(existing["payload_json"]) != payload_json:
                raise IdempotencyConflict(
                    "child_terminal_conflict",
                    "terminal signal replay differs from the first outcome",
                )
            return self._row_to_child_signal(existing)
        now = float(self._clock())
        await db.execute(
            """INSERT INTO execution_child_signal_inbox(
            signal_id,schema_version,operation_id,parent_run_id,command_id,
            child_run_id,kind,payload_json,created_at,updated_at
            ) VALUES(?,1,?,?,?,?, 'terminal',?,?,?)""",
            (
                signal_id,
                operation_id,
                str(command["parent_run_id"]),
                str(command["command_id"]),
                str(command["child_run_id"]),
                payload_json,
                now,
                now,
            ),
        )
        row = await (
            await db.execute(
                "SELECT * FROM execution_child_signal_inbox WHERE signal_id=?",
                (signal_id,),
            )
        ).fetchone()
        assert row is not None
        return self._row_to_child_signal(row)

    async def list_pending_child_signals(
        self,
        parent_run_id: str,
        *,
        limit: int = 16,
        recovery_lease: RecoveryLease | None = None,
    ) -> tuple[ChildSignalRecord, ...]:
        if isinstance(limit, bool) or limit < 1:
            raise ValueError("pending child signal limit must be positive")
        async with self._write_transaction() as db:
            await self._assert_optional_recovery_fence_tx(db, recovery_lease, parent_run_id)
            await self._recovery_run_tx(db, parent_run_id)
            now = float(self._clock())
            await db.execute(
                """UPDATE execution_child_signal_inbox
                SET attempts=attempts+1,updated_at=? WHERE signal_id IN (
                  SELECT signal_id FROM execution_child_signal_inbox
                  WHERE parent_run_id=? AND delivered_at IS NULL
                  ORDER BY created_at,
                  CASE kind WHEN 'accepted' THEN 0 ELSE 1 END,signal_id LIMIT ?
                )""",
                (now, parent_run_id, limit),
            )
            rows = await (
                await db.execute(
                    """SELECT * FROM execution_child_signal_inbox
                    WHERE parent_run_id=? AND delivered_at IS NULL
                    ORDER BY created_at,
                    CASE kind WHEN 'accepted' THEN 0 ELSE 1 END,signal_id LIMIT ?""",
                    (parent_run_id, limit),
                )
            ).fetchall()
            await db.commit()
            return tuple(self._row_to_child_signal(row) for row in rows)

    async def list_pending_child_signal_parents(
        self, *, limit: int = 10_000
    ) -> tuple[RunRecord, ...]:
        if isinstance(limit, bool) or limit < 1:
            raise ValueError("pending child parent limit must be positive")
        async with self._read_connection() as db:
            owner_kind, owner_generation = await self._required_recovery_owner_tx(db)
            rows = await (
                await db.execute(
                    """SELECT run.* FROM execution_runs AS run
                    WHERE EXISTS(
                      SELECT 1 FROM execution_child_signal_inbox AS signal
                      WHERE signal.parent_run_id=run.run_id
                      AND signal.delivered_at IS NULL
                    ) AND run.owner_kind=? AND run.owner_generation=?
                    ORDER BY run.created_at,run.run_id LIMIT ?""",
                    (owner_kind, owner_generation, limit),
                )
            ).fetchall()
            return tuple(self._row_to_record(row) for row in rows)

    async def _acknowledge_child_signal_tx(
        self, db: aiosqlite.Connection, signal_id: str, *, now: float
    ) -> ChildSignalRecord:
        row = await (
            await db.execute(
                "SELECT * FROM execution_child_signal_inbox WHERE signal_id=?",
                (signal_id,),
            )
        ).fetchone()
        if row is None:
            raise RunNotFound("child_signal_not_found", "child signal does not exist")
        if row["delivered_at"] is None:
            await db.execute(
                """UPDATE execution_child_signal_inbox
                SET delivered_at=?,updated_at=? WHERE signal_id=?
                AND delivered_at IS NULL""",
                (now, now, signal_id),
            )
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_signal_inbox WHERE signal_id=?",
                    (signal_id,),
                )
            ).fetchone()
            assert row is not None
        return self._row_to_child_signal(row)

    async def prepare_workflow_child_resume(
        self, signal_id: str, *, recovery_lease: RecoveryLease
    ) -> DecisionRecord:
        """Bind a pending child signal to the Native interrupt that will consume it."""

        async with self._write_transaction() as db:
            signal = await (
                await db.execute(
                    """SELECT signal.*,parent.driver_kind
                    FROM execution_child_signal_inbox AS signal
                    JOIN execution_runs AS parent ON parent.run_id=signal.parent_run_id
                    WHERE signal.signal_id=?""",
                    (signal_id,),
                )
            ).fetchone()
            if signal is None:
                raise RunNotFound("child_signal_not_found", "child signal does not exist")
            parent_run_id = str(signal["parent_run_id"])
            await self._assert_recovery_fence_tx(db, recovery_lease, run_id=parent_run_id)
            if str(signal["driver_kind"]) != "workflow":
                raise RunIdentityConflict(
                    "workflow_child_signal_owner_required",
                    "only a workflow parent may prepare a Native child resume",
                )
            decisions = await (
                await db.execute(
                    """SELECT * FROM execution_decisions WHERE run_id=?
                    AND kind='workflow_hitl' AND consumed_at IS NULL
                    AND status IN ('open','allowed') ORDER BY created_at,decision_id""",
                    (parent_run_id,),
                )
            ).fetchall()
            matching = []
            for row in decisions:
                envelope = json.loads(str(row["prompt_json"]))
                prompt = envelope.get("prompt", envelope)
                if (
                    isinstance(prompt, Mapping)
                    and prompt.get("kind") == "child_run"
                    and str(prompt.get("command_id") or "") == str(signal["command_id"])
                ):
                    matching.append(row)
            if len(matching) != 1:
                raise DecisionConflict(
                    "workflow_child_interrupt_not_found",
                    "child signal requires exactly one matching Native child interrupt",
                )
            row = matching[0]
            payload = {
                "kind": f"child_{signal['kind']}",
                "signal_id": signal_id,
                "command_id": str(signal["command_id"]),
                "child_run_id": str(signal["child_run_id"]),
            }
            if str(signal["kind"]) == "terminal":
                payload.update(json.loads(str(signal["payload_json"])))
            response_json = canonical_json({"child_signal": payload})
            if str(row["status"]) == "open":
                cursor = await db.execute(
                    """UPDATE execution_decisions SET status='allowed',
                    response_schema_version=1,response_json=?,
                    decision_version=decision_version+1,resolved_at=?
                    WHERE decision_id=? AND status='open' AND consumed_at IS NULL""",
                    (response_json, float(self._clock()), str(row["decision_id"])),
                )
                if cursor.rowcount != 1:
                    raise DecisionConflict(
                        "stale_decision_version", "workflow child interrupt changed"
                    )
            elif str(row["response_json"] or "") != response_json:
                raise DecisionConflict(
                    "workflow_child_signal_conflict",
                    "workflow child interrupt is bound to another response",
                )
            resolved = await (
                await db.execute(
                    "SELECT * FROM execution_decisions WHERE decision_id=?",
                    (str(row["decision_id"]),),
                )
            ).fetchone()
            assert resolved is not None
            await db.commit()
            return self._row_to_decision(resolved)

    async def ack_child_signal(
        self,
        signal_id: str,
        *,
        expected_continuation_version: int | None = None,
        continuation_payload: Mapping[str, Any] | None = None,
        event: RunEventCandidate | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        recovery_lease: RecoveryLease | None = None,
        transaction_hook: Any | None = None,
    ) -> ChildSignalRecord | tuple[ChildSignalRecord, ContinuationRecord, RunEvent]:
        """Discard a detached terminal signal or apply and ack a live boundary."""

        applies_boundary = continuation_payload is not None or event is not None
        if not applies_boundary:
            if transaction_hook is not None:
                raise ValueError("child signal transaction hook requires a boundary apply")
            async with self._write_transaction() as db:
                row = await (
                    await db.execute(
                        """SELECT parent.status AS parent_status,parent.cancel_reason,
                    parent.owner_kind,parent.owner_generation,command.join_policy
                    FROM execution_child_signal_inbox AS signal
                    JOIN execution_child_commands AS command
                      ON command.operation_id=signal.operation_id
                    JOIN execution_runs AS parent ON parent.run_id=signal.parent_run_id
                    WHERE signal.signal_id=?""",
                        (signal_id,),
                    )
                ).fetchone()
                if row is None:
                    raise RunNotFound("child_signal_not_found", "child signal does not exist")
                owner = await self._required_recovery_owner_tx(db)
                if (str(row["owner_kind"]), int(row["owner_generation"])) != owner:
                    raise RuntimeActivationError(
                        "child_signal_owner_fenced",
                        "parent belongs to another runtime owner",
                    )
                parent_status = RunStatus(str(row["parent_status"]))
                cancelling_parent = parent_status is RunStatus.CANCEL_REQUESTED or (
                    parent_status is RunStatus.CANCELLED and row["cancel_reason"] is not None
                )
                if parent_status not in TERMINAL_RUN_STATUSES and not cancelling_parent:
                    raise RunIdentityConflict(
                        "parent_not_terminal",
                        "only a terminal parent may discard a child signal",
                    )
                if (
                    str(row["join_policy"]) != AttachmentPolicy.DETACHED.value
                    and not cancelling_parent
                ):
                    raise RunIdentityConflict(
                        "attached_child_signal_after_parent_terminal",
                        "attached child signal cannot be discarded after parent terminal",
                    )
                record = await self._acknowledge_child_signal_tx(
                    db, signal_id, now=float(self._clock())
                )
                await db.commit()
                return record
        if expected_continuation_version is None or continuation_payload is None or event is None:
            raise ValueError("child boundary requires version, payload and event")
        self._validate_continuation_version(expected_continuation_version)
        payload_json = self._continuation_payload_json(continuation_payload)
        applied_event = replace(
            event,
            payload=thaw_json(event.payload),
            error=None if event.error is None else thaw_json(event.error),
            correlation={
                **thaw_json(event.correlation),
                "child_signal_id": signal_id,
                "continuation_version": expected_continuation_version + 1,
                "continuation_payload_hash": fingerprint_json(continuation_payload),
            },
        )
        async with self._write_transaction() as db:
            inbox = await (
                await db.execute(
                    "SELECT * FROM execution_child_signal_inbox WHERE signal_id=?",
                    (signal_id,),
                )
            ).fetchone()
            if inbox is None:
                raise RunNotFound("child_signal_not_found", "child signal does not exist")
            parent_run_id = str(inbox["parent_run_id"])
            await self._assert_optional_recovery_fence_tx(db, recovery_lease, parent_run_id)
            run = await self._continuation_run_tx(db, parent_run_id)
            if inbox["delivered_at"] is not None:
                continuation_row = await (
                    await db.execute(
                        "SELECT created_at FROM execution_continuations WHERE run_id=?",
                        (parent_run_id,),
                    )
                ).fetchone()
                if continuation_row is None:
                    raise IdempotencyConflict(
                        "child_signal_replay_conflict",
                        "duplicate child signal lost its parent continuation",
                    )
                stored_event, _, _ = await self._append_event_tx(
                    db,
                    run,
                    expected_version=int(run["version"]),
                    event=applied_event,
                    deliveries=deliveries,
                )
                await db.commit()
                continuation = ContinuationRecord(
                    run_id=parent_run_id,
                    payload=dict(continuation_payload),
                    version=expected_continuation_version + 1,
                    pending_decision_id=None,
                    created_at=float(continuation_row["created_at"]),
                    updated_at=float(inbox["delivered_at"]),
                )
                return self._row_to_child_signal(inbox), continuation, stored_event
            now = float(self._clock())
            continuation, _ = await self._save_continuation_tx(
                db,
                run=run,
                expected_version=expected_continuation_version,
                payload_json=payload_json,
                decision=None,
                now=now,
            )
            self._fault("child_apply_after_boundary")
            stored_event, _, _ = await self._append_event_tx(
                db,
                run,
                expected_version=int(run["version"]),
                event=applied_event,
                deliveries=deliveries,
            )
            self._fault("child_apply_after_event")
            if transaction_hook is not None:
                hooked = transaction_hook(db, continuation)
                if inspect.isawaitable(hooked):
                    await hooked
            record = await self._acknowledge_child_signal_tx(db, signal_id, now=now)
            self._fault("child_signal_ack_before_commit")
            self._fault("child_apply_before_commit")
            await db.commit()
            return record, continuation, stored_event

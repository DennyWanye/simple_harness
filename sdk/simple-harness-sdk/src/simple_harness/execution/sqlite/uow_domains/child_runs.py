# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Child run launch tickets, child terminals and child reads."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import cast

from simple_harness.contracts import JsonValue, canonical_json, thaw_json
from simple_harness.execution.contracts.children import (
    AttachmentPolicy,
    ChildCommandRecord,
    ChildCommandState,
    ChildLaunchResult,
    ChildSignalAckReceipt,
    ChildTerminalResult,
    ProfileLaunchTicket,
    ProfileLaunchTicketState,
    child_launch_fingerprint,
)
from simple_harness.execution.fences import RunFenceLease
from simple_harness.execution.uow import (
    ExecutionLease,
    FaultHook,
    RunState,
    UnitOfWorkConflict,
    UnitOfWorkNotFound,
)

from ...context_use_requirements import bind
from .core import ExecutionUnitOfWorkCore
from .support import (
    _child_signal_ack_receipt,
    _child_terminal_receipt,
    _fault,
    _object_json,
    _required,
    _time,
)
from .workflow_cancel import WorkflowCancelMixin


def _profile_launch_ticket(row: sqlite3.Row) -> ProfileLaunchTicket:
    return ProfileLaunchTicket(
        ticket_id=str(row["ticket_id"]),
        parent_run_id=str(row["parent_run_id"]),
        profile_key=str(row["profile_key"]),
        catalog_generation=int(row["catalog_generation"]),
        fingerprint=str(row["fingerprint"]),
        state=ProfileLaunchTicketState(str(row["state"])),
        child_run_id=(None if row["child_run_id"] is None else str(row["child_run_id"])),
    )


def _child_command_record(row: sqlite3.Row) -> ChildCommandRecord:
    ticket_id = (
        row["ticket_id"] if row["ticket_id"] is not None else row["workflow_ticket_receipt_id"]
    )
    return ChildCommandRecord(
        command_id=str(row["command_id"]),
        parent_run_id=str(row["parent_run_id"]),
        child_run_id=str(row["child_run_id"]),
        ticket_id=str(ticket_id),
        state=ChildCommandState(str(row["state"])),
    )


class ChildRunMixin(ExecutionUnitOfWorkCore):
    """Child run launch tickets, child terminals and child reads."""

    __slots__ = ()

    @staticmethod
    def _close_spawn_child_waits_for_parent_terminal(
        connection: sqlite3.Connection,
        *,
        parent_run_id: str,
        terminal_state: RunState,
        terminal_receipt_id: str,
        claimed_continuation_ack_receipt_id: str | None,
        now: float,
        fault: FaultHook | None,
    ) -> None:
        """Close child-wait lifecycle rows in the parent terminal tx.

        Once the child continuation is ACKed, a terminal parent must not append
        its queued public completion to Context.  The immutable queue entry is
        retained for audit and bound to the same terminal event instead.
        """

        rows = connection.execute(
            "SELECT * FROM workflow_spawn_child_wait_receipts "
            "WHERE parent_run_id=? AND state IN "
            "('unconsumed','woken','claimed','acked_completion_pending') "
            "ORDER BY parent_wait_receipt_id",
            (parent_run_id,),
        ).fetchall()
        for row in rows:
            prior_state = str(row["state"])
            phase_kind = (
                "child_active"
                if prior_state == "unconsumed"
                else (
                    "signal_pending"
                    if prior_state == "woken"
                    else (
                        "continuation_claimed" if prior_state == "claimed" else "completion_pending"
                    )
                )
            )
            child_cancel_request_id: str | None = None
            child_cancel_receipt_id: str | None = None
            reused_child_cancel_receipt_id: str | None = None
            if prior_state == "unconsumed":
                signal = connection.execute(
                    "SELECT signal_id FROM child_signals WHERE parent_run_id=? "
                    "AND child_run_id=? AND state<>'acked'",
                    (parent_run_id, str(row["child_run_id"])),
                ).fetchone()
                if signal is not None:
                    raise UnitOfWorkConflict(
                        "unconsumed spawn child-wait already has a child signal"
                    )
                child = connection.execute(
                    "SELECT state,version FROM runs WHERE run_id=?",
                    (str(row["child_run_id"]),),
                ).fetchone()
                if child is None:
                    raise UnitOfWorkConflict("spawn child Run is missing")
                existing_cancel = connection.execute(
                    "SELECT * FROM workflow_cancel_receipts WHERE run_id=? "
                    "ORDER BY generation DESC LIMIT 1",
                    (str(row["child_run_id"]),),
                ).fetchone()
                if str(child["state"]) == RunState.CANCEL_REQUESTED.value:
                    if existing_cancel is None or str(existing_cancel["phase"]) == "terminal":
                        raise UnitOfWorkConflict(
                            "cancel-requested spawn child lacks active cancel receipt"
                        )
                    reused_child_cancel_receipt_id = str(existing_cancel["cancel_id"])
                elif str(child["state"]) in {
                    RunState.RUNNING.value,
                    RunState.WAITING.value,
                }:
                    if existing_cancel is not None:
                        raise UnitOfWorkConflict(
                            "active spawn child has an inconsistent cancel receipt"
                        )
                    child_cancel_request_id = hashlib.sha256(
                        canonical_json(
                            {
                                "protocol": "workflow-spawn-parent-terminal-child-cancel-v1",
                                "parent_wait_receipt_id": str(row["parent_wait_receipt_id"]),
                                "child_run_id": str(row["child_run_id"]),
                            }
                        ).encode()
                    ).hexdigest()
                    child_cancel_receipt_id = child_cancel_request_id
                    blocker_rows = connection.execute(
                        "SELECT blocker_id,kind,ledger_identity,handoff_attempt,version "
                        "FROM run_wait_blockers WHERE run_id=? ORDER BY blocker_id",
                        (str(row["child_run_id"]),),
                    ).fetchall()
                    blocker_ids: list[JsonValue] = [
                        str(item["blocker_id"]) for item in blocker_rows
                    ]
                    blocker_snapshot: dict[str, JsonValue] = {
                        str(item["blocker_id"]): cast(
                            dict[str, JsonValue],
                            {
                                "kind": str(item["kind"]),
                                "ledger_identity": str(item["ledger_identity"]),
                                "handoff_attempt": int(item["handoff_attempt"]),
                                "observed_blocker_version": int(item["version"]),
                            },
                        )
                        for item in blocker_rows
                    }
                    _fault(fault, "root_terminal.child_cancel.run.before_write")
                    changed = connection.execute(
                        "UPDATE runs SET state='cancel_requested',version=version+1,"
                        "updated_at=? WHERE run_id=? AND version=? "
                        "AND state IN ('running','waiting')",
                        (
                            now,
                            str(row["child_run_id"]),
                            int(child["version"]),
                        ),
                    ).rowcount
                    if changed != 1:
                        raise UnitOfWorkConflict("spawn child parent-terminal cancel CAS failed")
                    _fault(fault, "root_terminal.child_cancel.run.after_write")
                    WorkflowCancelMixin._invalidate_cancel_activation(
                        connection,
                        str(row["child_run_id"]),
                        None,
                        now=now,
                        fault=fault,
                    )
                    _fault(fault, "root_terminal.child_cancel.receipt.before_write")
                    connection.execute(
                        "INSERT INTO workflow_cancel_receipts("
                        "cancel_id,run_id,generation,reason,phase,blocker_ids_json,"
                        "blocker_snapshot_json,terminal,version,created_at,updated_at) "
                        "VALUES(?,?,0,?,?,?,?,NULL,0,?,?)",
                        (
                            child_cancel_request_id,
                            str(row["child_run_id"]),
                            "attached parent reached terminal",
                            "cancelling" if blocker_ids else "requested",
                            canonical_json(blocker_ids),
                            canonical_json(blocker_snapshot),
                            now,
                            now,
                        ),
                    )
                    _fault(fault, "root_terminal.child_cancel.receipt.after_write")
                else:
                    raise UnitOfWorkConflict(
                        "spawn child is not cancellable during parent terminal"
                    )
            elif prior_state == "woken":
                continuation_id = row["continuation_id"]
                signal_id = row["child_signal_id"]
                if continuation_id is None or signal_id is None:
                    raise UnitOfWorkConflict("woken spawn child-wait lacks signal continuation")
                _fault(fault, "root_terminal.spawn_continuation.before_write")
                changed = connection.execute(
                    "UPDATE continuations SET state='quarantined',acked_at=?,"
                    "version=version+1 WHERE continuation_id=? "
                    "AND run_id=? AND state='pending'",
                    (now, continuation_id, parent_run_id),
                ).rowcount
                if changed != 1:
                    raise UnitOfWorkConflict("spawn signal continuation terminal quarantine failed")
                _fault(fault, "root_terminal.spawn_continuation.after_write")
            elif prior_state == "claimed":
                if claimed_continuation_ack_receipt_id is None:
                    raise UnitOfWorkConflict(
                        "claimed spawn child-wait requires continuation-aware terminal"
                    )
                continuation = connection.execute(
                    "SELECT state,ack_receipt_id FROM continuations "
                    "WHERE continuation_id=? AND run_id=?",
                    (row["continuation_id"], parent_run_id),
                ).fetchone()
                if (
                    continuation is None
                    or str(continuation["state"]) != "acked"
                    or str(continuation["ack_receipt_id"]) != claimed_continuation_ack_receipt_id
                ):
                    raise UnitOfWorkConflict("claimed spawn child-wait terminal ACK differs")
            terminal_hash = hashlib.sha256(
                canonical_json(
                    {
                        "parent_wait_receipt_id": str(row["parent_wait_receipt_id"]),
                        "spawn_operation_id": str(row["spawn_operation_id"]),
                        "phase_kind": phase_kind,
                        "terminal_receipt_id": terminal_receipt_id,
                        "terminal_state": terminal_state.value,
                        "child_signal_id": row["child_signal_id"],
                        "continuation_id": row["continuation_id"],
                        "pending_child_completion_hash": (
                            None
                            if row["pending_child_completion_hash"] is None
                            else str(row["pending_child_completion_hash"])
                        ),
                        "claimed_continuation_ack_receipt_id": (
                            claimed_continuation_ack_receipt_id
                            if phase_kind == "continuation_claimed"
                            else None
                        ),
                        "child_cancel_request_id": child_cancel_request_id,
                        "child_cancel_receipt_id": child_cancel_receipt_id,
                        "reused_child_cancel_receipt_id": reused_child_cancel_receipt_id,
                    }
                ).encode()
            ).hexdigest()
            next_version = int(row["version"]) + 1
            lifecycle_hash = hashlib.sha256(
                canonical_json(
                    {
                        "identity_hash": str(row["identity_hash"]),
                        "state": "acked_parent_terminal",
                        "version": next_version,
                        "phase_kind": phase_kind,
                        "terminal_receipt_id": terminal_receipt_id,
                        "terminal_state": terminal_state.value,
                        "terminal_hash": terminal_hash,
                    }
                ).encode()
            ).hexdigest()
            _fault(fault, "root_terminal.spawn_wait.before_write")
            changed = connection.execute(
                "UPDATE workflow_spawn_child_wait_receipts SET "
                "state='acked_parent_terminal',"
                "pending_completion_terminal_receipt_id=?,"
                "pending_completion_terminal_state=?,"
                "pending_completion_terminal_hash=?,"
                "parent_terminal_phase_kind=?,"
                "late_signal_quarantine_receipt_id=?,"
                "claimed_continuation_terminal_ack_receipt_id=?,"
                "progress_receipt_id=COALESCE(progress_receipt_id,?),"
                "child_cancel_request_id=?,child_cancel_receipt_id=?,"
                "reused_child_cancel_receipt_id=?,"
                "version=?,lifecycle_hash=? "
                "WHERE parent_wait_receipt_id=? "
                "AND state=? AND version=?",
                (
                    terminal_receipt_id,
                    terminal_state.value,
                    terminal_hash,
                    phase_kind,
                    terminal_receipt_id if phase_kind == "signal_pending" else None,
                    (
                        claimed_continuation_ack_receipt_id
                        if phase_kind == "continuation_claimed"
                        else None
                    ),
                    (
                        claimed_continuation_ack_receipt_id
                        if phase_kind == "continuation_claimed"
                        else None
                    ),
                    child_cancel_request_id,
                    child_cancel_receipt_id,
                    reused_child_cancel_receipt_id,
                    next_version,
                    lifecycle_hash,
                    str(row["parent_wait_receipt_id"]),
                    prior_state,
                    int(row["version"]),
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("spawn child completion terminal closure CAS failed")
            _fault(fault, "root_terminal.spawn_wait.after_write")

    def issue_profile_launch_ticket(
        self,
        ticket: ProfileLaunchTicket,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> ProfileLaunchTicket:
        """Persist one immutable ticket; issuance never creates a child."""

        if ticket.state is not ProfileLaunchTicketState.ISSUED:
            raise ValueError("new profile launch ticket must be issued")
        now = _time(now)
        existing = self.read_profile_launch_ticket(ticket.ticket_id)
        if existing is not None:
            if existing == ticket:
                return existing
            raise UnitOfWorkConflict("profile launch ticket identity conflict")
        with self.database.transaction() as connection:
            parent = connection.execute(
                "SELECT state FROM runs WHERE run_id = ?", (ticket.parent_run_id,)
            ).fetchone()
            if parent is None:
                raise UnitOfWorkNotFound(ticket.parent_run_id)
            if str(parent["state"]) in {"completed", "failed", "cancelled"}:
                raise UnitOfWorkConflict("terminal parent cannot issue a launch ticket")
            _fault(fault, "profile_ticket_issue.ticket.before_write")
            connection.execute(
                """
                INSERT INTO profile_launch_tickets(
                    ticket_id, parent_run_id, profile_key, catalog_generation,
                    fingerprint, state, child_run_id, issued_at, claimed_at
                ) VALUES (?, ?, ?, ?, ?, 'issued', NULL, ?, NULL)
                """,
                (
                    ticket.ticket_id,
                    ticket.parent_run_id,
                    ticket.profile_key,
                    ticket.catalog_generation,
                    ticket.fingerprint,
                    now,
                ),
            )
            _fault(fault, "profile_ticket_issue.ticket.after_write")
        _fault(fault, "profile_ticket_issue.after_commit")
        result = self.read_profile_launch_ticket(ticket.ticket_id)
        assert result is not None
        return result

    def claim_profile_launch_and_commit_child(
        self,
        *,
        ticket_id: str,
        expected_catalog_generation: int,
        launch_request: Mapping[str, JsonValue],
        command_id: str,
        child_run_id: str,
        request_id: str,
        attachment_policy: AttachmentPolicy,
        start_snapshot: Mapping[str, JsonValue],
        event_id: str,
        now: float,
        fault: FaultHook | None = None,
    ) -> ChildLaunchResult:
        """Consume the ticket and commit the complete child start atomically."""

        ticket_id = _required(ticket_id, "ticket_id")
        command_id = _required(command_id, "command_id")
        child_run_id = _required(child_run_id, "child_run_id")
        request_id = _required(request_id, "request_id")
        event_id = _required(event_id, "event_id")
        if isinstance(expected_catalog_generation, bool) or expected_catalog_generation < 1:
            raise ValueError("expected_catalog_generation must be positive")
        attachment_policy = AttachmentPolicy(attachment_policy)
        now = _time(now)
        launch_json = _object_json(launch_request, "launch_request")
        launch_fingerprint = child_launch_fingerprint(dict(launch_request))
        snapshot_json = _object_json(start_snapshot, "start_snapshot")
        snapshot_hash = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
        profile_key = _required(launch_request.get("profile_key"), "profile_key")
        driver_kind = _required(launch_request.get("driver_kind"), "driver_kind")
        generation = launch_request.get("catalog_generation")
        if generation != expected_catalog_generation:
            raise UnitOfWorkConflict("launch request catalog generation is stale")

        existing_ticket = self.read_profile_launch_ticket(ticket_id)
        if existing_ticket is None:
            raise UnitOfWorkNotFound(ticket_id)
        if existing_ticket.state is ProfileLaunchTicketState.CLAIMED:
            command = self.read_child_command(command_id)
            if (
                existing_ticket.child_run_id == child_run_id
                and command is not None
                and command.ticket_id == ticket_id
                and command.child_run_id == child_run_id
                and self._same_existing_child_launch(
                    command,
                    request_id=request_id,
                    profile_key=profile_key,
                    driver_kind=driver_kind,
                    attachment_policy=attachment_policy,
                    snapshot_hash=snapshot_hash,
                )
            ):
                return ChildLaunchResult(existing_ticket, command, child_run_id)
            raise UnitOfWorkConflict("profile launch ticket was already consumed")
        if existing_ticket.state is not ProfileLaunchTicketState.ISSUED:
            raise UnitOfWorkConflict("profile launch ticket is not claimable")
        if (
            existing_ticket.catalog_generation != expected_catalog_generation
            or existing_ticket.profile_key != profile_key
            or existing_ticket.fingerprint != launch_fingerprint
        ):
            raise UnitOfWorkConflict("profile launch ticket binding mismatch")

        with self.database.transaction() as connection:
            parent = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (existing_ticket.parent_run_id,)
            ).fetchone()
            if parent is None:
                raise UnitOfWorkNotFound(existing_ticket.parent_run_id)
            if str(parent["state"]) in {"completed", "failed", "cancelled"}:
                raise UnitOfWorkConflict("terminal parent cannot launch a child")
            _fault(fault, "child_launch.run.before_write")
            connection.execute(
                """
                INSERT INTO runs(
                    run_id, execution_session_id, request_id, root_run_id,
                    parent_run_id, profile_key, driver_kind, state, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'created', 0, ?, ?)
                """,
                (
                    child_run_id,
                    str(parent["execution_session_id"]),
                    request_id,
                    str(parent["root_run_id"]),
                    existing_ticket.parent_run_id,
                    profile_key,
                    driver_kind,
                    now,
                    now,
                ),
            )
            _fault(fault, "child_launch.run.after_write")
            _fault(fault, "child_launch.snapshot.before_write")
            connection.execute(
                """
                INSERT INTO run_start_snapshots(run_id, snapshot_json, snapshot_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (child_run_id, snapshot_json, snapshot_hash, now),
            )

            bind(
                connection,
                child_run_id,
                self._context_use_scope,
                "run_start",
                child_run_id,
                snapshot_hash,
            )
            _fault(fault, "child_launch.snapshot.after_write")
            _fault(fault, "child_launch.ticket.before_write")
            changed = connection.execute(
                """
                UPDATE profile_launch_tickets
                SET state = 'claimed', child_run_id = ?, claimed_at = ?
                WHERE ticket_id = ? AND state = 'issued' AND child_run_id IS NULL
                """,
                (child_run_id, now, ticket_id),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("profile launch ticket claim CAS failed")
            _fault(fault, "child_launch.ticket.after_write")
            _fault(fault, "child_launch.command.before_write")
            connection.execute(
                """
                INSERT INTO child_commands(
                    command_id, parent_run_id, child_run_id, ticket_id,
                    state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    command_id,
                    existing_ticket.parent_run_id,
                    child_run_id,
                    ticket_id,
                    now,
                    now,
                ),
            )
            _fault(fault, "child_launch.command.after_write")
            _fault(fault, "child_launch.link.before_write")
            connection.execute(
                """
                INSERT INTO run_links(parent_run_id, child_run_id, attachment_policy, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    existing_ticket.parent_run_id,
                    child_run_id,
                    attachment_policy.value,
                    now,
                ),
            )
            _fault(fault, "child_launch.link.after_write")
            if attachment_policy is not AttachmentPolicy.DETACHED:
                _fault(fault, "child_launch.parent.before_write")
                changed = connection.execute(
                    """
                    UPDATE runs SET state = 'waiting', version = version + 1, updated_at = ?
                    WHERE run_id = ? AND state NOT IN ('completed', 'failed', 'cancelled')
                    """,
                    (now, existing_ticket.parent_run_id),
                ).rowcount
                if changed != 1:
                    raise UnitOfWorkConflict("parent wait transition CAS failed")
                _fault(fault, "child_launch.parent.after_write")
            _fault(fault, "child_launch.event.before_write")
            self._insert_event(
                connection,
                event_id=event_id,
                run_id=child_run_id,
                kind="child.created",
                payload={
                    "command_id": command_id,
                    "ticket_id": ticket_id,
                    "launch": json.loads(launch_json),
                },
                now=now,
            )
            _fault(fault, "child_launch.event.after_write")
        _fault(fault, "child_launch.after_commit")
        ticket = self.read_profile_launch_ticket(ticket_id)
        command = self.read_child_command(command_id)
        assert ticket is not None and command is not None
        return ChildLaunchResult(ticket, command, child_run_id)

    def finalize_child_and_enqueue_parent_signal(
        self,
        *,
        command_id: str,
        expected_child_version: int,
        terminal_state: RunState,
        signal_id: str,
        signal_payload: Mapping[str, JsonValue],
        event_id: str,
        receipt_id: str,
        run_fence: RunFenceLease,
        execution_lease: ExecutionLease,
        now: float,
        fault: FaultHook | None = None,
    ) -> ChildTerminalResult:
        return self._commit_child_terminal(
            command_id=command_id,
            expected_child_version=expected_child_version,
            terminal_state=terminal_state,
            terminal_payload=signal_payload,
            signal_id=signal_id,
            event_id=event_id,
            receipt_id=receipt_id,
            run_fence=run_fence,
            execution_lease=execution_lease,
            now=now,
            fault=fault,
            expect_detached=False,
        )

    def commit_detached_child_terminal(
        self,
        *,
        command_id: str,
        expected_child_version: int,
        terminal_state: RunState,
        terminal_payload: Mapping[str, JsonValue],
        event_id: str,
        receipt_id: str,
        run_fence: RunFenceLease,
        execution_lease: ExecutionLease,
        now: float,
        fault: FaultHook | None = None,
    ) -> ChildTerminalResult:
        return self._commit_child_terminal(
            command_id=command_id,
            expected_child_version=expected_child_version,
            terminal_state=terminal_state,
            terminal_payload=terminal_payload,
            signal_id=None,
            event_id=event_id,
            receipt_id=receipt_id,
            run_fence=run_fence,
            execution_lease=execution_lease,
            now=now,
            fault=fault,
            expect_detached=True,
        )

    def _commit_child_terminal(
        self,
        *,
        command_id: str,
        expected_child_version: int,
        terminal_state: RunState,
        terminal_payload: Mapping[str, JsonValue],
        signal_id: str | None,
        event_id: str,
        receipt_id: str,
        run_fence: RunFenceLease,
        execution_lease: ExecutionLease,
        now: float,
        fault: FaultHook | None,
        expect_detached: bool,
    ) -> ChildTerminalResult:
        command_id = _required(command_id, "command_id")
        if signal_id is not None:
            signal_id = _required(signal_id, "signal_id")
        event_id = _required(event_id, "event_id")
        receipt_id = _required(receipt_id, "receipt_id")
        if isinstance(expected_child_version, bool) or expected_child_version < 0:
            raise ValueError("expected_child_version must be non-negative")
        terminal_state = RunState(terminal_state)
        if terminal_state not in {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
        }:
            raise ValueError("child finalization requires a terminal run state")
        payload_json = _object_json(terminal_payload, "terminal_payload")
        outcome_hash = hashlib.sha256(
            canonical_json(
                {
                    "command_id": command_id,
                    "expected_child_version": expected_child_version,
                    "terminal_state": terminal_state.value,
                    "signal_id": signal_id,
                    "event_id": event_id,
                    "payload": json.loads(payload_json),
                }
            ).encode()
        ).hexdigest()
        now = _time(now)
        existing = self._read_child_terminal_receipt(receipt_id)
        if existing is not None:
            return self._replay_child_terminal(
                existing,
                command_id=command_id,
                terminal_state=terminal_state,
                outcome_hash=outcome_hash,
                signal_id=signal_id,
                event_id=event_id,
                run_fence=run_fence,
                execution_lease=execution_lease,
            )
        existing_command = self._read_child_terminal_receipt_for_command(command_id)
        if existing_command is not None:
            raise UnitOfWorkConflict(
                "child terminal command was already committed with another receipt"
            )
        with self.database.transaction() as connection:
            receipt_row = connection.execute(
                "SELECT * FROM child_terminal_receipts WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone()
            if receipt_row is not None:
                return self._replay_child_terminal(
                    _child_terminal_receipt(receipt_row),
                    command_id=command_id,
                    terminal_state=terminal_state,
                    outcome_hash=outcome_hash,
                    signal_id=signal_id,
                    event_id=event_id,
                    run_fence=run_fence,
                    execution_lease=execution_lease,
                )
            command_receipt = connection.execute(
                "SELECT receipt_id FROM child_terminal_receipts WHERE command_id=?",
                (command_id,),
            ).fetchone()
            if command_receipt is not None:
                raise UnitOfWorkConflict(
                    "child terminal command was already committed with another receipt"
                )
            command_row = connection.execute(
                "SELECT * FROM child_commands WHERE command_id=?", (command_id,)
            ).fetchone()
            if command_row is None:
                raise UnitOfWorkNotFound(command_id)
            child_run_id = str(command_row["child_run_id"])
            parent_run_id = str(command_row["parent_run_id"])
            link = connection.execute(
                "SELECT attachment_policy FROM run_links WHERE parent_run_id=? AND child_run_id=?",
                (parent_run_id, child_run_id),
            ).fetchone()
            if link is None:
                raise UnitOfWorkConflict("child attachment policy is missing")
            policy = AttachmentPolicy(str(link["attachment_policy"]))
            if (policy is AttachmentPolicy.DETACHED) != expect_detached:
                raise UnitOfWorkConflict("child terminal path differs from durable policy")
            if execution_lease.run_id != child_run_id:
                raise UnitOfWorkConflict("child terminal lease belongs to another Run")
            self._require_runtime_lease(connection, execution_lease, now=now)
            self._require_run_fence(connection, run_fence, execution_lease=execution_lease)
            _fault(fault, "child_terminal.run.before_write")
            changed = connection.execute(
                """
                UPDATE runs SET state = ?, version = version + 1, updated_at = ?
                WHERE run_id = ? AND version = ?
                  AND state NOT IN ('completed', 'failed', 'cancelled')
                """,
                (
                    terminal_state.value,
                    now,
                    child_run_id,
                    expected_child_version,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("child terminal CAS failed")
            _fault(fault, "child_terminal.run.after_write")
            _fault(fault, "child_terminal.command.before_write")
            changed = connection.execute(
                """
                UPDATE child_commands SET state = 'acked', updated_at = ?
                WHERE command_id = ? AND state IN ('pending', 'scheduled')
                """,
                (now, command_id),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("child command terminal CAS failed")
            _fault(fault, "child_terminal.command.after_write")
            if signal_id is not None:
                _fault(fault, "child_terminal.signal.before_write")
                connection.execute(
                    """
                    INSERT INTO child_signals(
                        signal_id, parent_run_id, child_run_id, payload_json,
                        state, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)
                    """,
                    (signal_id, parent_run_id, child_run_id, payload_json, now, now),
                )
                _fault(fault, "child_terminal.signal.after_write")
            _fault(fault, "child_terminal.event.before_write")
            self._insert_event(
                connection,
                event_id=event_id,
                run_id=child_run_id,
                kind=f"child.{terminal_state.value}",
                payload={
                    "command_id": command_id,
                    "signal_id": signal_id,
                    "terminal": dict(terminal_payload),
                },
                now=now,
            )
            _fault(fault, "child_terminal.event.after_write")
            _fault(fault, "child_terminal.receipt.before_write")
            connection.execute(
                "INSERT INTO child_terminal_receipts("
                "receipt_id,command_id,child_run_id,terminal_state,outcome_hash,"
                "signal_id,event_id,owner_id,runtime_lease_epoch,fence_epoch,created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    receipt_id,
                    command_id,
                    child_run_id,
                    terminal_state.value,
                    outcome_hash,
                    signal_id,
                    event_id,
                    execution_lease.owner_id,
                    execution_lease.epoch,
                    run_fence.epoch,
                    now,
                ),
            )
            _fault(fault, "child_terminal.receipt.after_write")
            _fault(fault, "child_terminal.fence.before_write")
            changed = connection.execute(
                "UPDATE run_fences SET state='released',released_at=? "
                "WHERE run_id=? AND owner_id=? AND runtime_lease_epoch=? "
                "AND epoch=? AND state='active'",
                (
                    now,
                    child_run_id,
                    run_fence.owner_id,
                    run_fence.runtime_lease_epoch,
                    run_fence.epoch,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("child terminal fence release CAS failed")
            _fault(fault, "child_terminal.fence.after_write")
            connection.execute(
                "UPDATE continuations SET state='quarantined',acked_at=?,"
                "version=version+1 WHERE run_id=? AND state IN ('pending','claimed')",
                (now, child_run_id),
            )
        _fault(fault, "child_terminal.after_commit")
        receipt = self._read_child_terminal_receipt(receipt_id)
        assert receipt is not None
        return ChildTerminalResult(
            receipt.child_run_id,
            receipt.terminal_state,
            receipt,
            None if signal_id is None else self.read_child_signal(signal_id),
        )

    def read_profile_launch_ticket(self, ticket_id: str) -> ProfileLaunchTicket | None:
        row = self.database.connection.execute(
            "SELECT * FROM profile_launch_tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        return None if row is None else _profile_launch_ticket(row)

    def read_child_command(self, command_id: str) -> ChildCommandRecord | None:
        row = self.database.connection.execute(
            "SELECT * FROM child_commands WHERE command_id = ?", (command_id,)
        ).fetchone()
        return None if row is None else _child_command_record(row)

    def read_child_command_for_run(self, child_run_id: str) -> ChildCommandRecord | None:
        row = self.database.connection.execute(
            "SELECT * FROM child_commands WHERE child_run_id = ?", (child_run_id,)
        ).fetchone()
        return None if row is None else _child_command_record(row)

    def is_workflow_spawn_child(self, child_run_id: str) -> bool:
        row = self.database.connection.execute(
            "SELECT workflow_ticket_receipt_id FROM child_commands WHERE child_run_id=?",
            (child_run_id,),
        ).fetchone()
        return row is not None and row["workflow_ticket_receipt_id"] is not None

    def read_child_terminal_result_for_run(self, child_run_id: str) -> ChildTerminalResult | None:
        receipt_row = self.database.connection.execute(
            "SELECT * FROM child_terminal_receipts WHERE child_run_id=?",
            (child_run_id,),
        ).fetchone()
        if receipt_row is None:
            return None
        receipt = _child_terminal_receipt(receipt_row)
        command = self.database.connection.execute(
            "SELECT state,parent_run_id FROM child_commands WHERE command_id=?",
            (receipt.command_id,),
        ).fetchone()
        run = self.database.connection.execute(
            "SELECT state,version FROM runs WHERE run_id=?", (child_run_id,)
        ).fetchone()
        event = self.database.connection.execute(
            "SELECT kind,payload_json FROM run_events WHERE event_id=? AND run_id=?",
            (receipt.event_id, child_run_id),
        ).fetchone()
        if command is None or run is None or event is None:
            raise UnitOfWorkConflict("workflow child terminal chain is incomplete")
        if (
            str(command["state"]) != ChildCommandState.ACKED.value
            or str(run["state"]) != receipt.terminal_state
            or str(event["kind"]) != f"child.{receipt.terminal_state}"
        ):
            raise UnitOfWorkConflict("workflow child terminal chain differs")
        event_payload = json.loads(str(event["payload_json"]))
        if not isinstance(event_payload, dict):
            raise UnitOfWorkConflict("workflow child terminal event is malformed")
        terminal_payload = event_payload.get("terminal")
        if not isinstance(terminal_payload, dict):
            raise UnitOfWorkConflict("workflow child terminal payload is malformed")
        expected_hash = hashlib.sha256(
            canonical_json(
                {
                    "command_id": receipt.command_id,
                    "expected_child_version": int(run["version"]) - 1,
                    "terminal_state": receipt.terminal_state,
                    "signal_id": receipt.signal_id,
                    "event_id": receipt.event_id,
                    "payload": terminal_payload,
                }
            ).encode()
        ).hexdigest()
        if expected_hash != receipt.outcome_hash:
            raise UnitOfWorkConflict("workflow child terminal outcome hash differs")
        signal = None if receipt.signal_id is None else self.read_child_signal(receipt.signal_id)
        if receipt.signal_id is not None:
            if signal is None or signal.parent_run_id != str(command["parent_run_id"]):
                raise UnitOfWorkConflict("workflow child terminal signal differs")
            if canonical_json(thaw_json(signal.payload)) != canonical_json(terminal_payload):
                raise UnitOfWorkConflict("workflow child terminal signal payload differs")
        return ChildTerminalResult(
            child_run_id,
            receipt.terminal_state,
            receipt,
            signal,
        )

    def read_child_attachment_policy(self, child_run_id: str) -> AttachmentPolicy:
        row = self.database.connection.execute(
            "SELECT attachment_policy FROM run_links WHERE child_run_id = ?",
            (child_run_id,),
        ).fetchone()
        if row is None:
            raise UnitOfWorkNotFound(child_run_id)
        return AttachmentPolicy(str(row["attachment_policy"]))

    def list_child_signal_parent_run_ids(self) -> tuple[str, ...]:
        rows = self.database.connection.execute(
            "SELECT DISTINCT parent_run_id FROM child_signals "
            "WHERE state <> 'acked' ORDER BY parent_run_id"
        ).fetchall()
        return tuple(str(row["parent_run_id"]) for row in rows)

    def read_child_signal_ack_receipt(self, receipt_id: str) -> ChildSignalAckReceipt | None:
        row = self.database.connection.execute(
            "SELECT * FROM child_signal_ack_receipts WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchone()
        return None if row is None else _child_signal_ack_receipt(row)

    def _same_existing_child_launch(
        self,
        command: ChildCommandRecord,
        *,
        request_id: str,
        profile_key: str,
        driver_kind: str,
        attachment_policy: AttachmentPolicy,
        snapshot_hash: str,
    ) -> bool:
        row = self.database.connection.execute(
            """
            SELECT runs.request_id, runs.profile_key, runs.driver_kind,
                   snapshots.snapshot_hash, links.attachment_policy
            FROM runs
            JOIN run_start_snapshots AS snapshots ON snapshots.run_id = runs.run_id
            JOIN run_links AS links ON links.child_run_id = runs.run_id
            WHERE runs.run_id = ? AND links.parent_run_id = ?
            """,
            (command.child_run_id, command.parent_run_id),
        ).fetchone()
        return row is not None and tuple(row) == (
            request_id,
            profile_key,
            driver_kind,
            snapshot_hash,
            attachment_policy.value,
        )

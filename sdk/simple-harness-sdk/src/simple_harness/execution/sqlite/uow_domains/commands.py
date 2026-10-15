# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Command ingress: submission, claiming, application and run-mode reservation."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping
from typing import TYPE_CHECKING

from simple_harness.contracts import JsonValue
from simple_harness.execution.uow import (
    ContinuationRecord,
    FaultHook,
    RunRecord,
    RunState,
    UnitOfWorkConflict,
)
from simple_harness.runtime.commands import (
    CancelCommandIntent,
    CommandErrorCode,
    CommandReceipt,
    CommandSnapshot,
    CommandState,
    ContinueCommandIntent,
    StartCommandIntent,
)

from ...command_ingress import CommandClaim, CommandIngress
from ..command_audit import record_command_event
from .core import ExecutionUnitOfWorkCore
from .support import _object_json


class CommandIngressMixin(ExecutionUnitOfWorkCore):
    """Command ingress: submission, claiming, application and run-mode reservation."""

    __slots__ = ()

    if TYPE_CHECKING:

        def _create_start_on_connection(
            self,
            connection: sqlite3.Connection,
            *,
            execution_session_id: str,
            run_id: str,
            request_id: str,
            profile_key: str,
            driver_kind: str,
            snapshot: Mapping[str, JsonValue],
            snapshot_json: str,
            snapshot_hash: str,
            event_id: str,
            now: float,
            user_id: str,
            context_stage_id: str | None,
            context_stage_hash: str | None,
            fault: FaultHook | None,
        ) -> None: ...
        def _enqueue_continuation_on_connection(
            self,
            connection: sqlite3.Connection,
            *,
            continuation_id: str,
            run_id: str,
            payload: Mapping[str, JsonValue],
            payload_json: str,
            now: float,
            context_stage_id: str | None,
            context_stage_hash: str | None,
            fault: FaultHook | None,
        ) -> None: ...
        def _request_run_cancel_on_connection(
            self,
            connection: sqlite3.Connection,
            *,
            run_id: str,
            expected_version: int,
            event_id: str,
            now: float,
            fault: FaultHook | None,
        ) -> None: ...

    def submit_start_command(self, intent: StartCommandIntent, *, now: float) -> CommandReceipt:
        return CommandIngress(
            self.database, context_use_scope=self._context_use_scope
        ).submit_start(intent, now=now)

    def submit_continue_command(
        self, intent: ContinueCommandIntent, *, now: float
    ) -> CommandReceipt:
        return CommandIngress(
            self.database, context_use_scope=self._context_use_scope
        ).submit_continue(intent, now=now)

    def submit_cancel_command(self, intent: CancelCommandIntent, *, now: float) -> CommandReceipt:
        return CommandIngress(
            self.database, context_use_scope=self._context_use_scope
        ).submit_cancel(intent, now=now)

    def get_command_receipt(self, command_id: str) -> CommandReceipt:
        return CommandIngress(self.database, context_use_scope=self._context_use_scope).get(
            command_id
        )

    def get_command_snapshot(self, command_id: str) -> CommandSnapshot:
        return CommandIngress(self.database, context_use_scope=self._context_use_scope).snapshot(
            command_id
        )

    def reserve_legacy_run_mode(self, *, run_id: str, intent_hash: str, now: float) -> None:
        CommandIngress(self.database, context_use_scope=self._context_use_scope).reserve_legacy_run(
            namespace="legacy/runtime",
            projection_key_id="legacy-v1",
            run_id=run_id,
            intent_hash=intent_hash,
            now=now,
        )

    def reserve_host_control_run_mode(self, *, run_id: str, intent_hash: str, now: float) -> None:
        CommandIngress(
            self.database, context_use_scope=self._context_use_scope
        ).reserve_host_control_run(run_id=run_id, intent_hash=intent_hash, now=now)

    def require_legacy_or_unmanaged_run(self, run_id: str) -> None:
        CommandIngress(
            self.database, context_use_scope=self._context_use_scope
        ).require_legacy_or_unmanaged(run_id)

    def reserve_base_agent_run_mode(self, *, run_id: str, intent_hash: str, now: float) -> None:
        CommandIngress(
            self.database, context_use_scope=self._context_use_scope
        ).reserve_base_agent_run(run_id=run_id, intent_hash=intent_hash, now=now)

    def require_base_agent_run_mode(self, run_id: str) -> None:
        CommandIngress(
            self.database, context_use_scope=self._context_use_scope
        ).require_base_agent_run(run_id)

    def claim_next_command(
        self, *, owner_id: str, now: float, lease_seconds: float
    ) -> CommandClaim | None:
        return CommandIngress(self.database, context_use_scope=self._context_use_scope).claim_next(
            owner_id=owner_id, now=now, lease_seconds=lease_seconds
        )

    def transition_command(
        self,
        claim: CommandClaim,
        *,
        expected: CommandState,
        target: CommandState,
        now: float,
    ) -> CommandReceipt:
        return CommandIngress(self.database, context_use_scope=self._context_use_scope).transition(
            claim, expected=expected, target=target, now=now
        )

    def retry_command(
        self,
        claim: CommandClaim,
        *,
        error_code: str,
        retry_at: float,
        now: float,
    ) -> CommandReceipt:
        return CommandIngress(self.database, context_use_scope=self._context_use_scope).retry(
            claim, error_code=error_code, retry_at=retry_at, now=now
        )

    def heartbeat_command(
        self, claim: CommandClaim, *, now: float, lease_seconds: float
    ) -> CommandClaim:
        return CommandIngress(self.database, context_use_scope=self._context_use_scope).heartbeat(
            claim, now=now, lease_seconds=lease_seconds
        )

    def reject_command(
        self,
        claim: CommandClaim,
        *,
        error_code: CommandErrorCode,
        now: float,
    ) -> CommandReceipt:
        return CommandIngress(self.database, context_use_scope=self._context_use_scope).reject(
            claim, error_code=error_code, now=now
        )

    def apply_start_command(
        self,
        claim: CommandClaim,
        *,
        execution_session_id: str,
        request_id: str,
        profile_key: str,
        driver_kind: str,
        snapshot: Mapping[str, JsonValue],
        event_id: str,
        now: float,
        user_id: str,
        context_stage_id: str | None = None,
        context_stage_hash: str | None = None,
        fault: FaultHook | None = None,
    ) -> RunRecord:
        """Atomically apply a staged start and settle its claimed command."""

        if claim.receipt.kind.value != "start":
            raise UnitOfWorkConflict("start apply requires a start command")
        snapshot_json = _object_json(snapshot, "snapshot")
        snapshot_hash = hashlib.sha256(snapshot_json.encode()).hexdigest()
        with self.database.transaction() as connection:
            self._create_start_on_connection(
                connection,
                execution_session_id=execution_session_id,
                run_id=claim.receipt.run_id.value,
                request_id=request_id,
                profile_key=profile_key,
                driver_kind=driver_kind,
                snapshot=snapshot,
                snapshot_json=snapshot_json,
                snapshot_hash=snapshot_hash,
                event_id=event_id,
                now=now,
                user_id=user_id,
                context_stage_id=context_stage_id,
                context_stage_hash=context_stage_hash,
                fault=fault,
            )
            self._settle_applied_command(connection, claim=claim, now=now)
        record = self.read_run(claim.receipt.run_id.value)
        assert record is not None
        return record

    def apply_continue_command(
        self,
        claim: CommandClaim,
        *,
        continuation_id: str,
        payload: Mapping[str, JsonValue],
        now: float,
        context_stage_id: str | None = None,
        context_stage_hash: str | None = None,
        fault: FaultHook | None = None,
    ) -> ContinuationRecord:
        if claim.receipt.kind.value != "continue":
            raise UnitOfWorkConflict("continue apply requires a continue command")
        payload_json = _object_json(payload, "payload")
        with self.database.transaction() as connection:
            self._enqueue_continuation_on_connection(
                connection,
                continuation_id=continuation_id,
                run_id=claim.receipt.run_id.value,
                payload=payload,
                payload_json=payload_json,
                now=now,
                context_stage_id=context_stage_id,
                context_stage_hash=context_stage_hash,
                fault=fault,
            )
            self._settle_applied_command(connection, claim=claim, now=now)
        result = self.read_continuation(continuation_id)
        assert result is not None
        return result

    def apply_cancel_command(
        self,
        claim: CommandClaim,
        *,
        event_id: str,
        now: float,
        fault: FaultHook | None = None,
    ) -> RunRecord:
        if claim.receipt.kind.value != "cancel":
            raise UnitOfWorkConflict("cancel apply requires a cancel command")
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM runs WHERE run_id=?", (claim.receipt.run_id.value,)
            ).fetchone()
            if existing is not None and str(existing["state"]) not in {
                "cancel_requested",
                "cancelled",
                "completed",
                "failed",
            }:
                self._request_run_cancel_on_connection(
                    connection,
                    run_id=claim.receipt.run_id.value,
                    expected_version=int(existing["version"]),
                    event_id=event_id,
                    now=now,
                    fault=fault,
                )
            self._settle_applied_command(connection, claim=claim, now=now)
        record = self.read_run(claim.receipt.run_id.value)
        if record is None:
            # Pre-apply cancel is a complete fence even though no Run was materialized.
            return RunRecord(
                claim.receipt.run_id.value,
                "",
                "",
                claim.receipt.run_id.value,
                None,
                "",
                "",
                RunState.CANCELLED,
                0,
            )
        return record

    @staticmethod
    def _settle_applied_command(
        connection: sqlite3.Connection, *, claim: CommandClaim, now: float
    ) -> None:
        changed = connection.execute(
            """
            UPDATE conversation_commands
            SET state='applied',raw_payload_json=NULL,owner_id=NULL,lease_expires_at=NULL,
                version=version+1,updated_at=?
            WHERE command_id=? AND state='context_ready' AND owner_id=? AND claim_epoch=?
            """,
            (now, claim.receipt.command_id, claim.owner_id, claim.claim_epoch),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("command apply stale-result fence failed")

        record_command_event(connection, claim.receipt.command_id, "applied", now=now, claim=claim)

    @staticmethod
    def _insert_conversation_output(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        output: Mapping[str, JsonValue] | None,
        now: float,
    ) -> None:
        if output is None:
            return
        command = connection.execute(
            "SELECT command_id FROM conversation_commands "
            "WHERE run_id=? AND state='applied' AND kind IN ('start','continue') "
            "ORDER BY accept_seq DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if command is None:
            return  # Legacy RunClient remains output-table neutral.
        output_json = _object_json(output, "conversation_output")
        output_hash = hashlib.sha256(output_json.encode()).hexdigest()
        existing = connection.execute(
            "SELECT command_id,output_json,output_hash FROM conversation_outputs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != (str(command[0]), output_json, output_hash):
                raise UnitOfWorkConflict("conversation output replay differs")
            return
        connection.execute(
            "INSERT INTO conversation_outputs VALUES (?,?,?,?,?)",
            (run_id, str(command[0]), output_json, output_hash, now),
        )

    @staticmethod
    def _verify_conversation_output_replay(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        output: Mapping[str, JsonValue] | None,
    ) -> None:
        mode = connection.execute(
            "SELECT api_mode FROM conversation_run_modes WHERE run_id=?", (run_id,)
        ).fetchone()
        if mode is None or str(mode[0]) != "command":
            return
        row = connection.execute(
            "SELECT output_json FROM conversation_outputs WHERE run_id=?", (run_id,)
        ).fetchone()
        expected = None if output is None else _object_json(output, "conversation_output")
        actual = None if row is None else str(row[0])
        if actual != expected:
            raise UnitOfWorkConflict("conversation output replay differs")

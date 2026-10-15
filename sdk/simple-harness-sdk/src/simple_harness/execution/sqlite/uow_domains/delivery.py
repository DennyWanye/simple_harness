# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Root terminal commits and outbound delivery lifecycle."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from simple_harness.contracts import JsonValue, canonical_json
from simple_harness.execution.delivery import (
    DeliveryConflictError,
    DeliveryRecord,
    DeliverySpec,
    DeliveryState,
    TerminalCommitResult,
    delivery_payload_json,
)
from simple_harness.execution.fences import RunFenceLease
from simple_harness.execution.uow import (
    ContinuationRecord,
    ContinuationTerminalResult,
    ExecutionLease,
    FaultHook,
    RunState,
    UnitOfWorkConflict,
    UnitOfWorkNotFound,
)

from ..delivery_audit import handoff, settle_attempt
from ..delivery_audit import record_version as _delivery_audit_version
from ..memory_port_audit import record as record_memory_audit

if TYPE_CHECKING:
    from simple_harness.execution.memory_outbox import CommittedTurnSpec
from .core import ExecutionUnitOfWorkCore
from .support import (
    _continuation_progress_receipt,
    _delivery_record,
    _fault,
    _object_json,
    _required,
    _thaw,
    _time,
)


def _insert_committed_turn(
    connection: sqlite3.Connection,
    intent: CommittedTurnSpec,
    *,
    run_id: str,
    now: float,
) -> None:
    turn = intent.turn
    connection.execute(
        "INSERT INTO memory_outbox("
        "intent_id,run_id,turn_id,deployment_id,household_id,actor_id,session_id,"
        "payload_json,payload_hash,state,claim_owner,claim_epoch,claim_expires_at,"
        "attempt_count,retry_at,error_code,created_at,settled_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,'pending',NULL,0,NULL,0,?,NULL,?,NULL)",
        (
            intent.intent_id,
            run_id,
            turn.turn_id,
            turn.identity.deployment_id,
            turn.identity.household_id,
            turn.identity.actor_id,
            turn.identity.session_id,
            intent.payload_json,
            intent.payload_hash,
            now,
            now,
        ),
    )

    record_memory_audit(connection, intent.intent_id, operation="created", now=now)


def _validate_committed_turn(
    connection: sqlite3.Connection,
    intent: CommittedTurnSpec,
    *,
    run_id: str,
) -> None:
    row = connection.execute(
        "SELECT r.execution_session_id,b.deployment_id,b.household_id,b.actor_id "
        "FROM runs AS r JOIN agent_identity_bindings AS b "
        "ON b.session_id=r.execution_session_id WHERE r.run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise UnitOfWorkConflict("committed turn lacks trusted identity binding")
    turn = intent.turn
    if (
        turn.identity.session_id != str(row["execution_session_id"])
        or turn.identity.deployment_id != str(row["deployment_id"])
        or turn.identity.household_id != str(row["household_id"])
        or turn.identity.actor_id != str(row["actor_id"])
    ):
        raise UnitOfWorkConflict("committed turn identity binding differs")
    if intent.payload_json != canonical_json(turn.canonical_payload()):
        raise UnitOfWorkConflict("committed turn canonical payload differs")
    if intent.payload_hash != turn.payload_hash:
        raise UnitOfWorkConflict("committed turn payload hash differs")


def _verify_committed_turn_replay(
    connection: sqlite3.Connection,
    intent: CommittedTurnSpec | None,
    *,
    run_id: str,
) -> None:
    rows = connection.execute(
        "SELECT intent_id,payload_json,payload_hash FROM memory_outbox WHERE run_id=?",
        (run_id,),
    ).fetchall()
    if not rows:
        for event in connection.execute(
            "SELECT payload_json FROM run_events WHERE run_id=? AND kind='run.completed'", (run_id,)
        ):
            anchor = json.loads(event[0]).get("sdk_memory_outbox")
            if isinstance(anchor, dict) and anchor.get("schema_version") == 1:
                expected_hash = None if intent is None else intent.payload_hash
                if anchor.get("committed_turn_hash") == expected_hash:
                    return
                raise UnitOfWorkConflict("committed-turn replay differs")
    if intent is None:
        if rows:
            raise UnitOfWorkConflict("committed-turn replay differs")
        return
    expected = (intent.intent_id, intent.payload_json, intent.payload_hash)
    if len(rows) != 1 or tuple(rows[0]) != expected:
        raise UnitOfWorkConflict("committed-turn replay differs")


def _consume_legacy_cursor(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    terminal_state: RunState,
    committed_turn: CommittedTurnSpec | None,
    expected_version: int | None,
    now: float,
    fault_prefix: str,
    fault: FaultHook | None,
) -> None:
    row = connection.execute(
        "SELECT * FROM legacy_turn_cursors WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        if expected_version is not None:
            raise UnitOfWorkConflict("legacy cursor disappeared")
        return
    if expected_version is None or int(row["cursor_version"]) != expected_version:
        raise UnitOfWorkConflict("legacy cursor version differs")
    if str(row["state"]) != "active":
        raise UnitOfWorkConflict("legacy cursor is not active")
    expected_hash: str | None = None
    if terminal_state is RunState.COMPLETED:
        if committed_turn is None:
            raise UnitOfWorkConflict("completed migrated Run requires committed turn")
        turn = committed_turn.turn
        if (
            turn.turn_id != str(row["turn_id"])
            or turn.user_text != str(row["user_text"])
            or turn.write_fence != (None if row["write_fence"] is None else str(row["write_fence"]))
            or turn.turn_started_at != float(row["turn_started_at"])
        ):
            raise UnitOfWorkConflict("committed turn differs from active legacy cursor")
        expected_hash = committed_turn.payload_hash
    elif committed_turn is not None:
        raise UnitOfWorkConflict("non-completed migrated Run rejects committed turn")
    _fault(fault, f"{fault_prefix}.legacy_cursor.before_write")
    changed = connection.execute(
        "UPDATE legacy_turn_cursors SET state='consumed',consumed_terminal_state=?,"
        "committed_turn_hash=?,updated_at=?,consumed_at=? WHERE run_id=? AND state='active' "
        "AND cursor_version=?",
        (
            terminal_state.value,
            expected_hash,
            now,
            now,
            run_id,
            expected_version,
        ),
    ).rowcount
    if changed != 1:
        raise UnitOfWorkConflict("legacy cursor terminal CAS failed")
    _fault(fault, f"{fault_prefix}.legacy_cursor.after_write")


def _verify_legacy_cursor_replay(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    terminal_state: RunState,
    committed_turn: CommittedTurnSpec | None,
    expected_version: int | None,
) -> None:
    row = connection.execute(
        "SELECT cursor_version,state,consumed_terminal_state,committed_turn_hash "
        "FROM legacy_turn_cursors WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        if expected_version is not None:
            raise UnitOfWorkConflict("legacy cursor replay differs")
        return
    expected_hash = None if committed_turn is None else committed_turn.payload_hash
    if (
        expected_version is None
        or int(row["cursor_version"]) != expected_version
        or str(row["state"]) != "consumed"
        or str(row["consumed_terminal_state"]) != terminal_state.value
        or (None if row["committed_turn_hash"] is None else str(row["committed_turn_hash"]))
        != expected_hash
    ):
        raise UnitOfWorkConflict("legacy cursor replay differs")


class RootTerminalDeliveryMixin(ExecutionUnitOfWorkCore):
    """Root terminal commits and outbound delivery lifecycle."""

    __slots__ = ()

    if TYPE_CHECKING:

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
        ) -> None: ...
        @staticmethod
        def _insert_conversation_output(
            connection: sqlite3.Connection,
            *,
            run_id: str,
            output: Mapping[str, JsonValue] | None,
            now: float,
        ) -> None: ...
        @staticmethod
        def _verify_conversation_output_replay(
            connection: sqlite3.Connection, *, run_id: str, output: Mapping[str, JsonValue] | None
        ) -> None: ...

    def commit_root_terminal_with_deliveries(
        self,
        *,
        run_id: str,
        expected_version: int,
        terminal_state: RunState,
        event_id: str,
        terminal_payload: Mapping[str, JsonValue],
        deliveries: Sequence[DeliverySpec],
        fence: RunFenceLease,
        execution_lease: ExecutionLease,
        terminal_fence_receipt_ref: str,
        now: float,
        committed_turn: CommittedTurnSpec | None = None,
        conversation_output: Mapping[str, JsonValue] | None = None,
        legacy_cursor_version: int | None = None,
        fault: FaultHook | None = None,
    ) -> TerminalCommitResult:
        run_id = _required(run_id, "run_id")
        event_id = _required(event_id, "event_id")
        terminal_fence_receipt_ref = _required(
            terminal_fence_receipt_ref, "terminal_fence_receipt_ref"
        )
        if isinstance(expected_version, bool) or expected_version < 0:
            raise ValueError("expected_version must be non-negative")
        terminal_state = RunState(terminal_state)
        if terminal_state not in {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
        }:
            raise ValueError("root terminal command requires a terminal state")
        if terminal_state is not RunState.COMPLETED and committed_turn is not None:
            raise UnitOfWorkConflict("only COMPLETED may create a committed turn")
        if fence.run_id.value != run_id:
            raise UnitOfWorkConflict("terminal fence belongs to another run")
        now = _time(now)
        payload = dict(terminal_payload)
        payload["sdk_memory_outbox"] = {
            "schema_version": 1,
            "committed_turn_hash": None if committed_turn is None else committed_turn.payload_hash,
        }
        payload["terminal_fence_receipt_ref"] = terminal_fence_receipt_ref
        payload["fence_epoch"] = fence.epoch
        payload_json = _object_json(payload, "terminal_payload")
        items = tuple(deliveries)
        identities = [item.idempotency_key for item in items]
        if len(set(identities)) != len(identities):
            raise DeliveryConflictError("duplicate delivery idempotency key")

        existing_run = self.read_run(run_id)
        if existing_run is None:
            raise UnitOfWorkNotFound(run_id)
        if existing_run.parent_run_id is not None:
            raise UnitOfWorkConflict("only root runs can commit terminal deliveries")
        if existing_run.state in {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
        }:
            existing_event = self.database.connection.execute(
                "SELECT kind, payload_json FROM run_events WHERE event_id = ? AND run_id = ?",
                (event_id, run_id),
            ).fetchone()
            if existing_event is not None:
                existing_payload = json.loads(existing_event["payload_json"])
                if "sdk_memory_outbox" in existing_payload:
                    if existing_payload["sdk_memory_outbox"] != payload["sdk_memory_outbox"]:
                        raise UnitOfWorkConflict("committed-turn replay differs")
                else:
                    # Exact legacy receipt comparison; never restamp its original bytes.
                    legacy_expected = dict(payload)
                    legacy_expected.pop("sdk_memory_outbox")
                    payload_json = _object_json(legacy_expected, "terminal_payload")
            stored = tuple(
                _delivery_record(row)
                for row in self.database.connection.execute(
                    "SELECT * FROM delivery_outbox WHERE run_id = ? ORDER BY delivery_id",
                    (run_id,),
                ).fetchall()
            )
            requested_rows: list[tuple[str, str, str, str]] = []
            for item in items:
                replay_payload = _thaw(item.payload)
                replay_payload["terminal_fence_receipt_ref"] = terminal_fence_receipt_ref
                replay_payload["fence_epoch"] = fence.epoch
                requested_rows.append(
                    (
                        item.delivery_id,
                        item.sink_kind,
                        item.idempotency_key,
                        delivery_payload_json(replay_payload),
                    )
                )
            requested = tuple(sorted(requested_rows))
            actual = tuple(
                sorted(
                    (
                        item.delivery_id,
                        item.sink_kind,
                        item.idempotency_key,
                        canonical_json(_thaw(item.payload)),
                    )
                    for item in stored
                )
            )
            if (
                existing_run.state is terminal_state
                and existing_event is not None
                and str(existing_event["kind"]) == f"run.{terminal_state.value}"
                and str(existing_event["payload_json"]) == payload_json
                and requested == actual
            ):
                _verify_committed_turn_replay(
                    self.database.connection,
                    committed_turn,
                    run_id=run_id,
                )
                _verify_legacy_cursor_replay(
                    self.database.connection,
                    run_id=run_id,
                    terminal_state=terminal_state,
                    committed_turn=committed_turn,
                    expected_version=legacy_cursor_version,
                )
                self._verify_conversation_output_replay(
                    self.database.connection,
                    run_id=run_id,
                    output=conversation_output,
                )
                return TerminalCommitResult(existing_run, stored)
            raise UnitOfWorkConflict("another root terminal intent already won")

        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, execution_lease, now=now)
            if execution_lease.run_id != run_id or execution_lease.owner_id != fence.owner_id:
                raise UnitOfWorkConflict("terminal runtime lease and Run fence differ")
            self._require_run_fence(
                connection,
                fence,
                execution_lease=execution_lease,
            )
            _fault(fault, "root_terminal.event.before_write")
            self._insert_event(
                connection,
                event_id=event_id,
                run_id=run_id,
                kind=f"run.{terminal_state.value}",
                payload=dict(payload),
                now=now,
            )
            _fault(fault, "root_terminal.event.after_write")
            self._close_spawn_child_waits_for_parent_terminal(
                connection,
                parent_run_id=run_id,
                terminal_state=terminal_state,
                terminal_receipt_id=event_id,
                claimed_continuation_ack_receipt_id=None,
                now=now,
                fault=fault,
            )
            for index, item in enumerate(items):
                _fault(fault, f"root_terminal.delivery.{index}.before_write")
                bound_payload = _thaw(item.payload)
                bound_payload["terminal_fence_receipt_ref"] = terminal_fence_receipt_ref
                bound_payload["fence_epoch"] = fence.epoch
                connection.execute(
                    """
                    INSERT INTO delivery_outbox(
                        delivery_id, run_id, sink_kind, idempotency_key,
                        payload_json, state, version, created_at, claimed_at, settled_at
                    ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, NULL, NULL)
                    """,
                    (
                        item.delivery_id,
                        run_id,
                        item.sink_kind,
                        item.idempotency_key,
                        delivery_payload_json(bound_payload),
                        now,
                    ),
                )
                _delivery_audit_version(connection, item.delivery_id, now=now, operation="created")
                _fault(fault, f"root_terminal.delivery.{index}.after_write")
            _consume_legacy_cursor(
                connection,
                run_id=run_id,
                terminal_state=terminal_state,
                committed_turn=committed_turn,
                expected_version=legacy_cursor_version,
                now=now,
                fault_prefix="root_terminal",
                fault=fault,
            )
            if committed_turn is not None:
                _validate_committed_turn(connection, committed_turn, run_id=run_id)
                _fault(fault, "root_terminal.committed_turn.before_write")
                _insert_committed_turn(
                    connection,
                    committed_turn,
                    run_id=run_id,
                    now=now,
                )
                _fault(fault, "root_terminal.committed_turn.after_write")
            _fault(fault, "root_terminal.conversation_output.before_write")
            self._insert_conversation_output(
                connection,
                run_id=run_id,
                output=conversation_output,
                now=now,
            )
            _fault(fault, "root_terminal.conversation_output.after_write")
            _fault(fault, "root_terminal.fence.before_write")
            changed = connection.execute(
                """
                UPDATE run_fences SET state = 'released', released_at = ?
                WHERE run_id = ? AND owner_id = ? AND epoch = ? AND state = 'active'
                """,
                (now, run_id, fence.owner_id, fence.epoch),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("terminal fence release CAS failed")
            _fault(fault, "root_terminal.fence.after_write")
            _fault(fault, "root_terminal.run.before_write")
            changed = connection.execute(
                """
                UPDATE runs SET state = ?, version = version + 1, updated_at = ?
                WHERE run_id = ? AND parent_run_id IS NULL AND version = ?
                  AND state NOT IN ('completed', 'failed', 'cancelled')
                """,
                (terminal_state.value, now, run_id, expected_version),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("root terminal CAS failed")
            connection.execute(
                "UPDATE continuations SET state='quarantined',acked_at=?,"
                "version=version+1 WHERE run_id=? AND state IN ('pending','claimed')",
                (now, run_id),
            )
            _fault(fault, "root_terminal.run.after_write")
        _fault(fault, "root_terminal.after_commit")
        run = self.read_run(run_id)
        assert run is not None
        stored = tuple(
            record for item in items if (record := self.read_delivery(item.delivery_id)) is not None
        )
        return TerminalCommitResult(run, stored)

    def claim_delivery(
        self,
        *,
        sink_kinds: Sequence[str],
        now: float,
        claim_ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> DeliveryRecord | None:
        now = _time(now)
        if not math.isfinite(claim_ttl_seconds) or claim_ttl_seconds <= 0:
            raise ValueError("claim_ttl_seconds must be finite and positive")
        normalized = tuple(_required(item, "sink_kind") for item in sink_kinds)
        if not normalized:
            return None
        placeholders = ",".join("?" for _ in normalized)
        claimed_id: str | None = None
        result = None
        with self.database.transaction() as connection:
            _fault(fault, "delivery_claim.expired.before_write")
            expired = connection.execute(
                "SELECT delivery_id FROM delivery_outbox WHERE state='claimed' AND claimed_at<=?",
                (now - claim_ttl_seconds,),
            ).fetchall()
            connection.execute(
                """
                UPDATE delivery_outbox SET state = 'pending', version = version + 1,
                    claimed_at = NULL
                WHERE state = 'claimed' AND claimed_at <= ?
                """,
                (now - claim_ttl_seconds,),
            )
            for expired_row in expired:
                _delivery_audit_version(
                    connection, expired_row["delivery_id"], now=now, operation="expired"
                )
            _fault(fault, "delivery_claim.expired.after_write")
            row = connection.execute(
                f"""
                SELECT delivery_id, version FROM delivery_outbox
                WHERE state = 'pending' AND sink_kind IN ({placeholders})
                ORDER BY created_at, delivery_id LIMIT 1
                """,
                normalized,
            ).fetchone()
            if row is not None:
                claimed_id = str(row["delivery_id"])
                _fault(fault, "delivery_claim.delivery.before_write")
                changed = connection.execute(
                    """
                    UPDATE delivery_outbox SET state = 'claimed', version = version + 1,
                        claimed_at = ?
                    WHERE delivery_id = ? AND state = 'pending' AND version = ?
                    """,
                    (now, claimed_id, int(row["version"])),
                ).rowcount
                if changed != 1:
                    raise DeliveryConflictError("delivery claim CAS failed")
                _delivery_audit_version(connection, claimed_id, now=now, operation="claimed")
                _fault(fault, "delivery_claim.delivery.after_write")
                result = _delivery_record(
                    connection.execute(
                        "SELECT * FROM delivery_outbox WHERE delivery_id=?", (claimed_id,)
                    ).fetchone()
                )
        _fault(fault, "delivery_claim.after_commit")
        return result

    def record_delivery_handoff(self, delivery_id, *, expected_version, now, fault=None):

        with self.database.transaction() as connection:
            handoff(connection, delivery_id, expected_version=expected_version, now=_time(now))
            result = _delivery_record(
                connection.execute(
                    "SELECT * FROM delivery_outbox WHERE delivery_id=?", (delivery_id,)
                ).fetchone()
            )
        _fault(fault, "delivery_handoff.after_commit")
        return result

    def complete_delivery(
        self,
        delivery_id: str,
        *,
        expected_version: int,
        now: float,
        fault: FaultHook | None = None,
    ) -> DeliveryRecord:
        return self._settle_delivery(
            delivery_id,
            expected_version=expected_version,
            target_state=DeliveryState.DELIVERED,
            now=now,
            fault=fault,
            command="delivery_complete",
        )

    def release_delivery(
        self,
        delivery_id: str,
        *,
        expected_version: int,
        now: float,
        fault: FaultHook | None = None,
        error_code: str | None = None,
    ) -> DeliveryRecord:
        return self._settle_delivery(
            delivery_id,
            expected_version=expected_version,
            target_state=DeliveryState.PENDING,
            now=now,
            fault=fault,
            command="delivery_release",
            error_code=error_code,
        )

    def read_delivery(self, delivery_id: str) -> DeliveryRecord | None:
        row = self.database.connection.execute(
            "SELECT * FROM delivery_outbox WHERE delivery_id = ?", (delivery_id,)
        ).fetchone()
        return None if row is None else _delivery_record(row)

    def _settle_delivery(
        self,
        delivery_id: str,
        *,
        expected_version: int,
        target_state: DeliveryState,
        now: float,
        fault: FaultHook | None,
        command: str,
        error_code: str | None = None,
    ) -> DeliveryRecord:
        delivery_id = _required(delivery_id, "delivery_id")
        now = _time(now)
        existing = self.read_delivery(delivery_id)
        if existing is None:
            raise UnitOfWorkNotFound(delivery_id)
        if existing.state is target_state and existing.version == expected_version + 1:
            return existing
        with self.database.transaction() as connection:
            _fault(fault, f"{command}.delivery.before_write")
            changed = connection.execute(
                """
                UPDATE delivery_outbox SET state = ?, version = version + 1,
                    claimed_at = CASE WHEN ? = 'pending' THEN NULL ELSE claimed_at END,
                    settled_at = CASE WHEN ? = 'delivered' THEN ? ELSE settled_at END
                WHERE delivery_id = ? AND state = 'claimed' AND version = ?
                """,
                (
                    target_state.value,
                    target_state.value,
                    target_state.value,
                    now,
                    delivery_id,
                    expected_version,
                ),
            ).rowcount
            if changed != 1:
                raise DeliveryConflictError("delivery settlement CAS failed")

            settle_attempt(
                connection,
                delivery_id,
                expected_version=expected_version,
                now=now,
                state="completed" if target_state is DeliveryState.DELIVERED else "unknown",
                error_code=error_code,
            )
            _delivery_audit_version(
                connection,
                delivery_id,
                now=now,
                operation="completed" if target_state is DeliveryState.DELIVERED else "released",
            )
            _fault(fault, f"{command}.delivery.after_write")
            result = _delivery_record(
                connection.execute(
                    "SELECT * FROM delivery_outbox WHERE delivery_id=?", (delivery_id,)
                ).fetchone()
            )
        _fault(fault, f"{command}.after_commit")
        return result

    def commit_root_terminal_with_deliveries_and_ack_continuation(
        self,
        *,
        run_id: str,
        expected_version: int,
        terminal_state: RunState,
        event_id: str,
        terminal_payload: Mapping[str, JsonValue],
        deliveries: Sequence[DeliverySpec],
        continuation_claim: ContinuationRecord,
        run_fence: RunFenceLease,
        execution_lease: ExecutionLease,
        receipt_id: str,
        terminal_fence_receipt_ref: str,
        now: float,
        committed_turn: CommittedTurnSpec | None = None,
        conversation_output: Mapping[str, JsonValue] | None = None,
        legacy_cursor_version: int | None = None,
        fault: FaultHook | None = None,
    ) -> ContinuationTerminalResult:
        run_id = _required(run_id, "run_id")
        terminal_state = RunState(terminal_state)
        if terminal_state not in {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
        }:
            raise ValueError("continuation terminal state is invalid")
        if terminal_state is not RunState.COMPLETED and committed_turn is not None:
            raise UnitOfWorkConflict("only COMPLETED may create a committed turn")
        receipt_id = _required(receipt_id, "receipt_id")
        event_id = _required(event_id, "event_id")
        now = _time(now)
        payload = dict(terminal_payload)
        payload["sdk_memory_outbox"] = {
            "schema_version": 1,
            "committed_turn_hash": None if committed_turn is None else committed_turn.payload_hash,
        }
        payload["terminal_fence_receipt_ref"] = terminal_fence_receipt_ref
        payload["fence_epoch"] = run_fence.epoch
        old_terminal = self.database.connection.execute(
            "SELECT payload_json FROM run_events WHERE event_id=? AND run_id=?", (event_id, run_id)
        ).fetchone()
        if old_terminal is not None and "sdk_memory_outbox" not in json.loads(old_terminal[0]):
            payload.pop("sdk_memory_outbox")
        payload_json = _object_json(payload, "terminal_payload")
        items = tuple(deliveries)
        identities = [item.idempotency_key for item in items]
        if len(set(identities)) != len(identities):
            raise DeliveryConflictError("duplicate delivery idempotency key")
        outcome_hash = hashlib.sha256(
            canonical_json(
                {
                    "run_id": run_id,
                    "expected_version": expected_version,
                    "terminal_state": terminal_state.value,
                    "event_id": event_id,
                    "payload": json.loads(payload_json),
                    "deliveries": [
                        {
                            "delivery_id": item.delivery_id,
                            "sink_kind": item.sink_kind,
                            "idempotency_key": item.idempotency_key,
                            "payload": _thaw(item.payload),
                        }
                        for item in items
                    ],
                    "committed_turn_hash": (
                        None if committed_turn is None else committed_turn.payload_hash
                    ),
                    "conversation_output": (
                        None if conversation_output is None else dict(conversation_output)
                    ),
                    "legacy_cursor_version": legacy_cursor_version,
                }
            ).encode()
        ).hexdigest()
        existing = self._read_continuation_progress_receipt(receipt_id)
        if existing is not None:
            self._verify_conversation_output_replay(
                self.database.connection,
                run_id=run_id,
                output=conversation_output,
            )
            _verify_committed_turn_replay(
                self.database.connection,
                committed_turn,
                run_id=run_id,
            )
            _verify_legacy_cursor_replay(
                self.database.connection,
                run_id=run_id,
                terminal_state=terminal_state,
                committed_turn=committed_turn,
                expected_version=legacy_cursor_version,
            )
            return self._replay_continuation_terminal(
                existing,
                continuation_claim=continuation_claim,
                execution_lease=execution_lease,
                outcome_hash=outcome_hash,
            )
        with self.database.transaction() as connection:
            receipt_row = connection.execute(
                "SELECT * FROM continuation_progress_receipts WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone()
            if receipt_row is not None:
                _verify_committed_turn_replay(
                    connection,
                    committed_turn,
                    run_id=run_id,
                )
                _verify_legacy_cursor_replay(
                    connection,
                    run_id=run_id,
                    terminal_state=terminal_state,
                    committed_turn=committed_turn,
                    expected_version=legacy_cursor_version,
                )
                return self._replay_continuation_terminal(
                    _continuation_progress_receipt(receipt_row),
                    continuation_claim=continuation_claim,
                    execution_lease=execution_lease,
                    outcome_hash=outcome_hash,
                )
            prior_row = connection.execute(
                "SELECT receipt_id FROM continuation_progress_receipts WHERE continuation_id=?",
                (continuation_claim.continuation_id,),
            ).fetchone()
            if prior_row is not None:
                raise UnitOfWorkConflict("continuation was already committed with another receipt")
            self._require_runtime_lease(connection, execution_lease, now=now)
            self._require_run_fence(connection, run_fence, execution_lease=execution_lease)
            self._require_continuation_claim(connection, continuation_claim, execution_lease)
            _fault(fault, "continuation_terminal.receipt.before_write")
            connection.execute(
                "INSERT INTO continuation_progress_receipts("
                "receipt_id,continuation_id,run_id,owner_id,runtime_lease_epoch,"
                "claim_epoch,outcome_hash,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    receipt_id,
                    continuation_claim.continuation_id,
                    run_id,
                    execution_lease.owner_id,
                    execution_lease.epoch,
                    continuation_claim.claim_epoch,
                    outcome_hash,
                    now,
                ),
            )
            _fault(fault, "continuation_terminal.receipt.after_write")
            _fault(fault, "continuation_terminal.continuation.before_write")
            changed = connection.execute(
                "UPDATE continuations SET state='acked',acked_at=?,ack_receipt_id=?,"
                "version=version+1 WHERE continuation_id=? AND state='claimed' "
                "AND claimed_by=? AND runtime_lease_epoch=? AND claim_epoch=? AND version=?",
                (
                    now,
                    receipt_id,
                    continuation_claim.continuation_id,
                    execution_lease.owner_id,
                    execution_lease.epoch,
                    continuation_claim.claim_epoch,
                    continuation_claim.version,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("continuation terminal claim CAS failed")
            _fault(fault, "continuation_terminal.continuation.after_write")
            _fault(fault, "continuation_terminal.event.before_write")
            self._insert_event(
                connection,
                event_id=event_id,
                run_id=run_id,
                kind=f"run.{terminal_state.value}",
                payload=payload,
                now=now,
            )
            _fault(fault, "continuation_terminal.event.after_write")
            self._close_spawn_child_waits_for_parent_terminal(
                connection,
                parent_run_id=run_id,
                terminal_state=terminal_state,
                terminal_receipt_id=event_id,
                claimed_continuation_ack_receipt_id=receipt_id,
                now=now,
                fault=fault,
            )
            for index, item in enumerate(items):
                _fault(fault, f"continuation_terminal.delivery.{index}.before_write")
                bound = _thaw(item.payload)
                bound["terminal_fence_receipt_ref"] = terminal_fence_receipt_ref
                bound["fence_epoch"] = run_fence.epoch
                connection.execute(
                    "INSERT INTO delivery_outbox("
                    "delivery_id,run_id,sink_kind,idempotency_key,payload_json,state,"
                    "version,created_at,claimed_at,settled_at) "
                    "VALUES(?,?,?,?,?,'pending',0,?,NULL,NULL)",
                    (
                        item.delivery_id,
                        run_id,
                        item.sink_kind,
                        item.idempotency_key,
                        delivery_payload_json(bound),
                        now,
                    ),
                )
                _delivery_audit_version(connection, item.delivery_id, now=now, operation="created")
                _fault(fault, f"continuation_terminal.delivery.{index}.after_write")
            _consume_legacy_cursor(
                connection,
                run_id=run_id,
                terminal_state=terminal_state,
                committed_turn=committed_turn,
                expected_version=legacy_cursor_version,
                now=now,
                fault_prefix="continuation_terminal",
                fault=fault,
            )
            if committed_turn is not None:
                _validate_committed_turn(connection, committed_turn, run_id=run_id)
                _fault(fault, "continuation_terminal.committed_turn.before_write")
                _insert_committed_turn(
                    connection,
                    committed_turn,
                    run_id=run_id,
                    now=now,
                )
                _fault(fault, "continuation_terminal.committed_turn.after_write")
            _fault(fault, "continuation_terminal.fence.before_write")
            changed = connection.execute(
                "UPDATE run_fences SET state='released',released_at=? "
                "WHERE run_id=? AND owner_id=? AND runtime_lease_epoch=? "
                "AND epoch=? AND state='active'",
                (
                    now,
                    run_id,
                    run_fence.owner_id,
                    run_fence.runtime_lease_epoch,
                    run_fence.epoch,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("continuation terminal fence CAS failed")
            _fault(fault, "continuation_terminal.fence.after_write")
            _fault(fault, "continuation_terminal.conversation_output.before_write")
            self._insert_conversation_output(
                connection,
                run_id=run_id,
                output=conversation_output,
                now=now,
            )
            _fault(fault, "continuation_terminal.conversation_output.after_write")
            _fault(fault, "continuation_terminal.run.before_write")
            changed = connection.execute(
                "UPDATE runs SET state=?,version=version+1,updated_at=? "
                "WHERE run_id=? AND parent_run_id IS NULL AND version=? "
                "AND state NOT IN ('completed','failed','cancelled')",
                (terminal_state.value, now, run_id, expected_version),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("continuation terminal Run CAS failed")
            connection.execute(
                "UPDATE continuations SET state='quarantined',acked_at=?,version=version+1 "
                "WHERE run_id=? AND state IN ('pending','claimed')",
                (now, run_id),
            )
            _fault(fault, "continuation_terminal.run.after_write")
        _fault(fault, "continuation_terminal.after_commit")
        receipt = self._read_continuation_progress_receipt(receipt_id)
        assert receipt is not None
        return self._replay_continuation_terminal(
            receipt,
            continuation_claim=continuation_claim,
            execution_lease=execution_lease,
            outcome_hash=outcome_hash,
        )

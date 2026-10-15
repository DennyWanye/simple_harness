# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Base-agent bindings, turns, journal, batches and delegations."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, cast

from simple_harness.contracts import JsonValue
from simple_harness.execution.base_agent import (
    AgentBindingRecord,
    AgentContextSelectionRecord,
    AgentControlCommandRecord,
    AgentCreationBatchRecord,
    AgentDelegationRecord,
    AgentJournalRecord,
    AgentSummaryRecord,
    AgentTurnRecord,
    AgentTurnResultRecord,
)
from simple_harness.execution.recovery import WaitBlockerRecord
from simple_harness.execution.uow import (
    ContinuationRecord,
    ExecutionLease,
    FaultHook,
    UnitOfWorkConflict,
)

from ..base_agent import batches, control, delegations, history, turns
from .core import ExecutionUnitOfWorkCore
from .support import _fault, _object_json, _required, _thaw_json, _time, _wait_blocker_record


class BaseAgentMixin(ExecutionUnitOfWorkCore):
    """Base-agent bindings, turns, journal, batches and delegations."""

    __slots__ = ()

    if TYPE_CHECKING:

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

    # --- BaseAgent (execution schema v10) --------------------------------------
    # Thin facades; the connection-level logic lives in ``sqlite/base_agent/turns.py``
    # so this module does not keep growing.  Each facade owns exactly one transaction.

    def create_agent_binding(
        self,
        *,
        agent_id: str,
        run_id: str,
        owner_scope: str,
        role: str,
        creation_key: str,
        config_json: Mapping[str, JsonValue],
        config_hash: str,
        now: float,
        max_agents: int | None = None,
    ) -> AgentBindingRecord:

        with self.database.transaction() as connection:
            return turns.insert_binding(
                connection,
                agent_id=_required(agent_id, "agent_id"),
                run_id=_required(run_id, "run_id"),
                owner_scope=_required(owner_scope, "owner_scope"),
                role=role,
                creation_key=_required(creation_key, "creation_key"),
                config_json=config_json,
                config_hash=config_hash,
                now=_time(now),
                max_agents=max_agents,
            )

    def read_agent_binding(self, agent_id: str) -> AgentBindingRecord | None:

        return turns.read_binding(self.database.connection, _required(agent_id, "agent_id"))

    def read_agent_binding_for_run(self, run_id: str) -> AgentBindingRecord | None:

        return turns.read_binding_by_run(self.database.connection, _required(run_id, "run_id"))

    def submit_agent_input(
        self,
        *,
        agent_id: str,
        run_id: str,
        turn_id: str,
        input_id: str,
        input_hash: str,
        input_json: Mapping[str, JsonValue],
        continuation_payload: Mapping[str, JsonValue],
        now: float,
        fault: FaultHook | None = None,
        max_pending_inputs: int | None = None,
    ) -> AgentTurnRecord:
        """One short transaction: queued turn row + its ``base_agent_input`` continuation."""

        def enqueue(connection: sqlite3.Connection, **kwargs: object) -> None:
            self._enqueue_continuation_on_connection(
                connection,
                context_stage_id=None,
                context_stage_hash=None,
                fault=fault,
                **kwargs,  # type: ignore[arg-type]
            )

        with self.database.transaction() as connection:
            return turns.submit_input(
                connection,
                agent_id=_required(agent_id, "agent_id"),
                run_id=_required(run_id, "run_id"),
                turn_id=_required(turn_id, "turn_id"),
                input_id=_required(input_id, "input_id"),
                input_hash=input_hash,
                input_json=input_json,
                continuation_payload=continuation_payload,
                now=_time(now),
                max_pending_inputs=max_pending_inputs,
                enqueue_continuation=enqueue,
                object_json=_object_json,
            )

    def read_agent_turn(self, turn_id: str) -> AgentTurnRecord | None:

        return turns.read_turn(self.database.connection, _required(turn_id, "turn_id"))

    def read_agent_turn_by_input(self, agent_id: str, input_id: str) -> AgentTurnRecord | None:

        return turns.read_turn_by_input(self.database.connection, agent_id, input_id)

    def list_agent_turns(self, agent_id: str) -> tuple[AgentTurnRecord, ...]:

        return turns.list_turns(self.database.connection, _required(agent_id, "agent_id"))

    def read_pending_agent_turn(self, run_id: str) -> AgentTurnRecord | None:
        """The ``result_pending`` turn of the Agent bound to ``run_id``, if any."""

        binding = turns.read_binding_by_run(self.database.connection, _required(run_id, "run_id"))
        if binding is None:
            return None
        return turns.read_pending_turn(self.database.connection, binding.agent_id)

    def read_open_agent_turn(self, run_id: str) -> AgentTurnRecord | None:

        binding = turns.read_binding_by_run(self.database.connection, _required(run_id, "run_id"))
        if binding is None:
            return None
        return turns.read_open_turn(self.database.connection, binding.agent_id)

    def _base_agent_tables_present(self) -> bool:
        """A v9 library (opened before the explicit v10 upgrade) has no BaseAgent tables;
        kernel-wide scans must treat it as "no BaseAgents" instead of failing."""

        row = self.database.connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='base_agent_turns_v1'"
        ).fetchone()
        return row is not None

    def list_runs_with_open_agent_turns(self) -> tuple[str, ...]:

        if not self._base_agent_tables_present():
            return ()

        return turns.list_runs_with_open_turns(self.database.connection)

    def read_agent_turn_result(self, turn_id: str) -> AgentTurnResultRecord | None:

        return turns.read_result(self.database.connection, _required(turn_id, "turn_id"))

    def reserve_child_base_agent_delegation(
        self,
        *,
        delegation_id: str,
        parent_agent_id: str,
        parent_turn_id: str,
        child_agent_id: str,
        child_run_id: str,
        ticket_id: str,
        intent_hash: str,
        now: float,
        max_per_turn: int | None = None,
    ) -> AgentDelegationRecord:
        """Pre-fence the child identity and record the delegation (closure E1/E2)."""

        with self.database.transaction() as connection:
            return delegations.reserve_delegation(
                connection,
                delegation_id=_required(delegation_id, "delegation_id"),
                parent_agent_id=_required(parent_agent_id, "parent_agent_id"),
                parent_turn_id=_required(parent_turn_id, "parent_turn_id"),
                child_agent_id=_required(child_agent_id, "child_agent_id"),
                child_run_id=_required(child_run_id, "child_run_id"),
                ticket_id=_required(ticket_id, "ticket_id"),
                intent_hash=intent_hash,
                context_use_scope=self._context_use_scope,
                now=_time(now),
                max_per_turn=max_per_turn,
            )

    def close_agent(
        self, *, agent_id: str, command_id: str, request_hash: str, now: float
    ) -> AgentControlCommandRecord:
        """One transaction: lifecycle ``open -> closing``, generation +1, command receipt.

        A same-hash replay returns the stored command without touching the binding;
        an already closing/closed Agent gets a receipt that reports the current state
        and does not bump the generation again.
        """

        with self.database.transaction() as connection:
            existing = control.read_control_command(connection, _required(command_id, "command_id"))
            if existing is not None:
                stored, _ = control.record_control_command(
                    connection,
                    command_id=command_id,
                    agent_id=_required(agent_id, "agent_id"),
                    kind="close",
                    target_turn_id=None,
                    control_generation=existing.control_generation,
                    request_hash=request_hash,
                    receipt=cast(Mapping[str, JsonValue], _thaw_json(existing.receipt)),
                    now=_time(now),
                )
                return stored
            lifecycle = turns.read_binding_lifecycle(connection, _required(agent_id, "agent_id"))
            if lifecycle is None:
                raise UnitOfWorkConflict("agent binding is missing")
            if lifecycle == "open":
                binding = turns.set_lifecycle(
                    connection, agent_id=agent_id, lifecycle="closing", now=_time(now)
                )
                generation = control.bump_control_generation(connection, agent_id)
                lifecycle = binding.lifecycle
            else:
                generation = int(
                    connection.execute(
                        "SELECT control_generation FROM base_agent_bindings_v1 WHERE agent_id=?",
                        (agent_id,),
                    ).fetchone()[0]
                )
            stored, _ = control.record_control_command(
                connection,
                command_id=command_id,
                agent_id=agent_id,
                kind="close",
                target_turn_id=None,
                control_generation=generation,
                request_hash=request_hash,
                receipt={"lifecycle": lifecycle, "control_generation": generation},
                now=_time(now),
            )
            return stored

    def request_agent_turn_cancel(
        self, *, agent_id: str, turn_id: str, command_id: str, request_hash: str, now: float
    ) -> AgentControlCommandRecord:
        """Durable cancel intent for one turn (idempotent by ``command_id``).

        Nothing here touches the turn row: the executor that holds the Run lease
        observes the intent cooperatively and fails the turn through the ordinary
        outcome path.  A turn that already settled gets an ``already_settled`` receipt.
        """

        with self.database.transaction() as connection:
            existing = control.read_control_command(connection, _required(command_id, "command_id"))
            if existing is not None:
                stored, _ = control.record_control_command(
                    connection,
                    command_id=command_id,
                    agent_id=_required(agent_id, "agent_id"),
                    kind="cancel_turn",
                    target_turn_id=turn_id,
                    control_generation=existing.control_generation,
                    request_hash=request_hash,
                    receipt=cast(Mapping[str, JsonValue], _thaw_json(existing.receipt)),
                    now=_time(now),
                )
                return stored
            turn = turns.read_turn(connection, _required(turn_id, "turn_id"))
            if turn is None or turn.agent_id != agent_id:
                raise UnitOfWorkConflict("agent turn is missing")
            generation = control.bump_control_generation(connection, agent_id)
            state = "requested" if turn.phase in ("queued", "running") else "already_settled"
            stored, _ = control.record_control_command(
                connection,
                command_id=command_id,
                agent_id=agent_id,
                kind="cancel_turn",
                target_turn_id=turn_id,
                control_generation=generation,
                request_hash=request_hash,
                receipt={
                    "state": state,
                    "turn_phase": turn.phase,
                    "control_generation": generation,
                },
                now=_time(now),
            )
            return stored

    def read_agent_turn_cancel(self, turn_id: str) -> AgentControlCommandRecord | None:

        return control.read_pending_cancel_for_turn(
            self.database.connection, _required(turn_id, "turn_id")
        )

    def mark_agent_closed(self, *, agent_id: str, now: float) -> AgentBindingRecord:
        """``closing -> closed`` only when no turn is still open; returns the binding."""

        with self.database.transaction() as connection:
            if turns.read_open_turn(connection, _required(agent_id, "agent_id")) is not None:
                raise UnitOfWorkConflict("agent still has an open turn")
            return turns.set_lifecycle(
                connection, agent_id=agent_id, lifecycle="closed", now=_time(now)
            )

    def read_agent_control_command(self, command_id: str) -> AgentControlCommandRecord | None:

        return control.read_control_command(
            self.database.connection, _required(command_id, "command_id")
        )

    def reserve_agent_batch(
        self,
        *,
        batch_id: str,
        owner_scope: str,
        batch_key: str,
        batch_fingerprint: str,
        agent_ids: Sequence[str],
        config_hashes: Sequence[str],
        now: float,
    ) -> tuple[AgentCreationBatchRecord, bool]:

        with self.database.transaction() as connection:
            return batches.reserve_batch(
                connection,
                batch_id=_required(batch_id, "batch_id"),
                owner_scope=_required(owner_scope, "owner_scope"),
                batch_key=_required(batch_key, "batch_key"),
                batch_fingerprint=batch_fingerprint,
                agent_ids=agent_ids,
                config_hashes=config_hashes,
                now=_time(now),
            )

    def commit_agent_batch(
        self, *, batch_id: str, receipt: Mapping[str, JsonValue], now: float
    ) -> AgentCreationBatchRecord:

        with self.database.transaction() as connection:
            return batches.commit_batch(
                connection,
                batch_id=_required(batch_id, "batch_id"),
                receipt=receipt,
                now=_time(now),
            )

    def read_agent_batch(self, owner_scope: str, batch_key: str) -> AgentCreationBatchRecord | None:

        return batches.read_batch(
            self.database.connection,
            _required(owner_scope, "owner_scope"),
            _required(batch_key, "batch_key"),
        )

    def count_agent_bindings(self, owner_scope: str) -> int:

        return batches.count_bindings(
            self.database.connection, _required(owner_scope, "owner_scope")
        )

    # ---- Slice 3: session Journal, context selections, summaries -----------------

    def append_agent_journal(
        self,
        *,
        agent_id: str,
        append_id: str,
        append_hash: str,
        expected_highwater: int,
        entries: Sequence[Mapping[str, JsonValue]],
        execution_lease: ExecutionLease,
        now: float,
    ) -> tuple[tuple[AgentJournalRecord, ...], bool, int]:

        now = _time(now)
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, execution_lease, now=now)
            return history.append_records(
                connection,
                agent_id=_required(agent_id, "agent_id"),
                append_id=_required(append_id, "append_id"),
                append_hash=append_hash,
                expected_highwater=expected_highwater,
                entries=entries,
                lease_epoch=execution_lease.epoch,
                now=now,
            )

    def read_agent_journal(
        self, agent_id: str, *, from_seq: int = 1, to_seq: int | None = None
    ) -> tuple[AgentJournalRecord, ...]:

        return history.read_records(
            self.database.connection,
            _required(agent_id, "agent_id"),
            from_seq=from_seq,
            to_seq=to_seq,
        )

    def latest_agent_journal_record(self, agent_id: str, *, kind: str) -> AgentJournalRecord | None:

        return history.latest_record(
            self.database.connection, _required(agent_id, "agent_id"), kind=kind
        )

    def read_agent_journal_group_head(
        self, agent_id: str, *, protocol_group_id: str, before_seq: int
    ) -> tuple[AgentJournalRecord, ...]:

        return history.read_group_head(
            self.database.connection,
            _required(agent_id, "agent_id"),
            protocol_group_id=protocol_group_id,
            before_seq=before_seq,
        )

    def agent_journal_highwater(self, agent_id: str) -> int:

        return history.highwater(self.database.connection, _required(agent_id, "agent_id"))

    def record_agent_context_selection(self, **kwargs: object) -> AgentContextSelectionRecord:

        with self.database.transaction() as connection:
            return history.record_selection(connection, **kwargs)  # type: ignore[arg-type]

    def bind_agent_context_selection_request(
        self,
        *,
        selection_id: str,
        provider_request_id: str,
        request_hash: str,
        request_tokens: int,
        now: float,
    ) -> AgentContextSelectionRecord:

        with self.database.transaction() as connection:
            return history.bind_selection_request(
                connection,
                selection_id=_required(selection_id, "selection_id"),
                provider_request_id=_required(provider_request_id, "provider_request_id"),
                request_hash=request_hash,
                request_tokens=request_tokens,
                now=_time(now),
            )

    def latest_agent_context_selection(self, agent_id: str) -> AgentContextSelectionRecord | None:

        return history.latest_selection(self.database.connection, _required(agent_id, "agent_id"))

    def read_agent_context_selection_by_request(
        self, provider_request_id: str
    ) -> AgentContextSelectionRecord | None:

        return history.read_selection_by_request(
            self.database.connection, _required(provider_request_id, "provider_request_id")
        )

    def upsert_agent_summary(self, **kwargs: object) -> AgentSummaryRecord:

        with self.database.transaction() as connection:
            return history.upsert_summary(connection, **kwargs)  # type: ignore[arg-type]

    def list_agent_summaries(self, agent_id: str) -> tuple[AgentSummaryRecord, ...]:

        return history.list_summaries(self.database.connection, _required(agent_id, "agent_id"))

    # ---- Slice 4: derived indexes (FTS, vectors, index jobs) ----------------------

    def read_agent_delegation(self, delegation_id: str) -> AgentDelegationRecord | None:

        return delegations.read_delegation(
            self.database.connection, _required(delegation_id, "delegation_id")
        )

    def count_agent_delegations(self, parent_turn_id: str) -> int:

        return delegations.count_for_turn(self.database.connection, parent_turn_id)

    def set_agent_delegation_state(
        self, *, delegation_id: str, state: str, now: float
    ) -> AgentDelegationRecord:

        with self.database.transaction() as connection:
            return delegations.set_state(
                connection, delegation_id=delegation_id, state=state, now=_time(now)
            )

    def mark_agent_turn_running(
        self,
        *,
        turn_id: str,
        execution_lease: ExecutionLease,
        provider_turn_ordinal_from: int | None,
        now: float,
        tool_call_ordinal_from: int | None = None,
    ) -> AgentTurnRecord:

        now = _time(now)
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, execution_lease, now=now)
            return turns.mark_turn_running(
                connection,
                turn_id=_required(turn_id, "turn_id"),
                lease_epoch=execution_lease.epoch,
                provider_turn_ordinal_from=provider_turn_ordinal_from,
                now=now,
                tool_call_ordinal_from=tool_call_ordinal_from,
            )

    def list_open_wait_blockers_for_run(self, run_id: str) -> tuple[WaitBlockerRecord, ...]:
        """Unresolved wait blockers of one Run (read model for ``turn_snapshot``)."""

        rows = self.database.connection.execute(
            "SELECT * FROM run_wait_blockers WHERE run_id=? AND resolution_id IS NULL "
            "ORDER BY created_at, blocker_id",
            (_required(run_id, "run_id"),),
        ).fetchall()
        return tuple(_wait_blocker_record(row) for row in rows)

    def stage_agent_turn_result(
        self,
        *,
        turn_id: str,
        result_hash: str,
        result_json: Mapping[str, JsonValue],
        provider_turn_ordinal_from: int | None,
        provider_turn_ordinal_to: int | None,
        execution_lease: ExecutionLease,
        now: float,
        fault: FaultHook | None = None,
    ) -> AgentTurnRecord:
        """RESULT_PENDING: freeze the result under the Run lease in its own short transaction."""

        now = _time(now)
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, execution_lease, now=now)
            _fault(fault, "agent_turn_stage.before_write")
            record = turns.stage_result(
                connection,
                turn_id=_required(turn_id, "turn_id"),
                result_hash=result_hash,
                result_json=result_json,
                provider_turn_ordinal_from=provider_turn_ordinal_from,
                provider_turn_ordinal_to=provider_turn_ordinal_to,
                lease_epoch=execution_lease.epoch,
                now=now,
            )
            _fault(fault, "agent_turn_stage.after_write")
        _fault(fault, "agent_turn_stage.after_commit")
        return record

    def commit_agent_turn_result_and_idle(
        self,
        *,
        run_id: str,
        expected_version: int,
        turn_id: str,
        event_id: str,
        payload: Mapping[str, JsonValue],
        continuation_claim: ContinuationRecord | None,
        execution_lease: ExecutionLease,
        receipt_id: str | None,
        usage_refs: tuple[str, ...] = (),
        now: float,
        fault: FaultHook | None = None,
    ) -> AgentTurnResultRecord:
        """Finalize one AgentTurn in a single transaction (body in ``base_agent.turns``).

        Writes the immutable result row, closes the turn, acks the input
        continuation (progress receipt + ``acked``) when a claim is supplied, and
        commits the Run as WAITING (never terminal).  Replays return the stored row.
        """

        if continuation_claim is not None and receipt_id is None:
            raise ValueError("acking a continuation requires receipt_id")
        existing = turns.read_result(self.database.connection, _required(turn_id, "turn_id"))
        if existing is not None:
            if (
                continuation_claim is not None
                and self._read_continuation_progress_receipt(cast(str, receipt_id)) is None
            ):
                raise UnitOfWorkConflict("agent turn result committed without its ack receipt")
            return existing
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, execution_lease, now=_time(now))
            record = turns.finalize_turn(
                connection,
                run_id=_required(run_id, "run_id"),
                expected_version=expected_version,
                turn_id=turn_id,
                event_id=_required(event_id, "event_id"),
                payload_json=_object_json(payload, "payload"),
                continuation_claim=continuation_claim,
                execution_lease=execution_lease,
                receipt_id=receipt_id,
                usage_refs=usage_refs,
                now=_time(now),
                fault=lambda point: _fault(fault, point),
                require_claim=self._require_continuation_claim,
                insert_event=self._insert_event,
            )
        _fault(fault, "agent_turn_finalize.after_commit")
        return record

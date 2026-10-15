# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Terminalizing runs and releasing their authority and leases."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from simple_harness.contracts import JsonValue, thaw_json
from simple_harness.execution.contracts.children import AttachmentPolicy
from simple_harness.execution.delivery import DeliverySpec
from simple_harness.execution.memory_outbox import CommittedTurnSpec
from simple_harness.execution.uow import ContinuationRecord, RunRecord, RunState, UnitOfWorkConflict
from simple_harness.observability import Outcome

from ..agent_memory import CommittedTurn, MemoryScopeRef
from ..conversation_memory import ConversationContinuationInput, ConversationTurnOutput
from ..start_snapshot import StartSnapshot
from .core import RuntimeCore
from .support import WORKFLOW_DRIVER_KIND, DriverCancellationRecovery, logger


class RunTerminationMixin(RuntimeCore):
    """Terminalizing runs and releasing their authority and leases."""

    __slots__ = ()

    async def _terminalize_cancelled(
        self,
        run: RunRecord,
        *,
        snapshot: StartSnapshot | None = None,
        reason: str = "user",
    ) -> None:
        if snapshot is None:
            raw = self._uow.read_start_snapshot(run.run_id)
            if raw is None:
                raise UnitOfWorkConflict("cancelled Run has no durable start snapshot")
            snapshot = StartSnapshot.from_json(raw)
        if snapshot.driver_kind == WORKFLOW_DRIVER_KIND:
            coordinator = self._cancellation_coordinators.get(WORKFLOW_DRIVER_KIND)
            if coordinator is None:
                raise UnitOfWorkConflict("workflow cancellation coordinator is not registered")
            if run.run_id not in self._leases or run.run_id not in self._fences:
                await self._activate(run.run_id)
            outcome = await coordinator.cancel(
                run,
                snapshot,
                reason=reason,
                now=self._now(),
                recovery=DriverCancellationRecovery(
                    self._leases[run.run_id], self._fences[run.run_id]
                ),
            )
            if outcome.terminal and not self._uow.verify_workflow_cancel_terminal(
                run_id=run.run_id,
                cancel_id=outcome.cancel_id,
                generation=outcome.generation,
            ):
                raise UnitOfWorkConflict("workflow cancel terminal receipt is not durable")
            self._drop_local_authority(run.run_id)
            return
        if run.run_id not in self._fences:
            await self._activate(run.run_id)
        self._terminalize(
            run,
            state=RunState.CANCELLED,
            payload={"code": "cancelled", "public_message": "The Run was cancelled."},
            deliveries=(),
        )

    def _terminalize(
        self,
        run: RunRecord,
        *,
        state: RunState,
        payload: Mapping[str, JsonValue],
        deliveries: Sequence[DeliverySpec],
        continuation_claim: ContinuationRecord | None = None,
        conversation_output: ConversationTurnOutput | None = None,
    ) -> None:
        raw_snapshot = self._uow.read_start_snapshot(run.run_id)
        snapshot = None if raw_snapshot is None else StartSnapshot.from_json(raw_snapshot)
        committed_turn: CommittedTurnSpec | None = None
        legacy_cursor = self._uow.read_legacy_turn_cursor(run.run_id)
        legacy_cursor_version = (
            None
            if legacy_cursor is None or legacy_cursor.state != "active"
            else legacy_cursor.cursor_version
        )
        if (
            self._ports.agent_memory is not None
            and snapshot is not None
            and snapshot.conversation is not None
        ):
            if state is RunState.COMPLETED and conversation_output is None:
                raise UnitOfWorkConflict(
                    "completed conversation requires typed conversation_output"
                )
            if state is not RunState.COMPLETED and conversation_output is not None:
                raise UnitOfWorkConflict("non-completed conversation rejects conversation_output")
            if conversation_output is not None and conversation_output.memory_text is not None:
                if legacy_cursor_version is not None:
                    assert legacy_cursor is not None
                    user_text: str | None = legacy_cursor.user_text
                    turn_id: str = legacy_cursor.turn_id
                    assert user_text is not None
                    committed_turn = CommittedTurnSpec.from_domain(
                        CommittedTurn(
                            turn_id,
                            snapshot.conversation.identity,
                            user_text,
                            conversation_output.memory_text,
                            MemoryScopeRef.personal(snapshot.conversation.identity.actor_id),
                            legacy_cursor.write_fence,
                            legacy_cursor.turn_started_at,
                        )
                    )
                elif continuation_claim is None:
                    user_text = snapshot.conversation.memory_text
                    turn_id = snapshot.turn_id
                    stage_where = "consumed_run_id=?"
                    stage_identity = run.run_id
                else:
                    continuation_payload = thaw_json(continuation_claim.payload)
                    if not isinstance(continuation_payload, dict):
                        raise UnitOfWorkConflict("conversation continuation payload is invalid")
                    raw_conversation = continuation_payload.get("conversation")
                    if not isinstance(raw_conversation, Mapping):
                        raise UnitOfWorkConflict("conversation continuation value is invalid")
                    continuation_value = ConversationContinuationInput.from_json(raw_conversation)
                    user_text = continuation_value.memory_text
                    turn_id = continuation_claim.continuation_id
                    stage_where = "consumed_continuation_id=?"
                    stage_identity = continuation_claim.continuation_id
                if legacy_cursor_version is None and user_text is not None:
                    staging = self._ports.context_staging
                    if staging is None:
                        raise UnitOfWorkConflict("committed turn lacks Context staging")
                    stage = staging.database.connection.execute(
                        "SELECT memory_write_fence,turn_started_at "
                        f"FROM context_preparation_staging WHERE {stage_where}",
                        (stage_identity,),
                    ).fetchone()
                    if stage is None or stage["turn_started_at"] is None:
                        raise UnitOfWorkConflict("committed turn lacks durable recall lineage")
                    committed_turn = CommittedTurnSpec.from_domain(
                        CommittedTurn(
                            turn_id,
                            snapshot.conversation.identity,
                            user_text,
                            conversation_output.memory_text,
                            MemoryScopeRef.personal(snapshot.conversation.identity.actor_id),
                            (
                                None
                                if stage["memory_write_fence"] is None
                                else str(stage["memory_write_fence"])
                            ),
                            float(stage["turn_started_at"]),
                        )
                    )
        event = (
            "run.complete"
            if state is RunState.COMPLETED
            else "run.fail"
            if state is RunState.FAILED
            else "run.cancelled"
        )
        logger.info(
            event,
            extra={
                "run_id": run.run_id,
                "state": state.value,
                "payload_keys": list(payload)[:10],
            },
        )
        fence = self._fences[run.run_id]
        if run.parent_run_id is not None:
            if continuation_claim is not None:
                raise UnitOfWorkConflict("child continuation terminalization has no atomic command")
            committed = self._uow.read_child_terminal_result_for_run(run.run_id)
            if committed is not None:
                if committed.terminal_state != state.value:
                    raise UnitOfWorkConflict(
                        "workflow child terminal result differs from Driver result"
                    )
                self._drop_local_authority(run.run_id)
                self._emit_run_terminal(run, state, replayed=True)
                return
            command = self._uow.read_child_command_for_run(run.run_id)
            if command is None:
                raise UnitOfWorkConflict("child Run has no durable launch command")
            identity = f"{run.run_id}:{run.version}:{state.value}"
            policy = self._uow.read_child_attachment_policy(run.run_id)
            terminal_payload: dict[str, JsonValue] = {
                "status": state.value,
                "result": dict(payload),
            }
            if policy is AttachmentPolicy.DETACHED:
                self._uow.commit_detached_child_terminal(
                    command_id=command.command_id,
                    expected_child_version=run.version,
                    terminal_state=state,
                    terminal_payload=terminal_payload,
                    event_id=f"{identity}:event",
                    receipt_id=f"{identity}:receipt",
                    run_fence=fence,
                    execution_lease=self._leases[run.run_id],
                    now=self._now(),
                )
            else:
                self._uow.finalize_child_and_enqueue_parent_signal(
                    command_id=command.command_id,
                    expected_child_version=run.version,
                    terminal_state=state,
                    signal_id=f"{identity}:signal",
                    signal_payload=terminal_payload,
                    event_id=f"{identity}:event",
                    receipt_id=f"{identity}:receipt",
                    run_fence=fence,
                    execution_lease=self._leases[run.run_id],
                    now=self._now(),
                )
            self._emit_run_terminal(run, state)
            self._fences.pop(run.run_id, None)
            return
        if continuation_claim is not None:
            identity = (
                f"{run.run_id}:terminal:continuation:"
                f"{continuation_claim.continuation_id}:"
                f"{continuation_claim.claim_epoch}:{state.value}"
            )
            self._uow.commit_root_terminal_with_deliveries_and_ack_continuation(
                run_id=run.run_id,
                expected_version=run.version,
                terminal_state=state,
                event_id=f"{identity}:event",
                terminal_payload=payload,
                deliveries=deliveries,
                continuation_claim=continuation_claim,
                run_fence=fence,
                execution_lease=self._leases[run.run_id],
                receipt_id=f"{identity}:receipt",
                terminal_fence_receipt_ref=(f"runtime-fence:{fence.owner_id}:{fence.epoch}"),
                now=self._now(),
                committed_turn=committed_turn,
                conversation_output=(
                    None if conversation_output is None else conversation_output.to_json()
                ),
                legacy_cursor_version=legacy_cursor_version,
            )
            self._emit_run_terminal(run, state)
            self._fences.pop(run.run_id, None)
            if deliveries:
                self._delivery_wake.set()
            if committed_turn is not None:
                self._memory_wake.set()
            return
        self._terminal.commit(
            run,
            state=state,
            payload=payload,
            deliveries=deliveries,
            fence=fence,
            execution_lease=self._leases[run.run_id],
            now=self._now(),
            committed_turn=committed_turn,
            conversation_output=conversation_output,
            legacy_cursor_version=legacy_cursor_version,
        )
        self._emit_run_terminal(run, state)
        self._fences.pop(run.run_id, None)
        if deliveries:
            self._delivery_wake.set()
        if committed_turn is not None:
            self._memory_wake.set()

    def _emit_run_terminal(
        self, run: RunRecord, state: RunState, *, replayed: bool = False
    ) -> None:
        self._emit_transition(
            "run.terminal",
            run_id=run.run_id,
            outcome=Outcome.TERMINAL,
            operation="run_lifecycle",
            attributes={
                "entity_kind": "run",
                "entity_id": run.run_id,
                "run_id": run.run_id,
                "from_state": run.state.value,
                "to_state": state.value,
                "state_version": run.version + (0 if replayed else 1),
                "replayed": replayed,
                "error_code": None if state is RunState.COMPLETED else state.value,
            },
        )

    async def _abandon_run_authority(self, run_id: str) -> None:
        fence = self._fences.pop(run_id, None)
        if fence is not None:
            try:
                await self._uow.release(fence)
            except UnitOfWorkConflict:
                pass
        self._release_runtime_lease(run_id)

    def _release_runtime_lease(self, run_id: str) -> None:
        lease = self._leases.pop(run_id, None)
        self._workflow_spawn_ready_activations.pop(run_id, None)
        self._workflow_start_dispatches.pop(run_id, None)
        self._workflow_recovery_work.pop(run_id, None)
        heartbeat = self._heartbeats.pop(run_id, None)
        if heartbeat is not None and heartbeat is not asyncio.current_task():
            heartbeat.cancel()
        self._cancels.pop(run_id, None)
        if lease is None:
            return
        try:
            self._uow.release_runtime_lease(lease, now=self._now())
        except UnitOfWorkConflict:
            pass

    def _drop_local_authority(self, run_id: str) -> None:
        self._leases.pop(run_id, None)
        self._fences.pop(run_id, None)
        self._workflow_spawn_ready_activations.pop(run_id, None)
        self._workflow_start_dispatches.pop(run_id, None)
        self._workflow_recovery_work.pop(run_id, None)
        heartbeat = self._heartbeats.pop(run_id, None)
        if heartbeat is not None and heartbeat is not asyncio.current_task():
            heartbeat.cancel()
        self._cancels.pop(run_id, None)

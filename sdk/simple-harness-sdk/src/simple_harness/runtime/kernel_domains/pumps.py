# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Background pumps: commands, deliveries, memory, resolved waits, child signals and spawn readiness."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import cast

from simple_harness.contracts import (
    ExecutionSessionId,
    FrozenJsonValue,
    HarnessError,
    JsonValue,
    RunId,
    thaw_json,
)
from simple_harness.execution.command_ingress import CommandClaim
from simple_harness.execution.context_staging import ContextStageKind, ContextStageRecord
from simple_harness.execution.sqlite.stage_audit import begin_release, settle_release
from simple_harness.execution.uow import (
    RUNTIME_LEASE_NAMESPACE,
    RunState,
    UnitOfWorkConflict,
    UnitOfWorkNotFound,
)
from simple_harness.providers import CancelToken

from ..commands import (
    CommandError,
    CommandErrorCode,
    CommandState,
    ContinueCommandIntent,
    StartCommandIntent,
    command_intent_from_json,
)
from ..conversation_memory import ContextPreparationMode, ConversationTurnInput
from ..orchestration import WorkflowSpawnReadyActivation
from ..start_snapshot import RunStart, StartSnapshot, bind_start_snapshot
from .core import RuntimeCore
from .support import RuntimeLifecycleState, logger


class RuntimePumpMixin(RuntimeCore):
    """Background pumps: commands, deliveries, memory, resolved waits, child signals and spawn readiness."""

    __slots__ = ()

    async def dispatch_deliveries_once(self) -> bool:
        self._require_started()
        return await self._ports.delivery.run_once()

    async def _wake_drain(self) -> None:
        interval = min(0.05, max(0.001, self._ports.lease_ttl_seconds / 3.0))
        try:
            while self._state is RuntimeLifecycleState.READY:
                await self._drain_resolved_waits_once()
                for run_id in tuple(self._pending_wakes):
                    if run_id in self._leases:
                        self._pending_wakes.discard(run_id)
                        continue
                    if run_id in self._live.active_run_ids():
                        # The previous drive is still unwinding (e.g. a BaseAgent
                        # driver exception dropping its authority): keep the wake for
                        # the next tick instead of losing it.
                        continue
                    await self._wake_continuation(run_id)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return

    async def _drain_commands_bounded(self, limit: int) -> bool:
        for _ in range(limit):
            claim = self._uow.claim_next_command(
                owner_id=self._ports.owner_id,
                now=self._now(),
                lease_seconds=self._ports.lease_ttl_seconds,
            )
            if claim is None:
                return True
            heartbeat = asyncio.create_task(
                self._command_heartbeat(claim),
                name=f"simple-harness-command-heartbeat:{claim.receipt.command_id}",
            )
            try:
                await self._process_command(claim)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                try:
                    current = self._uow.get_command_receipt(claim.receipt.command_id)
                    if current.state.terminal:
                        continue
                    definite = isinstance(
                        error, (TypeError, ValueError, HarnessError, CommandError)
                    )
                    if definite or claim.attempt_count >= 8:
                        self._uow.reject_command(
                            claim,
                            error_code=(
                                CommandErrorCode.PERMANENT_FAILURE
                                if definite
                                else CommandErrorCode.RETRY_EXHAUSTED
                            ),
                            now=self._now(),
                        )
                    else:
                        self._uow.retry_command(
                            claim,
                            error_code=CommandErrorCode.TRANSIENT_FAILURE.value,
                            retry_at=self._now() + min(60.0, 2.0**claim.attempt_count),
                            now=self._now(),
                        )
                except CommandError as settlement_error:
                    if settlement_error.code is not CommandErrorCode.INTENT_CONFLICT:
                        logger.warning(
                            "command.settlement_failed",
                            extra={
                                "command_id": claim.receipt.command_id,
                                "run_id": claim.receipt.run_id.value,
                                "error_code": settlement_error.code.value,
                            },
                        )
                except Exception:
                    logger.warning(
                        "command.settlement_failed",
                        extra={
                            "command_id": claim.receipt.command_id,
                            "run_id": claim.receipt.run_id.value,
                            "error_code": CommandErrorCode.TRANSIENT_FAILURE.value,
                        },
                    )
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
        return False

    async def _command_heartbeat(self, claim: CommandClaim) -> None:
        interval = max(0.01, self._ports.lease_ttl_seconds / 3.0)
        try:
            while True:
                await asyncio.sleep(interval)
                self._uow.heartbeat_command(
                    claim,
                    now=self._now(),
                    lease_seconds=self._ports.lease_ttl_seconds,
                )
        except (asyncio.CancelledError, Exception):
            return

    async def _command_pump(self) -> None:
        backoff = 0.01
        try:
            while self._state is RuntimeLifecycleState.READY:
                try:
                    await asyncio.wait_for(self._command_wake.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                self._command_wake.clear()
                empty = await self._drain_commands_bounded(100)
                if empty:
                    backoff = min(1.0, backoff * 2)
                else:
                    backoff = 0.01
                    self._command_wake.set()
        except asyncio.CancelledError:
            return

    async def _process_command(self, claim: CommandClaim) -> None:
        raw = json.loads(claim.raw_payload_json)
        if not isinstance(raw, Mapping):
            raise TypeError("durable command payload is corrupt")
        intent = command_intent_from_json(raw)
        state = claim.receipt.state
        if state is CommandState.ACCEPTED:
            self._uow.transition_command(
                claim,
                expected=CommandState.ACCEPTED,
                target=CommandState.CONTEXT_CALL_INTENT,
                now=self._now(),
            )
            state = CommandState.CONTEXT_CALL_INTENT
        if isinstance(intent, StartCommandIntent):
            await self._process_start_command(claim, intent, state)
        elif isinstance(intent, ContinueCommandIntent):
            await self._process_continue_command(claim, intent, state)
        else:
            if state is CommandState.CONTEXT_CALL_INTENT:
                self._uow.transition_command(
                    claim,
                    expected=state,
                    target=CommandState.CONTEXT_READY,
                    now=self._now(),
                )
            self._uow.apply_cancel_command(
                claim,
                event_id=f"{intent.command_id}:cancel",
                now=self._now(),
            )
            current = self._uow.read_run(intent.run_id.value)
            if current is not None:
                token = self._cancels.get(intent.run_id.value)
                if token is not None:
                    token.cancel()
                if intent.run_id.value in self._live.active_run_ids():
                    await self._live.cancel(intent.run_id.value)
                latest = self._uow.read_run(intent.run_id.value)
                if latest is not None and latest.state is RunState.CANCEL_REQUESTED:
                    await self._terminalize_cancelled(latest)

    async def _process_start_command(
        self, claim: CommandClaim, intent: StartCommandIntent, state: CommandState
    ) -> None:
        if intent.profile_key != self._root_profile_key:
            raise UnitOfWorkConflict("command profile differs from Runtime root")
        profile = self._profiles[self._root_profile_key]
        driver = self._drivers[profile.driver_kind]
        stage: ContextStageRecord | None = None
        if self._ports.agent_memory is not None:
            stage = await self.client._prepare_agent_context(
                kind=ContextStageKind.ROOT,
                identity_key=intent.run_id.value,
                root_run_id=intent.run_id.value,
                continuation_id=None,
                turn_id=intent.turn_id,
                value=intent.conversation,
            )
            if stage.private_snapshot is None or stage.private_snapshot_hash is None:
                raise UnitOfWorkConflict("prepared command context is unavailable")
        if state is CommandState.CONTEXT_CALL_INTENT:
            self._uow.transition_command(
                claim,
                expected=state,
                target=CommandState.CONTEXT_READY,
                now=self._now(),
            )
        start = RunStart(
            ExecutionSessionId(intent.conversation.identity.session_id),
            intent.run_id,
            intent.request_id,
            intent.turn_id,
            (
                {"messages": [intent.conversation.message.to_dict()]}
                if not intent.input
                else cast(
                    Mapping[str, JsonValue],
                    thaw_json(cast(FrozenJsonValue, intent.input)),
                )
            ),
            intent.tool_catalog_generation,
            intent.tool_catalog_fingerprint,
            conversation=intent.conversation,
            context_preparation_mode=(
                None if stage is None else ContextPreparationMode.SDK_PREPARED
            ),
            context_stage_id=None if stage is None else stage.stage_id,
            context_stage_hash=None if stage is None else stage.private_snapshot_hash,
            prepared_context=None if stage is None else stage.private_snapshot,
        )
        verdict = await self._ports.admission.evaluate(start)
        if not verdict.allowed:
            raise HarnessError("admission_denied", "The Run was denied by admission.")
        snapshot = bind_start_snapshot(
            start,
            profile_key=self._root_profile_key,
            driver_kind=profile.driver_kind,
            policy_fingerprint=getattr(driver, "policy_fingerprint", None),
        )
        created = self._uow.apply_start_command(
            claim,
            execution_session_id=start.execution_session_id.value,
            request_id=start.request_id.value,
            profile_key=self._root_profile_key,
            driver_kind=profile.driver_kind,
            snapshot=snapshot.to_json(),
            event_id=f"{start.run_id.value}:created",
            now=self._now(),
            user_id=intent.conversation.user_id,
            context_stage_id=start.context_stage_id,
            context_stage_hash=start.context_stage_hash,
        )
        if created.state not in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
            activated = await self._activate(created.run_id)
            self._schedule(activated.run_id)

    async def _process_continue_command(
        self, claim: CommandClaim, intent: ContinueCommandIntent, state: CommandState
    ) -> None:
        start_raw = self._uow.read_start_snapshot(intent.run_id.value)
        if start_raw is None:
            raise UnitOfWorkNotFound(intent.run_id.value)
        start = StartSnapshot.from_json(start_raw)
        stage: ContextStageRecord | None = None
        if self._ports.agent_memory is not None:
            if start.conversation is None:
                raise UnitOfWorkConflict("command Run has no conversation identity")
            stage = await self.client._prepare_agent_context(
                kind=ContextStageKind.CONTINUATION,
                identity_key=intent.continuation_id,
                root_run_id=intent.run_id.value,
                continuation_id=intent.continuation_id,
                turn_id=intent.turn_id,
                value=ConversationTurnInput(
                    start.conversation.identity,
                    intent.conversation.message,
                    intent.conversation.memory_text,
                    start.conversation.recall_scopes,
                    intent.conversation.context_source_snapshot_ref,
                ),
            )
            if stage.private_snapshot is None or stage.private_snapshot_hash is None:
                raise UnitOfWorkConflict("prepared continuation command context is unavailable")
        if state is CommandState.CONTEXT_CALL_INTENT:
            self._uow.transition_command(
                claim,
                expected=state,
                target=CommandState.CONTEXT_READY,
                now=self._now(),
            )
        self._uow.apply_continue_command(
            claim,
            continuation_id=intent.continuation_id,
            payload={
                "kind": "conversation_user",
                "context_use_turn_id": intent.turn_id,
                "conversation": intent.conversation.to_json(),
                **(
                    {}
                    if stage is None or stage.private_snapshot is None
                    else {"prepared_context": dict(stage.private_snapshot)}
                ),
            },
            now=self._now(),
            context_stage_id=None if stage is None else stage.stage_id,
            context_stage_hash=None if stage is None else stage.private_snapshot_hash,
        )
        await self._wake_continuation(intent.run_id.value)

    async def _drain_deliveries_bounded(self, limit: int) -> bool:
        run_once = getattr(self._ports.delivery, "run_once", None)
        if run_once is None:
            return True
        for _ in range(limit):
            if not await run_once():
                return True
        return False

    async def _delivery_pump(self) -> None:
        backoff = 0.01
        try:
            while self._state is RuntimeLifecycleState.READY:
                try:
                    await asyncio.wait_for(self._delivery_wake.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                self._delivery_wake.clear()
                empty = await self._drain_deliveries_bounded(100)
                await self._drain_recall_releases(100)
                if empty:
                    backoff = min(1.0, backoff * 2)
                else:
                    backoff = 0.01
                    self._delivery_wake.set()
        except asyncio.CancelledError:
            return

    async def _drain_recall_releases(self, limit: int) -> None:
        memory = self._ports.agent_memory
        staging = self._ports.context_staging
        if memory is None or staging is None:
            return
        now = self._now()
        rows = staging.database.connection.execute(
            "SELECT release_id,query_id,query_hash,result_id,result_hash,write_fence,"
            "attempt_count "
            "FROM memory_recall_releases WHERE state='pending' AND retry_at<=? "
            "ORDER BY retry_at,release_id LIMIT ?",
            (now, limit),
        ).fetchall()

        for row in rows:
            with staging.database.transaction() as connection:
                pending = begin_release(connection, str(row[0]), now=self._now())
            if pending is None:
                continue
            call, request = pending
            try:
                await asyncio.wait_for(memory.release_recall(request), timeout=1.0)
            except Exception:
                with staging.database.transaction() as connection:
                    settle_release(connection, str(row[0]), call, now=self._now(), returned=False)
            else:
                with staging.database.transaction() as connection:
                    settle_release(connection, str(row[0]), call, now=self._now(), returned=True)

    async def _drain_memory_bounded(self, limit: int) -> bool:
        dispatcher = self._ports.memory_dispatcher
        if dispatcher is None:
            return True
        return await dispatcher.drain(limit=limit)

    async def _memory_pump(self) -> None:
        dispatcher = self._ports.memory_dispatcher
        if dispatcher is None:
            return
        backoff = 0.01
        try:
            while self._state is RuntimeLifecycleState.READY:
                try:
                    await asyncio.wait_for(self._memory_wake.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                self._memory_wake.clear()
                progressed = await dispatcher.run_once()
                backoff = 0.01 if progressed else min(1.0, backoff * 2)
        except asyncio.CancelledError:
            return

    async def _drain_resolved_waits_once(self) -> None:
        await self._drain_child_signals_once()
        for blocker in self._uow.list_resolved_wait_blockers(
            owner_id=self._ports.owner_id,
            namespace=RUNTIME_LEASE_NAMESPACE,
            now=self._now(),
        ):
            try:
                run, lease, _receipt = self._uow.consume_resolved_wait_and_claim_activation(
                    blocker_id=blocker.blocker_id,
                    owner_id=self._ports.owner_id,
                    namespace=RUNTIME_LEASE_NAMESPACE,
                    now=self._now(),
                    lease_ttl_seconds=self._ports.lease_ttl_seconds,
                )
                fence = await self._uow.acquire(RunId(run.run_id), lease, now=self._now())
            except UnitOfWorkConflict:
                continue
            self._leases[run.run_id] = lease
            self._fences[run.run_id] = fence
            self._cancels.setdefault(run.run_id, CancelToken())
            heartbeat = self._heartbeats.get(run.run_id)
            if heartbeat is None or heartbeat.done():
                self._heartbeats[run.run_id] = asyncio.create_task(
                    self._heartbeat(run.run_id),
                    name=f"simple-harness-heartbeat:{run.run_id}",
                )
            if run.run_id not in self._live.active_run_ids():
                self._schedule(run.run_id)
        await self._drain_spawn_ready_once()

    async def _drain_child_signals_once(self) -> None:
        active_child_parents = set(self._child_signal_wait_handoffs)
        for active_run_id in self._live.active_run_ids():
            active_run = self._uow.read_run(active_run_id)
            if active_run is not None and active_run.parent_run_id is not None:
                active_child_parents.add(active_run.parent_run_id)
        for result in self._child_signals.reconcile_all(
            now=self._now(), blocked_parent_run_ids=active_child_parents
        ):
            run_id = result.signal.parent_run_id
            if run_id not in self._leases:
                try:
                    await self._activate(run_id)
                except UnitOfWorkConflict:
                    continue
            if run_id not in self._live.active_run_ids():
                self._schedule(run_id)

    async def _drain_spawn_ready_once(self) -> None:
        cursor: str | None = None
        while True:
            ready_values, cursor = self._uow.list_ready_spawn_continuations(cursor, limit=100)
            for ready in ready_values:
                blocker = self._uow.read_spawn_ready_blocker(ready)
                if blocker is None:
                    continue
                try:
                    activation = await self._uow.run_atomic(
                        lambda tx, ready=ready, blocker=blocker: (  # type: ignore[misc]
                            self._uow.consume_spawn_ready_and_claim_activation(
                                tx,
                                ready,
                                blocker,
                                self._ports.owner_id,
                                now=self._now(),
                                ttl_seconds=self._ports.lease_ttl_seconds,
                            )
                        ),
                        fault_label="runtime:spawn_ready:consume",
                    )
                except UnitOfWorkConflict:
                    continue
                self._register_spawn_ready_activation(activation)
                run_id = activation.execution_lease.run_id
                if run_id not in self._live.active_run_ids():
                    self._schedule(run_id)
            if cursor is None:
                return

    def _register_spawn_ready_activation(self, activation: WorkflowSpawnReadyActivation) -> None:
        run_id = activation.execution_lease.run_id
        self._workflow_spawn_ready_activations[run_id] = activation
        self._leases[run_id] = activation.execution_lease
        self._fences[run_id] = activation.run_fence
        self._cancels.setdefault(run_id, CancelToken())
        heartbeat = self._heartbeats.get(run_id)
        if heartbeat is None or heartbeat.done():
            self._heartbeats[run_id] = asyncio.create_task(
                self._heartbeat(run_id),
                name=f"simple-harness-heartbeat:{run_id}",
            )

    async def _release_child_signal_wait_handoff(self, parent_run_id: str, handoff: object) -> None:
        await asyncio.sleep(0)
        if self._child_signal_wait_handoffs.get(parent_run_id) is handoff:
            self._child_signal_wait_handoffs.pop(parent_run_id, None)

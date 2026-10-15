# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Driving a run: start, activation, heartbeat, scheduling, the drive loop and agent-turn commits."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from simple_harness.contracts import HarnessError, JsonValue, RunId, thaw_json
from simple_harness.execution.runtime_audit import runtime_operation
from simple_harness.execution.sqlite import SqliteExecutionUnitOfWork
from simple_harness.execution.uow import (
    RUNTIME_LEASE_NAMESPACE,
    ContinuationRecord,
    ContinuationState,
    DecisionState,
    RunRecord,
    RunState,
    UnitOfWorkConflict,
)
from simple_harness.observability import Outcome
from simple_harness.providers import CancelToken
from simple_harness.tools.executor import ToolAuthorizationPending
from simple_harness.tools.sidecar import resource_digest

from ..agent_turn import AgentTurnOutcome
from ..start_snapshot import RunStart, StartSnapshot, bind_start_snapshot
from ..terminal import ToolCatalogStale
from .core import RuntimeCore
from .support import (
    BASE_AGENT_DRIVER_EXCEPTION_WAKES,
    BASE_AGENT_DRIVER_KIND,
    WORKFLOW_DRIVER_KIND,
    DriverInvocation,
    _run_id,
    logger,
)


class RunDrivingMixin(RuntimeCore):
    """Driving a run: start, activation, heartbeat, scheduling, the drive loop and agent-turn commits."""

    __slots__ = ()

    async def _start_run(self, start: RunStart) -> RunRecord:
        self._require_started()
        host_control = start.start_mode == "host_control"
        if (
            self._ports.conversation_memory_enabled
            and start.conversation is None
            and not host_control
        ):
            raise HarnessError(
                "conversation_envelope_required",
                "Enabled conversation Memory requires an explicit envelope.",
            )
        if (
            self._ports.conversation_memory_enabled
            and not host_control
            and (start.context_stage_id is None or start.prepared_context is None)
        ):
            raise HarnessError(
                "context_stage_required",
                "Enabled conversation Memory requires durable prepared context.",
            )
        if (
            self._ports.conversation_memory_enabled
            and not host_control
            and (start.context_preparation_mode is not self._ports.context_preparation_mode)
        ):
            raise HarnessError(
                "context_preparation_mode_mismatch",
                "Run context preparation mode differs from Runtime composition.",
            )
        verdict = await self._ports.admission.evaluate(start)
        if not verdict.allowed:
            logger.warning(
                "run.admission_denied",
                extra={
                    "run_id": start.run_id.value,
                    "session_id": start.execution_session_id.value,
                },
            )
            raise HarnessError("admission_denied", "The Run was denied by admission.")
        profile = self._profiles[self._root_profile_key]
        driver = self._drivers[profile.driver_kind]
        snapshot = bind_start_snapshot(
            start,
            profile_key=self._root_profile_key,
            driver_kind=profile.driver_kind,
            policy_fingerprint=getattr(driver, "policy_fingerprint", None),
        )
        user_id = (
            start.host_control_user_id
            if host_control
            else ("harness-system" if start.conversation is None else start.conversation.user_id)
        )
        assert user_id is not None
        created = self._uow.create_with_start_snapshot(
            execution_session_id=start.execution_session_id.value,
            run_id=start.run_id.value,
            request_id=start.request_id.value,
            profile_key=self._root_profile_key,
            driver_kind=profile.driver_kind,
            snapshot=snapshot.to_json(),
            event_id=f"{start.run_id.value}:created",
            now=self._now(),
            user_id=user_id,
            context_stage_id=start.context_stage_id,
            context_stage_hash=start.context_stage_hash,
        )
        self._emit_transition(
            "run.started",
            run_id=created.run_id,
            request_id=start.request_id.value,
            execution_session_id=start.execution_session_id.value,
            outcome=Outcome.STARTED,
            operation="run_lifecycle",
            attributes={
                "entity_kind": "run",
                "entity_id": created.run_id,
                "run_id": created.run_id,
                "to_state": created.state.value,
                "state_version": created.version,
                "replayed": created.state is not RunState.CREATED,
            },
        )
        if start.context_stage_id is not None:
            self._emit_transition(
                "context.consumed",
                run_id=created.run_id,
                request_id=start.request_id.value,
                execution_session_id=start.execution_session_id.value,
                outcome=Outcome.SUCCEEDED,
                operation="context_staging",
                attributes={
                    "entity_kind": "context_stage",
                    "entity_id": start.context_stage_id,
                    "from_state": "staged",
                    "to_state": "consumed",
                    "run_id": created.run_id,
                },
            )
        logger.info(
            "run.start",
            extra={
                "run_id": start.run_id.value,
                "session_id": start.execution_session_id.value,
                "profile": self._root_profile_key,
            },
        )
        if created.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
            return created
        activated = await self._activate(created.run_id)
        self._schedule(activated.run_id)
        return activated

    async def _wake_continuation(self, run_id: str) -> None:
        if run_id in self._retiring_leases:
            self._pending_wakes.add(run_id)
            return
        if run_id not in self._leases:
            try:
                await self._activate(run_id)
            except UnitOfWorkConflict:
                # Another owner holds the Run (or its lease has not expired yet).
                # Remember the wake so the drain loop retries it instead of leaving
                # a queued input behind until the next recover().
                self._pending_wakes.add(run_id)
                return
        self._pending_wakes.discard(run_id)
        if run_id in self._live.active_run_ids():
            # A drive is already running and may have passed its claim point before
            # this input was enqueued (and, for idle BaseAgent Runs, may be about to
            # release the Run's authority).  Let it finish, then wake again so the
            # input is claimed by a fresh drive instead of waiting for recover().
            await self._live.wait(run_id)
            if self._closing:
                return
            blockers = getattr(self._uow, "list_open_wait_blockers_for_run", None)
            if callable(blockers) and blockers(run_id):
                # The drive ended in an UNKNOWN wait: the resolved-wait drain wakes
                # it once reconciliation settles the blocker; do not re-drive now.
                return
            await self._wake_continuation(run_id)
            return
        self._schedule(run_id)

    async def _activate(self, run_id: str) -> RunRecord:
        if run_id in self._retiring_leases:
            raise UnitOfWorkConflict("previous runtime epoch is still retiring")
        previous_lease = self._leases.get(run_id)
        run, lease = self._uow.claim_runtime_activation(
            run_id=run_id,
            owner_id=self._ports.owner_id,
            namespace=RUNTIME_LEASE_NAMESPACE,
            now=self._now(),
            lease_ttl_seconds=self._ports.lease_ttl_seconds,
        )
        self._leases[run_id] = lease
        fence = await self._uow.acquire(RunId(run_id), lease, now=self._now())
        self._fences[run_id] = fence
        if (
            previous_lease is None
            or previous_lease.epoch != lease.epoch
            or previous_lease.owner_id != lease.owner_id
            or run_id not in self._cancels
        ):
            # Cancellation due to lease loss belongs to that epoch, not to the
            # durable AgentTurn. A real user cancellation still fences recovery.
            token = CancelToken()
            if run.state is RunState.CANCEL_REQUESTED:
                token.cancel()
            self._cancels[run_id] = token
        heartbeat = self._heartbeats.get(run_id)
        if heartbeat is None or heartbeat.done():
            self._heartbeats[run_id] = asyncio.create_task(
                self._heartbeat(run_id), name=f"simple-harness-heartbeat:{run_id}"
            )
        return run

    async def _heartbeat(self, run_id: str) -> None:
        interval = max(0.001, self._ports.lease_ttl_seconds / 3.0)
        try:
            # Closing first cancels and joins every driver while ownership is still
            # renewed.  Only Runtime.close may stop the heartbeat after that join.
            while run_id in self._leases:
                await self._ports.sleep(interval)
                lease = self._leases.get(run_id)
                if lease is None:
                    return
                try:
                    renewed = self._uow.renew_runtime_lease(
                        lease,
                        now=self._now(),
                        lease_ttl_seconds=self._ports.lease_ttl_seconds,
                    )
                except UnitOfWorkConflict:
                    self._retiring_leases.add(run_id)
                    token = self._cancels.get(run_id)
                    fence = self._fences.get(run_id)
                    try:
                        if token is not None:
                            token.cancel()
                        await self._live.cancel(run_id)
                        current = self._leases.get(run_id)
                        same_epoch = current is not None and (
                            current.owner_id == lease.owner_id and current.epoch == lease.epoch
                        )
                        if current is None or same_epoch:
                            # The old driver may already have released its lease.
                            # Never remove authority installed by a different epoch.
                            if same_epoch:
                                self._leases.pop(run_id, None)
                            if self._cancels.get(run_id) is token:
                                self._cancels.pop(run_id, None)
                            if self._fences.get(run_id) is fence:
                                self._fences.pop(run_id, None)
                            self._workflow_spawn_ready_activations.pop(run_id, None)
                            self._workflow_start_dispatches.pop(run_id, None)
                            self._workflow_recovery_work.pop(run_id, None)
                        if fence is not None:
                            try:
                                # SDK release is fenced by the original fence epoch.
                                await self._uow.release(fence)
                            except UnitOfWorkConflict:
                                pass
                    finally:
                        if self._heartbeats.get(run_id) is asyncio.current_task():
                            self._heartbeats.pop(run_id, None)
                        self._retiring_leases.discard(run_id)
                    current_run = self._uow.read_run(run_id)
                    if (
                        not self._closing
                        and current_run is not None
                        and current_run.state
                        in {
                            RunState.RUNNING,
                            RunState.WAITING,
                            RunState.CANCEL_REQUESTED,
                        }
                    ):
                        self._pending_wakes.add(run_id)
                    return
                self._leases[run_id] = renewed
                ready = self._workflow_spawn_ready_activations.get(run_id)
                if ready is not None:
                    refreshed = await self._uow.run_atomic(
                        lambda tx, receipt_id=ready.activation_receipt_id: (  # type: ignore[misc]
                            self._uow.read_spawn_ready_activation(tx, run_id, receipt_id)
                        ),
                        fault_label="runtime:spawn_ready:heartbeat_read",
                    )
                    if refreshed is None:
                        raise UnitOfWorkConflict(
                            "workflow spawn activation disappeared after heartbeat"
                        )
                    self._workflow_spawn_ready_activations[run_id] = refreshed
        except asyncio.CancelledError:
            return

    def _schedule(self, run_id: str) -> None:
        current = self._uow.read_run(run_id)
        if current is not None and current.state in {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
        }:
            self._pending_wakes.discard(run_id)
            return
        if run_id in self._retiring_leases or run_id not in self._leases:
            # A submit/reschedule can finish waiting for the old driver before
            # its heartbeat finishes retiring. Defer instead of starting a
            # driver with absent or soon-to-be-removed authority.
            if not self._closing:
                self._pending_wakes.add(run_id)
            return
        self._live.schedule(run_id, self._drive(run_id))

    async def _drive(self, run_id: str) -> None:
        # Recovery or a cancel command can terminalize after this drive was
        # scheduled but before it starts. Its released fence is not a driver
        # failure, and the stale queued wake must not revive the terminal Run.
        current = self._uow.read_run(run_id)
        if current is not None and current.state in {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
        }:
            return
        continuation_claim: ContinuationRecord | None = None
        try:
            with runtime_operation(
                self._uow,
                "runtime.preflight",
                lease=self._leases[run_id],
                clock=self._now,
                identity={"run": run_id},
            ) as preflight_receipt:
                run = self._uow.read_run(run_id)
                raw_snapshot = self._uow.read_start_snapshot(run_id)
                if run is None or raw_snapshot is None:
                    raise RuntimeError("durable Run start state is incomplete")
                snapshot = StartSnapshot.from_json(raw_snapshot)
                driver = self._drivers[snapshot.driver_kind]
                if snapshot.policy_fingerprint is not None and (
                    getattr(driver, "policy_fingerprint", None) != snapshot.policy_fingerprint
                ):
                    error = HarnessError(
                        "runtime_policy_mismatch",
                        "The frozen Runtime policy is unavailable.",
                    )
                    preflight_receipt["audit_outcome"] = "rejected"
                    self._terminalize(
                        run,
                        state=RunState.FAILED,
                        payload=error.to_dict(),
                        deliveries=(),
                    )
                    return
                catalog_matches = False
                if snapshot.tool_catalog_fingerprint is not None:
                    resolver = getattr(self._ports.tool_catalog, "resolve", None)
                    resolved_catalog = (
                        None
                        if resolver is None
                        else resolver(
                            snapshot.tool_catalog_generation,
                            snapshot.tool_catalog_fingerprint,
                        )
                    )
                    catalog_matches = (
                        resolved_catalog is not None
                        and resolved_catalog.generation == snapshot.tool_catalog_generation
                        and resolved_catalog.content_fingerprint
                        == snapshot.tool_catalog_fingerprint
                    )
                else:
                    catalog_matches = (
                        snapshot.tool_catalog_generation
                        == self._ports.tool_catalog.current_generation()
                    )
                if not catalog_matches:
                    error = ToolCatalogStale()
                    preflight_receipt["audit_outcome"] = "rejected"
                    self._terminalize(
                        run,
                        state=RunState.FAILED,
                        payload=error.to_dict(),
                        deliveries=(),
                    )
                    return
                if run.state is RunState.CANCEL_REQUESTED:
                    preflight_receipt["audit_outcome"] = "rejected"
                    await self._terminalize_cancelled(
                        run, snapshot=snapshot, reason="pre_drive_cancel"
                    )
                    return
                continuation_claim = self._uow.claim_continuation(
                    run_id=run_id,
                    execution_lease=self._leases[run_id],
                    now=self._now(),
                )
                if run.driver_kind == BASE_AGENT_DRIVER_KIND:
                    pending_turn = self._uow.read_pending_agent_turn(run_id)
                    if pending_turn is not None:
                        # RESULT_PENDING survives any wake path: commit the staged result
                        # first and never hand the same input to the driver again.
                        if (
                            continuation_claim is not None
                            and continuation_claim.continuation_id != pending_turn.continuation_id
                        ):
                            raise UnitOfWorkConflict(
                                "pending agent turn is not at the continuation head"
                            )
                        if continuation_claim is None and pending_turn.continuation_id is not None:
                            durable = self._uow.read_continuation(pending_turn.continuation_id)
                            if durable is not None and durable.state is not ContinuationState.ACKED:
                                # Never finalize silently past an unacked input (review F5).
                                raise UnitOfWorkConflict(
                                    "pending agent turn input is claimed elsewhere"
                                )
                        await self._finalize_agent_turn(
                            run_id,
                            pending_turn.turn_id,
                            continuation_claim,
                            {"recovered": True},
                            reschedule=True,
                        )
                        return
                if run.state is RunState.WAITING and continuation_claim is None:
                    if run.driver_kind == BASE_AGENT_DRIVER_KIND and not self._closing:
                        # An idle BaseAgent keeps no lease, fence or heartbeat: N idle
                        # Agents must not cost N renewing tasks.  The next input
                        # re-activates the Run through ``_wake_continuation``.
                        await self._abandon_run_authority(run_id)
                    return
            # 按需导入：循环依赖（kernel ↔ react）
            from simple_harness.runtime.drivers.react import ReActDriver

            # 按需导入：循环依赖（kernel ↔ drivers）
            from simple_harness.runtime.drivers.start_mode import StartModeDriverRouter

            # 按需导入：循环依赖（kernel ↔ workflow）
            from simple_harness.runtime.drivers.workflow import WorkflowRuntimeDriver

            # Only this concrete SDK selector is transparent to recording.
            # Resolve after validating the persisted snapshot and before both
            # contract selection and invocation; never trust a Host contract tag.
            if type(driver) is StartModeDriverRouter:
                driver = driver.select(snapshot.start_mode)
            driver_contract = None
            if type(self._uow) is SqliteExecutionUnitOfWork:
                if type(driver) is ReActDriver:
                    driver_contract = "sdk.react.v2"
                elif type(driver) is WorkflowRuntimeDriver:
                    driver_contract = "sdk.workflow.v2"
                elif (
                    type(driver).__module__ == "simple_harness.agents.execution"
                    and type(driver).__name__ == "AgentExecutionDriver"
                ):
                    # SDK-owned BaseAgent driver; matched by its exact SDK module so the
                    # kernel never imports simple_harness.agents (layering rule).
                    driver_contract = "sdk.base_agent.v1"
            with runtime_operation(
                self._uow,
                "runtime.driver",
                contract=driver_contract,
                parent_operation_id=preflight_receipt.operation_id,
                lease=self._leases[run_id],
                clock=self._now,
                identity={"driver": run.driver_kind, "run": run_id},
            ) as audit_receipt:
                result = await driver.start(
                    DriverInvocation(
                        run=run,
                        start=snapshot,
                        execution_lease=self._leases[run_id],
                        run_fence=self._fences[run_id],
                        services=self._services,
                        continuations=(() if continuation_claim is None else (continuation_claim,)),
                        workflow_spawn_ready_activation=(
                            self._workflow_spawn_ready_activations.get(run_id)
                        ),
                        workflow_start_dispatch=self._workflow_start_dispatches.get(run_id),
                        workflow_recovery_work=self._workflow_recovery_work.get(run_id),
                    ),
                    context=self._ports.context,
                    cancel=self._cancels[run_id],
                )
                audit_receipt["state"] = result.state.value
                self._driver_exception_wakes.pop(run_id, None)
            if continuation_claim is not None:
                durable_continuation = self._uow.read_continuation(
                    continuation_claim.continuation_id
                )
                if (
                    durable_continuation is not None
                    and durable_continuation.state is ContinuationState.ACKED
                ):
                    continuation_claim = None
            if result.workflow_spawn_control is not None:
                await self._accept_workflow_spawn_control(run_id, result.workflow_spawn_control)
                return
            current = self._uow.read_run(run_id)
            if current is None:
                raise RuntimeError("Run disappeared during execution")
            if result.agent_turn_outcome is not None:
                # BaseAgent: stage + finalize the turn result; the Run stays WAITING
                # and never reaches the terminal branch below.  A failure here must
                # not terminalize the Agent (review F3): the stage is durable, so drop
                # this executor's authority and let finalize-first recover the turn.
                try:
                    await self._commit_agent_turn(
                        current, result.agent_turn_outcome, result.payload, continuation_claim
                    )
                except UnitOfWorkConflict:
                    raise
                except Exception:  # noqa: BLE001 - staged result survives; never FAILED
                    logger.exception(
                        "sdk_agent_turn_finalize_failed", extra={"run_id": str(run_id)}
                    )
                    await self._abandon_run_authority(run_id)
                return
            if (
                run.driver_kind == BASE_AGENT_DRIVER_KIND
                and continuation_claim is not None
                and result.state is RunState.WAITING
                and (result.wait_blocker is not None or result.authorization_wait is not None)
            ):
                # BaseAgent turn suspended mid-way (UNKNOWN outbound work / authorization):
                # keep the input continuation claimed (not acked) so the same turn resumes
                # under the fresh lease epoch once the blocker resolves (review F2).
                if result.authorization_wait is not None:
                    self._commit_authorization_wait(run_id, result.authorization_wait)
                else:
                    assert result.wait_blocker is not None
                    self._uow.commit_runtime_wait_with_blocker(
                        run_id=run_id,
                        expected_version=current.version,
                        event_id=f"{run_id}:waiting:{current.version + 1}",
                        payload=result.payload,
                        blocker=result.wait_blocker,
                        lease=self._leases[run_id],
                        now=self._now(),
                    )
                # Release this executor's lease: the resolved-wait drain re-activates the
                # Run under a new epoch, which is what lets the claimed input be re-claimed.
                await self._abandon_run_authority(run_id)
                return
            if result.state is RunState.WAITING:
                if result.authorization_wait is not None:
                    if continuation_claim is not None:
                        raise UnitOfWorkConflict("authorization wait cannot ack a continuation")
                    self._commit_authorization_wait(run_id, result.authorization_wait)
                    return
                if continuation_claim is None:
                    if result.wait_blocker is None:
                        self._uow.commit_runtime_state(
                            run_id=run_id,
                            expected_version=current.version,
                            state=RunState.WAITING,
                            event_id=f"{run_id}:waiting:{current.version + 1}",
                            payload=result.payload,
                            lease=self._leases[run_id],
                            now=self._now(),
                        )
                        if run.driver_kind == BASE_AGENT_DRIVER_KIND and not self._closing:
                            # Idle BaseAgent (fresh start, nothing queued): drop authority.
                            await self._abandon_run_authority(run_id)
                    else:
                        self._uow.commit_runtime_wait_with_blocker(
                            run_id=run_id,
                            expected_version=current.version,
                            event_id=f"{run_id}:waiting:{current.version + 1}",
                            payload=result.payload,
                            blocker=result.wait_blocker,
                            lease=self._leases[run_id],
                            now=self._now(),
                        )
                else:
                    if result.wait_blocker is not None:
                        raise UnitOfWorkConflict(
                            "uncertain outbound work cannot ack a continuation"
                        )
                    self._uow.commit_runtime_state_and_ack_continuation(
                        run_id=run_id,
                        expected_version=current.version,
                        state=RunState.WAITING,
                        event_id=(
                            f"{run_id}:waiting:continuation:"
                            f"{continuation_claim.continuation_id}:"
                            f"{continuation_claim.claim_epoch}"
                        ),
                        payload=result.payload,
                        continuation_claim=continuation_claim,
                        execution_lease=self._leases[run_id],
                        receipt_id=(
                            f"{run_id}:progress:{continuation_claim.continuation_id}:"
                            f"{continuation_claim.claim_epoch}"
                        ),
                        now=self._now(),
                    )
                    asyncio.create_task(self._reschedule(run_id))
            else:
                self._terminalize(
                    current,
                    state=result.state,
                    payload=result.payload,
                    deliveries=result.deliveries,
                    continuation_claim=continuation_claim,
                    conversation_output=result.conversation_output,
                )
        except asyncio.CancelledError:
            current = self._uow.read_run(run_id)
            if (
                not self._closing
                and current is not None
                and current.state is RunState.CANCEL_REQUESTED
            ):
                if continuation_claim is None:
                    await self._terminalize_cancelled(current, reason="driver_cancelled")
                else:
                    await self._abandon_run_authority(run_id)
        except UnitOfWorkConflict:
            if continuation_claim is not None:
                await self._abandon_run_authority(run_id)
            return
        except Exception as error:  # noqa: BLE001 - driver boundary becomes a durable failure
            # private_cause 不进 HarnessError.to_dict()（对外只暴露稳定 public 契约），
            # 但必须落到日志供 host 排障；否则 driver 失败完全不可追踪。
            logger.exception(
                "sdk_run_driver_failed",
                extra={"run_id": str(run_id)},
            )
            current = self._uow.read_run(run_id)
            if current is not None and current.driver_kind == BASE_AGENT_DRIVER_KIND:
                # A BaseAgent never dies of a driver exception (BA-v1.0 §1.3): keep the
                # input claimed under the lapsed lease and drop this executor's
                # authority; the next wake either finalizes a result the loop already
                # staged (BA31) or re-drives the same turn.  Kernel-integrity
                # failures reach here as explicit FAILED DriverResults, not exceptions.
                # The driver itself turns exceptions inside a turn into a visible
                # failed turn; what reaches here is re-woken through the drain loop a
                # bounded number of times, after which only recover() re-drives it
                # (review K1: a claimed input must never wait silently for recover()).
                await self._abandon_run_authority(run_id)
                attempts = self._driver_exception_wakes.get(run_id, 0) + 1
                self._driver_exception_wakes[run_id] = attempts
                if attempts <= BASE_AGENT_DRIVER_EXCEPTION_WAKES:
                    self._pending_wakes.add(run_id)
                else:
                    logger.error(
                        "sdk_base_agent_driver_exception_wakes_exhausted",
                        extra={"run_id": str(run_id), "attempts": attempts},
                    )
                return
            if current is not None and current.state not in {
                RunState.COMPLETED,
                RunState.FAILED,
                RunState.CANCELLED,
            }:
                failure = HarnessError(
                    "driver_failed",
                    "The Run driver failed.",
                    private_cause=error,
                )
                try:
                    self._terminalize(
                        current,
                        state=RunState.FAILED,
                        payload=failure.to_dict(),
                        deliveries=(),
                        continuation_claim=continuation_claim,
                    )
                except UnitOfWorkConflict:
                    pass
        finally:
            current = self._uow.read_run(run_id)
            if current is not None and current.state in {
                RunState.COMPLETED,
                RunState.FAILED,
                RunState.CANCELLED,
            }:
                self._release_runtime_lease(run_id)

    async def _cancel_run(self, run_id: RunId) -> RunRecord:
        self._require_started()
        value = _run_id(run_id)
        current = self._uow.read_run(value)
        if current is None:
            raise KeyError(value)
        snapshot_raw = self._uow.read_start_snapshot(value)
        if snapshot_raw is None:
            raise RuntimeError("durable Run start state is incomplete")
        snapshot = StartSnapshot.from_json(snapshot_raw)
        if snapshot.driver_kind == WORKFLOW_DRIVER_KIND:
            token = self._cancels.get(value)
            latest = self._uow.read_run(value)
            assert latest is not None
            await self._terminalize_cancelled(latest, snapshot=snapshot, reason="user")
            if token is not None:
                token.cancel()
            if value in self._live.active_run_ids():
                await self._live.cancel(value)
            result = self._uow.read_run(value)
            assert result is not None
            return result
        self._uow.request_run_cancel(
            run_id=value,
            expected_version=current.version,
            event_id=f"{value}:cancel-requested",
            now=self._now(),
        )
        token = self._cancels.get(value)
        if token is not None:
            token.cancel()
        task_active = value in self._live.active_run_ids()
        if task_active:
            await self._live.cancel(value)
        latest = self._uow.read_run(value)
        assert latest is not None
        if latest.state is RunState.CANCEL_REQUESTED:
            await self._terminalize_cancelled(latest)
        result = self._uow.read_run(value)
        assert result is not None
        return result

    def _commit_authorization_wait(self, run_id: str, pending: ToolAuthorizationPending) -> None:
        prepared = pending.prepared
        arguments = thaw_json(prepared.call.arguments)
        metadata = thaw_json(pending.request.metadata)
        if not isinstance(arguments, dict) or not isinstance(metadata, dict):
            raise TypeError("authorization request payload must be objects")
        self._uow.commit_decision(
            decision_id=pending.decision_id,
            run_id=run_id,
            kind="tool_authorization",
            state=DecisionState.OPEN,
            request={
                "arguments": arguments,
                "call_id": prepared.call.call_id.value,
                "effect_id": prepared.effect_id.value,
                "expires_at": pending.request.expires_at,
                "metadata": metadata,
                "nonce": pending.request.nonce,
                "prompt": pending.request.prompt,
                "resources": [resource.to_json() for resource in prepared.resources],
                "resources_digest": resource_digest(prepared.resources),
                "sidecar_digest": (None if prepared.sidecar is None else prepared.sidecar.digest),
                "tool_name": prepared.call.name,
            },
            response=None,
            event_id=f"{pending.decision_id}:open",
            now=self._now(),
        )

    async def _commit_agent_turn(
        self,
        current: RunRecord,
        outcome: AgentTurnOutcome,
        payload: Mapping[str, JsonValue],
        continuation_claim: ContinuationRecord | None,
    ) -> None:
        """stage (RESULT_PENDING) then finalize one BaseAgent turn; Run stays WAITING."""

        run_id = current.run_id
        turn = self._uow.read_agent_turn(outcome.turn_id)
        if turn is None or turn.agent_id != outcome.agent_id:
            raise UnitOfWorkConflict("agent turn row does not match the driver outcome")
        if turn.phase in {"queued", "running"}:
            self._uow.stage_agent_turn_result(
                turn_id=outcome.turn_id,
                result_hash=outcome.result_hash,
                result_json=outcome.result_object(),
                provider_turn_ordinal_from=outcome.provider_turn_ordinal_from,
                provider_turn_ordinal_to=outcome.provider_turn_ordinal_to,
                execution_lease=self._leases[run_id],
                now=self._now(),
            )
        elif turn.phase == "result_pending":
            if turn.staged_result_hash != outcome.result_hash:
                raise UnitOfWorkConflict("staged agent turn result differs from driver outcome")
        elif self._uow.read_agent_turn_result(outcome.turn_id) is None:
            raise UnitOfWorkConflict("agent turn is closed without a result row")
        await self._finalize_agent_turn(
            run_id,
            outcome.turn_id,
            continuation_claim,
            dict(payload),
            usage_refs=outcome.usage_refs,
            reschedule=True,
        )

    async def _finalize_agent_turn(
        self,
        run_id: str,
        turn_id: str,
        continuation_claim: ContinuationRecord | None,
        payload: dict[str, JsonValue],
        *,
        usage_refs: tuple[str, ...] = (),
        reschedule: bool,
    ) -> None:
        current = self._uow.read_run(run_id)
        if current is None:
            raise RuntimeError("Run disappeared during agent turn finalize")
        receipt_id = (
            None
            if continuation_claim is None
            else (
                f"{run_id}:progress:{continuation_claim.continuation_id}:"
                f"{continuation_claim.claim_epoch}"
            )
        )
        binding = self._uow.read_agent_binding_for_run(run_id)
        finalize_payload: dict[str, JsonValue] = {
            **payload,
            "base_agent_stage": "idle",
            "turn_id": turn_id,
            "agent_id": None if binding is None else binding.agent_id,
        }
        self._uow.commit_agent_turn_result_and_idle(
            run_id=run_id,
            expected_version=current.version,
            turn_id=turn_id,
            event_id=f"{run_id}:agent_turn:{turn_id}:committed",
            payload=finalize_payload,
            continuation_claim=continuation_claim,
            execution_lease=self._leases[run_id],
            receipt_id=receipt_id,
            usage_refs=usage_refs,
            now=self._now(),
        )
        if reschedule:
            asyncio.create_task(self._reschedule(run_id))

    async def _finalize_pending_agent_turn(self, run_id: str) -> bool:
        """Recovery: commit an already staged turn without re-entering the driver.

        Claims the turn's own input continuation (if it is still unacked) so the
        same input is never handed to the driver a second time.
        """

        pending = self._uow.read_pending_agent_turn(run_id)
        if pending is None:
            return False
        claim: ContinuationRecord | None = None
        if pending.continuation_id is not None:
            durable = self._uow.read_continuation(pending.continuation_id)
            if durable is not None and durable.state is not ContinuationState.ACKED:
                claim = self._uow.claim_continuation(
                    run_id=run_id,
                    execution_lease=self._leases[run_id],
                    now=self._now(),
                )
                if claim is None:
                    raise UnitOfWorkConflict("pending agent turn input is claimed elsewhere")
                if claim.continuation_id != pending.continuation_id:
                    raise UnitOfWorkConflict("pending agent turn is not at the continuation head")
        await self._finalize_agent_turn(
            run_id, pending.turn_id, claim, {"recovered": True}, reschedule=False
        )
        return True

    async def _reschedule(self, run_id: str) -> None:
        while run_id in self._live.active_run_ids():
            await asyncio.sleep(0)
        if run_id not in self._leases or self._closing:
            return
        self._schedule(run_id)

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import replace

from deskpet.execution.contracts import (
    ActorContext,
    AdmissionBoundary,
    AdmissionPhase,
    AttachmentPolicy,
    ChildCommandRecord,
    DecisionConflict,
    DecisionStatus,
    LiveCursor,
    JsonValue,
    OutcomeStatus,
    PersistenceLevel,
    RecoveryLease,
    RunCreate,
    RunEvent,
    RunEventCandidate,
    RunNotFound,
    RunRecord,
    RunRef,
    RunStatus,
    TERMINAL_RUN_STATUSES,
    TerminalConflict,
    VersionConflict,
    fingerprint_json,
    root_idempotency_key,
    stable_event_id,
    DecisionSignal as DurableDecisionSignal,
    thaw_json,
)
from deskpet.execution.uow_ports import KernelUnitOfWork
from deskpet.execution.run_block_signals import (
    RootBlockReasonV1,
    RunBlockReporter,
    block_signal_for_terminal,
)
from deskpet.execution.fences import (
    RunExecutionFencePort,
    UnboundRunExecutionFence,
    release_run_execution_fence,
)
from deskpet.permissions.admission import AdmissionAuthorizer
from deskpet.permissions.admission import resolve_admission_task_grant, task_grant_receipt_payload

from .admission_launch import (
    AdmissionLauncher,
    admission_from,
    build_admission_start,
    build_driver_start,
)
from .context import HostContextFactory, authorize_live
from .child_signal_runtime import ChildSignalRuntime
from .child_runs import ChildRunCoordinator
from .contracts import (
    CancelReceipt,
    HostContext,
    HostExtensionRefV1,
    PreparedRunContextV1,
    RegisteredDriver,
    RunHandle,
    RunRequest,
    SignalReceipt,
    TerminalDeliveryContributor,
    TerminalProjection,
)
from .live_index import BoundedLiveIndex, LiveStreamOverflow
from .ports import (
    DecisionSignal as DriverDecisionSignal,
    DriverRecoveryDeferred,
    DriverSignal,
    DriverStart,
    DriverTerminalCandidate,
)
from .router import RegisteredRouter
from .profiles import ProfileRegistry
from .runtime import DriverRuntime
from .start_snapshot import (
    activate_after_start_commit,
    build_run_start_snapshot,
    terminal_deliveries_from_snapshot,
)
from .tool_executor import EffectBatchExecutor
from .user_continuations import UserContinuationCoordinator

logger = logging.getLogger(__name__)

def root_run_identity(session_id: str, request_id: str, turn_id: str) -> tuple[str, RunRef]:
    key = root_idempotency_key(session_id, request_id, turn_id)
    return key, RunRef(uuid.uuid5(uuid.NAMESPACE_URL, f"deskpet:{key}").hex, session_id)


class RunKernel:
    def __init__(
        self,
        *,
        uow: KernelUnitOfWork,
        router: RegisteredRouter | None,
        drivers: Mapping[str, RegisteredDriver],
        profiles: ProfileRegistry | None = None,
        root_profile_key: str | None = None,
        context_factory: HostContextFactory | None = None,
        child_runs: ChildRunCoordinator | None = None,
        tool_executor: EffectBatchExecutor | None = None,
        terminal_projection: TerminalProjection | None = None,
        terminal_delivery_contributors: Sequence[
            TerminalDeliveryContributor
        ] = (),
        terminal_observer: Callable[[RunRecord, RunEvent], object] | None = None,
        continuation_observer: Callable[..., object] | None = None,
        admission_authorizer: AdmissionAuthorizer | None = None,
        run_execution_fence: RunExecutionFencePort | None = None,
        max_live_runs: int = 4096,
        child_signal_heartbeat_interval: float = 10.0,
    ) -> None:
        self._uow = uow
        if router is None and (profiles is None or not root_profile_key):
            raise ValueError("Kernel requires either a legacy router or one fixed root profile")
        self._router = router
        self._profiles = profiles
        self._root_profile_key = root_profile_key
        self._drivers = drivers
        self._context_factory = context_factory or HostContextFactory()
        self._child_runs = child_runs
        self._tool_executor = tool_executor
        self._terminal_projection = terminal_projection
        self._terminal_delivery_contributors = tuple(
            terminal_delivery_contributors
        )
        self._terminal_observer = terminal_observer
        self._admission_authorizer = admission_authorizer
        self._run_execution_fence = (
            run_execution_fence or UnboundRunExecutionFence()
        )
        self._child_signal_heartbeat_interval = max(
            0.001, float(child_signal_heartbeat_interval)
        )
        self._live = BoundedLiveIndex(max_runs=max_live_runs)
        self._lock = self._live.lock
        for registration in drivers.values():
            bind_live_index = getattr(registration.driver, "bind_live_index", None)
            if callable(bind_live_index):
                bind_live_index(self._live)
        self._recovery_owner = f"kernel:{uuid.uuid4().hex}"
        self._continuations = UserContinuationCoordinator(
            uow=uow,
            live=self._live,
            drivers=drivers,
            query=self._query,
            observer=continuation_observer,
        )
        self._runtime = DriverRuntime(
            uow=uow,
            live=self._live,
            query=self._query,
            finalize=self._finalize,
            terminal_handler=self._continuations.handle_terminal,
            child_runs=child_runs,
            tool_executor=tool_executor,
        )
        self._continuations.bind_runtime(self._runtime)
        self._child_signal_runtime = ChildSignalRuntime(
            live=self._live,
            runtime=self._runtime,
            continuations=self._continuations,
            heartbeat_interval=self._child_signal_heartbeat_interval,
        )
        self._admission_launcher = AdmissionLauncher(
            uow=uow,
            live=self._live,
            lock=self._lock,
            runtime=self._runtime,
            continuations=self._continuations,
            recovery_owner=self._recovery_owner,
            query=self._query,
            acquire_run_execution_fence=self._acquire_run_execution_fence,
            terminal_deliveries=self._terminal_deliveries,
            notify_terminal=self._notify_terminal,
        )

    async def _configure_active_execution_budget(
        self,
        run_id: str,
        request: RunRequest,
    ) -> None:
        configure = getattr(self._uow, "configure_run_active_budget", None)
        if not callable(configure):
            return
        raw = request.payload.get("active_execution_budget_seconds")
        if raw is None:
            return
        await configure(run_id, limit_seconds=float(raw))

    async def _acquire_run_execution_fence(self, run_id: str):
        lease = await self._run_execution_fence.acquire(run_id)
        if str(getattr(lease, "run_id", "")) != run_id:
            await release_run_execution_fence(lease)
            raise RuntimeError("run execution fence identity mismatch")
        return lease

    async def _notify_terminal(
        self, record: RunRecord, event: RunEvent
    ) -> None:
        observer = self._terminal_observer
        if observer is None:
            return
        try:
            value = observer(record, event)
            if inspect.isawaitable(value):
                await value
        except Exception:
            # The terminal outcome is already authoritative.  Product cleanup
            # is idempotently reconciled at the next Harness activation.
            logger.exception(
                "terminal observer failed after run commit",
                extra={"run_id": record.run_id},
            )

    async def _drain_active(self, timeout: float) -> bool:
        async with self._lock:
            tasks = tuple(
                active.task
                for active in self._live.values()
                if active.task is not None and not active.task.done()
            )
        if not tasks:
            return True
        _, pending = await asyncio.wait(tasks, timeout=max(0.0, timeout))
        if not pending:
            return True
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        return False

    @staticmethod
    def _ephemeral_record(spec: RunCreate) -> RunRecord:
        now = time.time()
        return RunRecord(
            spec=spec,
            status=spec.status,
            persistence_level=PersistenceLevel.EPHEMERAL,
            version=0,
            durable_seq=0,
            terminal_event_id=None,
            created_at=now,
            updated_at=now,
            started_at=now if spec.status is RunStatus.RUNNING else None,
        )

    async def _launch_live(
        self, active, registration: RegisteredDriver, record: RunRecord | None,
        *, start: DriverStart | None = None, name: str,
    ) -> RunRecord:
        if active.task is not None and not active.task.done():
            assert record is not None
            return record
        candidates = None
        if start is not None:
            candidates = registration.driver.start(start)
            if registration.atomic_start:
                try:
                    first = await anext(candidates)
                except StopAsyncIteration as exc:
                    raise RuntimeError(
                        "atomic-start driver ended before durable acceptance"
                    ) from exc
                durable = await self._query(
                    RunRef(start.run_id, start.session_id),
                    start.run_context.actor(),
                )
                await self._runtime.consume_candidate(registration, durable, first)
                record = durable
                if first.kind == "terminal":
                    return record
        assert record is not None
        if candidates is not None:
            operation = self._runtime.drive(registration, record, candidates)
        else:
            lease = await self._uow.recovery_scope(
                record.run_id, owner=self._recovery_owner
            )
            try:
                if callable(prepare := getattr(registration.driver, "prepare_recovery", None)):
                    await prepare(record.run_id, lease)
            except DriverRecoveryDeferred:
                await self._uow.recovery_scope(lease, lease_seconds=None)
                active.recovery_deferred_until = (
                    asyncio.get_running_loop().time() + 30.0
                )
                return record
            except BaseException as exc:
                await self._uow.recovery_scope(lease, lease_seconds=None)
                if await self._runtime.terminalize_permanent_recovery_failure(
                    registration, record, exc
                ):
                    return record
                raise
            active.recovery_deferred_until = 0.0
            operation = self._runtime.drive_recovery(registration, record, lease)
        async with self._lock:
            active.task = asyncio.create_task(
                self._continuations.run_owned(
                    active,
                    operation,
                    RunRef(record.run_id, record.context.session_id),
                    record.context.actor(),
                ),
                name=name,
            )
        return record

    async def _query(self, ref: RunRef, actor: ActorContext) -> RunRecord:
        async with self._lock:
            active = self._live.get(ref.run_id)
            if active is not None and active.record is not None:
                authorize_live(ref, actor, active.record)
                if (active.record.persistence_level is PersistenceLevel.EPHEMERAL
                        or active.record.status in TERMINAL_RUN_STATUSES):
                    return active.record
        record = await self._uow.query(ref, actor)
        if not isinstance(record, RunRecord):
            raise RunNotFound("legacy_run", "legacy projections are outside the active Kernel")
        return record

    async def _request_cancel(
        self,
        record: RunRecord,
        *,
        expected_version: int,
        reason: str,
        event: RunEventCandidate,
    ) -> RunRecord:
        if record.persistence_level is PersistenceLevel.DURABLE:
            return await self._uow.commit_run_outcome(
                record.run_id,
                expected_version=expected_version,
                cancel_reason=reason,
                event=event,
            )
        async with self._lock:
            active = self._live.get(record.run_id)
            current = active.record if active is not None else None
            if current is None:
                raise RunNotFound("run_not_found", f"live run does not exist: {record.run_id}")
            if current.version != expected_version:
                raise VersionConflict(
                    "stale_run_version",
                    f"expected run version {expected_version}, found {current.version}",
                )
            if current.status in TERMINAL_RUN_STATUSES:
                raise TerminalConflict("run_already_terminal", "a terminal run cannot be cancelled")
            updated = replace(
                current,
                status=RunStatus.CANCEL_REQUESTED,
                version=current.version + 1,
                cancel_reason=reason,
                updated_at=time.time(),
            )
            active.record = updated
            return updated

    async def _finalize(
        self,
        record: RunRecord,
        *,
        expected_version: int,
        status: RunStatus,
        event: RunEventCandidate,
        recovery_lease: RecoveryLease | None = None,
    ) -> RunEvent:
        if record.persistence_level is PersistenceLevel.DURABLE:
            command = (None if record.context.parent_run_id is None else
                await self._uow.get_child_command_for_run(record.run_id))
            finalize = (
                self._uow.finalize_child_and_enqueue_parent_signal
                if command is not None else self._uow.commit_run_outcome
            )
            kwargs = dict(
                expected_version=expected_version,
                terminal_status=status,
                event=event,
                recovery_lease=recovery_lease,
            )
            active = self._live.get(record.run_id)
            prepared = None if active is None else active.prepared_context
            terminal_extensions = list(
                () if prepared is None else prepared.terminal_commit_extensions
            )
            external_block = event.correlation.get("run_block_signal")
            if external_block is not None:
                if (
                    command is not None
                    or status is not RunStatus.FAILED
                    or not isinstance(external_block, Mapping)
                    or external_block.get("reason_code")
                    != RootBlockReasonV1.EXTERNAL_DEPENDENCY_UNAVAILABLE.value
                    or external_block.get("retry_exhausted") is not True
                    or external_block.get("replacement_pending") is not False
                ):
                    raise TerminalConflict(
                        "invalid_external_dependency_block",
                        "external dependency block evidence is incomplete",
                    )
                raw_refs = external_block.get("evidence_refs")
                if (
                    isinstance(raw_refs, (str, bytes))
                    or not isinstance(raw_refs, (list, tuple))
                    or not raw_refs
                ):
                    raise TerminalConflict(
                        "invalid_external_dependency_block",
                        "external dependency block requires evidence refs",
                    )
                terminal_extensions.append(
                    RunBlockReporter(
                        block_signal_for_terminal(
                            root_run_id=record.run_id,
                            event_key=event.event_key,
                            reason_code=(
                                RootBlockReasonV1.EXTERNAL_DEPENDENCY_UNAVAILABLE
                            ),
                            evidence_refs=tuple(str(item) for item in raw_refs),
                            created_at=record.created_at,
                        )
                    )
                )
            if terminal_extensions:
                kwargs["terminal_commit_extensions"] = (
                    tuple(terminal_extensions)
                )
            if command is None:
                kwargs["deliveries"] = await self._terminal_deliveries(record)
            if command is not None:
                kwargs["value"] = thaw_json(event.payload)
            execution_fence = await self._acquire_run_execution_fence(
                record.run_id
            )
            try:
                terminal_fence = getattr(
                    execution_fence, "terminal_commit_fence", None
                )
                if (
                    command is None
                    and kwargs.get("deliveries")
                    and terminal_fence is not None
                ):
                    kwargs["delivery_fence"] = (
                        terminal_fence.to_uow_fence()
                    )
                    kwargs["release_receipt_kind"] = (
                        terminal_fence.release_receipt_kind
                    )
                result = await finalize(
                    command.operation_id
                    if command is not None
                    else record.run_id,
                    **kwargs,
                )
            finally:
                await release_run_execution_fence(execution_fence)
            async with self._lock:
                self._live.adopt(result.record)
            await self._notify_terminal(result.record, result.event)
            if command is None:
                await self._cleanup_after_terminal_commit(
                    result.record, result.event
                )
            return result.event
        async with self._lock:
            active = self._live.get(record.run_id)
            current = active.record if active is not None else None
            if current is None:
                raise RunNotFound("run_not_found", f"live run does not exist: {record.run_id}")
            if current.status in TERMINAL_RUN_STATUSES:
                existing = next(
                    (item for item in active.events if item.event_id == current.terminal_event_id),
                    None,
                )
                if current.status is status and existing is not None and existing.candidate == event:
                    return existing
                raise TerminalConflict("terminal_conflict", "another terminal intent already won")
            if current.version != expected_version:
                raise VersionConflict(
                    "stale_run_version",
                    f"expected run version {expected_version}, found {current.version}",
                )
            now = time.time()
            terminal = RunEvent(
                event_id=stable_event_id(record.run_id, event.event_key),
                run_id=record.run_id,
                root_run_id=record.context.root_run_id,
                session_id=record.context.session_id,
                durable_seq=None,
                live_cursor=LiveCursor(f"kernel:{record.run_id}", len(active.events) + 1),
                candidate=event,
                created_at=now,
            )
            active.record = replace(
                current,
                status=status,
                version=current.version + 1,
                terminal_event_id=terminal.event_id,
                updated_at=now,
                ended_at=now,
            )
            finalized_record = active.record
        await self._notify_terminal(finalized_record, terminal)
        await self._cleanup_after_terminal_commit(finalized_record, terminal)
        return terminal

    async def _cleanup_after_terminal_commit(
        self, record: RunRecord, event: RunEvent
    ) -> None:
        active = self._live.get(record.run_id)
        prepared = None if active is None else active.prepared_context
        if prepared is None or not prepared.after_terminal_commit_cleanup:
            return
        reader = getattr(self._uow, "read_terminal_extension_receipts", None)
        receipts: tuple[HostExtensionRefV1, ...] = ()
        if callable(reader) and record.persistence_level is PersistenceLevel.DURABLE:
            raw = await reader(record.run_id, event.event_id)
            receipts = tuple(
                item
                if isinstance(item, HostExtensionRefV1)
                else HostExtensionRefV1(
                    kind=str(
                        item["kind"] if isinstance(item, Mapping) else item.kind
                    ),
                    ref=str(
                        item["ref"] if isinstance(item, Mapping) else item.ref
                    ),
                    content_hash=str(
                        item["content_hash"]
                        if isinstance(item, Mapping)
                        else item.content_hash
                    ),
                )
                for item in raw
            )
        for cleanup in prepared.after_terminal_commit_cleanup:
            try:
                await cleanup.cleanup_after_terminal(
                    record,
                    event,
                    extension_receipts=receipts,
                )
            except Exception:
                logger.exception(
                    "terminal cleanup failed after authoritative commit",
                    extra={
                        "run_id": record.run_id,
                        "extension_kind": cleanup.descriptor.kind,
                    },
                )

    def _build_root_start(
        self,
        *,
        key: str,
        run_id: str,
        request: RunRequest,
        host: HostContext,
        prepared: PreparedRunContextV1,
        force_durable: bool = False,
    ) -> tuple[
        PreparedRunContextV1,
        str,
        RegisteredDriver,
        Sequence[str],
        Mapping[str, Any],
        RunCreate,
        Any,
        RunEventCandidate | None,
    ]:
        """Build the sole trusted Root spec and immutable start snapshot."""

        frozen_deliveries = list(prepared.frozen_terminal_deliveries)
        contributor_requires_durable = False
        for contributor in self._terminal_delivery_contributors:
            contributor_requires_durable = (
                bool(contributor.requires_durable(request, host))
                or contributor_requires_durable
            )
            frozen_deliveries.extend(contributor.freeze_deliveries(request, host))
        prepared = replace(
            prepared,
            frozen_terminal_deliveries=tuple(frozen_deliveries),
        )
        projection_target = (
            self._terminal_projection.resolve(host.session_id)
            if self._terminal_projection is not None
            else None
        )
        if self._root_profile_key is not None:
            if self._profiles is None:
                raise RuntimeError("fixed root profile registry is unavailable")
            selected_profile = self._profiles.resolve(
                self._root_profile_key,
                generation=self._profiles.generation,
                available_capabilities=host.available_capabilities,
            )
            selected_profile_key = selected_profile.profile_key
            selected_driver_kind = selected_profile.driver_kind
        else:
            if self._router is None:
                raise RuntimeError("legacy route resolver is unavailable")
            routing = self._router.route(
                request,
                available_capabilities=host.available_capabilities,
            )
            selected_profile_key = routing.profile_key
            selected_driver_kind = routing.driver_kind
        request_capabilities = (
            prepared.prepared_tool_names
            if prepared.prepared_tool_names
            else (
                request.proposed_tools
                if isinstance(request.payload.get("context_os"), Mapping)
                else host.available_capabilities
            )
        )
        trusted_capability_snapshot = (
            dict(prepared.capability_snapshot)
            if prepared.capability_snapshot
            else {
                "capabilities": sorted(request_capabilities),
                "capability_hash": host.capability_hash,
            }
        )
        if trusted_capability_snapshot.get("capability_hash") != host.capability_hash:
            raise ValueError("prepared capability snapshot differs from HostContext")
        registration = self._drivers[selected_driver_kind]
        context = self._context_factory.create_run_context(
            session_id=host.session_id,
            root_run_id=run_id,
            request_id=request.request_id,
            turn_id=request.turn_id,
            venue=request.venue,
            capability_hash=host.capability_hash,
            provider_plan=host.provider_plan,
            provider_bindings=host.provider_bindings,
            trace_id=host.trace_id,
            principal_id=host.principal_id,
            auth_epoch=host.auth_epoch,
            workspace=host.workspace,
            write_scope_root=host.write_scope_root,
            owner_key=prepared.owner_key,
            profile_generation=prepared.profile_generation,
            binding_epoch=prepared.binding_epoch,
        )
        payload = {"text": request.text, "payload": dict(request.payload)}
        spec = RunCreate(
            run_id=run_id,
            idempotency_key=key,
            context=context,
            payload_fingerprint=fingerprint_json(payload),
            capability_fingerprint=host.capability_hash,
            driver_kind=registration.kind,
            profile_key=selected_profile_key,
            persistence_level=(
                PersistenceLevel.DURABLE
                if (
                    force_durable
                    or request.admission is not None
                    or registration.durable_from_start
                    or projection_target is not None
                    or prepared.persistence_required
                    or contributor_requires_durable
                )
                else PersistenceLevel.EPHEMERAL
            ),
            status=RunStatus.CREATED,
        )
        run_start_snapshot = (
            build_run_start_snapshot(
                spec=spec,
                request=request,
                host=host,
                prepared=prepared,
                created_at=time.time(),
            )
            if spec.persistence_level is PersistenceLevel.DURABLE
            else None
        )
        association_event = (
            self._terminal_projection.association_event(spec, projection_target)
            if self._terminal_projection is not None and projection_target is not None
            else None
        )
        return (
            prepared,
            selected_profile_key,
            registration,
            request_capabilities,
            trusted_capability_snapshot,
            spec,
            run_start_snapshot,
            association_event,
        )

    async def start(
        self,
        request: RunRequest,
        host: HostContext,
        *,
        prepared: PreparedRunContextV1 | None = None,
    ) -> RunHandle:
        if prepared is None:
            prepared = PreparedRunContextV1()
        elif not isinstance(prepared, PreparedRunContextV1):
            raise TypeError("prepared must be a host-issued PreparedRunContextV1")
        key, ref = root_run_identity(host.session_id, request.request_id, request.turn_id)
        run_id = ref.run_id
        actor = host.actor(root_run_id=run_id)
        async with self._lock:
            active = self._live.get(run_id) or self._live.add(run_id, actor)
        async with active.start_lock:
            async with self._lock:
                existing = active.record
            if existing is None:
                try:
                    existing = await self._uow.query(ref, actor)
                except RunNotFound:
                    existing = None
            if existing is not None:
                if not isinstance(existing, RunRecord):
                    raise RunNotFound("legacy_run", "legacy projections are outside the active Kernel")
                known = active.prepared_context
                if known is not None:
                    if (
                        known.prepared_fingerprint
                        != prepared.prepared_fingerprint
                    ):
                        raise ValueError("prepared_run_context_replay_conflict")
                elif existing.persistence_level is PersistenceLevel.DURABLE:
                    snapshot = await self._uow.read_run_start_snapshot(run_id)
                    if snapshot is not None:
                        stored_refs = json.loads(snapshot.prepared_refs_json)
                        if (
                            stored_refs.get("prepared_fingerprint")
                            != prepared.prepared_fingerprint
                        ):
                            raise ValueError(
                                "prepared_run_context_replay_conflict"
                            )
                        active.prepared_context = prepared
                        if existing.status not in TERMINAL_RUN_STATUSES:
                            await activate_after_start_commit(
                                record=existing,
                                snapshot=snapshot,
                                prepared=prepared,
                            )
                    elif (
                        prepared.persistence_required
                        or prepared.start_commit_extensions
                        or prepared.after_start_commit_handshakes
                    ):
                        raise RuntimeError(
                            "legacy durable Run has no trusted start snapshot"
                        )
                await self._configure_active_execution_budget(run_id, request)
                return RunHandle(ref, existing.context.root_run_id, existing.spec.driver_kind, existing.spec.profile_key)
            (
                prepared,
                selected_profile_key,
                registration,
                request_capabilities,
                trusted_capability_snapshot,
                spec,
                run_start_snapshot,
                association_event,
            ) = self._build_root_start(
                key=key,
                run_id=run_id,
                request=request,
                host=host,
                prepared=prepared,
            )
            active.prepared_context = prepared
            if request.admission is not None:
                if request.provider_launch_snapshot is None:
                    raise RuntimeError("durable admission requires a frozen provider launch policy")
                decision_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:admission").hex
                nonce = uuid.uuid5(uuid.NAMESPACE_URL, f"{decision_id}:nonce").hex
                boundary = AdmissionBoundary(
                    run_id, decision_id, nonce,
                    uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:launch").hex,
                    registration.kind, selected_profile_key, request.admission,
                    AdmissionPhase.PENDING, 1,
                    request.canonical_messages or ({"role": "user", "content": request.text},),
                    {"text": request.text, **dict(request.payload)},
                    request.provider_launch_snapshot,
                    trusted_capability_snapshot,
                    association_event=None if association_event is None else association_event.to_dict(),
                )
                waiting = RunEventCandidate(
                    event_key="admission:waiting", kind="admission.waiting",
                    status=OutcomeStatus.WAITING, driver_kind=registration.kind,
                    correlation={"decision_id": decision_id, "launch_operation_id": boundary.launch_operation_id},
                    payload={**thaw_json(request.admission.presentation), "run_id": run_id,
                        "decision_id": decision_id, "nonce": nonce, "version": 0,
                        "expires_at": request.admission.expires_at},
                )
                await self._uow.start_admission(
                    spec,
                    request.admission,
                    boundary,
                    waiting,
                    run_start_snapshot=run_start_snapshot,
                    start_commit_extensions=prepared.start_commit_extensions,
                )
                assert run_start_snapshot is not None
                stored_snapshot = await self._uow.read_run_start_snapshot(run_id)
                if stored_snapshot is None:
                    raise RuntimeError("durable admission start snapshot is missing")
                admission_record = await self._uow.query(ref, actor)
                await activate_after_start_commit(
                    record=admission_record,
                    snapshot=stored_snapshot,
                    prepared=prepared,
                )
            elif registration.atomic_start:
                await self._launch_live(
                    active, registration, None,
                    start=build_driver_start(
                        spec,
                        text=request.text,
                        payload=request.payload,
                        canonical_messages=request.canonical_messages,
                        capabilities=request_capabilities,
                        association_event=association_event,
                        run_start_snapshot=run_start_snapshot,
                        prepared_run_context=prepared,
                    ),
                    name=f"deskpet-run:{run_id}",
                )
            else:
                if spec.persistence_level is PersistenceLevel.DURABLE:
                    assert run_start_snapshot is not None
                    result = await self._uow.create_with_start_snapshot(
                        spec,
                        run_start_snapshot,
                        initial_event=association_event,
                        start_commit_extensions=prepared.start_commit_extensions,
                    )
                    result_record = result.record
                    created = result.created
                    stored_snapshot = await self._uow.read_run_start_snapshot(run_id)
                    if stored_snapshot is None:
                        raise RuntimeError("durable Run start snapshot is missing")
                    await activate_after_start_commit(
                        record=result_record,
                        snapshot=stored_snapshot,
                        prepared=prepared,
                    )
                else:
                    result_record = self._ephemeral_record(spec)
                    async with self._lock:
                        if active.record is None:
                            active.record = result_record
                            created = True
                        else:
                            result_record = active.record
                            created = False
                if created:
                    driver_snapshot = (
                        stored_snapshot
                        if spec.persistence_level is PersistenceLevel.DURABLE
                        else None
                    )
                    await self._launch_live(
                        active, registration, result_record,
                        start=build_driver_start(
                            result_record.spec,
                            text=request.text,
                            payload=request.payload,
                            canonical_messages=request.canonical_messages,
                            capabilities=request_capabilities,
                            association_event=association_event,
                            run_start_snapshot=driver_snapshot,
                            prepared_run_context=prepared,
                        ),
                        name=f"deskpet-run:{run_id}",
                    )
            await self._configure_active_execution_budget(run_id, request)
            return RunHandle(
                ref,
                spec.context.root_run_id,
                registration.kind,
                selected_profile_key,
            )

    async def _start_blocked(
        self,
        request: RunRequest,
        host: HostContext,
        *,
        reason_code: RootBlockReasonV1 | str,
        evidence_refs: Sequence[str],
        prepared: PreparedRunContextV1 | None = None,
    ) -> RunHandle:
        """Commit a typed preflight block without launching a Driver."""

        reason = RootBlockReasonV1(reason_code)
        if reason is RootBlockReasonV1.EXTERNAL_DEPENDENCY_UNAVAILABLE:
            raise ValueError(
                "external dependency blocks belong to an already-started Root"
            )
        if prepared is None:
            prepared = PreparedRunContextV1(persistence_required=True)
        elif not isinstance(prepared, PreparedRunContextV1):
            raise TypeError("prepared must be a host-issued PreparedRunContextV1")
        key, ref = root_run_identity(
            host.session_id, request.request_id, request.turn_id
        )
        run_id = ref.run_id
        actor = host.actor(root_run_id=run_id)
        async with self._lock:
            active = self._live.get(run_id) or self._live.add(run_id, actor)
        async with active.start_lock:
            try:
                existing = await self._uow.query(ref, actor)
            except RunNotFound:
                existing = None
            if existing is not None and not isinstance(existing, RunRecord):
                raise RunNotFound(
                    "legacy_run", "legacy projections are outside the active Kernel"
                )
            if existing is None:
                (
                    prepared,
                    selected_profile_key,
                    registration,
                    _request_capabilities,
                    _trusted_capability_snapshot,
                    spec,
                    snapshot,
                    _association_event,
                ) = self._build_root_start(
                    key=key,
                    run_id=run_id,
                    request=request,
                    host=host,
                    prepared=prepared,
                    force_durable=True,
                )
                assert snapshot is not None
            else:
                spec = existing.spec
                selected_profile_key = spec.profile_key
                registration = self._drivers[spec.driver_kind]
                snapshot = await self._uow.read_run_start_snapshot(run_id)
                if snapshot is None:
                    raise RuntimeError("blocked Root start snapshot is missing")
            event_key = f"preflight-block:{reason.value}:final"
            event = RunEventCandidate(
                event_key=event_key,
                kind="run.final",
                status=OutcomeStatus.FAILED,
                driver_kind=registration.kind,
                payload={
                    "kind": "final",
                    "explanation_code": reason.value,
                    "route_availability": "unavailable",
                    "provider_invocation_created": False,
                },
            )
            reporter = RunBlockReporter(
                block_signal_for_terminal(
                    root_run_id=run_id,
                    event_key=event_key,
                    reason_code=reason,
                    evidence_refs=evidence_refs,
                    created_at=snapshot.created_at,
                )
            )
            result = await self._uow.start_blocked_root(
                spec,
                snapshot,
                event=event,
                block_reporter=reporter,
                start_commit_extensions=(
                    prepared.start_commit_extensions if existing is None else ()
                ),
                deliveries=prepared.frozen_terminal_deliveries,
            )
            active.prepared_context = prepared
            async with self._lock:
                active.record = result.record
            if existing is None:
                await activate_after_start_commit(
                    record=result.record,
                    snapshot=snapshot,
                    prepared=prepared,
                )
            await self._notify_terminal(result.record, result.event)
            await self._cleanup_after_terminal_commit(result.record, result.event)
            return RunHandle(
                ref,
                spec.context.root_run_id,
                registration.kind,
                selected_profile_key,
            )

    async def observe(
        self,
        ref: RunRef,
        actor: ActorContext,
        cursor: int | None = None,
    ) -> AsyncIterator[RunEvent]:
        record = await self._query(ref, actor)
        start = max(0, int(cursor or 0))
        queue: asyncio.Queue[RunEvent | None]
        async with self._lock:
            active = self._live.get(ref.run_id) or self._live.add(ref.run_id, actor)
            live_history = self._live.history(active, after_live_seq=start)
            queue = self._live.subscribe(active)

        try:
            # Subscribe before the second durable read.  A terminal commit can
            # land between the first authorization query and subscription;
            # registering first ensures terminal emit/finish is either in this
            # snapshot or queued, never lost between the two.
            refreshed = await self._query(ref, actor)
            durable_history: tuple[RunEvent, ...] = ()
            if refreshed.persistence_level is PersistenceLevel.DURABLE:
                durable_history = await self._uow.list_events(
                    ref.run_id, after_durable_seq=start
                )
            seen = {event.event_id for event in durable_history}
            # Creation time preserves ordering between durable and live history.
            history = tuple(sorted(
                durable_history + tuple(
                    event for event in live_history if event.event_id not in seen
                ),
                key=lambda event: (
                    event.created_at,
                    self._runtime.is_terminal_event(event),
                    event.event_id,
                ),
            ))
            terminal_in_history = refreshed.status in TERMINAL_RUN_STATUSES or any(
                self._runtime.is_terminal_event(event) for event in history
            )
            seen = {event.event_id for event in history}
            if terminal_in_history:
                async with self._lock:
                    current = self._live.get(ref.run_id)
                    if current is not None:
                        current.subscribers.discard(queue)
            for event in history:
                yield event
            if terminal_in_history:
                return
            while True:
                event = await queue.get()
                if event is None:
                    break
                if isinstance(event, LiveStreamOverflow):
                    raise event
                if event.event_id in seen:
                    continue
                seen.add(event.event_id)
                yield event
        finally:
            async with self._lock:
                current = self._live.get(ref.run_id)
                if current is not None and queue in current.subscribers:
                    current.subscribers.discard(queue)

    async def signal(
        self,
        ref: RunRef,
        actor: ActorContext,
        signal: DriverSignal,
    ) -> SignalReceipt:
        record = await self._query(ref, actor)
        if signal.run_id != ref.run_id:
            return SignalReceipt(False, reason="signal_run_binding_mismatch")
        if signal.kind == "user_continuation":
            if record.status in TERMINAL_RUN_STATUSES:
                return SignalReceipt(False, reason="run_already_terminal")
            receipt = await self._continuations.enqueue(
                record, ref, actor, signal
            )
            if receipt.accepted:
                async with self._lock:
                    active = self._live.get(record.run_id) or self._live.add(
                        record.run_id, actor
                    )
                    active.recovery_deferred_until = 0.0
            return receipt
        async with self._lock:
            active = self._live.get(record.run_id) or self._live.add(
                record.run_id, actor
            )
        async with active.control_lock:
            active.recovery_deferred_until = 0.0
            # Re-read after taking the control lane. Another signal may have
            # resolved or terminalized the Run while this caller was queued.
            record = await self._query(ref, actor)
            try:
                return await self._signal_control_locked(
                    ref,
                    actor,
                    signal,
                    record,
                )
            finally:
                # A racing recovery preparation may have deferred just before
                # this signal committed the missing resume condition.
                active.recovery_deferred_until = 0.0

    async def _signal_control_locked(
        self, ref: RunRef, actor: ActorContext,
        signal: DriverSignal,
        record: RunRecord,
    ) -> SignalReceipt:
        registration = self._drivers[record.spec.driver_kind]
        driver_signal = signal
        if signal.kind == "decision":
            if signal.nonce is None or signal.version is None:
                return SignalReceipt(False, reason="decision_fence_required")
            current = await self._uow.get_decision(
                signal.decision_id,
                ref=ref,
                actor=actor,
            )
            request = current.request
            response = dict(signal.response)
            verdict = response.get("decision", response.get("resolution"))
            allow = bool(response.get("allow", response.get("approved", verdict not in {"cancel", "cancelled", "reject", "rejected"})))
            admission = self._admission_from(
                await self._uow.load_continuation(record.run_id)
            )
            admission_task_grant = None
            if admission is not None and allow:
                admission_task_grant = await resolve_admission_task_grant(
                    self._admission_authorizer, record, admission, actor
                )
                if admission_task_grant is not None:
                    response.update(task_grant_receipt_payload(admission_task_grant))
            durable_signal = DurableDecisionSignal(
                decision_id=request.decision_id,
                run_id=request.run_id,
                expected_session_id=ref.expected_session_id,
                nonce=signal.nonce,
                expected_version=signal.version,
                allow=allow,
                response_schema_version=1,
                response=response,
                domain_kind=request.domain_kind,
                domain_id=request.domain_id,
                call_id=request.call_id,
                effect_id=request.effect_id,
                tool_name=request.tool_name,
                args_hash=request.args_hash,
                capability_hash=request.capability_hash,
                scope_hash=request.scope_hash,
            )
            if admission is not None:
                execution_fence = await self._acquire_run_execution_fence(
                    record.run_id
                )
                try:
                    resolved = await self._uow.resolve_admission(
                        ref,
                        actor,
                        durable_signal,
                        expected_boundary_version=admission.boundary_version,
                        terminal_deliveries=await self._terminal_deliveries(
                            record
                        ),
                        admission_task_grant=admission_task_grant,
                    )
                finally:
                    await release_run_execution_fence(execution_fence)
                await self._runtime.emit(resolved.event)
                if not resolved.boundary.consumed:
                    await self._start_admitted(record, registration, actor)
                return SignalReceipt(True, duplicate=resolved.duplicate)
            if current.status is not DecisionStatus.OPEN:
                expected_status = DecisionStatus.ALLOWED if allow else DecisionStatus.DENIED
                if (current.status is expected_status and current.response_schema_version == 1
                        and dict(current.response or {}) == response
                        and current.decision_version == signal.version + 1
                        and request.nonce == signal.nonce):
                    return SignalReceipt(True, duplicate=True, reason="decision_already_applied")
                raise DecisionConflict("decision_replay_conflict", "resolved decision replay differs from the authoritative response")
            atomic_signal = getattr(registration.driver, "signal_decision_atomically", None)
            if callable(atomic_signal):
                candidates = atomic_signal(driver_signal, durable_signal, actor)
            else:
                resolved, authorization = await self._uow.commit_decision(
                    durable_signal, actor,
                )
                response["decision_status"] = resolved.status.value
                if authorization is not None:
                    response["grant_id"] = authorization.grant_id
                    response["grant_version"] = authorization.version
                driver_signal = DriverDecisionSignal(
                    run_id=signal.run_id,
                    decision_id=signal.decision_id,
                    response=response,
                    nonce=signal.nonce,
                    version=signal.version,
                )
                candidates = registration.driver.signal(driver_signal)
        else:
            candidates = registration.driver.signal(driver_signal)
        try:
            await self._runtime.consume(
                registration,
                record,
                candidates,
            )
        except TerminalConflict:
            current = await self._query(ref, actor)
            if current.status in TERMINAL_RUN_STATUSES:
                return SignalReceipt(
                    True, duplicate=True, reason="terminal_already_settled"
                )
            raise
        return SignalReceipt(True)

    async def cancel(self, ref: RunRef, actor: ActorContext, reason: str) -> CancelReceipt:
        record = await self._query(ref, actor)
        async with self._lock:
            active = self._live.get(record.run_id) or self._live.add(record.run_id, actor)
        async with active.start_lock:
            while True:
                record = await self._query(ref, actor)
                if record.status in TERMINAL_RUN_STATUSES:
                    return CancelReceipt(ref.run_id, record.status, record.status is RunStatus.CANCELLED)
                admission = self._admission_from(await self._uow.load_continuation(
                    record.run_id))
                if admission is not None and admission.phase in {AdmissionPhase.PENDING, AdmissionPhase.ACCEPTED_START_PENDING}:
                    execution_fence = await self._acquire_run_execution_fence(
                        record.run_id
                    )
                    try:
                        resolved = await self._uow.resolve_admission(
                            ref,
                            actor,
                            DurableDecisionSignal(
                                admission.decision_id,
                                record.run_id,
                                ref.expected_session_id,
                                admission.nonce,
                                admission.boundary_version - 1,
                                False,
                                1,
                                {"resolution": "cancelled"},
                            ),
                            expected_boundary_version=(
                                admission.boundary_version
                            ),
                            terminal_deliveries=(
                                await self._terminal_deliveries(record)
                            ),
                        )
                    finally:
                        await release_run_execution_fence(
                            execution_fence
                        )
                    await self._runtime.emit(resolved.event)
                    return CancelReceipt(record.run_id, RunStatus.CANCELLED, True)
                if record.status is RunStatus.CANCEL_REQUESTED:
                    break
                event = RunEventCandidate(
                    event_key=f"cancel-request:{ref.run_id}", kind="cancel_requested",
                    status=OutcomeStatus.CANCEL_REQUESTED, driver_kind=record.spec.driver_kind,
                    correlation={"request_id": record.context.request_id},
                    payload={"reason": reason})
                try:
                    await self._request_cancel(
                        record, expected_version=record.version,
                        reason=reason, event=event)
                    break
                except VersionConflict:
                    continue
            await self._continuations.interrupt_owner(active)
            current = await self._query(ref, actor)
            if current.persistence_level is PersistenceLevel.DURABLE:
                await self._continuations.fail_pending(
                    ref.run_id, "run_cancelled", record=current
                )
                await self._uow.cancel_open_decisions(
                    ref, actor, expected_run_version=current.version)
                links = await self._uow.list_child_links(ref, actor)
            else:
                links = ()
            for link in links:
                if link.attachment_policy is AttachmentPolicy.DETACHED:
                    continue
                child_ref = RunRef(link.child_run_id, ref.expected_session_id)
                child = await self._query(child_ref, actor)
                if child.status not in TERMINAL_RUN_STATUSES:
                    await self.cancel(child_ref, actor, f"parent_cancel:{ref.run_id}")
            registration = self._drivers[current.spec.driver_kind]
            async with active.driver_lock:
                acknowledged = await self._runtime.consume(
                    registration, current,
                    registration.driver.cancel(current.run_id, reason))
            current = await self._query(ref, actor)
            if acknowledged and current.status not in TERMINAL_RUN_STATUSES:
                await self._runtime.commit_terminal(
                    current, registration.kind,
                    DriverTerminalCandidate(current.run_id, "cancelled", error=reason))
                current = await self._query(ref, actor)
            return CancelReceipt(ref.run_id, current.status, acknowledged)

    async def recover(self, ref: RunRef, actor: ActorContext) -> RunHandle:
        record = await self._query(ref, actor)
        registration = self._drivers[record.spec.driver_kind]
        handle = RunHandle(ref, record.context.root_run_id, record.spec.driver_kind, record.spec.profile_key)
        if (record.persistence_level is PersistenceLevel.EPHEMERAL
                or record.status in TERMINAL_RUN_STATUSES):
            return handle
        if record.status is RunStatus.CANCEL_REQUESTED:
            await self.cancel(ref, actor, record.cancel_reason or "recovered cancellation")
            return handle
        continuation = await self._uow.load_continuation(record.run_id)
        admission = self._admission_from(continuation)
        if admission is not None and admission.phase is not AdmissionPhase.LAUNCHED:
            if admission.phase is AdmissionPhase.PENDING or admission.consumed:
                return handle
            await self._start_admitted(record, registration, actor)
            return handle
        async with self._lock:
            active = self._live.get(ref.run_id) or self._live.add(ref.run_id, actor)
        async with active.start_lock:
            if (
                active.recovery_deferred_until
                > asyncio.get_running_loop().time()
            ):
                return handle
            record = await self._query(ref, actor)
            if (record.status in TERMINAL_RUN_STATUSES
                    or record.status is RunStatus.CANCEL_REQUESTED):
                return handle
            owner = active.task
            async with active.driver_lock:
                if active.task is not owner:
                    return handle
                if owner is not None:
                    if not owner.done():
                        await asyncio.gather(owner, return_exceptions=True)
                    record = await self._query(ref, actor)
                    if (record.status in TERMINAL_RUN_STATUSES
                            or record.status is RunStatus.CANCEL_REQUESTED):
                        return handle
                    failed = owner.cancelled() or owner.exception() is not None
                    effect_ready = (self._tool_executor is not None
                                    and ref.run_id in self._tool_executor.ready_run_ids())
                    if not failed and not effect_ready:
                        return handle
                await self._launch_live(
                    active, registration, record,
                    name=f"deskpet-recover:{ref.run_id}")
        await self._continuations.ensure_drain(active, ref, actor)
        return handle

    async def close(self, ref: RunRef, actor: ActorContext) -> None:
        record = await self._query(ref, actor)
        async with self._lock:
            active = self._live.get(ref.run_id)
            tasks = (
                ()
                if active is None
                else tuple(
                    (active.task,) if active.task is not None else ()
                )
            )
        terminal_tasks = tuple(
            task
            for task in tasks
            if record.status in TERMINAL_RUN_STATUSES
            and task is not asyncio.current_task()
        )
        if terminal_tasks:
            await asyncio.gather(*terminal_tasks, return_exceptions=True)
        async with self._lock:
            active = self._live.get(ref.run_id)
            if active is None or (
                active.task is not None and not active.task.done()
            ):
                return
            self._live.finish(ref.run_id, active)
            self._live.pop(ref.run_id)

    async def _accept_precreated_child(self, command: ChildCommandRecord) -> None:
        spec = command.intent.child_spec
        actor = spec.context.actor()
        record = await self._query(RunRef(spec.run_id, actor.session_id), actor)
        if record.status in TERMINAL_RUN_STATUSES:
            async with self._lock:
                self._live.pop(record.run_id)
            return
        async with self._lock:
            active = self._live.get(record.run_id) or self._live.add(record.run_id, actor)
            active.record = record
        async with active.start_lock:
            start_snapshot = await self._uow.read_run_start_snapshot(
                record.run_id
            )
            if start_snapshot is None:
                raise RuntimeError(
                    "precreated child has no trusted start snapshot"
                )
            prepared_child_context = None
            if self._child_runs is not None:
                prepared_child_context = (
                    await self._child_runs.activate_child_snapshot_after_commit(
                        start_snapshot
                    )
                )
            active.prepared_context = prepared_child_context
            if await self._uow.load_continuation(record.run_id) is not None:
                await self._launch_live(active, self._drivers[record.spec.driver_kind],
                                        record, name=f"deskpet-recover:{record.run_id}")
                return
            registration = self._drivers[record.spec.driver_kind]
            payload = thaw_json(command.intent.child_request)
            assert isinstance(payload, dict)
            text = str(payload.get("text") or payload.get("task") or payload.get("request") or "")
            await self._launch_live(
                active, registration, record,
                start=build_driver_start(
                    spec, text=text, payload=payload,
                    capabilities=command.intent.capability_subset,
                    run_start_snapshot=start_snapshot,
                    prepared_run_context=prepared_child_context,
                ),
                name=f"deskpet-child:{command.operation_id}",
            )
            await asyncio.sleep(0)

    async def _deliver_child_signal(
        self,
        parent: RunRecord,
        signal: DriverSignal,
        recovery_lease: RecoveryLease,
    ) -> bool:
        return await self._child_signal_runtime.deliver(
            parent,
            signal,
            recovery_lease,
            self._drivers[parent.spec.driver_kind],
        )

    async def _deliver_child_signal_inline(
        self,
        parent: RunRecord,
        signal: DriverSignal,
        recovery_lease: RecoveryLease,
    ) -> None:
        await self._child_signal_runtime.deliver_inline(
            parent,
            signal,
            recovery_lease,
            self._drivers[parent.spec.driver_kind],
        )

    def _child_signal_in_flight(self, run_id: str) -> bool:
        return self._child_signal_runtime.in_flight(run_id)

    @staticmethod
    def _admission_from(continuation) -> AdmissionBoundary | None:
        return admission_from(continuation)

    async def _terminal_deliveries(self, record: RunRecord):
        frozen = ()
        active = self._live.get(record.run_id)
        prepared = None if active is None else active.prepared_context
        if prepared is not None:
            frozen = prepared.frozen_terminal_deliveries
        elif record.persistence_level is PersistenceLevel.DURABLE:
            snapshot = await self._uow.read_run_start_snapshot(record.run_id)
            if snapshot is not None:
                frozen = terminal_deliveries_from_snapshot(snapshot)
        projected = (() if self._terminal_projection is None else
            self._terminal_projection.deliveries(
                record, await self._uow.list_events(record.run_id)))
        merged = {
            (
                item.sink_kind,
                item.sink_instance,
                item.target_id,
                item.policy.value,
            ): item
            for item in (*frozen, *projected)
        }
        return tuple(merged[key] for key in sorted(merged))

    @staticmethod
    def _admission_start(spec: RunCreate, boundary: AdmissionBoundary,
        claim) -> DriverStart:
        return build_admission_start(spec, boundary, claim)
    async def _start_admitted(self, record: RunRecord, registration: RegisteredDriver,
                              actor: ActorContext) -> None:
        await self._admission_launcher.start(record, registration, actor)

    async def _consume_admitted(self, registration: RegisteredDriver, record: RunRecord,
                                stream, lease) -> None:
        await self._admission_launcher.consume(registration, record, stream, lease)

    async def _fail_unknown_launch(
        self, record: RunRecord, boundary: AdmissionBoundary) -> None:
        await self._admission_launcher.fail_unknown(record, boundary)

def kernel_public_operations() -> tuple[str, ...]:
    return ("start", "observe", "signal", "cancel", "recover", "close")

"""Start preparation, prepared and precreated runs, and workflow start."""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from deskpet.execution.contracts import (
    AdmissionLaunchClaim,
    DeliveryPolicy,
    DeliverySpec,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunStartSnapshotRecord,
    RunStatus,
    WorkflowRunSeed,
    WorkflowSessionRef,
    fingerprint_json,
)
from deskpet.execution.contracts import stable_event_id as stable_execution_event_id

from ..contracts import JsonValue, WorkflowContext, validate_json_value
from ..execution_ports import WorkflowExecutionPorts
from ..runner import manifest_hash
from .core import WorkflowServiceCore
from .support import START_SNAPSHOT_KEY, WorkflowServiceError, WorkflowStartIdentity, _hash_json


@dataclass(frozen=True, slots=True)
class PreparedWorkflowStart:
    """Pure, immutable-enough description of one workflow start intent."""

    identity: WorkflowStartIdentity
    workflow_version: str
    manifest_hash: str
    implementation_hash: str
    state_schema_version: int
    capability_snapshot: Mapping[str, JsonValue]
    capability_hash: str
    start_payload: Mapping[str, JsonValue]
    args_hash: str
    request_hash: str
    delivery_targets: tuple[tuple[str, str], ...]
    run_id: str
    trace_id: str
    thread_id: str
    checkpoint_ns: str

    @property
    def profile_key(self) -> str:
        return f"{self.identity.workflow_name}/{self.workflow_version}"


class LaunchMixin(WorkflowServiceCore):
    """Start preparation, prepared and precreated runs, and workflow start."""

    __slots__ = ()

    def prepare_start(
        self,
        *,
        venue: str,
        base_session_id: str,
        delivery_session_id: str,
        request_id: str,
        turn_id: str,
        workflow_name: str,
        workflow_version: str,
        capability_snapshot: Mapping[str, JsonValue],
        start_payload: Mapping[str, JsonValue] | None = None,
        code_session_id: str | None = None,
        base_epoch: int = 0,
        code_epoch: int = 0,
        logical_slot: str = "accepted_async:0",
        delivery_targets: Sequence[tuple[str, str] | Mapping[str, str]] | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        thread_id: str | None = None,
        checkpoint_ns: str = "",
    ) -> PreparedWorkflowStart:
        """Validate and hash a start without touching SQLite or an outbox."""

        if self.runner is None or getattr(self.runner, "registry", None) is None:
            raise WorkflowServiceError(
                "workflow_start_unavailable",
                "The workflow runner does not expose a version registry",
            )
        if (
            isinstance(base_epoch, bool)
            or base_epoch < 0
            or isinstance(code_epoch, bool)
            or code_epoch < 0
        ):
            raise WorkflowServiceError("invalid_request", "session epochs must be non-negative")
        identity = WorkflowStartIdentity(
            venue=self._required(venue, "venue"),
            base_session_id=self._required(base_session_id, "base_session_id"),
            code_session_id=str(code_session_id or ""),
            delivery_session_id=self._required(delivery_session_id, "delivery_session_id"),
            base_epoch=int(base_epoch),
            code_epoch=int(code_epoch),
            request_id=self._required(request_id, "request_id"),
            turn_id=self._required(turn_id, "turn_id"),
            workflow_name=self._required(workflow_name, "workflow_name"),
            logical_slot=self._required(logical_slot, "logical_slot"),
        )
        version = self._required(workflow_version, "workflow_version")
        capabilities = copy.deepcopy(dict(capability_snapshot))
        args = copy.deepcopy(dict(start_payload or {}))
        validate_json_value(capabilities, path="$.capability_snapshot")
        validate_json_value(args, path="$.start_payload")
        if START_SNAPSHOT_KEY in capabilities:
            raise WorkflowServiceError(
                "reserved_snapshot_key",
                f"capability_snapshot may not contain {START_SNAPSHOT_KEY}",
            )
        manifest = self._manifest(identity.workflow_name, version)
        original_capability_hash = _hash_json(capabilities)
        args_hash = _hash_json(args)
        request_hash = _hash_json(
            {
                "workflow_version": version,
                "definition_hash": str(
                    getattr(
                        manifest,
                        "definition_hash",
                        getattr(manifest, "implementation_bundle_hash", "unversioned"),
                    )
                ),
                "args_hash": args_hash,
                "capability_hash": original_capability_hash,
            }
        )
        persisted_snapshot: dict[str, JsonValue] = {
            **capabilities,
            START_SNAPSHOT_KEY: {
                "identity": identity.to_dict(),
                "identity_key": identity.identity_key,
                "request_hash": request_hash,
                "args_hash": args_hash,
                "capability_hash": original_capability_hash,
                "start_payload": args,
            },
        }
        normalized_targets: list[tuple[str, str]] = []
        raw_targets = delivery_targets or (
            ("session_message", identity.delivery_session_id),
            ("websocket", identity.delivery_session_id),
        )
        for target in raw_targets:
            if isinstance(target, Mapping):
                channel = str(target.get("channel") or target.get("sink_kind") or "").strip()
                target_id = str(target.get("target_id") or "").strip()
            else:
                channel, target_id = (str(target[0]).strip(), str(target[1]).strip())
            if not channel or not target_id:
                raise WorkflowServiceError(
                    "invalid_request", "delivery target requires channel and target_id"
                )
            normalized_targets.append((channel, target_id))
        if len(set(normalized_targets)) != len(normalized_targets):
            raise WorkflowServiceError("invalid_request", "delivery targets must be unique")
        resolved_run_id = str(run_id or uuid.uuid5(uuid.NAMESPACE_URL, identity.identity_key).hex)
        resolved_trace_id = str(
            trace_id or uuid.uuid5(uuid.NAMESPACE_URL, f"trace:{identity.identity_key}").hex
        )
        return PreparedWorkflowStart(
            identity=identity,
            workflow_version=version,
            manifest_hash=manifest_hash(manifest),
            implementation_hash=str(manifest.implementation_bundle_hash),
            state_schema_version=int(manifest.state_schema_version),
            capability_snapshot=persisted_snapshot,
            capability_hash=fingerprint_json(persisted_snapshot),
            start_payload=args,
            args_hash=args_hash,
            request_hash=request_hash,
            delivery_targets=tuple(normalized_targets),
            run_id=resolved_run_id,
            trace_id=resolved_trace_id,
            thread_id=str(thread_id or resolved_run_id),
            checkpoint_ns=str(checkpoint_ns or ""),
        )

    @staticmethod
    def execution_spec(
        prepared: PreparedWorkflowStart,
        *,
        principal_id: str | None = None,
        workspace: Mapping[str, JsonValue] | None = None,
        provider_plan: Mapping[str, JsonValue] | None = None,
    ) -> RunCreate:
        """Build the generic root-run contract for DR/PPT/Code workflow profiles."""

        identity = prepared.identity
        context = RunContext(
            session_id=identity.base_session_id,
            root_run_id=prepared.run_id,
            parent_run_id=None,
            request_id=identity.request_id,
            turn_id=identity.turn_id,
            venue=identity.venue,
            workspace=dict(workspace or {}),
            capability_hash=prepared.capability_hash,
            provider_plan=dict(
                provider_plan
                or {
                    "driver_kind": "workflow",
                    "workflow_name": identity.workflow_name,
                    "workflow_version": prepared.workflow_version,
                }
            ),
            trace_id=prepared.trace_id,
            principal_id=str(principal_id or identity.base_session_id),
            auth_epoch=identity.base_epoch,
        )
        return RunCreate(
            run_id=prepared.run_id,
            idempotency_key=f"root:{identity.identity_key}",
            context=context,
            payload_fingerprint=prepared.request_hash,
            capability_fingerprint=prepared.capability_hash,
            driver_kind="workflow",
            profile_key=prepared.profile_key,
            persistence_level=PersistenceLevel.DURABLE,
            status=RunStatus.CREATED,
        )

    async def start_prepared(
        self,
        prepared: PreparedWorkflowStart,
        spec: RunCreate,
        *,
        execution_ports: WorkflowExecutionPorts | None = None,
        association_event: RunEventCandidate | None = None,
        admission_launch: AdmissionLaunchClaim | None = None,
        kernel_owned_spec: bool = False,
        start_snapshot: RunStartSnapshotRecord | None = None,
        start_commit_extensions: Sequence[Any] = (),
    ) -> dict[str, Any]:
        """Atomically create generic + workflow + accepted facts (opt-in only)."""

        ports = execution_ports or self.execution_ports
        if ports is None:
            raise WorkflowServiceError(
                "workflow_execution_ports_required",
                "precreated workflow start requires explicit execution ports",
            )
        identity = prepared.identity
        expected = {
            "run_id": prepared.run_id,
            "driver_kind": "workflow",
        }
        if admission_launch is None:
            if not kernel_owned_spec:
                expected.update(
                    {
                        "profile_key": prepared.profile_key,
                        "payload_fingerprint": prepared.request_hash,
                        "capability_fingerprint": prepared.capability_hash,
                    }
                )
        else:
            boundary = admission_launch.boundary
            if (
                boundary.run_id != spec.run_id
                or boundary.driver_kind != spec.driver_kind
                or boundary.profile_key != spec.profile_key
            ):
                raise WorkflowServiceError(
                    "workflow_admission_spec_conflict",
                    "admission claim differs from the Kernel-frozen execution spec",
                )
        if any(getattr(spec, key) != value for key, value in expected.items()):
            raise WorkflowServiceError(
                "workflow_execution_spec_conflict",
                "generic execution spec differs from the prepared workflow start",
            )
        context_expected = {
            "session_id": identity.base_session_id,
            "request_id": identity.request_id,
            "turn_id": identity.turn_id,
            "venue": identity.venue,
            "trace_id": prepared.trace_id,
            "auth_epoch": identity.base_epoch,
        }
        if not kernel_owned_spec:
            context_expected.update(
                {
                    "root_run_id": prepared.run_id,
                    "parent_run_id": None,
                }
            )
        if admission_launch is None and not kernel_owned_spec:
            context_expected["capability_hash"] = prepared.capability_hash
        if any(getattr(spec.context, key) != value for key, value in context_expected.items()):
            raise WorkflowServiceError(
                "workflow_execution_context_conflict",
                "generic run context differs from the prepared workflow start",
            )
        refs = tuple(
            WorkflowSessionRef(kind, session_id, epoch)
            for kind, session_id, epoch in self._session_refs(identity)
        )
        seed = WorkflowRunSeed(
            request_key=identity.identity_key,
            workflow_name=identity.workflow_name,
            workflow_version=prepared.workflow_version,
            manifest_hash=prepared.manifest_hash,
            implementation_hash=prepared.implementation_hash,
            capability_hash=prepared.capability_hash,
            capability_snapshot=prepared.capability_snapshot,
            state_schema_version=prepared.state_schema_version,
            trace_id=prepared.trace_id,
            thread_id=prepared.thread_id,
            checkpoint_ns=prepared.checkpoint_ns,
            session_refs=refs,
        )
        card: dict[str, JsonValue] = {
            "run_id": prepared.run_id,
            "request_id": identity.request_id,
            "turn_id": identity.turn_id,
            "workflow_name": identity.workflow_name,
            "workflow_version": prepared.workflow_version,
            "status": "running",
            "recovery_action": None,
            "error": None,
        }
        accepted_event = RunEventCandidate(
            event_key="run:create:accepted",
            kind="workflow.accepted",
            status=OutcomeStatus.ACCEPTED,
            driver_kind="workflow",
            correlation={
                "request_id": identity.request_id,
                "turn_id": identity.turn_id,
                "identity_key": identity.identity_key,
            },
            payload={
                "kind": "accepted",
                "status": "running",
                "identity_key": identity.identity_key,
                "request_hash": prepared.request_hash,
                "card": card,
            },
        )
        deliveries = tuple(
            DeliverySpec(
                sink_kind=channel,
                sink_instance="workflow",
                target_id=target_id,
                policy=(
                    DeliveryPolicy.DURABLE_REQUIRED
                    if channel in {"session_message", "receipt", "artifact"}
                    else DeliveryPolicy.RETRY_WHILE_BOUND
                ),
            )
            for channel, target_id in prepared.delivery_targets
        )
        result = await ports.unit_of_work.start_workflow(
            spec,
            seed,
            start_snapshot=start_snapshot,
            start_commit_extensions=start_commit_extensions,
            association_event=association_event,
            accepted_event=accepted_event,
            deliveries=deliveries,
            admission_launch=admission_launch,
        )
        return {
            "run_id": result.record.run_id,
            "created": result.workflow_created,
            "execution_created": result.execution_created,
            "workflow_created": result.workflow_created,
            "admission_consumed": result.admission_consumed,
            "start_claimed": result.start_claimed,
            "identity_key": identity.identity_key,
            "request_hash": prepared.request_hash,
            "accepted_event_id": stable_execution_event_id(
                result.record.run_id, accepted_event.event_key
            ),
        }

    async def run_precreated(
        self,
        run_id: str,
        state: object,
        context: WorkflowContext | None = None,
        *,
        active_lease: Any | None = None,
    ) -> Any:
        runner = self.runner
        call = getattr(runner, "run_precreated", None)
        if not callable(call):
            raise WorkflowServiceError(
                "workflow_execution_ports_required",
                "workflow runner cannot drive a precreated execution",
            )
        return await call(
            str(run_id),
            state,
            context,
            **({} if active_lease is None else {"active_lease": active_lease}),
        )

    async def resume_precreated(
        self,
        run_id: str,
        responses: Mapping[str, JsonValue],
        context: WorkflowContext | None = None,
        *,
        active_lease: Any | None = None,
    ) -> Any:
        runner = self.runner
        call = getattr(runner, "resume_precreated", None)
        if not callable(call):
            raise WorkflowServiceError(
                "workflow_execution_ports_required",
                "workflow runner cannot resume a precreated execution",
            )
        return await call(
            str(run_id),
            responses,
            context,
            **({} if active_lease is None else {"active_lease": active_lease}),
        )

    async def cancel_precreated(self, run_id: str, reason: str = "user") -> dict[str, Any]:
        runner = self.runner
        call = getattr(runner, "request_cancel_precreated", None)
        if not callable(call):
            raise WorkflowServiceError(
                "workflow_execution_ports_required",
                "workflow runner cannot cancel a precreated execution",
            )
        return await call(str(run_id), reason)

    async def start_workflow(
        self,
        *,
        venue: str,
        base_session_id: str,
        delivery_session_id: str,
        request_id: str,
        turn_id: str,
        workflow_name: str,
        workflow_version: str,
        capability_snapshot: Mapping[str, JsonValue],
        start_payload: Mapping[str, JsonValue] | None = None,
        code_session_id: str | None = None,
        base_epoch: int = 0,
        code_epoch: int = 0,
        logical_slot: str = "accepted_async:0",
        delivery_targets: Sequence[tuple[str, str] | Mapping[str, str]] | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        thread_id: str | None = None,
        checkpoint_ns: str = "",
    ) -> dict[str, Any]:
        self.ensure_runtime_active()
        if self.runner is None or not callable(getattr(self.runner, "start", None)):
            raise WorkflowServiceError(
                "workflow_start_unavailable", "The workflow runner cannot start workflows"
            )
        if (
            isinstance(base_epoch, bool)
            or base_epoch < 0
            or isinstance(code_epoch, bool)
            or code_epoch < 0
        ):
            raise WorkflowServiceError("invalid_request", "session epochs must be non-negative")
        identity = WorkflowStartIdentity(
            venue=self._required(venue, "venue"),
            base_session_id=self._required(base_session_id, "base_session_id"),
            code_session_id=str(code_session_id or ""),
            delivery_session_id=self._required(delivery_session_id, "delivery_session_id"),
            base_epoch=int(base_epoch),
            code_epoch=int(code_epoch),
            request_id=self._required(request_id, "request_id"),
            turn_id=self._required(turn_id, "turn_id"),
            workflow_name=self._required(workflow_name, "workflow_name"),
            logical_slot=self._required(logical_slot, "logical_slot"),
        )
        version = self._required(workflow_version, "workflow_version")
        capabilities = dict(capability_snapshot)
        args = dict(start_payload or {})
        validate_json_value(capabilities, path="$.capability_snapshot")
        validate_json_value(args, path="$.start_payload")
        if START_SNAPSHOT_KEY in capabilities:
            raise WorkflowServiceError(
                "reserved_snapshot_key",
                f"capability_snapshot may not contain {START_SNAPSHOT_KEY}",
            )
        manifest = self._manifest(identity.workflow_name, version)
        capability_hash = _hash_json(capabilities)
        args_hash = _hash_json(args)
        request_hash = _hash_json(
            {
                "workflow_version": version,
                "definition_hash": str(
                    getattr(
                        manifest,
                        "definition_hash",
                        getattr(manifest, "implementation_bundle_hash", "unversioned"),
                    )
                ),
                "args_hash": args_hash,
                "capability_hash": capability_hash,
            }
        )
        persisted_snapshot: dict[str, JsonValue] = {
            **capabilities,
            START_SNAPSHOT_KEY: {
                "identity": identity.to_dict(),
                "identity_key": identity.identity_key,
                "request_hash": request_hash,
                "args_hash": args_hash,
                "capability_hash": capability_hash,
                "start_payload": args,
            },
        }
        lock = self._start_locks.setdefault(identity.identity_key, asyncio.Lock())
        async with lock:
            existing = await self._start_record(identity.identity_key)
            self._assert_same_start(existing, request_hash)
            resolved_run_id = await self.runner.start(
                session_id=identity.base_session_id,
                request_id=identity.request_id,
                turn_id=identity.turn_id,
                workflow_name=identity.workflow_name,
                workflow_version=version,
                capability_snapshot=persisted_snapshot,
                request_key=identity.identity_key,
                capability_hash=_hash_json(persisted_snapshot),
                run_id=run_id,
                trace_id=trace_id,
                thread_id=thread_id,
                checkpoint_ns=checkpoint_ns,
            )
            await self.require_legacy_owner(str(resolved_run_id), "start")
            persisted = await self._start_record(identity.identity_key)
            self._assert_same_start(persisted, request_hash)
            await self.run_store.bind_session_refs(
                str(resolved_run_id),
                self._session_refs(identity),
            )
            targets = list(
                delivery_targets
                or (
                    ("session_message", identity.delivery_session_id),
                    ("websocket", identity.delivery_session_id),
                )
            )
            card: dict[str, JsonValue] = {
                "run_id": str(resolved_run_id),
                "request_id": identity.request_id,
                "turn_id": identity.turn_id,
                "workflow_name": identity.workflow_name,
                "workflow_version": version,
                "status": "running",
                "recovery_action": None,
                "error": None,
            }
            accepted = await self.outbox.ensure_event(
                run_id=str(resolved_run_id),
                event_key="run:create:accepted",
                event_type="workflow.accepted",
                payload={
                    "kind": "accepted",
                    "run_id": str(resolved_run_id),
                    "request_id": identity.request_id,
                    "turn_id": identity.turn_id,
                    "workflow_name": identity.workflow_name,
                    "workflow_version": version,
                    "status": "running",
                    "identity_key": identity.identity_key,
                    "request_hash": request_hash,
                    "card": card,
                },
                deliveries=targets,
            )
            return {
                "run_id": str(resolved_run_id),
                "created": existing is None,
                "identity_key": identity.identity_key,
                "request_hash": request_hash,
                "accepted_event_id": accepted["event_id"],
                "accepted_event": accepted,
            }

    start = start_workflow

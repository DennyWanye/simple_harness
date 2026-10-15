# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Workflow spawn catalog publication, launch-ticket issue and continuation claims."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING

from simple_harness.contracts import JsonValue, canonical_json
from simple_harness.execution.effects import EffectRecord, EffectState
from simple_harness.execution.fences import RunFenceLease
from simple_harness.execution.recovery import RecoveryKind, WaitBlockerRecord
from simple_harness.execution.uow import (
    ExecutionLease,
    FaultHook,
    RunState,
    UnitOfWorkConflict,
    UnitOfWorkNotFound,
)
from simple_harness.runtime.orchestration import (
    VerifiedWorkflowCatalogAuthority,
    WorkflowCatalogAuthority,
    WorkflowCatalogProfileBinding,
    WorkflowLaunchRequest,
    WorkflowLaunchTicket,
    WorkflowSpawnContinuationClaim,
    WorkflowSpawnContinuationReady,
    WorkflowSpawnIssueAuthority,
)
from simple_harness.tools.schema import (
    ArgumentsValidationError,
    SchemaDefinitionError,
    validate_arguments,
)
from simple_harness.workflow.execution_ports import WorkflowTransaction
from simple_harness.workflow.lease import WorkflowLease

if TYPE_CHECKING:
    from simple_harness.runtime.orchestration import (
        VerifiedWorkflowCatalogAuthority,
        VerifiedWorkflowLaunchTicket,
        WorkflowCatalogAuthority,
        WorkflowLaunchRequest,
        WorkflowLaunchTicket,
        WorkflowSpawnContinuationClaim,
        WorkflowSpawnContinuationReady,
        WorkflowSpawnIssueAuthority,
    )
from .core import ExecutionUnitOfWorkCore
from .support import (
    _effect_record,
    _fault,
    _json_object,
    _optional_json_object,
    _positive_ttl,
    _required,
    _start_input_schema_from_json,
    _thaw,
    _time,
    _verified_workflow_launch_ticket,
    _wait_blocker_record,
    _workflow_launch_request_from_json,
    _workflow_launch_ticket,
    _workflow_spawn_continuation_claim,
    _workflow_spawn_continuation_ready,
)


def _workflow_catalog_profiles_json(authority: WorkflowCatalogAuthority) -> str:
    return canonical_json([_workflow_catalog_profile_json(item) for item in authority.profiles])


def _workflow_catalog_profile_json(item: object) -> dict[str, JsonValue]:

    if not isinstance(item, WorkflowCatalogProfileBinding):
        raise TypeError("workflow catalog profile binding must be typed")
    return {
        "profile_key": item.profile_key,
        "description": item.description,
        "use_when": item.use_when,
        "avoid_when": item.avoid_when,
        "input_schema_ref": item.input_schema_ref,
        "profile_fingerprint": item.profile_fingerprint,
        "workflow_name": item.workflow_name,
        "workflow_version": item.workflow_version,
        "implementation_fingerprint": item.implementation_fingerprint,
        "checkpoint_namespace": item.checkpoint_namespace,
        "manifest_hash": item.manifest_hash,
        "state_schema_version": item.state_schema_version,
        "start_input_schema": item.start_input_schema.to_json(),
        "terminal_projection_descriptor": (
            None
            if item.terminal_projection_descriptor is None
            else _thaw(item.terminal_projection_descriptor)
        ),
        "terminal_request_factory_hash": item.terminal_request_factory_hash,
        "capability_snapshot": _thaw(item.capability_snapshot),
    }


def _workflow_catalog_authority(row: sqlite3.Row) -> WorkflowCatalogAuthority:

    raw_profiles = json.loads(str(row["canonical_profiles"]))
    if not isinstance(raw_profiles, list):
        raise UnitOfWorkConflict("stored workflow catalog profiles are invalid")
    profiles = []
    for raw in raw_profiles:
        if not isinstance(raw, dict):
            raise UnitOfWorkConflict("stored workflow catalog profile is invalid")
        profiles.append(
            WorkflowCatalogProfileBinding(
                profile_key=_required(raw.get("profile_key"), "profile.profile_key"),
                description=_required(raw.get("description"), "profile.description"),
                use_when=_required(raw.get("use_when"), "profile.use_when"),
                avoid_when=_required(raw.get("avoid_when"), "profile.avoid_when"),
                input_schema_ref=_required(raw.get("input_schema_ref"), "profile.input_schema_ref"),
                profile_fingerprint=_required(
                    raw.get("profile_fingerprint"), "profile.profile_fingerprint"
                ),
                workflow_name=_required(raw.get("workflow_name"), "profile.workflow_name"),
                workflow_version=_required(raw.get("workflow_version"), "profile.workflow_version"),
                implementation_fingerprint=_required(
                    raw.get("implementation_fingerprint"),
                    "profile.implementation_fingerprint",
                ),
                checkpoint_namespace=_required(
                    raw.get("checkpoint_namespace"), "profile.checkpoint_namespace"
                ),
                manifest_hash=_required(raw.get("manifest_hash"), "profile.manifest_hash"),
                state_schema_version=int(raw.get("state_schema_version", 0)),
                start_input_schema=_start_input_schema_from_json(raw.get("start_input_schema")),
                terminal_projection_descriptor=_optional_json_object(
                    raw.get("terminal_projection_descriptor"),
                    "profile.terminal_projection_descriptor",
                ),
                terminal_request_factory_hash=(
                    None
                    if raw.get("terminal_request_factory_hash") is None
                    else _required(
                        raw.get("terminal_request_factory_hash"),
                        "profile.terminal_request_factory_hash",
                    )
                ),
                capability_snapshot=_json_object(
                    raw.get("capability_snapshot"), "profile.capability_snapshot"
                ),
            )
        )
    return WorkflowCatalogAuthority(
        authority_id=str(row["authority_id"]),
        generation=int(row["generation"]),
        version=int(row["version"]),
        catalog_hash=str(row["catalog_hash"]),
        profiles=tuple(profiles),
    )


def _workflow_launch_request_json(
    request: WorkflowLaunchRequest,
) -> dict[str, JsonValue]:
    return {
        "request_key": request.request_key,
        "candidate_id": request.candidate_id,
        "profile_key": request.profile_key,
        "catalog_generation": request.catalog_generation,
        "session_id": request.session_id,
        "request_id": request.request_id,
        "turn_id": request.turn_id,
        "requested_run_id": request.requested_run_id,
        "requested_trace_id": request.requested_trace_id,
        "requested_thread_id": request.requested_thread_id,
        "tool_catalog_generation": request.tool_catalog_generation,
        "objective": request.objective,
        "start_input": _thaw(request.start_input),
        "spawn_origin": request.spawn_origin.to_json(),
        "root_run_id": request.root_run_id,
        "attachment_policy": request.attachment_policy.value,
        "child_command_id": request.child_command_id,
    }


class WorkflowSpawnIssueMixin(ExecutionUnitOfWorkCore):
    """Workflow spawn catalog publication, launch-ticket issue and continuation claims."""

    __slots__ = ()

    if TYPE_CHECKING:

        @staticmethod
        def _require_matching_spawn_ready(
            connection: sqlite3.Connection,
            continuation: sqlite3.Row,
            ready: WorkflowSpawnContinuationReady,
        ) -> None: ...
        @staticmethod
        def _require_runtime_and_fence(
            connection: sqlite3.Connection,
            *,
            run_id: str,
            execution_lease: ExecutionLease,
            run_fence: RunFenceLease,
            now: float,
            require_exact_expiry: bool = True,
        ) -> None: ...
        @staticmethod
        def _require_workflow_handoff_authority(
            connection: sqlite3.Connection,
            *,
            run_id: str,
            execution_lease: ExecutionLease,
            workflow_lease: WorkflowLease | None,
            now: float,
        ) -> None: ...

    async def publish_catalog(
        self,
        transaction: WorkflowTransaction,
        authority: VerifiedWorkflowCatalogAuthority,
        expected_version: int,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> WorkflowCatalogAuthority:
        tx = self._assert_open_workflow_transaction(transaction)

        if (
            type(authority) is not VerifiedWorkflowCatalogAuthority
            or not authority._is_sdk_verified()
        ):
            raise UnitOfWorkConflict("workflow catalog authority was not SDK verified")
        durable_authority = authority.authority
        expected_snapshot_hash = hashlib.sha256(
            canonical_json(
                {
                    "generation": durable_authority.generation,
                    "profiles": [
                        _workflow_catalog_profile_json(item) for item in durable_authority.profiles
                    ],
                }
            ).encode()
        ).hexdigest()
        expected_snapshot_id = hashlib.sha256(
            ("simple-harness.workflow.registry-snapshot.v1|" + expected_snapshot_hash).encode()
        ).hexdigest()
        if (
            authority.registry_snapshot_hash != expected_snapshot_hash
            or authority.registry_snapshot_id != expected_snapshot_id
        ):
            raise UnitOfWorkConflict("workflow registry snapshot differs from catalog")
        if expected_version < 0:
            raise ValueError("expected catalog version must be non-negative")
        if durable_authority.version != expected_version + 1:
            raise UnitOfWorkConflict("catalog version is not the next CAS version")
        existing = tx.connection.execute(
            "SELECT * FROM workflow_catalog_authorities WHERE authority_id=?",
            (durable_authority.authority_id,),
        ).fetchone()
        if existing is not None:
            current = _workflow_catalog_authority(existing)
            if current == durable_authority:
                return current
            if current.version != expected_version:
                raise UnitOfWorkConflict("workflow catalog version changed")
            _fault(fault, "workflow:catalog:before_authority_write")
            changed = tx.connection.execute(
                "UPDATE workflow_catalog_authorities SET"
                " generation=?,version=?,catalog_hash=?,canonical_profiles=?,updated_at=? WHERE"
                " authority_id=? AND version=?",
                (
                    durable_authority.generation,
                    durable_authority.version,
                    durable_authority.catalog_hash,
                    _workflow_catalog_profiles_json(durable_authority),
                    now,
                    durable_authority.authority_id,
                    expected_version,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("workflow catalog CAS failed")
        else:
            if expected_version != 0:
                raise UnitOfWorkConflict("initial workflow catalog version must be one")
            _fault(fault, "workflow:catalog:before_authority_write")
            tx.connection.execute(
                "INSERT INTO"
                " workflow_catalog_authorities(authority_id,generation,version,catalog_hash,canonical_profiles,updated_at)"  # noqa: E501
                " VALUES(?,?,?,?,?,?)",
                (
                    durable_authority.authority_id,
                    durable_authority.generation,
                    durable_authority.version,
                    durable_authority.catalog_hash,
                    _workflow_catalog_profiles_json(durable_authority),
                    now,
                ),
            )
        _fault(fault, "workflow:catalog:after_authority_write")
        tx.register_after_commit_fault("workflow:catalog:after_commit")
        return durable_authority

    async def read_catalog(self, transaction: WorkflowTransaction) -> WorkflowCatalogAuthority:
        tx = self._assert_open_workflow_transaction(transaction)
        row = tx.connection.execute(
            "SELECT * FROM workflow_catalog_authorities WHERE authority_id='model_spawnable'"
        ).fetchone()
        if row is None:
            raise UnitOfWorkNotFound("model_spawnable workflow catalog")
        return _workflow_catalog_authority(row)

    @classmethod
    def _require_workflow_spawn_issue_authority(
        cls,
        connection: sqlite3.Connection,
        *,
        request: WorkflowLaunchRequest,
        authority: WorkflowSpawnIssueAuthority,
        now: float,
        require_effect_fence: bool = True,
    ) -> str:

        if not isinstance(authority, WorkflowSpawnIssueAuthority):
            raise TypeError("issue_authority must be a WorkflowSpawnIssueAuthority")
        parent_run_id = request.spawn_origin.parent_run_id
        if authority.execution_lease.run_id != parent_run_id:
            raise UnitOfWorkConflict("workflow spawn authority belongs to another Run")
        cls._require_runtime_and_fence(
            connection,
            run_id=parent_run_id,
            execution_lease=authority.execution_lease,
            run_fence=authority.run_fence,
            now=now,
            require_exact_expiry=False,
        )
        cls._require_workflow_handoff_authority(
            connection,
            run_id=parent_run_id,
            execution_lease=authority.execution_lease,
            workflow_lease=authority.workflow_lease,
            now=now,
        )
        run = connection.execute("SELECT * FROM runs WHERE run_id=?", (parent_run_id,)).fetchone()
        if (
            run is None
            or str(run["state"]) != RunState.RUNNING.value
            or str(run["request_id"]) != request.spawn_origin.parent_request_id
            or str(run["root_run_id"]) != request.root_run_id
            or str(run["execution_session_id"]) != request.session_id
        ):
            raise UnitOfWorkConflict("workflow spawn parent Run authority differs")
        snapshot = connection.execute(
            "SELECT snapshot_json,snapshot_hash FROM run_start_snapshots WHERE run_id=?",
            (parent_run_id,),
        ).fetchone()
        if snapshot is None:
            raise UnitOfWorkConflict("workflow spawn parent start snapshot is missing")
        snapshot_json = str(snapshot["snapshot_json"])
        try:
            snapshot_value = json.loads(snapshot_json)
        except (TypeError, ValueError) as exc:
            raise UnitOfWorkConflict("workflow spawn parent start snapshot is malformed") from exc
        if (
            not isinstance(snapshot_value, dict)
            or canonical_json(snapshot_value) != snapshot_json
            or hashlib.sha256(snapshot_json.encode()).hexdigest() != str(snapshot["snapshot_hash"])
            or snapshot_value.get("turn_id") != request.turn_id
            or snapshot_value.get("tool_catalog_generation") != request.tool_catalog_generation
        ):
            raise UnitOfWorkConflict("workflow spawn parent start snapshot differs")
        checkpoint = connection.execute(
            """
            SELECT version,checkpoint_json,checkpoint_hash
            FROM workflow_checkpoints
            WHERE run_id=? AND namespace='react.termination.v1'
            ORDER BY version DESC LIMIT 1
            """,
            (parent_run_id,),
        ).fetchone()
        if checkpoint is None:
            raise UnitOfWorkConflict("workflow spawn ReAct checkpoint is missing")
        checkpoint_json = str(checkpoint["checkpoint_json"])
        try:
            checkpoint_value = json.loads(checkpoint_json)
        except (TypeError, ValueError) as exc:
            raise UnitOfWorkConflict("workflow spawn ReAct checkpoint is malformed") from exc
        if (
            int(checkpoint["version"]) != authority.react_checkpoint_revision
            or not isinstance(checkpoint_value, dict)
            or canonical_json(checkpoint_value) != checkpoint_json
            or hashlib.sha256(checkpoint_json.encode()).hexdigest()
            != str(checkpoint["checkpoint_hash"])
            or checkpoint_value.get("phase") != "tool_batch_reserved"
        ):
            raise UnitOfWorkConflict("workflow spawn ReAct checkpoint authority differs")
        effect = connection.execute(
            "SELECT * FROM execution_effects WHERE effect_id=?",
            (authority.effect_id,),
        ).fetchone()
        if (
            effect is None
            or str(effect["run_id"]) != parent_run_id
            or str(effect["state"]) != EffectState.HANDED_OFF.value
            or str(effect["tool_name"]) != "workflow_spawn"
            or str(effect["raw_call_id"]) != request.spawn_origin.internal_tool_call_id
            or str(effect["request_hash"]) != authority.effect_request_hash
            or int(effect["handoff_attempt"]) != authority.effect_handoff_attempt
            or (require_effect_fence and int(effect["fence_epoch"]) != authority.run_fence.epoch)
        ):
            raise UnitOfWorkConflict("workflow spawn Effect authority differs")
        authority_payload: dict[str, JsonValue] = {
            "react_checkpoint_revision": authority.react_checkpoint_revision,
            "execution_lease": {
                "run_id": authority.execution_lease.run_id,
                "namespace": authority.execution_lease.namespace,
                "owner_id": authority.execution_lease.owner_id,
                "epoch": authority.execution_lease.epoch,
            },
            "run_fence": {
                "run_id": authority.run_fence.run_id.value,
                "owner_id": authority.run_fence.owner_id,
                "runtime_lease_epoch": authority.run_fence.runtime_lease_epoch,
                "epoch": authority.run_fence.epoch,
            },
            "workflow_lease": (
                None
                if authority.workflow_lease is None
                else {
                    "run_id": authority.workflow_lease.run_id,
                    "namespace": authority.workflow_lease.namespace,
                    "owner_id": authority.workflow_lease.owner_id,
                    "epoch": authority.workflow_lease.epoch,
                    "runtime_lease_epoch": authority.workflow_lease.runtime_lease_epoch,
                }
            ),
            "effect_id": authority.effect_id,
            "effect_handoff_attempt": authority.effect_handoff_attempt,
            "effect_request_hash": authority.effect_request_hash,
        }
        return hashlib.sha256(canonical_json(authority_payload).encode("utf-8")).hexdigest()

    async def issue(
        self,
        transaction: WorkflowTransaction,
        request: WorkflowLaunchRequest,
        issue_authority: WorkflowSpawnIssueAuthority,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> WorkflowLaunchTicket:
        tx = self._assert_open_workflow_transaction(transaction)
        request_payload = _workflow_launch_request_json(request)
        canonical_request = canonical_json(request_payload)
        ticket_receipt_id = self._derived_id("workflow-launch/receipt/v1", request.request_key)
        existing = tx.connection.execute(
            "SELECT * FROM workflow_launch_ticket_receipts WHERE request_key=?",
            (request.request_key,),
        ).fetchone()
        if existing is not None:
            stored_payload = json.loads(str(existing["canonical_payload"]))
            stored_request = (
                stored_payload.get("request") if isinstance(stored_payload, dict) else None
            )
            canonical_payload = canonical_json(stored_payload)
            payload_hash = hashlib.sha256(canonical_payload.encode()).hexdigest()
            if (
                str(existing["ticket_receipt_id"]) != ticket_receipt_id
                or canonical_json(stored_request) != canonical_request
                or str(existing["canonical_payload"]) != canonical_payload
                or str(existing["payload_hash"]) != payload_hash
                or str(existing["ticket_id"])
                != self._derived_id("workflow-launch/ticket/v1", payload_hash)
            ):
                raise UnitOfWorkConflict(
                    "workflow launch request key reused with different payload"
                )
            return _workflow_launch_ticket(existing)

        issue_authority_hash = self._require_workflow_spawn_issue_authority(
            tx.connection,
            request=request,
            authority=issue_authority,
            now=now,
        )

        catalog = await self.read_catalog(transaction)
        if request.catalog_generation != catalog.generation:
            raise UnitOfWorkConflict("workflow launch catalog generation is stale")
        profile = catalog.require(request.profile_key)
        try:
            validate_arguments(
                _thaw(request.start_input),
                profile.start_input_schema.canonical_schema,
            )
        except (ArgumentsValidationError, SchemaDefinitionError) as exc:
            raise UnitOfWorkConflict("workflow start input violates durable schema") from exc
        request_fingerprint = hashlib.sha256(canonical_request.encode()).hexdigest()
        resolved_run_id = request.requested_run_id or self._derived_id(
            "workflow-launch/run/v1", request_fingerprint
        )
        resolved_trace_id = request.requested_trace_id or self._derived_id(
            "workflow-launch/trace/v1", request_fingerprint
        )
        resolved_thread_id = request.requested_thread_id or self._derived_id(
            "workflow-launch/thread/v1", request_fingerprint
        )
        objective_hash = hashlib.sha256(request.objective.encode()).hexdigest()
        start_input_hash = hashlib.sha256(
            canonical_json(_thaw(request.start_input)).encode()
        ).hexdigest()
        durable_payload: dict[str, JsonValue] = {
            "request": request_payload,
            "catalog_authority_version": catalog.version,
            "catalog_hash": catalog.catalog_hash,
            "profile_binding": _workflow_catalog_profile_json(profile),
            "profile_fingerprint": profile.profile_fingerprint,
            "workflow_name": profile.workflow_name,
            "workflow_version": profile.workflow_version,
            "implementation_fingerprint": profile.implementation_fingerprint,
            "resolved_run_id": resolved_run_id,
            "resolved_trace_id": resolved_trace_id,
            "resolved_thread_id": resolved_thread_id,
            "objective_hash": objective_hash,
            "start_input_hash": start_input_hash,
            "issue_authority_hash": issue_authority_hash,
        }
        canonical_payload = canonical_json(durable_payload)
        payload_hash = hashlib.sha256(canonical_payload.encode()).hexdigest()
        ticket_id = self._derived_id("workflow-launch/ticket/v1", payload_hash)
        _fault(fault, "workflow:launch_ticket:before_receipt_write")
        tx.connection.execute(
            """
            INSERT INTO workflow_launch_ticket_receipts(
                ticket_receipt_id,request_key,ticket_id,canonical_payload,payload_hash,
                candidate_id,profile_key,catalog_generation,catalog_authority_version,
                catalog_hash,profile_fingerprint,workflow_name,workflow_version,
                implementation_fingerprint,session_id,request_id,turn_id,
                requested_run_id,requested_trace_id,requested_thread_id,
                resolved_run_id,resolved_trace_id,resolved_thread_id,
                tool_catalog_generation,objective,objective_hash,start_input_hash,
                spawn_origin_json,parent_run_id,root_run_id,attachment_policy,
                child_command_id,issue_authority_hash,issued_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ticket_receipt_id,
                request.request_key,
                ticket_id,
                canonical_payload,
                payload_hash,
                request.candidate_id,
                request.profile_key,
                request.catalog_generation,
                catalog.version,
                catalog.catalog_hash,
                profile.profile_fingerprint,
                profile.workflow_name,
                profile.workflow_version,
                profile.implementation_fingerprint,
                request.session_id,
                request.request_id,
                request.turn_id,
                request.requested_run_id,
                request.requested_trace_id,
                request.requested_thread_id,
                resolved_run_id,
                resolved_trace_id,
                resolved_thread_id,
                request.tool_catalog_generation,
                request.objective,
                objective_hash,
                start_input_hash,
                canonical_json(request.spawn_origin.to_json()),
                request.spawn_origin.parent_run_id,
                request.root_run_id,
                request.attachment_policy.value,
                request.child_command_id,
                issue_authority_hash,
                now,
            ),
        )
        _fault(fault, "workflow:launch_ticket:after_receipt_write")
        tx.connection.execute(
            """
            INSERT INTO workflow_spawn_continuations(
                operation_id,ticket_receipt_id,parent_run_id,state,
                owner_id,runtime_lease_epoch,run_fence_epoch,workflow_lease_epoch,
                claim_epoch,expires_at,version,completion_receipt_id,
                completion_path_kind,effect_id,handoff_attempt,
                effect_request_hash,issue_authority_hash,created_at,updated_at
            ) VALUES(?,?,?,'pending',NULL,NULL,NULL,NULL,0,NULL,0,NULL,NULL,?,?,?,?,?,?)
            """,
            (
                request.request_key,
                ticket_receipt_id,
                request.spawn_origin.parent_run_id,
                issue_authority.effect_id,
                issue_authority.effect_handoff_attempt,
                issue_authority.effect_request_hash,
                issue_authority_hash,
                now,
                now,
            ),
        )
        _fault(fault, "workflow:launch_ticket:after_continuation_write")
        tx.register_after_commit_fault("workflow:launch_ticket:after_commit")
        row = tx.connection.execute(
            "SELECT * FROM workflow_launch_ticket_receipts WHERE ticket_receipt_id=?",
            (ticket_receipt_id,),
        ).fetchone()
        assert row is not None
        return _workflow_launch_ticket(row)

    async def read_issued(
        self, transaction: WorkflowTransaction, request_key: str
    ) -> tuple[WorkflowLaunchTicket, WorkflowLaunchRequest] | None:
        tx = self._assert_open_workflow_transaction(transaction)
        request_key = _required(request_key, "request_key")
        row = tx.connection.execute(
            "SELECT * FROM workflow_launch_ticket_receipts WHERE request_key=?",
            (request_key,),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["canonical_payload"]))
        if not isinstance(payload, dict):
            raise UnitOfWorkConflict("stored workflow launch ticket is malformed")
        canonical_payload = canonical_json(payload)
        payload_hash = hashlib.sha256(canonical_payload.encode()).hexdigest()
        if (
            str(row["canonical_payload"]) != canonical_payload
            or str(row["payload_hash"]) != payload_hash
            or str(row["ticket_id"]) != self._derived_id("workflow-launch/ticket/v1", payload_hash)
        ):
            raise UnitOfWorkConflict("stored workflow launch ticket self-hash differs")
        request = _workflow_launch_request_from_json(payload.get("request"))
        if request.request_key != request_key:
            raise UnitOfWorkConflict("stored workflow launch request key differs")
        return _workflow_launch_ticket(row), request

    async def claim_spawn_continuation(
        self,
        transaction: WorkflowTransaction,
        ticket: WorkflowLaunchTicket,
        issue_authority: WorkflowSpawnIssueAuthority,
        ready: WorkflowSpawnContinuationReady | None,
        *,
        now: float,
        ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> WorkflowSpawnContinuationClaim:
        tx = self._assert_open_workflow_transaction(transaction)
        now = _time(now)
        ttl_seconds = _positive_ttl(ttl_seconds)
        ticket_row = tx.connection.execute(
            "SELECT * FROM workflow_launch_ticket_receipts WHERE ticket_receipt_id=?",
            (ticket.ticket_receipt_id,),
        ).fetchone()
        if ticket_row is None or _workflow_launch_ticket(ticket_row) != ticket:
            raise UnitOfWorkConflict("workflow spawn ticket is forged or stale")
        payload = json.loads(str(ticket_row["canonical_payload"]))
        if not isinstance(payload, dict):
            raise UnitOfWorkConflict("workflow spawn ticket payload is malformed")
        request = _workflow_launch_request_from_json(payload.get("request"))
        row = tx.connection.execute(
            "SELECT * FROM workflow_spawn_continuations WHERE operation_id=?",
            (request.request_key,),
        ).fetchone()
        if (
            row is None
            or str(row["ticket_receipt_id"]) != ticket.ticket_receipt_id
            or str(row["effect_id"]) != issue_authority.effect_id
            or int(row["handoff_attempt"]) != issue_authority.effect_handoff_attempt
            or str(row["effect_request_hash"]) != issue_authority.effect_request_hash
        ):
            raise UnitOfWorkConflict("workflow spawn continuation identity differs")
        state = str(row["state"])
        workflow_epoch = (
            None if issue_authority.workflow_lease is None else issue_authority.workflow_lease.epoch
        )
        if state == "claimed" and float(row["expires_at"]) > now:
            authority_hash = self._require_workflow_spawn_issue_authority(
                tx.connection,
                request=request,
                authority=issue_authority,
                now=now,
            )
            if str(row["issue_authority_hash"]) != authority_hash:
                raise UnitOfWorkConflict("workflow spawn continuation issue authority differs")
            current = _workflow_spawn_continuation_claim(row)
            if (
                current.owner_id != issue_authority.execution_lease.owner_id
                or current.runtime_lease_epoch != issue_authority.execution_lease.epoch
                or current.run_fence_epoch != issue_authority.run_fence.epoch
                or current.workflow_lease_epoch != workflow_epoch
            ):
                raise UnitOfWorkConflict("workflow spawn continuation has a live foreign owner")
            return current
        if state == "completed":
            raise UnitOfWorkConflict("workflow spawn continuation is already completed")
        if state == "claimed":
            if ready is None:
                raise UnitOfWorkConflict(
                    "expired workflow spawn continuation requires ready evidence"
                )
            self._require_matching_spawn_ready(tx.connection, row, ready)
            self._require_workflow_spawn_issue_authority(
                tx.connection,
                request=request,
                authority=issue_authority,
                now=now,
                require_effect_fence=False,
            )
        elif state == "pending" and ready is not None:
            raise UnitOfWorkConflict(
                "initial workflow spawn continuation rejects recovery evidence"
            )
        elif state != "pending":
            raise UnitOfWorkConflict("workflow spawn continuation state is malformed")
        else:
            authority_hash = self._require_workflow_spawn_issue_authority(
                tx.connection,
                request=request,
                authority=issue_authority,
                now=now,
            )
            if str(row["issue_authority_hash"]) != authority_hash:
                raise UnitOfWorkConflict("workflow spawn continuation issue authority differs")
        next_epoch = int(row["claim_epoch"]) + 1
        expires_at = now + ttl_seconds
        _fault(fault, "workflow:spawn_continuation:before_claim_write")
        changed = tx.connection.execute(
            """
            UPDATE workflow_spawn_continuations
            SET state='claimed',owner_id=?,runtime_lease_epoch=?,run_fence_epoch=?,
                workflow_lease_epoch=?,claim_epoch=?,expires_at=?,version=version+1,
                updated_at=?
            WHERE operation_id=? AND version=? AND state=?
            """,
            (
                issue_authority.execution_lease.owner_id,
                issue_authority.execution_lease.epoch,
                issue_authority.run_fence.epoch,
                workflow_epoch,
                next_epoch,
                expires_at,
                now,
                request.request_key,
                int(row["version"]),
                state,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn continuation claim CAS failed")
        _fault(fault, "workflow:spawn_continuation:after_claim_write")
        tx.register_after_commit_fault("workflow:spawn_continuation:after_commit")
        claimed = tx.connection.execute(
            "SELECT * FROM workflow_spawn_continuations WHERE operation_id=?",
            (request.request_key,),
        ).fetchone()
        assert claimed is not None
        return _workflow_spawn_continuation_claim(claimed)

    async def mark_spawn_continuation_ready(
        self,
        transaction: WorkflowTransaction,
        ticket: WorkflowLaunchTicket,
        effect_snapshot: EffectRecord,
        evidence_ref: str,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> WorkflowSpawnContinuationReady:
        tx = self._assert_open_workflow_transaction(transaction)
        evidence_ref = _required(evidence_ref, "evidence_ref")
        now = _time(now)
        ticket_row = tx.connection.execute(
            "SELECT * FROM workflow_launch_ticket_receipts WHERE ticket_receipt_id=?",
            (ticket.ticket_receipt_id,),
        ).fetchone()
        if ticket_row is None or _workflow_launch_ticket(ticket_row) != ticket:
            raise UnitOfWorkConflict("workflow spawn ticket is forged or stale")
        continuation = tx.connection.execute(
            "SELECT * FROM workflow_spawn_continuations WHERE ticket_receipt_id=?",
            (ticket.ticket_receipt_id,),
        ).fetchone()
        if continuation is None or str(continuation["state"]) == "completed":
            raise UnitOfWorkConflict("workflow spawn continuation is unavailable")
        durable_effect = tx.connection.execute(
            "SELECT * FROM execution_effects WHERE effect_id=?",
            (effect_snapshot.effect_id.value,),
        ).fetchone()
        if (
            durable_effect is None
            or _effect_record(durable_effect) != effect_snapshot
            or str(continuation["effect_id"]) != effect_snapshot.effect_id.value
            or int(continuation["handoff_attempt"]) != effect_snapshot.handoff_attempt
            or str(continuation["effect_request_hash"]) != effect_snapshot.request_hash
            or effect_snapshot.state not in {EffectState.HANDED_OFF, EffectState.UNKNOWN}
        ):
            raise UnitOfWorkConflict("workflow spawn ready Effect evidence differs")
        ready_receipt_id = self._derived_id(
            "workflow-spawn/ready/v1", str(continuation["operation_id"])
        )
        existing = tx.connection.execute(
            "SELECT * FROM workflow_spawn_continuation_ready WHERE operation_id=?",
            (str(continuation["operation_id"]),),
        ).fetchone()
        if existing is not None:
            ready = _workflow_spawn_continuation_ready(existing)
            if (
                ready.ready_receipt_id != ready_receipt_id
                or ready.ticket_receipt_id != ticket.ticket_receipt_id
                or ready.effect_id != effect_snapshot.effect_id.value
                or ready.handoff_attempt != effect_snapshot.handoff_attempt
                or ready.evidence_ref != evidence_ref
            ):
                raise UnitOfWorkConflict(
                    "workflow spawn ready identity reused with different evidence"
                )
            return ready
        _fault(fault, "workflow:spawn_ready:before_receipt_write")
        tx.connection.execute(
            """
            INSERT INTO workflow_spawn_continuation_ready(
                ready_receipt_id,operation_id,ticket_receipt_id,effect_id,
                handoff_attempt,evidence_ref,version,created_at,consumed_at
            ) VALUES(?,?,?,?,?,?,0,?,NULL)
            """,
            (
                ready_receipt_id,
                str(continuation["operation_id"]),
                ticket.ticket_receipt_id,
                effect_snapshot.effect_id.value,
                effect_snapshot.handoff_attempt,
                evidence_ref,
                now,
            ),
        )
        _fault(fault, "workflow:spawn_ready:after_receipt_write")
        tx.register_after_commit_fault("workflow:spawn_ready:after_commit")
        created = tx.connection.execute(
            "SELECT * FROM workflow_spawn_continuation_ready WHERE ready_receipt_id=?",
            (ready_receipt_id,),
        ).fetchone()
        assert created is not None
        return _workflow_spawn_continuation_ready(created)

    def list_ready_spawn_continuations(
        self, snapshot_cursor: str | None, *, limit: int
    ) -> tuple[tuple[WorkflowSpawnContinuationReady, ...], str | None]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("workflow spawn ready limit must be between 1 and 100")
        if snapshot_cursor is not None:
            snapshot_cursor = _required(snapshot_cursor, "snapshot_cursor")
        rows = self.database.connection.execute(
            """
            SELECT ready.* FROM workflow_spawn_continuation_ready AS ready
            JOIN workflow_spawn_continuations AS continuation
              ON continuation.operation_id=ready.operation_id
            WHERE ready.consumed_at IS NULL AND continuation.state<>'completed'
              AND (? IS NULL OR ready.ready_receipt_id>?)
            ORDER BY ready.ready_receipt_id LIMIT ?
            """,
            (snapshot_cursor, snapshot_cursor, limit + 1),
        ).fetchall()
        page = rows[:limit]
        next_cursor = None if len(rows) <= limit else str(page[-1]["ready_receipt_id"])
        return tuple(_workflow_spawn_continuation_ready(row) for row in page), next_cursor

    def read_spawn_ready_blocker(
        self, ready: WorkflowSpawnContinuationReady
    ) -> WaitBlockerRecord | None:

        if not isinstance(ready, WorkflowSpawnContinuationReady):
            raise TypeError("ready must be a WorkflowSpawnContinuationReady")
        durable = self.database.connection.execute(
            "SELECT * FROM workflow_spawn_continuation_ready WHERE ready_receipt_id=?",
            (ready.ready_receipt_id,),
        ).fetchone()
        if durable is None or _workflow_spawn_continuation_ready(durable) != ready:
            raise UnitOfWorkConflict("workflow spawn ready receipt differs")
        rows = self.database.connection.execute(
            """
            SELECT * FROM run_wait_blockers
            WHERE kind='tool' AND ledger_identity=? AND handoff_attempt=?
              AND wake_consumed=0 AND superseded_by IS NULL
            ORDER BY blocker_id LIMIT 2
            """,
            (ready.effect_id, ready.handoff_attempt),
        ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise UnitOfWorkConflict("workflow spawn ready maps to multiple wait blockers")
        blocker = _wait_blocker_record(rows[0])
        if (
            blocker.kind is not RecoveryKind.TOOL
            or blocker.ledger_identity != ready.effect_id
            or blocker.handoff_attempt != ready.handoff_attempt
        ):
            raise UnitOfWorkConflict("workflow spawn ready blocker differs")
        return blocker

    async def verify(
        self,
        transaction: WorkflowTransaction,
        ticket: WorkflowLaunchTicket,
    ) -> VerifiedWorkflowLaunchTicket:
        tx = self._assert_open_workflow_transaction(transaction)
        row = tx.connection.execute(
            "SELECT * FROM workflow_launch_ticket_receipts WHERE ticket_receipt_id=?",
            (ticket.ticket_receipt_id,),
        ).fetchone()
        if row is None or _workflow_launch_ticket(row) != ticket:
            raise UnitOfWorkConflict("workflow launch ticket is forged or stale")
        catalog = await self.read_catalog(transaction)
        if (
            catalog.generation != int(row["catalog_generation"])
            or catalog.version != int(row["catalog_authority_version"])
            or catalog.catalog_hash != str(row["catalog_hash"])
        ):
            raise UnitOfWorkConflict("workflow launch catalog authority changed")
        profile = catalog.require(str(row["profile_key"]))
        payload = json.loads(str(row["canonical_payload"]))
        stored_binding = payload.get("profile_binding") if isinstance(payload, dict) else None
        if (
            canonical_json(stored_binding)
            != canonical_json(_workflow_catalog_profile_json(profile))
            or profile.profile_fingerprint != str(row["profile_fingerprint"])
            or profile.workflow_name != str(row["workflow_name"])
            or profile.workflow_version != str(row["workflow_version"])
            or profile.implementation_fingerprint != str(row["implementation_fingerprint"])
        ):
            raise UnitOfWorkConflict("workflow launch profile binding changed")
        return _verified_workflow_launch_ticket(row)

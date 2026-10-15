# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Module-level helpers shared by several execution unit-of-work domain mixins."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from simple_harness.contracts import (
    CallId,
    EffectId,
    JsonValue,
    RunId,
    canonical_json,
    freeze_json,
    thaw_json,
)
from simple_harness.execution.contracts.children import (
    AttachmentPolicy,
    ChildSignalAckReceipt,
    ChildSignalRecord,
    ChildSignalState,
    ChildTerminalReceipt,
)
from simple_harness.execution.delivery import DeliveryRecord, DeliveryState
from simple_harness.execution.effects import (
    EffectRecord,
    EffectState,
    TaskExecutionEnvelope,
)
from simple_harness.execution.fences import RunFenceLease
from simple_harness.execution.recovery import (
    ReconciliationResolution,
    RecoveryKind,
    ResolutionOutcome,
    WaitBlockerRecord,
)
from simple_harness.execution.uow import (
    RUNTIME_LEASE_NAMESPACE,
    ContinuationProgressReceipt,
    ExecutionLease,
    FaultHook,
    RunRecord,
    RunState,
    UnitOfWorkConflict,
    WorkflowCheckpoint,
)
from simple_harness.runtime.orchestration import (
    StartInputSchema,
    WorkflowLaunchRequest,
    WorkflowLaunchTicket,
    WorkflowSpawnContinuationClaim,
    WorkflowSpawnContinuationReady,
    WorkflowSpawnOrigin,
    WorkflowSpawnReadyActivation,
    WorkflowSpawnReadyActivationState,
    _create_verified_workflow_launch_ticket,
    _create_workflow_spawn_ready_activation,
)
from simple_harness.tools.contracts import ToolOutcome, ToolResult
from simple_harness.workflow.lease import WorkflowLease

if TYPE_CHECKING:
    from simple_harness.runtime.orchestration import (
        VerifiedWorkflowLaunchTicket,
        WorkflowLaunchRequest,
        WorkflowLaunchTicket,
        WorkflowSpawnContinuationClaim,
        WorkflowSpawnContinuationReady,
        WorkflowSpawnReadyActivation,
    )


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _time(value: object, name: str = "now") -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValueError(f"{name} must be finite and non-negative")
    return float(value)


def _positive_ttl(value: object) -> float:
    ttl = _time(value, "ttl_seconds")
    if ttl <= 0:
        raise ValueError("ttl_seconds must be positive")
    return ttl


def _object_json(value: Mapping[str, JsonValue], name: str) -> str:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a JSON object")
    return canonical_json(value)


def _fault(hook: FaultHook | None, point: str) -> None:
    if hook is not None:
        hook(point)


def _start_input_schema_from_json(value: object):  # type: ignore[no-untyped-def]

    raw = _json_object(value, "start_input_schema")
    return StartInputSchema(
        schema_ref=_required(raw.get("schema_ref"), "start_input_schema.schema_ref"),
        canonical_schema=_json_object(
            raw.get("canonical_schema"), "start_input_schema.canonical_schema"
        ),
        schema_hash=_required(raw.get("schema_hash"), "start_input_schema.schema_hash"),
    )


def _json_object(value: object, name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise UnitOfWorkConflict(f"stored {name} is not a JSON object")
    return cast(dict[str, JsonValue], value)


def _optional_json_object(value: object, name: str) -> dict[str, JsonValue] | None:
    if value is None:
        return None
    return _json_object(value, name)


def _workflow_launch_request_from_json(value: object) -> WorkflowLaunchRequest:

    raw = _json_object(value, "workflow launch request")
    expected_fields = {
        "request_key",
        "candidate_id",
        "profile_key",
        "catalog_generation",
        "session_id",
        "request_id",
        "turn_id",
        "requested_run_id",
        "requested_trace_id",
        "requested_thread_id",
        "tool_catalog_generation",
        "objective",
        "start_input",
        "spawn_origin",
        "root_run_id",
        "attachment_policy",
        "child_command_id",
    }
    if set(raw) != expected_fields:
        raise UnitOfWorkConflict("stored workflow launch request fields differ")
    origin = _json_object(raw["spawn_origin"], "workflow launch spawn_origin")
    if set(origin) != {
        "parent_run_id",
        "parent_request_id",
        "turn_id",
        "internal_tool_call_id",
    }:
        raise UnitOfWorkConflict("stored workflow launch origin fields differ")
    candidate = raw["candidate_id"]
    if candidate is not None and not isinstance(candidate, str):
        raise UnitOfWorkConflict("stored workflow launch candidate_id is malformed")
    optional_identifiers: dict[str, str | None] = {}
    for field_name in (
        "requested_run_id",
        "requested_trace_id",
        "requested_thread_id",
    ):
        item = raw[field_name]
        if item is not None and not isinstance(item, str):
            raise UnitOfWorkConflict(f"stored workflow launch {field_name} is malformed")
        optional_identifiers[field_name] = item
    catalog_generation = raw["catalog_generation"]
    tool_catalog_generation = raw["tool_catalog_generation"]
    if (
        isinstance(catalog_generation, bool)
        or not isinstance(catalog_generation, int)
        or isinstance(tool_catalog_generation, bool)
        or not isinstance(tool_catalog_generation, int)
    ):
        raise UnitOfWorkConflict("stored workflow launch generations are malformed")
    try:
        return WorkflowLaunchRequest(
            request_key=_required(raw["request_key"], "request_key"),
            candidate_id=candidate,
            profile_key=_required(raw["profile_key"], "profile_key"),
            catalog_generation=catalog_generation,
            session_id=_required(raw["session_id"], "session_id"),
            request_id=_required(raw["request_id"], "request_id"),
            turn_id=_required(raw["turn_id"], "turn_id"),
            requested_run_id=optional_identifiers["requested_run_id"],
            requested_trace_id=optional_identifiers["requested_trace_id"],
            requested_thread_id=optional_identifiers["requested_thread_id"],
            tool_catalog_generation=tool_catalog_generation,
            objective=_required(raw["objective"], "objective"),
            start_input=_json_object(raw["start_input"], "workflow launch start_input"),
            spawn_origin=WorkflowSpawnOrigin(
                parent_run_id=_required(origin["parent_run_id"], "parent_run_id"),
                parent_request_id=_required(origin["parent_request_id"], "parent_request_id"),
                turn_id=_required(origin["turn_id"], "turn_id"),
                internal_tool_call_id=_required(
                    origin["internal_tool_call_id"], "internal_tool_call_id"
                ),
            ),
            root_run_id=_required(raw["root_run_id"], "root_run_id"),
            attachment_policy=AttachmentPolicy(
                _required(raw["attachment_policy"], "attachment_policy")
            ),
            child_command_id=_required(raw["child_command_id"], "child_command_id"),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise UnitOfWorkConflict("stored workflow launch request is malformed") from exc


def _workflow_launch_ticket(row: sqlite3.Row) -> WorkflowLaunchTicket:

    return WorkflowLaunchTicket(
        ticket_receipt_id=str(row["ticket_receipt_id"]),
        payload_hash=str(row["payload_hash"]),
        candidate_id=(None if row["candidate_id"] is None else str(row["candidate_id"])),
        profile_key=str(row["profile_key"]),
        catalog_generation=int(row["catalog_generation"]),
    )


def _workflow_spawn_continuation_claim(
    row: sqlite3.Row,
) -> WorkflowSpawnContinuationClaim:

    return WorkflowSpawnContinuationClaim(
        spawn_operation_id=str(row["operation_id"]),
        ticket_receipt_id=str(row["ticket_receipt_id"]),
        parent_run_id=str(row["parent_run_id"]),
        owner_id=str(row["owner_id"]),
        runtime_lease_epoch=int(row["runtime_lease_epoch"]),
        run_fence_epoch=int(row["run_fence_epoch"]),
        workflow_lease_epoch=(
            None if row["workflow_lease_epoch"] is None else int(row["workflow_lease_epoch"])
        ),
        claim_epoch=int(row["claim_epoch"]),
        expires_at=float(row["expires_at"]),
        version=int(row["version"]),
    )


def _workflow_spawn_continuation_ready(
    row: sqlite3.Row,
) -> WorkflowSpawnContinuationReady:

    return WorkflowSpawnContinuationReady(
        ready_receipt_id=str(row["ready_receipt_id"]),
        spawn_operation_id=str(row["operation_id"]),
        ticket_receipt_id=str(row["ticket_receipt_id"]),
        effect_id=str(row["effect_id"]),
        handoff_attempt=int(row["handoff_attempt"]),
        evidence_ref=str(row["evidence_ref"]),
        version=int(row["version"]),
        created_at=float(row["created_at"]),
    )


def _workflow_spawn_activation_hash(
    *,
    activation_receipt_id: str,
    ready_receipt_id: str,
    spawn_operation_id: str,
    parent_run_id: str,
    effect_id: str,
    owner_id: str,
    runtime_lease_epoch: int,
    run_fence_epoch: int,
    workflow_lease_epoch: int | None,
    continuation_claim_epoch: int,
    predecessor_activation_receipt_id: str | None,
    version: int,
) -> str:
    payload: dict[str, JsonValue] = {
        "activation_receipt_id": activation_receipt_id,
        "ready_receipt_id": ready_receipt_id,
        "spawn_operation_id": spawn_operation_id,
        "parent_run_id": parent_run_id,
        "effect_id": effect_id,
        "owner_id": owner_id,
        "runtime_lease_epoch": runtime_lease_epoch,
        "run_fence_epoch": run_fence_epoch,
        "workflow_lease_epoch": workflow_lease_epoch,
        "continuation_claim_epoch": continuation_claim_epoch,
        "predecessor_activation_receipt_id": predecessor_activation_receipt_id,
        "version": version,
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


_WORKFLOW_SPAWN_COMPLETION_FIELDS = (
    "completion_receipt_id",
    "spawn_operation_id",
    "ticket_receipt_id",
    "parent_run_id",
    "path_kind",
    "effect_id",
    "handoff_attempt",
    "effect_request_hash",
    "issue_authority_hash",
    "tool_result_json",
    "tool_result_hash",
    "child_runtime_start_receipt_id",
    "failure_evidence_kind",
    "failure_evidence_id",
    "failure_evidence_json",
    "failure_evidence_hash",
    "activation_chain_head_id",
    "child_wait_receipt_id",
    "created_at",
)


def _workflow_spawn_completion_hash(values: Mapping[str, object]) -> str:
    payload: dict[str, JsonValue] = {
        name: cast(JsonValue, values[name]) for name in _WORKFLOW_SPAWN_COMPLETION_FIELDS
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def _workflow_spawn_failure_evidence(row: sqlite3.Row) -> dict[str, JsonValue]:
    raw = row["failure_evidence_json"]
    if raw is None:
        raise UnitOfWorkConflict("workflow spawn failure evidence is missing")
    try:
        evidence = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise UnitOfWorkConflict("workflow spawn failure evidence is malformed") from exc
    if not isinstance(evidence, dict):
        raise UnitOfWorkConflict("workflow spawn failure evidence is malformed")
    canonical = canonical_json(evidence)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    if (
        str(raw) != canonical
        or row["failure_evidence_hash"] is None
        or str(row["failure_evidence_hash"]) != digest
        or row["failure_evidence_kind"] != evidence.get("kind")
        or row["failure_evidence_id"] is None
    ):
        raise UnitOfWorkConflict("workflow spawn failure evidence hash differs")
    return cast(dict[str, JsonValue], evidence)


def _workflow_spawn_ready_activation(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> WorkflowSpawnReadyActivation:

    canonical_hash = _workflow_spawn_activation_hash(
        activation_receipt_id=str(row["activation_receipt_id"]),
        ready_receipt_id=str(row["ready_receipt_id"]),
        spawn_operation_id=str(row["spawn_operation_id"]),
        parent_run_id=str(row["parent_run_id"]),
        effect_id=str(row["effect_id"]),
        owner_id=str(row["owner_id"]),
        runtime_lease_epoch=int(row["runtime_lease_epoch"]),
        run_fence_epoch=int(row["run_fence_epoch"]),
        workflow_lease_epoch=(
            None if row["workflow_lease_epoch"] is None else int(row["workflow_lease_epoch"])
        ),
        continuation_claim_epoch=int(row["continuation_claim_epoch"]),
        predecessor_activation_receipt_id=(
            None
            if row["predecessor_activation_receipt_id"] is None
            else str(row["predecessor_activation_receipt_id"])
        ),
        version=int(row["version"]),
    )
    if canonical_hash != str(row["canonical_hash"]):
        raise UnitOfWorkConflict("workflow spawn activation self-hash differs")
    ready_row = connection.execute(
        "SELECT * FROM workflow_spawn_continuation_ready WHERE ready_receipt_id=?",
        (str(row["ready_receipt_id"]),),
    ).fetchone()
    continuation_row = connection.execute(
        "SELECT * FROM workflow_spawn_continuations WHERE operation_id=?",
        (str(row["spawn_operation_id"]),),
    ).fetchone()
    runtime = connection.execute(
        "SELECT * FROM workflow_leases WHERE run_id=? AND namespace=?",
        (str(row["parent_run_id"]), RUNTIME_LEASE_NAMESPACE),
    ).fetchone()
    fence = connection.execute(
        "SELECT * FROM run_fences WHERE run_id=?", (str(row["parent_run_id"]),)
    ).fetchone()
    blocker = connection.execute(
        """
        SELECT blocker_id FROM run_wait_blockers
        WHERE run_id=? AND kind='tool' AND ledger_identity=?
        ORDER BY created_at DESC LIMIT 1
        """,
        (str(row["parent_run_id"]), str(row["effect_id"])),
    ).fetchone()
    if (
        ready_row is None
        or continuation_row is None
        or runtime is None
        or fence is None
        or blocker is None
        or str(runtime["owner_id"]) != str(row["owner_id"])
        or int(runtime["epoch"]) != int(row["runtime_lease_epoch"])
        or str(fence["owner_id"]) != str(row["owner_id"])
        or int(fence["runtime_lease_epoch"]) != int(row["runtime_lease_epoch"])
        or int(fence["epoch"]) != int(row["run_fence_epoch"])
        or str(fence["state"]) != "active"
        or int(continuation_row["claim_epoch"]) != int(row["continuation_claim_epoch"])
        or str(continuation_row["owner_id"]) != str(row["owner_id"])
    ):
        raise UnitOfWorkConflict("workflow spawn activation authority disappeared")
    execution_lease = ExecutionLease(
        str(row["parent_run_id"]),
        RUNTIME_LEASE_NAMESPACE,
        str(row["owner_id"]),
        int(row["runtime_lease_epoch"]),
        float(runtime["expires_at"]),
    )
    run_fence = RunFenceLease(
        RunId(str(row["parent_run_id"])),
        int(row["run_fence_epoch"]),
        str(row["owner_id"]),
        int(row["runtime_lease_epoch"]),
    )
    workflow_lease: WorkflowLease | None = None
    if row["workflow_lease_epoch"] is not None:
        projection = connection.execute(
            "SELECT * FROM workflow_leases WHERE run_id=? AND epoch=? AND owner_id=? AND"
            " namespace<>?",
            (
                str(row["parent_run_id"]),
                int(row["workflow_lease_epoch"]),
                str(row["owner_id"]),
                RUNTIME_LEASE_NAMESPACE,
            ),
        ).fetchone()
        if projection is None:
            raise UnitOfWorkConflict("workflow spawn projection lease disappeared")
        workflow_lease = WorkflowLease(
            run_id=str(row["parent_run_id"]),
            owner_id=str(row["owner_id"]),
            epoch=int(row["workflow_lease_epoch"]),
            expires_at=float(projection["expires_at"]),
            runtime_lease_epoch=int(row["runtime_lease_epoch"]),
            namespace=str(projection["namespace"]),
        )
    return _create_workflow_spawn_ready_activation(
        ready_receipt=_workflow_spawn_continuation_ready(ready_row),
        continuation_claim=_workflow_spawn_continuation_claim(continuation_row),
        execution_lease=execution_lease,
        run_fence=run_fence,
        workflow_lease=workflow_lease,
        blocker_id=str(blocker["blocker_id"]),
        activation_receipt_id=str(row["activation_receipt_id"]),
        activation_version=int(row["version"]),
        predecessor_activation_receipt_id=(
            None
            if row["predecessor_activation_receipt_id"] is None
            else str(row["predecessor_activation_receipt_id"])
        ),
        state=WorkflowSpawnReadyActivationState(str(row["state"])),
    )


def _verified_workflow_launch_ticket(
    row: sqlite3.Row,
) -> VerifiedWorkflowLaunchTicket:

    fields = (
        "ticket_receipt_id",
        "ticket_id",
        "candidate_id",
        "profile_key",
        "catalog_generation",
        "catalog_authority_version",
        "catalog_hash",
        "profile_fingerprint",
        "workflow_name",
        "workflow_version",
        "implementation_fingerprint",
        "session_id",
        "request_id",
        "turn_id",
        "requested_run_id",
        "requested_trace_id",
        "requested_thread_id",
        "resolved_run_id",
        "resolved_trace_id",
        "resolved_thread_id",
        "tool_catalog_generation",
        "objective_hash",
        "start_input_hash",
    )
    values: dict[str, object] = {}
    integer_fields = {
        "catalog_generation",
        "catalog_authority_version",
        "tool_catalog_generation",
    }
    nullable_fields = {
        "candidate_id",
        "requested_run_id",
        "requested_trace_id",
        "requested_thread_id",
    }
    for field_name in fields:
        value = row[field_name]
        if field_name in integer_fields:
            values[field_name] = int(value)
        elif field_name in nullable_fields and value is None:
            values[field_name] = None
        else:
            values[field_name] = str(value)
    payload = json.loads(str(row["canonical_payload"]))
    binding = payload.get("profile_binding") if isinstance(payload, dict) else None
    if not isinstance(binding, dict):
        raise UnitOfWorkConflict("workflow launch ticket profile binding is missing")
    schema = _start_input_schema_from_json(binding.get("start_input_schema"))
    binding_columns = (
        _required(binding.get("profile_key"), "ticket.profile_key"),
        _required(binding.get("profile_fingerprint"), "ticket.profile_fingerprint"),
        _required(binding.get("workflow_name"), "ticket.workflow_name"),
        _required(binding.get("workflow_version"), "ticket.workflow_version"),
        _required(
            binding.get("implementation_fingerprint"),
            "ticket.implementation_fingerprint",
        ),
    )
    durable_columns = (
        str(row["profile_key"]),
        str(row["profile_fingerprint"]),
        str(row["workflow_name"]),
        str(row["workflow_version"]),
        str(row["implementation_fingerprint"]),
    )
    payload_binding_columns = (
        _required(payload.get("profile_fingerprint"), "ticket.profile_fingerprint"),
        _required(payload.get("workflow_name"), "ticket.workflow_name"),
        _required(payload.get("workflow_version"), "ticket.workflow_version"),
        _required(
            payload.get("implementation_fingerprint"),
            "ticket.implementation_fingerprint",
        ),
    )
    if binding_columns != durable_columns or payload_binding_columns != durable_columns[1:]:
        raise UnitOfWorkConflict("workflow launch ticket binding columns differ")
    values.update(
        {
            "description": _required(binding.get("description"), "ticket.description"),
            "use_when": _required(binding.get("use_when"), "ticket.use_when"),
            "avoid_when": _required(binding.get("avoid_when"), "ticket.avoid_when"),
            "input_schema_ref": _required(
                binding.get("input_schema_ref"), "ticket.input_schema_ref"
            ),
            "checkpoint_namespace": _required(
                binding.get("checkpoint_namespace"), "ticket.checkpoint_namespace"
            ),
            "manifest_hash": _required(binding.get("manifest_hash"), "ticket.manifest_hash"),
            "state_schema_version": int(binding.get("state_schema_version", 0)),
            "start_input_schema": schema,
            "terminal_projection_descriptor": _optional_json_object(
                binding.get("terminal_projection_descriptor"),
                "ticket.terminal_projection_descriptor",
            ),
            "terminal_request_factory_hash": (
                None
                if binding.get("terminal_request_factory_hash") is None
                else _required(
                    binding.get("terminal_request_factory_hash"),
                    "ticket.terminal_request_factory_hash",
                )
            ),
            "capability_snapshot": _json_object(
                binding.get("capability_snapshot"), "ticket.capability_snapshot"
            ),
        }
    )
    request_payload = payload.get("request") if isinstance(payload, dict) else None
    if not isinstance(request_payload, dict):
        raise UnitOfWorkConflict("workflow launch ticket request is missing")
    launch_request = _workflow_launch_request_from_json(request_payload)
    request_key = _required(request_payload.get("request_key"), "ticket.request_key")
    profile_key = _required(request_payload.get("profile_key"), "ticket.profile_key")
    session_id = _required(request_payload.get("session_id"), "ticket.session_id")
    request_id = _required(request_payload.get("request_id"), "ticket.request_id")
    turn_id = _required(request_payload.get("turn_id"), "ticket.turn_id")
    objective = _required(request_payload.get("objective"), "ticket.objective")
    start_input = _json_object(request_payload.get("start_input"), "ticket.start_input")
    candidate_id = request_payload.get("candidate_id")
    requested_run_id = request_payload.get("requested_run_id")
    requested_trace_id = request_payload.get("requested_trace_id")
    requested_thread_id = request_payload.get("requested_thread_id")
    for field_name, field_value in (
        ("candidate_id", candidate_id),
        ("requested_run_id", requested_run_id),
        ("requested_trace_id", requested_trace_id),
        ("requested_thread_id", requested_thread_id),
    ):
        if field_value is not None and not isinstance(field_value, str):
            raise UnitOfWorkConflict(f"ticket {field_name} is not a string")
    catalog_generation = request_payload.get("catalog_generation")
    tool_catalog_generation = request_payload.get("tool_catalog_generation")
    if (
        not isinstance(catalog_generation, int)
        or isinstance(catalog_generation, bool)
        or not isinstance(tool_catalog_generation, int)
        or isinstance(tool_catalog_generation, bool)
    ):
        raise UnitOfWorkConflict("workflow launch ticket generations are invalid")
    canonical_request = canonical_json(request_payload)
    request_fingerprint = hashlib.sha256(canonical_request.encode()).hexdigest()

    def derived_id(domain: str) -> str:
        return hashlib.sha256(
            f"simple-harness.workflow.{domain}|{request_fingerprint}".encode()
        ).hexdigest()

    resolved_run_id = requested_run_id or derived_id("workflow-launch/run/v1")
    resolved_trace_id = requested_trace_id or derived_id("workflow-launch/trace/v1")
    resolved_thread_id = requested_thread_id or derived_id("workflow-launch/thread/v1")
    request_columns = (
        request_key,
        candidate_id,
        profile_key,
        catalog_generation,
        session_id,
        request_id,
        turn_id,
        requested_run_id,
        requested_trace_id,
        requested_thread_id,
        resolved_run_id,
        resolved_trace_id,
        resolved_thread_id,
        tool_catalog_generation,
        hashlib.sha256(objective.encode()).hexdigest(),
        hashlib.sha256(canonical_json(start_input).encode()).hexdigest(),
    )
    durable_request_columns = (
        str(row["request_key"]),
        None if row["candidate_id"] is None else str(row["candidate_id"]),
        str(row["profile_key"]),
        int(row["catalog_generation"]),
        str(row["session_id"]),
        str(row["request_id"]),
        str(row["turn_id"]),
        None if row["requested_run_id"] is None else str(row["requested_run_id"]),
        None if row["requested_trace_id"] is None else str(row["requested_trace_id"]),
        None if row["requested_thread_id"] is None else str(row["requested_thread_id"]),
        str(row["resolved_run_id"]),
        str(row["resolved_trace_id"]),
        str(row["resolved_thread_id"]),
        int(row["tool_catalog_generation"]),
        str(row["objective_hash"]),
        str(row["start_input_hash"]),
    )
    payload_catalog_columns = (
        payload.get("catalog_authority_version"),
        payload.get("catalog_hash"),
    )
    durable_catalog_columns = (
        int(row["catalog_authority_version"]),
        str(row["catalog_hash"]),
    )
    payload_derived_columns = (
        payload.get("resolved_run_id"),
        payload.get("resolved_trace_id"),
        payload.get("resolved_thread_id"),
        payload.get("objective_hash"),
        payload.get("start_input_hash"),
    )
    durable_derived_columns = (
        resolved_run_id,
        resolved_trace_id,
        resolved_thread_id,
        hashlib.sha256(objective.encode()).hexdigest(),
        hashlib.sha256(canonical_json(start_input).encode()).hexdigest(),
    )
    canonical_payload = canonical_json(payload)
    payload_hash = hashlib.sha256(canonical_payload.encode()).hexdigest()
    ticket_id = hashlib.sha256(
        f"simple-harness.workflow.workflow-launch/ticket/v1|{payload_hash}".encode()
    ).hexdigest()
    if (
        request_columns != durable_request_columns
        or payload_catalog_columns != durable_catalog_columns
        or payload_derived_columns != durable_derived_columns
        or str(row["canonical_payload"]) != canonical_payload
        or str(row["payload_hash"]) != payload_hash
        or str(row["ticket_id"]) != ticket_id
        or str(row["ticket_receipt_id"])
        != hashlib.sha256(
            f"simple-harness.workflow.workflow-launch/receipt/v1|{request_key}".encode()
        ).hexdigest()
    ):
        raise UnitOfWorkConflict("workflow launch ticket self-hash differs")
    issue_authority_hash = payload.get("issue_authority_hash")
    if (
        not isinstance(issue_authority_hash, str)
        or issue_authority_hash != str(row["issue_authority_hash"])
        or objective != str(row["objective"])
        or canonical_json(launch_request.spawn_origin.to_json()) != str(row["spawn_origin_json"])
        or launch_request.spawn_origin.parent_run_id != str(row["parent_run_id"])
        or launch_request.root_run_id != str(row["root_run_id"])
        or launch_request.attachment_policy.value != str(row["attachment_policy"])
        or launch_request.child_command_id != str(row["child_command_id"])
    ):
        raise UnitOfWorkConflict("workflow launch spawn authority binding differs")
    values.update(
        {
            "objective": objective,
            "spawn_origin": launch_request.spawn_origin,
            "parent_run_id": launch_request.spawn_origin.parent_run_id,
            "root_run_id": launch_request.root_run_id,
            "attachment_policy": launch_request.attachment_policy,
            "child_command_id": launch_request.child_command_id,
        }
    )
    return _create_verified_workflow_launch_ticket(values)


def _thaw(value: object) -> dict[str, JsonValue]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    raise TypeError("expected JSON object")


def _thaw_json(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError(type(value).__name__)


def _run_record(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=str(row["run_id"]),
        execution_session_id=str(row["execution_session_id"]),
        request_id=str(row["request_id"]),
        root_run_id=str(row["root_run_id"]),
        parent_run_id=None if row["parent_run_id"] is None else str(row["parent_run_id"]),
        profile_key=str(row["profile_key"]),
        driver_kind=str(row["driver_kind"]),
        state=RunState(row["state"]),
        version=int(row["version"]),
    )


def _workflow_checkpoint(row: sqlite3.Row) -> WorkflowCheckpoint:
    return WorkflowCheckpoint(
        run_id=str(row["run_id"]),
        namespace=str(row["namespace"]),
        checkpoint=freeze_json(json.loads(str(row["checkpoint_json"]))),
        checkpoint_hash=str(row["checkpoint_hash"]),
        lease_epoch=int(row["lease_epoch"]),
        version=int(row["version"]),
    )


def _continuation_progress_receipt(row: sqlite3.Row) -> ContinuationProgressReceipt:
    return ContinuationProgressReceipt(
        receipt_id=str(row["receipt_id"]),
        continuation_id=str(row["continuation_id"]),
        run_id=str(row["run_id"]),
        owner_id=str(row["owner_id"]),
        runtime_lease_epoch=int(row["runtime_lease_epoch"]),
        claim_epoch=int(row["claim_epoch"]),
        outcome_hash=str(row["outcome_hash"]),
    )


def _child_terminal_receipt(row: sqlite3.Row) -> ChildTerminalReceipt:
    return ChildTerminalReceipt(
        receipt_id=str(row["receipt_id"]),
        command_id=str(row["command_id"]),
        child_run_id=str(row["child_run_id"]),
        terminal_state=str(row["terminal_state"]),
        outcome_hash=str(row["outcome_hash"]),
        signal_id=None if row["signal_id"] is None else str(row["signal_id"]),
        event_id=str(row["event_id"]),
        owner_id=str(row["owner_id"]),
        runtime_lease_epoch=int(row["runtime_lease_epoch"]),
        fence_epoch=int(row["fence_epoch"]),
    )


def _delivery_record(row: sqlite3.Row) -> DeliveryRecord:
    return DeliveryRecord(
        delivery_id=str(row["delivery_id"]),
        run_id=str(row["run_id"]),
        sink_kind=str(row["sink_kind"]),
        idempotency_key=str(row["idempotency_key"]),
        payload=freeze_json(json.loads(str(row["payload_json"]))),
        state=DeliveryState(str(row["state"])),
        version=int(row["version"]),
    )


def _child_signal_record(row: sqlite3.Row) -> ChildSignalRecord:
    return ChildSignalRecord(
        signal_id=str(row["signal_id"]),
        parent_run_id=str(row["parent_run_id"]),
        child_run_id=str(row["child_run_id"]),
        payload=freeze_json(json.loads(str(row["payload_json"]))),
        state=ChildSignalState(str(row["state"])),
        version=int(row["version"]),
        claimed_by=None if row["claimed_by"] is None else str(row["claimed_by"]),
        claimed_at=None if row["claimed_at"] is None else float(row["claimed_at"]),
        claim_expires_at=(
            None if row["claim_expires_at"] is None else float(row["claim_expires_at"])
        ),
        claim_epoch=int(row["claim_epoch"]),
        acked_at=None if row["acked_at"] is None else float(row["acked_at"]),
        ack_receipt_id=(None if row["ack_receipt_id"] is None else str(row["ack_receipt_id"])),
    )


def _child_signal_ack_receipt(row: sqlite3.Row) -> ChildSignalAckReceipt:
    return ChildSignalAckReceipt(
        receipt_id=str(row["receipt_id"]),
        signal_id=str(row["signal_id"]),
        parent_run_id=str(row["parent_run_id"]),
        owner_id=str(row["owner_id"]),
        claim_epoch=int(row["claim_epoch"]),
        continuation_id=str(row["continuation_id"]),
        event_id=str(row["event_id"]),
        continuation_payload_hash=str(row["continuation_payload_hash"]),
        event_payload_hash=str(row["event_payload_hash"]),
        created_at=float(row["created_at"]),
    )


def _tool_result_json(result: ToolResult) -> str:
    return canonical_json(
        {
            "call_id": result.call_id.value,
            "outcome": result.outcome.value,
            "value": thaw_json(result.value),
            "error_code": result.error_code,
            "public_message": result.public_message,
            "retryable": result.retryable,
        }
    )


def _tool_result(value: object) -> ToolResult | None:
    if value is None:
        return None
    payload = json.loads(str(value))
    return ToolResult(
        call_id=CallId(str(payload["call_id"])),
        outcome=ToolOutcome(str(payload["outcome"])),
        value=payload.get("value"),
        error_code=payload.get("error_code"),
        public_message=payload.get("public_message"),
        retryable=bool(payload.get("retryable", False)),
    )


def _effect_record(row: sqlite3.Row) -> EffectRecord:
    envelope_json = row["task_execution_envelope_json"]
    envelope_hash = row["task_execution_envelope_hash"]
    if (envelope_json is None) != (envelope_hash is None):
        raise ValueError("stored TaskExecutionEnvelope payload/hash differ")
    envelope = None
    if envelope_json is not None:
        payload = json.loads(str(envelope_json))
        if not isinstance(payload, dict):
            raise TypeError("stored TaskExecutionEnvelope must be an object")
        envelope = TaskExecutionEnvelope.from_json(payload)
        if envelope.envelope_hash != str(envelope_hash):
            raise ValueError("stored TaskExecutionEnvelope hash differs")
    return EffectRecord(
        effect_id=EffectId(str(row["effect_id"])),
        run_id=RunId(str(row["run_id"])),
        call_id=CallId(str(row["call_id"])),
        tool_name=str(row["tool_name"]),
        request_hash=str(row["request_hash"]),
        arguments=freeze_json(json.loads(str(row["arguments_json"]))),
        state=EffectState(str(row["state"])),
        version=int(row["version"]),
        fence_epoch=int(row["fence_epoch"]),
        authorization_receipt_ref=str(row["authorization_receipt_ref"]),
        handoff_receipt_ref=(
            None if row["handoff_receipt_ref"] is None else str(row["handoff_receipt_ref"])
        ),
        evidence_ref=(None if row["evidence_ref"] is None else str(row["evidence_ref"])),
        result=_tool_result(row["result_json"]),
        raw_call_id=(None if row["raw_call_id"] is None else str(row["raw_call_id"])),
        turn_ordinal=int(row["turn_ordinal"]),
        call_ordinal=int(row["call_ordinal"]),
        handoff_attempt=int(row["handoff_attempt"]),
        rehandoff_count=int(row["rehandoff_count"]),
        task_execution_envelope=envelope,
    )


def _wait_blocker_record(row: sqlite3.Row) -> WaitBlockerRecord:
    return WaitBlockerRecord(
        blocker_id=str(row["blocker_id"]),
        run_id=str(row["run_id"]),
        kind=RecoveryKind(str(row["kind"])),
        ledger_identity=str(row["ledger_identity"]),
        handoff_attempt=int(row["handoff_attempt"]),
        observed_version=int(row["observed_version"]),
        resolution_id=(None if row["resolution_id"] is None else str(row["resolution_id"])),
        wake_consumed=bool(row["wake_consumed"]),
        version=int(row["version"]),
    )


def _reconciliation_resolution(row: sqlite3.Row) -> ReconciliationResolution:
    return ReconciliationResolution(
        resolution_id=str(row["resolution_id"]),
        kind=RecoveryKind(str(row["kind"])),
        ledger_identity=str(row["ledger_identity"]),
        handoff_attempt=int(row["handoff_attempt"]),
        outcome=ResolutionOutcome(str(row["outcome"])),
        outcome_hash=str(row["outcome_hash"]),
        evidence_ref=str(row["evidence_ref"]),
        payload=json.loads(str(row["payload_json"])),
    )

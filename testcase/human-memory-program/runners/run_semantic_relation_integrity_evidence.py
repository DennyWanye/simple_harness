#!/usr/bin/env python3
"""Verify the complete semantic-relation integrity evidence index fail-closed.

This verifier never manufactures product evidence. Before post-build pinning or
when any frozen case artifact is absent it returns NOT_RUN/BLOCKED.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

BLOCKED_EXIT = 3
HEX64 = re.compile(r"^[0-9a-f]{64}$")

EXECUTOR_BOOTSTRAP = r"""
import asyncio
import copy
import json
import sqlite3
import sys
import time

import simple_harness
import simple_harness_memory

request = json.loads(open(sys.argv[1], encoding="utf-8").read())
db_path = sys.argv[2]
case_id = request["case_id"]
phase = request["phase"]
calls = []
fault_events = []
command_results = []
fault_points = {
    "before-relation-memory-insert": "mutation.before_relation_memory_insert",
    "after-memory-before-edge": "mutation.after_relation_memory_before_knowledge_row",
    "after-edge-before-commit": "mutation.after_knowledge_row_before_commit",
    "commit-before-ack": "mutation.after_commit",
}

class VerifierInjectedFault(RuntimeError):
    pass

class EvidenceAuthority:
    def __init__(self, values):
        self.values = values

    async def resolve_admitted_evidence(self, span):
        key = (span.evidence_id, span.item_ordinal)
        return self.values[key]

    async def resolve_typed_observation(self, reference):
        raise ValueError("typed observation is not authorized by this frozen case")

class ActionAuthority:
    def __init__(self, values):
        self.values = values

    async def resolve_memory_action_authority(self, reference):
        return self.values[reference.authority_id]

def principal(value):
    return simple_harness_memory.MemoryPrincipal(**value)

def scope(value):
    return simple_harness_memory.MemoryScope(
        simple_harness_memory.ScopeKind(value["kind"]), value["owner_id"]
    )

def evidence_authority(command):
    values = {}
    for wire in command.get("admitted_evidence", []):
        envelope = simple_harness.SanitizedEvidenceEnvelope.from_json(wire["envelope"])
        receipt = simple_harness.SanitizedEvidenceReceipt.from_json(wire["receipt"])
        item_wire = wire["item_authority"]
        item = simple_harness.EvidenceItemAuthority(
            schema_version=item_wire["schema_version"],
            authority_id=item_wire["authority_id"],
            evidence_id=item_wire["evidence_id"],
            envelope_hash=item_wire["envelope_hash"],
            sanitized_hash=item_wire["sanitized_hash"],
            source_hash=item_wire["source_hash"],
            source_kind=simple_harness.EvidenceSourceKind(item_wire["source_kind"]),
            item_ordinal=item_wire["item_ordinal"],
            item_id=item_wire["item_id"],
            item_json_pointer=item_wire["item_json_pointer"],
            normalization_version=item_wire["normalization_version"],
            actor_role=simple_harness.EvidenceActorRole(item_wire["actor_role"]),
            provenance=simple_harness.EvidenceProvenance(item_wire["provenance"]),
            required_privacy_class=simple_harness.PrivacyClass(
                item_wire["required_privacy_class"]
            ),
            required_information_attributes=tuple(
                simple_harness.InformationAttribute(value)
                for value in item_wire["required_information_attributes"]
            ),
            classification_authority_ref=item_wire["classification_authority_ref"],
            issuer_ref=item_wire["issuer_ref"],
        )
        values[(item.evidence_id, item.item_ordinal)] = simple_harness.AdmittedEvidenceAuthority(
            envelope, receipt, item
        )
    return EvidenceAuthority(values)

def action_authority(values):
    return ActionAuthority(values)

def exact_setup_ref(operation_id):
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT memory_id,revision FROM cognitive_memory_revisions "
            "WHERE operation_id=? ORDER BY memory_id,revision",
            (operation_id,),
        ).fetchall()
    finally:
        connection.close()
    if len(rows) != 1:
        raise ValueError("setup operation exact ref is unresolved or ambiguous")
    return str(rows[0][0]), int(rows[0][1])

def apply_head(subject):
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT revision FROM cognitive_apply_heads WHERE principal_id=?",
            (subject,),
        ).fetchone()
    finally:
        connection.close()
    return 1 if row is None else int(row[0])

def current_revision(operation_id):
    memory_id, _ = exact_setup_ref(operation_id)
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT current_revision FROM cognitive_memory_heads WHERE memory_id=?",
            (memory_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError("setup memory head is missing")
    return int(row[0])

def resolve_templates(value, subject):
    if isinstance(value, dict):
        return {key: resolve_templates(item, subject) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_templates(item, subject) for item in value]
    if value == "$apply_head_revision":
        return apply_head(subject)
    if isinstance(value, str) and value.startswith("$setup_memory:"):
        return exact_setup_ref(value.split(":", 1)[1])[0]
    if isinstance(value, str) and value.startswith("$setup_revision:"):
        return exact_setup_ref(value.split(":", 1)[1])[1]
    if isinstance(value, str) and value.startswith("$current_revision:"):
        return current_revision(value.split(":", 1)[1])
    if isinstance(value, str) and value.startswith("$verifier_now_plus:"):
        return time.time() + float(value.split(":", 1)[1])
    return value

def mutation_target(wire):
    if wire is None:
        return None
    if wire["target_kind"] == "existing_memory":
        return simple_harness.ExistingMemoryTarget.from_json(wire)
    if wire["target_kind"] == "created_by_operation":
        return simple_harness.CreatedByOperationTarget.from_json(wire)
    raise ValueError("mutation target kind is unsupported")

def mutation_payload(wire):
    if wire is None:
        return None
    memory_type = wire["memory_type"]
    if memory_type == "semantic":
        if wire["semantic_kind"] == "relation":
            return simple_harness.SemanticRelationMemoryPayload.from_json(wire)
        return simple_harness.SemanticMemoryPayload.from_json(wire)
    classes = {
        "episode": simple_harness.EpisodeMemoryPayload,
        "procedure": simple_harness.ProcedureMemoryPayload,
        "prospective": simple_harness.ProspectiveMemoryPayload,
    }
    return classes[memory_type].from_json(wire)

def lifecycle(memory_type, value):
    classes = {
        "episode": simple_harness.EpisodeLifecycleState,
        "semantic": simple_harness.SemanticLifecycleState,
        "procedure": simple_harness.ProcedureLifecycleState,
        "prospective": simple_harness.ProspectiveLifecycleState,
    }
    return classes[memory_type](value)

def rebuild_operation(wire, authority_ref=None):
    memory_type = wire["memory_type"]
    return simple_harness.MemoryMutationOperation(
        operation_id=wire["operation_id"],
        kind=simple_harness.MemoryMutationKind(wire["kind"]),
        memory_type=simple_harness.LongTermMemoryType(memory_type),
        payload=mutation_payload(wire["payload"]),
        target=mutation_target(wire["target"]),
        depends_on_operation_ids=tuple(wire["depends_on_operation_ids"]),
        lifecycle_state=lifecycle(memory_type, wire["lifecycle_state"]),
        epistemic_status=simple_harness.EpistemicStatus(wire["epistemic_status"]),
        conflict_status=simple_harness.ConflictStatus(wire["conflict_status"]),
        verification_state=simple_harness.VerificationState(wire["verification_state"]),
        valid_time_interval=simple_harness.ValidTimeInterval.from_json(
            wire["valid_time_interval"]
        ),
        proposed_privacy_class=simple_harness.PrivacyClass(
            wire["proposed_privacy_class"]
        ),
        proposed_information_attributes=tuple(
            simple_harness.InformationAttribute(item)
            for item in wire["proposed_information_attributes"]
        ),
        evidence_spans=tuple(
            simple_harness.EvidenceSpanRef.from_json(item)
            for item in wire["evidence_spans"]
        ),
        reason_code=wire["reason_code"],
        action_authority_ref=authority_ref,
    )

def rebuild_plan(wire, operations):
    return simple_harness.MemoryMutationPlan(
        plan_id=wire["plan_id"],
        run_id=wire["run_id"],
        turn_id=wire["turn_id"],
        subject=wire["subject"],
        base_revision=wire["base_revision"],
        outcome=simple_harness.MemoryMutationPlanOutcome(wire["outcome"]),
        operations=tuple(operations),
        disclosure_context=simple_harness.DisclosureContext.from_json(
            wire["disclosure_context"]
        ),
        evidence_refs=tuple(
            simple_harness.EvidenceRef.from_json(item) for item in wire["evidence_refs"]
        ),
        idempotency_key=wire["idempotency_key"],
        apply_mode=simple_harness.MemoryMutationApplyMode(wire["apply_mode"]),
        schema_version=wire["schema_version"],
    )

def materialize_apply(command):
    wire = resolve_templates(copy.deepcopy(command["plan"]), command["plan"]["subject"])
    operations = [rebuild_operation(item) for item in wire["operations"]]
    authority_free_plan = rebuild_plan(wire, operations)
    templates = command.get("memory_action_authorities", [])
    authorities = {}
    refs = {}
    for template in templates:
        if set(template) != {
            "template_kind", "authority_id", "operation_id", "target_operation_id",
            "issuer_ref", "issued_at", "expires_at", "nonce",
        } or template["template_kind"] != "verifier_memory_action_authority_v1":
            raise ValueError("memory action authority template differs")
        intent = authority_free_plan.action_intent(template["operation_id"])
        authority = simple_harness.issue_memory_action_authority(
            intent,
            authority_id=template["authority_id"],
            issued_at=template["issued_at"],
            expires_at=template["expires_at"],
            nonce=template["nonce"],
            issuer_ref=template["issuer_ref"],
        )
        authorities[authority.authority_id] = authority
        refs[template["operation_id"]] = (
            simple_harness.MemoryActionAuthorityRef.from_authority(authority)
        )
    if refs:
        operations = [
            rebuild_operation(item, refs.get(item["operation_id"]))
            for item in wire["operations"]
        ]
    plan = rebuild_plan(wire, operations)
    if plan.plan_intent_hash != authority_free_plan.plan_intent_hash:
        raise ValueError("authority materialization changed plan intent")
    return plan, authorities

async def execute():
    for command_index, command in enumerate(request["commands"]):
        kind = command["kind"]
        if kind == "sleep":
            seconds = float(command["seconds"])
            if not 0.0 <= seconds <= 2.0:
                raise ValueError("sleep command exceeds frozen bound")
            await asyncio.sleep(seconds)
            command_results.append({"index": command_index, "kind": kind, "status": "RETURNED"})
            continue

        plan = None
        authority_values = {}
        if kind == "apply_memory_mutation_plan":
            plan, authority_values = materialize_apply(command)
        manager = await simple_harness_memory.build_human_memory_v7(
            db_path,
            evidence_authority=evidence_authority(command),
            memory_action_authority=action_authority(authority_values),
            classification_policy=(
                simple_harness_memory.InformationClassificationPolicy(
                    policy_id="semantic-relation-integrity-policy",
                    policy_version="1",
                    authority_ref="verifier:classification-policy/v1",
                    required_privacy_class=simple_harness.PrivacyClass.PERSONAL,
                    required_information_attributes=(),
                )
            ),
        )
        try:
            if kind == "ingest_committed_evidence":
                envelope = simple_harness.SanitizedEvidenceEnvelope.from_json(command["envelope"])
                receipt = simple_harness.SanitizedEvidenceReceipt.from_json(command["receipt"])
                result = await manager.ingest_committed_evidence(envelope, receipt)
                command_results.append({
                    "index": command_index,
                    "kind": kind,
                    "status": "RETURNED",
                    "result_type": type(result).__name__,
                })
                continue

            if kind == "suppress":
                wire = command["request"]
                suppression = simple_harness_memory.SuppressionRequest(
                    request_id=wire["request_id"],
                    subject=wire["subject"],
                    scope_kind=simple_harness_memory.SuppressionScopeKind(wire["scope_kind"]),
                    scope_ref=wire["scope_ref"],
                    reason_code=wire["reason_code"],
                    requested_at=wire["requested_at"],
                    purpose=(
                        None
                        if wire["purpose"] is None
                        else simple_harness_memory.OrdinaryMemoryPurpose(wire["purpose"])
                    ),
                    schema_version=wire["schema_version"],
                )
                result = await manager.suppress(
                    principal=principal(command["principal"]), request=suppression
                )
                command_results.append({
                    "index": command_index,
                    "kind": kind,
                    "status": "RETURNED",
                    "result_type": type(result).__name__,
                })
                continue

            if kind != "apply_memory_mutation_plan":
                raise ValueError("unsupported verifier command")
            if plan is None:
                raise ValueError("executor did not materialize mutation plan")
            call_index = len(calls)
            target = None
            if phase == "exercise" and case_id in fault_points:
                if case_id != "commit-before-ack" or call_index == 0:
                    target = fault_points[case_id]
            backend = manager.backend
            previous = getattr(backend, "_fault_injector", None)

            def inject(point):
                if previous is not None:
                    previous(point)
                if point == target:
                    fault_events.append(point)
                    raise VerifierInjectedFault(point)

            if target is not None:
                setattr(backend, "_fault_injector", inject)
            try:
                result = await manager.apply_memory_mutation_plan(
                    principal=principal(command["principal"]),
                    scope=scope(command["scope"]),
                    plan=plan,
                )
            except BaseException as exc:
                calls.append({
                    "index": call_index,
                    "status": "RAISED",
                    "result_type": None,
                    "result": None,
                    "exception_type": type(exc).__name__,
                    "exception_reason": str(exc)[:256],
                })
            else:
                calls.append({
                    "index": call_index,
                    "status": "RETURNED",
                    "result_type": type(result).__name__,
                    "result": result.to_json(),
                    "exception_type": None,
                    "exception_reason": None,
                })
            finally:
                if target is not None:
                    setattr(backend, "_fault_injector", previous)
        finally:
            await manager.close()

asyncio.run(execute())
print(json.dumps({
    "schema_version": 1,
    "case_id": case_id,
    "phase": phase,
    "execution_nonce": request["execution_nonce"],
    "invocation_hash": request["invocation_hash"],
    "calls": calls,
    "fault_events": fault_events,
    "command_results": command_results,
}, sort_keys=True))
"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _case_specs(fixture: Mapping[str, Any]) -> dict[str, tuple[str, Mapping[str, Any]]]:
    result: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for group in (
        "positive_endpoint_cases",
        "rejection_cases",
        "pre_admission_wire_cases",
        "transaction_fault_cases",
        "replay_cases",
        "lifecycle_cases",
        "restart_corruption_cases",
    ):
        for item in fixture[group]:
            result[item["id"]] = (group, item)
    return result


def self_check(fixture_path: Path) -> dict[str, Any]:
    fixture = _fixture(fixture_path)
    errors: list[str] = []
    if fixture.get("fixture_revision") != 1:
        errors.append("fixture_revision must be 1")
    if fixture.get("protocol_schema_version") != 5:
        errors.append("protocol schema must be 5")
    if fixture.get("storage_schema_version") != 7:
        errors.append("storage schema must be 7")
    if fixture.get("quality_gate") != "NOT_RUN/BLOCKED":
        errors.append("quality gate must remain blocked before candidate execution")
    cases = _case_specs(fixture)
    if len(cases) != 40:
        errors.append("integrity matrix must contain exactly 40 unique cases")
    expected_total = sum(
        len(fixture[name])
        for name in (
            "positive_endpoint_cases",
            "rejection_cases",
            "pre_admission_wire_cases",
            "transaction_fault_cases",
            "replay_cases",
            "lifecycle_cases",
            "restart_corruption_cases",
        )
    )
    if len(cases) != expected_total:
        errors.append("integrity case ids must be unique")
    case_command_plans = fixture.get("case_command_plans")
    if not isinstance(case_command_plans, dict) or set(case_command_plans) != set(cases):
        errors.append("case command plans must cover every frozen case exactly once")
    else:
        allowed_kinds = {
            "ingest_committed_evidence",
            "apply_memory_mutation_plan",
            "suppress",
            "sleep",
        }
        for case_id, phases in case_command_plans.items():
            if not isinstance(phases, dict) or set(phases) != {"setup", "exercise"}:
                errors.append(f"case command phases differ: {case_id}")
                continue
            for phase, plan in phases.items():
                if not isinstance(plan, dict) or set(plan) != {
                    "command_kinds",
                    "canonical_sha256",
                }:
                    errors.append(f"case command plan schema differs: {case_id}/{phase}")
                    continue
                kinds = plan["command_kinds"]
                if (
                    not isinstance(kinds, list)
                    or any(kind not in allowed_kinds for kind in kinds)
                    or not _valid_hash(plan["canonical_sha256"])
                ):
                    errors.append(f"case command plan value differs: {case_id}/{phase}")
    boundaries = {
        item.get("boundary") for item in fixture.get("rejection_cases", [])
    }
    if boundaries != {"harness_pre_admission", "memory_post_admission"}:
        errors.append("rejection boundaries must separate Harness and Memory ownership")
    contract = fixture.get("execution_contract", {})
    artifact_fields = contract.get("required_artifact_fields", [])
    if len(artifact_fields) != 23 or len(set(artifact_fields)) != 23:
        errors.append("artifact fields must be 23 unique names")
    if contract.get("required_adapter_result_fields") != [
        "case_id",
        "phase",
        "execution_nonce",
        "invocation_hash",
        "commands",
        "invalid_wire",
    ]:
        errors.append("adapter result fields differ")
    producer_forbidden_fields = {
        "calls",
        "fault_events",
        "outcome",
        "reason_code",
        "memory_call_count",
        "evidence_artifact_ref",
    }
    if producer_forbidden_fields & set(
        contract.get("required_adapter_result_fields", [])
    ):
        errors.append("adapter contract still accepts producer-reported evidence")
    if (
        "runpy" in EXECUTOR_BOOTSTRAP
        or "__globals__" not in contract.get("adapter_forbidden_source_tokens", [])
    ):
        errors.append("adapter and verifier execution are not process-isolated")
    malicious_request = {
        "case_id": "probe",
        "phase": "exercise",
        "execution_nonce": "d" * 64,
        "invocation_hash": "e" * 64,
        "commands": [],
        "invalid_wire": None,
        "calls": [{"status": "RETURNED"}],
        "fault_events": ["mutation.after_commit"],
    }
    if _validate_adapter_request(
        malicious_request,
        case_id="probe",
        phase="exercise",
        execution_nonce="d" * 64,
        invocation_hash="e" * 64,
        contract=contract,
    ) is None:
        errors.append("adapter request accepted producer-reported execution evidence")
    fake_artifact = _self_attested_fake_artifact(fixture, contract)
    common_error = _validate_common_artifact(
        fake_artifact,
        case_id="created-endpoints",
        execution_nonce="d" * 64,
        invocation_hash="e" * 64,
        candidate=fixture["candidate_identity"],
        contract=contract,
    )
    if common_error is not None:
        errors.append(f"self-attestation probe setup invalid: {common_error}")
    elif _validate_case_oracle(
        "created-endpoints",
        "positive_endpoint_cases",
        fixture["positive_endpoint_cases"][0],
        fake_artifact,
    ) is None:
        errors.append("self-attested shallow PASS was accepted")
    errors.extend(_self_attestation_escape_errors(fixture))
    if errors:
        return {"status": "FAIL", "errors": errors}
    return {
        "status": "PASS",
        "fixture_sha256": _sha256(fixture_path),
        "frozen_case_count": len(cases),
        "candidate_execution": "NOT_RUN/BLOCKED",
    }


def _candidate_identity(
    fixture: Mapping[str, Any], args: argparse.Namespace
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    candidate = fixture.get("candidate_identity", {})
    if candidate.get("status") != "PINNED":
        return None, {
            "status": "NOT_RUN/BLOCKED",
            "reason": "semantic relation integrity candidate is not pinned",
        }
    if not _valid_hash(candidate.get("integrity_adapter_sha256")):
        return None, {"status": "FAIL", "reason": "integrity adapter pin is invalid"}
    supplied = {
        "harness": (
            args.harness_wheel,
            args.harness_wheel_sha256,
            args.harness_source_commit,
        ),
        "memory": (
            args.memory_wheel,
            args.memory_wheel_sha256,
            args.memory_source_commit,
        ),
    }
    for key, (path_value, digest, commit) in supplied.items():
        if not path_value:
            return None, {
                "status": "NOT_RUN/BLOCKED",
                "reason": f"candidate wheel missing: {key}",
            }
        path = Path(path_value).resolve()
        if not path.is_file() or _sha256(path) != digest:
            return None, {"status": "FAIL", "reason": f"wheel hash mismatch: {key}"}
        pin = candidate.get(key, {})
        if digest != pin.get("wheel_sha256") or commit != pin.get("source_commit"):
            return None, {"status": "FAIL", "reason": f"candidate pin mismatch: {key}"}
    return dict(candidate), None


def _valid_hash(value: object) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _row_delta(artifact: Mapping[str, Any], field: str) -> int:
    return int(artifact["after_row_cardinality"][field]) - int(
        artifact["before_row_cardinality"][field]
    )


def _exact_row_deltas(artifact: Mapping[str, Any], expected: Mapping[str, int]) -> bool:
    return all(_row_delta(artifact, field) == delta for field, delta in expected.items())


def _roots_unchanged(artifact: Mapping[str, Any], fields: set[str]) -> bool:
    return all(
        artifact["before_roots"][field] == artifact["after_roots"][field]
        for field in fields
    )


def _receipt_pair_valid(
    artifact: Mapping[str, Any], *, first: bool, replay_equal: bool = False
) -> bool:
    first_hash = artifact["first_receipt_hash"]
    replay_hash = artifact["replay_receipt_hash"]
    if first and not _valid_hash(first_hash):
        return False
    if not first and first_hash is not None:
        return False
    if replay_equal:
        return _valid_hash(replay_hash) and replay_hash == first_hash
    return replay_hash is None


def _reopen_matches_graph(
    reopen: Mapping[str, Any], graph: Mapping[str, Any], *, status: str = "PASS"
) -> bool:
    return (
        reopen["status"] == status
        and reopen["edge_count"] == graph["edge_count"]
        and reopen["payload_sha256"] == graph["payload_sha256"]
    )


def _validate_common_artifact(
    artifact: Mapping[str, Any],
    *,
    case_id: str,
    execution_nonce: str,
    invocation_hash: str,
    candidate: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> str | None:
    if set(artifact) != set(contract["required_artifact_fields"]):
        return "artifact schema differs"
    if artifact["schema_version"] != 1 or artifact["case_id"] != case_id:
        return "artifact identity differs"
    if artifact["execution_nonce"] != execution_nonce:
        return "artifact execution nonce differs"
    if artifact["invocation_hash"] != invocation_hash:
        return "artifact invocation hash differs"
    if artifact["candidate_identity"] != candidate:
        return "artifact candidate identity differs"
    if not isinstance(artifact["memory_call_count"], int) or artifact[
        "memory_call_count"
    ] < 0:
        return "memory_call_count is invalid"
    root_fields = set(contract["required_root_fields"])
    for name in ("before_roots", "after_roots"):
        roots = artifact[name]
        if not isinstance(roots, Mapping) or set(roots) != root_fields:
            return f"{name} schema differs"
        if not all(_valid_hash(value) for value in roots.values()):
            return f"{name} contains an invalid hash"
    row_fields = set(contract["required_row_fields"])
    for name in ("before_row_cardinality", "after_row_cardinality"):
        rows = artifact[name]
        if not isinstance(rows, Mapping) or set(rows) != row_fields:
            return f"{name} schema differs"
        if not all(isinstance(value, int) and value >= 0 for value in rows.values()):
            return f"{name} contains an invalid count"
    before_raw = artifact["raw_evidence_hash_before"]
    if not _valid_hash(before_raw) or before_raw != artifact["raw_evidence_hash_after"]:
        return "raw evidence changed"
    if artifact["physical_delete_count"] != 0:
        return "physical delete count is nonzero"
    graph_fields = set(contract["required_graph_fields"])
    for name in ("graph_before", "graph_after"):
        graph = artifact[name]
        if not isinstance(graph, Mapping) or set(graph) != graph_fields:
            return f"{name} schema differs"
        for count_name in ("node_count", "edge_count", "relation_memory_node_count"):
            if not isinstance(graph[count_name], int) or graph[count_name] < 0:
                return f"{name} count is invalid"
        if graph["relation_memory_node_count"] != 0:
            return f"{name} exposes a relation memory node"
        if not _valid_hash(graph["payload_sha256"]):
            return f"{name} payload hash is invalid"
    reopen = artifact["reopen_result"]
    if not isinstance(reopen, Mapping) or set(reopen) != set(
        contract["required_reopen_fields"]
    ):
        return "reopen_result schema differs"
    if reopen["status"] not in {"PASS", "FAIL_CLOSED"}:
        return "reopen status is invalid"
    if not isinstance(reopen["edge_count"], int) or reopen["edge_count"] < 0:
        return "reopen edge count is invalid"
    if not _valid_hash(reopen["payload_sha256"]):
        return "reopen payload hash is invalid"
    if _row_delta(artifact, "evidence_rows") != 0:
        return "raw evidence row cardinality changed"
    for field in ("old_edge_count", "new_edge_count"):
        if artifact[field] is not None and (
            not isinstance(artifact[field], int) or artifact[field] < 0
        ):
            return f"{field} is invalid"
    if artifact["fault_seam"] is not None and not isinstance(
        artifact["fault_seam"], str
    ):
        return "fault_seam is invalid"
    return None


def _validate_case_oracle(
    case_id: str,
    group: str,
    spec: Mapping[str, Any],
    artifact: Mapping[str, Any],
) -> str | None:
    graph_before = artifact["graph_before"]
    graph_after = artifact["graph_after"]
    reopen = artifact["reopen_result"]
    stable_state_roots = {
        "evidence_root",
        "memory_state_root",
        "cognitive_relation_root",
        "graph_projection_root",
    }
    zero_state_deltas = {
        "evidence_rows": 0,
        "memory_revision_rows": 0,
        "knowledge_relation_rows": 0,
        "receipt_rows": 0,
    }

    if group == "positive_endpoint_cases":
        expected_memory_delta = 3 if case_id == "created-endpoints" else 1
        if (
            artifact["outcome"] != "COMMITTED"
            or artifact["reason_code"] is not None
            or artifact["memory_call_count"] != 1
            or graph_before["edge_count"] != 0
            or graph_after["edge_count"] != 1
            or not _reopen_matches_graph(reopen, graph_after)
            or not _exact_row_deltas(
                artifact,
                {
                    "evidence_rows": 0,
                    "memory_revision_rows": expected_memory_delta,
                    "knowledge_relation_rows": 1,
                    "receipt_rows": 1,
                    "audit_rows": 0,
                },
            )
            or not _receipt_pair_valid(artifact, first=True)
        ):
            return "positive endpoint terminal oracle differs"
        return None

    if group == "pre_admission_wire_cases" or (
        group == "rejection_cases" and spec["boundary"] == "harness_pre_admission"
    ):
        expected_reason = spec["reason_code"]
        if (
            artifact["outcome"] != "REJECTED_PRE_ADMISSION"
            or artifact["reason_code"] != expected_reason
            or artifact["memory_call_count"] != 0
            or artifact["before_roots"] != artifact["after_roots"]
            or artifact["before_row_cardinality"]
            != artifact["after_row_cardinality"]
            or graph_before != graph_after
            or not _reopen_matches_graph(reopen, graph_after)
            or not _receipt_pair_valid(artifact, first=False)
        ):
            return "Harness pre-admission rejection oracle differs"
        return None

    if group == "rejection_cases":
        if (
            artifact["outcome"] != "REJECTED"
            or artifact["reason_code"] != spec["reason_code"]
            or artifact["memory_call_count"] != 1
            or not _roots_unchanged(artifact, stable_state_roots)
            or not _exact_row_deltas(artifact, {**zero_state_deltas, "audit_rows": 1})
            or graph_before != graph_after
            or not _reopen_matches_graph(reopen, graph_after)
            or not _receipt_pair_valid(artifact, first=False)
        ):
            return "Memory post-admission rejection oracle differs"
        return None

    if group == "transaction_fault_cases":
        seam = spec["seam"]
        if case_id == "commit-before-ack":
            if (
                artifact["outcome"] != "COMMITTED_REPLAY"
                or artifact["reason_code"] != "commit_before_ack"
                or artifact["memory_call_count"] != 2
                or artifact["fault_seam"] != seam
                or graph_before["edge_count"] != 0
                or graph_after["edge_count"] != 1
                or not _reopen_matches_graph(reopen, graph_after)
                or not _exact_row_deltas(
                    artifact,
                    {
                        "evidence_rows": 0,
                        "memory_revision_rows": 3,
                        "knowledge_relation_rows": 1,
                        "receipt_rows": 1,
                        "audit_rows": 0,
                    },
                )
                or not _receipt_pair_valid(artifact, first=True, replay_equal=True)
            ):
                return "commit-before-ack oracle differs"
            return None
        if (
            artifact["outcome"] != "FAULT_ROLLED_BACK"
            or artifact["reason_code"] != seam
            or artifact["memory_call_count"] != 1
            or artifact["fault_seam"] != seam
            or not _roots_unchanged(artifact, stable_state_roots)
            or not _exact_row_deltas(artifact, {**zero_state_deltas, "audit_rows": 1})
            or graph_before != graph_after
            or not _reopen_matches_graph(reopen, graph_after)
            or not _receipt_pair_valid(artifact, first=False)
        ):
            return "transaction rollback oracle differs"
        return None

    if group == "replay_cases":
        if case_id == "exact-replay":
            expected_setup_rows = {
                "evidence_rows": 1,
                "memory_revision_rows": 3,
                "knowledge_relation_rows": 1,
                "receipt_rows": 1,
                "audit_rows": 0,
            }
            if (
                artifact["outcome"] != "REPLAYED"
                or artifact["reason_code"] is not None
                or artifact["memory_call_count"] != 1
                or artifact["before_row_cardinality"] != expected_setup_rows
                or graph_before["node_count"] != 2
                or graph_before["edge_count"] != 1
                or artifact["before_roots"] != artifact["after_roots"]
                or artifact["before_row_cardinality"]
                != artifact["after_row_cardinality"]
                or graph_before != graph_after
                or not _reopen_matches_graph(reopen, graph_after)
                or not _receipt_pair_valid(artifact, first=True, replay_equal=True)
            ):
                return "exact replay oracle differs"
            return None
        if (
            artifact["outcome"] != "REJECTED"
            or artifact["reason_code"] != "mutation_idempotency_hash_conflict"
            or artifact["memory_call_count"] != 1
            or not _roots_unchanged(artifact, stable_state_roots)
            or not _exact_row_deltas(artifact, {**zero_state_deltas, "audit_rows": 1})
            or graph_before != graph_after
            or not _reopen_matches_graph(reopen, graph_after)
            or not _receipt_pair_valid(artifact, first=True)
        ):
            return "conflicting replay oracle differs"
        return None

    if group == "lifecycle_cases":
        non_mutating = {"owner-expired", "endpoint-expired", "evidence-suppressed"}
        owner_revision_cases = {
            "owner-suppressed",
            "owner-contested",
            "owner-superseded",
            "classification-restricted",
        }
        if case_id == "relation-corrected":
            if (
                artifact["outcome"] != "COMMITTED"
                or artifact["reason_code"] is not None
                or artifact["memory_call_count"] != 1
                or graph_before["edge_count"] != 1
                or graph_after["edge_count"] != 1
                or artifact["old_edge_count"] != 0
                or artifact["new_edge_count"] != 1
                or not _reopen_matches_graph(reopen, graph_after)
                or not _exact_row_deltas(
                    artifact,
                    {
                        "evidence_rows": 0,
                        "memory_revision_rows": 2,
                        "knowledge_relation_rows": 1,
                        "receipt_rows": 1,
                        "audit_rows": 0,
                    },
                )
                or not _receipt_pair_valid(artifact, first=True)
            ):
                return "relation correction oracle differs"
            return None
        if graph_before["edge_count"] != 1 or graph_after["edge_count"] != 0:
            return "lifecycle edge did not exit"
        if not _reopen_matches_graph(reopen, graph_after):
            return "lifecycle edge revived after reopen"
        if case_id in non_mutating:
            expected_outcome = (
                "EVIDENCE_SUPPRESSED"
                if case_id == "evidence-suppressed"
                else "TIME_ELAPSED"
            )
            if (
                artifact["outcome"] != expected_outcome
                or artifact["reason_code"] is not None
                or artifact["memory_call_count"] != 0
                or artifact["before_row_cardinality"]
                != artifact["after_row_cardinality"]
                or not _receipt_pair_valid(artifact, first=False)
            ):
                return "non-mutating lifecycle oracle differs"
            return None
        if (
            artifact["outcome"] != "COMMITTED"
            or artifact["reason_code"] is not None
            or artifact["memory_call_count"] != 1
            or not _exact_row_deltas(
                artifact,
                {
                    "evidence_rows": 0,
                    "memory_revision_rows": (
                        2
                        if case_id in {"owner-contested", "owner-superseded"}
                        else 1
                    ),
                    "knowledge_relation_rows": (
                        1 if case_id in owner_revision_cases else 0
                    ),
                    "receipt_rows": 1,
                    "audit_rows": 0,
                },
            )
            or not _receipt_pair_valid(artifact, first=True)
        ):
            return "mutating lifecycle oracle differs"
        return None

    if group == "restart_corruption_cases":
        if (
            artifact["outcome"] != "FAIL_CLOSED"
            or artifact["reason_code"] != f"corruption_{case_id}"
            or artifact["memory_call_count"] != 0
            or graph_before["edge_count"] != 1
            or graph_after["edge_count"] != 0
            or reopen["status"] != "FAIL_CLOSED"
            or reopen["edge_count"] != 0
            or not _receipt_pair_valid(artifact, first=False)
        ):
            return "restart corruption fail-closed oracle differs"
        return None

    return "unknown relation integrity case group"


def _self_attested_fake_artifact(
    fixture: Mapping[str, Any], contract: Mapping[str, Any]
) -> dict[str, Any]:
    roots = {field: "a" * 64 for field in contract["required_root_fields"]}
    rows = {field: 0 for field in contract["required_row_fields"]}
    graph = {
        "node_count": 0,
        "edge_count": 0,
        "relation_memory_node_count": 0,
        "payload_sha256": "b" * 64,
    }
    return {
        "schema_version": 1,
        "case_id": "created-endpoints",
        "execution_nonce": "d" * 64,
        "invocation_hash": "e" * 64,
        "candidate_identity": fixture["candidate_identity"],
        "memory_call_count": 0,
        "outcome": "COMMITTED",
        "reason_code": None,
        "before_roots": dict(roots),
        "after_roots": dict(roots),
        "before_row_cardinality": dict(rows),
        "after_row_cardinality": dict(rows),
        "raw_evidence_hash_before": "c" * 64,
        "raw_evidence_hash_after": "c" * 64,
        "graph_before": dict(graph),
        "graph_after": dict(graph),
        "reopen_result": {
            "status": "PASS",
            "edge_count": 0,
            "payload_sha256": "b" * 64,
        },
        "first_receipt_hash": None,
        "replay_receipt_hash": None,
        "physical_delete_count": 0,
        "old_edge_count": None,
        "new_edge_count": None,
        "fault_seam": None,
    }


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _python_in_venv(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _adapter_uses_required_public_calls(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            calls.add(node.func.attr)
    return {
        "simple_harness",
        "simple_harness_memory",
    } <= imported and {
        "MemoryMutationPlan",
        "SanitizedEvidenceEnvelope",
        "SuppressionRequest",
    } <= calls


def _jsonable_sql(value: object) -> object:
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    return value


def _read_table(
    connection: sqlite3.Connection, table: str
) -> list[dict[str, object]]:
    cursor = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
    columns = [item[0] for item in cursor.description]
    return [
        {
            column: _jsonable_sql(value)
            for column, value in zip(columns, row, strict=True)
        }
        for row in cursor.fetchall()
    ]


def _inspect_database(db_path: Path) -> dict[str, Any]:
    if not db_path.is_file():
        raise FileNotFoundError("case database missing")
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = {
            table: _read_table(connection, table)
            for table in (
                "evidence_envelopes",
                "evidence_items",
                "cognitive_memory_heads",
                "cognitive_memory_revisions",
                "cognitive_relations",
                "memory_mutation_receipts",
                "memory_mutation_rejection_audits",
                "memory_mutation_apply_results",
            )
        }
    finally:
        connection.close()
    evidence_rows = tables["evidence_envelopes"]
    memory_rows = tables["cognitive_memory_revisions"]
    relation_rows = tables["cognitive_relations"]
    knowledge_rows = [
        item for item in relation_rows if item.get("relation_domain") == "knowledge"
    ]
    receipt_rows = tables["memory_mutation_receipts"]
    audit_rows = tables["memory_mutation_rejection_audits"]
    roots = {
        "evidence_root": _canonical_hash(
            {
                "envelopes": evidence_rows,
                "items": tables["evidence_items"],
            }
        ),
        "mutation_plan_root": _canonical_hash(
            {
                "receipts": receipt_rows,
                "rejections": audit_rows,
                "results": tables["memory_mutation_apply_results"],
            }
        ),
        "memory_state_root": _canonical_hash(
            {
                "heads": tables["cognitive_memory_heads"],
                "revisions": memory_rows,
            }
        ),
        "cognitive_relation_root": _canonical_hash(relation_rows),
        "graph_projection_root": "0" * 64,
    }
    receipt_hashes = [str(item["receipt_hash"]) for item in receipt_rows]
    rejection_reasons = [str(item["reason_code"]) for item in audit_rows]
    relation_owner_ids = {
        str(item["relation_memory_id"])
        for item in knowledge_rows
        if item.get("relation_memory_id") is not None
    }
    knowledge_relation_ids = [str(item["relation_id"]) for item in knowledge_rows]
    return {
        "roots": roots,
        "row_cardinality": {
            "evidence_rows": len(evidence_rows),
            "memory_revision_rows": len(memory_rows),
            "knowledge_relation_rows": len(knowledge_rows),
            "receipt_rows": len(receipt_rows),
            "audit_rows": len(audit_rows),
        },
        "raw_evidence_hash": roots["evidence_root"],
        "receipt_hashes": receipt_hashes,
        "rejection_reasons": rejection_reasons,
        "relation_owner_ids": sorted(relation_owner_ids),
        "knowledge_relation_ids": knowledge_relation_ids,
    }


def _inspect_graph(
    python: Path,
    db_path: Path,
    principal_context: Mapping[str, str],
    *,
    env: Mapping[str, str],
    relation_owner_ids: set[str],
) -> dict[str, Any]:
    probe_code = """
import asyncio,json,sqlite3,sys
import simple_harness_memory
async def main():
    db=sys.argv[1]; context=json.loads(sys.argv[2])
    manager=None
    try:
        connection=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
        try:
            row=connection.execute(
                "SELECT deployment_id,household_id,actor_id FROM principals "
                "WHERE principal_id=?",(context["actor_id"],)
            ).fetchone()
        finally:
            connection.close()
        if row is not None:
            context={
                "deployment_id":str(row[0]),
                "household_id":str(row[1]),
                "actor_id":str(row[2]),
                "session_id":context["session_id"],
            }
        manager=await simple_harness_memory.build_human_memory_v7(db)
        principal=simple_harness_memory.MemoryPrincipal(**context)
        view=await manager.get_twin_graph_view(principal=principal)
        result={'status':'PASS','graph':view.to_json()}
    except Exception as exc:
        result={'status':'FAIL_CLOSED','reason_code':type(exc).__name__}
    finally:
        if manager is not None:
            try:
                await manager.close()
            except Exception as exc:
                result={'status':'FAIL_CLOSED','reason_code':type(exc).__name__}
    print(json.dumps(result,sort_keys=True))
asyncio.run(main())
"""
    run = subprocess.run(
        [
            str(python),
            "-c",
            probe_code,
            str(db_path),
            json.dumps(principal_context, sort_keys=True),
        ],
        env=dict(env),
        capture_output=True,
        text=True,
        check=False,
    )
    if run.returncode != 0:
        raise RuntimeError("verifier-owned graph inspector failed")
    result = json.loads(run.stdout.strip().splitlines()[-1])
    if result.get("status") == "FAIL_CLOSED":
        payload_hash = _canonical_hash(result)
        return {
            "status": "FAIL_CLOSED",
            "summary": {
                "node_count": 0,
                "edge_count": 0,
                "relation_memory_node_count": 0,
                "payload_sha256": payload_hash,
            },
        }
    graph = result.get("graph")
    if not isinstance(graph, dict):
        raise TypeError("verifier-owned graph payload missing")
    normalized = {"nodes": graph.get("nodes"), "edges": graph.get("edges")}
    relation_memory_node_count = sum(
        1 for node in graph["nodes"] if node.get("memory_id") in relation_owner_ids
    )
    return {
        "status": "PASS",
        "graph": graph,
        "summary": {
            "node_count": len(graph["nodes"]),
            "edge_count": sum(
                1
                for edge in graph["edges"]
                if edge.get("relation_kind") == "applies_to"
            ),
            "relation_memory_node_count": relation_memory_node_count,
            "payload_sha256": _canonical_hash(normalized),
        },
    }


def _inspect_harness_wire(
    python: Path, wire_path: Path, *, env: Mapping[str, str]
) -> str:
    probe_code = """
import json,sys
import simple_harness
wire=json.loads(open(sys.argv[1],encoding='utf-8').read())
result=simple_harness.parse_memory_mutation_plan(wire)
print(json.dumps({'type':type(result).__name__,'value':result.to_json()},sort_keys=True))
"""
    run = subprocess.run(
        [str(python), "-c", probe_code, str(wire_path)],
        env=dict(env),
        capture_output=True,
        text=True,
        check=False,
    )
    if run.returncode != 0:
        raise RuntimeError("verifier-owned Harness parser failed")
    result = json.loads(run.stdout.strip().splitlines()[-1])
    if result.get("type") != "MemoryMutationValidationDiagnostic":
        raise RuntimeError("invalid wire was accepted by installed Harness parser")
    return str(result["value"]["reason_code"])


def _corrupt_relation_database(db_path: Path, case_id: str) -> None:
    updates = {
        "owner-mismatch": ("relation_memory_id", "missing-owner"),
        "domain-mismatch": ("relation_domain", "evolution"),
        "relation-hash-mismatch": ("relation_hash", "0" * 64),
        "endpoint-foreign-key-mismatch": ("source_memory_id", "missing-source"),
    }
    column, value = updates[case_id]
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("DROP TRIGGER IF EXISTS cognitive_relations_immutable_update")
        connection.execute(f'UPDATE cognitive_relations SET "{column}"=?', (value,))
        connection.commit()
    finally:
        connection.close()


def _validate_adapter_request(
    request: Any,
    *,
    case_id: str,
    phase: str,
    execution_nonce: str,
    invocation_hash: str,
    contract: Mapping[str, Any],
) -> str | None:
    if not isinstance(request, dict) or set(request) != set(
        contract["required_adapter_result_fields"]
    ):
        return "schema differs"
    if (
        request["case_id"] != case_id
        or request["phase"] != phase
        or request["execution_nonce"] != execution_nonce
        or request["invocation_hash"] != invocation_hash
    ):
        return "binding differs"
    commands = request["commands"]
    allowed_command_fields = {
        "ingest_committed_evidence": {"kind", "envelope", "receipt"},
        "apply_memory_mutation_plan": {
            "kind",
            "principal",
            "scope",
            "plan",
            "admitted_evidence",
            "memory_action_authorities",
        },
        "suppress": {"kind", "principal", "request"},
        "sleep": {"kind", "seconds"},
    }
    if (
        not isinstance(commands, list)
        or len(commands) > 16
        or any(
            not isinstance(command, dict)
            or command.get("kind") not in allowed_command_fields
            or set(command) != allowed_command_fields[command["kind"]]
            for command in commands
        )
    ):
        return "command plan differs"
    if request["invalid_wire"] is not None and not isinstance(
        request["invalid_wire"], dict
    ):
        return "invalid wire differs"
    return None


def _run_adapter_phase(
    *,
    python: Path,
    adapter: Path,
    fixture_path: Path,
    case_id: str,
    phase: str,
    case_dir: Path,
    execution_nonce: str,
    invocation_hash: str,
    env: Mapping[str, str],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="hm-adapter-request-") as request_value:
        request_dir = Path(request_value)
        producer = subprocess.run(
            [
                str(python),
                str(adapter),
                "--fixture",
                str(fixture_path),
                "--case-id",
                case_id,
                "--phase",
                phase,
                "--db-dir",
                str(request_dir),
                "--execution-nonce",
                execution_nonce,
                "--invocation-hash",
                invocation_hash,
            ],
            cwd=request_dir,
            env=dict(env),
            capture_output=True,
            text=True,
            check=False,
        )
        if producer.returncode != 0:
            raise RuntimeError(f"integrity request adapter failed for {case_id}")
        request = json.loads(producer.stdout.strip().splitlines()[-1])
    request_error = _validate_adapter_request(
        request,
        case_id=case_id,
        phase=phase,
        execution_nonce=execution_nonce,
        invocation_hash=invocation_hash,
        contract=contract,
    )
    if request_error is not None:
        raise RuntimeError(f"integrity request {request_error} for {case_id}")
    fixture = _fixture(fixture_path)
    expected_plan = fixture["case_command_plans"][case_id][phase]
    command_kinds = [command["kind"] for command in request["commands"]]
    command_payload_hash = _canonical_hash(
        {
            "commands": request["commands"],
            "invalid_wire": request["invalid_wire"],
        }
    )
    if (
        command_kinds != expected_plan["command_kinds"]
        or command_payload_hash != expected_plan["canonical_sha256"]
    ):
        raise RuntimeError(f"integrity request command plan differs for {case_id}/{phase}")
    if request["invalid_wire"] is not None:
        (case_dir / contract["case_wire_filename"]).write_text(
            json.dumps(request["invalid_wire"], ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    request_path = case_dir / f"verifier-{phase}-request.json"
    request_path.write_text(
        json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    execution = subprocess.run(
        [
            str(python),
            "-c",
            EXECUTOR_BOOTSTRAP,
            str(request_path),
            str(case_dir / contract["case_db_filename"]),
        ],
        cwd=case_dir,
        env=dict(env),
        capture_output=True,
        text=True,
        check=False,
    )
    if execution.returncode != 0:
        raise RuntimeError(
            f"verifier executor {phase} failed for {case_id}: {execution.stderr[-500:]}"
        )
    trace = json.loads(execution.stdout.strip().splitlines()[-1])
    expected_trace_fields = {
        "schema_version",
        "case_id",
        "phase",
        "execution_nonce",
        "invocation_hash",
        "calls",
        "fault_events",
        "command_results",
    }
    if not isinstance(trace, dict) or set(trace) != expected_trace_fields:
        raise RuntimeError(f"verifier bootstrap {phase} schema differs for {case_id}")
    if (
        trace["schema_version"] != 1
        or trace["case_id"] != case_id
        or trace["phase"] != phase
        or trace["execution_nonce"] != execution_nonce
        or trace["invocation_hash"] != invocation_hash
    ):
        raise RuntimeError(f"verifier bootstrap {phase} binding differs for {case_id}")
    calls = trace["calls"]
    if not isinstance(calls, list) or any(
        not isinstance(call, dict)
        or set(call)
        != {
            "index",
            "status",
            "result_type",
            "result",
            "exception_type",
            "exception_reason",
        }
        or call["index"] != index
        or call["status"] not in {"RETURNED", "RAISED"}
        for index, call in enumerate(calls)
    ):
        raise RuntimeError(f"verifier call trace differs for {case_id}")
    if not isinstance(trace["fault_events"], list) or not all(
        isinstance(point, str) for point in trace["fault_events"]
    ):
        raise RuntimeError(f"verifier fault trace differs for {case_id}")
    if not isinstance(trace["command_results"], list):
        raise TypeError(f"verifier command trace differs for {case_id}")
    trace_path = case_dir / f"verifier-{phase}-call-trace.json"
    trace_path.write_text(
        json.dumps(trace, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return trace


def _new_values(before: list[str], after: list[str]) -> list[str]:
    """Return the ordered multiset suffix added by one case exercise."""
    remaining = list(before)
    added: list[str] = []
    for value in after:
        if value in remaining:
            remaining.remove(value)
        else:
            added.append(value)
    return added


def _graph_edge_ids(graph: Mapping[str, Any]) -> set[str]:
    payload = graph.get("graph")
    if not isinstance(payload, Mapping):
        return set()
    edges = payload.get("edges")
    if not isinstance(edges, list):
        return set()
    return {
        str(edge["edge_id"])
        for edge in edges
        if isinstance(edge, Mapping)
        and edge.get("relation_kind") == "applies_to"
        and isinstance(edge.get("edge_id"), str)
    }


def _returned_result(call: Mapping[str, Any]) -> Mapping[str, Any] | None:
    result = call.get("result")
    if call.get("status") != "RETURNED" or not isinstance(result, Mapping):
        return None
    return result


def _committed_receipt_hash(call: Mapping[str, Any]) -> str | None:
    result = _returned_result(call)
    if result is None or result.get("outcome") != "committed":
        return None
    receipt = result.get("receipt_ref")
    if not isinstance(receipt, Mapping):
        return None
    value = receipt.get("receipt_hash")
    return str(value) if _valid_hash(value) else None


def _derive_artifact(
    *,
    case_id: str,
    group: str,
    spec: Mapping[str, Any],
    candidate: Mapping[str, Any],
    execution_nonce: str,
    invocation_hash: str,
    before_db: Mapping[str, Any],
    after_db: Mapping[str, Any],
    before_graph: Mapping[str, Any],
    after_graph: Mapping[str, Any],
    reopen_graph: Mapping[str, Any],
    setup_observation: Mapping[str, Any],
    exercise_observation: Mapping[str, Any],
    harness_reason: str | None,
) -> dict[str, Any]:
    """Build the frozen artifact only from verifier-owned observations."""
    new_receipts = _new_values(
        list(before_db["receipt_hashes"]), list(after_db["receipt_hashes"])
    )
    new_rejections = _new_values(
        list(before_db["rejection_reasons"]), list(after_db["rejection_reasons"])
    )
    setup_calls = list(setup_observation["calls"])
    exercise_calls = list(exercise_observation["calls"])
    if group == "replay_cases" and len(setup_calls) != 1:
        raise RuntimeError("replay setup must contain exactly one real apply call")
    memory_call_count = len(exercise_calls)
    first_receipt_hash: str | None = None
    replay_receipt_hash: str | None = None
    reason_code: str | None = None
    fault_seam: str | None = None

    if group == "positive_endpoint_cases":
        if memory_call_count != 1:
            raise RuntimeError("positive case did not perform exactly one mutation call")
        first_receipt_hash = _committed_receipt_hash(exercise_calls[0])
        if first_receipt_hash is None:
            raise RuntimeError("positive mutation did not return a committed receipt")
        outcome = "COMMITTED"
    elif group == "pre_admission_wire_cases" or (
        group == "rejection_cases" and spec.get("boundary") == "harness_pre_admission"
    ):
        if memory_call_count != 0:
            raise RuntimeError("pre-admission case called Memory")
        outcome = "REJECTED_PRE_ADMISSION"
        reason_code = harness_reason
    elif group == "rejection_cases":
        if memory_call_count != 1:
            raise RuntimeError("post-admission rejection call count differs")
        if exercise_calls[0]["status"] != "RAISED" or not new_rejections:
            raise RuntimeError("post-admission rejection lacks call and durable audit")
        outcome = "REJECTED"
        reason_code = str(exercise_calls[0]["exception_reason"])
    elif group == "transaction_fault_cases":
        fault_seam = str(spec["seam"])
        expected_fault_point = {
            "before-relation-memory-insert": "mutation.before_relation_memory_insert",
            "after-memory-before-edge": (
                "mutation.after_relation_memory_before_knowledge_row"
            ),
            "after-edge-before-commit": "mutation.after_knowledge_row_before_commit",
            "commit-before-ack": "mutation.after_commit",
        }[case_id]
        if exercise_observation["fault_events"] != [expected_fault_point]:
            raise RuntimeError("frozen transaction fault point was not reached")
        if case_id == "commit-before-ack":
            if (
                memory_call_count != 2
                or exercise_calls[0]["status"] != "RAISED"
                or exercise_calls[1]["status"] != "RETURNED"
            ):
                raise RuntimeError("commit-before-ack did not retry after lost ack")
            outcome = "COMMITTED_REPLAY"
            reason_code = "commit_before_ack"
            first_receipt_hash = new_receipts[-1] if new_receipts else None
            replay_receipt_hash = _committed_receipt_hash(exercise_calls[1])
            if first_receipt_hash is None or replay_receipt_hash != first_receipt_hash:
                raise RuntimeError("commit-before-ack replay receipt differs")
        else:
            if memory_call_count != 1 or exercise_calls[0]["status"] != "RAISED":
                raise RuntimeError("rollback fault did not interrupt one mutation call")
            outcome = "FAULT_ROLLED_BACK"
            reason_code = fault_seam
    elif group == "replay_cases":
        if memory_call_count != 1 or not setup_calls:
            raise RuntimeError("replay case call trace is incomplete")
        if case_id == "exact-replay":
            setup_result = _returned_result(setup_calls[-1])
            replay_result = _returned_result(exercise_calls[0])
            if setup_result is None or replay_result != setup_result:
                raise RuntimeError("exact replay public result differs")
            outcome = "REPLAYED"
            first_receipt_hash = _committed_receipt_hash(setup_calls[-1])
            replay_receipt_hash = _committed_receipt_hash(exercise_calls[0])
        else:
            outcome = "REJECTED"
            if exercise_calls[0]["status"] != "RAISED" or not new_rejections:
                raise RuntimeError("conflicting replay lacks call and durable audit")
            reason_code = str(exercise_calls[0]["exception_reason"])
            first_receipt_hash = _committed_receipt_hash(setup_calls[-1])
    elif group == "lifecycle_cases":
        if case_id in {"owner-expired", "endpoint-expired", "evidence-suppressed"}:
            if memory_call_count != 0:
                raise RuntimeError("non-mutating lifecycle case called mutation apply")
            outcome = (
                "EVIDENCE_SUPPRESSED"
                if case_id == "evidence-suppressed"
                else "TIME_ELAPSED"
            )
        else:
            if memory_call_count != 1:
                raise RuntimeError("mutating lifecycle call count differs")
            first_receipt_hash = _committed_receipt_hash(exercise_calls[0])
            if first_receipt_hash is None:
                raise RuntimeError("lifecycle mutation did not commit")
            outcome = "COMMITTED"
    elif group == "restart_corruption_cases":
        if memory_call_count != 0:
            raise RuntimeError("corruption case called mutation during corruption phase")
        outcome = "FAIL_CLOSED"
        reason_code = f"corruption_{case_id}"
    else:  # pragma: no cover - fixture self-check owns group exhaustiveness
        raise RuntimeError(f"unsupported integrity group: {group}")

    # The adapter supplies only bounded requests in a separate process. It never
    # shares interpreter state with this call trace and never supplies evidence.
    stored_receipts = set(after_db["receipt_hashes"])
    for receipt_hash in (first_receipt_hash, replay_receipt_hash):
        if receipt_hash is not None and receipt_hash not in stored_receipts:
            raise RuntimeError("public receipt is absent from durable receipt rows")

    before_roots = dict(before_db["roots"])
    after_roots = dict(after_db["roots"])
    before_roots["graph_projection_root"] = before_graph["summary"]["payload_sha256"]
    after_roots["graph_projection_root"] = after_graph["summary"]["payload_sha256"]
    after_edge_ids = _graph_edge_ids(after_graph)
    old_relation_ids = set(before_db["knowledge_relation_ids"])
    new_relation_ids = set(after_db["knowledge_relation_ids"]) - old_relation_ids
    old_edge_count = None
    new_edge_count = None
    if case_id == "relation-corrected":
        old_edge_count = len(old_relation_ids & after_edge_ids)
        new_edge_count = len(new_relation_ids & after_edge_ids)

    return {
        "schema_version": 1,
        "case_id": case_id,
        "execution_nonce": execution_nonce,
        "invocation_hash": invocation_hash,
        "candidate_identity": dict(candidate),
        "memory_call_count": memory_call_count,
        "outcome": outcome,
        "reason_code": reason_code,
        "before_roots": before_roots,
        "after_roots": after_roots,
        "before_row_cardinality": dict(before_db["row_cardinality"]),
        "after_row_cardinality": dict(after_db["row_cardinality"]),
        "raw_evidence_hash_before": before_db["raw_evidence_hash"],
        "raw_evidence_hash_after": after_db["raw_evidence_hash"],
        "graph_before": dict(before_graph["summary"]),
        "graph_after": dict(after_graph["summary"]),
        "reopen_result": {
            "status": reopen_graph["status"],
            "edge_count": reopen_graph["summary"]["edge_count"],
            "payload_sha256": reopen_graph["summary"]["payload_sha256"],
        },
        "first_receipt_hash": first_receipt_hash,
        "replay_receipt_hash": replay_receipt_hash,
        "physical_delete_count": max(
            0,
            int(before_db["row_cardinality"]["evidence_rows"])
            - int(after_db["row_cardinality"]["evidence_rows"]),
        ),
        "old_edge_count": old_edge_count,
        "new_edge_count": new_edge_count,
        "fault_seam": fault_seam,
    }


def _self_attestation_escape_errors(fixture: Mapping[str, Any]) -> list[str]:
    """Prove the three concrete producer-self-attestation escapes stay closed."""
    contract = fixture["execution_contract"]
    roots = {field: "a" * 64 for field in contract["required_root_fields"]}
    rows = {field: 0 for field in contract["required_row_fields"]}
    snapshot = {
        "roots": roots,
        "row_cardinality": rows,
        "raw_evidence_hash": "a" * 64,
        "receipt_hashes": ["b" * 64],
        "rejection_reasons": ["mutation_input_rejected"],
        "relation_owner_ids": [],
        "knowledge_relation_ids": [],
    }
    graph = {
        "status": "PASS",
        "graph": {"nodes": [], "edges": []},
        "summary": {
            "node_count": 0,
            "edge_count": 0,
            "relation_memory_node_count": 0,
            "payload_sha256": "c" * 64,
        },
    }
    adapter_result = {
        "case_id": "probe",
        "phase": "probe",
        "execution_nonce": "d" * 64,
        "invocation_hash": "e" * 64,
        "observation_kind": None,
        "exception_type": None,
        "exception_reason": None,
    }

    def trace(calls: list[dict[str, Any]], faults: list[str]) -> dict[str, Any]:
        return {
            "adapter_result": adapter_result,
            "calls": calls,
            "fault_events": faults,
        }

    committed_call = {
        "index": 0,
        "status": "RETURNED",
        "result_type": "MemoryMutationApplyResult",
        "result": {"outcome": "committed", "receipt_ref": {"receipt_hash": "b" * 64}},
        "exception_type": None,
        "exception_reason": None,
    }
    raised_call = {
        "index": 0,
        "status": "RAISED",
        "result_type": None,
        "result": None,
        "exception_type": "MemoryValidationError",
        "exception_reason": "ordinary rejection",
    }
    probes = (
        (
            "no-op exact replay",
            "exact-replay",
            "replay_cases",
            fixture["replay_cases"][0],
            trace([committed_call], []),
            trace([], []),
        ),
        (
            "single-call commit-before-ack",
            "commit-before-ack",
            "transaction_fault_cases",
            fixture["transaction_fault_cases"][3],
            trace([], []),
            trace([committed_call], []),
        ),
        (
            "ordinary rejection as rollback fault",
            "before-relation-memory-insert",
            "transaction_fault_cases",
            fixture["transaction_fault_cases"][0],
            trace([], []),
            trace([raised_call], []),
        ),
        (
            "two setup relations before replaying the last",
            "exact-replay",
            "replay_cases",
            fixture["replay_cases"][0],
            trace([committed_call, committed_call], []),
            trace([committed_call], []),
        ),
    )
    errors: list[str] = []
    for label, case_id, group, spec, setup, exercise in probes:
        try:
            _derive_artifact(
                case_id=case_id,
                group=group,
                spec=spec,
                candidate=fixture["candidate_identity"],
                execution_nonce="d" * 64,
                invocation_hash="e" * 64,
                before_db=snapshot,
                after_db=snapshot,
                before_graph=graph,
                after_graph=graph,
                reopen_graph=graph,
                setup_observation=setup,
                exercise_observation=exercise,
                harness_reason=None,
            )
        except RuntimeError:
            continue
        errors.append(f"self-attestation escape was accepted: {label}")
    return errors


def verify(fixture_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    fixture = _fixture(fixture_path)
    candidate, failure = _candidate_identity(fixture, args)
    if failure is not None:
        return failure
    assert candidate is not None
    if not args.case_entrypoint:
        return {"status": "NOT_RUN/BLOCKED", "reason": "integrity case adapter missing"}
    adapter = Path(args.case_entrypoint).resolve()
    if not adapter.is_file():
        return {"status": "NOT_RUN/BLOCKED", "reason": "integrity case adapter unavailable"}
    if _sha256(adapter) != candidate["integrity_adapter_sha256"]:
        return {"status": "FAIL", "reason": "integrity case adapter hash mismatch"}
    adapter_text = adapter.read_text(encoding="utf-8")
    forbidden = fixture["execution_contract"]["adapter_forbidden_source_tokens"]
    if any(token in adapter_text for token in forbidden):
        return {"status": "FAIL", "reason": "integrity case adapter crosses package boundary"}
    required_tokens = fixture["execution_contract"]["adapter_required_source_tokens"]
    if any(token not in adapter_text for token in required_tokens) or not (
        _adapter_uses_required_public_calls(adapter_text)
    ):
        return {"status": "FAIL", "reason": "integrity case adapter omits public SDK calls"}
    if not args.artifact_root:
        return {"status": "NOT_RUN/BLOCKED", "reason": "artifact root missing"}
    artifact_root = Path(args.artifact_root).resolve()
    if artifact_root.exists():
        return {"status": "FAIL", "reason": "artifact root must not pre-exist"}
    artifact_root.parent.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir()
    uv = shutil.which("uv")
    if uv is None:
        return {"status": "NOT_RUN/BLOCKED", "reason": "isolated installer unavailable: uv"}
    case_specs = _case_specs(fixture)
    contract = fixture["execution_contract"]
    fixture_hash = _sha256(fixture_path)
    wheel_paths = {
        "harness": Path(args.harness_wheel).resolve(),
        "memory": Path(args.memory_wheel).resolve(),
    }
    with tempfile.TemporaryDirectory(prefix="hm-relation-integrity-") as temp_value:
        temp_root = Path(temp_value)
        venv = temp_root / "venv"
        create = subprocess.run(
            [uv, "venv", "--python", sys.executable, str(venv)],
            cwd=temp_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if create.returncode != 0:
            return {"status": "NOT_RUN/BLOCKED", "reason": "isolated venv creation failed"}
        python = _python_in_venv(venv)
        install = subprocess.run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                str(wheel_paths["harness"]),
                str(wheel_paths["memory"]),
            ],
            cwd=temp_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if install.returncode != 0:
            return {"status": "NOT_RUN/BLOCKED", "reason": "candidate wheel install failed"}
        env = os.environ.copy()
        env["PYTHONPATH"] = ""
        env["PYTHONNOUSERSITE"] = "1"
        probe_code = (
            "import importlib.metadata as m,json,simple_harness,simple_harness_memory;"
            "print(json.dumps({'harness':{'version':m.version('simple-harness-sdk'),"
            "'module_origin':simple_harness.__file__},'memory':{'version':"
            "m.version('simple-harness-memory-sdk'),'module_origin':"
            "simple_harness_memory.__file__}},sort_keys=True))"
        )
        probe = subprocess.run(
            [str(python), "-c", probe_code],
            cwd=temp_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0:
            return {"status": "FAIL", "reason": "installed package identity probe failed"}
        try:
            installed_identity = json.loads(probe.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            return {"status": "FAIL", "reason": "installed identity probe emitted invalid JSON"}
        for key in ("harness", "memory"):
            installed = installed_identity.get(key, {})
            if installed.get("version") != candidate[key]["version"]:
                return {"status": "FAIL", "reason": f"installed version mismatch: {key}"}
            origin_value = installed.get("module_origin")
            if not isinstance(origin_value, str) or venv.resolve() not in Path(
                origin_value
            ).resolve().parents:
                return {"status": "FAIL", "reason": f"module origin differs: {key}"}

        for case_id, (group, spec) in case_specs.items():
            execution_nonce = secrets.token_hex(32)
            invocation_hash = _canonical_hash(
                {
                    "fixture_sha256": fixture_hash,
                    "case_id": case_id,
                    "execution_nonce": execution_nonce,
                    "candidate_identity": candidate,
                }
            )
            case_dir = artifact_root / case_id
            case_dir.mkdir()
            try:
                setup_observation = _run_adapter_phase(
                    python=python,
                    adapter=adapter,
                    fixture_path=fixture_path,
                    case_id=case_id,
                    phase="setup",
                    case_dir=case_dir,
                    execution_nonce=execution_nonce,
                    invocation_hash=invocation_hash,
                    env=env,
                    contract=contract,
                )
                db_path = case_dir / contract["case_db_filename"]
                before_db = _inspect_database(db_path)
                before_graph = _inspect_graph(
                    python,
                    db_path,
                    contract["principal_context"],
                    env=env,
                    relation_owner_ids=set(before_db["relation_owner_ids"]),
                )
                exercise_observation = _run_adapter_phase(
                    python=python,
                    adapter=adapter,
                    fixture_path=fixture_path,
                    case_id=case_id,
                    phase="exercise",
                    case_dir=case_dir,
                    execution_nonce=execution_nonce,
                    invocation_hash=invocation_hash,
                    env=env,
                    contract=contract,
                )
                harness_reason = None
                if group == "pre_admission_wire_cases" or (
                    group == "rejection_cases"
                    and spec.get("boundary") == "harness_pre_admission"
                ):
                    harness_reason = _inspect_harness_wire(
                        python,
                        case_dir / contract["case_wire_filename"],
                        env=env,
                    )
                if group == "restart_corruption_cases":
                    _corrupt_relation_database(db_path, case_id)
                after_db = _inspect_database(db_path)
                after_graph = _inspect_graph(
                    python,
                    db_path,
                    contract["principal_context"],
                    env=env,
                    relation_owner_ids=set(after_db["relation_owner_ids"]),
                )
                reopen_graph = _inspect_graph(
                    python,
                    db_path,
                    contract["principal_context"],
                    env=env,
                    relation_owner_ids=set(after_db["relation_owner_ids"]),
                )
                artifact_data = _derive_artifact(
                    case_id=case_id,
                    group=group,
                    spec=spec,
                    candidate=candidate,
                    execution_nonce=execution_nonce,
                    invocation_hash=invocation_hash,
                    before_db=before_db,
                    after_db=after_db,
                    before_graph=before_graph,
                    after_graph=after_graph,
                    reopen_graph=reopen_graph,
                    setup_observation=setup_observation,
                    exercise_observation=exercise_observation,
                    harness_reason=harness_reason,
                )
            except (
                FileNotFoundError,
                KeyError,
                RuntimeError,
                sqlite3.DatabaseError,
                json.JSONDecodeError,
            ) as exc:
                return {
                    "status": "FAIL",
                    "reason": f"verifier-owned observation failed: {case_id}",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }
            artifact = case_dir / "verifier-observation.json"
            artifact.write_text(
                json.dumps(artifact_data, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            common_error = _validate_common_artifact(
                artifact_data,
                case_id=case_id,
                execution_nonce=execution_nonce,
                invocation_hash=invocation_hash,
                candidate=candidate,
                contract=contract,
            )
            if common_error is not None:
                return {"status": "FAIL", "reason": f"{common_error}: {case_id}"}
            oracle_error = _validate_case_oracle(case_id, group, spec, artifact_data)
            if oracle_error is not None:
                return {"status": "FAIL", "reason": f"{oracle_error}: {case_id}"}
    return {
        "status": "PASS",
        "fixture_sha256": fixture_hash,
        "candidate_identity": candidate,
        "installed_identity": installed_identity,
        "verified_case_count": len(case_specs),
        "raw_evidence_retention": "PASS_BYTE_IDENTICAL_ALL_CASES",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        default=str(
            Path(__file__).parents[1]
            / "fixtures"
            / "semantic-relation-integrity-v1.json"
        ),
    )
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--case-entrypoint")
    parser.add_argument("--artifact-root")
    parser.add_argument("--harness-wheel")
    parser.add_argument("--harness-wheel-sha256")
    parser.add_argument("--harness-source-commit")
    parser.add_argument("--memory-wheel")
    parser.add_argument("--memory-wheel-sha256")
    parser.add_argument("--memory-source-commit")
    args = parser.parse_args()
    fixture_path = Path(args.fixture).resolve()
    if args.self_check:
        result = self_check(fixture_path)
    else:
        result = verify(fixture_path, args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] == "PASS":
        return 0
    if result["status"] == "NOT_RUN/BLOCKED":
        return BLOCKED_EXIT
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

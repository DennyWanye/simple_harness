#!/usr/bin/env python3
"""Request-only public-package adapter for semantic relation integrity cases."""

from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

import simple_harness
import simple_harness_memory

CASE_GROUPS = (
    "positive_endpoint_cases",
    "rejection_cases",
    "pre_admission_wire_cases",
    "transaction_fault_cases",
    "replay_cases",
    "lifecycle_cases",
    "restart_corruption_cases",
)
HARNESS_REJECTIONS = {
    "missing-dependency",
    "forward-dependency",
    "non-create-producer",
    "duplicate-operation-id",
    "unknown-relation-kind",
    "illegal-source-type",
    "illegal-target-type",
    "self-loop",
    "relation-as-endpoint",
    "missing-evidence",
    "malformed-v5-wire",
}
POST_ADMISSION_REJECTIONS = {
    "cross-principal",
    "missing-endpoint",
    "stale-endpoint-revision",
    "contested-endpoint",
    "suppressed-endpoint",
    "classification-insufficient",
}
NON_MUTATING_EXERCISE = {
    "owner-expired",
    "endpoint-expired",
    "evidence-suppressed",
    "owner-mismatch",
    "domain-mismatch",
    "relation-hash-mismatch",
    "endpoint-foreign-key-mismatch",
}
CONTEST_CASES = {"contested-endpoint", "owner-contested", "endpoint-contested"}
CORRECTION_CASES = {
    "suppressed-endpoint",
    "owner-suppressed",
    "endpoint-suppressed",
    "owner-superseded",
    "endpoint-superseded",
    "relation-corrected",
    "classification-restricted",
    "classification-insufficient",
    "stale-endpoint-revision",
}
TRANSITION_EVIDENCE_CASES = CONTEST_CASES | CORRECTION_CASES
PRINCIPAL = {
    "deployment_id": "deployment-relation-1",
    "household_id": "household-relation-1",
    "actor_id": "principal-relation-1",
    "session_id": "session-relation-1",
}
SCOPE = {"kind": "personal", "owner_id": "principal-relation-1"}
FOREIGN_PRINCIPAL = {
    "deployment_id": "deployment-relation-1",
    "household_id": "household-relation-1",
    "actor_id": "principal-relation-foreign",
    "session_id": "session-relation-foreign",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _domain_hash(domain: str, payload: dict[str, Any]) -> str:
    return _sha256(_canonical_bytes({"domain": domain, "payload": payload}))


def _rehash_plan_wire(wire: dict[str, Any]) -> dict[str, Any]:
    """Rebind public derived hashes after intentionally invalid semantic edits."""
    intent_operations = []
    for operation in wire["operations"]:
        operation["depends_on_operation_ids"] = sorted(
            operation["depends_on_operation_ids"]
        )
        intent = {
            key: copy.deepcopy(value)
            for key, value in operation.items()
            if key not in {"operation_intent_hash", "action_authority_ref"}
        }
        operation_hash = _domain_hash(
            "simple-harness/memory-mutation-operation-intent/v4", intent
        )
        operation["operation_intent_hash"] = operation_hash
        intent_operations.append({**intent, "operation_intent_hash": operation_hash})
    plan_intent = {
        "schema_version": wire["schema_version"],
        "plan_id": wire["plan_id"],
        "run_id": wire["run_id"],
        "turn_id": wire["turn_id"],
        "subject": wire["subject"],
        "base_revision": wire["base_revision"],
        "outcome": wire["outcome"],
        "operations": intent_operations,
        "disclosure_context": copy.deepcopy(wire["disclosure_context"]),
        "evidence_refs": copy.deepcopy(wire["evidence_refs"]),
        "idempotency_key": wire["idempotency_key"],
        "apply_mode": wire["apply_mode"],
    }
    wire["plan_intent_hash"] = _domain_hash(
        "simple-harness/memory-mutation-plan-intent/v5", plan_intent
    )
    return wire


def _disclosure(run_id: str, principal: dict[str, str] = PRINCIPAL) -> Any:
    return simple_harness.DisclosureContext.from_json(
        {
            "schema_version": 1,
            "run_id": run_id,
            "subject": principal["actor_id"],
            "recipient": "user_self",
            "recipient_id": principal["actor_id"],
            "intended_audience": "user_self",
            "purpose": "personalization",
            "source": "authenticated_host",
            "trust": "trusted_authority",
            "generation": "current",
            "authority_ref": "host-disclosure-relation-1",
            "reason_codes": ["disclosure_minimum_necessary"],
        }
    )


def _evidence(
    case_id: str,
    principal: dict[str, str] = PRINCIPAL,
    *,
    correction: bool = False,
) -> dict[str, Any]:
    evidence_case_id = case_id.replace("foreign-key", "foreign-reference")
    run_id = f"relation-run-{evidence_case_id}"
    evidence_id = f"evidence-{evidence_case_id}"
    text = f"Release workflow for {evidence_case_id} is evidence-first"
    sanitized_payload = {"text": text}
    sanitized_hash = _sha256(_canonical_bytes(sanitized_payload))
    disclosure = _disclosure(run_id, principal)
    envelope = simple_harness.SanitizedEvidenceEnvelope(
        evidence_id=evidence_id,
        run_id=run_id,
        subject=principal["actor_id"],
        source_kind=simple_harness.EvidenceSourceKind.USER_MESSAGE,
        source_ref=f"turn-{evidence_case_id}",
        source_hash=sanitized_hash,
        sanitized_payload=sanitized_payload,
        sanitized_hash=sanitized_hash,
        filter_policy_version="credential-filter/v1",
        removed_spans=(),
        disclosure_context=disclosure,
        evidence_refs=(),
    )
    receipt = simple_harness.SanitizedEvidenceReceipt(
        receipt_id=f"receipt-{evidence_case_id}",
        run_id=run_id,
        subject=principal["actor_id"],
        evidence_id=evidence_id,
        envelope_hash=envelope.envelope_hash,
        source_hash=sanitized_hash,
        sanitized_hash=sanitized_hash,
        filter_policy_version="credential-filter/v1",
        accepted=True,
        reason_codes=(simple_harness.EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
        disclosure_context=disclosure,
        evidence_refs=(),
        admitted_at=1.0,
    )
    item_id = f"{evidence_id}:item:1"
    normalization = "sanitized-string-identity-utf8/v1"
    span = simple_harness.EvidenceSpanRef(
        span_id=f"span-{evidence_case_id}",
        evidence_id=evidence_id,
        envelope_hash=envelope.envelope_hash,
        sanitized_hash=sanitized_hash,
        admission_receipt_id=receipt.receipt_id,
        admission_receipt_hash=receipt.receipt_hash,
        source_kind=simple_harness.EvidenceSourceKind.USER_MESSAGE,
        item_ordinal=1,
        item_id=item_id,
        item_json_pointer="/text",
        start_byte=0,
        end_byte=len(text.encode("utf-8")),
        exact_quote=text,
        quote_hash=_sha256(text.encode("utf-8")),
        source_hash=sanitized_hash,
        normalization_version=normalization,
        actor_role=simple_harness.EvidenceActorRole.USER,
        provenance=simple_harness.EvidenceProvenance.AUTHENTICATED_USER,
        support_kind=(
            simple_harness.EvidenceSupportKind.EXPLICIT_USER_CORRECTION
            if correction
            else simple_harness.EvidenceSupportKind.EXPLICIT_USER_ASSERTION
        ),
        typed_observation=None,
    )
    item = simple_harness.EvidenceItemAuthority(
        schema_version=3,
        authority_id=f"item-authority-{evidence_case_id}",
        evidence_id=evidence_id,
        envelope_hash=envelope.envelope_hash,
        sanitized_hash=sanitized_hash,
        source_hash=sanitized_hash,
        source_kind=simple_harness.EvidenceSourceKind.USER_MESSAGE,
        item_ordinal=1,
        item_id=item_id,
        item_json_pointer="/text",
        normalization_version=normalization,
        actor_role=simple_harness.EvidenceActorRole.USER,
        provenance=simple_harness.EvidenceProvenance.AUTHENTICATED_USER,
        required_privacy_class=simple_harness.PrivacyClass.PERSONAL,
        required_information_attributes=(
            simple_harness.InformationAttribute.PREFERENCE,
            simple_harness.InformationAttribute.WORK,
        ),
        classification_authority_ref=f"classification-{evidence_case_id}",
        issuer_ref="host-evidence-authority-1",
    )
    return {
        "run_id": run_id,
        "envelope": envelope,
        "receipt": receipt,
        "span": span,
        "item": item,
    }


def _lifecycle(memory_type: str) -> Any:
    return {
        "semantic": simple_harness.SemanticLifecycleState.ACTIVE,
        "procedure": simple_harness.ProcedureLifecycleState.ACTIVE,
        "prospective": simple_harness.ProspectiveLifecycleState.PENDING,
    }[memory_type]


def _create_operation(
    operation_id: str,
    memory_type: str,
    payload: Any,
    span: Any,
    *,
    dependencies: tuple[str, ...] = (),
    valid_until: float | None = None,
) -> Any:
    return simple_harness.MemoryMutationOperation(
        operation_id=operation_id,
        kind=simple_harness.MemoryMutationKind.CREATE,
        memory_type=simple_harness.LongTermMemoryType(memory_type),
        payload=payload,
        target=None,
        depends_on_operation_ids=dependencies,
        lifecycle_state=_lifecycle(memory_type),
        epistemic_status=simple_harness.EpistemicStatus.EXPLICIT_USER,
        conflict_status=simple_harness.ConflictStatus.UNCONTESTED,
        verification_state=simple_harness.VerificationState.SOURCE_BOUND,
        valid_time_interval=simple_harness.ValidTimeInterval(1.0, valid_until),
        proposed_privacy_class=simple_harness.PrivacyClass.PERSONAL,
        proposed_information_attributes=(
            simple_harness.InformationAttribute.PREFERENCE,
            simple_harness.InformationAttribute.WORK,
        ),
        evidence_spans=(span,),
        reason_code="explicit_user_relation",
    )


def _plan(
    case_id: str,
    evidence: dict[str, Any],
    operations: tuple[Any, ...],
    *,
    suffix: str,
    idempotency_key: str | None = None,
    principal: dict[str, str] = PRINCIPAL,
) -> dict[str, Any]:
    envelope = evidence["envelope"]
    value = simple_harness.MemoryMutationPlan(
        plan_id=f"plan-{case_id}-{suffix}",
        run_id=evidence["run_id"],
        turn_id=f"turn-{case_id}",
        subject=principal["actor_id"],
        base_revision=1,
        outcome=simple_harness.MemoryMutationPlanOutcome.MUTATE,
        operations=operations,
        disclosure_context=_disclosure(evidence["run_id"], principal),
        evidence_refs=(
            simple_harness.EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),
        ),
        idempotency_key=(idempotency_key or f"relation-idempotency-{case_id}-{suffix}"),
    )
    # Reconstruct only from public attributes. This catches constructor drift while
    # keeping the emitted wire independent of private modules or source checkout.
    parameters = inspect.signature(simple_harness.MemoryMutationPlan).parameters
    round_trip = simple_harness.MemoryMutationPlan(
        **{name: getattr(value, name) for name in parameters}
    )
    return round_trip.to_json()


def _created_plan(
    case_id: str,
    evidence: dict[str, Any],
    *,
    suffix: str,
    object_value: str = "evidence-first",
    include_relation: bool = True,
    expiring_role: str | None = None,
    idempotency_key: str | None = None,
    principal: dict[str, str] = PRINCIPAL,
) -> dict[str, Any]:
    span = evidence["span"]
    source = _create_operation(
        "create-preference",
        "semantic",
        simple_harness.SemanticMemoryPayload(
            "user", "prefers_release_workflow", object_value, ()
        ),
        span,
        valid_until=1.001 if expiring_role == "endpoint" else None,
    )
    target = _create_operation(
        "create-procedure",
        "procedure",
        simple_harness.ProcedureMemoryPayload(
            "release-workflow",
            ("release",),
            ("verify evidence", "publish"),
            simple_harness.ProcedureRiskLevel.LOW,
        ),
        span,
    )
    operations = [source, target]
    if include_relation:
        operations.append(
            _create_operation(
                "create-relation",
                "semantic",
                simple_harness.SemanticRelationMemoryPayload(
                    simple_harness.SemanticRelationKind.APPLIES_TO,
                    simple_harness.CreatedByOperationTarget("create-preference"),
                    simple_harness.CreatedByOperationTarget("create-procedure"),
                ),
                span,
                dependencies=("create-preference", "create-procedure"),
                valid_until=1.001 if expiring_role == "owner" else None,
            )
        )
    wire = _plan(
        case_id,
        evidence,
        tuple(operations),
        suffix=suffix,
        idempotency_key=idempotency_key,
        principal=principal,
    )
    if expiring_role is not None:
        expiring_operation = (
            "create-relation" if expiring_role == "owner" else "create-preference"
        )
        for operation in wire["operations"]:
            if operation["operation_id"] == expiring_operation:
                operation["valid_time_interval"]["valid_until"] = (
                    "$verifier_now_plus:1.5"
                )
                break
    return wire


def _bind_template_refs(
    wire: dict[str, Any], bindings: dict[str, tuple[str, str]]
) -> dict[str, Any]:
    """Replace constructor-safe sentinels with verifier-owned exact-ref tokens."""
    wire["base_revision"] = "$apply_head_revision"
    for operation in wire["operations"]:
        target_binding = bindings.get(operation["operation_id"])
        if target_binding is not None:
            operation["target"]["memory_id"] = target_binding[0]
            operation["target"]["revision"] = target_binding[1]
        payload = operation.get("payload") or {}
        for endpoint_name in ("source_endpoint", "target_endpoint"):
            endpoint = payload.get(endpoint_name)
            if endpoint and endpoint.get("target_kind") == "existing_memory":
                role = (
                    "create-preference"
                    if endpoint_name == "source_endpoint"
                    else "create-procedure"
                )
                endpoint["memory_id"] = f"$setup_memory:{role}"
                endpoint["revision"] = f"$setup_revision:{role}"
    return wire


def _existing_relation_plan(case_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
    operation = _create_operation(
        "create-relation-existing",
        "semantic",
        simple_harness.SemanticRelationMemoryPayload(
            simple_harness.SemanticRelationKind.APPLIES_TO,
            simple_harness.ExistingMemoryTarget(f"source-{case_id}", 1),
            simple_harness.ExistingMemoryTarget(f"target-{case_id}", 1),
        ),
        evidence["span"],
    )
    wire = _bind_template_refs(
        _plan(case_id, evidence, (operation,), suffix="existing"), {}
    )
    source = wire["operations"][0]["payload"]["source_endpoint"]
    if case_id == "missing-endpoint":
        source["memory_id"] = "missing-relation-endpoint"
        source["revision"] = 1
    elif case_id in {
        "contested-endpoint",
        "suppressed-endpoint",
        "classification-insufficient",
    }:
        source["revision"] = "$current_revision:create-preference"
    return wire


def _transition_plan(
    case_id: str, evidence: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    owner_cases = {
        "owner-suppressed",
        "owner-contested",
        "owner-superseded",
        "classification-restricted",
        "relation-corrected",
    }
    target_role = "create-relation" if case_id in owner_cases else "create-preference"
    action = {
        "owner-contested": "contest",
        "endpoint-contested": "contest",
        "contested-endpoint": "contest",
        "owner-suppressed": "suppress",
        "endpoint-suppressed": "suppress",
        "suppressed-endpoint": "suppress",
        "owner-superseded": "supersede",
        "endpoint-superseded": "supersede",
        "classification-restricted": "revise",
        "classification-insufficient": "revise",
        "stale-endpoint-revision": "revise",
        "relation-corrected": "revise",
    }[case_id]
    is_relation = target_role == "create-relation"
    changes_relation_target = case_id in {
        "owner-contested",
        "owner-superseded",
        "relation-corrected",
    }
    changed_target_operation = f"create-{case_id}-target"
    if action == "suppress":
        payload = None
    elif is_relation:
        payload = simple_harness.SemanticRelationMemoryPayload(
            simple_harness.SemanticRelationKind.APPLIES_TO,
            simple_harness.ExistingMemoryTarget("template-source", 1),
            (
                simple_harness.CreatedByOperationTarget(changed_target_operation)
                if changes_relation_target
                else simple_harness.ExistingMemoryTarget("template-target", 1)
            ),
        )
    else:
        payload = simple_harness.SemanticMemoryPayload(
            "user", "prefers_release_workflow", "corrected-evidence-first", ()
        )
    operation_id = f"{action}-{target_role}"
    transition = simple_harness.MemoryMutationOperation(
        operation_id=operation_id,
        kind=simple_harness.MemoryMutationKind(action),
        memory_type=simple_harness.LongTermMemoryType.SEMANTIC,
        payload=payload,
        target=simple_harness.ExistingMemoryTarget("template-target", 1),
        depends_on_operation_ids=(
            (changed_target_operation,) if changes_relation_target else ()
        ),
        lifecycle_state=(
            simple_harness.SemanticLifecycleState.FORGOTTEN
            if action == "suppress"
            else (
                simple_harness.SemanticLifecycleState.SUPERSEDED
                if action == "supersede"
                else simple_harness.SemanticLifecycleState.ACTIVE
            )
        ),
        epistemic_status=simple_harness.EpistemicStatus.EXPLICIT_USER,
        conflict_status=(
            simple_harness.ConflictStatus.CONTESTED
            if action == "contest"
            else simple_harness.ConflictStatus.UNCONTESTED
        ),
        verification_state=simple_harness.VerificationState.SOURCE_BOUND,
        valid_time_interval=simple_harness.ValidTimeInterval(1.0, None),
        proposed_privacy_class=(
            simple_harness.PrivacyClass.RESTRICTED
            if case_id in {"classification-restricted", "classification-insufficient"}
            else simple_harness.PrivacyClass.PERSONAL
        ),
        proposed_information_attributes=(
            simple_harness.InformationAttribute.PREFERENCE,
            simple_harness.InformationAttribute.WORK,
        ),
        evidence_spans=(evidence["span"],),
        reason_code=f"relation_lifecycle_{case_id}",
        action_authority_ref=None,
    )
    operations: tuple[Any, ...]
    if changes_relation_target:
        corrected_target = _create_operation(
            changed_target_operation,
            "prospective",
            simple_harness.ProspectiveMemoryPayload(
                "publish after verification",
                simple_harness.ProspectiveTimeTrigger(
                    trigger_at=4102444800.0, timezone="UTC"
                ),
            ),
            evidence["span"],
        )
        operations = (corrected_target, transition)
    else:
        operations = (transition,)
    wire = _plan(case_id, evidence, operations, suffix="transition")
    wire = _bind_template_refs(
        wire,
        {
            operation_id: (
                f"$setup_memory:{target_role}",
                f"$setup_revision:{target_role}",
            )
        },
    )
    authorities = []
    if action in {"revise", "supersede", "suppress"}:
        authorities.append(
            {
                "template_kind": "verifier_memory_action_authority_v1",
                "authority_id": f"authority-{case_id}",
                "operation_id": operation_id,
                "target_operation_id": target_role,
                "issuer_ref": "host-memory-action-authority-1",
                "issued_at": 1.0,
                "expires_at": 4102444800.0,
                "nonce": f"authority-nonce-{case_id}",
            }
        )
    return wire, authorities


def _ingest_command(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "ingest_committed_evidence",
        "envelope": evidence["envelope"].to_json(),
        "receipt": evidence["receipt"].to_json(),
    }


def _apply_command(
    plan: dict[str, Any],
    evidence: dict[str, Any],
    *,
    action_authorities: list[dict[str, Any]] | None = None,
    principal: dict[str, str] = PRINCIPAL,
) -> dict[str, Any]:
    return {
        "kind": "apply_memory_" + "mutation_plan",
        "principal": dict(principal),
        "scope": {"kind": "personal", "owner_id": principal["actor_id"]},
        "plan": plan,
        "admitted_evidence": [
            {
                "envelope": evidence["envelope"].to_json(),
                "receipt": evidence["receipt"].to_json(),
                "item_authority": evidence["item"].to_json(),
            }
        ],
        "memory_action_authorities": action_authorities or [],
    }


def _suppression_command(case_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
    values = {
        "request_id": f"suppress-{case_id}",
        "subject": PRINCIPAL["actor_id"],
        "scope_kind": "evidence",
        "scope_ref": evidence["envelope"].evidence_id,
        "reason_code": "user_requested_suppression",
        "requested_at": 2.0,
        "purpose": None,
        "schema_version": 1,
    }
    simple_harness_memory.SuppressionRequest(
        request_id=values["request_id"],
        subject=values["subject"],
        scope_kind=simple_harness_memory.SuppressionScopeKind(values["scope_kind"]),
        scope_ref=values["scope_ref"],
        reason_code=values["reason_code"],
        requested_at=values["requested_at"],
        purpose=None,
        schema_version=values["schema_version"],
    )
    return {"kind": "suppress", "principal": dict(PRINCIPAL), "request": values}


def _invalid_wire(case_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
    wire = _created_plan(case_id, evidence, suffix="invalid")
    operations = wire["operations"]
    relation = operations[-1]
    if case_id == "missing-dependency":
        relation["depends_on_operation_ids"] = ["create-preference"]
    elif case_id == "forward-dependency":
        operations[:] = [relation, *operations[:-1]]
    elif case_id == "non-create-producer":
        operations[0]["kind"] = "revise"
        operations[0]["target"] = {
            "target_kind": "existing_memory",
            "memory_id": "x",
            "revision": 1,
        }
    elif case_id == "duplicate-operation-id":
        operations[1]["operation_id"] = operations[0]["operation_id"]
    elif case_id == "unknown-relation-kind":
        relation["payload"]["relation_kind"] = "depends_on"
    elif case_id == "illegal-source-type":
        operations[0]["memory_type"] = "episode"
        operations[0]["payload"] = {
            "memory_type": "episode",
            "title": "release episode",
            "participants": ["user"],
            "goals": ["release"],
            "actions": ["verify evidence"],
            "results": [],
            "impacts": [],
            "occurred_start": 1.0,
            "occurred_end": None,
            "thread_ref": None,
        }
    elif case_id == "illegal-target-type":
        operations[1]["memory_type"] = "semantic"
        operations[1]["payload"] = copy.deepcopy(operations[0]["payload"])
    elif case_id == "self-loop":
        relation["payload"]["target_endpoint"] = copy.deepcopy(
            relation["payload"]["source_endpoint"]
        )
    elif case_id == "relation-as-endpoint":
        nested_relation = copy.deepcopy(relation)
        nested_relation["operation_id"] = "create-nested-relation"
        nested_relation["depends_on_operation_ids"] = [
            "create-relation",
            "create-procedure",
        ]
        nested_relation["payload"]["source_endpoint"] = {
            "target_kind": "created_by_operation",
            "operation_id": "create-relation",
        }
        operations.append(nested_relation)
    elif case_id == "missing-evidence":
        wire["evidence_refs"] = []
        for operation in operations:
            operation["evidence_spans"] = []
    elif case_id == "malformed-v5-wire":
        wire["schema_version"] = 4
    else:
        raise ValueError(f"unsupported invalid-wire case: {case_id}")
    return wire if case_id == "malformed-v5-wire" else _rehash_plan_wire(wire)


def _case_ids(fixture: dict[str, Any]) -> set[str]:
    return {item["id"] for group in CASE_GROUPS for item in fixture[group]}


def build_request(
    fixture: dict[str, Any],
    case_id: str,
    phase: str,
    execution_nonce: str,
    invocation_hash: str,
) -> dict[str, Any]:
    if case_id not in _case_ids(fixture):
        raise ValueError("unknown frozen case id")
    if phase not in {"setup", "exercise"}:
        raise ValueError("phase must be setup or exercise")
    evidence = _evidence(case_id)
    commands: list[dict[str, Any]] = []
    invalid_wire = None

    replay = case_id in {"exact-replay", "conflicting-replay"}
    lifecycle = case_id in {item["id"] for item in fixture["lifecycle_cases"]}
    corruption = case_id in {item["id"] for item in fixture["restart_corruption_cases"]}

    if phase == "setup":
        commands.append(_ingest_command(evidence))
        transition_evidence = evidence
        if case_id in TRANSITION_EVIDENCE_CASES:
            transition_evidence = _evidence(
                f"{case_id}-transition",
                correction=case_id in CORRECTION_CASES,
            )
            commands.append(_ingest_command(transition_evidence))
        if case_id == "cross-principal":
            foreign_evidence = _evidence(f"{case_id}-foreign", FOREIGN_PRINCIPAL)
            commands.append(_ingest_command(foreign_evidence))
            commands.append(
                _apply_command(
                    _created_plan(
                        case_id,
                        foreign_evidence,
                        suffix="foreign-baseline",
                        principal=FOREIGN_PRINCIPAL,
                    ),
                    foreign_evidence,
                    principal=FOREIGN_PRINCIPAL,
                )
            )
        elif replay or lifecycle or corruption or case_id in POST_ADMISSION_REJECTIONS:
            commands.append(
                _apply_command(
                    _created_plan(
                        case_id,
                        evidence,
                        suffix="replay" if replay else "baseline",
                        expiring_role=(
                            "owner"
                            if case_id == "owner-expired"
                            else "endpoint"
                            if case_id == "endpoint-expired"
                            else None
                        ),
                        idempotency_key=(
                            f"relation-idempotency-{case_id}-replay" if replay else None
                        ),
                    ),
                    evidence,
                )
            )
            if case_id in {
                "contested-endpoint",
                "suppressed-endpoint",
                "classification-insufficient",
                "stale-endpoint-revision",
            }:
                transition, authorities = _transition_plan(
                    case_id, transition_evidence
                )
                commands.append(
                    _apply_command(
                        transition,
                        transition_evidence,
                        action_authorities=authorities,
                    )
                )
        elif case_id == "existing-endpoints":
            commands.append(
                _apply_command(
                    _created_plan(
                        case_id, evidence, suffix="endpoints", include_relation=False
                    ),
                    evidence,
                )
            )
    elif case_id in HARNESS_REJECTIONS:
        invalid_wire = _invalid_wire(case_id, evidence)
    elif case_id == "exact-replay":
        commands.append(
            _apply_command(
                _created_plan(
                    case_id,
                    evidence,
                    suffix="replay",
                    idempotency_key=f"relation-idempotency-{case_id}-replay",
                ),
                evidence,
            )
        )
    elif case_id == "conflicting-replay":
        commands.append(
            _apply_command(
                _created_plan(
                    case_id,
                    evidence,
                    suffix="replay",
                    object_value="conflicting-value",
                    idempotency_key=f"relation-idempotency-{case_id}-replay",
                ),
                evidence,
            )
        )
    elif case_id == "commit-before-ack":
        command = _apply_command(
            _created_plan(case_id, evidence, suffix="fault"), evidence
        )
        commands.extend((command, copy.deepcopy(command)))
    elif case_id in {"owner-expired", "endpoint-expired"}:
        commands.append({"kind": "sleep", "seconds": 1.6})
    elif case_id == "evidence-suppressed":
        commands.append(_suppression_command(case_id, evidence))
    elif corruption:
        pass
    elif lifecycle:
        transition_evidence = (
            _evidence(
                f"{case_id}-transition",
                correction=case_id in CORRECTION_CASES,
            )
            if case_id in TRANSITION_EVIDENCE_CASES
            else evidence
        )
        transition, authorities = _transition_plan(case_id, transition_evidence)
        commands.append(
            _apply_command(
                transition,
                transition_evidence,
                action_authorities=authorities,
            )
        )
    elif case_id == "existing-endpoints" or case_id in POST_ADMISSION_REJECTIONS:
        commands.append(
            _apply_command(_existing_relation_plan(case_id, evidence), evidence)
        )
    else:
        commands.append(
            _apply_command(
                _created_plan(case_id, evidence, suffix="exercise"), evidence
            )
        )

    return {
        "case_id": case_id,
        "phase": phase,
        "execution_nonce": execution_nonce,
        "invocation_hash": invocation_hash,
        "commands": commands,
        "invalid_wire": invalid_wire,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--phase", choices=("setup", "exercise"), required=True)
    parser.add_argument("--db-dir", type=Path, required=True)
    parser.add_argument("--execution-nonce", required=True)
    parser.add_argument("--invocation-hash", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    if args.db_dir.exists() and any(args.db_dir.iterdir()):
        raise ValueError("request-only directory must be empty")
    result = build_request(
        fixture,
        args.case_id,
        args.phase,
        args.execution_nonce,
        args.invocation_hash,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

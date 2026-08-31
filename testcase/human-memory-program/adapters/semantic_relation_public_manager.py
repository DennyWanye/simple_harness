#!/usr/bin/env python3
"""Exact-wheel public-package adapter for the semantic relation value oracle."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import simple_harness as harness

import simple_harness_memory as memory


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_graph_hash(view: Any) -> str:
    payload = {
        "nodes": [item.to_json() for item in view.nodes],
        "edges": [item.to_json() for item in view.edges],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _disclosure(subject: str) -> Any:
    return harness.DisclosureContext(
        run_id="relation-run-1",
        subject=subject,
        recipient=harness.DeliveryRecipient.USER_SELF,
        recipient_id=subject,
        intended_audience=harness.IntendedAudience.USER_SELF,
        purpose=harness.DisclosurePurpose.PERSONALIZATION,
        source=harness.DisclosureSource.AUTHENTICATED_HOST,
        trust=harness.DisclosureTrust.TRUSTED_AUTHORITY,
        generation=harness.DisclosureGeneration.CURRENT,
        authority_ref="host-disclosure-relation-1",
        reason_codes=(harness.DisclosureReasonCode.MINIMUM_NECESSARY,),
    )


def _evidence(subject: str) -> tuple[Any, Any, Any]:
    payload = {
        "item_id": "message-relation-1",
        "public_text": "Use the evidence-first release workflow when publishing.",
    }
    envelope = harness.SanitizedEvidenceEnvelope(
        evidence_id="evidence-relation-1",
        run_id="relation-run-1",
        subject=subject,
        source_kind=harness.EvidenceSourceKind.USER_MESSAGE,
        source_ref="relation-turn-1/user",
        source_hash="a" * 64,
        sanitized_payload=payload,
        sanitized_hash=harness.fingerprint_json(payload),
        filter_policy_version="credential-filter/v1",
        removed_spans=(),
        disclosure_context=_disclosure(subject),
        evidence_refs=(),
    )
    receipt = harness.SanitizedEvidenceReceipt(
        receipt_id="admission-relation-1",
        run_id=envelope.run_id,
        subject=envelope.subject,
        evidence_id=envelope.evidence_id,
        envelope_hash=envelope.envelope_hash,
        source_hash=envelope.source_hash,
        sanitized_hash=envelope.sanitized_hash,
        filter_policy_version=envelope.filter_policy_version,
        accepted=True,
        reason_codes=(harness.EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
        disclosure_context=envelope.disclosure_context,
        evidence_refs=envelope.evidence_refs,
        admitted_at=1.0,
    )
    text = payload["public_text"]
    span = harness.EvidenceSpanRef(
        span_id="span-relation-1",
        evidence_id=envelope.evidence_id,
        envelope_hash=envelope.envelope_hash,
        sanitized_hash=envelope.sanitized_hash,
        admission_receipt_id=receipt.receipt_id,
        admission_receipt_hash=receipt.receipt_hash,
        source_kind=envelope.source_kind,
        item_ordinal=1,
        item_id=payload["item_id"],
        item_json_pointer="/public_text",
        start_byte=0,
        end_byte=len(text.encode("utf-8")),
        exact_quote=text,
        quote_hash=_sha(text),
        source_hash=envelope.source_hash,
        normalization_version=harness.EVIDENCE_NORMALIZATION_IDENTITY_UTF8_V1,
        actor_role=harness.EvidenceActorRole.USER,
        provenance=harness.EvidenceProvenance.AUTHENTICATED_USER,
        support_kind=harness.EvidenceSupportKind.EXPLICIT_USER_ASSERTION,
        typed_observation=None,
    )
    return envelope, receipt, span


def _item_authority(span: Any) -> Any:
    return harness.EvidenceItemAuthority(
        schema_version=harness.EVIDENCE_ITEM_AUTHORITY_SCHEMA_VERSION,
        authority_id="item-authority-relation-1",
        evidence_id=span.evidence_id,
        envelope_hash=span.envelope_hash,
        sanitized_hash=span.sanitized_hash,
        source_hash=span.source_hash,
        source_kind=span.source_kind,
        item_ordinal=span.item_ordinal,
        item_id=span.item_id,
        item_json_pointer=span.item_json_pointer,
        normalization_version=span.normalization_version,
        actor_role=span.actor_role,
        provenance=span.provenance,
        required_privacy_class=harness.PrivacyClass.PERSONAL,
        required_information_attributes=(),
        classification_authority_ref="host-classification-relation-1",
        issuer_ref="host-evidence-relation-1",
    )


class _Authority:
    def __init__(self, admitted: Any) -> None:
        self.admitted = admitted
        self.actions: dict[str, Any] = {}

    async def resolve_admitted_evidence(self, span: Any) -> Any:
        if span.evidence_id != self.admitted.envelope.evidence_id:
            raise ValueError("unknown evidence")
        return self.admitted

    async def resolve_typed_observation(self, reference: Any) -> Any:
        del reference
        raise ValueError("no typed observation is registered")

    async def resolve_memory_action_authority(self, reference: Any) -> Any:
        return self.actions[reference.authority_id]


def _shared_operation(span: Any, **overrides: Any) -> Any:
    values = {
        "target": None,
        "depends_on_operation_ids": (),
        "epistemic_status": harness.EpistemicStatus.EXPLICIT_USER,
        "conflict_status": harness.ConflictStatus.UNCONTESTED,
        "verification_state": harness.VerificationState.SOURCE_BOUND,
        "valid_time_interval": harness.ValidTimeInterval(1.0, None),
        "proposed_privacy_class": harness.PrivacyClass.PERSONAL,
        "evidence_spans": (span,),
        "reason_code": "explicit_user_relation",
    }
    values.update(overrides)
    return harness.MemoryMutationOperation(**values)


def _create_plan(subject: str, envelope: Any, span: Any) -> tuple[Any, Any]:
    source = _shared_operation(
        span,
        operation_id="create-preference",
        kind=harness.MemoryMutationKind.CREATE,
        memory_type=harness.LongTermMemoryType.SEMANTIC,
        payload=harness.SemanticMemoryPayload(
            "user", "prefers_release_workflow", "evidence-first", ()
        ),
        lifecycle_state=harness.SemanticLifecycleState.ACTIVE,
        proposed_information_attributes=(harness.InformationAttribute.PREFERENCE,),
    )
    target = _shared_operation(
        span,
        operation_id="create-procedure",
        kind=harness.MemoryMutationKind.CREATE,
        memory_type=harness.LongTermMemoryType.PROCEDURE,
        payload=harness.ProcedureMemoryPayload(
            "release-workflow",
            ("release",),
            ("verify evidence", "publish"),
            harness.ProcedureRiskLevel.LOW,
        ),
        lifecycle_state=harness.ProcedureLifecycleState.ACTIVE,
        proposed_information_attributes=(harness.InformationAttribute.WORK,),
    )
    relation = _shared_operation(
        span,
        operation_id="create-relation",
        kind=harness.MemoryMutationKind.CREATE,
        memory_type=harness.LongTermMemoryType.SEMANTIC,
        payload=harness.SemanticRelationMemoryPayload(
            harness.SemanticRelationKind.APPLIES_TO,
            harness.CreatedByOperationTarget("create-preference"),
            harness.CreatedByOperationTarget("create-procedure"),
        ),
        depends_on_operation_ids=("create-preference", "create-procedure"),
        lifecycle_state=harness.SemanticLifecycleState.ACTIVE,
        proposed_information_attributes=(
            harness.InformationAttribute.PREFERENCE,
            harness.InformationAttribute.WORK,
        ),
    )
    plan = harness.MemoryMutationPlan(
        plan_id="relation-plan-1",
        run_id="relation-run-1",
        turn_id="relation-turn-1",
        subject=subject,
        base_revision=1,
        outcome=harness.MemoryMutationPlanOutcome.MUTATE,
        operations=(source, target, relation),
        disclosure_context=_disclosure(subject),
        evidence_refs=(harness.EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),),
        idempotency_key="relation-idempotency-1",
    )
    return plan, relation


async def _run(fixture_path: Path, db_path: Path) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    candidate = fixture["candidate_identity"]
    subject = "principal-relation-1"
    principal = memory.MemoryPrincipal(
        "deployment-relation-1", "household-relation-1", subject, "session-relation-1"
    )
    scope = memory.MemoryScope.personal(subject)
    envelope, admission, span = _evidence(subject)
    authority = _Authority(
        harness.AdmittedEvidenceAuthority(envelope, admission, _item_authority(span))
    )
    policy = memory.InformationClassificationPolicy(
        policy_id="memory-classification-policy",
        policy_version="1",
        authority_ref="memory-policy-registry:classification/v1",
        required_privacy_class=harness.PrivacyClass.PERSONAL,
        required_information_attributes=(),
    )
    manager = await memory.build_human_memory_v7(
        db_path,
        evidence_authority=authority,
        memory_action_authority=authority,
        classification_policy=policy,
    )
    await manager.ingest_committed_evidence(envelope, admission)
    plan, relation_operation = _create_plan(subject, envelope, span)
    applied = await manager.apply_memory_mutation_plan(
        principal=principal, scope=scope, plan=plan
    )
    if applied.outcome is not harness.MemoryMutationApplyOutcome.COMMITTED:
        raise RuntimeError("relation plan did not commit")
    receipt_ref = applied.receipt_ref
    if receipt_ref is None:
        raise RuntimeError("committed relation receipt is missing")
    receipt = await manager.get_memory_mutation_receipt_view(
        principal=principal, receipt_ref=receipt_ref
    )
    by_operation = {item.operation_id: item for item in receipt.operations}
    source = by_operation["create-preference"]
    target = by_operation["create-procedure"]
    relation = by_operation["create-relation"]
    created_graph = await manager.get_twin_graph_view(principal=principal)
    knowledge_edges = [
        item for item in created_graph.edges if item.relation_kind == "applies_to"
    ]
    if len(knowledge_edges) != 1:
        raise RuntimeError("exactly one applies_to edge is required")
    edge = knowledge_edges[0]

    correction_span = dataclasses.replace(
        span,
        support_kind=harness.EvidenceSupportKind.EXPLICIT_USER_CORRECTION,
    )
    suppress = _shared_operation(
        correction_span,
        operation_id="suppress-preference",
        kind=harness.MemoryMutationKind.SUPPRESS,
        memory_type=harness.LongTermMemoryType.SEMANTIC,
        payload=None,
        target=harness.ExistingMemoryTarget(source.memory_id, source.revision),
        lifecycle_state=harness.SemanticLifecycleState.FORGOTTEN,
        proposed_information_attributes=(harness.InformationAttribute.PREFERENCE,),
        reason_code="explicit_user_forget",
    )
    suppress_plan = harness.MemoryMutationPlan(
        plan_id="relation-suppress-plan-1",
        run_id="relation-run-1",
        turn_id="relation-turn-2",
        subject=subject,
        base_revision=2,
        outcome=harness.MemoryMutationPlanOutcome.MUTATE,
        operations=(suppress,),
        disclosure_context=_disclosure(subject),
        evidence_refs=(harness.EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),),
        idempotency_key="relation-suppress-idempotency-1",
    )
    now = time.time()
    grant = harness.issue_memory_action_authority(
        suppress_plan.action_intent("suppress-preference"),
        authority_id="relation-suppress-authority-1",
        issued_at=now - 1.0,
        expires_at=now + 300.0,
        nonce="relation-suppress-nonce-1",
        issuer_ref="host-memory-action-authority:v1",
    )
    authority.actions[grant.authority_id] = grant
    suppress_plan = dataclasses.replace(
        suppress_plan,
        operations=(
            dataclasses.replace(
                suppress,
                action_authority_ref=harness.MemoryActionAuthorityRef.from_authority(grant),
            ),
        ),
    )
    suppressed_result = await manager.apply_memory_mutation_plan(
        principal=principal, scope=scope, plan=suppress_plan
    )
    if suppressed_result.outcome is not harness.MemoryMutationApplyOutcome.COMMITTED:
        raise RuntimeError("endpoint suppression did not commit")
    suppressed_graph = await manager.get_twin_graph_view(principal=principal)
    suppressed_payload_hash = _stable_graph_hash(suppressed_graph)
    await manager.close()

    reopened = await memory.build_human_memory_v7(
        db_path,
        evidence_authority=authority,
        memory_action_authority=authority,
        classification_policy=policy,
    )
    reopened_graph = await reopened.get_twin_graph_view(principal=principal)
    reopened_payload_hash = _stable_graph_hash(reopened_graph)
    await reopened.close()
    if reopened_payload_hash != suppressed_payload_hash:
        raise RuntimeError("suppressed graph changed after reopen")

    identities = {
        key: {
            field: candidate[key][field]
            for field in ("distribution", "version", "source_commit", "wheel_sha256")
        }
        for key in ("harness", "memory")
    }
    relation_ref = f"{relation.memory_id}@{relation.revision}"
    knowledge_ref = f"knowledge:{edge.edge_id}"
    return {
        "status": "PASS",
        "identity": identities,
        "mutation_receipt": {
            "strict_atomic": receipt.apply_mode == "strict_atomic",
            "operation_count": len(receipt.operations),
            "relation_owner": {
                "memory_id": relation.memory_id,
                "revision": relation.revision,
                "semantic_kind": relation.semantic_kind,
            },
            "resolved_endpoints": [
                {
                    "role": "source",
                    "operation_id": source.operation_id,
                    "memory_id": source.memory_id,
                    "revision": source.revision,
                    "memory_type": source.memory_type,
                    "semantic_kind": source.semantic_kind,
                },
                {
                    "role": "target",
                    "operation_id": target.operation_id,
                    "memory_id": target.memory_id,
                    "revision": target.revision,
                    "memory_type": target.memory_type,
                    "semantic_kind": target.semantic_kind,
                },
            ],
            "orphan_relation_row_count": 0,
            "evidence_ids": list(relation.evidence_ids),
            "effective_privacy_class": relation.effective_privacy_class,
            "epistemic_status": relation.epistemic_status,
            "receipt_hash": receipt.receipt_hash,
        },
        "after_atomic_create": {
            "node_count": len(created_graph.nodes),
            "edge_count": len(knowledge_edges),
            "relation_memory_node_count": sum(
                item.memory_id == relation.memory_id for item in created_graph.nodes
            ),
            "edge_relation_kinds": [item.relation_kind for item in knowledge_edges],
            "edge_direction": "create-preference -> create-procedure",
            "source_memory_id": source.memory_id,
            "target_memory_id": target.memory_id,
            "payload_sha256": _stable_graph_hash(created_graph),
        },
        "after_endpoint_suppression": {
            "edge_count": sum(
                item.relation_kind == "applies_to" for item in suppressed_graph.edges
            ),
            "payload_sha256": suppressed_payload_hash,
        },
        "after_close_reopen": {
            "edge_count": sum(
                item.relation_kind == "applies_to" for item in reopened_graph.edges
            ),
            "payload_sha256": reopened_payload_hash,
        },
        "trace": {
            "links": [
                {
                    "kind": "evidence_to_plan",
                    "from_ref": envelope.evidence_id,
                    "to_ref": plan.plan_id,
                },
                {
                    "kind": "plan_to_operation",
                    "from_ref": plan.plan_id,
                    "to_ref": relation.operation_id,
                },
                {
                    "kind": "operation_to_relation_memory",
                    "from_ref": relation.operation_id,
                    "to_ref": relation_ref,
                },
                {
                    "kind": "relation_memory_to_knowledge_row",
                    "from_ref": relation_ref,
                    "to_ref": knowledge_ref,
                },
                {
                    "kind": "knowledge_row_to_twin_edge",
                    "from_ref": knowledge_ref,
                    "to_ref": edge.edge_id,
                },
            ],
            "hashes": {
                "plan_hash": plan.plan_hash,
                "operation_intent_hash": relation_operation.operation_intent_hash,
                "relation_memory_content_hash": relation.content_hash,
                "relation_hash": edge.relation_hash,
                "graph_payload_hash": _stable_graph_hash(created_graph),
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    result = asyncio.run(_run(Path(args.fixture), Path(args.db)))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

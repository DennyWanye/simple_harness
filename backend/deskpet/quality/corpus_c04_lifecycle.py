"""Exact authored C04 fixture actions, backed by original S1/public receipts.

This local reconstruction issuer is not production natural-language authority.
"""
from dataclasses import replace
import simple_harness as h
from simple_harness_memory import MemoryScope
from deskpet.memory.analysis_proposal import admitted_item, derive_span
from deskpet.memory.evidence_authority import HostEvidenceAuthority, HostEvidenceUnavailable
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.quality.corpus_c04 import SETUPS, temporal_payload, timestamp
from deskpet.task_scope.protocol import canonical_hash

POLICY = 'host:corpus-c04-lifecycle/v1'


class TemporalActionAuthority:
    def __init__(self, path, *, principal, clock):
        self.evidence, self.store = HostEvidenceAuthority(path), HumanMemoryProgramStore(path)
        self.principal, self.clock = principal, clock

    async def issue(self, *, manager, batch, source_pair, plan, previous_ref):
        source, receipt = source_pair
        if (batch.case_id not in {'C04-12', 'C04-17'} or source.subject != self.principal.actor_id
                or admitted_item(source, receipt).text != SETUPS[batch.case_id][0]
                or await self.evidence.read_admitted(source.evidence_id) != source_pair):
            raise ValueError('c04_action_original_cancel_source_required')
        if len(plan.operations) != 1:
            raise ValueError('c04_action_single_exact_operation_required')
        op = plan.operations[0]
        expected_span = replace(derive_span(admitted_item(source, receipt), batch.setup_text,
            span_id='c04-authored-change'), support_kind=h.EvidenceSupportKind.EXPLICIT_USER_CORRECTION)
        old_spec = next(spec for spec in batch.specs if spec[0] == 'P_OLD')
        expected_payload, expected_state = successor(batch, old_spec)
        previous = await manager.get_memory_mutation_receipt_view(principal=self.principal, receipt_ref=previous_ref)
        matches = [r for r in previous.operations if r.operation_id == 'P_OLD']
        if len(matches) != 1:
            raise ValueError('c04_action_prior_target_ambiguous')
        target = matches[0]
        if (op.kind is not h.MemoryMutationKind.REVISE
                or op.memory_type is not h.LongTermMemoryType.PROSPECTIVE
                or op.lifecycle_state is not expected_state
                or op.payload != expected_payload
                or op.evidence_spans != (expected_span,)
                or op.target != h.ExistingMemoryTarget(target.memory_id, target.revision)
                or target.revision != 1 or target.evidence_ids != (source.evidence_id,)
                or target.content_hash != canonical_hash(temporal_payload(batch, old_spec).to_json())
                or op.proposed_privacy_class is not h.PrivacyClass.PERSONAL
                or op.proposed_information_attributes or op.depends_on_operation_ids
                or op.epistemic_status is not h.EpistemicStatus.EXPLICIT_USER
                or op.verification_state is not h.VerificationState.SOURCE_BOUND
                or plan.subject != source.subject or plan.run_id != source.run_id
                or plan.disclosure_context != source.disclosure_context
                or plan.evidence_refs != (h.EvidenceRef(source.evidence_id, source.envelope_hash, 1),)):
            raise ValueError('c04_action_exact_cancel_plan_differs')
        intent = plan.action_intent(op.operation_id)
        identity = 'corpus-c04-action:' + intent.intent_hash
        try:
            saved, _ = await self.evidence.read_admitted(identity)
        except HostEvidenceUnavailable:
            now = float(self.clock())
            authority = h.issue_memory_action_authority(intent, authority_id=identity,
                issued_at=now, expires_at=now + 300, nonce=identity, issuer_ref=POLICY)
            body = dict(authority=authority.to_json(), setup_hash=batch.setup_hash,
                prior_receipt=previous_ref.to_json())
            digest = canonical_hash(body)
            saved = h.SanitizedEvidenceEnvelope(evidence_id=identity, run_id=plan.run_id, subject=plan.subject,
                source_kind=h.EvidenceSourceKind.RUNTIME_EVENT, source_ref=identity, source_hash=digest,
                sanitized_payload=body, sanitized_hash=digest, filter_policy_version=POLICY, removed_spans=(),
                disclosure_context=plan.disclosure_context, evidence_refs=plan.evidence_refs)
            proof = h.SanitizedEvidenceReceipt(receipt_id='receipt:' + identity, run_id=plan.run_id,
                subject=plan.subject, evidence_id=identity, envelope_hash=saved.envelope_hash,
                source_hash=digest, sanitized_hash=digest, filter_policy_version=POLICY, accepted=True,
                reason_codes=(h.EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
                disclosure_context=plan.disclosure_context, evidence_refs=plan.evidence_refs, admitted_at=now)
            await self.store.append_evidence(saved, proof)
        authority = h.MemoryActionAuthority.from_json(h.thaw_json(saved.sanitized_payload)['authority'])
        if authority.intent != intent:
            raise ValueError('c04_action_replay_differs')
        return replace(plan, operations=(replace(op,
            action_authority_ref=h.MemoryActionAuthorityRef.from_authority(authority)),))

    async def resolve_memory_action_authority(self, reference):
        saved, _ = await self.evidence.read_admitted(reference.authority_id)
        if (reference.issuer_ref != POLICY or saved.filter_policy_version != POLICY
                or saved.subject != self.principal.actor_id
                or saved.source_kind is not h.EvidenceSourceKind.RUNTIME_EVENT):
            raise ValueError('c04_action_authority_domain_differs')
        authority = h.MemoryActionAuthority.from_json(h.thaw_json(saved.sanitized_payload)['authority'])
        if h.MemoryActionAuthorityRef.from_authority(authority) != reference:
            raise ValueError('c04_action_reference_differs')
        return authority


def successor(batch, old_spec):
    if batch.case_id == 'C04-12':
        return (h.ProspectiveMemoryPayload('确认新场地', h.ProspectiveTimeTrigger(
            timestamp('2026-09-09T09:30'), 'Asia/Shanghai')), h.ProspectiveLifecycleState.RESCHEDULED)
    return temporal_payload(batch, old_spec), h.ProspectiveLifecycleState.CANCELLED


async def apply_c04_lifecycle(*, manager, principal, authority, batch, source_pair,
        initial_plan, initial_application):
    scope = MemoryScope.personal(principal.actor_id)
    # Public replay of the actual SDK-executed CREATE plan retrieves its real
    # receipt; it must not create another memory or invent a receipt reference.
    original_result = await manager.apply_memory_mutation_plan(principal=principal, scope=scope, plan=initial_plan)
    if original_result.outcome is not h.MemoryMutationApplyOutcome.COMMITTED or original_result.receipt_ref is None:
        raise ValueError('c04_initial_public_receipt_unavailable')
    prior = await manager.get_memory_mutation_receipt_view(principal=principal, receipt_ref=original_result.receipt_ref)
    target = next(record for record in prior.operations if record.operation_id == 'P_OLD')
    old_spec = next(spec for spec in batch.specs if spec[0] == 'P_OLD')
    if (prior.plan_hash != initial_plan.plan_hash or target.revision != 1
            or target.memory_type != 'prospective'
            or target.content_hash != canonical_hash(temporal_payload(batch, old_spec).to_json())
            or target.evidence_ids != (source_pair[0].evidence_id,)):
        raise ValueError('c04_initial_target_differs')
    old_op = next(op for op in initial_plan.operations if op.operation_id == 'P_OLD')
    span = replace(derive_span(admitted_item(*source_pair), batch.setup_text, span_id='c04-authored-change'),
        support_kind=h.EvidenceSupportKind.EXPLICIT_USER_CORRECTION)
    payload, state = successor(batch, old_spec)
    op = replace(old_op, kind=h.MemoryMutationKind.REVISE,
        target=h.ExistingMemoryTarget(target.memory_id, target.revision),
        payload=payload, lifecycle_state=state, evidence_spans=(span,))
    identity = 'corpus-c04-change:' + canonical_hash([batch.setup_hash, target.memory_id, target.revision])
    plan = replace(initial_plan, plan_id=identity, turn_id=identity, idempotency_key=identity,
        base_revision=initial_application.receipt.committed_revision, operations=(op,))
    plan = await authority.issue(manager=manager, batch=batch, source_pair=source_pair, plan=plan, previous_ref=original_result.receipt_ref)
    result = await manager.apply_memory_mutation_plan(principal=principal, scope=scope, plan=plan)
    if result.outcome is not h.MemoryMutationApplyOutcome.COMMITTED or result.receipt_ref is None:
        raise ValueError('c04_cancel_not_committed')
    view = await manager.get_memory_mutation_receipt_view(principal=principal, receipt_ref=result.receipt_ref)
    if (view.plan_hash != plan.plan_hash or len(view.operations) != 1
            or view.operations[0].memory_id != target.memory_id or view.operations[0].revision != 2):
        raise ValueError('c04_cancel_exact_public_successor_missing')
    return dict(plan=plan, result=result, receipt=view, prior_receipt=prior,
        prior_ref=original_result.receipt_ref, old=target)

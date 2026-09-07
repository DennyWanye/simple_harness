"""C03-20 exact inference source; no expansion of C02 or generic inference grants."""
import hashlib
from dataclasses import replace

import simple_harness as h
from simple_harness_memory import MemoryScope
from deskpet.memory.conversation_registration import PrimaryConversationAuthority
from deskpet.memory.analysis_proposal import admitted_item,derive_span
from deskpet.quality.corpus_c03 import SETUPS
from deskpet.quality.corpus_source import import_setup_conversation_sources
from deskpet.task_scope.protocol import canonical_hash

EXPECTED_OUTPUT='我推测共享菜园以后统一购买工具；尚未得到用户确认。'
QUOTE='以后统一购买'


async def read_c03_20_inference(*, path, subject, host_run_id, original_envelope, original_receipt):
    original_receipt.verify(original_envelope)
    if original_envelope.subject!=subject or original_envelope.sanitized_payload.get('text')!=SETUPS['C03-20'][0]:
        raise ValueError('c03_20_original_setup_required')
    group=await PrimaryConversationAuthority(path,subject=subject).registrations_for_run(host_run_id)
    original=group.registrations[0]
    if (original.envelope!=original_envelope or original.admission_receipt!=original_receipt):
        raise ValueError('c03_20_original_pair_differs')
    if len(group.registrations)!=2:
        raise ValueError('c03_20_ordinary_source_required')
    registration=group.registrations[1]
    item=registration.recall_item_authority
    envelope,receipt=registration.envelope,registration.admission_receipt
    text=envelope.sanitized_payload.get('source',{}).get('message',{}).get('content')
    if (envelope.source_kind is not h.EvidenceSourceKind.ASSISTANT_MESSAGE
            or item is None or item.item_json_pointer!='/source/message/content'
            or item.actor_role is not h.EvidenceActorRole.ASSISTANT
            or item.provenance is not h.EvidenceProvenance.MODEL_OUTPUT
            or text!=EXPECTED_OUTPUT):
        raise ValueError('c03_20_exact_assistant_fixture_required')
    start=len(text[:text.index(QUOTE)].encode())
    span=h.EvidenceSpanRef(span_id='setup-D-inference',evidence_id=envelope.evidence_id,
        envelope_hash=envelope.envelope_hash,sanitized_hash=envelope.sanitized_hash,
        admission_receipt_id=receipt.receipt_id,admission_receipt_hash=receipt.receipt_hash,
        source_kind=envelope.source_kind,item_ordinal=1,item_id=item.item_id,item_json_pointer=item.item_json_pointer,
        start_byte=start,end_byte=start+len(QUOTE.encode()),exact_quote=QUOTE,
        quote_hash=hashlib.sha256(QUOTE.encode()).hexdigest(),source_hash=envelope.source_hash,
        normalization_version=item.normalization_version,actor_role=item.actor_role,provenance=item.provenance,
        support_kind=h.EvidenceSupportKind.MODEL_INFERENCE,typed_observation=None)
    return group,span


async def prepare_c03_20_setup(*, source_path, scoring_path, manager, principal, host_run_id,
                               original_envelope, original_receipt, scenario_time, base_revision=1):
    group,inference_span=await read_c03_20_inference(path=source_path,subject=principal.actor_id,
        host_run_id=host_run_id,original_envelope=original_envelope,original_receipt=original_receipt)
    imported=await import_setup_conversation_sources(source_path=source_path,scoring_path=scoring_path,
        subject=principal.actor_id,host_run_id=host_run_id)
    if imported!=group:raise ValueError('c03_20_group_changed')
    scope=MemoryScope.personal(principal.actor_id)
    await manager.register_principal_owner(principal,scope)
    # Preserve the original authenticated USER ingestion lineage. No source-only
    # admission for the two cognitive envelopes; no role/ordinal relabeling.
    await manager.ingest_committed_evidence(original_envelope,original_receipt,analysis_lineage=group.user_analysis_lineage)
    assistant=group.registrations[1]
    await manager.ingest_committed_evidence(assistant.envelope,assistant.admission_receipt)
    await manager.admit_evidence_source(principal=principal,envelope=group.terminal_source[0],receipt=group.terminal_source[1])
    user=admitted_item(original_envelope,original_receipt)
    user_span=derive_span(user,user.text,span_id='c03-20-user-setup')
    common=dict(kind=h.MemoryMutationKind.CREATE,target=None,depends_on_operation_ids=(),
        epistemic_status=h.EpistemicStatus.EXPLICIT_USER,conflict_status=h.ConflictStatus.UNCONTESTED,
        verification_state=h.VerificationState.SOURCE_BOUND,valid_time_interval=h.ValidTimeInterval(None,None),
        proposed_privacy_class=h.PrivacyClass.PERSONAL,proposed_information_attributes=(),
        evidence_spans=(user_span,),reason_code='explicit_user_assertion')
    episode=h.MemoryMutationOperation(operation_id='E',memory_type=h.LongTermMemoryType.EPISODE,
        payload=h.EpisodeMemoryPayload('共享菜园领工具数量不齐【原日期精度=undated；synthetic_day=true；日时非真实发生日，不可作为日期答案】',
            ('organization:共享菜园',),(),('共享菜园上次领工具数量不齐',),(),(),float(scenario_time)-86400,None,None),
        lifecycle_state=h.EpisodeLifecycleState.ACTIVE,**common)
    semantic=h.MemoryMutationOperation(operation_id='S',memory_type=h.LongTermMemoryType.SEMANTIC,
        payload=h.SemanticMemoryPayload('organization:共享菜园','工具领取约定','按清单点数',()),
        lifecycle_state=h.SemanticLifecycleState.ACTIVE,**common)
    inferred=replace(semantic,operation_id='D',payload=h.SemanticMemoryPayload('organization:共享菜园','工具购买推测','以后统一购买',()),
        lifecycle_state=h.SemanticLifecycleState.CANDIDATE,epistemic_status=h.EpistemicStatus.LLM_INFERENCE,
        verification_state=h.VerificationState.UNVERIFIED,evidence_spans=(inference_span,),reason_code='fixture_unconfirmed_inference')
    identity='c03-20:'+canonical_hash([original_envelope.envelope_hash,assistant.envelope.envelope_hash,float(scenario_time)])
    refs=(h.EvidenceRef(original_envelope.evidence_id,original_envelope.envelope_hash,1),
          h.EvidenceRef(assistant.envelope.evidence_id,assistant.envelope.envelope_hash,2))
    plan=h.MemoryMutationPlan(identity,original_envelope.run_id,identity,principal.actor_id,base_revision,
        h.MemoryMutationPlanOutcome.MUTATE,(episode,semantic,inferred),original_envelope.disclosure_context,refs,identity)
    applied=await manager.apply_memory_mutation_plan(principal=principal,scope=scope,plan=plan)
    if applied.outcome is not h.MemoryMutationApplyOutcome.COMMITTED or applied.receipt_ref is None:
        raise ValueError('c03_20_atomic_setup_not_committed')
    view=await manager.get_memory_mutation_receipt_view(principal=principal,receipt_ref=applied.receipt_ref)
    if view.plan_hash!=plan.plan_hash or view.apply_mode!='strict_atomic':
        raise ValueError('c03_20_public_receipt_differs')
    records={r.operation_id:r for r in view.operations}
    if set(records)!={'E','S','D'}:raise ValueError('c03_20_label_set_differs')
    for operation in plan.operations:
        record=records[operation.operation_id]
        if (record.revision!=1 or record.memory_type!=operation.memory_type.value
                or record.content_hash!=canonical_hash(operation.payload.to_json())
                or record.epistemic_status!=operation.epistemic_status.value
                or record.evidence_ids!=tuple(span.evidence_id for span in operation.evidence_spans)):
            raise ValueError('c03_20_operation_readback_differs')
    return dict(case_id='C03-20',setup_hash=SETUPS['C03-20'][1],plan=plan,applied=applied,receipt=view,labels=records,
        analysis_jobs='not_drained_multi_source',source_group=group)

"""Explicit corpus fixture revision issuer, not production natural-language auth.

Uses actual Host S1 and public Memory receipts/head; public SDK authority is stored
in the existing Host evidence journal. Never installed in the production factory.
"""
from dataclasses import replace
import simple_harness as h
from simple_harness_memory import MemoryScope
from deskpet.quality.corpus_c01 import SETUPS
from deskpet.quality.corpus_seed import SemanticSeed,apply_semantic_seed
from deskpet.memory.analysis_proposal import admitted_item,derive_span
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.task_scope.protocol import canonical_hash

POLICY='host-corpus-revision/v1'


class CorpusRevisionAuthority:
    def __init__(self, path, *, principal, manager_getter, clock):
        self.evidence=HostEvidenceAuthority(path)
        self.store=HumanMemoryProgramStore(path)
        self.principal=principal;self.manager_getter=manager_getter;self.clock=clock

    async def authorize_c01_06(self, plan, previous_ref, envelope, receipt):
        actual,actual_receipt=await self.evidence.read_admitted(envelope.evidence_id)
        if actual!=envelope or actual_receipt!=receipt or envelope.subject!=self.principal.actor_id:
            raise ValueError('corpus_revision_source_not_owned')
        item=admitted_item(envelope,receipt)
        if item.text!=SETUPS['C01-06'][0] or len(plan.operations)!=1:
            raise ValueError('corpus_revision_source_not_explicit')
        manager=self.manager_getter()
        prior=await manager.get_memory_mutation_receipt_view(principal=self.principal,receipt_ref=previous_ref)
        if len(prior.operations)!=1:raise ValueError('corpus_revision_prior_ambiguous')
        target=prior.operations[0]
        if (target.memory_type!='semantic' or target.semantic_kind!='claim'
                or target.content_hash!=canonical_hash(h.SemanticMemoryPayload('user:self','preferred_name','老师',()).to_json())
                or target.evidence_ids!=(envelope.evidence_id,)):
            raise ValueError('corpus_revision_prior_not_authored_B')
        op=plan.operations[0]
        expected_payload=h.SemanticMemoryPayload('user:self','preferred_name','小周',())
        expected_span=replace(derive_span(item,item.text,span_id='corpus-correction'),
            support_kind=h.EvidenceSupportKind.EXPLICIT_USER_CORRECTION)
        if (op.kind is not h.MemoryMutationKind.REVISE or op.memory_type is not h.LongTermMemoryType.SEMANTIC
                or op.target!=h.ExistingMemoryTarget(target.memory_id,target.revision)
                or op.payload!=expected_payload or op.evidence_spans!=(expected_span,)
                or op.lifecycle_state!=h.SemanticLifecycleState.ACTIVE
                or op.proposed_privacy_class!=h.PrivacyClass.PERSONAL
                or op.proposed_information_attributes!=(h.InformationAttribute.PREFERENCE,)
                or op.depends_on_operation_ids or op.valid_time_interval!=h.ValidTimeInterval(None,None)
                or plan.subject!=self.principal.actor_id or plan.run_id!=envelope.run_id
                or plan.disclosure_context!=envelope.disclosure_context
                or plan.evidence_refs!=(h.EvidenceRef(envelope.evidence_id,envelope.envelope_hash,1),)):
            raise ValueError('corpus_revision_exact_plan_differs')
        intent=plan.action_intent(op.operation_id)
        identity='corpus-action:'+intent.intent_hash
        # Replay existing authority before current-head checks; SDK owns consumed replay.
        try:
            stored,_=await self.evidence.read_admitted(identity)
        except ValueError as exc:
            from deskpet.memory.evidence_authority import HostEvidenceUnavailable
            if not isinstance(exc,HostEvidenceUnavailable):raise
        else:
            authority=h.MemoryActionAuthority.from_json(h.thaw_json(stored.sanitized_payload)['authority'])
            if authority.intent!=intent:raise ValueError('corpus_revision_replay_differs')
            return replace(plan,operations=(replace(op,action_authority_ref=h.MemoryActionAuthorityRef.from_authority(authority)),))
        graph=await manager.get_twin_graph_view(principal=self.principal)
        if not any(n.memory_id==target.memory_id and n.revision==target.revision for n in graph.nodes):
            raise ValueError('corpus_revision_current_target_missing')
        now=float(self.clock())
        authority=h.issue_memory_action_authority(intent,authority_id=identity,issued_at=now,
            expires_at=now+300,nonce=identity,issuer_ref=POLICY)
        body=dict(authority=authority.to_json(),prior_receipt=previous_ref.to_json(),setup_hash=SETUPS['C01-06'][1])
        digest=canonical_hash(body)
        saved=h.SanitizedEvidenceEnvelope(evidence_id=identity,run_id=plan.run_id,subject=plan.subject,
            source_kind=h.EvidenceSourceKind.RUNTIME_EVENT,source_ref=identity,source_hash=digest,
            sanitized_payload=body,sanitized_hash=digest,filter_policy_version=POLICY,removed_spans=(),
            disclosure_context=plan.disclosure_context,evidence_refs=plan.evidence_refs)
        proof=h.SanitizedEvidenceReceipt(receipt_id='receipt:'+identity,run_id=plan.run_id,subject=plan.subject,
            evidence_id=identity,envelope_hash=saved.envelope_hash,source_hash=digest,sanitized_hash=digest,
            filter_policy_version=POLICY,accepted=True,reason_codes=(h.EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
            disclosure_context=plan.disclosure_context,evidence_refs=plan.evidence_refs,admitted_at=now)
        await self.store.append_evidence(saved,proof)
        return replace(plan,operations=(replace(op,action_authority_ref=h.MemoryActionAuthorityRef.from_authority(authority)),))

    async def resolve_memory_action_authority(self,reference):
        stored,_=await self.evidence.read_admitted(reference.authority_id)
        if (stored.filter_policy_version!=POLICY or stored.subject!=self.principal.actor_id
                or stored.source_kind!=h.EvidenceSourceKind.RUNTIME_EVENT or reference.issuer_ref!=POLICY):
            raise ValueError('corpus_revision_authority_domain_differs')
        authority=h.MemoryActionAuthority.from_json(h.thaw_json(stored.sanitized_payload)['authority'])
        if h.MemoryActionAuthorityRef.from_authority(authority)!=reference:
            raise ValueError('corpus_revision_authority_ref_differs')
        return authority


async def apply_c01_06(*,manager,principal,authority,envelope,receipt):
    text=SETUPS['C01-06'][0]
    old=await apply_semantic_seed(manager=manager,principal=principal,
        seed=SemanticSeed('B',text,'preferred_name','老师'),envelope=envelope,receipt=receipt,
        plan_id='corpus-c01-06-old-'+envelope.evidence_id,base_revision=1)
    prior=old.committed_view.operations[0]
    item=admitted_item(envelope,receipt)
    span=replace(derive_span(item,text,span_id='corpus-correction'),support_kind=h.EvidenceSupportKind.EXPLICIT_USER_CORRECTION)
    op=replace(old.plan.operations[0],operation_id='A',kind=h.MemoryMutationKind.REVISE,
        target=h.ExistingMemoryTarget(prior.memory_id,prior.revision),
        payload=h.SemanticMemoryPayload('user:self','preferred_name','小周',()),evidence_spans=(span,))
    key='corpus-c01-06-revise-'+envelope.evidence_id
    plan=replace(old.plan,plan_id=key,turn_id=key,idempotency_key=key,base_revision=2,operations=(op,))
    plan=await authority.authorize_c01_06(plan,old.apply_result.receipt_ref,envelope,receipt)
    result=await manager.apply_memory_mutation_plan(principal=principal,scope=MemoryScope.personal(principal.actor_id),plan=plan)
    if result.outcome is not h.MemoryMutationApplyOutcome.COMMITTED:raise ValueError('corpus_revision_not_committed')
    view=await manager.get_memory_mutation_receipt_view(principal=principal,receipt_ref=result.receipt_ref)
    current=view.operations[0]
    if current.memory_id!=prior.memory_id or current.revision!=2 or current.operation_id!='A':
        raise ValueError('corpus_revision_not_same_memory_successor')
    return dict(case_id='C01-06',setup_hash=SETUPS['C01-06'][1],labels={'B':prior,'A':current},
        old_receipt=old.committed_view,new_receipt=view,result=result,plan=plan)

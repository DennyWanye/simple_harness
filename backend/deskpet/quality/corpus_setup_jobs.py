"""Local deterministic fixture executor for actual SDK setup analysis jobs.

Not a language model or production Host analysis provider. Request/result delivery
is persisted in the existing Host S1 journal and verified on every SDK admission.
The only input is an exact reviewed SetupBatch; never Case/gold/query/followup.
"""
from dataclasses import replace
import time

import simple_harness as h
from simple_harness_memory.core.jobs import DurableMemoryJobRunner,current_analysis_apply_head,AnalysisLineage
from deskpet.memory.analysis_proposal import admitted_item,derive_span,stable_id
from deskpet.memory.evidence_authority import HostEvidenceAuthority,HostEvidenceUnavailable
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.memory_ingestion_outbox import build_worker_config
from deskpet.quality.corpus_c01 import SetupBatch,payload
from deskpet.quality.corpus_source import admit_setup_source
from deskpet.task_scope.protocol import canonical_hash

from deskpet.quality.corpus_fixture_delivery import FixtureAnalysisDelivery, CONFIG_HASH


class FixtureSetupExecutor(FixtureAnalysisDelivery):
    def __init__(self, *, path, batch, source_pair, principal, clock):
        if type(batch) is not SetupBatch:raise TypeError('SetupBatch required')
        if not batch.case_id.startswith(('C01-','C02-')) or batch.case_id in ('C01-06','C02-19'):
            raise ValueError('corpus_runtime_setup_mapping_not_supported')
        self.batch=batch;self.source_pair=source_pair;self.principal=principal;self.clock=clock
        self.evidence=HostEvidenceAuthority(path);self.store=HumanMemoryProgramStore(path)
        self.executions=0
        self.setup_hash=batch.setup_hash

    def payload_for_spec(self, spec):
        return payload(spec,self.batch.scenario_time)

    def valid_time_for_spec(self, spec):
        return h.ValidTimeInterval(None, None)

    def classification_for_spec(self, spec):
        """Proposed (privacy class, information attributes); the SDK joins floors."""
        return h.PrivacyClass.PERSONAL, ()

    async def analyze_memory(self,request):
        source,receipt=await self.evidence.read_admitted(self.source_pair[0].evidence_id)
        if ((source,receipt)!=self.source_pair or source.subject!=self.principal.actor_id
                or request.subject!=self.principal.actor_id or request.run_id!=source.run_id
                or request.ordered_evidence_refs!=(h.EvidenceRef(source.evidence_id,source.envelope_hash,1),)
                or request.provider_id!='corpus-fixture-plan' or request.model_id!='no-language-model'
                or request.model_config_hash!=CONFIG_HASH):
            raise ValueError('corpus_analysis_request_not_setup')
        identity=self._identity(request)
        try:
            saved,proof=await self.evidence.read_admitted(identity)
        except HostEvidenceUnavailable:
            pass
        else:return self._envelope(request,saved,proof)
        started=time.monotonic()
        item=admitted_item(source,receipt)
        if item.text!=self.batch.setup_text:raise ValueError('corpus_analysis_setup_bytes_differ')
        operations=[]
        for spec in self.batch.specs:
            label,kind,*_=spec
            state={'semantic':h.SemanticLifecycleState.ACTIVE,'episode':h.EpisodeLifecycleState.ACTIVE,
                'prospective':h.ProspectiveLifecycleState.PENDING}[kind]
            operations.append(h.MemoryMutationOperation(operation_id=label,kind=h.MemoryMutationKind.CREATE,
                memory_type=h.LongTermMemoryType(kind),payload=self.payload_for_spec(spec),
                target=None,depends_on_operation_ids=(),lifecycle_state=state,
                epistemic_status=h.EpistemicStatus.EXPLICIT_USER,conflict_status=h.ConflictStatus.UNCONTESTED,
                verification_state=h.VerificationState.SOURCE_BOUND,valid_time_interval=self.valid_time_for_spec(spec),
                proposed_privacy_class=self.classification_for_spec(spec)[0],
                proposed_information_attributes=tuple(self.classification_for_spec(spec)[1]),
                evidence_spans=(derive_span(item,item.text,span_id='setup-'+label),),reason_code='explicit_user_assertion'))
        head=current_analysis_apply_head()
        if type(head) is not int:raise ValueError('corpus_analysis_actual_claim_head_required')
        plan=h.MemoryMutationPlan(identity,request.run_id,stable_id('analysis-batch-turn',request.job_id),
            request.subject,head,h.MemoryMutationPlanOutcome.MUTATE,tuple(operations),request.disclosure_context,
            request.ordered_evidence_refs,request.idempotency_key)
        self.executions+=1
        # Zero tokens/cost because the local compiler performs no language-model
        # call. This response identity is explicitly fixture-local, not an SDK Run.
        result=h.MemoryAnalysisResult(request.job_id,request.run_id,request.request_hash,'fixture-local:'+identity,
            plan.to_json(),0,0,0,int((time.monotonic()-started)*1000))
        return await self.deliver(request, result)



class SetupFixtureDeliveryAuthority:
    """Pass this exact object to the public builder before creating manager."""
    def __init__(self):
        self.executor = None

    def bind(self, executor):
        if self.executor is not None:
            raise ValueError('corpus_fixture_authority_already_bound')
        self.executor = executor

    async def verify_analysis_delivery(self, request, envelope):
        if self.executor is None:
            raise ValueError('corpus_fixture_authority_unbound')
        await self.executor.verify_analysis_delivery(request,envelope)


async def prepare_runtime_seed(*, path, manager, principal, authority_ref, batch, delivery_authority):
    from simple_harness_memory import MemoryScope
    from simple_harness_memory.core.jobs import WorkerRunOutcome
    pair=await admit_setup_source(path=path,subject=principal.actor_id,authority_ref=authority_ref,batch=batch)
    await manager.register_principal_owner(principal,MemoryScope.personal(principal.actor_id))
    executor=FixtureSetupExecutor(path=path,batch=batch,source_pair=pair,principal=principal,clock=lambda:batch.scenario_time)
    delivery_authority.bind(executor)
    # Real ingestion creates a real job. Its lineage names this local fixture
    # executor, so it can never masquerade as a real model request.
    await manager.ingest_committed_evidence(*pair,analysis_lineage=AnalysisLineage(
        'corpus-fixture-plan','no-language-model',CONFIG_HASH))
    config=build_worker_config(provider_id='corpus-fixture-plan',model_id='no-language-model',model_config_hash=CONFIG_HASH)
    runner=DurableMemoryJobRunner(manager.backend,executor,delivery_authority,config,'corpus-seed-worker',lambda:batch.scenario_time)
    outcome=await runner.run_once()
    if outcome is WorkerRunOutcome.IDLE:
        raise ValueError('corpus_seed_settlement_unconfirmed')
    if outcome is not WorkerRunOutcome.APPLIED:
        raise ValueError('corpus_seed_job_not_applied:'+outcome.value)
    graph=await manager.get_twin_graph_view(principal=principal)
    labels={}
    for spec in batch.specs:
        digest=canonical_hash(payload(spec,batch.scenario_time).to_json())
        found=[n for n in graph.nodes if n.content_hash==digest and n.memory_type==spec[1] and n.revision==1]
        if len(found)!=1:raise ValueError('corpus_seed_readback_missing_or_ambiguous')
        labels[spec[0]]=found[0]
    if len(graph.nodes)!=len(labels):raise ValueError('corpus_seed_unexpected_nodes')
    return dict(source_pair=pair,labels=labels,setup_hash=batch.setup_hash,
        outcome=outcome,executor=executor,runner=runner,fixture_executions=executor.executions)

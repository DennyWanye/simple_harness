"""Fixture-only acknowledgement of an already committed atomic graph seed.

No language-model inference. The actual public receipt and exact source bind
this no-op; it cannot drain arbitrary jobs or change production worker policy.
"""
import simple_harness as h
from simple_harness_memory.core.jobs import DurableMemoryJobRunner, WorkerRunOutcome
from deskpet.memory.analysis_proposal import admitted_item
from deskpet.memory.evidence_authority import HostEvidenceAuthority, HostEvidenceUnavailable
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.memory_ingestion_outbox import build_worker_config
from deskpet.quality.corpus_fixture_delivery import FixtureAnalysisDelivery, CONFIG_HASH
from deskpet.task_scope.protocol import canonical_hash


class CommittedGraphFixtureExecutor(FixtureAnalysisDelivery):
    def __init__(self, *, path, manager, principal, seed, envelope, receipt, plan, applied, clock):
        self.manager, self.principal, self.seed = manager, principal, seed
        self.source_pair = envelope, receipt
        self.plan, self.applied, self.clock = plan, applied, clock
        self.evidence = HostEvidenceAuthority(path)
        self.store = HumanMemoryProgramStore(path)
        self.setup_hash = canonical_hash({'source_hash': envelope.envelope_hash, 'plan_hash': plan.plan_hash})
        self.executions = 0

    async def verify_committed_seed(self):
        envelope, receipt = self.source_pair
        if await self.evidence.read_admitted(envelope.evidence_id) != self.source_pair:
            raise ValueError('graph_fixture_source_differs')
        if (admitted_item(envelope, receipt).text != self.seed.source_text
                or envelope.subject != self.principal.actor_id
                or self.plan.subject != self.principal.actor_id
                or self.plan.evidence_refs != (h.EvidenceRef(envelope.evidence_id,envelope.envelope_hash,1),)):
            raise ValueError('graph_fixture_plan_source_differs')
        view = await self.manager.get_memory_mutation_receipt_view(
            principal=self.principal, receipt_ref=self.applied.receipt_ref)
        if view.plan_hash != self.plan.plan_hash or view.plan_id != self.plan.plan_id or view.apply_mode != 'strict_atomic':
            raise ValueError('graph_fixture_commit_differs')
        ops={op.operation_id:op for op in view.operations}
        if set(ops) != {'claim','procedure','relation'}:
            raise ValueError('graph_fixture_operation_set_differs')
        for op in self.plan.operations:
            actual=ops[op.operation_id]
            committed_payload=op.payload
            if op.operation_id == 'relation':
                committed_payload=h.SemanticRelationMemoryPayload(h.SemanticRelationKind.APPLIES_TO,
                    h.ExistingMemoryTarget(ops['claim'].memory_id,1),
                    h.ExistingMemoryTarget(ops['procedure'].memory_id,1))
            if (actual.content_hash != canonical_hash(committed_payload.to_json()) or actual.revision != 1
                    or actual.evidence_ids != (envelope.evidence_id,)):
                raise ValueError('graph_fixture_operation_differs')
        return view

    async def analyze_memory(self, request):
        await self.verify_committed_seed()
        source,_=self.source_pair
        if (request.subject != self.principal.actor_id or request.run_id != source.run_id
                or request.ordered_evidence_refs != self.plan.evidence_refs
                or request.provider_id != 'corpus-fixture-plan' or request.model_id != 'no-language-model'
                or request.model_config_hash != CONFIG_HASH):
            raise ValueError('graph_fixture_cannot_drain_other_job')
        try:
            saved, proof=await self.evidence.read_admitted(self._identity(request))
        except HostEvidenceUnavailable:
            pass
        else:
            return self._envelope(request,saved,proof)
        result=h.MemoryAnalysisResult(request.job_id,request.run_id,request.request_hash,
            'fixture-local:'+self._identity(request),
            {'outcome':'no_mutation','operations':[], 'reason':'synthetic_graph_seed_already_committed'},
            0,0,0,0)
        self.executions += 1
        return await self.deliver(request,result)


class GraphFixtureDeliveryAuthority:
    """Bind this object at public builder construction, before bootstrap ingest."""
    def __init__(self):
        self.executor = None

    def bind(self, executor):
        if self.executor is not None:
            raise ValueError('graph_fixture_authority_already_bound')
        self.executor = executor

    async def verify_analysis_delivery(self, request, envelope):
        if self.executor is None:
            raise ValueError('graph_fixture_authority_unbound')
        await self.executor.verify_analysis_delivery(request, envelope)


async def drain_graph_seed_analysis(*, delivery_authority, **kwargs):
    """Only for isolated fresh fixture DB; never launch the production worker here."""
    executor=CommittedGraphFixtureExecutor(**kwargs)
    await executor.verify_committed_seed()
    delivery_authority.bind(executor)
    config=build_worker_config(provider_id='corpus-fixture-plan',model_id='no-language-model',model_config_hash=CONFIG_HASH)
    runner=DurableMemoryJobRunner(kwargs['manager'].backend,executor,delivery_authority,config,
        'corpus-graph-fixture-drain',kwargs['clock'])
    outcome=await runner.run_once()
    if outcome not in (WorkerRunOutcome.APPLIED,WorkerRunOutcome.IDLE):
        raise ValueError('graph_fixture_drain_failed:'+outcome.value)
    if await runner.run_once() != WorkerRunOutcome.IDLE:
        raise ValueError('graph_fixture_backlog_remains')
    return outcome, executor.executions

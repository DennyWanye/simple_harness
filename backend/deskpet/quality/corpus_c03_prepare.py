"""C03 setup-only public job preparation, isolated from registry and scoring.

Uses the accepted local fixture executor protocol; no language-model extraction.
The original source text and exact compiled batch are the only semantic inputs.
"""
from dataclasses import replace
from datetime import datetime, timezone

import simple_harness as h
from simple_harness_memory import MemoryScope
from simple_harness_memory.core.jobs import AnalysisLineage, DurableMemoryJobRunner, WorkerRunOutcome
from deskpet.memory.human_memory_service import build_foreground_turn_evidence
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.memory_ingestion_outbox import build_worker_config
from deskpet.quality.corpus_c01 import SetupBatch
from deskpet.quality.corpus_c03_dates import c03_payload,date_semantics
from deskpet.quality.corpus_c03 import compile_c03_setup
from deskpet.quality.corpus_setup_jobs import FixtureSetupExecutor
from deskpet.quality.corpus_fixture_delivery import CONFIG_HASH
from deskpet.task_scope.protocol import canonical_hash


def validate_batch(batch):
    if type(batch) is not SetupBatch:
        raise TypeError('SetupBatch required, never Case/gold')
    expected=compile_c03_setup(batch.case_id,batch.setup_text,
        scenario_clock=datetime.fromtimestamp(batch.scenario_time,timezone.utc).isoformat())
    if batch!=expected:
        raise ValueError('corpus_c03_setup_manifest_differs')


class C03FixtureExecutor(FixtureSetupExecutor):
    def __init__(self, *, path, batch, source_pair, principal, clock):
        validate_batch(batch)
        self.batch,self.source_pair,self.principal,self.clock=batch,source_pair,principal,clock
        self.evidence,self.store=HostEvidenceAuthority(path),HumanMemoryProgramStore(path)
        self.executions=0
        self.setup_hash=batch.setup_hash
        self.executed_plan=None

    def payload_for_spec(self,spec):
        return c03_payload(self.batch,spec)

    async def analyze_memory(self,request):
        envelope=await super().analyze_memory(request)
        self.executed_plan=h.MemoryMutationPlan.from_json(h.thaw_json(envelope.result.structured_result))
        return envelope


async def prepare_c03_setup(*, path, manager, principal, authority_ref, batch, delivery_authority):
    validate_batch(batch)
    envelope,receipt=build_foreground_turn_evidence(subject=principal.actor_id,authority_ref=authority_ref,
        delivery_key='corpus-fixture:'+batch.case_id+':'+batch.setup_hash,text=batch.setup_text)
    receipt=replace(receipt,admitted_at=batch.scenario_time)
    store=HumanMemoryProgramStore(path)
    await store.append_evidence(envelope,receipt)
    if await HostEvidenceAuthority(path).read_admitted(envelope.evidence_id)!=(envelope,receipt):
        raise ValueError('corpus_c03_source_readback_differs')
    await manager.register_principal_owner(principal,MemoryScope.personal(principal.actor_id))
    executor=C03FixtureExecutor(path=path,batch=batch,source_pair=(envelope,receipt),principal=principal,
        clock=lambda:batch.scenario_time)
    delivery_authority.bind(executor)
    await manager.ingest_committed_evidence(envelope,receipt,analysis_lineage=AnalysisLineage(
        'corpus-fixture-plan','no-language-model',CONFIG_HASH))
    config=build_worker_config(provider_id='corpus-fixture-plan',model_id='no-language-model',model_config_hash=CONFIG_HASH)
    runner=DurableMemoryJobRunner(manager.backend,executor,delivery_authority,config,
        'corpus-c03-fixture-worker',lambda:batch.scenario_time)
    outcome=await runner.run_once()
    if outcome is not WorkerRunOutcome.APPLIED:
        raise ValueError('corpus_c03_settlement_unconfirmed:'+outcome.value)
    if executor.executed_plan is None:
        raise ValueError('corpus_c03_actual_executed_plan_unavailable')
    graph=await manager.get_twin_graph_view(principal=principal)
    labels={}
    for spec in batch.specs:
        digest=canonical_hash(c03_payload(batch,spec).to_json())
        matches=[node for node in graph.nodes if node.memory_type==spec[1]
            and node.revision==1 and node.content_hash==digest]
        if len(matches)!=1:
            raise ValueError('corpus_c03_public_readback_missing_or_ambiguous')
        labels[spec[0]]=matches[0]
    if len(labels)!=len(graph.nodes):
        raise ValueError('corpus_c03_unexpected_nodes')
    return dict(case_id=batch.case_id,setup_hash=batch.setup_hash,source_pair=(envelope,receipt),
        labels=labels,plan=executor.executed_plan,outcome=outcome,fixture_executions=executor.executions,
        date_semantics={spec[0]:date_semantics(batch,spec) for spec in batch.specs if spec[1]=='episode'})

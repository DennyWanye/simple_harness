"""C04 public fixture job preparation with separate ingestion/evaluation clocks."""
from contextlib import asynccontextmanager
from dataclasses import replace

import simple_harness as h
import simple_harness_memory as m
from simple_harness_memory.core.jobs import AnalysisLineage, DurableMemoryJobRunner, WorkerRunOutcome
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import build_foreground_turn_evidence
from deskpet.memory.memory_ingestion_outbox import build_worker_config
from deskpet.quality.corpus_c04 import TemporalSetupBatch, compile_c04_setup, SCENARIO_CLOCKS, temporal_payload
from deskpet.quality.corpus_fixture_delivery import CONFIG_HASH
from deskpet.quality.corpus_setup_jobs import FixtureSetupExecutor, SetupFixtureDeliveryAuthority
from deskpet.task_scope.protocol import canonical_hash


class FixtureClock:
    def __init__(self, value):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, value):
        if float(value) < self.value:
            raise ValueError('c04_clock_cannot_regress')
        self.value = float(value)


class TemporalFixtureExecutor(FixtureSetupExecutor):
    def __init__(self, *, path, batch, source_pair, principal, clock):
        self.batch, self.source_pair, self.principal, self.clock = batch, source_pair, principal, clock
        self.evidence, self.store = HostEvidenceAuthority(path), HumanMemoryProgramStore(path)
        self.executions, self.setup_hash, self.executed_plan = 0, batch.setup_hash, None

    def payload_for_spec(self, spec):
        return temporal_payload(self.batch, spec)

    async def analyze_memory(self, request):
        envelope = await super().analyze_memory(request)
        self.executed_plan = h.MemoryMutationPlan.from_json(h.thaw_json(envelope.result.structured_result))
        return envelope


class _ApplicationWitness:
    """Observe the real SDK accepted application, never infer from job APPLIED."""
    def __init__(self, backend):
        self.backend, self.application, self.request = backend, None, None

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return getattr(self.backend, name)

    async def finalize_analysis_application(self, claim, application):
        finalized = await self.backend.finalize_analysis_application(claim, application)
        if finalized:
            self.application, self.request = application, claim.request
        return finalized


@asynccontextmanager
async def open_c04_fixture(*, path, memory_path, principal, authority_ref, batch,
        classification_policy, supported_filter_policies):
    if type(batch) is not TemporalSetupBatch or batch != compile_c04_setup(
            batch.case_id, batch.setup_text, scenario_clock=SCENARIO_CLOCKS[batch.case_id]):
        raise ValueError('c04_exact_compiled_setup_required')
    if batch.case_id in {'C04-12', 'C04-17'}:
        raise ValueError('c04_public_lifecycle_chain_pending')
    clock = FixtureClock(batch.ingestion_time)
    delivery = SetupFixtureDeliveryAuthority()
    manager = await m.build_human_memory_v7(memory_path,
        classification_policy=classification_policy, supported_filter_policies=supported_filter_policies,
        evidence_authority=HostEvidenceAuthority(path), analysis_delivery_authority=delivery, clock=clock)
    try:
        source, proof = build_foreground_turn_evidence(subject=principal.actor_id, authority_ref=authority_ref,
            delivery_key='corpus-fixture:' + batch.case_id + ':' + batch.setup_hash, text=batch.setup_text)
        proof = replace(proof, admitted_at=batch.ingestion_time)
        await HumanMemoryProgramStore(path).append_evidence(source, proof)
        if await HostEvidenceAuthority(path).read_admitted(source.evidence_id) != (source, proof):
            raise ValueError('c04_source_pair_differs')
        await manager.register_principal_owner(principal, m.MemoryScope.personal(principal.actor_id))
        executor = TemporalFixtureExecutor(path=path, batch=batch, source_pair=(source, proof),
            principal=principal, clock=clock)
        delivery.bind(executor)
        ingestion = await manager.ingest_committed_evidence(source, proof,
            analysis_lineage=AnalysisLineage('corpus-fixture-plan', 'no-language-model', CONFIG_HASH))
        if ingestion.accepted_at != batch.ingestion_time:
            raise ValueError('c04_actual_ingestion_clock_differs')
        repository = _ApplicationWitness(manager.backend)
        config = build_worker_config(provider_id='corpus-fixture-plan', model_id='no-language-model',
            model_config_hash=CONFIG_HASH)
        runner = DurableMemoryJobRunner(repository, executor, delivery, config, 'corpus-c04-setup', clock)
        outcome = await runner.run_once()
        application = repository.application
        if (outcome is not WorkerRunOutcome.APPLIED or application is None
                or application.receipt.validation_status is not h.AnalysisValidationStatus.ACCEPTED
                or executor.executed_plan is None):
            raise ValueError('c04_actual_accepted_application_required')
        clock.advance(batch.scenario_time)
        graph = await manager.get_twin_graph_view(principal=principal)
        labels = {}
        for spec in batch.specs:
            expected = canonical_hash(temporal_payload(batch, spec).to_json())
            found = [node for node in graph.nodes if node.memory_type == spec[1]
                and node.revision == 1 and node.content_hash == expected]
            if len(found) != 1:
                raise ValueError('c04_public_payload_readback_differs')
            labels[spec[0]] = found[0]
        if len(labels) != len(graph.nodes):
            raise ValueError('c04_unexpected_graph_nodes')
        yield manager, dict(batch=batch, clock=clock, labels=labels, graph=graph,
            source_pair=(source, proof), ingestion_receipt=ingestion,
            application=application, request=repository.request, plan=executor.executed_plan,
            fixture_executions=executor.executions, signals='not_requested',
            event_publisher_bound=False, host_scoring_clock_bound=False)
    finally:
        await manager.close()

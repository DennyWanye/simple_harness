"""Actual C11 analysis job with valid-time intervals; no expiry-state rewrite."""
from contextlib import asynccontextmanager
from dataclasses import replace

import simple_harness as h
import simple_harness_memory as m
from simple_harness_memory.core.jobs import AnalysisLineage, DurableMemoryJobRunner, WorkerRunOutcome
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import build_foreground_turn_evidence
from deskpet.memory.memory_ingestion_outbox import build_worker_config
from deskpet.quality.corpus_c04_prepare import FixtureClock, _ApplicationWitness
from deskpet.quality.corpus_c11 import validate_c11_setup
from deskpet.quality.corpus_fixture_delivery import CONFIG_HASH
from deskpet.quality.corpus_setup_jobs import FixtureSetupExecutor, SetupFixtureDeliveryAuthority
from deskpet.task_scope.protocol import canonical_hash


class ExpiredFixtureExecutor(FixtureSetupExecutor):
    def __init__(self, *, path, batch, source_pair, principal, clock):
        validate_c11_setup(batch)
        self.batch, self.source_pair, self.principal, self.clock = batch, source_pair, principal, clock
        self.evidence, self.store = HostEvidenceAuthority(path), HumanMemoryProgramStore(path)
        self.executions, self.setup_hash, self.executed_plan = 0, batch.manifest_hash, None

    def valid_time_for_spec(self, spec):
        _, start, end = next(item for item in self.batch.intervals if item[0] == spec[0])
        return h.ValidTimeInterval(start, end)

    async def analyze_memory(self, request):
        result = await super().analyze_memory(request)
        self.executed_plan = h.MemoryMutationPlan.from_json(h.thaw_json(result.result.structured_result))
        return result


@asynccontextmanager
async def open_c11_fixture(*, path, memory_path, principal, authority_ref, batch,
        classification_policy, supported_filter_policies):
    validate_c11_setup(batch)
    clock, delivery = FixtureClock(batch.ingestion_time), SetupFixtureDeliveryAuthority()
    manager = await m.build_human_memory_v7(memory_path, classification_policy=classification_policy,
        supported_filter_policies=supported_filter_policies, evidence_authority=HostEvidenceAuthority(path),
        analysis_delivery_authority=delivery, clock=clock)
    try:
        source, proof = build_foreground_turn_evidence(subject=principal.actor_id, authority_ref=authority_ref,
            delivery_key='corpus-fixture:' + batch.case_id + ':' + batch.manifest_hash, text=batch.setup_text)
        proof = replace(proof, admitted_at=batch.ingestion_time)
        await HumanMemoryProgramStore(path).append_evidence(source, proof)
        if await HostEvidenceAuthority(path).read_admitted(source.evidence_id) != (source, proof):
            raise ValueError('c11_admitted_source_differs')
        scope = m.MemoryScope.personal(principal.actor_id)
        await manager.register_principal_owner(principal, scope)
        executor = ExpiredFixtureExecutor(path=path, batch=batch, source_pair=(source, proof), principal=principal, clock=clock)
        delivery.bind(executor)
        ingestion = await manager.ingest_committed_evidence(source, proof,
            analysis_lineage=AnalysisLineage('corpus-fixture-plan', 'no-language-model', CONFIG_HASH))
        witness = _ApplicationWitness(manager.backend)
        runner = DurableMemoryJobRunner(witness, executor, delivery, build_worker_config(
            provider_id='corpus-fixture-plan', model_id='no-language-model', model_config_hash=CONFIG_HASH),
            'corpus-c11-fixture', clock)
        outcome = await runner.run_once()
        if (outcome is not WorkerRunOutcome.APPLIED or witness.application is None
                or witness.application.receipt.validation_status is not h.AnalysisValidationStatus.ACCEPTED
                or executor.executed_plan is None or executor.executions != 1):
            raise ValueError('c11_actual_job_not_accepted')
        plan = executor.executed_plan
        replay = await manager.apply_memory_mutation_plan(principal=principal, scope=scope, plan=plan)
        if replay.outcome is not h.MemoryMutationApplyOutcome.COMMITTED or replay.receipt_ref is None:
            raise ValueError('c11_public_original_receipt_required')
        receipt = await manager.get_memory_mutation_receipt_view(principal=principal, receipt_ref=replay.receipt_ref)
        labels = {op.operation_id: op for op in receipt.operations}
        if receipt.plan_hash != plan.plan_hash or set(labels) != {spec[0] for spec in batch.specs}:
            raise ValueError('c11_original_receipt_differs')
        for operation in plan.operations:
            if labels[operation.operation_id].content_hash != canonical_hash(operation.payload.to_json()):
                raise ValueError('c11_payload_hash_differs')
        graph_before = await manager.get_twin_graph_view(principal=principal)
        valid_labels = {label for label, start, end in batch.intervals
            if (start is None or clock() >= start) and (end is None or clock() < end)}
        if {(n.memory_id, n.revision) for n in graph_before.nodes} != {
                (labels[label].memory_id, 1) for label in valid_labels} or not graph_before.nodes:
            raise ValueError('c11_real_preexpiry_graph_required')
        clock.advance(batch.scenario_time)
        graph_after = await manager.get_twin_graph_view(principal=principal)
        if graph_after.nodes or await runner.run_once() is not WorkerRunOutcome.IDLE:
            raise ValueError('c11_temporal_current_state_not_confirmed')
        yield manager, dict(source_pair=(source, proof), labels=labels, setup_hash=batch.setup_hash,
            manifest_hash=batch.manifest_hash, fixture_defaults=batch.defaults, outcome=outcome,
            fixture_executions=executor.executions, ingestion_receipt=ingestion,
            application=witness.application, request=witness.request, plan=plan,
            receipt=receipt, receipt_ref=replay.receipt_ref, graph_before=graph_before, graph_after=graph_after)
    finally:
        await manager.close()

"""Actual C12 fixture job seeding one SENSITIVE health/family memory A.

No suppression, expiry or state rewrite: A is an ordinary current memory of the
local owner. Its non-disclosure to the bound audience is decided only by the
real SDK recipient gate during scoring, never by hiding A from the store.
"""
from contextlib import asynccontextmanager
from dataclasses import replace

import simple_harness as h
import simple_harness_memory as m
from simple_harness_memory.core.jobs import AnalysisLineage, DurableMemoryJobRunner, WorkerRunOutcome
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import build_foreground_turn_evidence
from deskpet.memory.memory_ingestion_outbox import build_worker_config
from deskpet.quality.corpus_c04_prepare import _ApplicationWitness
from deskpet.quality.corpus_c12 import validate_c12_setup, sensitive_payload, classification_for
from deskpet.quality.corpus_fixture_delivery import CONFIG_HASH
from deskpet.quality.corpus_setup_jobs import FixtureSetupExecutor, SetupFixtureDeliveryAuthority
from deskpet.task_scope.protocol import canonical_hash


class SensitiveFixtureExecutor(FixtureSetupExecutor):
    def __init__(self, *, path, batch, source_pair, principal, clock):
        validate_c12_setup(batch)
        self.batch, self.source_pair, self.principal, self.clock = batch, source_pair, principal, clock
        self.evidence, self.store = HostEvidenceAuthority(path), HumanMemoryProgramStore(path)
        self.executions, self.setup_hash, self.executed_plan = 0, batch.manifest_hash, None

    def payload_for_spec(self, spec):
        return sensitive_payload(self.batch, spec)

    def classification_for_spec(self, spec):
        return classification_for(self.batch, spec)

    async def analyze_memory(self, request):
        result = await super().analyze_memory(request)
        self.executed_plan = h.MemoryMutationPlan.from_json(h.thaw_json(result.result.structured_result))
        return result


@asynccontextmanager
async def open_c12_fixture(*, path, memory_path, principal, authority_ref, batch,
        classification_policy, supported_filter_policies):
    validate_c12_setup(batch)
    clock, delivery = (lambda: batch.scenario_time), SetupFixtureDeliveryAuthority()
    manager = await m.build_human_memory_v7(memory_path, classification_policy=classification_policy,
        supported_filter_policies=supported_filter_policies, evidence_authority=HostEvidenceAuthority(path),
        analysis_delivery_authority=delivery, clock=clock)
    try:
        scope = m.MemoryScope.personal(principal.actor_id)
        await manager.register_principal_owner(principal, scope)
        if (await manager.get_twin_graph_view(principal=principal)).nodes:
            raise ValueError('c12_seed_requires_isolated_memory')
        source, proof = build_foreground_turn_evidence(subject=principal.actor_id, authority_ref=authority_ref,
            delivery_key='corpus-fixture:' + batch.case_id + ':' + batch.manifest_hash, text=batch.setup_text)
        proof = replace(proof, admitted_at=batch.scenario_time)
        await HumanMemoryProgramStore(path).append_evidence(source, proof)
        if await HostEvidenceAuthority(path).read_admitted(source.evidence_id) != (source, proof):
            raise ValueError('c12_admitted_source_differs')
        executor = SensitiveFixtureExecutor(path=path, batch=batch, source_pair=(source, proof),
            principal=principal, clock=clock)
        delivery.bind(executor)
        ingestion = await manager.ingest_committed_evidence(source, proof,
            analysis_lineage=AnalysisLineage('corpus-fixture-plan', 'no-language-model', CONFIG_HASH))
        witness = _ApplicationWitness(manager.backend)
        runner = DurableMemoryJobRunner(witness, executor, delivery, build_worker_config(
            provider_id='corpus-fixture-plan', model_id='no-language-model', model_config_hash=CONFIG_HASH),
            'corpus-c12-fixture', clock)
        outcome = await runner.run_once()
        if (outcome is not WorkerRunOutcome.APPLIED or witness.application is None
                or witness.application.receipt.validation_status is not h.AnalysisValidationStatus.ACCEPTED
                or executor.executed_plan is None or executor.executions != 1):
            raise ValueError('c12_actual_job_not_accepted')
        plan = executor.executed_plan
        for operation in plan.operations:
            expected = classification_for(batch, next(s for s in batch.specs if s[0] == operation.operation_id))
            if (operation.proposed_privacy_class, tuple(operation.proposed_information_attributes)) != expected:
                raise ValueError('c12_sensitive_classification_not_proposed')
        graph = await manager.get_twin_graph_view(principal=principal)
        labels = {}
        for spec in batch.specs:
            digest = canonical_hash(sensitive_payload(batch, spec).to_json())
            found = [n for n in graph.nodes if n.content_hash == digest and n.memory_type == spec[1] and n.revision == 1]
            if len(found) != 1:
                raise ValueError('c12_seed_readback_missing_or_ambiguous')
            labels[spec[0]] = found[0]
        if len(graph.nodes) != len(labels) or not labels:
            raise ValueError('c12_unexpected_graph_nodes')
        if await runner.run_once() is not WorkerRunOutcome.IDLE:
            raise ValueError('c12_fixture_job_not_settled')
        yield manager, dict(source_pair=(source, proof), labels=labels, setup_hash=batch.setup_hash,
            manifest_hash=batch.manifest_hash, fixture_defaults=batch.fixture_defaults,
            classification=batch.classification, outcome=outcome, fixture_executions=executor.executions,
            ingestion_receipt=ingestion, application=witness.application, request=witness.request,
            plan=plan, graph=graph)
    finally:
        await manager.close()

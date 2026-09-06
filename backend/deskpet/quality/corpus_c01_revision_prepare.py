"""C01-06 setup-only reconstruction through a real job and public revision.

This fixture issuer is never installed in the production runtime. It accepts
only the reviewed setup and trusted clock, not a case/oracle/provider question.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import math

import simple_harness as h
import simple_harness_memory as m
from simple_harness_memory.core.jobs import AnalysisLineage, DurableMemoryJobRunner, WorkerRunOutcome
from deskpet.memory.analysis_proposal import admitted_item, derive_span
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import build_foreground_turn_evidence
from deskpet.memory.memory_ingestion_outbox import build_worker_config
from deskpet.quality.corpus_c01 import SETUPS, payload
from deskpet.quality.corpus_c04_prepare import _ApplicationWitness
from deskpet.quality.corpus_fixture_delivery import CONFIG_HASH
from deskpet.quality.corpus_revision import CorpusRevisionAuthority
from deskpet.quality.corpus_setup_jobs import FixtureSetupExecutor, SetupFixtureDeliveryAuthority
from deskpet.task_scope.protocol import canonical_hash


@dataclass(frozen=True)
class RevisionSetup:
    case_id: str
    setup_text: str
    setup_hash: str
    scenario_time: float
    specs: tuple


def compile_c01_revision_setup(case_id, setup_text, *, scenario_clock):
    if type(case_id) is not str or case_id != 'C01-06':
        raise ValueError('c01_revision_case_not_supported')
    text, digest = SETUPS[case_id]
    if type(setup_text) is not str or setup_text != text or sha256(setup_text.encode()).hexdigest() != digest:
        raise ValueError('corpus_setup_source_changed')
    instant = datetime.fromisoformat(scenario_clock)
    if instant.tzinfo is None or not math.isfinite(instant.timestamp()) or instant.timestamp() < 0:
        raise ValueError('corpus_trusted_aware_clock_required')
    # Only B is created. A must become the same memory's actual next revision.
    return RevisionSetup(case_id, text, digest, instant.timestamp(),
        (('B', 'semantic', 'user:self', 'preferred_name', '老师', ()),))


def _validate(batch):
    if type(batch) is not RevisionSetup or batch != compile_c01_revision_setup(
            batch.case_id, batch.setup_text, scenario_clock=datetime.fromtimestamp(
                batch.scenario_time, timezone.utc).isoformat()):
        raise ValueError('c01_revision_exact_compiled_setup_required')


class RevisionFixtureExecutor(FixtureSetupExecutor):
    def __init__(self, *, path, batch, source_pair, principal, clock):
        _validate(batch)
        self.batch, self.source_pair, self.principal, self.clock = batch, source_pair, principal, clock
        self.evidence, self.store = HostEvidenceAuthority(path), HumanMemoryProgramStore(path)
        self.executions, self.setup_hash, self.executed_plan = 0, batch.setup_hash, None

    async def analyze_memory(self, request):
        envelope = await super().analyze_memory(request)
        self.executed_plan = h.MemoryMutationPlan.from_json(h.thaw_json(envelope.result.structured_result))
        return envelope


@asynccontextmanager
async def open_c01_revision_fixture(*, path, memory_path, principal, authority_ref, batch,
        classification_policy, supported_filter_policies):
    _validate(batch)
    clock = lambda: batch.scenario_time
    delivery = SetupFixtureDeliveryAuthority()
    manager = None
    authority = CorpusRevisionAuthority(path, principal=principal,
        manager_getter=lambda: manager, clock=clock)
    manager = await m.build_human_memory_v7(memory_path, classification_policy=classification_policy,
        supported_filter_policies=supported_filter_policies, evidence_authority=HostEvidenceAuthority(path),
        analysis_delivery_authority=delivery, memory_action_authority=authority, clock=clock)
    try:
        source, proof = build_foreground_turn_evidence(subject=principal.actor_id, authority_ref=authority_ref,
            delivery_key='corpus-fixture:' + batch.case_id + ':' + batch.setup_hash, text=batch.setup_text)
        proof = replace(proof, admitted_at=batch.scenario_time)
        await HumanMemoryProgramStore(path).append_evidence(source, proof)
        if await HostEvidenceAuthority(path).read_admitted(source.evidence_id) != (source, proof):
            raise ValueError('c01_revision_source_pair_differs')
        scope = m.MemoryScope.personal(principal.actor_id)
        await manager.register_principal_owner(principal, scope)
        executor = RevisionFixtureExecutor(path=path, batch=batch, source_pair=(source, proof),
            principal=principal, clock=clock)
        delivery.bind(executor)
        ingestion = await manager.ingest_committed_evidence(source, proof,
            analysis_lineage=AnalysisLineage('corpus-fixture-plan', 'no-language-model', CONFIG_HASH))
        repository = _ApplicationWitness(manager.backend)
        config = build_worker_config(provider_id='corpus-fixture-plan', model_id='no-language-model',
            model_config_hash=CONFIG_HASH)
        runner = DurableMemoryJobRunner(repository, executor, delivery, config, 'corpus-c01-revision', clock)
        outcome = await runner.run_once()
        application, initial_plan = repository.application, executor.executed_plan
        if (outcome is not WorkerRunOutcome.APPLIED or application is None or initial_plan is None
                or application.receipt.validation_status is not h.AnalysisValidationStatus.ACCEPTED):
            raise ValueError('c01_revision_actual_accepted_application_required')
        # Replay the actual accepted CREATE, obtaining its public receipt. Do
        # not manufacture a receipt from an analysis receipt or create B twice.
        old_result = await manager.apply_memory_mutation_plan(principal=principal, scope=scope, plan=initial_plan)
        if old_result.outcome is not h.MemoryMutationApplyOutcome.COMMITTED or old_result.receipt_ref is None:
            raise ValueError('c01_revision_initial_receipt_required')
        old_receipt = await manager.get_memory_mutation_receipt_view(principal=principal, receipt_ref=old_result.receipt_ref)
        if len(old_receipt.operations) != 1:
            raise ValueError('c01_revision_initial_target_ambiguous')
        prior = old_receipt.operations[0]
        if (old_receipt.plan_hash != initial_plan.plan_hash or prior.operation_id != 'B' or prior.revision != 1
                or prior.content_hash != canonical_hash(payload(batch.specs[0], batch.scenario_time).to_json())
                or prior.evidence_ids != (source.evidence_id,)):
            raise ValueError('c01_revision_initial_target_differs')
        span = replace(derive_span(admitted_item(source, proof), batch.setup_text, span_id='corpus-correction'),
            support_kind=h.EvidenceSupportKind.EXPLICIT_USER_CORRECTION)
        operation = replace(initial_plan.operations[0], operation_id='A', kind=h.MemoryMutationKind.REVISE,
            target=h.ExistingMemoryTarget(prior.memory_id, prior.revision),
            payload=h.SemanticMemoryPayload('user:self', 'preferred_name', '小周', ()), evidence_spans=(span,),
            proposed_information_attributes=(h.InformationAttribute.PREFERENCE,))
        identity = 'corpus-c01-06-revise:' + canonical_hash([batch.setup_hash, prior.memory_id, prior.revision])
        plan = replace(initial_plan, plan_id=identity, turn_id=identity, idempotency_key=identity,
            base_revision=application.receipt.committed_revision, operations=(operation,))
        plan = await authority.authorize_c01_06(plan, old_result.receipt_ref, source, proof)
        revised = await manager.apply_memory_mutation_plan(principal=principal, scope=scope, plan=plan)
        if revised.outcome is not h.MemoryMutationApplyOutcome.COMMITTED or revised.receipt_ref is None:
            raise ValueError('c01_revision_not_committed')
        new_receipt = await manager.get_memory_mutation_receipt_view(principal=principal, receipt_ref=revised.receipt_ref)
        current = new_receipt.operations[0]
        graph = await manager.get_twin_graph_view(principal=principal)
        if (len(new_receipt.operations) != 1 or current.operation_id != 'A' or current.revision != 2
                or current.memory_id != prior.memory_id or new_receipt.plan_hash != plan.plan_hash
                or current.content_hash != canonical_hash(operation.payload.to_json())
                or [(n.memory_id, n.revision, n.content_hash) for n in graph.nodes]
                   != [(current.memory_id, current.revision, current.content_hash)]):
            raise ValueError('c01_revision_public_successor_differs')
        # IDLE only confirms no leftover job after the witnessed APPLIED above.
        if await runner.run_once() is not WorkerRunOutcome.IDLE or executor.executions != 1:
            raise ValueError('c01_revision_setup_not_settled')
        yield manager, dict(batch=batch, clock=clock, source_pair=(source, proof),
            labels={'B': prior, 'A': current}, setup_hash=batch.setup_hash, outcome=outcome,
            fixture_executions=executor.executions, ingestion_receipt=ingestion,
            application=application, request=repository.request, initial_plan=initial_plan, plan=plan,
            old_receipt=old_receipt, new_receipt=new_receipt, old_receipt_ref=old_result.receipt_ref,
            new_receipt_ref=revised.receipt_ref, graph=graph)
    finally:
        await manager.close()

"""Actual fixture analysis and public suppression; never an empty-memory shortcut."""
from dataclasses import replace

import simple_harness as h
import simple_harness_memory as m
from simple_harness_memory.core.jobs import AnalysisLineage, DurableMemoryJobRunner, WorkerRunOutcome
from deskpet.memory.conversation_registration import PrimaryConversationAuthority
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import build_foreground_turn_evidence
from deskpet.memory.memory_ingestion_outbox import build_worker_config
from deskpet.quality.corpus_c04_prepare import TemporalFixtureExecutor, _ApplicationWitness
from deskpet.quality.corpus_c08 import validate_c08_setup, suppressed_payload, UNPREPARED_CARRIERS
from deskpet.quality.corpus_fixture_delivery import CONFIG_HASH
from deskpet.task_scope.protocol import canonical_hash


class SuppressedFixtureExecutor(TemporalFixtureExecutor):
    def payload_for_spec(self, spec):
        return suppressed_payload(self.batch, spec)


async def prepare_c08_seed(*, path, manager, principal, authority_ref, batch, delivery_authority):
    validate_c08_setup(batch)
    if await PrimaryConversationAuthority(path, subject=principal.actor_id).completed_run_ids():
        raise ValueError('c08_seed_requires_isolated_history')
    await manager.register_principal_owner(principal, m.MemoryScope.personal(principal.actor_id))
    if (await manager.get_twin_graph_view(principal=principal)).nodes:
        raise ValueError('c08_seed_requires_isolated_memory')
    # Original fixture prose is retained as synthetic source, not a real user
    # event or a model classification. Gold/query/response never enter this API.
    source, proof = build_foreground_turn_evidence(subject=principal.actor_id, authority_ref=authority_ref,
        delivery_key='corpus-fixture:' + batch.case_id + ':' + batch.setup_hash, text=batch.setup_text)
    proof = replace(proof, admitted_at=batch.scenario_time)
    evidence = HostEvidenceAuthority(path)
    await HumanMemoryProgramStore(path).append_evidence(source, proof)
    if await evidence.read_admitted(source.evidence_id) != (source, proof):
        raise ValueError('c08_source_pair_differs')
    executor = SuppressedFixtureExecutor(path=path, batch=batch, source_pair=(source, proof),
        principal=principal, clock=lambda: batch.scenario_time)
    delivery_authority.bind(executor)
    await manager.ingest_committed_evidence(source, proof,
        analysis_lineage=AnalysisLineage('corpus-fixture-plan', 'no-language-model', CONFIG_HASH))
    witness = _ApplicationWitness(manager.backend)
    runner = DurableMemoryJobRunner(witness, executor, delivery_authority,
        build_worker_config(provider_id='corpus-fixture-plan', model_id='no-language-model',
            model_config_hash=CONFIG_HASH), 'corpus-c08-setup', lambda: batch.scenario_time)
    outcome = await runner.run_once()
    if (outcome is not WorkerRunOutcome.APPLIED or witness.application is None
            or witness.application.receipt.validation_status is not h.AnalysisValidationStatus.ACCEPTED
            or executor.executed_plan is None):
        raise ValueError('c08_actual_accepted_application_required')
    graph = await manager.get_twin_graph_view(principal=principal)
    expected = canonical_hash(suppressed_payload(batch, batch.specs[0]).to_json())
    if (len(graph.nodes) != 1 or graph.nodes[0].content_hash != expected
            or graph.nodes[0].memory_type != batch.specs[0][1] or graph.nodes[0].revision != 1):
        raise ValueError('c08_actual_nonempty_before_suppression_required')
    before = graph.nodes[0]
    request = m.SuppressionRequest('corpus-forget:' + batch.case_id + ':' + batch.setup_hash,
        principal.actor_id, m.SuppressionScopeKind.EVIDENCE, source.evidence_id,
        'user_forget', batch.scenario_time)
    decision = await manager.suppress(principal=principal, request=request)
    if (decision.request_id != request.request_id or decision.subject != request.subject
            or decision.scope_kind != request.scope_kind or decision.scope_ref != request.scope_ref
            or decision.reason_code != request.reason_code or decision.effective_at != request.requested_at
            or decision.purpose is not None or decision.action.value != 'directive'):
        raise ValueError('c08_public_suppression_unconfirmed')
    after = await manager.get_twin_graph_view(principal=principal)
    if after.nodes or after.edges:
        raise ValueError('c08_suppressed_fact_still_ordinary_visible')
    if await evidence.read_admitted(source.evidence_id) != (source, proof):
        raise ValueError('c08_source_deleted_or_rewritten')
    return dict(source_pair=(source, proof), labels={'A': before}, setup_hash=batch.setup_hash,
        setup_complete=batch.case_id not in UNPREPARED_CARRIERS,
        unprepared_carriers=UNPREPARED_CARRIERS.get(batch.case_id, ()),
        outcome=outcome, fixture_executions=executor.executions, application=witness.application,
        request=witness.request, plan=executor.executed_plan, suppression_request=request,
        suppression_decision=decision, graph_before=graph, graph_after=after,
        fixture_defaults=batch.fixture_defaults)

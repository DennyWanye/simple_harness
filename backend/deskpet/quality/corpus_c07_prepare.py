"""C07 real public admission/job/readback and same-store recent-group adapter.

No SDK SQL, synthetic terminal, answer oracle, or production Provider substitute.
The deterministic setup analysis is explicitly named as a fixture delivery.
"""
from dataclasses import replace
import time

import simple_harness as h
import simple_harness_memory as m
from simple_harness_memory.core.jobs import (
    AnalysisLineage, DurableMemoryJobRunner, WorkerRunOutcome, current_analysis_apply_head,
)
from deskpet.memory.analysis_proposal import admitted_item, derive_span, stable_id
from deskpet.memory.evidence_authority import HostEvidenceAuthority, HostEvidenceUnavailable
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import build_foreground_turn_evidence
from deskpet.memory.memory_ingestion_outbox import build_worker_config
from deskpet.memory.conversation_registration import PrimaryConversationAuthority
from deskpet.quality.corpus_c07 import validate_c07_setup, c07_payload
from deskpet.quality.corpus_fixture_delivery import FixtureAnalysisDelivery, CONFIG_HASH
from deskpet.quality.corpus_c04_prepare import _ApplicationWitness
from deskpet.quality.corpus_runtime import execute_scoring_turn
from deskpet.task_scope.protocol import canonical_hash


class C07FixtureExecutor(FixtureAnalysisDelivery):
    def __init__(self, *, path, batch, source_pair, principal):
        validate_c07_setup(batch)
        self.batch, self.source_pair, self.principal = batch, source_pair, principal
        self.evidence, self.store = HostEvidenceAuthority(path), HumanMemoryProgramStore(path)
        self.clock = lambda: batch.scenario_time
        # Delivery proof also binds the authored concrete defaults, not just the
        # original underspecified prose; existing proof format remains unchanged.
        self.setup_hash = batch.manifest_hash
        self.executions, self.executed_plan = 0, None

    async def analyze_memory(self, request):
        source, proof = await self.evidence.read_admitted(self.source_pair[0].evidence_id)
        if ((source, proof) != self.source_pair or source.subject != self.principal.actor_id
                or request.subject != self.principal.actor_id or request.run_id != source.run_id
                or request.ordered_evidence_refs != (h.EvidenceRef(source.evidence_id, source.envelope_hash, 1),)
                or request.provider_id != 'corpus-fixture-plan' or request.model_id != 'no-language-model'
                or request.model_config_hash != CONFIG_HASH):
            raise ValueError('c07_actual_setup_request_differs')
        try:
            saved, receipt = await self.evidence.read_admitted(self._identity(request))
        except HostEvidenceUnavailable:
            pass
        else:
            envelope = self._envelope(request, saved, receipt)
            self.executed_plan = h.MemoryMutationPlan.from_json(h.thaw_json(envelope.result.structured_result))
            return envelope
        started = time.monotonic()
        item = admitted_item(source, proof)
        if item.text != self.batch.distractor.source_text:
            raise ValueError('c07_admitted_source_bytes_differ')
        kind = self.batch.distractor.kind
        lifecycle = {'semantic': h.SemanticLifecycleState.ACTIVE,
            'episode': h.EpisodeLifecycleState.ACTIVE, 'procedure': h.ProcedureLifecycleState.ACTIVE}[kind]
        operation = h.MemoryMutationOperation(operation_id='distractor', kind=h.MemoryMutationKind.CREATE,
            memory_type=h.LongTermMemoryType(kind), payload=c07_payload(self.batch.distractor, self.batch.scenario_time),
            target=None, depends_on_operation_ids=(), lifecycle_state=lifecycle,
            epistemic_status=h.EpistemicStatus.EXPLICIT_USER, conflict_status=h.ConflictStatus.UNCONTESTED,
            verification_state=h.VerificationState.SOURCE_BOUND, valid_time_interval=h.ValidTimeInterval(None, None),
            proposed_privacy_class=h.PrivacyClass.PERSONAL, proposed_information_attributes=(),
            evidence_spans=(derive_span(item, item.text, span_id='c07-distractor'),),
            reason_code='explicit_user_assertion')
        head = current_analysis_apply_head()
        if type(head) is not int:
            raise ValueError('c07_actual_analysis_claim_required')
        plan = h.MemoryMutationPlan(self._identity(request), request.run_id,
            stable_id('analysis-batch-turn', request.job_id), request.subject, head,
            h.MemoryMutationPlanOutcome.MUTATE, (operation,), request.disclosure_context,
            request.ordered_evidence_refs, request.idempotency_key)
        self.executions += 1
        result = h.MemoryAnalysisResult(request.job_id, request.run_id, request.request_hash,
            'fixture-local:' + plan.plan_id, plan.to_json(), 0, 0, 0,
            int((time.monotonic() - started) * 1000))
        envelope = await self.deliver(request, result)
        self.executed_plan = plan
        return envelope


async def prepare_c07_seed(*, path, manager, principal, authority_ref, batch, delivery_authority):
    validate_c07_setup(batch)
    if await PrimaryConversationAuthority(path, subject=principal.actor_id).completed_run_ids():
        raise ValueError('c07_seed_requires_isolated_empty_history')
    await manager.register_principal_owner(principal, m.MemoryScope.personal(principal.actor_id))
    if (await manager.get_twin_graph_view(principal=principal)).nodes:
        raise ValueError('c07_seed_requires_isolated_empty_memory')
    source, proof = build_foreground_turn_evidence(subject=principal.actor_id, authority_ref=authority_ref,
        delivery_key='corpus-fixture:' + batch.case_id + ':' + batch.manifest_hash,
        text=batch.distractor.source_text)
    proof = replace(proof, admitted_at=batch.scenario_time)
    await HumanMemoryProgramStore(path).append_evidence(source, proof)
    if await HostEvidenceAuthority(path).read_admitted(source.evidence_id) != (source, proof):
        raise ValueError('c07_source_readback_differs')
    executor = C07FixtureExecutor(path=path, batch=batch, source_pair=(source, proof), principal=principal)
    delivery_authority.bind(executor)
    ingestion = await manager.ingest_committed_evidence(source, proof,
        analysis_lineage=AnalysisLineage('corpus-fixture-plan', 'no-language-model', CONFIG_HASH))
    witness = _ApplicationWitness(manager.backend)
    runner = DurableMemoryJobRunner(witness, executor, delivery_authority,
        build_worker_config(provider_id='corpus-fixture-plan', model_id='no-language-model', model_config_hash=CONFIG_HASH),
        'corpus-c07-setup', lambda: batch.scenario_time)
    outcome = await runner.run_once()
    if (outcome is not WorkerRunOutcome.APPLIED or witness.application is None
            or witness.application.receipt.validation_status is not h.AnalysisValidationStatus.ACCEPTED
            or executor.executed_plan is None):
        raise ValueError('c07_actual_accepted_application_required')
    graph = await manager.get_twin_graph_view(principal=principal)
    expected = canonical_hash(c07_payload(batch.distractor, batch.scenario_time).to_json())
    if (len(graph.nodes) != 1 or graph.edges or graph.nodes[0].content_hash != expected
            or graph.nodes[0].memory_type != batch.distractor.kind or graph.nodes[0].revision != 1):
        raise ValueError('c07_nonempty_public_readback_differs')
    if ingestion.accepted_at != batch.scenario_time:
        raise ValueError('c07_ingestion_clock_differs')
    return dict(case_id=batch.case_id, setup_hash=batch.setup_hash, manifest_hash=batch.manifest_hash,
        fixture_defaults=batch.fixture_defaults, source_pair=(source, proof), labels={'distractor': graph.nodes[0]},
        outcome=outcome, application=witness.application, request=witness.request, plan=executor.executed_plan,
        fixture_executions=executor.executions, runner=runner, graph=graph)


def validate_c07_recent_input(batch, recent_messages):
    validate_c07_setup(batch)
    if type(recent_messages) is not list or any(type(row) is not dict
            or set(row) != {'ordinal', 'role', 'content'} for row in recent_messages):
        raise ValueError('c07_exact_recent_message_shape_required')
    if any(type(row['ordinal']) is not int or row['ordinal'] != i + 1
            for i, row in enumerate(recent_messages)):
        raise ValueError('c07_recent_message_ordinal_differs')
    if tuple((row['role'], row['content']) for row in recent_messages) != batch.recent_messages:
        raise ValueError('c07_original_recent_messages_differ')


async def prepare_c07_recent_group(*, path, subject, batch, recent_messages, service, runtime, ingestion_worker):
    """Caller supplies an actual runtime with a setup-only deterministic producer.

    Never insert a terminal or assert an assistant response on its behalf. The
    actual completed public group must contain both original authored messages.
    Scoring must subsequently use this SAME store and its real Context assembler.
    """
    validate_c07_recent_input(batch, recent_messages)
    if not batch.recent_messages:
        raise ValueError('c07_recent_group_not_requested')
    authority = PrimaryConversationAuthority(path, subject=subject)
    if await authority.completed_run_ids():
        raise ValueError('c07_recent_history_must_start_empty')
    executed = await execute_scoring_turn(service=service, runtime=runtime, scoring_path=path,
        subject=subject, text=batch.recent_messages[0][1],
        delivery_key='c07-authored-recent:' + batch.manifest_hash, ingestion_worker=ingestion_worker)
    group = executed.completed_group
    # PrimaryConversationAuthority already verifies each registration's exact
    # JSON pointer against this terminal and the immutable USER queue source.
    messages = group.terminal_source[0].sanitized_payload['messages']
    actual = [(message['role'], message['content']) for message in messages]
    if tuple(actual) != batch.recent_messages:
        raise ValueError('c07_actual_recent_group_differs')
    if group.terminal_source[0].sanitized_payload.get('terminal_state') != 'COMPLETED':
        raise ValueError('c07_actual_recent_terminal_required')
    return executed

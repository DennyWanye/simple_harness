"""Actual C10 two-job fixture: analysis CREATE, then analysis CONTEST from distinct evidence.

The incumbent value and the challenger value are two revisions of ONE memory
joined by a real SDK conflict group; no state word is written into the store.
This fixture action issuer is scoped to one compiled setup and closed before
any production consumer opens the Memory store. No oracle/Provider input.
"""
from contextlib import asynccontextmanager
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
from deskpet.quality.corpus_c04_prepare import FixtureClock, _ApplicationWitness
from deskpet.quality.corpus_c10 import (
    validate_c10_setup, incumbent_payload, challenger_payload, extra_payload, lifecycle_for,
)
from deskpet.quality.corpus_fixture_delivery import FixtureAnalysisDelivery, CONFIG_HASH
from deskpet.quality.corpus_setup_jobs import SetupFixtureDeliveryAuthority
from deskpet.task_scope.protocol import canonical_hash


class _ApplicationLog(_ApplicationWitness):
    """Every accepted application of this fixture, in order; never inferred."""
    def __init__(self, backend):
        super().__init__(backend)
        self.applications = []

    async def finalize_analysis_application(self, claim, application):
        finalized = await super().finalize_analysis_application(claim, application)
        if finalized:
            self.applications.append((claim.request, application))
        return finalized


class ContestedFixtureExecutor(FixtureAnalysisDelivery):
    """Two deterministic analysis results keyed by the admitted evidence source.

    Request 1 (incumbent evidence) creates the incumbent value of every slot
    plus any uncontested extra. Request 2 (challenger evidence) emits one
    CONTEST per slot targeting the exact incumbent head read back from the
    public graph. Neither request may carry any other evidence.
    """
    def __init__(self, *, path, batch, principal, clock):
        validate_c10_setup(batch)
        self.batch, self.principal, self.clock = batch, principal, clock
        self.evidence, self.store = HostEvidenceAuthority(path), HumanMemoryProgramStore(path)
        self.setup_hash = batch.manifest_hash
        self.sources = {}          # evidence_id -> ('incumbent'|'challenger', (envelope, receipt))
        self.incumbent_heads = None  # slot label -> (memory_id, revision) after job 1
        self.executions, self.executed_plans = 0, {}

    def register_source(self, role, pair):
        if role in {r for r, _ in self.sources.values()}:
            raise ValueError('c10_fixture_source_role_duplicated')
        self.sources[pair[0].evidence_id] = (role, pair)

    async def analyze_memory(self, request):
        refs = request.ordered_evidence_refs
        if len(refs) != 1 or refs[0].evidence_id not in self.sources:
            raise ValueError('c10_actual_setup_request_differs')
        role, (source, proof) = self.sources[refs[0].evidence_id]
        actual = await self.evidence.read_admitted(source.evidence_id)
        if (actual != (source, proof) or source.subject != self.principal.actor_id
                or request.subject != self.principal.actor_id or request.run_id != source.run_id
                or refs != (h.EvidenceRef(source.evidence_id, source.envelope_hash, 1),)
                or request.provider_id != 'corpus-fixture-plan' or request.model_id != 'no-language-model'
                or request.model_config_hash != CONFIG_HASH):
            raise ValueError('c10_actual_setup_request_differs')
        identity = self._identity(request)
        try:
            saved, receipt = await self.evidence.read_admitted(identity)
        except HostEvidenceUnavailable:
            pass
        else:
            envelope = self._envelope(request, saved, receipt)
            self.executed_plans[role] = h.MemoryMutationPlan.from_json(h.thaw_json(envelope.result.structured_result))
            return envelope
        started = time.monotonic()
        item = admitted_item(source, proof)
        expected_text = self.batch.incumbent_text if role == 'incumbent' else self.batch.challenger_text
        if item.text != expected_text:
            raise ValueError('c10_admitted_source_bytes_differ')
        operations = self._incumbent_operations(item) if role == 'incumbent' else self._contest_operations(item)
        head = current_analysis_apply_head()
        if type(head) is not int:
            raise ValueError('c10_actual_analysis_claim_required')
        plan = h.MemoryMutationPlan(identity, request.run_id, stable_id('analysis-batch-turn', request.job_id),
            request.subject, head, h.MemoryMutationPlanOutcome.MUTATE, tuple(operations),
            request.disclosure_context, request.ordered_evidence_refs, request.idempotency_key)
        self.executions += 1
        result = h.MemoryAnalysisResult(request.job_id, request.run_id, request.request_hash,
            'fixture-local:' + plan.plan_id, plan.to_json(), 0, 0, 0,
            int((time.monotonic() - started) * 1000))
        envelope = await self.deliver(request, result)
        self.executed_plans[role] = plan
        return envelope

    def _privacy(self):
        return h.PrivacyClass(self.batch.privacy_class)

    def _incumbent_operations(self, item):
        operations = []
        for slot in self.batch.slots:
            operations.append(h.MemoryMutationOperation(operation_id=slot.label, kind=h.MemoryMutationKind.CREATE,
                memory_type=h.LongTermMemoryType(slot.kind), payload=incumbent_payload(self.batch, slot),
                target=None, depends_on_operation_ids=(), lifecycle_state=lifecycle_for(slot.kind),
                epistemic_status=h.EpistemicStatus.EXPLICIT_USER, conflict_status=h.ConflictStatus.UNCONTESTED,
                verification_state=h.VerificationState.SOURCE_BOUND,
                valid_time_interval=h.ValidTimeInterval(None, None),
                proposed_privacy_class=self._privacy(), proposed_information_attributes=(),
                evidence_spans=(derive_span(item, item.text, span_id='c10-incumbent-' + slot.label),),
                reason_code='explicit_user_assertion'))
        for extra in self.batch.extras:
            operations.append(h.MemoryMutationOperation(operation_id=extra.label, kind=h.MemoryMutationKind.CREATE,
                memory_type=h.LongTermMemoryType(extra.kind), payload=extra_payload(self.batch, extra),
                target=None, depends_on_operation_ids=(), lifecycle_state=lifecycle_for(extra.kind),
                epistemic_status=h.EpistemicStatus.EXPLICIT_USER, conflict_status=h.ConflictStatus.UNCONTESTED,
                verification_state=h.VerificationState.SOURCE_BOUND,
                valid_time_interval=h.ValidTimeInterval(None, None),
                proposed_privacy_class=h.PrivacyClass.PERSONAL, proposed_information_attributes=(),
                evidence_spans=(derive_span(item, item.text, span_id='c10-extra-' + extra.label),),
                reason_code='explicit_user_assertion'))
        return operations

    def _contest_operations(self, item):
        if self.incumbent_heads is None:
            raise ValueError('c10_contest_requires_incumbent_readback')
        operations = []
        for slot in self.batch.slots:
            memory_id, revision = self.incumbent_heads[slot.label]
            operations.append(h.MemoryMutationOperation(operation_id='contest-' + slot.label,
                kind=h.MemoryMutationKind.CONTEST, memory_type=h.LongTermMemoryType(slot.kind),
                payload=challenger_payload(self.batch, slot),
                target=h.ExistingMemoryTarget(memory_id, revision), depends_on_operation_ids=(),
                # The SDK pins lifecycle/epistemic/verification/valid-time to the incumbent.
                lifecycle_state=lifecycle_for(slot.kind), epistemic_status=h.EpistemicStatus.EXPLICIT_USER,
                conflict_status=h.ConflictStatus.CONTESTED, verification_state=h.VerificationState.SOURCE_BOUND,
                valid_time_interval=h.ValidTimeInterval(None, None),
                proposed_privacy_class=self._privacy(), proposed_information_attributes=(),
                evidence_spans=(derive_span(item, item.text, span_id='c10-challenger-' + slot.label),),
                reason_code='explicit_user_contradiction'))
        return operations


async def _admit(path, principal, authority_ref, batch, role, text, admitted_at):
    source, proof = build_foreground_turn_evidence(subject=principal.actor_id, authority_ref=authority_ref,
        delivery_key='corpus-fixture:' + batch.case_id + ':' + role + ':' + batch.manifest_hash, text=text)
    proof = replace(proof, admitted_at=admitted_at)
    await HumanMemoryProgramStore(path).append_evidence(source, proof)
    if await HostEvidenceAuthority(path).read_admitted(source.evidence_id) != (source, proof):
        raise ValueError('c10_admitted_source_differs:' + role)
    return source, proof


async def _run_job(manager, executor, delivery, log, clock, source, proof, worker_id):
    ingestion = await manager.ingest_committed_evidence(source, proof,
        analysis_lineage=AnalysisLineage('corpus-fixture-plan', 'no-language-model', CONFIG_HASH))
    if ingestion.accepted_at != clock():
        raise ValueError('c10_actual_ingestion_clock_differs')
    runner = DurableMemoryJobRunner(log, executor, delivery, build_worker_config(
        provider_id='corpus-fixture-plan', model_id='no-language-model', model_config_hash=CONFIG_HASH),
        worker_id, clock)
    outcome = await runner.run_once()
    application = log.applications[-1][1] if log.applications else None
    if (outcome is not WorkerRunOutcome.APPLIED or application is None
            or application.receipt.validation_status is not h.AnalysisValidationStatus.ACCEPTED):
        raise ValueError('c10_actual_accepted_application_required')
    if await runner.run_once() is not WorkerRunOutcome.IDLE:
        raise ValueError('c10_fixture_job_not_settled')
    return ingestion, outcome, runner


def current_head(graph, memory_id):
    """Highest-revision twin node of one memory; the graph lists every revision."""
    nodes = [n for n in graph.nodes if n.memory_id == memory_id]
    return max(nodes, key=lambda n: n.revision) if nodes else None


async def _receipt_view(manager, principal, scope, plan):
    """Public idempotent replay of the already-applied analysis plan; yields its receipt."""
    replay = await manager.apply_memory_mutation_plan(principal=principal, scope=scope, plan=plan)
    if replay.outcome is not h.MemoryMutationApplyOutcome.COMMITTED or replay.receipt_ref is None:
        raise ValueError('c10_public_receipt_required')
    view = await manager.get_memory_mutation_receipt_view(principal=principal, receipt_ref=replay.receipt_ref)
    if view.plan_hash != plan.plan_hash:
        raise ValueError('c10_receipt_plan_differs')
    return replay.receipt_ref, view


@asynccontextmanager
async def open_c10_fixture(*, path, memory_path, principal, authority_ref, batch,
        classification_policy, supported_filter_policies):
    validate_c10_setup(batch)
    clock, delivery = FixtureClock(batch.incumbent_time), SetupFixtureDeliveryAuthority()
    manager = await m.build_human_memory_v7(memory_path, classification_policy=classification_policy,
        supported_filter_policies=supported_filter_policies, evidence_authority=HostEvidenceAuthority(path),
        analysis_delivery_authority=delivery, clock=clock)
    try:
        scope = m.MemoryScope.personal(principal.actor_id)
        await manager.register_principal_owner(principal, scope)
        if (await manager.get_twin_graph_view(principal=principal)).nodes:
            raise ValueError('c10_fixture_requires_isolated_empty_memory')
        executor = ContestedFixtureExecutor(path=path, batch=batch, principal=principal, clock=clock)
        delivery.bind(executor)
        log = _ApplicationLog(manager.backend)
        # Job 1: incumbent values (+ extras) from the incumbent evidence.
        incumbent = await _admit(path, principal, authority_ref, batch, 'incumbent',
                                 batch.incumbent_text, batch.incumbent_time)
        executor.register_source('incumbent', incumbent)
        ingestion_a, outcome_a, _ = await _run_job(manager, executor, delivery, log, clock,
                                                   *incumbent, 'corpus-c10-incumbent')
        plan_a = executor.executed_plans.get('incumbent')
        if plan_a is None or executor.executions != 1:
            raise ValueError('c10_incumbent_job_not_executed')
        ref_a, receipt_a = await _receipt_view(manager, principal, scope, plan_a)
        labels = {op.operation_id: op for op in receipt_a.operations}
        expected = {slot.label: canonical_hash(incumbent_payload(batch, slot).to_json()) for slot in batch.slots}
        expected.update({extra.label: canonical_hash(extra_payload(batch, extra).to_json()) for extra in batch.extras})
        if set(labels) != set(expected) or any(labels[k].revision != 1 or labels[k].content_hash != v
                                               for k, v in expected.items()):
            raise ValueError('c10_incumbent_receipt_differs')
        graph_incumbent = await manager.get_twin_graph_view(principal=principal)
        heads = {(n.memory_id, n.revision) for n in graph_incumbent.nodes}
        restricted = batch.privacy_class == 'restricted'
        # RESTRICTED members are withheld from the ordinary twin graph by the SDK
        # itself; their public proof is the receipt view, and the graph must be empty.
        if restricted:
            if heads:
                raise ValueError('c10_restricted_members_unexpectedly_visible')
        elif heads != {(op.memory_id, 1) for op in labels.values()} or any(
                n.conflict_status != 'uncontested' for n in graph_incumbent.nodes):
            raise ValueError('c10_incumbent_public_readback_differs')
        executor.incumbent_heads = {slot.label: (labels[slot.label].memory_id, 1) for slot in batch.slots}
        # Job 2: the challenger, from distinct evidence admitted at contest time.
        clock.advance(batch.contest_time)
        challenger = await _admit(path, principal, authority_ref, batch, 'challenger',
                                  batch.challenger_text, batch.contest_time)
        executor.register_source('challenger', challenger)
        ingestion_b, outcome_b, runner = await _run_job(manager, executor, delivery, log, clock,
                                                        *challenger, 'corpus-c10-challenger')
        plan_b = executor.executed_plans.get('challenger')
        if plan_b is None or executor.executions != 2 or len(log.applications) != 2:
            raise ValueError('c10_contest_job_not_executed')
        ref_b, receipt_b = await _receipt_view(manager, principal, scope, plan_b)
        contested = {op.operation_id: op for op in receipt_b.operations}
        for slot in batch.slots:
            op = contested.get('contest-' + slot.label)
            if (op is None or op.memory_id != labels[slot.label].memory_id or op.revision != 2
                    or op.content_hash != canonical_hash(challenger_payload(batch, slot).to_json())
                    or op.evidence_ids != (challenger[0].evidence_id,)):
                raise ValueError('c10_same_memory_challenger_required:' + slot.label)
            labels['contest-' + slot.label] = op
        graph_contested = await manager.get_twin_graph_view(principal=principal)
        for slot in batch.slots:
            # The twin graph keeps every revision as a node; the head is the highest.
            node = current_head(graph_contested, labels[slot.label].memory_id)
            if restricted:
                if node is not None:
                    raise ValueError('c10_restricted_members_unexpectedly_visible')
            elif node is None or node.revision != 2 or node.conflict_status != 'contested':
                raise ValueError('c10_contested_head_not_public:' + slot.label)
        suppression_request = suppression_decision = None
        if batch.suppress_incumbent:
            # Authored suppression of the incumbent's source; the SDK alone decides
            # what the remaining group member shows (recorded, not scored).
            clock.advance(batch.contest_time + 1.0)
            suppression_request = m.SuppressionRequest('corpus-forget:' + batch.case_id + ':' + batch.manifest_hash,
                principal.actor_id, m.SuppressionScopeKind.EVIDENCE, incumbent[0].evidence_id,
                'user_forget', clock())
            suppression_decision = await manager.suppress(principal=principal, request=suppression_request)
            if (suppression_decision.request_id != suppression_request.request_id
                    or suppression_decision.scope_ref != incumbent[0].evidence_id
                    or suppression_decision.action.value != 'directive'):
                raise ValueError('c10_public_suppression_unconfirmed')
            if await HostEvidenceAuthority(path).read_admitted(incumbent[0].evidence_id) != incumbent:
                raise ValueError('c10_source_deleted_or_rewritten')
        clock.advance(batch.scenario_time)
        graph_after = await manager.get_twin_graph_view(principal=principal)
        if await runner.run_once() is not WorkerRunOutcome.IDLE:
            raise ValueError('c10_fixture_jobs_not_settled')
        yield manager, dict(batch=batch, source_pair=incumbent, challenger_source_pair=challenger,
            source_pairs=dict(incumbent=incumbent, challenger=challenger),
            fixture_executions=executor.executions, labels=labels, setup_hash=batch.setup_hash,
            manifest_hash=batch.manifest_hash, fixture_defaults=batch.fixture_defaults,
            outcome=outcome_b, incumbent_outcome=outcome_a,
            ingestion_receipt=ingestion_b, incumbent_ingestion_receipt=ingestion_a,
            application=log.applications[1][1], request=log.applications[1][0],
            incumbent_application=log.applications[0][1], incumbent_request=log.applications[0][0],
            plan=plan_b, initial_plan=plan_a, old_receipt=receipt_a, new_receipt=receipt_b,
            old_receipt_ref=ref_a, new_receipt_ref=ref_b,
            suppression_request=suppression_request, suppression_decision=suppression_decision,
            graph_before=graph_incumbent, graph_contested=graph_contested, graph_after=graph_after,
            partial_group_visible_memory_ids=sorted({n.memory_id for n in graph_after.nodes}))
    finally:
        await manager.close()

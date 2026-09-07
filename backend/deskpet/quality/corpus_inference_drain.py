"""Isolated fixture settlement for already committed inference setup.

The original ingestion lineage is an input constraint, not a claim that this
local no-mutation executor called that model. Never install in a product worker.
"""
from dataclasses import dataclass

import simple_harness as h
from simple_harness_memory.core.jobs import AnalysisLineage, DurableMemoryJobRunner, WorkerRunOutcome

from deskpet.memory.conversation_registration import PrimaryConversationAuthority
from deskpet.memory.evidence_authority import HostEvidenceAuthority, HostEvidenceUnavailable
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.memory_ingestion_outbox import build_worker_config
from deskpet.quality.corpus_fixture_delivery import CONFIG_HASH, FixtureAnalysisDelivery
from deskpet.task_scope.protocol import canonical_hash


FALLBACK = AnalysisLineage('corpus-fixture-plan', 'no-language-model', CONFIG_HASH)
# Exact accepted setup text, not a model input or a gold-derived target.
C02_19_SETUP = 'A：本人直接声明优先选离线方案；B：系统曾推测本人偏好云端，未确认。'


def _fixture_result(request, identity):
    return h.MemoryAnalysisResult(request.job_id, request.run_id, request.request_hash,
        'fixture-local:' + identity,
        {'outcome': 'no_mutation', 'operations': [],
         'closure_reason': 'synthetic_inference_setup_already_committed'}, 0, 0, 0, 0)


@dataclass(frozen=True)
class AppliedFixtureJob:
    """Public claim/application proof; a decoded candidate is not yet APPLIED."""
    source_id: str
    job_ids: tuple[str, ...]
    request: object
    application: object
    claim: object


class InferenceFixtureAuthority:
    """Supply to build_human_memory_v7 before any ingest, including on reopen."""
    def __init__(self):
        self.executor = None

    def bind(self, executor):
        if self.executor is not None:
            raise ValueError('inference_fixture_authority_already_bound')
        self.executor = executor

    async def verify_analysis_delivery(self, request, envelope):
        if self.executor is None:
            raise ValueError('inference_fixture_authority_unbound')
        await self.executor.verify_committed_seed()
        self.executor.source_for(request)
        await self.executor.verify_analysis_delivery(request, envelope)


class CommittedInferenceExecutor(FixtureAnalysisDelivery):
    def __init__(self, *, case_id, source_path, scoring_path, host_run_id,
                 manager, principal, group, plan, applied, clock):
        if case_id not in {'C02-19', 'C03-20'}:
            raise ValueError('inference_fixture_case_not_supported')
        if len(group.registrations) != 2:
            raise ValueError('inference_fixture_two_sources_required')
        self.case_id, self.source_path, self.host_run_id = case_id, source_path, host_run_id
        self.manager, self.principal, self.group = manager, principal, group
        self.plan, self.applied, self.clock = plan, applied, clock
        self.evidence = HostEvidenceAuthority(scoring_path)
        self.store = HumanMemoryProgramStore(scoring_path)
        self.setup_hash = canonical_hash({'case_id': case_id, 'plan_hash': plan.plan_hash,
            'sources': [r.envelope.envelope_hash for r in group.registrations]})
        self.executions = 0
        self.source_jobs = {}

    def source_for(self, request):
        for index, registration in enumerate(self.group.registrations):
            source = registration.envelope
            lineage = self.group.user_analysis_lineage if index == 0 else FALLBACK
            if (request.subject == self.principal.actor_id == source.subject
                    and request.run_id == source.run_id
                    and request.disclosure_context == source.disclosure_context
                    and request.ordered_evidence_refs == (h.EvidenceRef(source.evidence_id, source.envelope_hash, 1),)
                    and (request.provider_id, request.model_id, request.model_config_hash)
                    == (lineage.provider_id, lineage.model_id, lineage.model_config_hash)):
                return source.evidence_id
        raise ValueError('inference_fixture_request_source_or_lineage_differs')

    async def verify_committed_seed(self):
        from deskpet.quality.corpus_c03 import SETUPS
        expected_text = C02_19_SETUP if self.case_id == 'C02-19' else SETUPS['C03-20'][0]
        if self.group.registrations[0].envelope.sanitized_payload.get('text') != expected_text:
            raise ValueError('inference_fixture_case_source_differs')
        actual = await PrimaryConversationAuthority(self.source_path,
            subject=self.principal.actor_id).registrations_for_run(self.host_run_id)
        if actual != self.group:
            raise ValueError('inference_fixture_original_group_differs')
        refs = tuple(h.EvidenceRef(r.envelope.evidence_id, r.envelope.envelope_hash, i + 1)
            for i, r in enumerate(self.group.registrations))
        if (self.plan.subject != self.principal.actor_id or self.plan.evidence_refs != refs
                or self.plan.run_id != self.group.registrations[0].envelope.run_id):
            raise ValueError('inference_fixture_plan_sources_differ')
        for registration in self.group.registrations:
            pair = registration.envelope, registration.admission_receipt
            if await self.evidence.read_admitted(pair[0].evidence_id) != pair:
                raise ValueError('inference_fixture_copied_source_differs')
        view = await self.manager.get_memory_mutation_receipt_view(
            principal=self.principal, receipt_ref=self.applied.receipt_ref)
        if (view.plan_id != self.plan.plan_id or view.plan_hash != self.plan.plan_hash
                or view.apply_mode != 'strict_atomic'):
            raise ValueError('inference_fixture_commit_differs')
        records = {r.operation_id: r for r in view.operations}
        if set(records) != {op.operation_id for op in self.plan.operations}:
            raise ValueError('inference_fixture_commit_operations_differ')
        for op in self.plan.operations:
            record = records[op.operation_id]
            if (op.kind is not h.MemoryMutationKind.CREATE or record.revision != 1
                    or record.memory_type != op.memory_type.value
                    or record.content_hash != canonical_hash(op.payload.to_json())
                    or record.epistemic_status != op.epistemic_status.value
                    or record.evidence_ids != tuple(span.evidence_id for span in op.evidence_spans)):
                raise ValueError('inference_fixture_committed_operation_differs')

    async def bind_public_jobs(self):
        # Public idempotent ingestion returns the original actual job identity.
        # Never derive it from an ID format or trust caller-supplied claim IDs.
        for index, registration in enumerate(self.group.registrations):
            receipt = await self.manager.ingest_committed_evidence(
                registration.envelope, registration.admission_receipt,
                analysis_lineage=self.group.user_analysis_lineage if index == 0 else None)
            self.source_jobs[registration.envelope.evidence_id] = receipt.mutation_job_id

    def verify_claim(self, claim):
        source_id = self.source_for(claim.request)
        if (claim.job_ids != (self.source_jobs[source_id],)
                or claim.subject != claim.request.subject
                or claim.request.job_id != claim.batch_id):
            raise ValueError('inference_fixture_claim_identity_differs')
        return source_id

    async def analyze_memory(self, request):
        await self.verify_committed_seed()
        self.source_for(request)
        try:
            saved, receipt = await self.evidence.read_admitted(self._identity(request))
        except HostEvidenceUnavailable:
            pass
        else:
            return self._envelope(request, saved, receipt)
        result = _fixture_result(request, self._identity(request))
        self.executions += 1
        return await self.deliver(request, result)

    async def application_is_accepted(self, claim, application):
        """Job APPLIED also covers rejected analyses; require accepted no-op."""
        request = claim.request
        saved, proof = await self.evidence.read_admitted(self._identity(request))
        envelope = self._envelope(request, saved, proof)
        expected = _fixture_result(request, self._identity(request))
        receipt = application.receipt
        return (envelope.result == expected
            and (claim.envelope is None or claim.envelope == envelope)
            and receipt.validation_status is h.AnalysisValidationStatus.ACCEPTED
            and receipt.job_id == claim.batch_id
            and (receipt.job_id, receipt.run_id, receipt.request_hash, receipt.result_hash)
                == (request.job_id, request.run_id, request.request_hash, expected.result_hash)
            and application.decisions == ())


class _ObservedRepository:
    """Forward the public repository protocol; do not replace SDK transactions.

    Captures recovery claims as well: audit_pending can skip the executor.
    Only a successful underlying finalize yields an AppliedFixtureJob.
    """
    def __init__(self, repository, executor, checkpoint=None):
        self.repository, self.executor = repository, executor
        self.checkpoint = checkpoint
        self.applied = []
        self.rejected = []

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return getattr(self.repository, name)

    async def claim_analysis_batch(self, config, worker_id):
        claim = await self.repository.claim_analysis_batch(config, worker_id)
        if claim is not None:
            await self.executor.verify_committed_seed()
            self.executor.verify_claim(claim)
            if claim.envelope is not None:
                # Recovery must use our exact durable fixture delivery, not an
                # earlier real-model application merely sharing source IDs.
                await self.executor.verify_analysis_delivery(claim.request, claim.envelope)
        return claim

    async def finalize_analysis_application(self, claim, application):
        source_id = self.executor.verify_claim(claim)
        candidate = AppliedFixtureJob(source_id, claim.job_ids, claim.request, application, claim)
        if self.checkpoint is not None and await self.executor.application_is_accepted(claim, application):
            # Save the original candidate before the durable finalize boundary.
            # A process can lose the ACK after commit; on restart the SDK, not
            # this file, decides whether finalization has actually completed.
            self.checkpoint.save_candidate(candidate)
        finalized = await self.repository.finalize_analysis_application(claim, application)
        if finalized:
            if await self.executor.application_is_accepted(claim, application):
                self.applied.append(candidate)
            else:
                self.rejected.append(application)
        return finalized


async def drain_inference_setup(*, authority, prior_applied=(), proof_path=None, **kwargs):
    """Two bounded real runner calls; IDLE/backoff never fills missing proof.

    Retained original public claims/applications may be revalidated by SDK's
    idempotent finalize after reopen. These values are not trusted completion
    flags. No proof is inferred from queue absence.
    """
    executor = CommittedInferenceExecutor(**kwargs)
    await executor.verify_committed_seed()
    checkpoint = None
    if proof_path is not None:
        from deskpet.quality.corpus_inference_checkpoint import FixtureRecoveryFile
        if prior_applied:
            raise ValueError('inference_fixture_two_recovery_inputs')
        binding = dict(case_id=executor.case_id, subject=executor.principal.actor_id,
            host_run_id=executor.host_run_id, plan_hash=executor.plan.plan_hash,
            sources=[dict(evidence_id=r.envelope.evidence_id, envelope_hash=r.envelope.envelope_hash,
                receipt_hash=r.admission_receipt.receipt_hash) for r in executor.group.registrations])
        checkpoint = FixtureRecoveryFile(proof_path, binding=binding)
        prior_applied = checkpoint.load()
    await executor.bind_public_jobs()
    authority.bind(executor)
    repository = _ObservedRepository(kwargs['manager'].backend, executor, checkpoint)
    restored = set()
    for previous in prior_applied:
        if type(previous) is not AppliedFixtureJob:
            raise ValueError('inference_fixture_prior_proof_type')
        claim = previous.claim
        source_id = executor.verify_claim(claim)
        if (previous.request != claim.request or previous.job_ids != claim.job_ids
                or previous.source_id != source_id or source_id in restored):
            raise ValueError('inference_fixture_prior_proof_differs')
        saved, receipt = await executor.evidence.read_admitted(executor._identity(claim.request))
        executor._envelope(claim.request, saved, receipt)
        if not await executor.application_is_accepted(claim, previous.application):
            raise ValueError('inference_fixture_prior_application_not_accepted')
        if not await repository.finalize_analysis_application(claim, previous.application):
            if checkpoint is None:
                raise ValueError('inference_fixture_prior_finalize_unconfirmed')
            # Candidate saved before finalize may have an expired lease. Let
            # the actual SDK claim/recovery protocol recover it below; do not
            # count this candidate as applied or invent a replacement claim.
            continue
        restored.add(source_id)
    config = build_worker_config(provider_id=FALLBACK.provider_id,
        model_id=FALLBACK.model_id, model_config_hash=FALLBACK.model_config_hash)
    runner = DurableMemoryJobRunner(repository, executor, authority, config,
        'corpus-inference-fixture-drain', kwargs['clock'])
    outcomes = []
    for _ in range(len(executor.group.registrations) - len(restored)):
        outcome = await runner.run_once()
        outcomes.append(outcome)
        if outcome is not WorkerRunOutcome.APPLIED or repository.rejected:
            break
    expected = {r.envelope.evidence_id for r in executor.group.registrations}
    observed = [item.source_id for item in repository.applied]
    confirmed = len(observed) == len(expected) and set(observed) == expected
    return dict(confirmed=confirmed, outcomes=tuple(outcomes), applied=tuple(repository.applied),
        rejected_applications=tuple(repository.rejected),
        fixture_executions=executor.executions,
        reason=None if confirmed else 'inference_fixture_settlement_unconfirmed')

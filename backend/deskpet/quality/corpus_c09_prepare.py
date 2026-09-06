"""Actual C09 analysis CREATE plus public source-bound REVISE/SUPERSEDE.

This exact fixture action issuer is scoped to one compiled setup and is closed
before any production consumer opens the Memory store. No oracle/Provider input.
"""
from contextlib import asynccontextmanager
from dataclasses import replace

import simple_harness as h
import simple_harness_memory as m
from simple_harness_memory.core.jobs import AnalysisLineage, DurableMemoryJobRunner, WorkerRunOutcome
from deskpet.memory.analysis_proposal import admitted_item, derive_span
from deskpet.memory.evidence_authority import HostEvidenceAuthority, HostEvidenceUnavailable
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import build_foreground_turn_evidence
from deskpet.memory.memory_ingestion_outbox import build_worker_config
from deskpet.quality.corpus_c01 import payload
from deskpet.quality.corpus_c04_prepare import _ApplicationWitness
from deskpet.quality.corpus_c09 import validate_c09_setup
from deskpet.quality.corpus_fixture_delivery import CONFIG_HASH
from deskpet.quality.corpus_setup_jobs import FixtureSetupExecutor, SetupFixtureDeliveryAuthority
from deskpet.task_scope.protocol import canonical_hash

POLICY = 'host-corpus-c09-revision/v1'


class SupersededFixtureExecutor(FixtureSetupExecutor):
    def __init__(self, *, path, batch, source_pair, principal, clock):
        validate_c09_setup(batch)
        self.batch, self.source_pair, self.principal, self.clock = batch, source_pair, principal, clock
        self.evidence, self.store = HostEvidenceAuthority(path), HumanMemoryProgramStore(path)
        self.executions, self.setup_hash, self.executed_plan = 0, batch.manifest_hash, None

    async def analyze_memory(self, request):
        envelope = await super().analyze_memory(request)
        self.executed_plan = h.MemoryMutationPlan.from_json(h.thaw_json(envelope.result.structured_result))
        return envelope


def _successor_plan(batch, original, prior, committed_revision, source, proof):
    validate_c09_setup(batch)
    by_label = {op.operation_id: op for op in prior.operations}
    span = replace(derive_span(admitted_item(source, proof), batch.setup_text,
        span_id='c09-explicit-correction'), support_kind=h.EvidenceSupportKind.EXPLICIT_USER_CORRECTION)
    operations = []
    for i, (predicate, old, new, qualifiers) in enumerate(batch.changes):
        label = f'old-{i}'
        target = by_label[label]
        create = next(op for op in original.operations if op.operation_id == label)
        operations.append(replace(create, operation_id=f'successor-{i}',
            kind=h.MemoryMutationKind.SUPERSEDE if new is None else h.MemoryMutationKind.REVISE,
            target=h.ExistingMemoryTarget(target.memory_id, target.revision),
            payload=h.SemanticMemoryPayload('user:self', predicate, old if new is None else new, qualifiers),
            lifecycle_state=h.SemanticLifecycleState.SUPERSEDED if new is None else h.SemanticLifecycleState.ACTIVE,
            evidence_spans=(span,)))
    identity = 'corpus-c09-successor:' + canonical_hash([batch.manifest_hash, prior.plan_hash,
        [(op.memory_id, op.revision) for op in prior.operations]])
    return replace(original, plan_id=identity, turn_id=identity, idempotency_key=identity,
        base_revision=committed_revision, operations=tuple(operations))


class SupersededActionAuthority:
    def __init__(self, *, path, batch, principal, manager_getter):
        validate_c09_setup(batch)
        self.batch, self.principal, self.manager_getter = batch, principal, manager_getter
        self.evidence, self.store = HostEvidenceAuthority(path), HumanMemoryProgramStore(path)

    async def authorize(self, *, plan, original, prior_ref, committed_revision, source, proof):
        if (await self.evidence.read_admitted(source.evidence_id) != (source, proof)
                or source.subject != self.principal.actor_id
                or admitted_item(source, proof).text != self.batch.setup_text
                or original.subject != self.principal.actor_id or original.run_id != source.run_id
                or original.disclosure_context != source.disclosure_context
                or original.evidence_refs != (h.EvidenceRef(source.evidence_id, source.envelope_hash, 1),)):
            raise ValueError('c09_revision_original_source_differs')
        manager = self.manager_getter()
        prior = await manager.get_memory_mutation_receipt_view(principal=self.principal, receipt_ref=prior_ref)
        expected = {spec[0]: canonical_hash(payload(spec, self.batch.scenario_time).to_json()) for spec in self.batch.specs}
        if (prior.plan_hash != original.plan_hash or len(prior.operations) != len(expected)
                or {op.operation_id for op in prior.operations} != set(expected)
                or any(op.memory_type != 'semantic' or op.revision != 1
                    or op.content_hash != expected[op.operation_id]
                    or op.evidence_ids != (source.evidence_id,) for op in prior.operations)):
            raise ValueError('c09_revision_prior_not_authored')
        if plan != _successor_plan(self.batch, original, prior, committed_revision, source, proof):
            raise ValueError('c09_revision_exact_successor_required')
        graph = await manager.get_twin_graph_view(principal=self.principal)
        if {(n.memory_id, n.revision, n.content_hash) for n in graph.nodes} != {
                (op.memory_id, op.revision, op.content_hash) for op in prior.operations}:
            raise ValueError('c09_revision_original_current_heads_differ')
        authorized = []
        now = self.batch.scenario_time
        for operation in plan.operations:
            intent = plan.action_intent(operation.operation_id)
            identity = 'corpus-c09-action:' + intent.intent_hash
            authority = h.issue_memory_action_authority(intent, authority_id=identity,
                issued_at=now, expires_at=now + 300, nonce=identity, issuer_ref=POLICY)
            body = dict(authority=authority.to_json(), prior_receipt=prior_ref.to_json(),
                setup_hash=self.batch.setup_hash, manifest_hash=self.batch.manifest_hash)
            digest = canonical_hash(body)
            saved = h.SanitizedEvidenceEnvelope(evidence_id=identity, run_id=plan.run_id,
                subject=plan.subject, source_kind=h.EvidenceSourceKind.RUNTIME_EVENT,
                source_ref=identity, source_hash=digest, sanitized_payload=body, sanitized_hash=digest,
                filter_policy_version=POLICY, removed_spans=(), disclosure_context=plan.disclosure_context,
                evidence_refs=plan.evidence_refs)
            receipt = h.SanitizedEvidenceReceipt(receipt_id='receipt:' + identity, run_id=plan.run_id,
                subject=plan.subject, evidence_id=identity, envelope_hash=saved.envelope_hash,
                source_hash=digest, sanitized_hash=digest, filter_policy_version=POLICY, accepted=True,
                reason_codes=(h.EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
                disclosure_context=plan.disclosure_context, evidence_refs=plan.evidence_refs, admitted_at=now)
            await self.store.append_evidence(saved, receipt)
            authorized.append(replace(operation, action_authority_ref=h.MemoryActionAuthorityRef.from_authority(authority)))
        return replace(plan, operations=tuple(authorized))

    async def resolve_memory_action_authority(self, reference):
        saved, _ = await self.evidence.read_admitted(reference.authority_id)
        body = h.thaw_json(saved.sanitized_payload)
        if (saved.subject != self.principal.actor_id or saved.filter_policy_version != POLICY
                or saved.source_kind is not h.EvidenceSourceKind.RUNTIME_EVENT or reference.issuer_ref != POLICY
                or body.get('manifest_hash') != self.batch.manifest_hash):
            raise ValueError('c09_action_authority_domain_differs')
        authority = h.MemoryActionAuthority.from_json(body['authority'])
        if h.MemoryActionAuthorityRef.from_authority(authority) != reference:
            raise ValueError('c09_action_authority_reference_differs')
        return authority


@asynccontextmanager
async def open_c09_fixture(*, path, memory_path, principal, authority_ref, batch,
        classification_policy, supported_filter_policies):
    validate_c09_setup(batch)
    clock = lambda: batch.scenario_time
    delivery = SetupFixtureDeliveryAuthority()
    manager = None
    action = SupersededActionAuthority(path=path, batch=batch, principal=principal, manager_getter=lambda: manager)
    manager = await m.build_human_memory_v7(memory_path, classification_policy=classification_policy,
        supported_filter_policies=supported_filter_policies, evidence_authority=HostEvidenceAuthority(path),
        analysis_delivery_authority=delivery, memory_action_authority=action, clock=clock)
    try:
        source, proof = build_foreground_turn_evidence(subject=principal.actor_id, authority_ref=authority_ref,
            delivery_key='corpus-fixture:' + batch.case_id + ':' + batch.manifest_hash, text=batch.setup_text)
        proof = replace(proof, admitted_at=batch.scenario_time)
        await HumanMemoryProgramStore(path).append_evidence(source, proof)
        if await HostEvidenceAuthority(path).read_admitted(source.evidence_id) != (source, proof):
            raise ValueError('c09_setup_source_readback_differs')
        scope = m.MemoryScope.personal(principal.actor_id)
        await manager.register_principal_owner(principal, scope)
        executor = SupersededFixtureExecutor(path=path, batch=batch, source_pair=(source, proof),
            principal=principal, clock=clock)
        delivery.bind(executor)
        ingestion = await manager.ingest_committed_evidence(source, proof,
            analysis_lineage=AnalysisLineage('corpus-fixture-plan', 'no-language-model', CONFIG_HASH))
        witness = _ApplicationWitness(manager.backend)
        runner = DurableMemoryJobRunner(witness, executor, delivery, build_worker_config(
            provider_id='corpus-fixture-plan', model_id='no-language-model', model_config_hash=CONFIG_HASH),
            'corpus-c09-setup', clock)
        outcome = await runner.run_once()
        if (outcome is not WorkerRunOutcome.APPLIED or witness.application is None
                or witness.application.receipt.validation_status is not h.AnalysisValidationStatus.ACCEPTED
                or executor.executed_plan is None or executor.executions != 1):
            raise ValueError('c09_accepted_original_job_required')
        original = executor.executed_plan
        created = await manager.apply_memory_mutation_plan(principal=principal, scope=scope, plan=original)
        if created.outcome is not h.MemoryMutationApplyOutcome.COMMITTED or created.receipt_ref is None:
            raise ValueError('c09_original_public_receipt_required')
        prior = await manager.get_memory_mutation_receipt_view(principal=principal, receipt_ref=created.receipt_ref)
        graph_before = await manager.get_twin_graph_view(principal=principal)
        plan = _successor_plan(batch, original, prior, witness.application.receipt.committed_revision, source, proof)
        plan = await action.authorize(plan=plan, original=original, prior_ref=created.receipt_ref,
            committed_revision=witness.application.receipt.committed_revision, source=source, proof=proof)
        changed = await manager.apply_memory_mutation_plan(principal=principal, scope=scope, plan=plan)
        if changed.outcome is not h.MemoryMutationApplyOutcome.COMMITTED or changed.receipt_ref is None:
            raise ValueError('c09_successor_not_committed')
        successor = await manager.get_memory_mutation_receipt_view(principal=principal, receipt_ref=changed.receipt_ref)
        original_by_label = {op.operation_id: op for op in prior.operations}
        if successor.plan_hash != plan.plan_hash or len(successor.operations) != len(batch.changes):
            raise ValueError('c09_successor_receipt_differs')
        for i, operation in enumerate(successor.operations):
            old = original_by_label[f'old-{i}']
            if (operation.operation_id != f'successor-{i}' or operation.memory_id != old.memory_id
                    or operation.revision != 2 or operation.evidence_ids != (source.evidence_id,)
                    or operation.content_hash != canonical_hash(plan.operations[i].payload.to_json())):
                raise ValueError('c09_same_memory_successor_required')
        if await runner.run_once() is not WorkerRunOutcome.IDLE:
            raise ValueError('c09_original_job_not_settled')
        graph_after = await manager.get_twin_graph_view(principal=principal)
        # Preparation receipts only. Labels describe the authored history, not
        # gold targets or Provider-visible memory references.
        labels = {op.operation_id: op for op in prior.operations}
        labels.update({op.operation_id: op for op in successor.operations})
        yield manager, dict(batch=batch, source_pair=(source, proof), fixture_executions=executor.executions,
            labels=labels,
            setup_hash=batch.setup_hash, manifest_hash=batch.manifest_hash, outcome=outcome,
            ingestion_receipt=ingestion, application=witness.application, request=witness.request,
            initial_plan=original, plan=plan, old_receipt=prior, new_receipt=successor,
            old_receipt_ref=created.receipt_ref, new_receipt_ref=changed.receipt_ref,
            graph_before=graph_before, graph_after=graph_after)
    finally:
        await manager.close()

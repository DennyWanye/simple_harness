"""Public C06 fixture preparation; never production/model analysis authority.

The builder must receive delivery_authority before construction. This helper
creates no TaskScope, foreground history or fabricated terminal. Consumers must
obtain current principals/disclosure and actual typed recall from production.
"""
from dataclasses import replace
from hashlib import sha256
import time

import simple_harness as h
from simple_harness_memory import MemoryScope
from simple_harness_memory.core.jobs import (
    AnalysisLineage, DurableMemoryJobRunner, WorkerRunOutcome, current_analysis_apply_head,
)
from deskpet.memory.analysis_proposal import admitted_item, derive_span, stable_id
from deskpet.memory.evidence_authority import HostEvidenceAuthority, HostEvidenceUnavailable
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import build_foreground_turn_evidence
from deskpet.memory.memory_ingestion_outbox import build_worker_config
from deskpet.quality.corpus_c06 import SPECS, validate_batch
from deskpet.quality.corpus_fixture_delivery import FixtureAnalysisDelivery, CONFIG_HASH
from deskpet.task_scope.protocol import canonical_hash


def operations_for(batch, pair):
    validate_batch(batch)
    item = admitted_item(*pair)
    if item.text != batch.setup_text:
        raise ValueError('corpus_c06_admitted_setup_differs')
    semantic_quote, predicate, value, procedure_quote, title, conditions, steps = SPECS[batch.case_id]
    semantic = h.MemoryMutationOperation(
        operation_id='S', kind=h.MemoryMutationKind.CREATE,
        memory_type=h.LongTermMemoryType.SEMANTIC,
        payload=h.SemanticMemoryPayload('user:self', predicate, value, ()),
        target=None, depends_on_operation_ids=(), lifecycle_state=h.SemanticLifecycleState.ACTIVE,
        epistemic_status=h.EpistemicStatus.EXPLICIT_USER,
        conflict_status=h.ConflictStatus.UNCONTESTED,
        verification_state=h.VerificationState.SOURCE_BOUND,
        valid_time_interval=h.ValidTimeInterval(None, None),
        proposed_privacy_class=h.PrivacyClass.PERSONAL,
        proposed_information_attributes=(h.InformationAttribute.PREFERENCE,),
        evidence_spans=(derive_span(item, semantic_quote, span_id='setup-S'),),
        reason_code='explicit_user_assertion',
    )
    procedure = replace(semantic, operation_id='P',
        memory_type=h.LongTermMemoryType.PROCEDURE,
        payload=h.ProcedureMemoryPayload(title, conditions, steps, h.ProcedureRiskLevel.LOW),
        lifecycle_state=h.ProcedureLifecycleState.ACTIVE,
        evidence_spans=(derive_span(item, procedure_quote, span_id='setup-P'),))
    if batch.case_id == 'C06-17':
        # Reuse the approved common undated-episode convention. The literal
        # old-project claim is preserved; no actual Task/closure is fabricated.
        from deskpet.quality.corpus_c03_dates import c03_payload
        spec = ('E', 'episode', 'user:self', '旧项目跳过抽样导致出错',
            '旧项目跳过抽样导致出错', ())
        episode = replace(semantic, operation_id='E',
            memory_type=h.LongTermMemoryType.EPISODE, payload=c03_payload(batch, spec),
            lifecycle_state=h.EpisodeLifecycleState.ACTIVE,
            proposed_information_attributes=(),
            evidence_spans=(derive_span(item, spec[4], span_id='setup-E'),))
        return semantic, procedure, episode
    if batch.case_id in ('C06-18', 'C06-19'):
        first = replace(procedure, operation_id='P1',
            evidence_spans=(derive_span(item, procedure_quote, span_id='setup-P1'),))
        if batch.case_id == 'C06-18':
            quote = '仅财务项目先套专有账模板'
            second_payload = h.ProcedureMemoryPayload('财务专有账模板',
                ('仅财务项目',), ('先套专有账模板',), h.ProcedureRiskLevel.LOW)
        else:
            quote = '需联网插件的active程序'
            # Source gives a capability requirement but no actionable algorithm.
            # Keep that limitation explicit, never invent plugin/tool/arguments.
            second_payload = h.ProcedureMemoryPayload('需联网插件的程序（原文未提供具体步骤）',
                ('需联网插件',), ('需联网插件的active程序',), h.ProcedureRiskLevel.LOW)
        second = replace(procedure, operation_id='P2', payload=second_payload,
            evidence_spans=(derive_span(item, quote, span_id='setup-P2'),))
        if batch.case_id == 'C06-18':
            return semantic, first, second
        environment = replace(semantic, operation_id='ENV',
            payload=h.SemanticMemoryPayload('device:current', '网络状态', '离线',
                ('setup时点声明，非运行时设备观测',)),
            proposed_information_attributes=(),
            valid_time_interval=h.ValidTimeInterval(batch.scenario_time, None),
            evidence_spans=(derive_span(item, '当前设备离线', span_id='setup-ENV'),))
        return semantic, first, second, environment
    return semantic, procedure


class C06FixtureExecutor(FixtureAnalysisDelivery):
    def __init__(self, *, path, batch, pair, principal):
        validate_batch(batch)
        self.batch, self.pair, self.principal = batch, pair, principal
        self.setup_hash = batch.setup_hash
        self.clock = lambda: batch.scenario_time
        self.evidence, self.store = HostEvidenceAuthority(path), HumanMemoryProgramStore(path)
        self.executions = 0
        self.executed_plan = None

    async def analyze_memory(self, request):
        actual = await self.evidence.read_admitted(self.pair[0].evidence_id)
        source, receipt = actual
        if (actual != self.pair or source.subject != self.principal.actor_id
                or request.subject != source.subject or request.run_id != source.run_id
                or request.ordered_evidence_refs != (h.EvidenceRef(source.evidence_id, source.envelope_hash, 1),)
                or request.provider_id != 'corpus-fixture-plan' or request.model_id != 'no-language-model'
                or request.model_config_hash != CONFIG_HASH):
            raise ValueError('corpus_c06_request_not_setup')
        identity = self._identity(request)
        try:
            saved, proof = await self.evidence.read_admitted(identity)
        except HostEvidenceUnavailable:
            pass
        else:
            envelope = self._envelope(request, saved, proof)
            self.executed_plan = h.MemoryMutationPlan.from_json(h.thaw_json(envelope.result.structured_result))
            return envelope
        started = time.monotonic()
        head = current_analysis_apply_head()
        if type(head) is not int:
            raise ValueError('corpus_c06_actual_claim_head_required')
        plan = h.MemoryMutationPlan(identity, request.run_id, stable_id('analysis-batch-turn', request.job_id),
            request.subject, head, h.MemoryMutationPlanOutcome.MUTATE, operations_for(self.batch, actual),
            request.disclosure_context, request.ordered_evidence_refs, request.idempotency_key)
        result = h.MemoryAnalysisResult(request.job_id, request.run_id, request.request_hash,
            'fixture-local:' + identity, plan.to_json(), 0, 0, 0, int((time.monotonic()-started)*1000))
        envelope = await self.deliver(request, result)
        self.executed_plan = plan
        self.executions += 1
        return envelope


async def read_c06_preparation(*, manager, principal, batch, pair):
    """Acceptance-only display readback; NOT a source of Provider input.

    A same-payload foreign memory or unrelated prior source cannot satisfy it.
    Public typed recall and final visibility checks remain required at runtime.
    """
    if pair[0].subject != principal.actor_id:
        raise ValueError('corpus_c06_source_owner_differs')
    graph = await manager.get_twin_graph_view(principal=principal)
    labels = {}
    for operation in operations_for(batch, pair):
        digest = canonical_hash(operation.payload.to_json())
        span = operation.evidence_spans[0]
        matches = [node for node in graph.nodes
            if node.memory_type == operation.memory_type.value and node.revision == 1
            and node.content_hash == digest and node.lifecycle_state == 'active'
            and node.status == 'active' and node.conflict_status == 'uncontested' and not node.redacted
            and node.epistemic_status == 'explicit_user' and node.verification_state == 'source_bound'
            and len(node.source_refs) == 1
            and node.source_refs[0].quote_hash == span.quote_hash
            and node.source_refs[0].evidence_ref_hash == sha256(span.evidence_id.encode()).hexdigest()
            and node.source_refs[0].span_ref_hash == sha256(span.span_id.encode()).hexdigest()
            and node.source_refs[0].source_kind == span.source_kind.value]
        if len(matches) != 1:
            raise ValueError('corpus_c06_public_readback_missing_or_ambiguous')
        labels[operation.operation_id] = matches[0]
    return labels


async def prepare_c06_setup(*, path, manager, principal, authority_ref, batch, delivery_authority):
    validate_batch(batch)
    envelope, receipt = build_foreground_turn_evidence(subject=principal.actor_id,
        authority_ref=authority_ref, delivery_key='corpus-fixture:'+batch.case_id+':'+batch.setup_hash,
        text=batch.setup_text)
    receipt = replace(receipt, admitted_at=batch.scenario_time)
    pair = envelope, receipt
    store = HumanMemoryProgramStore(path)
    await store.append_evidence(*pair)
    if await HostEvidenceAuthority(path).read_admitted(envelope.evidence_id) != pair:
        raise ValueError('corpus_c06_source_readback_differs')
    await manager.register_principal_owner(principal, MemoryScope.personal(principal.actor_id))
    executor = C06FixtureExecutor(path=path, batch=batch, pair=pair, principal=principal)
    delivery_authority.bind(executor)
    await manager.ingest_committed_evidence(*pair, analysis_lineage=AnalysisLineage(
        'corpus-fixture-plan', 'no-language-model', CONFIG_HASH))
    config = build_worker_config(provider_id='corpus-fixture-plan', model_id='no-language-model', model_config_hash=CONFIG_HASH)
    runner = DurableMemoryJobRunner(manager.backend, executor, delivery_authority, config,
        'corpus-c06-fixture-worker', lambda: batch.scenario_time)
    outcome = await runner.run_once()
    if outcome is not WorkerRunOutcome.APPLIED or executor.executed_plan is None:
        raise ValueError('corpus_c06_settlement_unconfirmed:'+outcome.value)
    labels = await read_c06_preparation(manager=manager, principal=principal, batch=batch, pair=pair)
    return dict(case_id=batch.case_id, setup_hash=batch.setup_hash, source_pair=pair,
        labels=labels, plan=executor.executed_plan, outcome=outcome,
        fixture_executions=executor.executions,
        source_limits={
            'C06-17': ('undated_episode_uses_labelled_synthetic_time',),
            'C06-18': ('finance_applicability_requires_actual_runtime_facts',),
            'C06-19': ('online_procedure_steps_unspecified',
                'setup_offline_declaration_is_not_runtime_capability_authority'),
        }.get(batch.case_id, ()))

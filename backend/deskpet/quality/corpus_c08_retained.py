"""C08 retained USER/assistant sources: exact fixtures, real Host/SDK facts.

No oracle/current query input, terminal insertion, synthetic summary receipt or
SDK private SQL. The caller owns the actual runtime and manager lifecycle.
"""
from dataclasses import dataclass
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import time

import simple_harness as h
import simple_harness_memory as m
from simple_harness_memory.core.jobs import DurableMemoryJobRunner, WorkerRunOutcome, current_analysis_apply_head
from deskpet.memory.analysis_proposal import admitted_item, derive_span, stable_id
from deskpet.memory.conversation_registration import PrimaryConversationAuthority
from deskpet.memory.evidence_authority import HostEvidenceAuthority, HostEvidenceUnavailable
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.memory_ingestion_outbox import build_worker_config
from deskpet.memory.short_indexing import PrimaryShortIndexingService
from deskpet.quality.corpus_c08 import SETUPS
from deskpet.quality.corpus_c04_prepare import _ApplicationWitness
from deskpet.quality.corpus_fixture_delivery import FixtureAnalysisDelivery
from deskpet.quality.corpus_runtime import execute_scoring_turn
from deskpet.task_scope.protocol import canonical_hash


FIXTURE_PROVIDER_ID = 'corpus-retained-fixture'
# Authored setup realizations, not quotations supplied by the original dataset
# except C08-18's exact old sentence. No additional biographical facts are added.
RETAINED = {
    'C08-01': ('address', '梧桐巷42号测试楼',
        '我的地址是梧桐巷42号测试楼。', '地址摘要：梧桐巷42号测试楼。'),
    'C08-06': ('storage_note', '物品藏在绿柜',
        '物品藏在绿柜', '备注摘要：物品藏在绿柜。'),
    'C08-11': ('school', '青麦学堂',
        '我的学校是青麦学堂。', '旧介绍：学校为青麦学堂。'),
    'C08-18': ('colour_choice', '我以后只选深绿色',
        '我以后只选深绿色', '偏好摘要：以后只选深绿色。'),
}


@dataclass(frozen=True)
class RetainedSetup:
    case_id: str
    setup_text: str
    setup_hash: str
    scenario_time: float
    predicate: str
    value: str
    messages: tuple[tuple[str, str], ...]
    manifest_hash: str


def compile_c08_retained_setup(case_id, setup_text, *, scenario_clock):
    if type(case_id) is not str or case_id not in RETAINED:
        raise ValueError('c08_retained_case_not_supported')
    text, digest = SETUPS[case_id]
    if type(setup_text) is not str or setup_text != text or sha256(setup_text.encode()).hexdigest() != digest:
        raise ValueError('c08_setup_source_changed')
    instant = datetime.fromisoformat(scenario_clock)
    if instant.tzinfo is None or not math.isfinite(instant.timestamp()) or instant.timestamp() < 0:
        raise ValueError('c08_retained_trusted_clock_required')
    predicate, value, user, assistant = RETAINED[case_id]
    messages = (('user', user), ('assistant', assistant))
    manifest = canonical_hash(dict(domain='host:corpus-c08-retained/v1', case_id=case_id,
        setup_hash=digest, scenario_time=instant.timestamp(), predicate=predicate, value=value,
        messages=messages, realization='authored-setup-only-old-group'))
    return RetainedSetup(case_id, text, digest, instant.timestamp(), predicate, value, messages, manifest)


def validate_retained_setup(batch):
    if type(batch) is not RetainedSetup or batch != compile_c08_retained_setup(
            batch.case_id, batch.setup_text, scenario_clock=datetime.fromtimestamp(
                batch.scenario_time, timezone.utc).isoformat()):
        raise ValueError('c08_retained_exact_manifest_required')


class RetainedSummaryProvider:
    """Single loopback response through the real production HTTP adapter.

    Deliberately C08-only. No network proxy, credentials, tools, retries or
    scoring request accepted. A failed setup requires a new evidence directory.
    """
    _key = 'corpus-retained-local-not-a-credential'

    def __init__(self, batch, *, model):
        validate_retained_setup(batch)
        self.batch, self.model = batch, model
        self.attempts = 0
        self.accepted_request_hash = None
        self.base_url = None
        self._server, self._started, self._tasks = None, False, set()

    async def start(self):
        if self._started:
            raise ValueError('c08_retained_provider_cannot_restart')
        self._started = True
        self._server = await asyncio.start_server(self._connected, '127.0.0.1', 0, limit=65536)
        self.base_url = f'http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}/v1'
        return self

    def registration(self):
        if self._server is None:
            raise ValueError('c08_retained_provider_not_listening')
        return dict(id=FIXTURE_PROVIDER_ID, base_url=self.base_url, api_key=self._key,
            models=[self.model], priority=2)

    def _connected(self, reader, writer):
        task = asyncio.create_task(self._serve(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _serve(self, reader, writer):
        try:
            async with asyncio.timeout(5):
                header = await reader.readuntil(b'\r\n\r\n')
                self.attempts += 1
                lines = header.decode('ascii').split('\r\n')
                headers = {}
                for line in lines[1:]:
                    if not line:
                        continue
                    key, value = line.split(':', 1)
                    if key.lower() in headers:
                        raise ValueError('duplicate_header')
                    headers[key.lower()] = value.strip()
                if (self.attempts != 1 or lines[0] != 'POST /v1/chat/completions HTTP/1.1'
                        or headers.get('authorization') != 'Bearer ' + self._key
                        or 'transfer-encoding' in headers):
                    raise ValueError('fixture_request_binding')
                length = int(headers['content-length'])
                if not 0 < length <= 2 * 1024 * 1024:
                    raise ValueError('fixture_body_limit')
                body = await reader.readexactly(length)
                payload = json.loads(body)
                messages = payload.get('messages') if type(payload) is dict else None
                if (type(messages) is not list or payload.get('model') != self.model
                        or any(type(row) is not dict for row in messages)
                        or [(row.get('role'), row.get('content')) for row in messages
                            if row.get('role') != 'system'] != [self.batch.messages[0]]):
                    raise ValueError('fixture_original_user_differs')
                self.accepted_request_hash = sha256(body).hexdigest()
                await self._reply(writer, 200, dict(
                    id='c08-retained-response-' + self.accepted_request_hash,
                    object='chat.completion', model=self.model,
                    choices=[dict(index=0, message=dict(role='assistant', content=self.batch.messages[1][1]),
                        finish_reason='stop')], usage=dict(prompt_tokens=0, completion_tokens=0, total_tokens=0)))
        except asyncio.CancelledError:
            raise
        except (ValueError, TypeError, KeyError, UnicodeError, TimeoutError,
                asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            try:
                await self._reply(writer, 409, {'error': {'message': 'c08_retained_request_rejected'}})
            except (ConnectionError, OSError):
                pass
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    @staticmethod
    async def _reply(writer, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
        writer.write(f'HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body)
        await writer.drain()

    def verify_completed_phase(self, *, observations, binding):
        trace = observations.get('trace') or {}
        providers = trace.get('providers', ())
        if (self.attempts != 1 or self.accepted_request_hash is None
                or observations.get('observation_errors')
                or trace.get('trace_status') != 'COMPLETE' or trace.get('terminal_status') != 'TERMINAL'
                or trace.get('provider_observation_complete') is not True or len(providers) != 1
                or providers[0]['state'] != 'succeeded' or providers[0]['handed_off_at'] is None
                or providers[0]['handoff_attempt'] != 1 or providers[0]['rehandoff_count'] != 0
                or binding.provider_id != FIXTURE_PROVIDER_ID or binding.run_id != observations.get('sdk_run_id')
                or binding.model_id != self.model
                or (providers[0].get('response_json') or {}).get('provider_request_id')
                   != 'c08-retained-response-' + self.accepted_request_hash):
            raise ValueError('c08_retained_phase_actual_trace_incomplete')
        return dict(kind='deterministic_retained_summary', provider_id=FIXTURE_PROVIDER_ID,
            host_run_id=observations['host_run_id'], sdk_run_id=observations['sdk_run_id'],
            provider_invocation_id=providers[0]['invocation_id'], request_sha256=self.accepted_request_hash,
            fixture_http_requests=self.attempts, real_model_calls=0, trace_hash=trace['trace_hash'])

    async def close(self):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def _read_group(*, path, subject, batch, executed):
    validate_retained_setup(batch)
    authority = PrimaryConversationAuthority(path, subject=subject)
    group = await authority.registrations_for_run(executed.completed_group.host_run_id)
    if group != executed.completed_group:
        raise ValueError('c08_retained_group_not_original')
    terminal = group.terminal_source[0].sanitized_payload
    if (terminal.get('terminal_state') != 'COMPLETED'
            or terminal.get('turn_id') != executed.queue_receipt['turn_ref']
            or tuple((row['role'], row['content']) for row in terminal['messages']) != batch.messages
            or len(group.registrations) != 2
            or tuple(r.metadata.role.value for r in group.registrations) != ('user', 'assistant')
            or group.user_analysis_lineage.provider_id != FIXTURE_PROVIDER_ID):
        raise ValueError('c08_retained_actual_terminal_or_fixture_binding_differs')
    user = group.registrations[0]
    if user.envelope.sanitized_payload.get('delivery_key') != 'c08-retained:' + batch.manifest_hash:
        raise ValueError('c08_retained_original_queue_source_differs')
    return authority, group


async def execute_c08_retained_group(*, path, subject, batch, service, runtime, ingestion_worker):
    """Run the setup pair through actual enqueue/SDK/outbox, never write history.

    Caller selects the dedicated deterministic fixture Provider for this phase
    and captures failed/nonterminal trace before attempting any later scoring.
    """
    validate_retained_setup(batch)
    authority = PrimaryConversationAuthority(path, subject=subject)
    if await authority.completed_run_ids() or (await service.queue_snapshot())['turns']:
        raise ValueError('c08_retained_requires_isolated_empty_history')
    executed = await execute_scoring_turn(service=service, runtime=runtime, scoring_path=path,
        subject=subject, text=batch.messages[0][1], delivery_key='c08-retained:' + batch.manifest_hash,
        ingestion_worker=ingestion_worker)
    await _read_group(path=path, subject=subject, batch=batch, executed=executed)
    return executed


class RetainedAnalysisExecutor(FixtureAnalysisDelivery):
    """Settle the actual old USER job, using its ORIGINAL delivered lineage."""
    def __init__(self, *, path, batch, group, principal):
        validate_retained_setup(batch)
        self.batch, self.group, self.principal = batch, group, principal
        self.evidence, self.store = HostEvidenceAuthority(path), HumanMemoryProgramStore(path)
        self.clock = lambda: batch.scenario_time
        self.setup_hash = batch.manifest_hash
        self.executions, self.executed_plan = 0, None

    async def analyze_memory(self, request):
        registration = self.group.registrations[0]
        pair = (registration.envelope, registration.admission_receipt)
        source, proof = await self.evidence.read_admitted(registration.envelope.evidence_id)
        lineage = self.group.user_analysis_lineage
        if ((source, proof) != pair or source.subject != self.principal.actor_id
                or request.subject != self.principal.actor_id or request.run_id != source.run_id
                or request.ordered_evidence_refs != (h.EvidenceRef(source.evidence_id, source.envelope_hash, 1),)
                or lineage.provider_id != FIXTURE_PROVIDER_ID
                or (request.provider_id, request.model_id, request.model_config_hash)
                   != (lineage.provider_id, lineage.model_id, lineage.model_config_hash)):
            raise ValueError('c08_retained_actual_user_job_differs')
        item = admitted_item(source, proof)
        if item.text != self.batch.messages[0][1]:
            raise ValueError('c08_retained_user_text_differs')
        try:
            saved, receipt = await self.evidence.read_admitted(self._identity(request))
        except HostEvidenceUnavailable:
            pass
        else:
            envelope = self._envelope(request, saved, receipt)
            self.executed_plan = h.MemoryMutationPlan.from_json(h.thaw_json(envelope.result.structured_result))
            return envelope
        started = time.monotonic()
        operation = h.MemoryMutationOperation(operation_id='A', kind=h.MemoryMutationKind.CREATE,
            memory_type=h.LongTermMemoryType.SEMANTIC,
            payload=h.SemanticMemoryPayload('user:self', self.batch.predicate, self.batch.value, ()),
            target=None, depends_on_operation_ids=(), lifecycle_state=h.SemanticLifecycleState.ACTIVE,
            epistemic_status=h.EpistemicStatus.EXPLICIT_USER, conflict_status=h.ConflictStatus.UNCONTESTED,
            verification_state=h.VerificationState.SOURCE_BOUND, valid_time_interval=h.ValidTimeInterval(None, None),
            proposed_privacy_class=h.PrivacyClass.PERSONAL, proposed_information_attributes=(),
            evidence_spans=(derive_span(item, item.text, span_id='c08-retained-original-user'),),
            reason_code='explicit_user_assertion')
        head = current_analysis_apply_head()
        if type(head) is not int:
            raise ValueError('c08_retained_actual_analysis_claim_required')
        plan = h.MemoryMutationPlan(self._identity(request), request.run_id,
            stable_id('analysis-batch-turn', request.job_id), request.subject, head,
            h.MemoryMutationPlanOutcome.MUTATE, (operation,), request.disclosure_context,
            request.ordered_evidence_refs, request.idempotency_key)
        self.executions += 1
        response = h.MemoryAnalysisResult(request.job_id, request.run_id, request.request_hash,
            'fixture-local:' + plan.plan_id, plan.to_json(), 0, 0, 0, int((time.monotonic() - started) * 1000))
        envelope = await self.deliver(request, response)
        self.executed_plan = plan
        return envelope


async def prepare_c08_retained_seed(*, path, manager, principal, batch, executed, delivery_authority):
    """Caller reopens a setup-only manager after the actual old group completes.

    Required public builder authorities: HostEvidenceAuthority(path), the
    unbound SetupFixtureDeliveryAuthority, PrimaryConversationAuthority and
    HostHistorySourceAuthority. Stop/close the owned production Memory manager
    before opening this fixture manager; afterward close it and reopen production.
    """
    authority, group = await _read_group(path=path, subject=principal.actor_id, batch=batch, executed=executed)
    if await authority.completed_run_ids() != (group.host_run_id,):
        raise ValueError('c08_retained_unexpected_history')
    await manager.register_principal_owner(principal, m.MemoryScope.personal(principal.actor_id))
    if (await manager.get_twin_graph_view(principal=principal)).nodes:
        raise ValueError('c08_retained_requires_empty_memory_before_actual_user_job')
    executor = RetainedAnalysisExecutor(path=path, batch=batch, group=group, principal=principal)
    delivery_authority.bind(executor)
    lineage = group.user_analysis_lineage
    witness = _ApplicationWitness(manager.backend)
    runner = DurableMemoryJobRunner(witness, executor, delivery_authority, build_worker_config(
        provider_id=lineage.provider_id, model_id=lineage.model_id, model_config_hash=lineage.model_config_hash),
        'corpus-c08-retained-analysis', lambda: batch.scenario_time)
    # Do not ingest a substitute setup source or override original job lineage.
    outcome = await runner.run_once()
    if (outcome is not WorkerRunOutcome.APPLIED or witness.application is None
            or witness.application.receipt.validation_status is not h.AnalysisValidationStatus.ACCEPTED
            or executor.executed_plan is None or executor.executions != 1):
        raise ValueError('c08_retained_actual_accepted_user_application_required')
    plan = executor.executed_plan
    receipt = await manager.apply_memory_mutation_plan(principal=principal,
        scope=m.MemoryScope.personal(principal.actor_id), plan=plan)
    if receipt.outcome is not h.MemoryMutationApplyOutcome.COMMITTED or receipt.receipt_ref is None:
        raise ValueError('c08_retained_actual_mutation_receipt_required')
    view = await manager.get_memory_mutation_receipt_view(principal=principal, receipt_ref=receipt.receipt_ref)
    graph_before = await manager.get_twin_graph_view(principal=principal)
    expected_hash = canonical_hash(plan.operations[0].payload.to_json())
    user = group.registrations[0]
    if (len(view.operations) != 1 or view.plan_hash != plan.plan_hash
            or view.operations[0].evidence_ids != (user.envelope.evidence_id,)
            or [(node.memory_id, node.revision, node.content_hash) for node in graph_before.nodes]
               != [(view.operations[0].memory_id, 1, expected_hash)]):
        raise ValueError('c08_retained_nonempty_user_derived_memory_required')
    # Includes the real terminal ancestor and assistant source admission; full
    # group metadata comes from Host receipts, never from the fixed text itself.
    await PrimaryShortIndexingService(authority, manager=manager, principal=principal).register_group(group)
    bindings = tuple(m.HistoryEvidenceBinding(r.envelope, r.admission_receipt) for r in group.registrations)
    disclosure = plan.disclosure_context
    before = await manager.check_history_visibility(principal=principal, disclosure_context=disclosure, bindings=bindings)
    if len(before.items) != 2 or not all(item.visible for item in before.items):
        raise ValueError('c08_retained_original_and_summary_not_visible_before_forget')
    request = m.SuppressionRequest('corpus-retained-forget:' + batch.manifest_hash, principal.actor_id,
        m.SuppressionScopeKind.EVIDENCE, user.envelope.evidence_id, 'user_forget', batch.scenario_time)
    decision = await manager.suppress(principal=principal, request=request)
    if (decision.request_id != request.request_id or decision.subject != request.subject
            or decision.scope_kind != request.scope_kind or decision.scope_ref != request.scope_ref
            or decision.reason_code != request.reason_code or decision.effective_at != request.requested_at
            or decision.purpose is not None or decision.action.value != 'directive'):
        raise ValueError('c08_retained_exact_source_suppression_unconfirmed')
    after = await manager.check_history_visibility(principal=principal, disclosure_context=disclosure, bindings=bindings)
    graph_after = await manager.get_twin_graph_view(principal=principal)
    if (len(after.items) != 2 or any(item.visible or item.reason != 'history_suppressed' for item in after.items)
            or graph_after.nodes or graph_after.edges):
        raise ValueError('c08_retained_original_summary_or_memory_still_visible')
    # Original evidence/summary remains physically stored under its old exact
    # registration identity even though ordinary visibility has been revoked.
    await _read_group(path=path, subject=principal.actor_id, batch=batch, executed=executed)
    evidence = HostEvidenceAuthority(path)
    for registration in group.registrations:
        if await evidence.read_admitted(registration.envelope.evidence_id) != (
                registration.envelope, registration.admission_receipt):
            raise ValueError('c08_retained_source_was_rewritten_or_deleted')
        if await manager.register_conversation_evidence(group.references[registration.metadata.item_ordinal - 1]) != (
                group.references[registration.metadata.item_ordinal - 1]):
            raise ValueError('c08_retained_public_registration_replay_differs')
    if await runner.run_once() is not WorkerRunOutcome.IDLE:
        raise ValueError('c08_retained_analysis_not_settled')
    return dict(case_id=batch.case_id, setup_hash=batch.setup_hash, manifest_hash=batch.manifest_hash,
        setup_complete=True, unprepared_carriers=(), labels={'A':view.operations[0]},
        source_pair=(user.envelope, user.admission_receipt), completed_group=group,
        outcome=outcome, fixture_executions=executor.executions, application=witness.application,
        request=witness.request, plan=plan, mutation_receipt=view, mutation_receipt_ref=receipt.receipt_ref,
        suppression_request=request, suppression_decision=decision,
        visibility_before=before, visibility_after=after, graph_before=graph_before, graph_after=graph_after,
        fixture_defaults=('authored-setup-only-old-group', 'retained-assistant-summary',),
        short_generation_exercised=False)

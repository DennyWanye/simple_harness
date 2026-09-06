"""Synthetic C05 setup through real Host tools; never a scoring Provider.

The caller composes the actual runtime with this deterministic fixture Provider.
Completion is re-read from public SDK facts, not trusted from the drive callback.
Archives remain intact. Scoring-history isolation is a separate required seam.
"""
from dataclasses import dataclass
import hashlib
import json
import aiosqlite

from simple_harness import CallId, thaw_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage
from deskpet.memory.human_memory_service import QueueTurnRequest
from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
from deskpet.task_scope.search import TaskScopeSearchStore
from deskpet.task_scope.disclosure import render_scope_disclosure
from deskpet.quality.corpus_c05 import TaskSetupBatch, compile_c05_setup, operational_spec, operational_text
from deskpet.execution.terminal_identity import read_primary_terminal_identity_tx
from deskpet.memory.primary_visibility import read_evidence_pair

MARKER_CONTENT = 'Synthetic C05 fixture preparation; not historical task content.\n'


@dataclass(frozen=True)
class PreparedScopeArchive:
    label: str
    subject: str
    turn_id: str
    sdk_run_id: str
    task_scope_id: str
    route_receipt: dict
    terminal: object
    source_id: str
    source_hash: str
    disclosure: dict
    remaining_requirements: tuple[str, ...]
    case_id: str
    setup_hash: str
    phase: str = 'create'
    marker_effect_id: str | None = None


class TaskSetupProvider:
    """Only setup-declared route/update actions, with no external model call."""
    def __init__(self, *, target):
        self.target = target
        self.requests = []
        self._active = None

    def arm(self, batch, label, *, revision_of=None):
        if batch != compile_c05_setup(batch.case_id, batch.setup_text):
            raise ValueError('c05_exact_batch_required')
        phase = 'create' if revision_of is None else 'before_selection'
        self._active = operational_spec(batch, label, phase=phase)
        self._revision_of = revision_of
        self.route_receipt = None
        self._route_called = self._closure_called = False
        self._counter = 0
        self._marker_stage = 0
        self.marker_result = None

    async def invoke(self, request, *, cancel):
        if self._active is None:
            raise RuntimeError('c05_provider_not_armed')
        self.requests.append(request)
        self._counter += 1
        if self._counter > 9:
            raise RuntimeError('c05_unexpected_tool_cycle')
        last_tool = None
        for message in request.messages:
            if message.role is MessageRole.TOOL and isinstance(message.content, str):
                body = json.loads(message.content)
                last_tool = body
                value = body.get('value', {})
                if isinstance(value, dict) and 'context_route_receipt' in value:
                    self.route_receipt = value['context_route_receipt']
        spec = self._active
        if not self._route_called:
            self._route_called = True
            args = {'route': 'create_new', 'title': spec.title}
            if spec.goal is not None:
                args['goal'] = spec.goal
            if self._revision_of is not None:
                args = dict(route='resume_existing', task_scope_id=self._revision_of.task_scope_id,
                            expected_source_hash=self._revision_of.source_hash)
            return self._tool(request, 'context_route', args)
        if self.route_receipt is None:
            raise RuntimeError('c05_actual_route_not_accepted')
        if spec.next_step is not None or spec.status != 'active':
            # CREATE itself is clean: only an actual material effect produces
            # the existing closure instruction. This explicit synthetic marker
            # has no setup/gold content and is never an invented dirty event.
            stage = self._marker_stage
            if stage < 4:
                if stage and (last_tool is None or last_tool.get('outcome') != 'succeeded'):
                    raise RuntimeError('c05_marker_tool_not_succeeded')
                value = {} if last_tool is None else last_tool.get('value', {})
                if stage == 0:
                    name, args = 'tool_search', {'query': 'write_file'}
                elif stage == 1:
                    name, args = 'tool_describe', {'capability_id': value['matches'][0]['capability_id']}
                elif stage == 2:
                    name, args = 'tool_activate', {k: value[k] for k in ('capability_id', 'schema_hash', 'describe_nonce')}
                else:
                    marker_key = hashlib.sha256(request.request_id.value.encode()).hexdigest()[:24]
                    name, args = 'write_file', {'path': '.c05-fixture-' + marker_key + '.txt',
                                              'content': MARKER_CONTENT}
                self._marker_stage += 1
                return self._tool(request, name, args)
            if self.marker_result is None:
                if last_tool is None or last_tool.get('outcome') != 'succeeded':
                    raise RuntimeError('c05_marker_write_not_succeeded')
                self.marker_result = last_tool['value']
        instructions = []
        for message in request.messages:
            if message.role is MessageRole.SYSTEM and isinstance(message.content, str):
                try:
                    value = json.loads(message.content)
                except ValueError:
                    continue
                if isinstance(value, dict) and value.get('kind') == 'task_scope_closure_required':
                    instructions.append(value)
        if instructions and not self._closure_called:
            if len(instructions) != 1:
                raise RuntimeError('c05_closure_request_ambiguous')
            instruction = instructions[0]
            refs = instruction['allowed_evidence_refs']
            operations = []
            if spec.next_step is not None:
                operations.append(dict(operation_id='resume', kind='resume.update', value=spec.next_step,
                    reason_code='corpus_authored_setup', evidence_refs=refs))
            if spec.status != 'active':
                kind = {'paused': 'task.pause', 'complete': 'task.complete'}[spec.status]
                operations.append(dict(operation_id='status', kind=kind, value=spec.status,
                    reason_code='corpus_authored_setup', evidence_refs=refs))
            args = dict(outcome='mutate' if operations else 'no_mutation',
                base_revision=instruction['current_revision'], evidence_refs=refs,
                idempotency_key='c05-fixture-closure:' + self.route_receipt['run_id'])
            if operations:
                args['operations'] = operations
            else:
                args['closure_reason'] = 'Setup title created; no additional authored state.'
            self._closure_called = True
            return self._tool(request, 'task_scope_update', args)
        if instructions:
            raise RuntimeError('c05_closure_not_settled')
        if (spec.next_step is not None or spec.status != 'active') and not self._closure_called:
            raise RuntimeError('c05_material_closure_instruction_missing')
        return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, 'Fixture setup complete.'),
            model=self.target.model, usage=ProviderUsage(0, 0, 0))

    def _tool(self, request, name, arguments):
        return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, name),
            tool_calls=(ProviderToolCall(CallId('setup-' + str(self._counter)), name, arguments),),
            model=self.target.model, usage=ProviderUsage(0, 0, 0))


async def prepare_scope_archive(*, batch: TaskSetupBatch, label: str, subject: str,
        service, provider: TaskSetupProvider, drive, stack, path, policy,
        disclosure_context=None, disclosure_context_resolver=None,
        owner_subjects=None, revision_of=None) -> PreparedScopeArchive:
    """Real queue/tool/terminal source, then production exact disclosure.

drive runs the caller's real runtime once. It cannot authorize a fabricated
result: actual immutable effect, Host route, SDK terminal and disclosure must
agree. This function does not import history into a scoring conversation.
"""
    if batch != compile_c05_setup(batch.case_id, batch.setup_text):
        raise ValueError('c05_exact_batch_required')
    if policy is None or ((disclosure_context is None) == (disclosure_context_resolver is None)):
        raise ValueError('c05_actual_disclosure_policy_required')
    phase = 'create' if revision_of is None else 'before_selection'
    spec = operational_spec(batch, label, phase=phase)
    actual_text = operational_text(batch, label, phase=phase)
    if revision_of is not None and (revision_of.case_id != batch.case_id or revision_of.label != label
            or revision_of.subject != subject or revision_of.setup_hash != batch.setup_hash
            or revision_of.phase != 'create'):
        raise ValueError('c05_revision_origin_differs')
    owners = {'self': subject} if owner_subjects is None else owner_subjects
    if owners.get(spec.owner) != subject or (spec.owner != 'self' and owners.get('self') == subject):
        raise ValueError('c05_actual_owner_context_required')
    provider.arm(batch, label, revision_of=revision_of)
    key = hashlib.sha256((batch.setup_hash + ':' + label + ':' + subject + ':' + phase).encode()).hexdigest()
    queued = await service.enqueue_turn(QueueTurnRequest(None, 'c05-setup:' + key, actual_text))
    bind_queued = getattr(provider, 'bind_queued', None)
    if bind_queued is not None:
        bind_queued(queued)
    await drive()
    route = provider.route_receipt
    expected_route = 'create_new' if revision_of is None else 'resume_existing'
    if not isinstance(route, dict) or route.get('route') != expected_route:
        raise RuntimeError('c05_route_receipt_missing')
    run_id, scope_id = route['run_id'], route['task_scope_id']
    if disclosure_context_resolver is not None:
        disclosure_context = await disclosure_context_resolver(run_id=run_id, turn_id=queued['turn_ref'])
    if disclosure_context is None or disclosure_context.subject != subject:
        raise ValueError('c05_actual_disclosure_policy_required')
    start, effects = stack.read_primary_dependency_facts(run_id, (route['effect_id'],))
    effect = effects[0]
    if (effect is None or not effect.terminal or effect.result is None
            or effect.tool_name != 'context_route' or effect.raw_call_id != route['raw_call_id']
            or thaw_json(effect.result.value).get('context_route_receipt') != route
            or (revision_of is None and thaw_json(effect.arguments).get('title') != spec.title)
            or (revision_of is not None and scope_id != revision_of.task_scope_id)):
        raise RuntimeError('c05_actual_effect_differs')
    saved = await ContextRouteLedgerStore(path).latest_route_decision_for_run(run_id)
    if saved is None or saved['task_scope_id'] != scope_id or saved['route'] != expected_route:
        raise RuntimeError('c05_host_route_differs')
    terminal, messages = stack.read_settled_primary_run(run_id, current_text=actual_text)
    if terminal.state != 'completed':
        raise RuntimeError('c05_actual_terminal_not_completed')
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            'SELECT t.*,r.host_run_id,b.sdk_run_id FROM foreground_turns t '
            'JOIN foreground_runs r ON r.turn_id=t.turn_id AND r.subject=t.subject '
            'JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id '
            'WHERE t.turn_id=? AND t.subject=?', (str(queued['turn_ref']), subject),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None or row['sdk_run_id'] != run_id:
            raise RuntimeError('c05_setup_turn_run_differs')
        identity = await read_primary_terminal_identity_tx(db, subject=subject,
            primary_ref=row['primary_conversation_id'], host_run_id=row['host_run_id'], sdk_run_id=run_id)
        if identity is None:
            raise RuntimeError('c05_setup_host_terminal_missing')
        identity.verify_sdk_terminal(terminal)
        envelope, _ = await read_evidence_pair(db=db, subject=subject,
            primary_ref=row['primary_conversation_id'], evidence_id=row['evidence_id'])
        if (envelope.envelope_hash != row['evidence_hash']
                or json.loads(row['turn_json'])['payload']['text'] != actual_text):
            raise RuntimeError('c05_setup_original_source_differs')
    marker_effect_id = None
    if spec.next_step is not None or spec.status != 'active':
        from pathlib import Path
        async with aiosqlite.connect(path) as db:
            async with db.execute("SELECT effect_id FROM primary_effect_identities WHERE sdk_run_id=? AND tool_name='write_file'",
                                  (run_id,)) as cursor:
                marker_ids = [r[0] for r in await cursor.fetchall()]
        if len(marker_ids) != 1:
            raise RuntimeError('c05_actual_marker_effect_not_unique')
        _, (marker,) = stack.read_primary_dependency_facts(run_id, tuple(marker_ids))
        if (marker is None or not marker.terminal or marker.result is None
                or thaw_json(marker.arguments).get('content') != MARKER_CONTENT
                or thaw_json(marker.result.value) != provider.marker_result):
            raise RuntimeError('c05_actual_marker_effect_differs')
        if Path(provider.marker_result['path']).read_text() != MARKER_CONTENT:
            raise RuntimeError('c05_actual_marker_file_differs')
        marker_effect_id = marker_ids[0]
    search = TaskScopeSearchStore(path)
    await search.rebuild_scope(scope_id)
    opened = await search.open_exact(subject=subject, allowed_scope_ids=(scope_id,), task_scope_id=scope_id)
    disclosed = await render_scope_disclosure(db_path=path, package=opened.resume_package,
        subject=subject, stack=stack, policy=policy, disclosure_context=disclosure_context)
    fields = disclosed['disclosure']['fields']
    expected = {'title': spec.title}
    if spec.goal is not None:
        expected['goal'] = spec.goal
    if spec.next_step is not None:
        expected['resume'] = spec.next_step
    if any(fields.get(k) != v for k, v in expected.items()):
        raise RuntimeError('c05_required_disclosure_unavailable')
    for field in expected:
        if revision_of is not None and field != 'resume':
            continue  # unchanged title/goal retain their original creation source
        proof = disclosed['disclosure_manifest']['fragments'][field]['dependencies']['evidence']
        if not any(item['evidence_id'] == row['evidence_id'] and item['envelope_hash'] == row['evidence_hash'] for item in proof):
            raise RuntimeError('c05_original_user_dependency_missing')
    if disclosed['disclosure']['structure'].get('status') != spec.status:
        raise RuntimeError('c05_actual_status_differs')
    return PreparedScopeArchive(label, subject, str(queued['turn_ref']), run_id, scope_id,
        route, terminal, opened.source_id, opened.source_hash, disclosed,
        tuple(batch.requirements) + ('scoring_history_isolation',), batch.case_id, batch.setup_hash, phase, marker_effect_id)

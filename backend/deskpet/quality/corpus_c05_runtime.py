"""C05 setup/scoring phase adapter using real Host-owned source archives.

Labels and archive receipts are fixture control data, never scoring messages.
The dispatcher must use scoring_scope_ref for actual queue admission and supply
its original corpus input unchanged. This module does not call a model.
"""
from dataclasses import dataclass
from pathlib import Path

from deskpet.memory.human_memory_service import (OpenTaskScopeRequest, SearchTaskScopesRequest,
    QueueTurnRequest)
from deskpet.task_scope.search import TaskScopeSearchError
from deskpet.task_scope.disclosure import render_scope_disclosure
from deskpet.quality.corpus_c05 import compile_c05_setup
from deskpet.quality.corpus_c05_prepare import prepare_scope_archive


@dataclass(frozen=True)
class TaskRuntimeLane:
    subject: str
    service: object
    provider: object
    drive: object
    stack: object
    path: object
    policy: object
    disclosure_context: object | None = None
    disclosure_context_resolver: object | None = None
    read_context_resolver: object | None = None
    binding_reader: object | None = None
    configured_root: object | None = None

    def __post_init__(self):
        if (self.disclosure_context is None) == (self.disclosure_context_resolver is None):
            raise ValueError('c05_exact_setup_disclosure_lane_required')
        if self.disclosure_context_resolver is not None and self.read_context_resolver is None:
            raise ValueError('c05_current_read_resolver_required')


class TaskSourceReader:
    def __init__(self, lane):
        self.lane = lane

    async def open(self, scope_ref, *, expected_source_hash=None):
        lane = self.lane
        context = (await lane.read_context_resolver() if lane.read_context_resolver is not None
                   else lane.disclosure_context)
        if context is None or context.subject != lane.subject:
            raise ValueError('c05_current_read_disclosure_required')
        actual = await lane.service.open_task_scope(OpenTaskScopeRequest(scope_ref,
            expected_source_hash=expected_source_hash))
        disclosed = await render_scope_disclosure(db_path=lane.path,
            package=actual['resume_package'], subject=lane.subject, stack=lane.stack,
            policy=lane.policy, disclosure_context=context)
        if lane.read_context_resolver is not None and await lane.read_context_resolver() != context:
            raise ValueError('c05_read_disclosure_changed')
        # Raw README/snippet/project fields bypass ordinary disclosure, so only
        # actual source identities and the verified disclosure are exposed.
        return dict(scope_ref=scope_ref, source_ref=actual['source_ref'],
            source_hash=actual['source_hash'], open_receipt_hash=actual['receipt_hash'],
            disclosure=disclosed)

    async def page(self, query, *, limit=1, cursor=None):
        actual = await self.lane.service.search_task_scopes(
            SearchTaskScopesRequest(query, max_candidates=limit, cursor=cursor))
        candidates = []
        for item in actual['candidates']:
            candidates.append(await self.open(item['scope_ref'], expected_source_hash=item['source_hash']))
        # This is a derived view with the unchanged actual search receipt, not
        # a claim that the receipt hashes our transformed candidate JSON.
        return dict(candidates=tuple(candidates), next_cursor=actual['next_cursor'],
            search_receipt_hash=actual['receipt_hash'])


@dataclass
class PreparedTaskCase:
    batch: object
    lanes: dict
    archives: list
    scoring_scope_ref: str | None
    reader: TaskSourceReader

    async def enqueue_scoring(self, *, text, delivery_key):
        # The caller supplies the unchanged corpus turn, never a whole Case.
        # C07's shared dispatcher owns that separation; labels stay here.
        if self.batch.case_id not in {'C05-04', 'C05-07', 'C05-08', 'C05-09', 'C05-10',
                                      'C05-11', 'C05-12', 'C05-14', 'C05-18', 'C05-20'}:
            raise RuntimeError('c05_remaining_source_obligations:' + ','.join(self.batch.requirements))
        result = await self.lanes['self'].service.enqueue_turn(
            QueueTurnRequest(self.scoring_scope_ref, delivery_key, text))
        if result['scope_ref'] != self.scoring_scope_ref:
            raise RuntimeError('c05_scoring_scope_admission_differs')
        return result

    async def history_reader(self, *, primary_ref, delegate):
        from deskpet.quality.corpus_c05_history import SetupPhaseHistoryReader
        lane = self.lanes['self']
        owned = tuple(a for a in self.archives if a.subject == lane.subject)
        return await SetupPhaseHistoryReader.from_archives(path=lane.path, subject=lane.subject,
            primary_ref=primary_ref, archives=owned, stack=lane.stack, delegate=delegate)

    async def read_named_roots(self):
        """Require an actual unique first binding; never append a second root."""
        if self.batch.case_id not in ('C05-17', 'C05-19'):
            raise ValueError('c05_named_roots_not_declared')
        lane = self.lanes['self']
        if lane.binding_reader is None or lane.configured_root is None:
            raise ValueError('c05_actual_binding_lane_missing')
        parent = Path(lane.configured_root).resolve(strict=True)
        captured = {}
        for spec in self.batch.scopes:
            archive = next(a for a in self.archives if a.label == spec.label)
            receipt = await lane.binding_reader.current_receipt(archive.task_scope_id)
            if len(receipt.root_identity_hashes) != 1:
                raise RuntimeError('c05_unique_task_root_required')
            authority = await lane.binding_reader.verify_effect_authority(
                task_scope_id=archive.task_scope_id, binding_set_revision=receipt.binding_set_revision,
                binding_set_receipt_id=receipt.receipt_id, binding_set_receipt_hash=receipt.receipt_hash,
                root_identity_hash=receipt.root_identity_hashes[0])
            actual = Path(authority.root.canonical_path)
            if actual != parent / spec.root_label or actual == parent:
                raise RuntimeError('c05_first_binding_named_root_missing')
            captured[spec.label] = receipt
        return captured

    async def before_selection(self):
        """Only C18 has this authored phase; do not run on model/gold demand."""
        if self.batch.case_id != 'C05-18':
            raise ValueError('c05_before_selection_not_declared')
        previous = [a for a in self.archives if a.label == 'A' and a.phase == 'create']
        if len(previous) != 1:
            raise ValueError('c05_revision_origin_missing')
        done = [a for a in self.archives if a.phase == 'before_selection']
        if done:
            return done[0]  # actual previously captured completion, no new send
        lane = self.lanes['self']
        original = await self.reader.open(previous[0].task_scope_id)
        archive = await prepare_scope_archive(batch=self.batch, label='A', subject=lane.subject,
            service=lane.service, provider=lane.provider, drive=lane.drive, stack=lane.stack,
            path=lane.path, policy=lane.policy, disclosure_context=lane.disclosure_context,
            disclosure_context_resolver=lane.disclosure_context_resolver, revision_of=previous[0])
        now = await self.reader.open(archive.task_scope_id, expected_source_hash=archive.source_hash)
        old_revision = original['disclosure']['disclosure']['structure']['canonical_revision']
        new_revision = now['disclosure']['disclosure']['structure']['canonical_revision']
        if new_revision <= old_revision or now['source_hash'] == original['source_hash']:
            raise RuntimeError('c05_revision_not_advanced')
        if now['disclosure']['disclosure']['fields'].get('resume') != '待确认图片':
            raise RuntimeError('c05_revision_source_not_disclosed')
        self.archives.append(archive)
        return archive

    async def inspect_search_order(self):
        """Read real pages, never sort candidates into the expected answer."""
        if self.batch.case_id not in ('C05-10', 'C05-11'):

            raise ValueError('c05_order_not_declared')
        # unicode61 indexes each complete Chinese title as a token. Use all
        # original setup titles symmetrically, never gold or a target-only query.
        query = ' '.join(spec.title for spec in self.batch.scopes)
        labels = {a.task_scope_id: a.label for a in self.archives}
        first = await self.reader.page(query, limit=1)
        if first['next_cursor'] is None:
            raise RuntimeError('c05_actual_second_page_missing')
        second = await self.reader.page(query, limit=1, cursor=first['next_cursor'])
        actual_labels = [labels[item['scope_ref']] for page in (first, second) for item in page['candidates']]
        if actual_labels != ['B', 'A']:
            raise RuntimeError('c05_actual_search_order_differs')
        return first, second


async def prepare_task_case(batch, *, lanes):
    if batch != compile_c05_setup(batch.case_id, batch.setup_text):
        raise ValueError('c05_exact_batch_required')
    owners = {key: lane.subject for key, lane in lanes.items()}
    if 'self' not in lanes or any(s.owner not in lanes for s in batch.scopes):
        raise ValueError('c05_actual_owner_lane_missing')
    if len(set(owners.values())) != len(owners):
        raise ValueError('c05_owner_lanes_not_distinct')
    if len({Path(lane.path).resolve() for lane in lanes.values()}) != 1:
        raise ValueError('c05_owner_lanes_require_same_host_store')
    archives = []
    # Preserve authored source order. source_sequence is scope-local, not a
    # cross-scope append clock. Actual public rank/cursor is checked below.
    for spec in batch.scopes:
        lane = lanes[spec.owner]
        archives.append(await prepare_scope_archive(batch=batch, label=spec.label,
            subject=lane.subject, service=lane.service, provider=lane.provider,
            drive=lane.drive, stack=lane.stack, path=lane.path, policy=lane.policy,
            disclosure_context=lane.disclosure_context,
            disclosure_context_resolver=lane.disclosure_context_resolver, owner_subjects=owners))
    active = next(a.task_scope_id for a in archives if a.label == 'C') if batch.case_id == 'C05-07' else None
    result = PreparedTaskCase(batch, dict(lanes), archives, active, TaskSourceReader(lanes['self']))
    if batch.case_id in ('C05-10', 'C05-11'):
        await result.inspect_search_order()
    if batch.case_id == 'C05-08':
        archived = next(a for a in archives if a.label == 'A')
        before = lanes['self'].stack.read_run_terminal_evidence(archived.sdk_run_id)
        opened = await result.reader.open(archived.task_scope_id, expected_source_hash=archived.source_hash)
        if opened['disclosure']['disclosure']['structure']['status'] != 'complete':
            raise RuntimeError('c05_archive_not_complete')
        if lanes['self'].stack.read_run_terminal_evidence(archived.sdk_run_id) != before:
            raise RuntimeError('c05_archive_read_changed_terminal')
    if batch.case_id == 'C05-12':
        foreign = next(a for a in archives if a.label == 'B')
        await TaskSourceReader(lanes['other']).open(foreign.task_scope_id,
            expected_source_hash=foreign.source_hash)
        try:
            await result.reader.open(foreign.task_scope_id)
        except TaskScopeSearchError as exc:
            if exc.code != 'human_memory_permission_denied':
                raise
        else:
            raise RuntimeError('c05_foreign_scope_disclosed')
        page = await result.reader.page('照片编目', limit=20)
        own = next(a for a in archives if a.label == 'A')
        actual = {item['scope_ref'] for item in page['candidates']}
        if foreign.task_scope_id in actual or own.task_scope_id not in actual:
            raise RuntimeError('c05_permission_first_search_differs')
    return result


async def read_candidate_events(*, path, subject, sdk_run_id, stack, policy):
    """Read actual nonempty visible search effects for dispatcher scheduling.

    No target selection, inferred release, resume authority or fabricated event.
    The returned event is an observation of the public effect, not a new ledger.
    """
    import aiosqlite
    from simple_harness import thaw_json
    from deskpet.task_scope.protocol import canonical_hash, canonical_json
    from deskpet.task_scope.disclosure import verify_scope_disclosure
    from deskpet.memory.trusted_disclosure import resolve_current_disclosure
    if policy is None:
        raise ValueError('c05_candidate_policy_required')
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            'SELECT i.*,r.primary_conversation_id FROM primary_effect_identities i '
            'JOIN foreground_run_sdk_bindings b ON b.sdk_run_id=i.sdk_run_id AND b.host_run_id=i.host_run_id '
            'JOIN foreground_runs r ON r.host_run_id=b.host_run_id '
            "WHERE r.subject=? AND i.sdk_run_id=? AND i.tool_name='task_scope_search' ORDER BY i.sequence LIMIT 257",
            (subject, sdk_run_id),
        ) as cursor:
            rows = await cursor.fetchall()
        if len(rows) > 256:
            raise RuntimeError('c05_candidate_effect_limit')
        events = []
        for row in rows:
            identity = dict(host_run_id=row['host_run_id'], sdk_run_id=sdk_run_id,
                effect_id=row['effect_id'], tool_name='task_scope_search')
            if row['identity_hash'] != canonical_hash(identity) or row['identity_json'] != canonical_json(identity):
                raise RuntimeError('c05_candidate_effect_index_differs')
            _, (effect,) = stack.read_primary_dependency_facts(sdk_run_id, (row['effect_id'],))
            if effect is None:
                raise ValueError('c05_indexed_effect_missing')
            if (effect.effect_id.value != row['effect_id'] or effect.tool_name != 'task_scope_search'
                    or effect.run_id.value != sdk_run_id):
                raise RuntimeError('c05_candidate_effect_differs')
            if not effect.terminal:
                continue  # pending, never evidence of completed evaluation
            if effect.result is None:
                raise ValueError('c05_terminal_effect_result_missing')
            if effect.result.call_id != effect.call_id:
                raise ValueError('c05_candidate_result_call_differs')
            outcome = effect.result.outcome.value
            if outcome in ('rejected', 'failed') and effect.result.error_code:
                continue  # real SDK terminal failure, not an arbitrary payload key
            if outcome != 'succeeded':
                raise ValueError('c05_candidate_outcome_unverifiable')
            value = thaw_json(effect.result.value)
            if not isinstance(value, dict):
                raise ValueError('c05_candidate_result_invalid')
            if 'error' in value:
                raise ValueError('c05_candidate_error_payload_unverified')
            if not isinstance(value.get('candidates'), list):
                raise ValueError('c05_candidate_result_invalid')
            disclosure = await resolve_current_disclosure(db_path=path, subject=subject,
                run_id=sdk_run_id, request_id=row['effect_id'])
            visible = []
            for candidate in value['candidates']:
                if not isinstance(candidate, dict) or not isinstance(candidate.get('scope_disclosure'), dict):
                    raise ValueError('c05_candidate_shape_invalid')
                package = candidate['scope_disclosure']
                if candidate != dict(task_scope_id=package['task_scope_id'], source_id=package['source_id'],
                                     source_hash=package['source_hash'], scope_disclosure=package):
                    raise ValueError('c05_candidate_source_differs')
                proof = await verify_scope_disclosure(db_path=path, package=package, subject=subject, stack=stack)
                if (package['disclosure']['fields'] and await policy.check_dependencies(db=db,
                        primary_ref=row['primary_conversation_id'], dependencies=proof, disclosure_context=disclosure)):
                    visible.append({key: candidate[key] for key in
                                    ('task_scope_id', 'source_id', 'source_hash')})
            if await resolve_current_disclosure(db_path=path, subject=subject,
                    run_id=sdk_run_id, request_id=row['effect_id']) != disclosure:
                raise ValueError('c05_candidate_disclosure_changed')
            if visible:
                events.append(dict(kind='task_candidates_visible', sdk_run_id=sdk_run_id,
                    effect_id=row['effect_id'], call_id=effect.raw_call_id,
                    actual_result_hash=canonical_hash(value), visible_count=len(visible),
                    visible_sources=tuple(visible)))
        return tuple(events)

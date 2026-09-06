"""C05 runner-only schedule contract; never a Provider or setup-authority input.

The four supported schedules are copied from the frozen 05-task script tables,
not their gold. Runtime event/phase wiring consumes Carver's public fact reader.
"""
from dataclasses import dataclass


SUPPORTED_CASES = frozenset({'C05-04', 'C05-09', 'C05-14', 'C05-20'})
_PREVIEW = 'candidate_preview_then_turn_terminal'
_UNMET = 'record_unmet_and_stop_no_rescue'
# Exact authored scripts. No target ID, expected answer, or selected label.
_SCRIPTS = {
    'C05-04': ((_PREVIEW, '选社区物品登记那个。'),),
    'C05-09': ((_PREVIEW, '选等待字体授权的家谱任务，先告诉我卡点。'),),
    'C05-14': ((_PREVIEW, '选需要核对座位的那项。'),),
    'C05-20': ((_PREVIEW, '我原想选排版，先别切换。'),
               ('assistant_turn_terminal_after_f1', '改选封面校对，以这条为准。')),
}


@dataclass(frozen=True)
class ScheduledFollowup:
    followup_id: str
    after_event: str
    fixture_action: str
    user_message: str
    on_unmet: str


def validate_schedule(case_id, raw):
    """Validate the separate compiler scheduler row before any setup dispatch.

    The compiler's NOT_IMPLEMENTED marker remains a source-era fact; this
    function neither changes it nor treats it as proof of runtime readiness.
    """
    if case_id not in SUPPORTED_CASES:
        raise ValueError('c05_session_case_requirements_not_implemented')
    if (type(raw) is not dict or set(raw) != {'scripted_followup', 'scheduler_state'}
            or raw['scheduler_state'] != 'NOT_IMPLEMENTED'
            or type(raw['scripted_followup']) is not list):
        raise ValueError('c05_exact_compiler_schedule_required')
    expected = tuple(ScheduledFollowup('f' + str(index), event, 'none', text, _UNMET)
        for index, (event, text) in enumerate(_SCRIPTS[case_id], 1))
    fields = set(ScheduledFollowup.__dataclass_fields__)
    rows = raw['scripted_followup']
    if (any(type(row) is not dict or set(row) != fields
            or any(type(value) is not str for value in row.values()) for row in rows)
            or tuple(ScheduledFollowup(**row) for row in rows) != expected):
        raise ValueError('c05_authored_followup_schedule_differs')
    return expected


def _confirmed_setup_trace(observations, transport, *, binding, turn_ref):
    from deskpet.quality.corpus_c05_transport import PROVIDER_ID
    trace = observations.get('trace') or {}
    providers = trace.get('providers', ())
    responses = tuple(transport.current_responses)
    expected = {item['response_id']: item for item in responses}
    if (observations.get('observation_errors') or trace.get('trace_status') != 'COMPLETE'
            or trace.get('terminal_status') != 'TERMINAL'
            or trace.get('provider_observation_complete') is not True
            or binding.provider_id != PROVIDER_ID or binding.model_id != transport.model
            or binding.run_id != observations.get('sdk_run_id')
            or not responses or len(expected) != len(responses) or len(providers) != len(responses)
            or len(set(transport.current_request_hashes)) != len(responses)):
        raise ValueError('c05_setup_public_trace_incomplete')
    observed = set()
    for invocation in providers:
        response = invocation.get('response_json') or {}
        response_id = response.get('provider_request_id')
        sent = expected.get(response_id)
        if (sent is None or response_id in observed or sent['turn_ref'] != turn_ref
                or response_id != 'c05-fixture-' + sent['wire_request_hash']
                or sent['wire_request_hash'] not in transport.current_request_hashes
                or invocation['state'] != 'succeeded' or invocation['handed_off_at'] is None
                or invocation['handoff_attempt'] != 1 or invocation['rehandoff_count'] != 0
                or tuple(call['call_id'] for call in response['tool_calls']) != tuple(sent['call_ids'])):
            raise ValueError('c05_setup_physical_response_binding_differs')
        observed.add(response_id)
    return dict(sdk_run_id=observations['sdk_run_id'], host_run_id=observations['host_run_id'],
        provider_id=binding.provider_id, trace_hash=trace['trace_hash'],
        provider_invocation_ids=[item['invocation_id'] for item in providers],
        fixture_http_responses=responses, sdk_handoff_count=len(providers), real_model_calls=0)


async def prepare_setup_phase(*, main, batch, service, runtime, transport, history_reader,
        workspace_root, directory, collect_turn, record, ingestion_worker):
    """Prepare the four source-complete cases through the unchanged main stack.

    Carver owns the exact transport, approval, source archive and history reader.
    This orchestrator consumes those ports; it does not replace their authority.
    """
    import asyncio
    import aiosqlite
    from deskpet.quality.corpus_c05 import compile_c05_setup, operational_text
    from deskpet.quality.corpus_c05_prepare import prepare_scope_archive
    from deskpet.quality.corpus_c05_approval import C05SetupApproval
    from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
    from deskpet.sdk_adapters.context_route import local_owner_auth
    from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1
    from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore
    from deskpet.memory.trusted_disclosure import resolve_current_disclosure
    if batch.case_id not in SUPPORTED_CASES or batch != compile_c05_setup(batch.case_id, batch.setup_text):
        raise ValueError('c05_supported_exact_setup_required')
    subject = local_owner_auth().subject
    state = await service.read_primary_state(request_id='c05-setup-owner')
    primary = state['primary_ref']
    if runtime.subject != subject or (await service.queue_snapshot())['turns']:
        raise ValueError('c05_fresh_primary_required')
    phase_dir = directory / 'setup-tasks'
    phase_dir.mkdir(exist_ok=False)
    policy = main._primary_history_policy(subject)
    approval = C05SetupApproval(ingress=main._sdk_ingress, transport=transport,
        stack=main._sdk_runtime_stack, ledger=ContextRouteLedgerStore(main._state_db_path),
        binding_store=WorkspaceBindingAuthorityStore(main._state_db_path,
            configured_workspace_root=workspace_root), expected_configured_root=workspace_root,
        persist=lambda name, value: record(phase_dir / (name + '.json'), value))

    async def drive():
        queued = transport.queued
        if queued is None:
            raise ValueError('c05_actual_queue_missing')
        await runtime.after_enqueue(subject=subject)
        await runtime.drain()
        if runtime.last_error is not None:
            raise RuntimeError('c05_setup_runtime_failed') from runtime.last_error
        while await approval(service=service, queued=queued):
            await runtime.after_control(subject=subject)
            await runtime.drain()
            if runtime.last_error is not None:
                raise RuntimeError('c05_setup_runtime_failed') from runtime.last_error
        await ingestion_worker.run_once()

    async def disclosure_for_run(*, run_id, turn_id):
        if transport.queued is None or str(transport.queued['turn_ref']) != turn_id:
            raise ValueError('c05_disclosure_turn_differs')
        # Host identity metadata only. The SDK terminal/effects remain public.
        async with aiosqlite.connect(main._state_db_path) as db:
            async with db.execute('SELECT r.subject,r.primary_conversation_id,r.turn_id '
                    'FROM foreground_runs r JOIN foreground_run_sdk_bindings b '
                    'ON b.host_run_id=r.host_run_id WHERE b.sdk_run_id=?', (run_id,)) as cursor:
                rows = await cursor.fetchall()
        if len(rows) != 1 or tuple(rows[0]) != (subject, primary, turn_id):
            raise ValueError('c05_disclosure_run_binding_differs')
        return await resolve_current_disclosure(db_path=main._state_db_path, subject=subject,
            run_id=run_id, request_id='c05-source:' + turn_id)

    archives, runs = [], []
    phase = dict(status='NOT_CONFIRMED', kind='deterministic_authored_task_setup',
        real_model_calls=0, runs=runs)
    try:
        for index, spec in enumerate(batch.scopes, 1):
            turn_dir = phase_dir / f'turn-{index:02d}'
            turn_dir.mkdir()
            observations = None
            try:
                archive = await prepare_scope_archive(batch=batch, label=spec.label, subject=subject,
                    service=service, provider=transport, drive=drive, stack=main._sdk_runtime_stack,
                    path=main._state_db_path, policy=policy, disclosure_context_resolver=disclosure_for_run)
                observations = await collect_turn(main, service, subject, transport.queued,
                    operational_text(batch, spec.label), directory=turn_dir)
                binding = SdkRunBindingV1.from_record(main._sdk_runtime_stack.read_closure_run_facts(
                    archive.sdk_run_id).binding_record)
                proof = _confirmed_setup_trace(observations, transport,
                    binding=binding, turn_ref=archive.turn_id)
                if proof['sdk_run_id'] != archive.sdk_run_id:
                    raise ValueError('c05_setup_archive_trace_differs')
                record(turn_dir / 'archive.json', archive)
                archives.append(archive)
                runs.append(proof)
            except asyncio.CancelledError:
                raise
            except Exception:
                if observations is None and transport.queued is not None:
                    try:
                        await collect_turn(main, service, subject, transport.queued,
                            operational_text(batch, spec.label), directory=turn_dir)
                    except Exception as error:
                        phase['observation_error_type'] = type(error).__name__
                raise
        await history_reader.freeze(archives=archives, stack=main._sdk_runtime_stack, primary_ref=primary)
        if main._sdk_provider_binding_resolver.active_provider_ids():
            raise ValueError('c05_setup_provider_still_active')
        phase.update(status='CONFIRMED', setup_archive_count=len(archives),
            fixture_http_requests=transport.attempts, primary_ref=primary,
            labels={archive.label: dict(task_scope_id=archive.task_scope_id,
                source_id=archive.source_id, source_hash=archive.source_hash) for archive in archives})
        return phase
    except BaseException as error:
        phase['error_type'] = type(error).__name__
        raise
    finally:
        record(phase_dir / 'phase.json', phase)


async def _unbound_preview_turn(*, stack, ledger, sdk_run_id):
    """Read original start and current route authority; never infer from text."""
    start, _ = stack.read_primary_dependency_facts(sdk_run_id)
    metadata = start.get('input', {}).get('context_metadata', {})
    keys = ('task_scope_id', 'initial_route_receipt_id', 'initial_route_receipt_hash')
    if any(key not in metadata for key in keys):
        raise ValueError('c05_initial_scope_identity_missing')
    return (all(metadata[key] is None for key in keys)
        and await ledger.latest_route_decision_for_run(sdk_run_id, task_only=True) is None)


async def execute_task_scoring(*, main, service, runtime, subject, text, schedule,
        directory, setup_phase, worker, collect_turn, record):
    """Initial turn + exact authored followups. No missing-event rescue or retry."""
    from deskpet.quality.corpus_runtime import execute_scoring_turn
    from deskpet.quality.corpus_c05_scoring_approval import C05ScoringApproval
    from deskpet.quality.corpus_c05_runtime import read_candidate_events
    from deskpet.quality.corpus_trace import wire, digest
    from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
    from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1
    ledger = ContextRouteLedgerStore(main._state_db_path)
    runs, events, preview_runs = [], [], []
    result = dict(execution_status='DISPATCH_STARTED', scoring_runs=runs,
        followup_events=events, trace=None, provider_statistics_scope='all_scoring_runs_setup_excluded')
    setup_runs = {item['sdk_run_id'] for item in setup_phase['runs']}
    turns = [('initial', text), *((item.followup_id, item.user_message) for item in schedule)]
    for ordinal, (turn_name, current_text) in enumerate(turns):
        if ordinal:
            followup = schedule[ordinal - 1]
            prior = runs[-1]
            sdk = prior['sdk_run_id']
            try:
                unbound = await _unbound_preview_turn(stack=main._sdk_runtime_stack,
                    ledger=ledger, sdk_run_id=sdk)
                visible = ()
                if followup.after_event == _PREVIEW:
                    visible = await read_candidate_events(path=main._state_db_path, subject=subject,
                        sdk_run_id=sdk, stack=main._sdk_runtime_stack,
                        policy=main._primary_history_policy(subject))
                satisfied = unbound and (bool(visible) if followup.after_event == _PREVIEW else True)
            except Exception as error:
                result.update(execution_status='OBSERVATION_FAILED', error_type=type(error).__name__)
                record(directory / f'followup-{followup.followup_id}.json',
                    dict(followup_id=followup.followup_id, status='UNVERIFIABLE', error_type=type(error).__name__))
                return result
            event = dict(followup_id=followup.followup_id, after_event=followup.after_event,
                prerequisite_sdk_run_id=sdk, candidate_events=visible, no_formal_scope_authority=unbound,
                status='SATISFIED' if satisfied else 'UNMET', on_unmet=followup.on_unmet)
            events.append(event)
            record(directory / f'followup-{followup.followup_id}.json', event)
            if not satisfied:
                result['execution_status'] = 'FOLLOWUP_UNMET'
                result['unmet_followup'] = followup.followup_id
                return result
            if visible:
                preview_runs.append(sdk)
        turn_dir = directory / ('scoring-' + turn_name)
        turn_dir.mkdir(exist_ok=False)
        delivery_key = 'scoring-turn-' + str(ordinal + 1)
        selection_allowed = ordinal == len(schedule)
        approval = C05ScoringApproval(main=main, subject=subject,
            selection_allowed=selection_allowed, preview_runs=preview_runs,
            persist=lambda name, value: record(turn_dir / (name + '.json'), value))
        observed = None
        entry = dict(turn_name=turn_name, trace=None)
        runs.append(entry)
        try:
            executed = await execute_scoring_turn(service=service, runtime=runtime,
                scoring_path=main._state_db_path, subject=subject, text=current_text,
                delivery_key=delivery_key, ingestion_worker=worker, approval_driver=approval)
            observed = await collect_turn(main, service, subject, executed.queue_receipt,
                current_text, directory=turn_dir)
            entry.update(queue_receipt=wire(executed.queue_receipt),
                completed_group=wire(executed.completed_group), **observed)
            sdk = observed['sdk_run_id']
            binding = SdkRunBindingV1.from_record(main._sdk_runtime_stack.read_closure_run_facts(sdk).binding_record)
            terminal = main._sdk_runtime_stack.read_run_terminal_evidence(sdk)
            if (binding.provider_id != 'corpus-real-provider' or sdk in setup_runs
                    or sdk in {item.get('sdk_run_id') for item in runs[:-1]}):
                raise ValueError('c05_scoring_provider_phase_differs')
            if (observed['observation_errors'] or observed['trace']['trace_status'] != 'COMPLETE'
                    or terminal is None or terminal.state != 'completed'):
                raise ValueError('c05_scoring_terminal_unverified')
            result.update(sdk_run_id=sdk, host_run_id=observed['host_run_id'], trace=observed['trace'])
            record(turn_dir / 'execution.json', entry)
        except Exception as error:
            # Preserve real dispatch attempts even when no completed group exists.
            if observed is None:
                pending = [item for item in (await service.queue_snapshot())['turns']
                    if item['delivery_key'] == delivery_key]
                if len(pending) == 1:
                    try:
                        observed = await collect_turn(main, service, subject, pending[0], current_text, directory=turn_dir)
                        entry.update(queue_receipt=pending[0], **observed)
                    except Exception as trace_error:
                        result['trace_error_type'] = type(trace_error).__name__
            result.update(execution_status='OBSERVATION_FAILED' if 'completed_group' in entry else 'EXECUTION_FAILED',
                error_type=type(error).__name__)
            record(turn_dir / 'failure.json', dict(error_type=type(error).__name__, observation=entry))
            return result
    result.update(execution_status='COMPLETED', scripted_followups_executed=len(schedule),
        scoring_trace_hashes=[item['trace']['trace_hash'] for item in runs])
    result['schedule_observation_hash'] = digest(events)
    return result

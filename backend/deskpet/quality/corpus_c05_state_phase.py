"""Actual C05 active/completed scope facts across setup and scoring Runs.

These are observations of existing Host/public SDK authorities, not receipts
issued by the corpus runner. No fixture label reaches a Provider request.
"""
from deskpet.quality.corpus_trace import wire


STATE_CASES = frozenset({'C05-07', 'C05-08'})


def state_contract(batch, archives):
    if batch.case_id not in STATE_CASES:
        raise ValueError('c05_state_case_not_declared')
    if (len(archives) != len(batch.scopes)
            or len({a.task_scope_id for a in archives}) != len(archives)
            or len({a.subject for a in archives}) != 1):
        raise ValueError('c05_state_archives_not_distinct')
    observed = []
    for spec in batch.scopes:
        matches = [a for a in archives if a.label == spec.label]
        if len(matches) != 1:
            raise ValueError('c05_state_archive_missing')
        archive = matches[0]
        disclosure = archive.disclosure['disclosure']
        if (archive.case_id != batch.case_id or archive.setup_hash != batch.setup_hash
                or archive.phase != 'create' or archive.terminal.state != 'completed'
                or disclosure['structure']['status'] != spec.status
                or disclosure['fields'].get('title') != spec.title
                or (spec.next_step is not None and disclosure['fields'].get('resume') != spec.next_step)):
            raise ValueError('c05_state_authored_source_differs')
        observed.append(dict(task_scope_id=archive.task_scope_id, source_id=archive.source_id,
            source_hash=archive.source_hash, canonical_revision=disclosure['structure']['canonical_revision'],
            status=spec.status, fields=disclosure['fields'], setup_terminal=wire(archive.terminal)))
    initial = next(a.task_scope_id for a in archives if a.label == 'C') if batch.case_id == 'C05-07' else None
    return dict(case_id=batch.case_id, subject=archives[0].subject,
                initial_scope_ref=initial, archives=observed)


async def observe_unchanged_scopes(*, main, service, subject, sdk_run_id, contract):
    """Current read with actual scoring disclosure; never raw archived text.

Reads/resumes may advance projection-source identity. Canonical revision,
status and fields must remain unchanged; the old terminal must remain exact.
"""
    from deskpet.memory.human_memory_service import OpenTaskScopeRequest
    from deskpet.memory.trusted_disclosure import resolve_current_disclosure
    from deskpet.task_scope.disclosure import render_scope_disclosure
    if contract['subject'] != subject or contract['case_id'] not in STATE_CASES:
        raise ValueError('c05_state_subject_differs')
    kwargs = dict(db_path=main._state_db_path, subject=subject, run_id=sdk_run_id,
                  request_id='c05-state:' + sdk_run_id)
    context = await resolve_current_disclosure(**kwargs)
    observations = []
    for before in contract['archives']:
        terminal = main._sdk_runtime_stack.read_run_terminal_evidence(before['setup_terminal']['run_id'])
        if wire(terminal) != before['setup_terminal']:
            raise ValueError('c05_old_terminal_changed')
        opened = await service.open_task_scope(OpenTaskScopeRequest(before['task_scope_id']))
        current = await render_scope_disclosure(db_path=main._state_db_path,
            package=opened['resume_package'], subject=subject, stack=main._sdk_runtime_stack,
            policy=main._primary_history_policy(subject), disclosure_context=context)
        actual = current['disclosure']
        if (actual['structure']['status'] != before['status']
                or actual['structure']['canonical_revision'] != before['canonical_revision']
                or actual['fields'] != before['fields']):
            raise ValueError('c05_scoring_scope_state_or_visibility_changed')
        observations.append(dict(task_scope_id=before['task_scope_id'],
            source_id=current['source_id'], source_hash=current['source_hash'],
            canonical_revision=actual['structure']['canonical_revision'], status=actual['structure']['status'],
            disclosure_manifest_hash=current['disclosure_manifest']['manifest_hash'],
            old_terminal_event_id=terminal.event_id, old_terminal_event_hash=terminal.event_hash))
    if await resolve_current_disclosure(**kwargs) != context:
        raise ValueError('c05_state_disclosure_changed')
    return observations


async def bound_preview_unchanged(*, stack, ledger, sdk_run_id, contract):
    """C07 may retain its initial C; it may not acquire A before confirmation."""
    expected = contract['initial_scope_ref']
    if contract['case_id'] != 'C05-07' or expected is None:
        raise ValueError('c05_initial_scope_contract_missing')
    start, _ = stack.read_primary_dependency_facts(sdk_run_id)
    metadata = start.get('input', {}).get('context_metadata', {})
    receipt_id = metadata.get('initial_route_receipt_id')
    if metadata.get('task_scope_id') != expected or not receipt_id:
        raise ValueError('c05_actual_initial_scope_missing')
    receipt = await ledger.read_route_receipt(sdk_run_id, receipt_id)
    if (receipt is None or receipt.run_id != sdk_run_id or receipt.task_scope_id != expected
            or receipt.receipt_hash != metadata.get('initial_route_receipt_hash')):
        raise ValueError('c05_actual_initial_receipt_differs')
    package = metadata.get('scope_disclosure')
    source = next(item for item in contract['archives'] if item['task_scope_id'] == expected)
    if (not isinstance(package, dict) or package.get('task_scope_id') != expected
            or package['disclosure']['fields'] != source['fields']
            or package['disclosure']['structure']['canonical_revision'] != source['canonical_revision']):
        raise ValueError('c05_initial_scope_disclosure_differs')
    latest = await ledger.latest_route_decision_for_run(sdk_run_id, task_only=True)
    return (latest is not None and latest['origin'] == 'host_initial'
            and latest['task_scope_id'] == expected and latest['receipt_id'] == receipt_id)

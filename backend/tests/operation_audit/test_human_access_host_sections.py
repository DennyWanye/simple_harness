"""G6: Host run-audit pages and memory-call journal behind the same signed grant."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from deskpet.memory.human_memory_api import send_human_memory_response
from deskpet.operation_audit import human_access
from deskpet.operation_audit.memory_attempts import MemoryAttemptJournal
from deskpet.operation_audit.store import AuditStore, TerminalSource
from tests.operation_audit.test_human_access import command, open_page_request, setup


def _head(index, *, state='succeeded', **extra):
    return {'kind': 'effect', 'record_type': 'head', 'operation_id': f'effect:{index:064x}',
            'operation_name': 'tool_search', 'state': state, 'error_code': None, 'created_at': 1.0 + index,
            'settled_at': 2.0 + index, 'handoff_to_settlement_seconds': 0.5, 'parent_operation_id': None,
            'effect_id': f'effect:{index:064x}', 'provider_invocation_id': None, 'request_hash': 'r' * 64,
            'result_hash': 's' * 64, 'source_hash': 't' * 64, 'usage': None,
            # Never disclosed: payload-looking keys are outside the projection whitelist.
            'content': 'SECRET-PAYLOAD', 'arguments': {'query': 'SECRET-QUERY'}, **extra}


async def seed_host_audit(path, runtime, *, jobs=1, operations=3):
    store = AuditStore(path)
    await store.initialize()
    refs = []
    for i in range(jobs):
        source = TerminalSource(host_run_id=f'host-{i}', sdk_run_id=f'product-sdk-{i}', owner_ref='owner',
                                terminal_ref=f'tr-{i}', terminal_hash=f'th-{i}', terminal_state='COMPLETED',
                                generation=1, admission_hash='adm')
        await store.admit(source)
        claim = await store.claim('worker')
        ops = [_head(j) for j in range(operations)]
        ops[1] = _head(1, state='failed', error_code='boom')
        ops[0]['usage'] = {'total_tokens': 5, 'input_tokens': 3, 'budget_kind': 'trusted_usage', 'nested': {'x': 1}}
        ops.append({'kind': 'runtime', 'record_type': 'boundary', 'operation_id': 'runtime:x',
                    'operation_name': 'context.no_recall', 'state': 'completed'})
        ops.append({'kind': 'tool', 'record_type': 'proposal', 'operation_id': 'proposal:y', 'state': 'proposed'})
        page = {'run_id': source.sdk_run_id, 'snapshot_hash': 'a' * 64, 'page_size': 128, 'total_operations': len(ops),
                'total_pages': 1, 'metadata': {'schema': 1}, 'page_index': 0, 'page_hash': 'b' * 64, 'next_cursor': None,
                'snapshot_source_complete': True, 'operations': ops, 'schema_version': 1}
        finding = {'rule_id': 'operation_error_observed', 'operation_id': ops[1]['operation_id'], 'source_hash': 'sh',
                   'owner_component': 'harness.effect', 'source_operation_id': ops[1]['operation_id']}
        await store.commit_page(claim, page, [finding])
        refs.append(source.job_id)
    journal = MemoryAttemptJournal(path)
    principal = runtime.principal()
    first = await journal.start(principal=principal, context=SimpleNamespace(context_hash='c' * 64, run_id='product-sdk-0'),
                                plan=SimpleNamespace(plan_hash='d' * 64), caller='foreground_recall', now=1.0, harness_protocol=4)
    await journal.settle(first, state='returned', status='not_applicable', result_hash='e' * 64)
    second = await journal.start(principal=principal, context=SimpleNamespace(context_hash='c' * 64, run_id='product-sdk-0'),
                                 plan=SimpleNamespace(plan_hash='d' * 64), caller='analysis_candidates', now=2.0, harness_protocol=4)
    await journal.settle(second, state='raised', status='absent')
    return refs, (first[0], second[0])


def host_request(request, section, target_ref=None, cursor_ref=None, action='host'):
    return {'audit_ref': request['audit_ref'], 'page_action_id': action, 'section': section,
            'cursor_ref': cursor_ref, 'target_ref': target_ref}


@pytest.mark.asyncio
async def test_host_sections_project_payload_free_rows_replay_and_close(tmp_path):
    f = await setup(tmp_path)
    runtime, access = f[6], f[6].audit_access_authority
    try:
        jobs, attempts = await seed_host_audit(access.path, runtime)
        request = await open_page_request(f)
        runs = await command(f, 'primary.audit.host.page', host_request(request, 'runs', action='runs-1'))
        assert runs['payload']['ok'], runs
        data = runs['payload']['result']
        assert data['section'] == 'runs' and data['target_ref'] is None and data['reads_used'] == 1
        assert data['max_reads'] == 32 and data['all_operations_recorded'] is False
        assert data['enumeration_complete'] is True and data['next_cursor_ref'] is None
        [job] = data['items']
        assert job['job_ref'] == jobs[0] and job['run_ref'] == 'product-sdk-0' and job['status'] == 'enumerated'
        assert job['findings'] == {'operation_error_observed': 1} and job['attempts'] == {'returned': 1}
        assert job['total_operations'] == 5 and job['pages_committed'] == 1
        assert data['coverage']['jobs_by_status'] == {'enumerated': 1}
        assert data['coverage']['producer_scope'] == 'foreground_terminal_only'

        ops = await command(f, 'primary.audit.host.page', host_request(request, 'run_operations', jobs[0], action='ops-1'))
        assert ops['payload']['ok'], ops
        items = ops['payload']['result']['items']
        assert [i['record_type'] for i in items] == ['head', 'head', 'head', 'boundary']
        assert items[1]['error_code'] == 'boom' and items[1]['state'] == 'failed'
        assert items[0]['usage'] == {'total_tokens': 5, 'input_tokens': 3}
        assert set(items[0]) == set(human_access._OPERATION_FIELDS) | {'usage'}
        serialized = json.dumps(ops)
        assert 'SECRET' not in serialized and 'arguments' not in serialized
        assert ops['payload']['result']['coverage']['findings'] == {'operation_error_observed': 1}
        assert ops['payload']['result']['coverage']['job_status'] == 'enumerated'

        calls = await command(f, 'primary.audit.host.page', host_request(request, 'memory_calls', action='calls-1'))
        assert calls['payload']['ok'], calls
        rows = calls['payload']['result']['items']
        assert [r['attempt_ref'] for r in rows] == [attempts[1], attempts[0]]  # newest first
        assert rows[0]['state'] == 'raised' and rows[0]['finding_reason'] == 'memory_call_outcome_unverified'
        assert rows[1]['result_hash'] == 'e' * 64 and rows[1]['finding_reason'] is None
        assert 'observation_json' not in rows[0]
        assert calls['payload']['result']['coverage']['callers'] == {'analysis_candidates': 1, 'foreground_recall': 1}

        # Same logical action replays the saved delivery; a changed input conflicts.
        assert await command(f, 'primary.audit.host.page', host_request(request, 'runs', action='runs-1')) == runs
        conflict = await command(f, 'primary.audit.host.page', host_request(request, 'runs', cursor_ref='other', action='runs-1'))
        assert conflict['payload']['error']['code'] == 'primary_audit_action_conflict'
        # A stream never restarts inside one grant.
        restart = await command(f, 'primary.audit.host.page', host_request(request, 'runs', action='runs-2'))
        assert restart['payload']['error']['code'] == 'primary_audit_cursor_invalid'
        for bad in (host_request(request, 'plaintext', action='x1'),
                    host_request(request, 'runs', jobs[0], action='x2'),
                    host_request(request, 'run_operations', action='x3'),
                    {**host_request(request, 'runs', action='x4'), 'purpose': 'content'}):
            assert (await command(f, 'primary.audit.host.page', bad))['payload']['error']['code'] == 'primary_audit_request_invalid'
        missing = await command(f, 'primary.audit.host.page', host_request(request, 'run_operations', 'no-such-job', action='x5'))
        assert missing['payload']['error']['code'] == 'primary_audit_target_unavailable'
        with sqlite3.connect(access.path) as db:
            assert db.execute("SELECT count(*) FROM human_audit_host_deliveries WHERE status='saved'").fetchone()[0] == 3
            assert db.execute('SELECT reads FROM human_audit_grants').fetchone()[0] == 0  # SDK budget untouched
        closed = await command(f, 'primary.audit.close', {'audit_ref': request['audit_ref']})
        assert closed['payload']['ok']
        after = await command(f, 'primary.audit.host.page', host_request(request, 'runs', action='runs-1'))
        assert after['payload']['error']['code'] == 'primary_audit_closed'
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_host_stream_keeps_snapshot_and_per_stream_budget(tmp_path, monkeypatch):
    monkeypatch.setitem(human_access.HOST_SECTIONS, 'runs', 1)
    monkeypatch.setattr(human_access, 'HOST_MAX_READS', 2)
    f = await setup(tmp_path)
    runtime, access = f[6], f[6].audit_access_authority
    try:
        await seed_host_audit(access.path, runtime, jobs=3)
        request = await open_page_request(f)
        first = (await command(f, 'primary.audit.host.page', host_request(request, 'runs', action='p1')))['payload']['result']
        assert len(first['items']) == 1 and first['next_cursor_ref'] and first['enumeration_complete'] is False
        # A job admitted after the snapshot does not join the stream.
        store = AuditStore(access.path)
        await store.admit(TerminalSource(host_run_id='late', sdk_run_id='product-sdk-late', owner_ref='owner',
                                         terminal_ref='tr-late', terminal_hash='th', terminal_state='FAILED',
                                         generation=1, admission_hash='adm'))
        second = (await command(f, 'primary.audit.host.page', host_request(request, 'runs', cursor_ref=first['next_cursor_ref'], action='p2')))['payload']['result']
        assert second['snapshot_hash'] == first['snapshot_hash'] and second['reads_used'] == 2
        assert second['items'][0]['job_ref'] != first['items'][0]['job_ref']
        assert 'product-sdk-late' not in json.dumps([first, second])
        denied = await command(f, 'primary.audit.host.page', host_request(request, 'runs', cursor_ref=second['next_cursor_ref'], action='p3'))
        assert denied['payload']['error']['code'] == 'primary_audit_budget_exhausted'
        # Budget is per stream: another section still reads.
        calls = await command(f, 'primary.audit.host.page', host_request(request, 'memory_calls', action='p4'))
        assert calls['payload']['ok'] and calls['payload']['result']['reads_used'] == 1
        with sqlite3.connect(access.path) as db:
            assert sorted(db.execute('SELECT section,reads FROM human_audit_host_streams').fetchall()) == [('memory_calls', 1), ('runs', 2)]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_host_page_fault_before_save_charges_nothing_and_sender_rechecks(tmp_path):
    f = await setup(tmp_path)
    _, ingress, binding, challenge, auth, factory, runtime, _ = f
    access = runtime.audit_access_authority
    try:
        await seed_host_audit(access.path, runtime)
        request = await open_page_request(f)
        def fault(point):
            if point == 'before_host_page':
                raise OSError('injected local read failure')
        access.fault = fault
        failed = await command(f, 'primary.audit.host.page', host_request(request, 'runs', action='h1'))
        assert failed['payload']['ok'] is False
        with sqlite3.connect(access.path) as db:
            assert db.execute('SELECT count(*) FROM human_audit_host_deliveries').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM human_audit_host_streams').fetchone()[0] == 0
        access.fault = lambda _: None
        response = await command(f, 'primary.audit.host.page', host_request(request, 'runs', action='h1'))
        assert response['payload']['ok'] and response['payload']['result']['reads_used'] == 1
        sent = []
        async def send(value):
            sent.append(value)
        with binding.request_scope(ingress, challenge):
            assert (await command(f, 'primary.audit.close', {'audit_ref': request['audit_ref']}))['payload']['ok']
            await send_human_memory_response(response, factory=factory, auth=auth, send=send)
        assert len(sent) == 1 and sent[0]['payload']['ok'] is False
        assert 'job_ref' not in json.dumps(sent)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_run_operations_stream_freezes_the_committed_page_window(tmp_path, monkeypatch):
    monkeypatch.setitem(human_access.HOST_SECTIONS, 'run_operations', 2)
    f = await setup(tmp_path)
    runtime, access = f[6], f[6].audit_access_authority
    try:
        jobs, _ = await seed_host_audit(access.path, runtime)
        request = await open_page_request(f)
        first = (await command(f, 'primary.audit.host.page',
                               host_request(request, 'run_operations', jobs[0], action='o1')))['payload']['result']
        assert len(first['items']) == 2 and first['next_cursor_ref'] and first['enumeration_complete'] is False
        assert first['coverage']['pages_frozen'] == 1
        # A page committed after the stream opened must not shift its offsets.
        with sqlite3.connect(access.path) as db:
            db.execute('INSERT INTO audit_pages(job_id,page_index,snapshot_hash,page_hash,input_hash,payload_json)'
                       ' VALUES(?,?,?,?,?,?)',
                       (jobs[0], 1, 'a' * 64, 'c' * 64, 'i' * 64,
                        json.dumps({'operations': [_head(99, operation_name='late_tool')]})))
            db.execute('UPDATE audit_jobs SET next_page=2 WHERE job_id=?', (jobs[0],))
        second = (await command(f, 'primary.audit.host.page',
                                host_request(request, 'run_operations', jobs[0],
                                             cursor_ref=first['next_cursor_ref'], action='o2')))['payload']['result']
        assert len(second['items']) == 2 and second['enumeration_complete'] is True
        assert second['coverage']['pages_frozen'] == 1 and 'late_tool' not in json.dumps(second)
    finally:
        await runtime.close()

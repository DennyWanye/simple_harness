"""Real signed HUMAN, Host SQLite/S1 and public installed Memory audit grant."""
import asyncio
import json
import sqlite3

import pytest
import simple_harness_memory as m

from deskpet.memory.control_binding import HumanMemoryControlBinding
from deskpet.memory.human_memory_api import (
    handle_human_memory_command, send_human_memory_response,
)
from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.memory.schema import dispatch_startup_epoch
from tests.companion.test_window_control_credentials import _ingress
from tests.memory.test_primary_control_binding import _bind


async def setup(tmp_path):
    private, _, _, ingress = _ingress(tmp_path)
    binding = HumanMemoryControlBinding()
    challenge = await _bind(private, ingress, binding)
    auth = binding.authenticate(ingress, challenge)
    path = tmp_path / 'state.db'
    startup = await dispatch_startup_epoch(path, approved_fresh_lane=True)
    runtime = compose_human_memory_runtime(path, tmp_path / 'memory.db', adapter_factory=lambda **_: None)
    factory = HumanMemoryHostServiceFactory(path, startup, cognitive_runtime_getter=lambda: runtime)
    with binding.request_scope(ingress, challenge):
        primary = (await factory.bind(auth).open_primary())['primary_ref']
    return private, ingress, binding, challenge, auth, factory, runtime, primary


async def command(fixture, operation, request, *, scoped=True):
    _, ingress, binding, challenge, auth, factory, _, primary = fixture
    raw = {'type': 'human_memory_request', 'request_id': 'transport', 'operation': operation,
           'request': {'primary_ref': primary, **request}}
    if scoped:
        with binding.request_scope(ingress, challenge):
            return await handle_human_memory_command(raw, factory=factory, auth=auth)
    return await handle_human_memory_command(raw, factory=factory, auth=auth)


@pytest.mark.asyncio
async def test_signed_open_public_page_exact_delivery_and_close(tmp_path):
    f = await setup(tmp_path)
    runtime = f[6]
    try:
        manager = await runtime.manager()
        principal = runtime.principal()
        await manager.suppress(principal=principal, request=m.SuppressionRequest(
            'seed-operation', principal.actor_id, m.SuppressionScopeKind.EVIDENCE,
            'test-evidence', 'user_forget', 1.0))
        denied = await command(f, 'primary.audit.open', {'open_action_id': 'open-1'}, scoped=False)
        assert denied['payload']['error']['code'] == 'primary_audit_verified_request_required'
        opened = await command(f, 'primary.audit.open', {'open_action_id': 'open-1'})
        assert opened['payload']['ok'], opened
        grant = opened['payload']['result']
        request = {'audit_ref': grant['audit_ref'], 'page_action_id': 'page-1', 'cursor_ref': None}
        first = await command(f, 'primary.audit.page', request)
        assert first['payload']['ok'], first
        data = first['payload']['result']
        assert data['items'] and data['all_operations_recorded'] is False
        assert len(data['items']) <= 100
        assert any(i['family'] == 'suppression' for i in data['items'])
        replay = await command(f, 'primary.audit.page', request)
        assert replay == first
        serialized = json.dumps(first)
        assert 'nonce' not in serialized and 'token' not in serialized
        assert 'test-evidence' not in serialized
        closed = await command(f, 'primary.audit.close', {'audit_ref': grant['audit_ref']})
        assert closed['payload']['ok'], closed
        assert (await command(f, 'primary.audit.page', request))['payload']['error']['code'] == 'primary_audit_closed'
        with sqlite3.connect(tmp_path / 'operation-audit.db') as db:
            assert db.execute('SELECT COUNT(*) FROM human_audit_deliveries').fetchone()[0] == 1
            assert db.execute('SELECT status FROM human_audit_deliveries').fetchone()[0] == 'saved'
    finally:
        await runtime.close()


async def open_page_request(f, key='open'):
    reply = await command(f, 'primary.audit.open', {'open_action_id': key})
    assert reply['payload']['ok'], reply
    return {'audit_ref': reply['payload']['result']['audit_ref'],
            'page_action_id': 'page', 'cursor_ref': None}


@pytest.mark.asyncio
@pytest.mark.parametrize('fault_point,saved', [
    ('after_save_before_ack', True), ('after_page_before_save', False),
])
async def test_public_sdk_success_ack_or_host_save_loss_never_rereads(tmp_path, monkeypatch, fault_point, saved):
    f = await setup(tmp_path)
    runtime, access = f[6], f[6].audit_access_authority
    try:
        request = await open_page_request(f)
        manager = await runtime.manager()
        original = manager.read_operation_audit
        calls = 0

        async def read(**kwargs):
            nonlocal calls
            calls += 1
            # The actual public SDK read starts only after durable requested.
            with sqlite3.connect(access.path) as db:
                assert db.execute('SELECT status FROM human_audit_deliveries').fetchone()[0] == 'requested'
            return await original(**kwargs)

        monkeypatch.setattr(manager, 'read_operation_audit', read)
        def fault(point):
            if point == fault_point:
                raise OSError('injected local delivery failure')
        access.fault = fault
        failed = await command(f, 'primary.audit.page', request)
        assert failed['payload']['ok'] is False
        access.fault = lambda _: None
        retry = await command(f, 'primary.audit.page', request)
        assert calls == 1
        with sqlite3.connect(access.path) as db:
            status, response = db.execute('SELECT status,response_json FROM human_audit_deliveries').fetchone()
            assert status == ('saved' if saved else 'unknown')
            assert db.execute('SELECT reads FROM human_audit_grants').fetchone()[0] == 1
        if saved:
            assert retry['payload']['result'] == json.loads(response)
        else:
            assert retry['payload']['error']['code'] == 'primary_audit_delivery_unknown'
            newer = await command(f, 'primary.audit.page', {**request, 'page_action_id': 'new'})
            assert newer['payload']['error']['code'] == 'primary_audit_delivery_unknown'
            assert calls == 1
        changed = await command(f, 'primary.audit.page', {**request, 'cursor_ref': 'other'})
        assert changed['payload']['error']['code'] == 'primary_audit_action_conflict'
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_cancel_after_sdk_success_preserves_unknown(tmp_path):
    f = await setup(tmp_path)
    runtime, access = f[6], f[6].audit_access_authority
    try:
        request = await open_page_request(f)
        def fault(point):
            if point == 'after_page_before_save':
                raise asyncio.CancelledError()
        access.fault = fault
        with pytest.raises(asyncio.CancelledError):
            await command(f, 'primary.audit.page', request)
        access.fault = lambda _: None
        reply = await command(f, 'primary.audit.page', request)
        assert reply['payload']['error']['code'] == 'primary_audit_delivery_unknown'
        with sqlite3.connect(access.path) as db:
            assert db.execute('SELECT reads,status FROM human_audit_grants').fetchone() == (1, 'unknown')
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_concurrent_same_action_calls_public_sdk_once(tmp_path, monkeypatch):
    f = await setup(tmp_path)
    runtime = f[6]
    try:
        request = await open_page_request(f)
        manager = await runtime.manager()
        original = manager.read_operation_audit
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def read(**kwargs):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return await original(**kwargs)
        monkeypatch.setattr(manager, 'read_operation_audit', read)
        one = asyncio.create_task(command(f, 'primary.audit.page', request))
        await entered.wait()
        two = asyncio.create_task(command(f, 'primary.audit.page', request))
        release.set()
        first, second = await asyncio.gather(one, two)
        assert first['payload']['ok'], first
        assert first == second and calls == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_close_during_sdk_read_revokes_output_without_cancelling_read(tmp_path, monkeypatch):
    f = await setup(tmp_path)
    runtime = f[6]
    try:
        request = await open_page_request(f)
        manager = await runtime.manager()
        original = manager.read_operation_audit
        entered, release = asyncio.Event(), asyncio.Event()
        async def read(**kwargs):
            result = await original(**kwargs)
            entered.set()
            await release.wait()
            return result
        monkeypatch.setattr(manager, 'read_operation_audit', read)
        task = asyncio.create_task(command(f, 'primary.audit.page', request))
        await entered.wait()
        closed = await command(f, 'primary.audit.close', {'audit_ref': request['audit_ref']})
        assert closed['payload']['ok'], closed
        release.set()
        result = await task
        assert result['payload']['error']['code'] == 'primary_audit_closed'
        with sqlite3.connect(runtime.audit_access_authority.path) as db:
            assert db.execute('SELECT reads,status FROM human_audit_grants').fetchone() == (1, 'closed')
            assert db.execute('SELECT status FROM human_audit_deliveries').fetchone()[0] == 'unknown'
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('revocation', ['close', 'expiry', 'rebind'])
async def test_final_ws_sender_rechecks_saved_page_after_revocation(tmp_path, revocation):
    f = await setup(tmp_path)
    private, ingress, binding, challenge, auth, factory, runtime, _ = f
    try:
        request = await open_page_request(f)
        response = await command(f, 'primary.audit.page', request)
        assert response['payload']['ok'], response
        sent = []
        async def send(value):
            sent.append(value)
        with binding.request_scope(ingress, challenge):
            # Enter while the lease is real/current, then revoke before the
            # final sender. A stale scope cannot be newly entered at all.
            if revocation == 'close':
                assert (await command(f, 'primary.audit.close', {'audit_ref': request['audit_ref']}))['payload']['ok']
            elif revocation == 'expiry':
                runtime.audit_access_authority.clock = lambda: response['payload']['result']['expires_at']
            else:
                await _bind(private, ingress, HumanMemoryControlBinding())
            await send_human_memory_response(response, factory=factory, auth=auth, send=send)
        assert len(sent) == 1 and sent[0]['payload']['ok'] is False
        assert 'items' not in json.dumps(sent)
        if revocation == 'rebind':
            from deskpet.memory.human_memory_service import HumanMemoryHostServiceError
            with pytest.raises(HumanMemoryHostServiceError, match='connection_stale'):
                await command(f, 'primary.audit.page', request)
        else:
            cached = await command(f, 'primary.audit.page', request)
            assert cached['payload']['ok'] is False
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_slow_runtime_preparation_rebind_prevents_action_and_authorization(tmp_path, monkeypatch):
    f = await setup(tmp_path)
    private, ingress, _, _, _, _, runtime, _ = f
    manager = await runtime.manager()
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed():
        entered.set()
        await release.wait()
        return manager
    monkeypatch.setattr(runtime, 'manager', delayed)
    try:
        task = asyncio.create_task(command(f, 'primary.audit.open', {'open_action_id': 'slow'}))
        await entered.wait()
        await _bind(private, ingress, HumanMemoryControlBinding())
        release.set()
        response = await task
        assert response['payload']['ok'] is False
        with runtime.audit_access_authority._db() as db:
            assert db.execute('SELECT COUNT(*) FROM human_audit_grants').fetchone()[0] == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_runtime_reopen_and_inherited_task_do_not_mint_or_restore_grant(tmp_path):
    f = await setup(tmp_path)
    _, ingress, binding, challenge, auth, factory, runtime, primary = f
    try:
        request = await open_page_request(f)
        with binding.request_scope(ingress, challenge):
            raw = {'type': 'human_memory_request', 'request_id': 'child',
                   'operation': 'primary.audit.open',
                   'request': {'primary_ref': primary, 'open_action_id': 'child'}}
            child = await asyncio.create_task(handle_human_memory_command(raw, factory=factory, auth=auth))
        assert child['payload']['error']['code'] == 'primary_audit_verified_request_required'
        await runtime.close()
        reopened = await command(f, 'primary.audit.page', request)
        assert reopened['payload']['error']['code'] == 'primary_audit_session_unavailable'
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_signed_open_replay_and_cross_primary_or_authority_injection(tmp_path):
    f = await setup(tmp_path)
    runtime, access = f[6], f[6].audit_access_authority
    try:
        for request in (
            {'open_action_id': 'bad-primary', 'primary_ref': 'another-primary'},
            {'open_action_id': 'authority', 'purpose': 'read_plaintext'},
            {'open_action_id': 'injected', 'authority': 'approved-by-assistant'},
        ):
            response = await command(f, 'primary.audit.open', request)
            assert response['payload']['ok'] is False
        with access._db() as db:
            assert db.execute('SELECT COUNT(*) FROM human_audit_grants').fetchone()[0] == 0
        first = await command(f, 'primary.audit.open', {'open_action_id': 'stable'})
        replay = await command(f, 'primary.audit.open', {'open_action_id': 'stable'})
        assert first['payload']['ok'] and first == replay
        with access._db() as db:
            rows = db.execute('SELECT evidence_id,issued_at,expires_at,receipt_hash FROM human_audit_grants').fetchall()
        assert len(rows) == 1 and rows[0]['expires_at'] - rows[0]['issued_at'] == 300
        assert rows[0]['evidence_id'] and rows[0]['receipt_hash']
        # Receipt/hash/authority stay server-side; UI cannot turn this grant
        # into an SDK sealed evidence content read.
        assert set(first['payload']['result']) == {
            'primary_ref', 'audit_ref', 'open_action_id', 'expires_at',
            'max_reads', 'page_limit', 'purpose',
        }
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_public_snapshot_pages_and_32_read_budget_without_live_fallback(tmp_path, monkeypatch):
    from deskpet.operation_audit import human_access
    # One item per page exercises the public cursor; no fake SDK DTO/counter.
    monkeypatch.setattr(human_access, 'PAGE_LIMIT', 1)
    f = await setup(tmp_path)
    runtime = f[6]
    try:
        manager, principal = await runtime.manager(), runtime.principal()
        for index in range(34):
            await manager.suppress(principal=principal, request=m.SuppressionRequest(
                f'seed-{index}', principal.actor_id, m.SuppressionScopeKind.EVIDENCE,
                f'evidence-{index}', 'user_forget', 1.0))
        request = await open_page_request(f)
        calls = 0
        original = manager.read_operation_audit
        async def read(**kwargs):
            nonlocal calls
            calls += 1
            return await original(**kwargs)
        monkeypatch.setattr(manager, 'read_operation_audit', read)
        snapshot = None
        first_coverage = None
        seen = set()
        for index in range(32):
            result = await command(f, 'primary.audit.page', request)
            assert result['payload']['ok'], result
            data = result['payload']['result']
            if snapshot is None:
                snapshot, first_coverage = data['snapshot_hash'], data['coverage']
                await manager.suppress(principal=principal, request=m.SuppressionRequest(
                    'late', principal.actor_id, m.SuppressionScopeKind.EVIDENCE,
                    'late-evidence', 'user_forget', 1.0))
            assert data['snapshot_hash'] == snapshot and data['coverage'] == first_coverage
            assert data['reads_used'] == index + 1
            for item in data['items']:
                assert item['item_hash'] not in seen
                seen.add(item['item_hash'])
            request = {**request, 'page_action_id': f'page-{index + 1}',
                       'cursor_ref': data['next_cursor_ref']}
            assert request['cursor_ref'] is not None
        denied = await command(f, 'primary.audit.page', request)
        assert denied['payload']['error']['code'] == 'primary_audit_budget_exhausted'
        assert calls == 32
        with runtime.audit_access_authority._db() as db:
            assert db.execute('SELECT reads FROM human_audit_grants').fetchone()[0] == 32
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_transport_failure_is_not_a_second_error_ack(tmp_path):
    f = await setup(tmp_path)
    _, ingress, binding, challenge, auth, factory, runtime, _ = f
    try:
        request = await open_page_request(f)
        response = await command(f, 'primary.audit.page', request)
        attempts = 0
        async def failed_send(_):
            nonlocal attempts
            attempts += 1
            raise OSError('socket closed')
        with binding.request_scope(ingress, challenge):
            with pytest.raises(OSError, match='socket closed'):
                await send_human_memory_response(response, factory=factory, auth=auth, send=failed_send)
        assert attempts == 1
        assert await command(f, 'primary.audit.page', request) == response
    finally:
        await runtime.close()

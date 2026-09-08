"""G5: scripted, UI-less end-to-end exercise of the controlled audit surface.

grant (primary.audit.open) → SDK OA1 page → Host run/operation/memory-call pages →
final-sender ACK delivery → close. Afterwards the HM-AC-7 coverage checker must
report every controlled surface as actually exercised from the durable archives.
"""
import json
import shutil

import pytest
import simple_harness_memory as m

from deskpet.memory.human_memory_api import send_human_memory_response
from deskpet.quality import audit_coverage as ac
from tests.operation_audit.test_human_access import command, setup
from tests.operation_audit.test_human_access_host_sections import host_request, seed_host_audit


@pytest.mark.asyncio
async def test_grant_page_ack_close_is_provably_reachable_and_visible_to_the_checker(tmp_path):
    f = await setup(tmp_path)
    _, ingress, binding, challenge, auth, factory, runtime, _ = f
    access = runtime.audit_access_authority
    delivered = []

    async def send(value):
        delivered.append(value)

    async def deliver(operation, request):
        # The production /ws/control path: dispatch, then the final sender
        # rechecks the grant under the live lease before the socket write.
        response = await command(f, operation, request)
        assert response['payload']['ok'], response
        with binding.request_scope(ingress, challenge):
            await send_human_memory_response(response, factory=factory, auth=auth, send=send)
        assert delivered[-1] == response
        return response['payload']['result']

    try:
        manager, principal = await runtime.manager(), runtime.principal()
        await manager.suppress(principal=principal, request=m.SuppressionRequest(
            'seed-operation', principal.actor_id, m.SuppressionScopeKind.EVIDENCE, 'seed-evidence', 'user_forget', 1.0))
        jobs, _ = await seed_host_audit(access.path, runtime)

        grant = await deliver('primary.audit.open', {'open_action_id': 'exercise-open'})
        request = {'audit_ref': grant['audit_ref'], 'page_action_id': 'sdk-1', 'cursor_ref': None}
        sdk_page = await deliver('primary.audit.page', request)
        assert any(i['family'] == 'suppression' for i in sdk_page['items'])
        runs = await deliver('primary.audit.host.page', host_request(request, 'runs', action='runs'))
        assert runs['items'][0]['job_ref'] == jobs[0]
        ops = await deliver('primary.audit.host.page', host_request(request, 'run_operations', jobs[0], action='ops'))
        assert ops['items'] and ops['coverage']['run_ref'] == 'product-sdk-0'
        calls = await deliver('primary.audit.host.page', host_request(request, 'memory_calls', action='calls'))
        assert len(calls['items']) == 2
        closed = await deliver('primary.audit.close', {'audit_ref': grant['audit_ref']})
        assert closed['status'] == 'closed'
        assert len(delivered) == 6 and all(d['payload']['ok'] for d in delivered)
        text = json.dumps(delivered)
        assert 'seed-evidence' not in text and 'SECRET' not in text and 'nonce' not in text
    finally:
        await runtime.close()

    # The checker reads the same evidence layout as a native run (state.db +
    # operation-audit.db + human_memory_v7.db) and must see the surface as used.
    shutil.copy(tmp_path / 'memory.db', tmp_path / 'human_memory_v7.db')
    ev = ac.Evidence(tmp_path, tmp_path / 'checker-work')
    try:
        facts = ac.surface_facts(ev)
    finally:
        ev.close()
    sdk = facts['primary.audit.page(OA1)']
    assert sdk['exercised'] is True and sdk['grants'] == 1 and sdk['deliveries'] == 1
    assert any((sdk[key] or 0) > 0 for key in ('sdk_sealed_access_events', 'sdk_trace_access_events', 'sdk_access_authority_events'))
    assert facts['host.audit_pages'] == {**facts['host.audit_pages'], 'exercised': True, 'host_deliveries': 2}
    assert facts['host.memory_call_attempts']['exercised'] is True and facts['host.memory_call_attempts']['host_deliveries'] == 1
    assert facts['host.audit_pages']['ui_operation'].startswith('primary.audit.host.page')

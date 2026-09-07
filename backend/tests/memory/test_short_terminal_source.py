"""Regression for real r8 source ancestry; no model or embedding invocation."""
import sqlite3
import time

import pytest
from simple_harness_memory import SuppressionRequest, SuppressionScopeKind

from deskpet.memory.short_indexing import PrimaryShortIndexingService
from tests.memory.test_short_index_worker import seed, env, deliver_all  # noqa: F401


@pytest.mark.asyncio
async def test_source_ancestry_with_active_forget_and_window_exit(env):
    await deliver_all(env)
    manager = await env.runtime.manager()
    authority = env.runtime.conversation_evidence_authority
    groups = [await authority.registrations_for_run(r) for r in await authority.completed_run_ids()]
    groups.sort(key=lambda g: g.registrations[0].metadata.causal_group_sequence)
    principal = env.runtime.principal()
    # An unrelated active directive causes duplicate-source ancestry traversal
    # for the first eligible group; direct suppression must still work later.
    await manager.suppress(principal=principal, request=SuppressionRequest(
        'terminal-source-unrelated-forget', principal.actor_id, SuppressionScopeKind.EVIDENCE,
        groups[1].registrations[0].envelope.evidence_id, 'user_forget', time.time()))
    result = await PrimaryShortIndexingService(authority, manager=manager, principal=principal).reconcile()
    assert len(result.groups) == 11 and not result.blocked
    assert result.projection.projected_chunk_count == 1
    assert all(len(g.registrations) == 2 for g in result.groups)
    await manager.suppress(principal=principal, request=SuppressionRequest(
        'terminal-source-direct-forget', principal.actor_id, SuppressionScopeKind.EVIDENCE,
        groups[0].registrations[0].envelope.evidence_id, 'user_forget', time.time()))
    result = await manager.rebuild_short_horizon_projection(principal=principal)
    assert result.projected_chunk_count == 0


@pytest.mark.asyncio
async def test_terminal_source_lost_ack_reopen_reuses_public_receipt(env):
    await deliver_all(env)
    authority = env.runtime.conversation_evidence_authority
    group = await authority.registrations_for_run((await authority.completed_run_ids())[0])
    manager = await env.runtime.manager()
    principal = env.runtime.principal()
    def fail(point):
        if point == 'short.after_terminal_source':
            raise RuntimeError('terminal-source-lost-ack')
    with pytest.raises(RuntimeError, match='terminal-source-lost-ack'):
        await PrimaryShortIndexingService(authority, manager=manager, principal=principal,
            fault_hook=fail).register_group(group)
    terminal, receipt = group.terminal_source
    admitted = await manager.admit_evidence_source(principal=principal, envelope=terminal, receipt=receipt)
    await env.runtime.close()
    manager = await env.runtime.manager()
    await PrimaryShortIndexingService(authority, manager=manager, principal=principal).register_group(group)
    replay = await manager.admit_evidence_source(principal=principal, envelope=terminal, receipt=receipt)
    assert admitted == replay
    assert (await authority.registrations_for_run(group.host_run_id)).references == group.references


@pytest.mark.asyncio
async def test_terminal_source_tamper_still_rejected_by_host_reader(env):
    await deliver_all(env)
    authority = env.runtime.conversation_evidence_authority
    group = await authority.registrations_for_run((await authority.completed_run_ids())[0])
    with sqlite3.connect(env.state) as db:
        with pytest.raises(sqlite3.IntegrityError, match='human_memory_append_only'):
            db.execute('UPDATE human_memory_sanitization_receipts SET receipt_sha256=? WHERE receipt_id=?',
                ('0' * 64, group.terminal_source[1].receipt_id))
    assert (await authority.registrations_for_run(group.host_run_id)).references == group.references
    manager = await env.runtime.manager()
    with pytest.raises(ValueError):
        await manager.admit_evidence_source(principal=env.runtime.principal(),
            envelope=group.terminal_source[0], receipt=group.registrations[0].admission_receipt)

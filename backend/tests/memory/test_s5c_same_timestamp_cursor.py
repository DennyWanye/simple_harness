"""Host cursor controls; scripted transport, not an SDK authority oracle."""
from dataclasses import replace

import pytest

from deskpet.memory.s5c_consumer import ProspectiveRegistrationConsumer
from deskpet.memory.s5c_store import S5cStore, S5cConflict
from tests.memory.test_s5c_store import P, ready, registration
from tests.memory.test_s5c_consumer import Source, Memory


class RevisitingSource(Source):
    async def prepare_registration(self, *, principal, entry, revisit_same_timestamp=False):
        return await super().prepare_registration(principal=principal, entry=entry)


@pytest.mark.asyncio
async def test_late_small_key_is_indexed_once_and_scan_continues_after_restart(tmp_path):
    path, store = await ready(tmp_path)
    pairs = [registration(key, 10.0) for key in ('a', 'b', 'z')]
    source = RevisitingSource(*pairs)
    memory = Memory(source, [pairs[-1][0]])
    await ProspectiveRegistrationConsumer(store, memory, source).run_once()
    original = await store.registration('z')
    memory.entries = [entry for entry, _ in pairs]
    consumer = ProspectiveRegistrationConsumer(store, memory, source)
    await consumer.run_once(page_size=1, max_pages=1)
    assert await store.cursor() == (10.0, 'a')
    assert await store.scan_high_water() == (10.0, 'z')
    await consumer.run_once(page_size=1, max_pages=1)
    assert await store.registration('b') is not None
    reopened = S5cStore(path, P)
    await ProspectiveRegistrationConsumer(reopened, memory, source).run_once()
    page, after, upper = await reopened.page_accepted_registrations(limit=10)
    assert {item.entry.outbox_id for item in page} == {'a', 'b', 'z'}
    assert after == upper == 3
    assert len(memory.calls) == 3
    assert await reopened.registration('z') == original
    memory.entries[0] = replace(memory.entries[0], payload_hash='f' * 64)
    with pytest.raises(S5cConflict):
        await ProspectiveRegistrationConsumer(reopened, memory, source).run_once()
    assert len(memory.calls) == 3


@pytest.mark.asyncio
async def test_late_append_rejects_stale_consumption_cas(tmp_path):
    _, store = await ready(tmp_path)
    z, za = registration('z', 10.0)
    a, aa = registration('a', 10.0)
    b, ba = registration('b', 10.0)
    await store.commit_registration(z, za, expected_cursor=None)
    old = await store.cursor()
    await store.commit_registration(a, aa, expected_cursor=old, revisit_same_timestamp=True)
    with pytest.raises(S5cConflict, match='s5c_cursor_conflict'):
        await store.commit_registration(b, ba, expected_cursor=old, revisit_same_timestamp=True)
    assert await store.registration('b') is None
    assert await store.scan_high_water() == old


@pytest.mark.asyncio
@pytest.mark.parametrize('point', ['s5c.terminal.before_commit', 's5c.terminal.after_commit'])
async def test_real_terminal_receipt_late_key_atomic_reopen(tmp_path, monkeypatch, point):
    # Real SDK public legacy/upgrade/settlement receipt. Only the higher Host
    # cursor row is scripted: this isolates the new store ordering contract.
    from tests.memory.test_prospective_consumer_m617 import legacy_world
    w, entry = await legacy_world(tmp_path, monkeypatch)
    try:
        source = await w.manager.read_prospective_outbox_source_v2(
            principal=P, outbox_id=entry.outbox_id, payload_hash=entry.payload_hash)
        receipt = await w.manager.settle_prospective_invalidation(principal=P,
            outbox_id=entry.outbox_id, payload_hash=entry.payload_hash,
            expected_source_hash=source.source_hash)
        store = S5cStore(w.path, P)
        high, authority = registration('zz-scripted-host-cursor', entry.created_at)
        await store.commit_registration(high, authority, expected_cursor=await store.cursor())
        tail = await store.cursor()
        calls = len(w.calls)
        def fault(where):
            if where == point:
                raise ConnectionError('late terminal fault')
        faulty = S5cStore(w.path, P, fault_inject=fault)
        with pytest.raises(ConnectionError, match='late terminal fault'):
            await faulty.commit_not_required(entry, receipt, expected_source_hash=source.source_hash,
                expected_cursor=tail, revisit_same_timestamp=True)
        reopened = S5cStore(w.path, P)
        assert (await reopened.terminal(entry.outbox_id) is not None) == point.endswith('after_commit')
        await reopened.commit_not_required(entry, receipt, expected_source_hash=source.source_hash,
            expected_cursor=await reopened.cursor(), revisit_same_timestamp=True)
        latest = await reopened.cursor()
        await reopened.commit_not_required(entry, receipt, expected_source_hash=source.source_hash,
            expected_cursor=latest, revisit_same_timestamp=True)
        assert await reopened.cursor() == latest == (entry.created_at, entry.outbox_id)
        assert await reopened.scan_high_water() == tail
        assert await reopened.terminal(entry.outbox_id) == receipt
        assert len(w.calls) == calls
    finally:
        await w.manager.close()

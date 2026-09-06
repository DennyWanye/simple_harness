"""Actual SDK source reads, Host grants and public registration delivery."""
import asyncio
from dataclasses import replace

import pytest
from simple_harness_memory import MemoryScope
from simple_harness_memory.backends.sqlite_v5 import SQLiteHumanMemoryBackend

from deskpet.memory.human_memory_service import HOST_PUBLIC_TURN_FILTER_POLICY
from deskpet.memory.prospective_registration_source import PublicRegistrationAuthoritySource
from deskpet.memory.s5c_consumer import ProspectiveRegistrationConsumer
from deskpet.memory.s5c_store import HostProspectiveSignalAuthority, S5cConflict, S5cStore
from tests.memory.test_s5c_consumer_sdk import fixture
from tests.memory.test_s5c_store import P


async def registration_entry(memory):
    page = await memory.read_outbox(principal=P)
    return next(e for e in page.entries if e.topic == "memory.prospective.registration.requested")


@pytest.mark.asyncio
async def test_public_source_delivers_actual_target_receipt_and_run(tmp_path):
    clock = [20.0]
    path, memory, _unused_fixture_authority = await fixture(tmp_path, clock)
    try:
        store = S5cStore(path, P)
        source = PublicRegistrationAuthoritySource(store=store, memory=memory, clock=lambda: clock[0])
        entry = await registration_entry(memory)
        facts = await memory.read_prospective_outbox_source(
            principal=P, outbox_id=entry.outbox_id, payload_hash=entry.payload_hash)
        assert facts.target_plan_id == "s5c-test-plan"
        assert facts.target_operation_id == "s5c-create"
        assert facts.outbox_created_at == entry.created_at
        for changed in (replace(entry, created_at=entry.created_at - 1),
                        replace(entry, idempotency_key="foreign-key")):
            with pytest.raises(S5cConflict, match="s5c_public_outbox_source_differs"):
                await source.prepare_registration(principal=P, entry=changed)
            assert await store.registration(entry.outbox_id) is None
            assert await store.cursor() is None
        await ProspectiveRegistrationConsumer(store, memory, source).run_once()
        prepared = await store.registration(entry.outbox_id)
        assert prepared.result is not None
        intent = prepared.authority.intent
        assert intent.run_id == facts.target_run_id
        assert intent.operation_id == "s5c-create"
        assert intent.signal_receipt_id == facts.target_mutation_receipt_ref.receipt_id
        assert intent.signal_receipt_hash == facts.target_mutation_receipt_ref.receipt_hash
        assert intent.outbox_id == entry.outbox_id and intent.outbox_payload_hash == entry.payload_hash
        assert prepared.result.decided_at == clock[0]
        assert await store.accepted_registration(memory_id=facts.target_memory_id,
                                                   revision=facts.target_revision) == prepared
        assert facts.outbox_cause_status == "not_persisted"
        # An externally supplied conflicting entry cannot reuse a fixed grant.
        with pytest.raises(S5cConflict, match="s5c_registration_source_differs"):
            await source.prepare_registration(principal=P, entry=replace(entry, payload_hash="0" * 64))
        with pytest.raises(S5cConflict, match="s5c_source_principal_differs"):
            await source.prepare_registration(principal=replace(P, actor_id="other"), entry=entry)
    finally:
        await memory.close()


@pytest.mark.asyncio
async def test_concurrent_public_source_calls_keep_first_observation(tmp_path, monkeypatch):
    path, memory, _ = await fixture(tmp_path, [20.0])
    try:
        store = S5cStore(path, P)
        entry = await registration_entry(memory)
        read = memory.read_prospective_outbox_source
        both = asyncio.Event()
        called = 0
        async def barrier(**kwargs):
            nonlocal called
            result = await read(**kwargs)
            called += 1
            if called == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 3)
            return result
        monkeypatch.setattr(memory, "read_prospective_outbox_source", barrier)
        tick = [20.0]
        def clock():
            tick[0] += .1
            return tick[0]
        source = PublicRegistrationAuthoritySource(store=store, memory=memory, clock=clock)
        first, second = await asyncio.gather(*[
            source.prepare_registration(principal=P, entry=entry) for _ in range(2)])
        assert first == second
        assert called == 2
        pending = await store.pending_registrations()
        assert len(pending) == 1 and pending[0].authority == first
        assert await store.cursor() == (entry.created_at, entry.outbox_id)
    finally:
        await memory.close()


@pytest.mark.asyncio
async def test_actual_source_grant_lost_ack_reopens_after_expiry(tmp_path):
    clock = [20.0]
    fired = False
    def fault(point):
        nonlocal fired
        if point == "prospective.after_commit" and not fired:
            fired = True
            raise ConnectionError(point)
    path, memory, _ = await fixture(tmp_path, clock, fault)
    try:
        store = S5cStore(path, P)
        source = PublicRegistrationAuthoritySource(store=store, memory=memory,
            clock=lambda: clock[0], lifetime_seconds=5.0)
        with pytest.raises(ConnectionError, match="prospective.after_commit"):
            await ProspectiveRegistrationConsumer(store, memory, source).run_once()
        prepared = (await store.pending_registrations())[0]
        await memory.close()
        clock[0] = 40.0
        memory = SQLiteHumanMemoryBackend(tmp_path / "memory.db", now=lambda: clock[0],
            supported_filter_policies=frozenset({HOST_PUBLIC_TURN_FILTER_POLICY}),
            prospective_signal_authority=HostProspectiveSignalAuthority(path, P))
        await memory.initialize()
        store = S5cStore(path, replace(P, session_id="reopened"))
        class NoNewSource:
            async def prepare_registration(self, **kwargs):
                raise AssertionError("recovery must use the durable grant")
        await ProspectiveRegistrationConsumer(store, memory, NoNewSource()).run_once()
        restored = await store.registration(prepared.entry.outbox_id)
        assert restored.reference == prepared.reference and restored.result is not None
        assert restored.result.decided_at == 20.0
        assert await memory.apply_prospective_signal(principal=P, scope=MemoryScope.personal(P.actor_id),
            reference=prepared.reference) == restored.result
    finally:
        await memory.close()

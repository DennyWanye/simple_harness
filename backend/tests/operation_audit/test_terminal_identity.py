"""Installed SDK proof against actual Host terminal authorities; no SDK SQL."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace

import aiosqlite
import pytest
import simple_harness as sdk

from deskpet.execution.terminal_identity import read_primary_terminal_identity_tx
from deskpet.memory.human_memory_service import (
    CreateTaskScopeRequest, HumanMemoryHostServiceFactory, QueueTurnRequest,
)
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.operation_audit.consumer import PublicAuditReader
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.sdk_adapters.runtime_paths import ProductRuntimePathsAdapter
from tests.execution.test_primary_foreground_runtime import Provider, build
from tests.operation_audit.test_audit_recovery import consumer_at
from tests.operation_audit.test_terminal_audit import drain, setup
from tests.sdk_adapters.s5b_effect_gate_harness import bind_scope_root

pytestmark = pytest.mark.asyncio


async def identity_for(state, source):
    async with aiosqlite.connect(state.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        await db.execute("BEGIN")
        cursor = await db.execute(
            "SELECT primary_conversation_id FROM foreground_runs WHERE host_run_id=?",
            (source.host_run_id,),
        )
        row = await cursor.fetchone()
        return await read_primary_terminal_identity_tx(
            db, subject=local_owner_auth().subject, primary_ref=row[0],
            host_run_id=source.host_run_id, sdk_run_id=source.sdk_run_id,
        )


async def legacy_setup(tmp_path):
    state = tmp_path / "state.db"
    epoch = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, epoch).bind(local_owner_auth())
    await service.open_primary()
    scope = await service.create_task_scope(
        CreateTaskScopeRequest("legacy", "Task", "Actual scoped work", "create")
    )
    root = tmp_path / "legacy-root"
    root.mkdir()
    await bind_scope_root(state, scope["scope_ref"], root)
    await service.enqueue_turn(QueueTurnRequest(scope["scope_ref"], "legacy", "Actual user"))
    provider = Provider()
    runtime, stack, queue = await build(tmp_path, state, provider, legacy_observer=True)
    assert await asyncio.wait_for(runtime._drive_once(), 15)
    assert runtime.last_error is None
    return state, service, provider, runtime, stack, queue


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("fault", ["payload", "record", "envelope", "ref", "state", "missing"])
async def test_wrong_public_terminal_proof_cannot_persist_first_page(tmp_path, legacy, fault):
    state, _, provider, runtime, stack, _ = await (legacy_setup(tmp_path) if legacy else setup(tmp_path))
    consumer = await consumer_at(state, stack, [100.0])
    try:
        source = (await consumer.sources.read())[0]
        identity = await identity_for(state, source)
        assert identity.legacy_scoped is legacy
        with sqlite3.connect(state) as db:
            envelope = db.execute(
                "SELECT sdk_event_hash FROM foreground_terminal_receipts WHERE host_run_id=?",
                (source.host_run_id,),
            ).fetchone()[0]
        assert envelope != identity.raw_sdk_event_hash

        class WrongProof(PublicAuditReader):
            async def read(self, claim, *, page_size):
                page = await super().read(claim, page_size=page_size)
                metadata = page.to_json()["metadata"]
                proof = dict(metadata["terminal_evidence"])
                assert proof["event_payload_hash"] == identity.raw_sdk_event_hash
                if fault in {"payload", "record", "envelope"}:
                    proof["event_payload_hash"] = {
                        "payload": "0" * 64, "record": proof["event_record_hash"],
                        "envelope": envelope,
                    }[fault]
                    assert proof["event_payload_hash"] != identity.raw_sdk_event_hash
                elif fault == "ref":
                    proof["event_ref"] = "terminal_event:" + "0" * 64
                elif fault == "state":
                    proof.update(state="failed", event_kind="run.failed")
                metadata["terminal_evidence"] = None if fault == "missing" else proof
                return replace(page, metadata=metadata)

        consumer.reader = WrongProof(lambda: stack)
        await consumer.tick(max_pages=1)
        record = await consumer.store.inspect(source.job_id)
        assert record["job"]["status"] == "unavailable"
        assert record["job"]["last_code"] == "terminal_identity_unverified"
        assert record["pages"] == record["findings"] == []
        assert len(provider.requests) == 1
    finally:
        await consumer.close()
        await runtime.close()
        await stack.close()


@pytest.mark.parametrize("legacy", [False, True])
async def test_real_exact_terminal_authority_enumerates_without_evidence_rewrite(tmp_path, legacy):
    state, _, provider, runtime, stack, _ = await (legacy_setup(tmp_path) if legacy else setup(tmp_path))
    consumer = await consumer_at(state, stack, [100.0])
    try:
        source = (await consumer.sources.read())[0]
        identity = await identity_for(state, source)
        assert identity.legacy_scoped is legacy
        with sqlite3.connect(state) as db:
            before = db.execute("SELECT evidence_id,envelope_sha256 FROM human_memory_evidence ORDER BY evidence_id").fetchall()
        await drain(consumer)
        record = await consumer.store.inspect(source.job_id)
        assert record["job"]["status"] == "enumerated"
        assert record["job"]["rule_version"] == "terminal-run-v2"
        for row in record["pages"]:
            proof = sdk.RunTerminalAuditEvidenceV1.from_json(json.loads(row["payload_json"])["metadata"]["terminal_evidence"])
            assert proof.matches(event_id=identity.raw_sdk_event_id,
                                 payload_hash=identity.raw_sdk_event_hash, state="completed")
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT evidence_id,envelope_sha256 FROM human_memory_evidence ORDER BY evidence_id").fetchall() == before
        assert len(provider.requests) == 1
    finally:
        await consumer.close()
        await runtime.close()
        await stack.close()


async def test_nonnull_committed_turn_public_head_and_exact_pages_survive_reopen(tmp_path):
    from simple_harness.execution.memory_outbox import MemoryOutboxRepository
    from simple_harness.execution.sqlite import Database, SqliteExecutionUnitOfWork
    from simple_harness.runtime.start_snapshot import StartSnapshot
    from simple_harness_memory import MemoryManager

    memory = await MemoryManager.build_development(tmp_path / "agent-memory.db")
    state, _, provider, runtime, stack, _ = await setup(tmp_path, memory=memory)
    consumer = await consumer_at(state, stack, [100.0])
    try:
        source = (await consumer.sources.read())[0]
        identity = await identity_for(state, source)
        path = ProductRuntimePathsAdapter(tmp_path / "sdk").execution_database
        with Database.open(path) as db:
            uow = SqliteExecutionUnitOfWork(db)
            start = StartSnapshot.from_json(uow.read_start_snapshot(source.sdk_run_id))
            assert start.conversation.memory_text == "PRIVATE_USER_CANARY"
            record = MemoryOutboxRepository(db).read(f"agent-memory-turn/v1/{start.turn_id}")
            assert record is not None and record.run_id == source.sdk_run_id
            actual = record.committed_turn()
            turn_hash = record.payload_hash
            assert turn_hash == actual.payload_hash
            snapshot = uow.read_run_operation_audit(sdk.RunId(source.sdk_run_id))
            assert any(op.operation_name == "memory.outbox.created" and op.request_hash == turn_hash
                       for op in snapshot.operations)
            assert snapshot.terminal_evidence.event_payload_hash == identity.raw_sdk_event_hash
            assert turn_hash != identity.raw_sdk_event_hash
            outbox_before = record
        assert await consumer.tick(max_pages=1) == 1
        first = await consumer.store.inspect(source.job_id)
        cursor = first["job"]["next_cursor"]
        assert cursor is not None
        await consumer.close()
        await runtime.close()
        await stack.close()
        runtime, stack, _ = await build(tmp_path, state, provider, memory=memory)
        consumer = await consumer_at(state, stack, [100.0])
        await drain(consumer)
        final = await consumer.store.inspect(source.job_id)
        assert final["job"]["status"] == "enumerated"
        assert final["job"]["snapshot_hash"] == first["job"]["snapshot_hash"]
        assert consumer.reader.calls[0][1] == cursor
        assert final["pages"][0] == first["pages"][0]
        for row in final["pages"]:
            proof = sdk.RunTerminalAuditEvidenceV1.from_json(json.loads(row["payload_json"])["metadata"]["terminal_evidence"])
            assert proof.matches(event_id=identity.raw_sdk_event_id, payload_hash=identity.raw_sdk_event_hash, state="completed")
        with Database.open(path) as db:
            assert MemoryOutboxRepository(db).read(outbox_before.intent_id) == outbox_before
        assert len(provider.requests) == 1
        await drain(consumer)
        assert await consumer.store.inspect(source.job_id) == final
    finally:
        await consumer.close()
        await runtime.close()
        await stack.close()
        await memory.close()


async def test_foreign_real_run_proof_cannot_be_relabelled_as_selected_run(tmp_path):
    state, service, provider, runtime, stack, _ = await setup(tmp_path)
    consumer = await consumer_at(state, stack, [100.0])
    try:
        source = (await consumer.sources.read())[0]
        await service.enqueue_turn(QueueTurnRequest(None, "second", "Another actual turn"))
        assert await runtime._drive_once()
        other = next(row for row in await consumer.sources.read() if row.sdk_run_id != source.sdk_run_id)
        client = stack.require_ready().client
        foreign = await client.open_run_operation_audit(sdk.RunId(other.sdk_run_id), page_size=2)
        foreign_proof = foreign.to_json()["metadata"]["terminal_evidence"]
        # Admit only selected job; direct verifier also rejects a whole foreign page.
        await consumer.store.admit(source)
        claim = await consumer.store.claim("probe", lease_seconds=2)
        assert not await consumer.sources.verify_terminal_page(claim.job, foreign.to_json())
        own = await PublicAuditReader(lambda: stack).read(claim, page_size=2)
        value = own.to_json()
        value["metadata"]["terminal_evidence"] = foreign_proof
        assert not await consumer.sources.verify_terminal_page(claim.job, value)
        await consumer.store.unavailable(claim, "terminal_identity_unverified")
        record = await consumer.store.inspect(source.job_id)
        assert record["pages"] == []
        assert len(provider.requests) == 2
    finally:
        await consumer.close()
        await runtime.close()
        await stack.close()


async def test_late_host_source_change_rejects_next_page_preserving_saved_prefix(tmp_path):
    state, _, provider, runtime, stack, _ = await setup(tmp_path)
    consumer = await consumer_at(state, stack, [100.0])
    started, release = asyncio.Event(), asyncio.Event()
    pending = None
    other_runtime = other_stack = None
    try:
        source = (await consumer.sources.read())[0]
        await consumer.tick(max_pages=1)
        before = await consumer.store.inspect(source.job_id)
        assert before["pages"] and before["job"]["next_cursor"]

        class SlowReader(PublicAuditReader):
            async def read(self, claim, *, page_size):
                result = await super().read(claim, page_size=page_size)
                started.set()
                await release.wait()
                return result

        consumer.reader = SlowReader(lambda: stack)
        pending = asyncio.create_task(consumer.tick(max_pages=1))
        await asyncio.wait_for(started.wait(), 5)
        # Actual foreign Host storage selected during an in-flight SDK read.
        # Keep both original ledgers and their guards/evidence intact.
        other_root = tmp_path / "foreign-host"
        other_root.mkdir()
        other_state, _, other_provider, other_runtime, other_stack, _ = await setup(other_root)
        consumer.sources.state_path = other_state
        release.set()
        await asyncio.wait_for(pending, 5)
        after = await consumer.store.inspect(source.job_id)
        assert after["job"]["status"] == "unavailable"
        assert after["job"]["last_code"] == "source_binding_invalid"
        assert len(other_provider.requests) == 1
        assert after["pages"] == before["pages"]
        assert after["job"]["next_cursor"] == before["job"]["next_cursor"]
        assert len(provider.requests) == 1
    finally:
        release.set()
        if pending is not None and not pending.done():
            await pending
        if other_runtime is not None:
            await other_runtime.close()
        if other_stack is not None:
            await other_stack.close()
        await consumer.close()
        await runtime.close()
        await stack.close()


@pytest.mark.parametrize("old_status", ["pending", "unavailable", "partial", "enumerated"])
async def test_v2_discovery_preserves_v1_jobs_attempts_and_pages(tmp_path, monkeypatch, old_status):
    from deskpet.operation_audit import sources as source_module, store as store_module

    state, _, provider, runtime, stack, _ = await setup(tmp_path)
    consumer = await consumer_at(state, stack, [100.0])
    try:
        # Exercise the existing durable journal with its prior rule key. Actual SDK
        # pages remain real; this does not claim to rerun an old SDK producer.
        with monkeypatch.context() as old:
            old.setattr(source_module, "RULE_VERSION", "terminal-run-v1")
            old.setattr(store_module, "RULE_VERSION", "terminal-run-v1")
            source = (await consumer.sources.read())[0]
            old_id = source.job_id
            await consumer.store.admit(source)
            if old_status == "unavailable":
                claim = await consumer.store.claim("old", lease_seconds=2)
                await consumer.store.unavailable(claim, "capability_unavailable")
            elif old_status == "partial":
                await consumer.tick(max_pages=1)
            elif old_status == "enumerated":
                await drain(consumer)
            before = await consumer.store.inspect(old_id)
        new = (await consumer.sources.read())[0]
        assert new.job_id != old_id
        await drain(consumer)
        after = await consumer.store.inspect(new.job_id)
        assert after["job"]["status"] == "enumerated"
        assert after["job"]["rule_version"] == "terminal-run-v2"
        assert await consumer.store.inspect(old_id) == before
        assert len(provider.requests) == 1
    finally:
        await consumer.close()
        await runtime.close()
        await stack.close()

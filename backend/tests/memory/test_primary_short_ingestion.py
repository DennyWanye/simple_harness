"""Actual foreground/S1/outbox and installed Memory/Harness SQLite consumers.

Provider response is deterministic; no conversation metadata, groups, admission
receipts, expected SDK results or SQL rows are fabricated by the fixture.
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest
from deskpet.memory.conversation_registration import (
    PrimaryConversationAuthority,
)
from deskpet.memory.human_memory_service import (
    HumanMemoryHostServiceFactory,
    QueueTurnRequest,
)
from deskpet.memory.human_memory_v7 import (
    HOST_SUPPORTED_FILTER_POLICIES,
    host_classification_policy,
    local_memory_principal,
    local_memory_scope,
)
from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.memory.short_indexing import PrimaryShortIndexingService
from deskpet.sdk_adapters.context_route import local_owner_auth
from simple_harness_memory import (
    MemoryManager,
    SuppressionRequest,
    SuppressionScopeKind,
)
from tests.execution.test_primary_foreground_runtime import (
    Provider,
    build,
    history_disclosure,
)


async def memory(path, authority):
    manager = await MemoryManager.build_human_memory_v7(path,
        supported_filter_policies=HOST_SUPPORTED_FILTER_POLICIES,
        conversation_evidence_authority=authority, classification_policy=host_classification_policy())
    await manager.register_principal_owner(local_memory_principal(), local_memory_scope())
    return manager


async def real_turns(tmp_path, count):
    state = tmp_path / "state.db"
    service = HumanMemoryHostServiceFactory(state,
        await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    primary = (await service.open_primary())["primary_ref"]
    runtime, stack, queue = await build(tmp_path, state, Provider())
    try:
        for index in range(count):
            text = "quartznebula early real preference" if index == 0 else f"topazmarker current message {index}"
            await service.enqueue_turn(QueueTurnRequest(None, f"source-{index}", text))
            assert await asyncio.wait_for(runtime._drive_once(), 15)
            assert runtime.last_error is None
            assert await queue.current_snapshot(local_owner_auth().subject) is None
    finally:
        await runtime.close()
        await stack.close()
    authority = PrimaryConversationAuthority(state, subject=local_owner_auth().subject, primary_ref=primary)
    manager = await memory(tmp_path / "index.db", authority)
    worker = MemoryIngestionOutboxWorker(state, lambda: manager, owner_id="short-test")
    for _ in range(count):
        assert await worker.run_once() == "delivered"
    return state, service, authority, manager


async def recall(manager, query):
    return await manager.recall_short_horizon(principal=local_memory_principal(), query=query,
        disclosure_context=history_disclosure(), limit=20)


@pytest.mark.asyncio
async def test_real_eleven_completed_groups_outbox_short_recall(tmp_path):
    state, _, authority, manager = await real_turns(tmp_path, 11)
    try:
        result = await PrimaryShortIndexingService(authority, manager=manager, principal=local_memory_principal()).reconcile()
        assert result.blocked == ()
        assert len(result.groups) == 11
        for group in result.groups:
            assert len(group.registrations) == 2
            assert [r.metadata.role.value for r in group.registrations] == ["user", "assistant"]
            assert [r.metadata.item_ordinal for r in group.registrations] == [1, 2]
            assert {r.metadata.group_item_count for r in group.registrations} == {2}
            assert [r.metadata.public_text_json_pointer for r in group.registrations] == ["/text", "/source/message/content"]
            assert len({r.metadata.ordered_group_manifest_hash for r in group.registrations}) == 1
        hits = await recall(manager, "quartznebula")
        assert len(hits.hits) == 1
        assert hits.hits[0].content == "user: quartznebula early real preference\nassistant: Actual response 1"
        recent_query = await recall(manager, "topazmarker")
        assert all(hit.content == "user: quartznebula early real preference\nassistant: Actual response 1"
                   for hit in recent_query.hits)
        assert recent_query.eligible_count == 1
        assert result.projection.projected_chunk_count == 1
        assert len(result.visibility_dependencies["evidence"]) == 22
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT count(*) FROM memory_ingestion_outbox WHERE state='delivered'").fetchone()[0] == 11
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["short.after_ingest", "short.after_registration", "short.before_projection"])
async def test_crash_reopen_exact_replay(tmp_path, point):
    _, _, authority, manager = await real_turns(tmp_path, 11)
    references = tuple(group.references for group in [
        await authority.registrations_for_run(run) for run in await authority.completed_run_ids()])
    def crash(current):
        if current == point:
            raise RuntimeError("simulated process stop")
    try:
        with pytest.raises(RuntimeError, match="simulated process stop"):
            await PrimaryShortIndexingService(authority, manager=manager,
                principal=local_memory_principal(), fault_hook=crash).reconcile()
    finally:
        await manager.close()
    authority = PrimaryConversationAuthority(authority.db_path, subject=authority.subject, primary_ref=authority.primary_ref)
    manager = await memory(tmp_path / "index.db", authority)
    try:
        result = await PrimaryShortIndexingService(authority, manager=manager, principal=local_memory_principal()).reconcile()
        assert tuple(g.references for g in result.groups) == references
        assert len((await recall(manager, "quartznebula")).hits) == 1
        repeated = await PrimaryShortIndexingService(authority, manager=manager, principal=local_memory_principal()).reconcile()
        assert tuple(g.references for g in repeated.groups) == references
        assert len((await recall(manager, "quartznebula")).hits) == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [0, 1])
async def test_source_suppression_and_reopen_deny(tmp_path, source):
    import time

    from simple_harness_memory import HistoryShortHorizonBinding
    _, _, authority, manager = await real_turns(tmp_path, 11)
    result = await PrimaryShortIndexingService(authority, manager=manager, principal=local_memory_principal()).reconcile()
    hits = await recall(manager, "quartznebula")
    hit = hits.hits[0]
    binding = HistoryShortHorizonBinding(hits.audit_id, hit.chunk_ref, hit.content_hash)
    try:
        await manager.suppress(request=SuppressionRequest(f"forget-{source}", authority.subject, SuppressionScopeKind.EVIDENCE,
            result.groups[0].registrations[source].envelope.evidence_id, "user_forget", time.time()),
            principal=local_memory_principal())
        assert not (await recall(manager, "quartznebula")).hits
        snapshot = await manager.check_history_visibility(principal=local_memory_principal(),
            disclosure_context=history_disclosure(), bindings=(binding,))
        assert not snapshot.items[0].visible
    finally:
        await manager.close()
    manager = await memory(tmp_path / "index.db", authority)
    try:
        assert not (await recall(manager, "quartznebula")).hits
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_incomplete_turn_not_a_causal_group(tmp_path):
    state = tmp_path / "state.db"
    service = HumanMemoryHostServiceFactory(state,
        await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    primary = (await service.open_primary())["primary_ref"]
    await service.enqueue_turn(QueueTurnRequest(None, "uncompleted", "Actual pending USER"))
    authority = PrimaryConversationAuthority(state, subject=local_owner_auth().subject, primary_ref=primary)
    assert await authority.completed_run_ids() == ()
    manager = await memory(tmp_path / "index.db", authority)
    try:
        result = await PrimaryShortIndexingService(authority, manager=manager, principal=local_memory_principal()).reconcile()
        assert result.groups == ()
        assert result.projection.projected_chunk_count == 0
        assert not (await recall(manager, "pending")).hits
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_old_terminal_is_not_backfilled(tmp_path, monkeypatch):
    from deskpet.execution import primary_history
    from deskpet.memory import primary_message_evidence as producer
    original_pair = primary_history.evidence_pair
    def legacy_pair(subject, sdk_run_id, payload, occurred_at):
        legacy = dict(payload)
        legacy.pop("message_source_contract", None)
        return original_pair(subject, sdk_run_id, legacy, occurred_at)
    monkeypatch.setattr(primary_history, "evidence_pair", legacy_pair)
    async def old_producer(*args, **kwargs):
        return producer.PrimaryMessageProduction((), "legacy_no_message_producer")
    monkeypatch.setattr(producer, "append_new_primary_message_evidence_tx", old_producer)
    _, _, authority, manager = await real_turns(tmp_path, 1)
    try:
        service = PrimaryShortIndexingService(authority, manager=manager, principal=local_memory_principal())
        result = await service.reconcile()
        assert result.groups == ()
        assert result.blocked[0][1] == "conversation_message_source_missing"
        assert not (await recall(manager, "quartznebula")).hits
        replay = await service.reconcile()
        assert replay.blocked == result.blocked
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_new_message_source_atomic_rollback_and_runtime_reopen(tmp_path, monkeypatch):
    from deskpet.memory.human_memory_program import HumanMemoryProgramStore
    original_append = HumanMemoryProgramStore.append_evidence_tx
    crashed = False
    async def append_then_stop(self, db, envelope, receipt, **kwargs):
        nonlocal crashed
        actual = await original_append(self, db, envelope, receipt, **kwargs)
        if envelope.source_kind.value == "assistant_message" and not crashed:
            crashed = True
            raise RuntimeError("producer-stop-after-child-write")
        return actual
    monkeypatch.setattr(HumanMemoryProgramStore, "append_evidence_tx", append_then_stop)
    state = tmp_path / "state.db"
    service = HumanMemoryHostServiceFactory(state,
        await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    primary = (await service.open_primary())["primary_ref"]
    provider = Provider()
    runtime, stack, queue = await build(tmp_path, state, provider)
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "crash-producer", "Atomic real USER"))
        with pytest.raises(RuntimeError, match="producer-stop-after-child-write"):
            await runtime._drive_once()
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT count(*) FROM human_memory_evidence WHERE source_ref LIKE 'primary-runtime:%' OR source_ref LIKE 'primary-message:%'").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM foreground_terminal_receipts").fetchone()[0] == 0
        assert len(provider.requests) == 1
    finally:
        await runtime.close()
        await stack.close()
    runtime, stack, queue = await build(tmp_path, state, provider)
    try:
        await runtime._drive_once()
        assert len(provider.requests) == 1
        assert await queue.current_snapshot(local_owner_auth().subject) is None
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT count(*) FROM human_memory_evidence WHERE source_ref LIKE 'primary-runtime:%' OR source_ref LIKE 'primary-message:%'").fetchone()[0] == 2
    finally:
        await runtime.close()
        await stack.close()
    authority = PrimaryConversationAuthority(state, subject=local_owner_auth().subject, primary_ref=primary)
    manager = await memory(tmp_path / "index.db", authority)
    try:
        worker = MemoryIngestionOutboxWorker(state, lambda: manager, owner_id="after-restart")
        assert await worker.run_once() == "delivered"
        result = await PrimaryShortIndexingService(authority, manager=manager, principal=local_memory_principal()).reconcile()
        assert len(result.groups) == 1 and result.blocked == ()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_all_roots_guard_blocks_on_generated_source_ancestor_forget(tmp_path):
    import time

    import aiosqlite
    from deskpet.memory.primary_visibility import PrimaryHistoryPolicy
    state, _, authority, manager = await real_turns(tmp_path, 11)
    try:
        result = await PrimaryShortIndexingService(authority, manager=manager, principal=local_memory_principal()).reconcile()
        async def checker(*, subject, disclosure_context, bindings):
            assert subject == authority.subject
            return await manager.check_history_visibility(principal=local_memory_principal(),
                disclosure_context=disclosure_context, bindings=bindings)
        policy = PrimaryHistoryPolicy(state, authority.subject, checker)
        async def allowed():
            async with aiosqlite.connect(state) as db:
                db.row_factory = aiosqlite.Row
                return await policy.check_dependencies(db=db, primary_ref=authority.primary_ref,
                    dependencies=result.visibility_dependencies, disclosure_context=history_disclosure())
        assert await allowed()
        # Suppress a recent group's terminal ancestor, not the selected old
        # short chunk. Conservative all-indexed roots must still reject.
        last_child = result.groups[-1].registrations[1].envelope
        terminal_ref = last_child.evidence_refs[1]
        await manager.suppress(request=SuppressionRequest("terminal-ancestor-forget", authority.subject,
            SuppressionScopeKind.EVIDENCE, terminal_ref.evidence_id, "user_forget", time.time()),
            principal=local_memory_principal())
        assert not await allowed()
        assert len((await recall(manager, "quartznebula")).hits) == 1
        # This proves the bounded guard's necessary over-rejection, NOT complete
        # product short-lane usability or SDK traversal of Host dependency proof.
        from simple_harness_memory import HistoryEvidenceBinding
        user = result.groups[-1].registrations[0]
        snapshot = await manager.check_history_visibility(principal=local_memory_principal(),
            disclosure_context=history_disclosure(), bindings=(HistoryEvidenceBinding(user.envelope, user.admission_receipt),))
        assert snapshot.items[0].visible
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_tool_group_blocked_whole_while_normal_groups_index(tmp_path):
    import json

    from simple_harness import CallId
    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.providers import (
        ProviderResponse,
        ProviderToolCall,
        ProviderUsage,
    )
    class ToolFirstProvider(Provider):
        async def invoke(self, request, *, cancel):
            if not self.requests:
                self.requests.append(request)
                return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Searching actual tools"),
                    tool_calls=(ProviderToolCall(CallId("actual-search"), "tool_search", {}),),
                    model="model", usage=ProviderUsage(10, 10, 20), opaque_continuation_ref="fixture-opaque")
            return await super().invoke(request, cancel=cancel)
    state = tmp_path / "state.db"
    service = HumanMemoryHostServiceFactory(state,
        await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    primary = (await service.open_primary())["primary_ref"]
    runtime, stack, _ = await build(tmp_path, state, ToolFirstProvider())
    try:
        for index in range(12):
            await service.enqueue_turn(QueueTurnRequest(None, f"mixed-{index}", f"mixedmarker message {index}"))
            assert await runtime._drive_once()
        with sqlite3.connect(state) as db:
            transcripts = db.execute("SELECT payload_json FROM human_memory_evidence WHERE source_ref LIKE 'primary-runtime:%' ORDER BY occurred_at").fetchall()
            assert [m["role"] for m in json.loads(transcripts[0][0])["messages"]] == ["user", "assistant", "tool", "assistant"]
            assert db.execute("SELECT count(*) FROM human_memory_evidence WHERE source_kind='assistant_message'").fetchone()[0] == 11
    finally:
        await runtime.close()
        await stack.close()
    authority = PrimaryConversationAuthority(state, subject=local_owner_auth().subject, primary_ref=primary)
    manager = await memory(tmp_path / "index.db", authority)
    try:
        worker = MemoryIngestionOutboxWorker(state, lambda: manager, owner_id="mixed")
        for _ in range(12):
            assert await worker.run_once() == "delivered"
        result = await PrimaryShortIndexingService(authority, manager=manager, principal=local_memory_principal()).reconcile()
        assert len(result.groups) == 11
        assert len(result.blocked) == 1
        assert result.blocked[0][1] == "terminal_multiple_items_not_representable"
        hits = await recall(manager, "mixedmarker")
        assert len(hits.hits) == 1
        assert hits.hits[0].content.startswith("user: mixedmarker message 1\nassistant:")
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_marked_missing_child_replay_rejects_without_repair(tmp_path):
    import json

    import aiosqlite
    from deskpet.memory.primary_message_evidence import (
        verify_new_primary_message_evidence_tx,
    )
    from deskpet.memory.primary_visibility import read_evidence_pair
    state, _, authority, manager = await real_turns(tmp_path, 1)
    await manager.close()
    with sqlite3.connect(state) as db:
        terminal_id, payload = db.execute("SELECT evidence_id,payload_json FROM human_memory_evidence WHERE source_ref LIKE 'primary-runtime:%'").fetchone()
        assert json.loads(payload)["message_source_contract"] == "primary-message-v1"
        # Corrupt only this disposable Host database to model a lost child. The
        # production reader must detect the loss; never reconstruct an admission.
        triggers = db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('human_memory_evidence','human_memory_sanitization_receipts')").fetchall()
        for name, in triggers:
            db.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        child_id, = db.execute("SELECT evidence_id FROM human_memory_evidence WHERE source_kind='assistant_message'").fetchone()
        db.execute("DELETE FROM human_memory_evidence WHERE evidence_id=?", (child_id,))
        db.execute("DELETE FROM human_memory_sanitization_receipts WHERE evidence_id=?", (child_id,))
    async with aiosqlite.connect(state) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN")
        terminal, receipt = await read_evidence_pair(db=db, subject=authority.subject,
            primary_ref=authority.primary_ref, evidence_id=terminal_id)
        with pytest.raises(RuntimeError, match="primary_message_source_missing"):
            await verify_new_primary_message_evidence_tx(db, host_run_id=json.loads(payload)["host_run_id"],
                terminal_envelope=terminal, terminal_receipt=receipt)
        assert db.total_changes == 0
    run_id, = await authority.completed_run_ids()
    with pytest.raises(RuntimeError, match="primary_message_source_missing"):
        await authority.registrations_for_run(run_id)


@pytest.mark.asyncio
async def test_recreated_memory_preserves_original_user_analysis_lineage(tmp_path):
    _, _, authority, manager = await real_turns(tmp_path, 1)
    await manager.close()
    manager = await memory(tmp_path / "fresh-index.db", authority)
    try:
        result = await PrimaryShortIndexingService(authority, manager=manager, principal=local_memory_principal()).reconcile()
        assert len(result.groups) == 1
        group = result.groups[0]
        user = group.registrations[0]
        replay = await manager.ingest_committed_evidence(user.envelope, user.admission_receipt,
            analysis_lineage=group.user_analysis_lineage)
        assert replay.evidence_id == user.envelope.evidence_id
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_missing_source_only_port_rejects_before_any_user_or_group_work():
    from types import SimpleNamespace

    from deskpet.memory.conversation_registration import (
        ConversationRegistrationUnavailable,
    )
    calls = []
    class Authority:
        subject = local_memory_principal().actor_id
        async def completed_run_ids(self):
            calls.append('scan')
            raise AssertionError('capability must be checked before group work')
    async def full_ingest(*args, **kwargs):
        calls.append('full_ingest')
        raise AssertionError('no full-ingest fallback')
    service = PrimaryShortIndexingService(Authority(),
        manager=SimpleNamespace(ingest_committed_evidence=full_ingest), principal=local_memory_principal())
    with pytest.raises(ConversationRegistrationUnavailable, match='short_source_admission_unavailable'):
        await service.reconcile()
    assert calls == []


@pytest.mark.asyncio
async def test_real_eleven_user_jobs_and_source_only_assistants_no_retry_or_deadletter(tmp_path):
    import json
    import time

    from deskpet.memory.analysis_executor import HostMemoryAnalysisExecutor
    from deskpet.memory.analysis_proposal import PROPOSAL_TOOL_NAME
    from deskpet.memory.evidence_authority import HostEvidenceAuthority
    from deskpet.memory.memory_ingestion_outbox import build_worker_config
    from simple_harness import CallId
    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.providers import ProviderResponse, ProviderToolCall
    from simple_harness_memory import DurableMemoryJobRunner

    state, _, authority, manager = await real_turns(tmp_path, 11)
    result = await PrimaryShortIndexingService(authority, manager=manager,
        principal=local_memory_principal()).reconcile()
    users = {group.registrations[0].envelope.evidence_id for group in result.groups}
    assistants = {group.registrations[1].envelope.evidence_id for group in result.groups}
    assert len(users) == len(assistants) == 11 and not users & assistants
    pending = await manager.read_outbox(principal=local_memory_principal())
    assert len(pending.entries) == 11
    assert {entry.idempotency_key for entry in pending.entries} == users
    assert {entry.topic for entry in pending.entries} == {'memory.mutation.requested'}
    await manager.close()

    class AnalysisProvider:
        target = Provider.target  # exact target from the actual foreground fixture Run
        def __init__(self):
            self.requests = []
        async def invoke(self, request, *, cancel):
            self.requests.append(request)
            return ProviderResponse(request_id=request.request_id,
                message=Message(MessageRole.ASSISTANT, ''), model='model', finish_reason='tool_calls',
                tool_calls=(ProviderToolCall(CallId(f'analysis-{len(self.requests)}'), PROPOSAL_TOOL_NAME,
                    {'outcome': 'no_mutation', 'operations': []}),))

    provider = AnalysisProvider()
    executor = HostMemoryAnalysisExecutor(state, adapter_factory=lambda record: provider)
    kwargs = {"supported_filter_policies": HOST_SUPPORTED_FILTER_POLICIES,
        "conversation_evidence_authority": authority, "classification_policy": host_classification_policy(),
        "evidence_authority": HostEvidenceAuthority(state), "analysis_delivery_authority": executor}
    manager = await MemoryManager.build_human_memory_v7(tmp_path / 'index.db', **kwargs)
    config = build_worker_config(provider_id='fixture', model_id='model',
        model_config_hash=result.groups[0].user_analysis_lineage.model_config_hash)
    try:
        runner = DurableMemoryJobRunner(manager.backend, executor, executor, config,
            'actual-user-analysis', time.time)
        outcomes = [str(await runner.run_once()) for _ in range(11)]
        assert outcomes == ['applied'] * 11
        assert str(await runner.run_once()) == 'idle'
        assert executor.calls == len(provider.requests) == 11
        # These are Host-owned immutable attempt/member facts, not SDK SQL.
        with sqlite3.connect(state) as db:
            attempts = db.execute("SELECT status FROM post_turn_invocation_attempts WHERE purpose='analysis'").fetchall()
            assert len(attempts) == 11
            assert {row[0] for row in attempts} == {'succeeded'}
            members = db.execute("SELECT DISTINCT evidence_id FROM post_turn_invocation_members").fetchall()
            assert {row[0] for row in members} == users
            outbox = db.execute("SELECT state,attempts,last_error FROM memory_ingestion_outbox").fetchall()
            assert len(outbox) == 11 and all(row[0] == 'delivered' and row[2] is None for row in outbox)
        all_entries = await manager.read_outbox(principal=local_memory_principal(),
            states=('pending','claimed','applied','dead_letter'))
        analysis_entries = [e for e in all_entries.entries if e.topic == 'memory.mutation.requested']
        assert len(analysis_entries) == 11
        assert {e.idempotency_key for e in analysis_entries} == users
        assert not any(e.state == 'dead_letter' for e in analysis_entries)
        assert len((await recall(manager, 'quartznebula')).hits) == 1
        # Persist compact review facts alongside pytest's ignored database evidence.
        (tmp_path / 'job-combination.json').write_text(json.dumps({
            "user_evidence": sorted(users), "assistant_evidence": sorted(assistants),
            "outcomes": outcomes, "provider_calls": len(provider.requests), "assistant_analysis_calls": len({row[0] for row in members} & assistants),
        }, indent=2))
    finally:
        await manager.close()
    manager = await MemoryManager.build_human_memory_v7(tmp_path / 'index.db', **kwargs)
    try:
        assert str(await DurableMemoryJobRunner(manager.backend, executor, executor, config,
            'reopened-user-analysis', time.time).run_once()) == 'idle'
        assert executor.calls == len(provider.requests) == 11
        assert len((await recall(manager, 'quartznebula')).hits) == 1
    finally:
        await manager.close()

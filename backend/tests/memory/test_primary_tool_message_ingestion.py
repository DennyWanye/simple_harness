"""Real dynamic Host tool groups, atomic message sources and installed short index."""
import json
import sqlite3

import pytest
from simple_harness import CallId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage
from simple_harness_memory import SuppressionRequest, SuppressionScopeKind
from deskpet.memory.conversation_registration import PrimaryConversationAuthority
from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory, QueueTurnRequest
from deskpet.memory.human_memory_v7 import local_memory_principal
from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.memory.short_indexing import PrimaryShortIndexingService
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.execution.test_primary_foreground_runtime import Provider, build
from tests.memory.test_primary_short_ingestion import memory, recall


class TwoToolTurns(Provider):
    empty_assistant = False
    async def invoke(self, request, *, cancel):
        if len(self.requests) < 2:
            self.requests.append(request)
            return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "" if self.empty_assistant else "Search capabilities"),
                tool_calls=(ProviderToolCall(CallId("repeat-raw"), "tool_search", {"query": "capabilities"}),),
                model="model", usage=ProviderUsage(10, 10, 20), opaque_continuation_ref="fixture-tool")
        return await super().invoke(request, cancel=cancel)


async def host(tmp_path, *, empty_assistant=False):
    state = tmp_path / "state.db"
    service = HumanMemoryHostServiceFactory(state,
        await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    primary = (await service.open_primary())["primary_ref"]
    provider = TwoToolTurns()
    provider.empty_assistant = empty_assistant
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True)
    return state, service, primary, runtime, stack, queue


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_assistant", [False, True])
async def test_complete_six_item_tool_group_is_indexed_and_forgotten(tmp_path, empty_assistant):
    import time
    state, service, primary, runtime, stack, _ = await host(tmp_path, empty_assistant=empty_assistant)
    manager = None
    try:
        for index in range(11):
            text = "quartznebula capability preference" if index == 0 else f"recent ordinary turn {index}"
            await service.enqueue_turn(QueueTurnRequest(None, f"tool-source-{index}", text))
            assert await runtime._drive_once() and runtime.last_error is None
        authority = PrimaryConversationAuthority(state, subject=local_owner_auth().subject, primary_ref=primary)
        manager = await memory(tmp_path / "index.db", authority)
        outbox = MemoryIngestionOutboxWorker(state, lambda: manager, owner_id="tool-source-test")
        for _ in range(11):
            assert await outbox.run_once() == "delivered"
        service_index = PrimaryShortIndexingService(authority, manager=manager, principal=local_memory_principal())
        indexed = await service_index.reconcile()
        assert indexed.blocked == () and len(indexed.groups) == 11
        group = indexed.groups[0]
        assert [r.metadata.role.value for r in group.registrations] == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
        assert {r.metadata.group_item_count for r in group.registrations} == {6}
        links = [r.metadata.tool_causal_link for r in group.registrations if r.metadata.role.value == "tool"]
        assert [link.parent_item_ordinal for link in links] == [2, 4]
        assert len({link.tool_call_id for link in links}) == 2
        assert all(link.terminal_receipt_id.startswith("host-tool-terminal:") for link in links)
        hit = await recall(manager, "quartznebula")
        assert len(hit.hits) == 1
        assert hit.hits[0].content.count("\ntool:") == 2
        if not empty_assistant:
            assert "Search capabilities" in hit.hits[0].content
        else:
            assert group.registrations[1].envelope.sanitized_payload["source"]["message"]["content"] == ""
        await manager.close()
        manager = await memory(tmp_path / "index.db", authority)
        replay = await PrimaryShortIndexingService(authority, manager=manager, principal=local_memory_principal()).reconcile()
        assert replay.groups[0].references == group.references
        tool = group.registrations[2]
        await manager.suppress(request=SuppressionRequest("forget-tool-source", authority.subject,
            SuppressionScopeKind.EVIDENCE, tool.envelope.evidence_id, "user_forget", time.time()),
            principal=local_memory_principal())
        assert not (await recall(manager, "quartznebula")).hits
    finally:
        if manager is not None:
            await manager.close()
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
async def test_tool_child_failure_rolls_back_terminal_and_all_children(tmp_path, monkeypatch):
    from deskpet.memory.human_memory_program import HumanMemoryProgramStore
    state, service, primary, runtime, stack, _ = await host(tmp_path)
    original = HumanMemoryProgramStore.append_evidence_tx
    async def fail_child(self, db, envelope, receipt, **kwargs):
        if envelope.source_ref.startswith("primary-message-v2:") and envelope.source_kind.value == "tool_result":
            raise RuntimeError("fixture-child-write-interrupted")
        return await original(self, db, envelope, receipt, **kwargs)
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "atomic-tool", "Actual tool turn"))
        with monkeypatch.context() as patch:
            patch.setattr(HumanMemoryProgramStore, "append_evidence_tx", fail_child)
            with pytest.raises(RuntimeError, match="^fixture-child-write-interrupted$"):
                await runtime._drive_once()
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT count(*) FROM human_memory_evidence WHERE source_ref LIKE 'primary-runtime:%' OR source_ref LIKE 'primary-message-v2:%'").fetchone()[0] == 0
        assert await runtime._drive_once() and runtime.last_error is None
        with sqlite3.connect(state) as db:
            terminal = db.execute("SELECT payload_json FROM human_memory_evidence WHERE source_ref LIKE 'primary-runtime:%'").fetchone()[0]
            assert json.loads(terminal)["message_source_contract"] == "primary-message-v2"
            assert db.execute("SELECT count(*) FROM human_memory_evidence WHERE source_ref LIKE 'primary-message-v2:%'").fetchone()[0] == 5
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "resigned_attestation"])
async def test_reopen_refuses_missing_or_resigned_tool_child_without_repair(tmp_path, fault):
    from dataclasses import replace
    import aiosqlite
    from deskpet.memory.primary_visibility import read_evidence_pair
    from deskpet.memory.primary_message_v2 import verify
    from deskpet.task_scope.protocol import canonical_hash, canonical_json
    state, service, primary, runtime, stack, _ = await host(tmp_path)
    subject = local_owner_auth().subject
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "source-reopen", "Actual tool turn"))
        assert await runtime._drive_once() and runtime.last_error is None
        with sqlite3.connect(state) as db:
            host_run_id, = db.execute("SELECT host_run_id FROM foreground_runs").fetchone()
            user_id, = db.execute("SELECT evidence_id FROM foreground_turns").fetchone()
            terminal_id, = db.execute("SELECT evidence_id FROM human_memory_evidence WHERE source_ref LIKE 'primary-runtime:%'").fetchone()
        async with aiosqlite.connect(state) as db:
            db.row_factory = aiosqlite.Row
            terminal, terminal_receipt = await read_evidence_pair(db=db, subject=subject, primary_ref=primary, evidence_id=terminal_id)
            user, _ = await read_evidence_pair(db=db, subject=subject, primary_ref=primary, evidence_id=user_id)
            original = await verify(db, primary_ref=primary, host_run_id=host_run_id,
                                    terminal=terminal, terminal_receipt=terminal_receipt, user=user)
        child, receipt = original[1]
        await runtime.close()
        await stack.close()
        with sqlite3.connect(state) as db:
            for name, in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('human_memory_evidence','human_memory_sanitization_receipts')").fetchall():
                db.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
            if fault == "missing":
                db.execute("DELETE FROM human_memory_evidence WHERE evidence_id=?", (child.evidence_id,))
                db.execute("DELETE FROM human_memory_sanitization_receipts WHERE evidence_id=?", (child.evidence_id,))
            else:
                # Recompute every outer S1 checksum. Only comparison against the
                # original terminal's actual tool fact can reject this forgery.
                body = child.to_json()["sanitized_payload"]
                attestation = body["source"]["tool_terminal_attestation"]
                attestation["payload"]["source"]["result_hash"] = "f" * 64
                attestation["receipt_hash"] = canonical_hash({"domain": "host-tool-terminal/v1", "payload": attestation["payload"]})
                attestation["receipt_id"] = "host-tool-terminal:" + attestation["receipt_hash"]
                changed = replace(child, sanitized_payload=body,
                    source_hash=canonical_hash(body["source"]), sanitized_hash=canonical_hash(body))
                changed_receipt = replace(receipt, envelope_hash=changed.envelope_hash,
                    source_hash=changed.source_hash, sanitized_hash=changed.sanitized_hash)
                db.execute("UPDATE human_memory_evidence SET envelope_json=?,payload_json=?,envelope_sha256=?,source_sha256=?,sanitized_sha256=? WHERE evidence_id=?",
                    (canonical_json(changed.to_json()), canonical_json(body), changed.envelope_hash, changed.source_hash, changed.sanitized_hash, child.evidence_id))
                db.execute("UPDATE human_memory_sanitization_receipts SET receipt_json=?,receipt_sha256=?,envelope_sha256=?,source_sha256=?,sanitized_sha256=? WHERE evidence_id=?",
                    (canonical_json(changed_receipt.to_json()), changed_receipt.receipt_hash, changed.envelope_hash, changed.source_hash, changed.sanitized_hash, child.evidence_id))
        async with aiosqlite.connect(state) as db:
            db.row_factory = aiosqlite.Row
            if fault != "missing":
                assert await read_evidence_pair(db=db, subject=subject, primary_ref=primary,
                    evidence_id=child.evidence_id) == (changed, changed_receipt)
            code = "missing" if fault == "missing" else "corrupt"
            with pytest.raises(RuntimeError, match="^primary_message_v2_source_" + code + "$"):
                await verify(db, primary_ref=primary, host_run_id=host_run_id,
                             terminal=terminal, terminal_receipt=terminal_receipt, user=user)
            assert db.total_changes == 0
    finally:
        await runtime.close()
        await stack.close()

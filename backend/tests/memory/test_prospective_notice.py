"""New Host notice on a real ACK whose actual model answer contains no reminder.

Public Memory mutation/timer/source/visibility + real Harness/Host receipts;
deterministic HTTP. No native claim, private Memory SQL or old userdata.
"""
import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from tests.memory.test_prospective_mandatory_repair import (
    fixture, seed, HumanMemoryV7Runtime, HOST_SUPPORTED_FILTER_POLICIES,
    HostHistorySourceAuthority, S5cStore, ProspectiveSignalStore,
    ProspectiveScheduler, PublicTimeAuthoritySource, MemoryAttemptJournal,
    ProspectiveOccurrenceCoordinator, PublicOccurrenceCurrentReader,
    ProspectiveSourceDependencies, prospective_ack_registration, ProspectiveRequestGuard,
    ProductProviderAdapter, Registry, build, QueueTurnRequest, check_runtime_dependencies,
)
from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory
from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_route import local_owner_auth


@asynccontextmanager
async def world(tmp_path, monkeypatch, *, failed=False, pending_probe=False, legacy=False):
    state, _, service, configured, binding_authority = await fixture(tmp_path)
    primary = (await service.open_primary())["primary_ref"]
    identity = HumanMemoryV7Runtime(tmp_path / "identity.db")
    principal = identity.principal()
    monkeypatch.setattr(seed, "P", principal)
    w = seed.World(tmp_path)
    w.filter_policies = HOST_SUPPORTED_FILTER_POLICIES
    w.history_options = dict(history_source_authority=HostHistorySourceAuthority(state))
    await w.setup()
    await w.mutate("notice-source")
    await w.consumer().run_once()
    w.clock[0] = 30.
    store = S5cStore(state, principal)
    signals = ProspectiveSignalStore(state, principal)
    assert await ProspectiveScheduler(store=signals,
        source=PublicTimeAuthoritySource(registrations=store, signals=signals),
        memory=w, clock=lambda: w.clock[0]).tick(claim_owner="notice-timer") == 1
    async def manager(): return w.manager
    async def close(): pass
    memory_runtime = SimpleNamespace(principal=lambda: principal, manager=manager, close=close,
        semantic_clock=lambda: w.clock[0], _clock=lambda: w.clock[0],
        operation_audit=MemoryAttemptJournal(tmp_path / "notice-audit.db"))
    coordinator = ProspectiveOccurrenceCoordinator(store=store, clock=lambda: w.clock[0],
        read_current=PublicOccurrenceCurrentReader(store=store, runtime_getter=lambda: memory_runtime),
        source_dependencies=ProspectiveSourceDependencies(store=store, runtime_getter=lambda: memory_runtime))
    if legacy:
        # Produce the exact old ACK format at the real append boundary. No
        # persisted ACK/hash is changed and replay cannot add the new marker.
        import deskpet.memory.prospective_occurrence as occurrence
        append = occurrence._append
        async def old_append(db, **kwargs):
            if kwargs["phase"] == "acknowledged":
                kwargs["proof"] = {k: v for k, v in kwargs["proof"].items() if k != "notice_contract"}
            return await append(db, **kwargs)
        monkeypatch.setattr(occurrence, "_append", old_append)
    sends, early = [], []
    async def physical(request):
        body = json.loads(request.content); sends.append(body)
        message = {"role": "assistant", "content": "47"}
        if len(sends) == 1:
            groups = [json.loads(m["content"]) for m in body["messages"] if m["role"] == "system"
                and isinstance(m.get("content"), str) and '"kind":"pending_prospective_occurrences"' in m["content"]]
            assert len(groups) == 1 and groups[0]["count"] == 1
            message["tool_calls"] = [{"id": "real-ack", "type": "function", "function": {
                "name": "prospective_ack", "arguments": json.dumps({"occurrence_key": groups[0]["entries"][0]["occurrence_key"]})}}]
        else:
            if pending_probe:
                # Actual ACK already committed; successful/failed SDK terminal
                # does not exist yet. Public Host page must not lose the notice.
                early.append(await read())
            if failed:
                return httpx.Response(400, json={"error": {"message": "fixture provider rejected"}})
        return httpx.Response(200, json={"id": f"notice-{len(sends)}", "model": "model-a",
            "choices": [{"message": message, "finish_reason": "tool_calls" if "tool_calls" in message else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(physical))
    runtime = stack = queue = None
    async def guard(request):
        current = await queue.current_snapshot(principal.actor_id)
        await check_runtime_dependencies(db_path=state, stack=stack, sdk_run_id=current.sdk_run_id,
            request=request, policy_factory=lambda _: runtime.history_policy,
            typed_use_authority=runtime.typed_use_authority)
        await ProspectiveRequestGuard(sdk_run_id=current.sdk_run_id, coordinator=coordinator,
            read_provider_context_use=stack.read_provider_context_use)(request)
    provider = ProductProviderAdapter(Registry("fixture-secret"), provider_id="relay", client=client,
        price_resolver=lambda *_: (1, 1, "fixture-prices"), pre_invoke_guard=guard)
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    async def policy(candidate, purpose):
        return await w.manager.backend.resolve_suppression(candidate, purpose, principal=principal)
    async def checker(*, subject, disclosure_context, bindings):
        assert subject == principal.actor_id
        return await w.manager.check_history_visibility(principal=principal,
            disclosure_context=disclosure_context, bindings=bindings)
    async def read(operation="primary.messages.page", request=None, *, terminal_reader=None):
        factory = HumanMemoryHostServiceFactory(state, startup,
            settled_run_reader=terminal_reader or stack.read_settled_primary_run,
            suppression_resolver=policy, history_visibility_checker=checker,
            run_binding_reader=lambda run_id: stack.read_closure_run_facts(run_id).binding_record,
            cognitive_runtime_getter=lambda: memory_runtime)
        return await handle_human_memory_command({"type": "human_memory_request", "request_id": "notice-read",
            "operation": operation, "request": request or {"primary_ref": primary, "limit": 50}},
            factory=factory, auth=local_owner_auth())
    try:
        runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
            binding_authority=binding_authority, configured_root=configured,
            visibility_memory=memory_runtime, context_use_memory=memory_runtime,
            occurrence_coordinator=coordinator,
            extra_registrations=(prospective_ack_registration(coordinator=coordinator),))
        await service.enqueue_turn(QueueTurnRequest(None, "notice-math", "29+18"))
        await runtime.after_enqueue(subject=principal.actor_id)
        await asyncio.wait_for(runtime.drain(), 30)
        assert len(sends) == 2
        assert await queue.current_snapshot(principal.actor_id) is None
        yield SimpleNamespace(**locals())
    finally:
        if runtime is not None: await runtime.close()
        if stack is not None: await stack.close()
        await w.manager.close(); await identity.close(); await client.aclose()


def notices(response):
    assert response["payload"]["ok"], response
    return [m for m in response["payload"]["result"]["items"] if m["role"] == "reminder"]


@pytest.mark.asyncio
async def test_actual_ack_answer_only_has_stable_notice_and_public_detail(tmp_path, monkeypatch):
    async with world(tmp_path, monkeypatch, pending_probe=True) as env:
        page = await env.read()
        item, = notices(page)
        assert item["text"] == "send the report" and len(item["notice_id"]) == 64
        assert notices(env.early[0]) == [item]
        assert any(m["role"] == "assistant" and m["text"] == "47" for m in page["payload"]["result"]["items"])
        assert all(m["role"] != "assistant" or "send the report" not in m["text"] for m in page["payload"]["result"]["items"])
        detail = await env.read("primary.messages.detail", {"primary_ref": env.primary, "message_ref": item["message_ref"]})
        assert detail["payload"]["ok"] and detail["payload"]["result"]["text"] == item["text"]
        assert notices(await env.read()) == [item]
        await env.w.manager.close(); await env.w.open()
        assert notices(await env.read()) == [item]  # fresh service/Memory handle, same durable ACK
        assert len(env.sends) == 2


@pytest.mark.asyncio
async def test_actual_ack_survives_failed_answer_without_fake_terminal(tmp_path, monkeypatch):
    async with world(tmp_path, monkeypatch, failed=True, pending_probe=True) as env:
        item, = notices(await env.read())
        assert item == notices(env.early[0])[0]
        async with env.store._transaction() as db:
            state = await (await db.execute("SELECT terminal_state FROM foreground_terminal_receipts")).fetchone()
        assert state[0] == "FAILED"


@pytest.mark.asyncio
async def test_legacy_ack_does_not_get_a_notice_or_new_receipt(tmp_path, monkeypatch):
    async with world(tmp_path, monkeypatch, legacy=True) as env:
        assert notices(await env.read()) == []
        async with env.store._transaction() as db:
            row = await (await db.execute("SELECT * FROM prospective_occurrences WHERE phase='acknowledged'")).fetchone()
        receipt = await env.coordinator.ack(sdk_run_id=row["sdk_run_id"], occurrence_key=row["occurrence_key"])
        assert receipt["receipt_hash"] == row["record_hash"]
        assert notices(await env.read()) == [] and len(env.sends) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["memory", "evidence"])
async def test_late_forget_removes_notice_from_page_and_exact_detail(tmp_path, monkeypatch, scope):
    async with world(tmp_path, monkeypatch) as env:
        item, = notices(await env.read())
        entry = (await env.w.manager.read_occurrence_inbox(principal=env.principal)).entries[0]
        import simple_harness_memory as memory
        target = entry.memory_id
        if scope == "evidence":
            target, = await env.coordinator.source_dependencies.evidence_ids(env.w.manager, entry.memory_id, entry.prospective_revision)
        await env.w.manager.suppress(principal=env.principal, request=memory.SuppressionRequest(
            "notice-forget", env.principal.actor_id, memory.SuppressionScopeKind(scope), target, "user_forget", env.w.clock[0]))
        assert notices(await env.read()) == []
        detail = await env.read("primary.messages.detail", {"primary_ref": env.primary, "message_ref": item["message_ref"]})
        assert not detail["payload"]["ok"]
        assert detail["payload"]["error"]["code"] == "primary_message_unavailable"
        assert len(env.sends) == 2


@pytest.mark.asyncio
async def test_foreign_ack_and_wrong_public_terminal_never_project(tmp_path, monkeypatch):
    async with world(tmp_path, monkeypatch) as env:
        assert len(notices(await env.read())) == 1
        async def wrong_terminal(run, **kwargs):
            evidence, transcript = env.stack.read_settled_primary_run(run, **kwargs)
            return replace(evidence, event_hash="f" * 64), transcript
        bad = await env.read(terminal_reader=wrong_terminal)
        assert not bad["payload"]["ok"]
        async with env.store._transaction() as db:
            # Host corruption control only; original SDK identity is not changed.
            triggers = await (await db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='prospective_occurrences'")).fetchall()
            for trigger in triggers:
                await db.execute('DROP TRIGGER "' + trigger['name'].replace('"', '""') + '"')
            await db.execute("UPDATE prospective_occurrences SET owner_key=? WHERE phase='acknowledged'", ("f" * 64,))
            for trigger in triggers:
                await db.execute(trigger['sql'])
        bad = await env.read()
        assert not bad["payload"]["ok"] and len(env.sends) == 2


@pytest.mark.asyncio
async def test_legal_public_reschedule_retires_notice_without_breaking_history(tmp_path, monkeypatch):
    async with world(tmp_path, monkeypatch) as env:
        item, = notices(await env.read())
        original = (await env.w.manager.read_occurrence_inbox(principal=env.principal)).entries[0]
        async def commitments():
            async with env.store._transaction() as db:
                ack = await (await db.execute("SELECT record_id,record_hash,inbox_json FROM prospective_occurrences WHERE phase='acknowledged'")).fetchall()
                terminal = await (await db.execute("SELECT receipt_hash,receipt_json FROM foreground_terminal_receipts")).fetchall()
            return ([tuple(r) for r in ack], [tuple(r) for r in terminal])
        before = await commitments()
        assert await env.w.mutate("notice-rescheduled", (original.memory_id, original.prospective_revision)) == original.memory_id
        current = (await env.w.manager.read_occurrence_inbox(principal=env.principal)).entries[0]
        assert current.lifecycle_state == "rescheduled"
        assert current.content_hash != original.content_hash
        assert current.occurrence_key == original.occurrence_key and current.action_text == original.action_text
        page = await env.read()
        assert notices(page) == []
        assert any(m["role"] == "user" and m["text"] == "29+18" for m in page["payload"]["result"]["items"])
        detail = await env.read("primary.messages.detail", {"primary_ref": env.primary, "message_ref": item["message_ref"]})
        assert not detail["payload"]["ok"]
        assert detail["payload"]["error"]["code"] == "primary_message_unavailable"
        assert await commitments() == before and len(env.sends) == 2

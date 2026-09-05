"""Cross-component proof: real SDK terminal -> Host receipt -> public history.

The provider is deterministic; this is runtime integration, not live-provider or
desktop evidence. History policy uses the installed Memory public manager. Legacy terminal
observations without complete dependencies preserve USER only, without rewriting archives.
"""

import asyncio
import json
import sqlite3
from dataclasses import replace

import pytest

from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.human_memory_service import (
    CreateTaskScopeRequest,
    HumanMemoryHostServiceFactory,
    QueueTurnRequest,
)
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.execution.test_primary_foreground_runtime import Provider, build
from tests.sdk_adapters.s5b_effect_gate_harness import bind_scope_root


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_scoped", [False, True])
async def test_real_terminal_public_history_reopen_and_raw_event_binding(
    tmp_path, legacy_scoped
):
    path = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(path, approved_fresh_lane=True)
    auth = local_owner_auth()
    service = HumanMemoryHostServiceFactory(path, startup).bind(auth)
    primary = (await service.open_primary())["primary_ref"]
    scope = None
    if legacy_scoped:
        created = await service.create_task_scope(
            CreateTaskScopeRequest("legacy", "Legacy task", "Ordinary reply", "scope")
        )
        scope = created["scope_ref"]
        root = tmp_path / "actual-root"
        root.mkdir()
        await bind_scope_root(path, scope, root)

    user_text = "The runtime and history must agree. 中文🧭"
    queued = await service.enqueue_turn(QueueTurnRequest(scope, "durable-one", user_text))
    provider = Provider()
    runtime, stack, queue = await build(
        tmp_path, path, provider, legacy_observer=legacy_scoped
    )
    message_refs = None
    try:
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        assert await queue.current_snapshot(auth.subject) is None
        assert len(provider.requests) == 1

        async def policy(candidate, purpose):
            assert candidate.subject == auth.subject
            manager = await runtime.history_memory.manager()
            return await manager.backend.resolve_suppression(candidate, purpose)

        async def checker(*, subject, disclosure_context, bindings):
            assert subject == runtime.history_memory.principal().actor_id
            manager = await runtime.history_memory.manager()
            return await manager.check_history_visibility(
                principal=runtime.history_memory.principal(),
                disclosure_context=disclosure_context, bindings=bindings,
            )

        async def command(operation, request, reader=None):
            factory = HumanMemoryHostServiceFactory(
                path,
                startup,
                settled_run_reader=reader or stack.read_settled_primary_run,
                suppression_resolver=policy,
                history_visibility_checker=checker,
                run_binding_reader=lambda run_id: stack.read_closure_run_facts(run_id).binding_record,
            )
            return await handle_human_memory_command(
                {"type": "human_memory_request", "request_id": "read",
                 "operation": operation, "request": request},
                factory=factory, auth=auth,
            )

        for reopened in (False, True):
            if reopened:
                await runtime.close()
                await stack.close()
                runtime, stack, queue = await build(
                    tmp_path, path, provider, legacy_observer=legacy_scoped
                )
                assert not await runtime._drive_once()
                assert len(provider.requests) == 1
            page = await command("primary.messages.page", {"primary_ref": primary})
            assert page["payload"]["ok"], page
            items = page["payload"]["result"]["items"]
            expected_items = [("user", user_text)]
            if not legacy_scoped:
                expected_items.append(("assistant", "Actual response 1"))
            assert [(item["role"], item["text"]) for item in items] == expected_items
            assert {item["turn_ref"] for item in items} == {queued["turn_ref"]}
            refs = [item["message_ref"] for item in items]
            if message_refs is None:
                message_refs = refs
            assert refs == message_refs
            assert "PRIVATE_CANARY" not in json.dumps(page)
            assert "HIDDEN_CANARY" not in json.dumps(page)
            detail = await command("primary.messages.detail", {
                "primary_ref": primary, "message_ref": refs[-1], "offset": 0,
            })
            assert detail["payload"]["ok"], detail
            assert detail["payload"]["result"]["text"] == expected_items[-1][1]
            if legacy_scoped:
                # No manufactured terminal proof; the original USER is retained.
                continue
            for field, value in (("event_id", "wrong-raw-event"), ("event_hash", "0" * 64)):
                def wrong_reader(run_id, **kwargs):
                    terminal, transcript = stack.read_settled_primary_run(run_id, **kwargs)
                    return replace(terminal, **{field: value}), transcript
                bad = await command("primary.messages.page", {"primary_ref": primary}, wrong_reader)
                assert bad["payload"]["ok"] is False, bad
                assert "Actual response 1" not in json.dumps(bad)
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM foreground_turns").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM foreground_terminal_receipts").fetchone()[0] == 1
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
async def test_memory_only_forget_filters_real_history_and_next_outbound_after_reopen(tmp_path):
    """One real runtime/source chain reaches both public UI and physical transport."""
    import time

    import aiosqlite
    import httpx
    from simple_harness_memory import SuppressionRequest, SuppressionScopeKind

    from deskpet.execution.primary_dependencies import check_runtime_dependencies
    from deskpet.memory.primary_visibility import read_evidence_pair
    from deskpet.memory.evidence_authority import HostEvidenceAuthority
    from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
    from deskpet.sdk_adapters.provider import ProductProviderAdapter
    from tests.memory.test_primary_visibility import materialize
    from tests.sdk_adapters.test_product_host_ports import Registry

    path = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(path, approved_fresh_lane=True)
    auth = local_owner_auth()
    service = HumanMemoryHostServiceFactory(path, startup).bind(auth)
    primary = (await service.open_primary())["primary_ref"]
    sends = []

    def physical(request):
        sends.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": f"actual-{len(sends)}", "model": "model-a",
            "choices": [{"message": {"role": "assistant", "content": f"Derived answer {len(sends)}"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(physical))
    provider = ProductProviderAdapter(Registry("fixture-secret"), provider_id="relay", client=client,
                                     price_resolver=lambda *_: (1, 1, "price-v1"))
    visibility_runtime = HumanMemoryV7Runtime(tmp_path / "visibility-memory.db",
        evidence_authority=HostEvidenceAuthority(path))
    runtime, stack, queue = await build(tmp_path, path, provider, visibility_memory=visibility_runtime)

    async def guard(request):
        current = await queue.current_snapshot(auth.subject)
        await check_runtime_dependencies(
            db_path=path, stack=stack, sdk_run_id=current.sdk_run_id,
            request=request, policy_factory=lambda _: runtime.history_policy,
        )

    provider._pre_invoke_guard = guard

    async def policy(candidate, purpose):
        manager = await runtime.history_memory.manager()
        return await manager.backend.resolve_suppression(candidate, purpose)

    async def checker(*, subject, disclosure_context, bindings):
        assert subject == runtime.history_memory.principal().actor_id
        manager = await runtime.history_memory.manager()
        return await manager.check_history_visibility(
            principal=runtime.history_memory.principal(),
            disclosure_context=disclosure_context, bindings=bindings,
        )

    async def command(operation, request):
        return await handle_human_memory_command(
            {"type": "human_memory_request", "request_id": "actual-ui-read",
             "operation": operation, "request": request},
            factory=HumanMemoryHostServiceFactory(
                path, startup, settled_run_reader=stack.read_settled_primary_run,
                suppression_resolver=policy, history_visibility_checker=checker,
                run_binding_reader=lambda run_id: stack.read_closure_run_facts(run_id).binding_record,
            ), auth=auth,
        )

    async def page():
        response = await command("primary.messages.page", {"primary_ref": primary})
        assert response["payload"]["ok"], response
        return response["payload"]["result"]

    def archive():
        with sqlite3.connect(path) as db:
            return db.execute("SELECT envelope_json FROM human_memory_evidence ORDER BY evidence_id").fetchall()

    first = "Please keep replies concise. SOURCE_FORGET_CANARY"
    independent = "Second independent user message"
    try:
        queued = await service.enqueue_turn(QueueTurnRequest(None, "source-one", first))
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        await service.enqueue_turn(QueueTurnRequest(None, "inherits-one", independent))
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        assert len(sends) == 2 and first in json.dumps(sends[1]["messages"])
        before = await page()
        assert [i["text"] for i in before["items"]] == [first, "Derived answer 1", independent, "Derived answer 2"]
        refs = [before["items"][i]["message_ref"] for i in (0, 1, 3)]
        original = archive()
        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT evidence_id FROM foreground_turns WHERE turn_id=?", (queued["turn_ref"],))).fetchone()
            envelope, receipt = await read_evidence_pair(
                db=db, subject=auth.subject, primary_ref=primary, evidence_id=row[0],
            )
        manager = await runtime.history_memory.manager()
        principal = runtime.history_memory.principal()
        await manager.ingest_committed_evidence(envelope, receipt)
        memory_id = await materialize(manager, principal, envelope, receipt)
        await manager.suppress(principal=principal, request=SuppressionRequest(
            "forget-only-memory", auth.subject, SuppressionScopeKind.MEMORY,
            memory_id, "user_forget", time.time(),
        ))
        for reopened in (False, True):
            if reopened:
                await runtime.close()
                await stack.close()
                await visibility_runtime.close()
                visibility_runtime = HumanMemoryV7Runtime(tmp_path / "visibility-memory.db",
                    evidence_authority=HostEvidenceAuthority(path))
                runtime, stack, queue = await build(tmp_path, path, provider, visibility_memory=visibility_runtime)
                assert not await runtime._drive_once()
                assert len(sends) == 2
            after = await page()
            assert after["revision"] == before["revision"]
            assert [i["text"] for i in after["items"]] == [independent]
            for ref in refs:
                denied = await command("primary.messages.detail", {
                    "primary_ref": primary, "message_ref": ref, "offset": 0,
                })
                assert denied["payload"]["ok"] is False
            assert archive() == original
        await service.enqueue_turn(QueueTurnRequest(None, "after-forget", "Continue without forgotten content"))
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        assert len(sends) == 3
        final_messages = json.dumps(sends[-1]["messages"])
        assert independent in final_messages
        assert "Continue without forgotten content" in final_messages
        for hidden in (first, "Derived answer 1", "Derived answer 2"):
            assert hidden not in final_messages
    finally:
        await runtime.close()
        await stack.close()
        await visibility_runtime.close()
        await client.aclose()

"""Cross-component proof: real SDK terminal -> Host receipt -> public history.

The provider is deterministic; this is runtime integration, not live-provider or
desktop evidence. Policy is explicitly injected so the oracle isolates terminal
identity and restart behavior from the separately tested suppression backend.
"""

import asyncio
import json
import sqlite3
from dataclasses import replace

import pytest
from simple_harness_memory.core.suppression import SuppressionResolution

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
            return SuppressionResolution(False, (), 1.0)

        async def command(operation, request, reader=None):
            factory = HumanMemoryHostServiceFactory(
                path,
                startup,
                settled_run_reader=reader or stack.read_settled_primary_run,
                suppression_resolver=policy,
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
            assert [(item["role"], item["text"]) for item in items] == [
                ("user", user_text), ("assistant", "Actual response 1")
            ]
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
            assert detail["payload"]["result"]["text"] == "Actual response 1"
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

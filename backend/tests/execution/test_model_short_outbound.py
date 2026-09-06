"""Actual SDK tool/result and production preflight at a controlled HTTP transport."""
import asyncio
import json
import sqlite3

import httpx
import pytest

from deskpet.execution.primary_dependencies import check_runtime_dependencies
from deskpet.memory.human_memory_service import QueueTurnRequest
from deskpet.memory.human_memory_v7 import local_memory_principal
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.memory.short_indexing import PrimaryShortIndexingService
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.sdk_adapters.provider import ProductProviderAdapter
from tests.execution.test_primary_foreground_runtime import build
from tests.memory.test_primary_short_ingestion import real_turns
from tests.memory.test_selected_short_sources import suppress
from tests.sdk_adapters.test_product_host_ports import Registry


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "missing_sources", "late_forget"])
async def test_selected_typed_short_is_checked_before_next_physical_send(tmp_path, monkeypatch, fault):
    from deskpet.memory import human_memory_v7
    state, service, authority, manager = await real_turns(tmp_path, 11)
    try:
        indexed = await PrimaryShortIndexingService(
            authority, manager=manager, principal=local_memory_principal()).reconcile()
        assert len(indexed.groups) == 11 and not indexed.blocked
    finally:
        await manager.close()
    def no_provider(*args, **kwargs):
        raise AssertionError("No analysis provider is invoked")
    memory = compose_human_memory_runtime(state, tmp_path / "index.db", adapter_factory=no_provider)
    manager = await memory.manager()
    async def no_second_query(**kwargs):
        pytest.fail("Standalone short query must not run")
    monkeypatch.setattr(manager, "recall_short_horizon", no_second_query)
    projected = []
    project = human_memory_v7.project_recall_fragments
    def capture(lanes):
        rows = [dict(row) for row in project(lanes)]
        assert len(rows) == 1 and rows[0]["lane"] == "short_horizon_typed"
        projected.extend(rows)
        if fault == "missing_sources":
            rows[0].pop("history_source_dependencies")
        return tuple(rows)
    monkeypatch.setattr(human_memory_v7, "project_recall_fragments", capture)
    sends, guards = [], []
    def transport(request):
        sends.append(request)
        message = ({"role": "assistant", "content": None, "tool_calls": [{"id": "short-select",
            "type": "function", "function": {"name": "context_route", "arguments": json.dumps({
                "route": "memory_standalone", "query": "quartznebula", "memory_types": [],
                "include_short_horizon": True})}}]} if len(sends) == 1 else
            {"role": "assistant", "content": "Recalled the earlier preference."})
        return httpx.Response(200, json={"id": f"short-{len(sends)}", "model": "model-a",
            "choices": [{"message": message, "finish_reason": "tool_calls" if len(sends) == 1 else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    provider = ProductProviderAdapter(Registry("secret"), provider_id="relay", client=client,
                                     price_resolver=lambda *_: (1, 1, "price-v1"))
    await service.enqueue_turn(QueueTurnRequest(None, "typed-short-query", "Find the earlier quartznebula discussion."))
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
                                      visibility_memory=memory, recall_executor=memory.typed_recall)
    async def guard(request):
        guards.append(request)
        if fault == "late_forget" and len(guards) == 2:
            source = indexed.groups[0].registrations[1].envelope.evidence_refs[1].evidence_id
            await suppress(manager, memory.conversation_evidence_authority, source)
        current = await queue.current_snapshot(local_owner_auth().subject)
        await check_runtime_dependencies(db_path=state, stack=stack, sdk_run_id=current.sdk_run_id,
            request=request, policy_factory=lambda subject: runtime.history_policy)
    provider._pre_invoke_guard = guard
    try:
        assert await asyncio.wait_for(runtime._drive_once(), 20)
        assert len(projected) == 1 and len(guards) == 2
        assert len(sends) == (2 if fault is None else 1)
        if fault is None:
            assert "early real preference" in sends[1].content.decode()
        with sqlite3.connect(state) as db:
            terminal = db.execute("SELECT terminal_state FROM foreground_terminal_receipts ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        assert terminal == ("COMPLETED" if fault is None else "FAILED")
    finally:
        await runtime.close()
        await stack.close()
        await memory.close()
        await client.aclose()

"""Decisive typed-source guard: the remembered source is NOT in chat history."""

import asyncio
import json
import sqlite3

import aiosqlite
import httpx
import pytest
from deskpet.execution.primary_dependencies import (
    check_runtime_dependencies,
    current_disclosure,
    read_run_dependencies,
)
from deskpet.memory.human_memory_service import QueueTurnRequest
from deskpet.sdk_adapters.provider import ProductProviderAdapter
from tests.execution.test_primary_foreground_runtime import build
from tests.memory.test_primary_cognitive_controls import setup
from tests.sdk_adapters.test_product_host_ports import Registry


@pytest.mark.asyncio
@pytest.mark.parametrize("forget", [False, True])
async def test_selected_memory_barrier_without_incidental_history_suppression(
    tmp_path, forget
):
    s = await setup(tmp_path, queue_source=False)
    sent, executions, guards = [], [], []

    def physical(request):
        sent.append(json.loads(request.content))
        message = {"role": "assistant", "content": "The preference is concise."}
        if len(sent) == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "typed-selection",
                        "type": "function",
                        "function": {
                            "name": "context_route",
                            "arguments": json.dumps(
                                {"route": "memory_standalone", "query": "concise"}
                            ),
                        },
                    }
                ],
            }
        return httpx.Response(
            200,
            json={
                "id": f"reply-{len(sent)}",
                "model": "model-a",
                "choices": [
                    {
                        "message": message,
                        "finish_reason": "tool_calls" if len(sent) == 1 else "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 10,
                    "total_tokens": 20,
                },
            },
        )

    async def recall(**kwargs):
        lanes = await s["runtime"].typed_recall(**kwargs)
        (selected,) = lanes.execution.result.items
        assert selected.selected_item.source_ref == s["memory_id"]
        executions.append(lanes.execution)
        return lanes

    client = httpx.AsyncClient(transport=httpx.MockTransport(physical))
    provider = ProductProviderAdapter(
        Registry("test-secret"),
        provider_id="relay",
        client=client,
        price_resolver=lambda *_: (1, 1, "price-v1"),
    )
    await s["service"].enqueue_turn(
        QueueTurnRequest(None, "query", "What is my reply preference?")
    )
    runtime, stack, queue = await build(
        tmp_path / "foreground",
        s["path"],
        provider,
        dynamic=True,
        visibility_memory=s["runtime"],
        recall_executor=recall,
    )

    async def guard(request):
        guards.append(request)
        current = await queue.current_snapshot(s["auth"].subject)
        if len(guards) == 2:
            async with aiosqlite.connect(s["path"]) as db:
                db.row_factory = aiosqlite.Row
                run, proof = await read_run_dependencies(
                    db=db, stack=stack, sdk_run_id=current.sdk_run_id
                )
            assert len(proof["recall"]) == 1
            assert s["source_id"] not in {
                item["evidence_id"] for item in proof["evidence"]
            }
            if forget:
                response = await s["command"]("primary.memory.forget", s["payload"])
                assert response["payload"]["ok"], response
                # If recall bindings were dropped, this otherwise-identical
                # request would still pass. History cannot accidentally block it.
                async with aiosqlite.connect(s["path"]) as db:
                    db.row_factory = aiosqlite.Row
                    assert await runtime.history_policy.check_dependencies(
                        db=db,
                        primary_ref=run["primary_conversation_id"],
                        dependencies={**proof, "recall": []},
                        disclosure_context=current_disclosure(
                            run_id=current.sdk_run_id,
                            subject=s["auth"].subject,
                            request_id=request.request_id.value,
                        ),
                    )
        await check_runtime_dependencies(
            db_path=s["path"],
            stack=stack,
            sdk_run_id=current.sdk_run_id,
            request=request,
            policy_factory=lambda _: runtime.history_policy,
        )

    provider._pre_invoke_guard = guard
    try:
        assert await asyncio.wait_for(runtime._drive_once(), 20)
        assert len(executions) == 1 and len(guards) == 2
        assert len(sent) == (1 if forget else 2)
        assert "concise" not in json.dumps(sent[0]["messages"])
        if not forget:
            assert "concise" in json.dumps(sent[1]["messages"])
        with sqlite3.connect(s["path"]) as db:
            assert (
                db.execute("SELECT COUNT(*) FROM foreground_turns").fetchone()[0] == 1
            )
            assert db.execute(
                "SELECT terminal_state FROM foreground_terminal_receipts"
            ).fetchone()[0] == ("FAILED" if forget else "COMPLETED")
    finally:
        await runtime.close()
        await stack.close()
        await s["runtime"].close()
        await client.aclose()

"""Original unclosed write/revocation through actual production ClosureFallback."""
import asyncio
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import httpx
import pytest
from simple_harness import CallId, thaw_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage

from deskpet.execution.primary_context_pages import (
    HISTORY_PREFIX, PREFIX, PrimaryContextPageReader, _excerpt, project_history_group, verify_history_projections,
)
from deskpet.memory.human_memory_service import QueueTurnRequest
from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1
from deskpet.task_scope.protocol import canonical_hash
from deskpet.tools.context_page_in_tools import ContextPageInStore
from tests.execution.test_primary_create_new_runtime import CreateProvider, fixture
from tests.execution.test_primary_foreground_runtime import build
from tests.sdk_adapters.test_product_host_ports import Registry

LARGE = "中文边界" * 1400 + "A" * 1300 + "EXACT_PAGE_TAIL"



from deskpet.execution.current_tool_pages import CurrentToolProjector, MARKER, PREFIX as CURRENT_PREFIX
@pytest.mark.asyncio
@pytest.mark.parametrize("crash_before_terminal", [False, True])
async def test_revoked_unclosed_scope_real_fallback(tmp_path, monkeypatch, crash_before_terminal):
    mode = "forget_after_page"
    import main
    from deskpet.execution.primary_dependencies import read_run_dependencies
    from simple_harness_memory import SuppressionRequest, SuppressionScopeKind

    state, _, service, configured, authority = await fixture(tmp_path)
    import tests.execution.test_primary_foreground_runtime as runtime_fixture
    actual_authority = runtime_fixture.ProductRunContextAuthority
    def context_authority(**kwargs):
        return actual_authority(**kwargs, current_tool_projector=CurrentToolProjector(state, lambda: holder.stack))
    monkeypatch.setattr(runtime_fixture, "ProductRunContextAuthority", context_authority)
    from deskpet.execution import semantic_closure as closure
    actual_runtime = runtime_fixture.ForegroundRuntimeExecutionAuthority
    body_after_revoke = []
    original_observation = closure._scope_observation_tx
    async def observe_body(*args, **kwargs):
        body = await original_observation(*args, **kwargs)
        if getattr(holder, "revoked", False):
            body_after_revoke.append(canonical_hash(body))
        return body
    monkeypatch.setattr(closure, "_scope_observation_tx", observe_body)
    calls, settlement_statuses = [], []
    class Unused:
        def __getattr__(self, name):
            calls.append(name)
            raise AssertionError("Non-success fallback must not build a Provider request or mutation: " + name)
    def with_fallback(**kwargs):
        # The same production class/settle method instantiated by main's
        # _BoundClosureFallback; only unavailable model dependencies are traps.
        fallback = closure.ClosureFallback(state,
            invoker=Unused(), service=Unused(), run_facts_reader=Unused())
        settle = fallback.settle
        async def observed_settle(**arguments):
            result = await settle(**arguments)
            settlement_statuses.append(result.status)
            return result
        fallback.settle = observed_settle
        return actual_runtime(**kwargs, closure_fallback=fallback)
    monkeypatch.setattr(runtime_fixture, "ForegroundRuntimeExecutionAuthority", with_fallback)
    failures = []
    # 2026-09-08 HM-TO-A6：前台驱动改成了有界退避重试（原来一抛异常就永久退出）。
    # 这个用例验的是「崩在终态提交之前，什么都没有半落库，之后靠重开重放」，
    # 需要驱动在这一次注入之后就停下；把尝试数固定成 1 就精确保留了原语义，
    # 而不必把注入改成对每次尝试都成立（那会让幂等的 closure fallback 结算
    # 被重复观察到，与本用例要验的东西无关）。驱动的重试语义由
    # test_primary_foreground_runtime 的专门用例覆盖。
    from deskpet.execution import foreground_runtime as _fr
    monkeypatch.setattr(_fr, "DRIVER_RETRY_ATTEMPTS", 1)

    def crash(point):
        if crash_before_terminal and point == "terminal.before_commit" and not failures:
            failures.append(point)
            raise RuntimeError("injected-terminal-before-commit")


    write_module = importlib.import_module("deskpet.tools.os_tools.write_file")
    original_write = write_module.write_file
    def write_and_read(arguments, *, execution_context):
        result = json.loads(original_write(arguments, execution_context=execution_context))
        assert "error" not in result, result.get("error")
        # A real tool execution reads the file it actually wrote. The SDK,
        # Host observer and receipt producers handle its result normally.
        result["readback_text"] = (Path(execution_context.write_scope_root) / arguments["path"]).read_text()
        return json.dumps(result, ensure_ascii=False)
    monkeypatch.setattr(write_module, "write_file", write_and_read)
    provider = CreateProvider()
    pages = ContextPageInStore()
    runtime, stack, queue = await build(tmp_path, state, provider, fault=crash, dynamic=True,
        binding_authority=authority, configured_root=configured, page_in_store=pages)
    holder = SimpleNamespace(runtime=runtime, stack=stack, queue=queue, phase=0, step=0,
        sent=[], responses=[], next_response=None, source=None, second_run=None, first_run=None)
    actual_page_reader = PrimaryContextPageReader(state, stack_getter=lambda:holder.stack,
        policy_factory=lambda _:holder.runtime.history_policy)
    async def read_page(arguments):
        from deskpet.sdk_adapters.tools import active_product_tool_context
        from deskpet.execution.current_tool_pages import source_content
        context = active_product_tool_context()
        facts = holder.stack.read_primary_effect_page_facts(context.run_id.value, context.effect_id.value)
        assert not facts[0].terminal  # actual SDK-owned in-flight page effect
        with pytest.raises(ValueError, match="source_not_pageable"):
            source_content(facts)
        return await actual_page_reader(arguments)
    pages.primary_reader = read_page

    async def suppress():
        manager = await holder.runtime.history_memory.manager()
        start, _ = holder.stack.read_primary_dependency_facts(holder.first_run)
        source = start["input"]["context_metadata"]["visibility_dependencies"]["evidence"][0]
        await manager.backend.suppress(SuppressionRequest("forget-page", runtime.subject,
            SuppressionScopeKind.EVIDENCE, source["evidence_id"], "user_forget", 30.0),
            principal=holder.runtime.history_memory.principal())
        holder.revoked = True

    async def physical(request):
        holder.sent.append(json.loads(request.content))
        response = holder.next_response
        message = {"role":"assistant", "content":response.message.content}
        if response.tool_calls:
            message["tool_calls"] = [dict(id=call.call_id.value, type="function",
                function=dict(name=call.name, arguments=json.dumps(thaw_json(call.arguments)))) for call in response.tool_calls]
        return httpx.Response(200, json=dict(id="actual-page-wire", model="model",
            choices=[dict(message=message, finish_reason="tool_calls" if response.tool_calls else "stop")],
            usage=dict(prompt_tokens=10, completion_tokens=10, total_tokens=20)))

    client = httpx.AsyncClient(transport=httpx.MockTransport(physical))
    registry = Registry("test-not-a-real-key")
    registry.entry.id, registry.entry.model, registry.entry.models = "fixture", "model", ("model",)
    registry.entry.incarnation_id, registry.entry.config_revision = "fixture-1", 1
    monkeypatch.setattr(main, "_state_db_path", state)
    monkeypatch.setattr(main, "_sdk_runtime_stack", stack)
    monkeypatch.setattr(main, "_primary_history_policy", lambda _:holder.runtime.history_policy)
    monkeypatch.setattr(main, "_sdk_price_snapshot", lambda *_: (1, 1, "price-v1"))
    get_service = main.service_context.get
    monkeypatch.setattr(main.service_context, "get", lambda key, *a, **kw:
        None if key == "sdk_typed_context_use_authority" else get_service(key, *a, **kw))
    resolver = main._ProductSdkProviderBindingResolver(registry, client)
    scripted = provider.invoke

    def tool(request, name, args):
        return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, name),
            tool_calls=(ProviderToolCall(CallId(f"page-{holder.step}"), name, args),),
            model="model", usage=ProviderUsage(10, 10, 20))

    async def invoke(request, *, cancel):
        current = await holder.queue.current_snapshot(runtime.subject)
        holder.first_run = current.sdk_run_id
        n = len(provider.requests)
        if n == 4:
            provider.requests.append(request)
            response = ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Write bounded chunks."),
                tool_calls=tuple(ProviderToolCall(CallId(f"write-chunk-{i}"), "write_file",
                    dict(path="fresh.txt", content=LARGE[offset:offset + 3000],
                         mode="write" if offset == 0 else "append"))
                    for i, offset in enumerate(range(0, len(LARGE), 3000))),
                model="model", usage=ProviderUsage(10, 10, 20))
        elif n == 5 and holder.phase == 0:
            actual = stack.read_primary_run_messages(holder.first_run,
                current_text="Create a project and write its file")
            holder.raw_tool = next(m["content"] for m in actual
                if m["role"] == "tool" and "EXACT_PAGE_TAIL" in m["content"])
            holder.tail_offset = len(holder.raw_tool[:holder.raw_tool.index("EXACT_PAGE_TAIL")].encode())
            summaries = [json.loads(m.content) for m in request.messages
                if m.role.value == "tool" and m.metadata and m.metadata.get("source") == MARKER]
            holder.source = next(x for x in summaries
                if x["source_hash"] == hashlib.sha256(holder.raw_tool.encode()).hexdigest())
            assert len(summaries) == 2  # both real large append results stay distinct
            assert LARGE not in str(request.messages) and "page:causal:" not in str(request.messages)
            assert len(holder.source["excerpt"].encode()) <= 1024
            holder.phase = 1
            # Reuse a preceding turn's actual raw ID. Public parent/effect
            # identity, not a raw-ID lookup, must locate this new page call.
            response = ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Read exact current tail."),
                tool_calls=(ProviderToolCall(CallId("write-chunk-2"), "context_page_in", dict(
                    reference_id=holder.source["reference_id"].rsplit(":", 1)[0] + ":" + str(holder.tail_offset),
                    source_hash=holder.source["source_hash"])),),
                model="model", usage=ProviderUsage(10, 10, 20))
        else:
            if n == 5:
                page = json.loads(next(m.content for m in reversed(request.messages) if m.role.value == "tool"))["value"]
                assert page["kind"] == "primary_current_tool_page_v1"
                assert page["content"] == holder.raw_tool.encode()[holder.tail_offset:].decode()
                assert page["page_hash"] == hashlib.sha256(page["content"].encode()).hexdigest()
                holder.responses.append(page)
                if mode == "forget_after_page":
                    await suppress()
            response = await scripted(request, cancel=cancel)
        holder.next_response = response
        binding = SdkRunBindingV1.from_record(holder.stack.read_closure_run_facts(current.sdk_run_id).binding_record)
        return await resolver.build_authority(binding).provider.invoke(request, cancel=cancel)

    provider.invoke = invoke
    async def run():
        await holder.runtime.after_enqueue(subject=runtime.subject)
        await asyncio.wait_for(holder.runtime.drain(), 20)
        if holder.runtime.last_error is not None:
            raise holder.runtime.last_error

    async def pending_facts():
        from deskpet.execution.semantic_closure import dirty_state_tx
        async with aiosqlite.connect(state) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT * FROM task_scope_closure_receipts WHERE sdk_run_id=?",
                (holder.first_run,))).fetchall()
            assert len(rows) == 1
            row = dict(rows[0])
            assert row["outcome"] == "pending" and row["plan_id"] is None and row["attempt_id"] is None
            assert row["reason_code"] == "closure_run_not_completed"
            dirty = await dirty_state_tx(db, row["task_scope_id"])
            assert dirty.is_dirty and dirty.closure_watermark == 0
            head = await (await db.execute("SELECT current_revision FROM task_scope_heads WHERE task_scope_id=?",
                (row["task_scope_id"],))).fetchone()
            assert head[0] == 1
            return row

    try:
        await service.enqueue_turn(QueueTurnRequest(None, "large-source", "Create a project and write its file"))
        if crash_before_terminal:
            with pytest.raises(RuntimeError, match="injected-terminal-before-commit"):
                await run()
        else:
            await run()
        assert holder.responses and "EXACT_PAGE_TAIL" in holder.responses[0]["content"]
        assert len(holder.sent) == 6 and calls == []
        actual_terminal = stack.read_run_terminal_evidence(holder.first_run)
        assert actual_terminal.state == "failed"
        pending_before = await pending_facts()
        with __import__("sqlite3").connect(state) as db:
            assert db.execute("SELECT COUNT(*) FROM foreground_terminal_receipts").fetchone()[0] == int(not crash_before_terminal)
            host_id = db.execute("SELECT host_run_id FROM foreground_run_sdk_bindings WHERE sdk_run_id=?",
                (holder.first_run,)).fetchone()[0]
        await runtime.close()
        await stack.close()
        holder.runtime, holder.stack, holder.queue = await build(tmp_path, state, provider, dynamic=True,
            binding_authority=authority, configured_root=configured, page_in_store=pages)
        await run()  # same failed SDK Run, no Provider retransmission
        assert len(holder.sent) == 6 and calls == []
        assert await pending_facts() == pending_before
        assert settlement_statuses == (["pending", "pending"] if crash_before_terminal else ["pending"])
        recovered_terminal = holder.stack.read_run_terminal_evidence(holder.first_run)
        assert recovered_terminal == actual_terminal
        with __import__("sqlite3").connect(state) as db:
            db.row_factory = __import__("sqlite3").Row
            terminal_row = dict(db.execute("SELECT * FROM foreground_terminal_receipts WHERE host_run_id=?", (host_id,)).fetchone())
            assert terminal_row["terminal_state"] == "FAILED"
            assert db.execute("SELECT current_state FROM foreground_turn_heads").fetchone()[0] == "SETTLED"
        from deskpet.execution.foreground_queue import ForegroundQueueError
        with pytest.raises(ForegroundQueueError, match="foreground_terminal_immutable"):
            await holder.queue.record_sdk_terminal(host_run_id=host_id, sdk_run_id=holder.first_run,
                owner_id="primary-worker", generation=terminal_row["generation"], terminal_state="COMPLETED",
                sdk_event_id=terminal_row["sdk_event_id"], sdk_event_hash=terminal_row["sdk_event_hash"],
                idempotency_key="must-not-promote-failure")
        next_requests = []
        async def next_input(request, *, cancel):
            current = await holder.queue.current_snapshot(holder.runtime.subject)
            assert current.sdk_run_id != holder.first_run
            assert "EXACT_PAGE_TAIL" not in str(request.messages)
            assert "Create a project and write its file" not in str(request.messages)
            next_requests.append(request)
            if len(next_requests) == 1:
                response = ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Independent input."),
                    tool_calls=(ProviderToolCall(CallId("new-direct"), "context_route", {"route": "direct_standalone"}),),
                    model="model", usage=ProviderUsage(10, 10, 20))
            else:
                response = ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Ready for new work."),
                    model="model", usage=ProviderUsage(10, 10, 20))
            holder.next_response = response
            binding = SdkRunBindingV1.from_record(holder.stack.read_closure_run_facts(current.sdk_run_id).binding_record)
            return await resolver.build_authority(binding).provider.invoke(request, cancel=cancel)
        provider.invoke = next_input
        monkeypatch.setattr(main, "_sdk_runtime_stack", holder.stack)
        await service.enqueue_turn(QueueTurnRequest(None, "independent-next-input", "Hello, begin independent work"))
        await run()
        assert len(next_requests) == 2 and len(holder.sent) == 8
        assert await holder.queue.current_snapshot(holder.runtime.subject) is None
        assert await pending_facts() == pending_before and calls == []
        # The baseline production fallback may still render a source-bearing
        # observation after revocation. This assertion is the new narrow oracle,
        # after verifying the existing pending/terminal/new-input behavior.
        assert body_after_revoke == []
    finally:
        await holder.runtime.close()
        await holder.stack.close()
        await client.aclose()

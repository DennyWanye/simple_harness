"""New history-page producer -> public tool -> physical adapter controls."""
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
@pytest.mark.parametrize("mode", ["allow", "forget_after_page"])
async def test_actual_current_effect_page_and_physical_guard(tmp_path, monkeypatch, mode):
    import main
    from deskpet.execution.primary_dependencies import read_run_dependencies
    from simple_harness_memory import SuppressionRequest, SuppressionScopeKind

    state, _, service, configured, authority = await fixture(tmp_path)
    import tests.execution.test_primary_foreground_runtime as runtime_fixture
    actual_authority = runtime_fixture.ProductRunContextAuthority
    def context_authority(**kwargs):
        return actual_authority(**kwargs, current_tool_projector=CurrentToolProjector(state, lambda: holder.stack))
    monkeypatch.setattr(runtime_fixture, "ProductRunContextAuthority", context_authority)

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
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
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
        elif n == 5 and holder.phase == 0 and mode == "forget_after_page":
            # Settle the actual write scope through its public Tool first.
            # Otherwise the unrelated semantic-closure obligation correctly
            # keeps Host terminal pending after a privacy refusal.
            response = await scripted(request, cancel=cancel)
        elif (n == 5 or (n == 6 and mode == "forget_after_page")) and holder.phase == 0:
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
            if n == 5 or (n == 6 and mode == "forget_after_page"):
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

    try:
        await service.enqueue_turn(QueueTurnRequest(None, "large-source", "Create a project and write its file"))
        await run()
        assert holder.responses and "EXACT_PAGE_TAIL" in holder.responses[0]["content"]
        if mode == "forget_after_page":
            assert len(holder.sent) == 7
            terminal = stack.read_run_terminal_evidence(holder.first_run)
            assert terminal.state == "FAILED"
            return
        assert len(holder.sent) == 8
        async with aiosqlite.connect(state) as db:
            db.row_factory = aiosqlite.Row
            _, before = await read_run_dependencies(db=db, stack=stack, sdk_run_id=holder.first_run)
        await runtime.close()
        await stack.close()
        holder.runtime, holder.stack, holder.queue = await build(tmp_path, state, provider, dynamic=True,
            binding_authority=authority, configured_root=configured, page_in_store=pages)
        async with aiosqlite.connect(state) as db:
            db.row_factory = aiosqlite.Row
            _, after = await read_run_dependencies(db=db, stack=holder.stack, sdk_run_id=holder.first_run)
        assert before == after and len(holder.sent) == 8
    finally:
        await holder.runtime.close()
        await holder.stack.close()
        await client.aclose()

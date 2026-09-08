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
    HISTORY_PREFIX, HISTORY_SUFFIX, PREFIX, PrimaryContextPageReader, _excerpt, project_history_group,
    verify_history_projections,
)
from deskpet.memory.human_memory_service import QueueTurnRequest
from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1
from deskpet.task_scope.protocol import canonical_hash
from deskpet.tools.context_page_in_tools import ContextPageInStore
from tests.execution.test_primary_create_new_runtime import CreateProvider, fixture
from tests.execution.test_primary_foreground_runtime import build
from tests.sdk_adapters.test_product_host_ports import Registry

LARGE = "中文边界" * 1400 + "A" * 1300 + "EXACT_PAGE_TAIL"


@pytest.mark.asyncio
async def test_utf8_page_oracle_and_no_invented_source():
    # Independent bytes oracle: walk the actual returned boundary, preserving
    # the complete original stream, including characters split by byte 1024.
    offset, chunks = 0, []
    while offset < len(LARGE.encode()):
        piece = _excerpt(LARGE, offset)
        assert 0 < len(piece.encode()) <= 1024
        chunks.append(piece)
        offset += len(piece.encode())
    assert "".join(chunks) == LARGE
    for bad in (True, -1, 1, len(LARGE.encode()), len(LARGE.encode()) + 1):
        with pytest.raises(ValueError):
            _excerpt(LARGE, bad)
    group = dict(source_ref="primary-terminal:legacy", source_hash="a" * 64,
        terminal_state="COMPLETED", messages=[dict(role="user", content="read"),
            dict(role="tool", content=LARGE, call_id="real-call", name="read"),
            dict(role="assistant", content="done")])
    assert PREFIX not in project_history_group(group, run_id="new")[0]["content"]
    # A quoted group is delimited on both sides; a plain turn group is never
    # wrapped, so a single-turn request carries no marker at all.
    quoted = project_history_group(group, run_id="new")[0]["content"]
    assert quoted.startswith(HISTORY_PREFIX) and quoted.endswith(HISTORY_SUFFIX)
    assert json.loads(quoted[len(HISTORY_PREFIX):-len(HISTORY_SUFFIX)])["kind"] == "historical_causal_group"
    plain = dict(group, messages=[dict(role="user", content="read"), dict(role="assistant", content="done")])
    assert project_history_group(plain, run_id="new") == plain["messages"]
    group.update(source_ref="actual-source", terminal_state="FAILED")
    assert PREFIX not in project_history_group(group, run_id="new")[0]["content"]
    group["terminal_state"] = "COMPLETED"
    literal = project_history_group(group, run_id="new")[0]
    literal["metadata"] = None
    # A literal USER quotation cannot enroll its own claimed source as Host
    # metadata, and must not trigger SDK/source reads or a new denial.
    assert await verify_history_projections(db=None, stack=None, run=None, sdk_run_id="new",
        start={"input": {"messages": [literal]}}, proof={"evidence": []}) == ()


def summary_from(request, *, content_hash):
    for message in request.messages:
        if message.role.value != "user" or not isinstance(message.content, str) or not message.content.startswith(HISTORY_PREFIX):
            continue
        assert message.content.endswith(HISTORY_SUFFIX)
        group = json.loads(message.content[len(HISTORY_PREFIX):-len(HISTORY_SUFFIX)])
        for item in group["messages"]:
            if item["role"] == "tool":
                content = json.loads(item["content"])
                if (content.get("kind") == "primary_tool_result_summary_v1"
                        and content.get("content_hash") == content_hash):
                    return content
    raise AssertionError("actual initial history summary missing")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["allow", "forget_before_page", "forget_after_page"])
async def test_actual_history_page_and_physical_guard(tmp_path, monkeypatch, mode):
    import main
    from deskpet.execution.primary_dependencies import read_run_dependencies
    from simple_harness_memory import SuppressionRequest, SuppressionScopeKind

    state, _, service, configured, authority = await fixture(tmp_path)
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
    pages.primary_reader = PrimaryContextPageReader(state, stack_getter=lambda:holder.stack,
        policy_factory=lambda _:holder.runtime.history_policy)

    async def suppress():
        manager = await holder.runtime.history_memory.manager()
        await manager.backend.suppress(SuppressionRequest("forget-page", runtime.subject,
            SuppressionScopeKind.EVIDENCE, holder.source["evidence_id"], "user_forget", 30.0),
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
        if holder.phase == 0:
            holder.first_run = current.sdk_run_id
            if len(provider.requests) == 4:
                provider.requests.append(request)
                # Respect the real tool's 3000-character single-write cap.
                # A real sequential batch creates then appends the full file.
                response = ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Write bounded chunks."),
                    tool_calls=tuple(ProviderToolCall(CallId(f"write-chunk-{i}"), "write_file",
                        dict(path="fresh.txt", content=LARGE[offset:offset + 3000],
                             mode="write" if offset == 0 else "append"))
                        for i, offset in enumerate(range(0, len(LARGE), 3000))),
                    model="model", usage=ProviderUsage(10, 10, 20))
            else:
                response = await scripted(request, cancel=cancel)
        else:
            holder.second_run = current.sdk_run_id
            n = holder.step
            holder.step += 1
            if n == 0:
                holder.source = summary_from(request,
                    content_hash=hashlib.sha256(holder.raw_tool.encode()).hexdigest())
                assert LARGE not in str(request.messages)
                assert len(holder.source["excerpt"].encode()) <= 1024
                assert holder.source["content_hash"] == hashlib.sha256(holder.raw_tool.encode()).hexdigest()
                args = dict(reference_id=holder.source["reference_id"], source_hash=holder.source["content_hash"])
                if mode == "forget_before_page":
                    await suppress()  # excerpt itself must be refused before wire
                response = tool(request, "context_page_in", args)
            else:
                values = [json.loads(m.content)["value"] for m in request.messages
                    if m.role.value == "tool" and isinstance(m.content, str)
                    and isinstance(json.loads(m.content).get("value"), dict)]
                if n == 4:
                    failed = json.loads(next(m.content for m in reversed(request.messages)
                        if m.role.value == "tool"))
                    assert failed["outcome"] == "failed" and failed["value"] is None
                    assert failed["error_code"] == "primary_page_hash_mismatch"
                    page = {"error": failed["error_code"]}
                else:
                    page = values[-1]
                holder.responses.append(page)
                if mode == "forget_after_page":
                    assert page["kind"] == "primary_tool_history_page_v1"
                    await suppress()  # actual result exists, then invalidate before physical invoke
                if n == 1:
                    assert page["source_hash"] == holder.source["content_hash"]
                    assert page["page_hash"] == hashlib.sha256(page["content"].encode()).hexdigest()
                    assert page["next_reference_id"].endswith(":" + str(len(page["content"].encode())))
                    response = tool(request, "context_page_in", dict(
                        reference_id=page["next_reference_id"],
                        source_hash=holder.source["content_hash"]))
                elif n == 2:
                    assert page["content"] == holder.raw_tool.encode()[page["offset"]:page["offset"] + len(page["content"].encode())].decode()
                    response = tool(request, "context_page_in", dict(
                        reference_id=holder.source["reference_id"].rsplit(":", 1)[0] + ":" + str(holder.tail_offset),
                        source_hash=holder.source["content_hash"]))
                elif n == 3:
                    assert "EXACT_PAGE_TAIL" in page["content"]
                    response = tool(request, "context_page_in", dict(
                        reference_id=holder.source["reference_id"], source_hash="0" * 64))
                else:
                    assert page["error"] == "primary_page_hash_mismatch"
                    response = ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Read the exact tail."),
                        model="model", usage=ProviderUsage(10, 10, 20))
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
        actual = stack.read_primary_run_messages(holder.first_run,
            current_text="Create a project and write its file")
        holder.raw_tool = next(m["content"] for m in actual
            if m["role"] == "tool" and "EXACT_PAGE_TAIL" in m["content"])
        holder.tail_offset = len(holder.raw_tool[:holder.raw_tool.index("EXACT_PAGE_TAIL")].encode())
        first_sends = len(holder.sent)
        holder.phase = 1
        await service.enqueue_turn(QueueTurnRequest(None, "read-exact-page", "Read the exact tail of the previous tool output"))
        await run()
        if mode != "allow":
            assert len(holder.sent) == first_sends + (1 if mode == "forget_after_page" else 0)
            with __import__("sqlite3").connect(state) as db:
                assert db.execute("SELECT terminal_state FROM foreground_terminal_receipts WHERE sdk_run_id=?",
                    (holder.second_run,)).fetchone()[0] == "FAILED"
            return
        assert len(holder.sent) == first_sends + 5
        assert len(holder.responses) == 4
        async with aiosqlite.connect(state) as db:
            db.row_factory = aiosqlite.Row
            _, before = await read_run_dependencies(db=db, stack=stack, sdk_run_id=holder.second_run)
        await runtime.close()
        await stack.close()
        holder.runtime, holder.stack, holder.queue = await build(tmp_path, state, provider, dynamic=True,
            binding_authority=authority, configured_root=configured, page_in_store=pages)
        # A new full stack verifies the original durable effect and original
        # Run admission, with no ephemeral reference store and no new send.
        async with aiosqlite.connect(state) as db:
            db.row_factory = aiosqlite.Row
            _, after = await read_run_dependencies(db=db, stack=holder.stack, sdk_run_id=holder.second_run)
        assert after == before and len(holder.sent) == first_sends + 5
    finally:
        await holder.runtime.close()
        await holder.stack.close()
        await client.aclose()

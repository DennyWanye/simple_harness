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



from deskpet.execution.current_tool_pages import (
    CONTROL_TOOLS, CurrentToolProjector, MARKER, PREFIX as CURRENT_PREFIX,
)
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["allow", "forget_after_page"])
async def test_actual_current_effect_page_and_physical_guard(tmp_path, monkeypatch, mode, provider_context_window=32768, write_chunks=None, expected_budget_stop=False):
    import main
    from deskpet.execution.primary_dependencies import read_run_dependencies
    from simple_harness_memory import SuppressionRequest, SuppressionScopeKind

    state, _, service, configured, authority = await fixture(tmp_path)
    import tests.execution.test_primary_foreground_runtime as runtime_fixture
    actual_authority = runtime_fixture.ProductRunContextAuthority
    def context_authority(**kwargs):
        return actual_authority(**kwargs, current_tool_projector=CurrentToolProjector(state, lambda: holder.stack))
    monkeypatch.setattr(runtime_fixture, "ProductRunContextAuthority", context_authority)
    if expected_budget_stop:
        from deskpet.execution.semantic_closure import ClosureFallback
        actual_runtime = runtime_fixture.ForegroundRuntimeExecutionAuthority
        class Unused:
            def __getattr__(self, name):
                raise AssertionError("Failed-budget fallback attempted model/mutation: " + name)
        def with_fallback(**kwargs):
            return actual_runtime(**kwargs, closure_fallback=ClosureFallback(state,
                invoker=Unused(), service=Unused(), run_facts_reader=Unused()))
        monkeypatch.setattr(runtime_fixture, "ForegroundRuntimeExecutionAuthority", with_fallback)

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
        binding_authority=authority, configured_root=configured, page_in_store=pages,
        provider_context_window=provider_context_window)
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
                    dict(path="fresh.txt", content=chunk,
                         mode="write" if i == 0 else "append"))
                    for i, chunk in enumerate(write_chunks if write_chunks is not None else
                        tuple(LARGE[offset:offset + 3000] for offset in range(0, len(LARGE), 3000)))),
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
        assert binding.context_window == provider_context_window
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
        if expected_budget_stop:
            assert stack.read_run_terminal_evidence(holder.first_run).state == "failed"
            assert await queue.current_snapshot(runtime.subject) is None
            async with aiosqlite.connect(state) as db:
                rows = await (await db.execute("SELECT outcome,reason_code FROM task_scope_closure_receipts WHERE sdk_run_id=?",
                    (holder.first_run,))).fetchall()
                # Incident N (2026-09-08): the tool schemas are now charged to
                # protected_tokens, so on the smallest windows the budget can
                # stop the Run *before* it ever routes a TaskScope — then there
                # is no closure receipt at all.  Either shape is a safe stop;
                # what must never appear is a settled/closed receipt.
                assert rows in ([], [("pending", "closure_run_not_completed")]), rows
            before = len(holder.sent)
            await runtime.close()
            await stack.close()
            holder.runtime, holder.stack, holder.queue = await build(tmp_path, state, provider, dynamic=True,
                binding_authority=authority, configured_root=configured, page_in_store=pages,
                provider_context_window=provider_context_window)
            assert holder.stack.read_run_terminal_evidence(holder.first_run).state == "failed"
            assert not await holder.runtime._drive_once()
            assert len(holder.sent) == before
            return
        assert holder.responses and "EXACT_PAGE_TAIL" in holder.responses[0]["content"]
        if mode == "forget_after_page":
            assert len(holder.sent) == 7
            terminal = stack.read_run_terminal_evidence(holder.first_run)
            assert terminal.state == "failed"
            return
        assert len(holder.sent) == 8
        async with aiosqlite.connect(state) as db:
            db.row_factory = aiosqlite.Row
            _, before = await read_run_dependencies(db=db, stack=stack, sdk_run_id=holder.first_run)
        await runtime.close()
        await stack.close()
        holder.runtime, holder.stack, holder.queue = await build(tmp_path, state, provider, dynamic=True,
            binding_authority=authority, configured_root=configured, page_in_store=pages,
        provider_context_window=provider_context_window)
        async with aiosqlite.connect(state) as db:
            db.row_factory = aiosqlite.Row
            _, after = await read_run_dependencies(db=db, stack=holder.stack, sdk_run_id=holder.first_run)
        assert before == after and len(holder.sent) == 8
    finally:
        await holder.runtime.close()
        await holder.stack.close()
        await client.aclose()


BOUND_BATCHES, BOUND_PER_BATCH = 9, 4
BOUND_TAIL = "-TAIL-ANCHOR-"
BOUND_FAIL_PATH = "bound-fail.txt"


def _bound_body(index):
    # ASCII only: text_tokens() is then exactly bytes/4, so the assertions below
    # compare the real Host estimator against the real frozen budget.  2900 chars
    # is just under write_file's own 3000-char hard cap.
    return f"BOUND-{index:02d}-" + "x" * 2860 + BOUND_TAIL + f"{index:02d}"


@pytest.mark.asyncio
async def test_same_run_settled_results_get_a_per_result_ceiling_and_page_back_exact_bytes(
        tmp_path, monkeypatch):
    """HM-AC-6: one Run's own settled tool results are bounded, not just history.

    36 settled ``write_file`` results (none of them over the 16 KiB per-result
    threshold) accumulate inside the single open causal group, which history
    trimming never touches.  Their raw bodies alone exceed the frozen effective
    budget.  The same-Run bound must page the oldest ones out — while
    ``context_page_in`` still returns their exact bytes, a *failed* settled
    result never fails the Run, and the newest provider turn keeps its bodies.
    """
    import main
    from deskpet.execution.current_tool_pages import current_tool_allowance
    from deskpet.sdk_adapters.context_partitions import effective_input_budget, text_tokens

    window = 32768
    budget = effective_input_budget(window)
    allowance = current_tool_allowance({"context_window": window})
    # The paged form still costs a fixed ~1.9 KiB per settled result (descriptor
    # + 1 KiB excerpt), so the bound is a hard per-result ceiling rather than a
    # promise to reach `allowance` exactly; what must hold is that the request
    # keeps fitting the frozen budget as settled results keep arriving.
    ceiling = budget * 3 // 4

    state, _, service, configured, authority = await fixture(tmp_path)
    import tests.execution.test_primary_foreground_runtime as runtime_fixture
    actual_authority = runtime_fixture.ProductRunContextAuthority
    def context_authority(**kwargs):
        return actual_authority(**kwargs, current_tool_projector=CurrentToolProjector(state, lambda: holder.stack))
    monkeypatch.setattr(runtime_fixture, "ProductRunContextAuthority", context_authority)

    write_module = importlib.import_module("deskpet.tools.os_tools.write_file")
    original_write = write_module.write_file
    def write_and_read(arguments, *, execution_context):
        if arguments["path"] == BOUND_FAIL_PATH:
            # One genuinely failing tool call: its effect settles non-succeeded
            # and therefore has no pageable public body. It must keep its own
            # bytes, not abort the Run.
            holder.failed += 1
            raise RuntimeError("bound-fixture-write-refused")
        result = json.loads(original_write(arguments, execution_context=execution_context))
        if "error" in result:
            holder.write_errors.append(result["error"])
            return json.dumps(result, ensure_ascii=False)
        result["readback_text"] = (Path(execution_context.write_scope_root) / arguments["path"]).read_text()
        return json.dumps(result, ensure_ascii=False)
    monkeypatch.setattr(write_module, "write_file", write_and_read)

    provider = CreateProvider()
    pages = ContextPageInStore()
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
        binding_authority=authority, configured_root=configured, page_in_store=pages,
        provider_context_window=window)
    holder = SimpleNamespace(runtime=runtime, stack=stack, queue=queue, seen=[], next_response=None,
                             first_run=None, source=None, raw=None, offset=None, page=None, closed=False,
                             write_errors=[], failed=0)
    pages.primary_reader = PrimaryContextPageReader(state, stack_getter=lambda: holder.stack,
                                                    policy_factory=lambda _: holder.runtime.history_policy)

    async def physical(request):
        response = holder.next_response
        message = {"role": "assistant", "content": response.message.content}
        if response.tool_calls:
            message["tool_calls"] = [dict(id=call.call_id.value, type="function",
                function=dict(name=call.name, arguments=json.dumps(thaw_json(call.arguments))))
                for call in response.tool_calls]
        return httpx.Response(200, json=dict(id="bound-wire", model="model",
            choices=[dict(message=message, finish_reason="tool_calls" if response.tool_calls else "stop")],
            usage=dict(prompt_tokens=10, completion_tokens=10, total_tokens=20)))

    client = httpx.AsyncClient(transport=httpx.MockTransport(physical))
    registry = Registry("test-not-a-real-key")
    registry.entry.id, registry.entry.model, registry.entry.models = "fixture", "model", ("model",)
    registry.entry.incarnation_id, registry.entry.config_revision = "fixture-1", 1
    monkeypatch.setattr(main, "_state_db_path", state)
    monkeypatch.setattr(main, "_sdk_runtime_stack", stack)
    monkeypatch.setattr(main, "_primary_history_policy", lambda _: holder.runtime.history_policy)
    monkeypatch.setattr(main, "_sdk_price_snapshot", lambda *_: (1, 1, "price-v1"))
    get_service = main.service_context.get
    monkeypatch.setattr(main.service_context, "get", lambda key, *a, **kw:
        None if key == "sdk_typed_context_use_authority" else get_service(key, *a, **kw))
    resolver = main._ProductSdkProviderBindingResolver(registry, client)
    scripted = provider.invoke
    current_text = "Create a project and write its file"

    def call(request, step, name, args):
        return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, name),
            tool_calls=(ProviderToolCall(CallId(f"bound-{step}"), name, args),),
            model="model", usage=ProviderUsage(10, 10, 20))

    async def invoke(request, *, cancel):
        snapshot = await holder.queue.current_snapshot(runtime.subject)
        holder.first_run = snapshot.sdk_run_id
        holder.seen.append(request)
        n = len(provider.requests)
        if n <= 3:
            response = await scripted(request, cancel=cancel)
        else:
            provider.requests.append(request)
            batch = n - 4
            if batch < BOUND_BATCHES:
                response = ProviderResponse(request.request_id,
                    Message(MessageRole.ASSISTANT, "write bounded chunks"),
                    tool_calls=tuple(ProviderToolCall(CallId(f"bound-w-{batch * BOUND_PER_BATCH + k}"),
                        "write_file", dict(
                            path=(BOUND_FAIL_PATH if (batch, k) == (0, 0)
                                  else f"bound-{batch * BOUND_PER_BATCH + k}.txt"),
                            mode="write", content=_bound_body(batch * BOUND_PER_BATCH + k)))
                        for k in range(BOUND_PER_BATCH)),
                    model="model", usage=ProviderUsage(10, 10, 20))
            elif holder.source is None:
                summaries = [json.loads(m.content) for m in request.messages if m.role.value == "tool"
                             and m.metadata and m.metadata.get("source") == MARKER]
                assert summaries, "the same-Run bound produced no page reference"
                actual = stack.read_primary_run_messages(holder.first_run, current_text=current_text)
                by_hash = {hashlib.sha256(m["content"].encode()).hexdigest(): m["content"]
                           for m in actual if m["role"] == "tool"}
                # A bounded (sub-16 KiB) settled body must page back exactly too.
                holder.source = next(s for s in summaries
                                     if len(by_hash[s["source_hash"]].encode()) <= 16384)
                holder.raw = by_hash[holder.source["source_hash"]]
                assert holder.source["reference_id"].startswith(CURRENT_PREFIX)
                assert len(holder.source["excerpt"].encode()) <= 1024
                holder.offset = len(holder.raw[:holder.raw.index(BOUND_TAIL)].encode())
                response = call(request, "page", "context_page_in", dict(
                    reference_id=holder.source["reference_id"].rsplit(":", 1)[0] + ":" + str(holder.offset),
                    source_hash=holder.source["source_hash"]))
            elif holder.page is None:
                holder.page = json.loads(next(m.content for m in reversed(request.messages)
                                              if m.role.value == "tool"))["value"]
                instruction = next(json.loads(m.content) for m in request.messages if m.role.value == "system"
                                   and isinstance(m.content, str) and '"task_scope_closure_required"' in m.content)
                response = call(request, "close", "task_scope_update", dict(outcome="no_mutation",
                    base_revision=instruction["current_revision"], closure_reason="Chunks written.",
                    evidence_refs=instruction["allowed_evidence_refs"], idempotency_key="bound-close"))
            else:
                holder.closed = True
                response = ProviderResponse(request.request_id,
                    Message(MessageRole.ASSISTANT, "bounded and paged"), model="model",
                    usage=ProviderUsage(10, 10, 20))
        holder.next_response = response
        binding = SdkRunBindingV1.from_record(holder.stack.read_closure_run_facts(holder.first_run).binding_record)
        assert binding.context_window == window
        return await resolver.build_authority(binding).provider.invoke(request, cancel=cancel)

    provider.invoke = invoke
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "bounded-source", current_text))
        await holder.runtime.after_enqueue(subject=runtime.subject)
        await asyncio.wait_for(holder.runtime.drain(), 120)
        assert not holder.write_errors, holder.write_errors[:2]
        if holder.runtime.last_error is not None:
            raise holder.runtime.last_error
        assert holder.closed

        # 1) The page really returns the exact settled bytes of a sub-16 KiB body.
        assert holder.page["kind"] == "primary_current_tool_page_v1"
        assert holder.page["content"] == holder.raw.encode()[holder.offset:].decode()
        assert holder.page["page_hash"] == hashlib.sha256(holder.page["content"].encode()).hexdigest()
        assert BOUND_TAIL in holder.page["content"]

        # 2) >=20 same-Run settled results really accumulated, and their raw
        #    bodies alone would have blown the frozen effective budget.
        last = holder.seen[-1]
        settled = [m for m in last.messages if m.role.value == "tool" and m.name == "write_file"]
        assert len(settled) >= 20
        raw_total = sum(json.loads(m.content)["source"]["content_bytes"]
                        if m.metadata and m.metadata.get("source") == MARKER
                        else len(m.content.encode()) for m in settled)
        carried_last = sum(text_tokens(m.content) for m in settled)
        # Unsummarized these bodies alone exceed the frozen effective budget:
        # before the bound this exact Run failed closed (ContextBudgetExceeded)
        # or shipped an oversized payload.
        assert raw_total // 4 > budget, (raw_total, budget)
        assert carried_last <= ceiling, (carried_last, ceiling)

        # 2b) The one failed tool call settled, kept its own bytes, and did not
        #     abort the Run: a non-succeeded effect has no pageable public body.
        assert holder.failed == 1
        unpaged = [m for m in settled if not (m.metadata and m.metadata.get("source") == MARKER)]
        assert any('"outcome":"succeeded"' not in m.content for m in unpaged), unpaged[:1]

        # 3) Every real request stayed inside the budget, and this Run's own
        #    settled bodies stopped growing with the settled-result count.
        for sent in holder.seen:
            carried = sum(text_tokens(m.content) for m in sent.messages
                          if m.role.value == "tool" and m.name not in CONTROL_TOOLS)
            total = sum(text_tokens(m.content) for m in sent.messages if isinstance(m.content, str))
            assert total <= budget, (total, budget)
            assert carried <= ceiling, (carried, ceiling, allowance)

        # 4) The snapshot receipt attributes the paging (A6-3 / A6-4).
        async with aiosqlite.connect(state) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT source_revisions_json FROM run_context_snapshot_receipts "
                "WHERE sdk_run_id=? ORDER BY provider_turn_ordinal", (holder.first_run,))).fetchall()
        revisions = [json.loads(row["source_revisions_json"]) for row in rows]
        revisions = [r.get("source_revisions", r) for r in revisions]
        assert revisions and all({"current_tool_pages", "current_tool_tokens"} <= set(r) for r in revisions)
        assert max(r["current_tool_pages"] for r in revisions) >= 20
        assert max(r["current_tool_tokens"] for r in revisions) <= ceiling
        assert max(r["current_tool_tokens"] for r in revisions) > allowance // 2
    finally:
        await holder.runtime.close()
        await holder.stack.close()
        await client.aclose()

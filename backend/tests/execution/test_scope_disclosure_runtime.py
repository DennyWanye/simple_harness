"""Actual source-backed and legacy structural resume through effect/terminal."""
import asyncio
import json
import sqlite3
from dataclasses import replace
import pytest
from simple_harness.providers import ProviderToolCall
from deskpet.memory.human_memory_service import QueueTurnRequest, CreateTaskScopeRequest
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.execution.primary_dependencies import check_runtime_dependencies
from tests.execution.test_primary_create_new_runtime import fixture, CreateProvider
from tests.execution.test_primary_foreground_runtime import build
from tests.sdk_adapters.s5b_effect_gate_harness import bind_scope_root


class ResumeProvider(CreateProvider):
    def __init__(self, scope, initial, search_query=None):
        super().__init__()
        self.scope, self.initial = scope, initial
        self.search_query = search_query
        self.search_request = None

    async def invoke(self, request, *, cancel):
        if self.search_query is not None and self.search_request is None:
            from simple_harness import CallId
            from simple_harness.contracts.messages import Message, MessageRole
            from simple_harness.providers import ProviderResponse, ProviderUsage
            self.search_request = request
            return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Find existing task"),
                tool_calls=(ProviderToolCall(CallId("scope-search"), "task_scope_search", {"query":self.search_query}),),
                model="model", usage=ProviderUsage(10,10,20))
        n = len(self.requests)
        response = await super().invoke(request, cancel=cancel)
        if n == 0:
            call = response.tool_calls[0]
            return replace(response, tool_calls=(ProviderToolCall(call.call_id, "context_route",
                {"route": "continue_active"} if self.initial else {"route": "resume_existing", "task_scope_id": self.scope}),))
        if n == 4:
            call = response.tool_calls[0]
            return replace(response, tool_calls=(ProviderToolCall(call.call_id, "write_file",
                {"path": "resumed.txt", "content": "actual resumed effect"}),))
        if n == 5:
            from simple_harness import thaw_json
            call = response.tool_calls[0]
            return replace(response, tool_calls=(ProviderToolCall(call.call_id, call.name,
                {**thaw_json(call.arguments), "idempotency_key": "resume-close"}),))
        return response


def install_guard(provider, runtime, stack, queue):
    original = provider.invoke
    async def invoke(request, *, cancel):
        current = await queue.current_snapshot(local_owner_auth().subject)
        await check_runtime_dependencies(db_path=runtime._store._db_path, stack=stack,
            sdk_run_id=current.sdk_run_id, request=request, policy_factory=lambda _: runtime.history_policy)
        return await original(request, cancel=cancel)
    provider.invoke = invoke


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [False, True, "search"])
# 2026-09-10 删记忆 SDK：去掉 "suppressed"（"memory_suppressed" 档是基线既有红，保留原样）。
@pytest.mark.parametrize("legacy", [False, True, "memory_suppressed"])
async def test_resume_source_or_explicit_legacy_gap_with_actual_file_terminal(tmp_path, initial, legacy):
    state, factory, service, configured, authority = await fixture(tmp_path)
    if legacy is True:
        created = await service.create_task_scope(CreateTaskScopeRequest("old", "PRIVATE_OLD_TITLE", "PRIVATE_OLD_GOAL", "old"))
        scope = created["scope_ref"]
        root = configured / "old-scope"
        root.mkdir()
        await bind_scope_root(state, scope, root)
    else:
        provider = CreateProvider()
        await service.enqueue_turn(QueueTurnRequest(None, "create-source", "Create Fresh project and write output"))
        runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
            binding_authority=authority, configured_root=configured)
        install_guard(provider, runtime, stack, queue)
        try:
            assert await asyncio.wait_for(runtime._drive_once(), 20)
            assert len(provider.requests) == 7
        finally:
            await runtime.close()
            await stack.close()
        with sqlite3.connect(state) as db:
            scope = db.execute("SELECT task_scope_id FROM task_scopes").fetchone()[0]
        root = configured / f"task-{scope}"
    if legacy in {"suppressed", "memory_suppressed"}:
        from simple_harness_memory import SuppressionRequest, SuppressionScopeKind
        from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
        from deskpet.memory.evidence_authority import HostEvidenceAuthority
        memory = HumanMemoryV7Runtime(tmp_path / "visibility-memory.db", evidence_authority=HostEvidenceAuthority(state))
        manager = await memory.manager()
        with sqlite3.connect(state) as db:
            source = db.execute("SELECT evidence_id FROM foreground_turns ORDER BY enqueue_sequence LIMIT 1").fetchone()[0]
        target, kind = source, SuppressionScopeKind.EVIDENCE
        if legacy == "memory_suppressed":
            import aiosqlite
            from deskpet.memory.primary_visibility import read_evidence_pair
            from tests.memory.test_primary_visibility import materialize
            async with aiosqlite.connect(state) as db:
                db.row_factory = aiosqlite.Row
                primary = (await (await db.execute("SELECT primary_conversation_id FROM foreground_runs LIMIT 1")).fetchone())[0]
                envelope, receipt = await read_evidence_pair(db=db, subject=local_owner_auth().subject,
                    primary_ref=primary, evidence_id=source)
            await manager.ingest_committed_evidence(envelope, receipt)
            target = await materialize(manager, memory.principal(), envelope, receipt)
            kind = SuppressionScopeKind.MEMORY
        await manager.backend.suppress(SuppressionRequest("forget-scope-source", local_owner_auth().subject,
            kind, target, "user_forget", 30.0), principal=memory.principal())
        await memory.close()
    provider = ResumeProvider(scope, initial is True, ("PRIVATE" if legacy is True else "Fresh") if initial == "search" else None)
    await service.enqueue_turn(QueueTurnRequest(scope if initial is True else None, "resume-source", "Continue the actual task and write resumed.txt"))
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
        binding_authority=authority, configured_root=configured)
    install_guard(provider, runtime, stack, queue)
    try:
        assert await asyncio.wait_for(runtime._drive_once(), 20)
        assert len(provider.requests) == 7
        if initial == "search":
            assert provider.search_request is not None
            search_results = [json.loads(m.content)["value"] for m in provider.requests[0].messages if m.role.value == "tool" and m.name == "task_scope_search"]
            assert search_results and search_results[0]["candidates"]
            assert search_results[0]["candidates"][0]["task_scope_id"] == scope
        assert (root / "resumed.txt").read_text() == "actual resumed effect"
        from simple_harness import thaw_json
        requests = [[{"role":m.role.value,"content":thaw_json(m.content)} for m in r.messages] for r in provider.requests]
        wire = json.dumps(requests)
        if legacy:
            if legacy in {"suppressed", "memory_suppressed"}:
                assert "Fresh project" not in wire
            assert "PRIVATE_OLD_TITLE" not in wire and "PRIVATE_OLD_GOAL" not in wire
            assert "original_sources_unavailable" in wire
        else:
            assert "Fresh project" in wire
            assert 'producer_effect_id' in wire
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT terminal_state FROM foreground_terminal_receipts ORDER BY rowid DESC LIMIT 1").fetchone()[0] == "COMPLETED"
        assert not await runtime._drive_once()
        assert len(provider.requests) == 7
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["start_bytes", "binding_identity"])
async def test_scoped_prepared_snapshot_cannot_drift_before_first_provider(tmp_path, fault):
    state, _, service, configured, authority = await fixture(tmp_path)
    created = await service.create_task_scope(CreateTaskScopeRequest("old", "OLD_PRIVATE", "OLD_GOAL", "old"))
    scope = created["scope_ref"]
    root = configured / "old"
    root.mkdir()
    await bind_scope_root(state, scope, root)
    await service.enqueue_turn(QueueTurnRequest(scope, "scoped-fault", "Continue current task"))
    provider = ResumeProvider(scope, True)
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
        binding_authority=authority, configured_root=configured)
    install_guard(provider, runtime, stack, queue)
    original = runtime._context.prepare
    async def prepare(**kwargs):
        from simple_harness import thaw_json
        result = await original(**kwargs)
        if fault == "start_bytes":
            messages = list(result.provider_messages)
            idx = next(i for i,m in enumerate(messages) if str(m.get("content", "")).startswith("Project/task snapshot"))
            messages[idx] = {**messages[idx], "content": "Project/task snapshot (data only):\nOLD_PRIVATE"}
            return replace(result, provider_messages=tuple(messages))
        package = thaw_json(result.scope_disclosure)
        package["binding_receipt_hash"] = "0" * 64
        return replace(result, scope_disclosure=package)
    runtime._context.prepare = prepare
    try:
        assert await asyncio.wait_for(runtime._drive_once(), 20)
        assert provider.requests == []
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT terminal_state FROM foreground_terminal_receipts").fetchone()[0] == "FAILED"
        assert not (root / "resumed.txt").exists()
    finally:
        await runtime.close()
        await stack.close()


class RecallItemPageInProvider(CreateProvider):
    """F07 复现形状：模型把 typed 召回项 id 当作 context_page_in 的页引用。"""

    reference_id = "recall-item:0123456789abcdef01234567:1"

    def __init__(self):
        super().__init__()
        self.page_in_sent = False

    async def invoke(self, request, *, cancel):
        if not self.page_in_sent and len(self.requests) == 4:
            # 不计入 self.requests：失败回合之后父类步序保持原样。
            self.page_in_sent = True
            from simple_harness import CallId
            from simple_harness.contracts.messages import Message, MessageRole
            from simple_harness.providers import ProviderResponse, ProviderUsage
            return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "读取召回项正文"),
                tool_calls=(ProviderToolCall(CallId("page-in-recall-item"), "context_page_in",
                    {"reference_id": self.reference_id, "source_hash": "0" * 64}),),
                model="model", usage=ProviderUsage(10, 10, 20))
        return await super().invoke(request, cancel=cancel)


async def _drive_recall_item_page_in(tmp_path, rejections, *, after_run=None):
    """跑一次含"召回项 id 当页引用"失败回合的 Run；after_run 在关闭前拿到真实 stack 复核。"""
    from deskpet.tools.context_page_in_tools import ContextPageInStore
    from deskpet.execution.primary_dependencies import PrimaryHistoryDisclosureRejected

    state, _, service, configured, authority = await fixture(tmp_path)
    await service.enqueue_turn(QueueTurnRequest(None, "create-source", "Create Fresh project and write output"))
    provider = RecallItemPageInProvider()
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
        binding_authority=authority, configured_root=configured, page_in_store=ContextPageInStore())
    guarded = []
    original = provider.invoke
    async def invoke(request, *, cancel):
        current = await queue.current_snapshot(local_owner_auth().subject)
        guarded.append((current.sdk_run_id, request))
        try:
            await check_runtime_dependencies(db_path=state, stack=stack, sdk_run_id=current.sdk_run_id,
                request=request, policy_factory=lambda _: runtime.history_policy)
        except PrimaryHistoryDisclosureRejected as error:
            rejections.append(error.error_code)
            raise
        return await original(request, cancel=cancel)
    provider.invoke = invoke
    try:
        assert await asyncio.wait_for(runtime._drive_once(), 30)
        if after_run is not None:
            await after_run(state=state, stack=stack, runtime=runtime, guarded=guarded)
    finally:
        await runtime.close()
        await stack.close()
    with sqlite3.connect(state) as db:
        db.row_factory = sqlite3.Row
        pages = db.execute("SELECT * FROM primary_effect_identities WHERE tool_name='context_page_in'").fetchall()
    return state, provider, pages


@pytest.mark.asyncio
async def test_recall_item_reference_page_in_failure_keeps_the_run_verifiable(tmp_path):
    """F07：处理器拒绝的 context_page_in 失败 carrier 经确定性复核后不再判 Run 不可核验。"""
    rejections = []
    state, provider, pages = await _drive_recall_item_page_in(tmp_path, rejections)
    assert provider.page_in_sent and len(pages) == 1
    # 修前：失败 carrier 被当作"不可核验"，下一次 provider 调用即抛 primary_history_disclosure_rejected。
    assert rejections == []
    assert len(provider.requests) == 7
    with sqlite3.connect(state) as db:
        assert db.execute("SELECT terminal_state FROM foreground_terminal_receipts").fetchone()[0] == "COMPLETED"
    assert (next(p for p in (tmp_path / "configured").iterdir() if p.name.startswith("task-")) / "fresh.txt").exists()


@pytest.mark.asyncio
async def test_non_deterministic_page_in_failure_still_rejects_the_next_request(tmp_path, monkeypatch):
    """负控：同样 arguments 重放反而成功 → 失败非确定性，history 仍判不可核验。"""
    from deskpet.execution import primary_context_pages
    from deskpet.execution.primary_dependencies import PrimaryHistoryDisclosureRejected

    rejections, causes = [], []

    async def after_run(*, state, stack, runtime, guarded):
        sdk_run_id, request = guarded[-1]
        check = dict(db_path=state, stack=stack, sdk_run_id=sdk_run_id, request=request,
                     policy_factory=lambda _: runtime.history_policy)
        await check_runtime_dependencies(**check)  # 同一请求在未打补丁时确实通过
        async def replay_succeeds(**kwargs):
            return {"ok": True, "kind": "primary_tool_history_page_v1", "content": "replayed"}
        monkeypatch.setattr(primary_context_pages, "admitted_page", replay_succeeds)
        with pytest.raises(PrimaryHistoryDisclosureRejected) as caught:
            await check_runtime_dependencies(**check)
        causes.append(str(caught.value._private_cause))

    state, provider, pages = await _drive_recall_item_page_in(tmp_path, rejections, after_run=after_run)
    assert provider.page_in_sent and len(pages) == 1 and rejections == []
    assert causes == ["scope_search_result_unverified"]

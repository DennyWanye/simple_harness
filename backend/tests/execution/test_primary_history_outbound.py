"""Real SQLite/Harness ledger at the production preflight/transport boundary."""
import asyncio
import sqlite3
import aiosqlite
import httpx
import pytest

from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory, QueueTurnRequest
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.sdk_adapters.provider import ProductProviderAdapter
from deskpet.execution.primary_dependencies import check_runtime_dependencies
from tests.sdk_adapters.test_product_host_ports import Registry
from tests.execution.test_primary_foreground_runtime import build


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["late_suppression", "missing_proof", "wrong_hash", "sent_unknown"])
async def test_late_history_denial_is_failed_while_sent_ambiguity_stays_unknown(tmp_path, mode):
    late_deny = mode != "sent_unknown"
    from simple_harness import RunId, RequestId
    from simple_harness.execution.provider_invocations import provider_invocation_id
    from simple_harness_memory import SuppressionRequest, SuppressionScopeKind
    state = tmp_path / "state.db"
    service = HumanMemoryHostServiceFactory(state,
        await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    await service.open_primary()
    await service.enqueue_turn(QueueTurnRequest(None, "outbound", "Current actual user"))
    with sqlite3.connect(state) as db:
        evidence_id = db.execute("SELECT evidence_id FROM foreground_turns").fetchone()[0]
    sends, requests = [], []
    def physical(request):
        sends.append(request)
        raise RuntimeError("ambiguous handoff after physical transport entry")
    client = httpx.AsyncClient(transport=httpx.MockTransport(physical))
    provider = ProductProviderAdapter(Registry("secret"), provider_id="relay", client=client,
        price_resolver=lambda *_: (1, 1, "price-v1"))
    runtime, stack, queue = await build(tmp_path, state, provider)
    if mode in {"missing_proof", "wrong_hash"}:
        from dataclasses import replace
        from simple_harness import thaw_json
        original_prepare = runtime._context.prepare
        async def prepare(**kwargs):
            actual = await original_prepare(**kwargs)
            proof = thaw_json(actual.visibility_dependencies)
            if mode == "wrong_hash":
                proof["evidence"][0]["envelope_hash"] = "0" * 64
            return replace(actual, visibility_dependencies=None if mode == "missing_proof" else proof)
        runtime._context.prepare = prepare
    async def guard(request):
        current = await queue.current_snapshot(local_owner_auth().subject)
        requests.append((current.sdk_run_id, request.request_id.value))
        if mode == "late_suppression":
            manager = await runtime.history_memory.manager()
            await manager.backend.suppress(SuppressionRequest("late-forget", local_owner_auth().subject,
                SuppressionScopeKind.EVIDENCE, evidence_id, "user_forget", 20.0),
                principal=runtime.history_memory.principal())
        await check_runtime_dependencies(db_path=state, stack=stack, sdk_run_id=current.sdk_run_id,
            request=request, policy_factory=lambda subject: runtime.history_policy)
    provider._pre_invoke_guard = guard
    try:
        progressed = await asyncio.wait_for(runtime._drive_once(), 15)
        assert progressed is late_deny
        assert len(requests) == 1
        sdk_run_id, request_id = requests[0]
        record = stack._uow.read_provider_invocation(provider_invocation_id(RunId(sdk_run_id), RequestId(request_id)))
        assert record.state.value == ("failed" if late_deny else "unknown")
        assert len(sends) == (0 if late_deny else 1)
        if late_deny:
            assert record.error_code == "primary_history_disclosure_rejected"
            assert not await runtime._drive_once()
            assert len(requests) == 1 and sends == []
    finally:
        await runtime.close()
        await stack.close()
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("corrupt", [None, "missing", "public_payload_hash", "short", "late_forget", "unselected_short", "short_bytes", "short_missing_sources"])
async def test_new_recall_four_tuple_checked_before_next_physical_provider(tmp_path, corrupt, monkeypatch):
    """Real USER -> analysis -> typed recall -> production route -> next invoke."""
    import json
    import time
    from types import SimpleNamespace
    from tests.execution.test_primary_foreground_runtime import Provider
    from tests.sdk_adapters import s5b_memory_harness as mh, s5b_closure_harness as ch
    from deskpet.memory import human_memory_v7
    state = tmp_path / "state.db"
    service = HumanMemoryHostServiceFactory(state,
        await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    await service.open_primary()
    await service.enqueue_turn(QueueTurnRequest(None, "source", "README 版本号改成 1.2.0"))
    runtime, stack, _ = await build(tmp_path, state, Provider())
    assert await runtime._drive_once()
    with sqlite3.connect(state) as db:
        sdk_run = db.execute("SELECT sdk_run_id FROM foreground_run_sdk_bindings").fetchone()[0]
    binding = stack.read_closure_run_facts(sdk_run).binding_record
    await runtime.close()
    await stack.close()
    analysis = ch.FakeAdapter([mh.proposal_call([mh.semantic_op("source", "版本号改成 1.2.0")])])
    menv = mh.memory_env(SimpleNamespace(db_path=state, clock=time.time), analysis,
        subject=local_owner_auth().subject, production_builder=True,
        memory_db=tmp_path / "visibility-memory.db", binding=binding, endpoint=None)
    assert await menv.worker.run_once() == "delivered"
    assert await mh.run_job(menv) == "applied"
    executions = []
    async def recall(**kwargs):
        lanes = await menv.runtime.typed_recall(**kwargs)
        assert lanes.execution.result.items
        executions.append(lanes.execution)
        return lanes
    real_projection = human_memory_v7.project_recall_fragments
    def projection(lanes):
        rows = [dict(row) for row in real_projection(lanes)]
        item = lanes.execution.result.items[0]
        assert rows[0]["history_binding"] == {
            "result_id": lanes.execution.result.result_id,
            "result_hash": lanes.execution.result.result_hash,
            "item_id": item.selected_item.item_id, "item_hash": item.result_item_hash}
        assert item.result_item_hash != item.selected_item.public_payload_hash
        if corrupt == "missing":
            rows[0].pop("history_binding")
        elif corrupt == "public_payload_hash":
            rows[0]["history_binding"] = {**rows[0]["history_binding"], "item_hash": rows[0]["payload_hash"]}
        elif corrupt == "short":
            rows[0]["lane"] = "short_horizon"
            rows[0].pop("history_binding")
        elif corrupt in {"unselected_short", "short_bytes", "short_missing_sources"}:
            import hashlib
            payload = "short fixture has no actual selected audit"
            digest = hashlib.sha256(payload.encode()).hexdigest()
            with sqlite3.connect(state) as db:
                source = db.execute("SELECT evidence_id,evidence_hash FROM foreground_turns ORDER BY enqueue_sequence LIMIT 1").fetchone()
            rows[0].update(lane="short_horizon", payload=payload, payload_hash=digest,
                history_binding={"audit_id":"unselected-audit", "chunk_ref":rows[0]["ref"], "content_hash":digest},
                history_source_dependencies={"schema_version":2,"evidence":[{"evidence_id":source[0],"envelope_hash":source[1]}],"recall":[],"short_horizon":[]})
            if corrupt == "short_bytes":
                rows[0]["payload"] += " altered"
            if corrupt == "short_missing_sources":
                rows[0].pop("history_source_dependencies")
        return tuple(rows)
    monkeypatch.setattr(human_memory_v7, "project_recall_fragments", projection)
    sends, requests = [], []
    def physical(request):
        sends.append(request)
        message = ({"role": "assistant", "content": None, "tool_calls": [{"id": "actual-recall-call",
            "type": "function", "function": {"name": "context_route", "arguments": json.dumps(
                {"route": "memory_standalone", "query": "README", "memory_types": ["semantic", "episode", "procedure"]})}}]} if len(sends) == 1
            else {"role": "assistant", "content": "Actual recalled answer"})
        return httpx.Response(200, json={"id": f"p-{len(sends)}", "model": "model-a", "choices": [
            {"message": message, "finish_reason": "tool_calls" if len(sends) == 1 else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(physical))
    provider = ProductProviderAdapter(Registry("secret"), provider_id="relay", client=client,
        price_resolver=lambda *_: (1, 1, "price-v1"))
    await service.enqueue_turn(QueueTurnRequest(None, "recall", "What README version?"))
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
        visibility_memory=menv.runtime, recall_executor=recall)
    async def guard(request):
        current = await queue.current_snapshot(local_owner_auth().subject)
        requests.append(request)
        if corrupt == "late_forget" and len(requests) == 2:
            from deskpet.memory.human_memory_api import handle_human_memory_command

            primary = (await service.open_primary())["primary_ref"]
            factory = HumanMemoryHostServiceFactory(
                state, service.startup_decision, cognitive_runtime_getter=lambda: menv.runtime
            )

            async def command(operation, fields):
                return await handle_human_memory_command(
                    {"type": "human_memory_request", "request_id": "typed-forget-test",
                     "operation": operation, "request": {"primary_ref": primary, **fields}},
                    factory=factory, auth=local_owner_auth(),
                )

            listing = await command("primary.memory.list", {})
            assert listing["payload"]["ok"], listing
            memory_id = executions[0].result.items[0].selected_item.source_ref
            target, = [item for item in listing["payload"]["result"]["items"] if item["memory_id"] == memory_id]
            forgotten = await command("primary.memory.forget", {
                "action_id": "forget-after-real-selection", "memory_id": memory_id,
                "expected_revision": target["revision"], "expected_content_hash": target["content_hash"],
            })
            assert forgotten["payload"]["ok"], forgotten
            assert forgotten["payload"]["result"]["status"] == "applied"
        await check_runtime_dependencies(db_path=state, stack=stack, sdk_run_id=current.sdk_run_id,
            request=request, policy_factory=lambda subject: runtime.history_policy)
    provider._pre_invoke_guard = guard
    try:
        assert await asyncio.wait_for(runtime._drive_once(), 20)
        assert len(executions) == 1 and len(requests) == 2
        assert len(sends) == (2 if corrupt is None else 1)
        if corrupt is None:
            assert "1.2.0" in sends[1].content.decode()
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT terminal_state FROM foreground_terminal_receipts ORDER BY rowid DESC LIMIT 1").fetchone()[0] == ("COMPLETED" if corrupt is None else "FAILED")
    finally:
        await runtime.close()
        await stack.close()
        await mh.close(menv)
        await client.aclose()


# --------------------------------------------------------------------------
# 2026-09-08 HM-TO-A6 根因侧：工具密集的一轮产生了 74196 字节的终态观察，越过
# Memory 的 64 KiB 内联上限。Host 收下了它，Memory 之后每一次读都拒绝，整条
# 主对话不可读、前台驱动永死。写入点必须先用 SDK 自己的受理规则校验，并作
# 确定性降级，让这一轮的终态观察既可受理、又不会被悄悄丢掉。
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oversized_transcript_degrades_instead_of_poisoning_memory(tmp_path, caplog):
    """A real turn over the real 64 KiB ceiling still settles admissibly.

    Drives the production path end to end (`ForegroundRuntimeExecutionAuthority`
    -> `record_terminal_observation`) with a transcript that genuinely exceeds
    the SDK's inline limit.  What must hold: the stored terminal observation
    passes the SDK's own admission rule, the elided body hashes back to the
    settled SDK transcript, the USER text is untouched, the turn is still
    readable history, and the next turn runs.
    """
    import logging

    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.providers import ProviderResponse, ProviderUsage

    from deskpet.execution.primary_history import (
        ELISION_PREFIX,
        PrimaryHistoryStore,
        elided_content,
        terminal_observation_tx,
    )
    from deskpet.memory.primary_visibility import (
        assert_source_admissible,
        inline_evidence_limit,
        read_evidence_pair,
    )
    from tests.execution.test_primary_foreground_runtime import (
        Provider,
        history_disclosure,
    )

    limit = inline_evidence_limit()
    body = "z" * (limit + 4096)  # this one message alone busts the whole budget

    class HugeProvider(Provider):
        async def invoke(self, request, *, cancel):
            self.requests.append(request)
            return ProviderResponse(
                request.request_id, Message(MessageRole.ASSISTANT, body),
                model="model", usage=ProviderUsage(10, 10, 20),
                opaque_continuation_ref="fixture-opaque")

    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    primary = (await service.open_primary())["primary_ref"]
    subject = local_owner_auth().subject
    runtime, stack, _ = await build(
        tmp_path, state, HugeProvider(), provider_context_window=1_000_000)
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "huge", "Actual oversized turn"))
        with caplog.at_level(logging.WARNING, logger="deskpet.execution.primary_history"):
            assert await asyncio.wait_for(runtime._drive_once(), 30)

        host_run_id, sdk_run_id = _run_ids(state)
        _, settled = stack.read_settled_primary_run(
            sdk_run_id, current_text="Actual oversized turn")
        settled = list(settled)
        async with aiosqlite.connect(state) as db:
            db.row_factory = aiosqlite.Row
            found = await terminal_observation_tx(
                db, host_run_id=host_run_id, sdk_run_id=sdk_run_id, subject=subject)
            assert found is not None
            row, payload = found
            envelope, receipt = await read_evidence_pair(
                db=db, subject=subject, primary_ref=primary,
                evidence_id=row["evidence_id"])

        # 1. Admissible by the SDK's own rule — the exact check that used to
        #    fail on every later read and take the whole conversation with it.
        assert_source_admissible(envelope, receipt)
        # 2. Only the oversized body moved, and its marker hashes back to what
        #    the SDK actually settled.
        stored = payload["messages"]
        assert [m["role"] for m in stored] == ["user", "assistant"]
        assert settled[1]["content"] == body  # the SDK transcript is untouched
        assert stored[0] == settled[0]
        assert stored[0]["content"] == "Actual oversized turn"  # USER text kept
        assert stored[1]["content"].startswith(ELISION_PREFIX)
        assert stored[1]["content"] == elided_content(body)
        # 3. Reported where it is produced, with no transcript content, and the
        #    degrade actually worked — no residual unadmissible warning.
        degraded = [r.getMessage() for r in caplog.records
                    if "primary_terminal_observation_degraded" in r.getMessage()]
        assert len(degraded) == 1 and "elided_ordinals=[2]" in degraded[0]
        assert "zzzz" not in degraded[0]
        assert not [r for r in caplog.records
                    if "primary_terminal_observation_unadmissible" in r.getMessage()]
        # 4. The turn is still real, readable history — not a lost turn.
        history = await PrimaryHistoryStore(state, policy=runtime.history_policy).read(
            subject=subject, primary_ref=primary,
            disclosure_context=history_disclosure(), before_sequence=2)
        assert len(history) == 1 and history[0]["messages"] == stored
        # 5. And the conversation keeps going.
        await service.enqueue_turn(QueueTurnRequest(None, "after", "Continue after oversize"))
        assert await asyncio.wait_for(runtime._drive_once(), 30)
    finally:
        await runtime.close()
        await stack.close()


def _run_ids(state):
    with sqlite3.connect(state) as db:
        return db.execute(
            "SELECT r.host_run_id,b.sdk_run_id FROM foreground_runs r "
            "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id "
            "ORDER BY r.rowid LIMIT 1"
        ).fetchone()


def test_transcript_degrade_prefers_tool_results_and_stays_verifiable():
    """The elision policy itself: tool bodies first, USER text never, replayable."""
    from deskpet.execution.primary_history import (
        bound_terminal_messages,
        elided_content,
        transcript_matches,
    )

    messages = [
        {"role": "user", "content": "u" * 400},
        {"role": "assistant", "content": "a" * 600},
        {"role": "tool", "content": "t" * 4000, "call_id": "c1", "name": "web_search"},
        {"role": "tool", "content": "s" * 9000, "call_id": "c2", "name": "file_read"},
        {"role": "assistant", "content": "b" * 500},
    ]
    # Budget reachable by giving up the two tool bodies alone.
    bounded, elided = bound_terminal_messages(messages, 2500)
    assert elided == (3, 4)  # largest tool first, then the other; 1-based
    assert bounded[0] == messages[0] and bounded[1] == messages[1]
    assert bounded[4] == messages[4]
    assert bounded[3]["content"] == elided_content(messages[3]["content"])
    assert bounded[3]["call_id"] == "c2" and bounded[3]["name"] == "file_read"
    assert transcript_matches(messages, bounded)
    # Pure and replayable: the same input reduces to exactly the same bytes.
    assert bound_terminal_messages(messages, 2500) == (bounded, elided)
    # Already-elided bodies are never elided twice.
    assert bound_terminal_messages(bounded, 2500)[1] == ()

    # Under budget: byte-for-byte identical, nothing recorded.
    assert bound_terminal_messages(messages, 1_000_000) == (messages, ())
    # A forged marker is not a match.
    forged = [dict(m) for m in bounded]
    forged[3] = {**forged[3], "content": elided_content("something else")}
    assert not transcript_matches(messages, forged)
    # Assistant bodies only once the tool bodies are not enough; USER never.
    tiny, tiny_elided = bound_terminal_messages(messages, 600)
    assert tiny_elided == (2, 3, 4, 5)
    assert tiny[0] == messages[0]


def test_terminal_payload_budget_is_exact_not_a_reserve():
    """A payload Memory would admit keeps every byte — paging reads those bytes.

    The first version of this degrade reserved worst-case room for `turn_id`
    and `tool_scope_sources`, and cut a transcript that actually fit; that
    broke `context_page_in` for large tool results
    (`test_primary_context_pages::test_actual_history_page_and_physical_guard`).
    The budget is derived from the real canonical size instead.
    """
    from deskpet.execution.primary_history import bound_terminal_payload
    from deskpet.memory.primary_visibility import inline_evidence_limit
    from deskpet.task_scope.protocol import canonical_json

    limit = inline_evidence_limit()

    def payload(body):
        return {
            "schema_version": 1, "kind": "primary_run_terminal",
            "host_run_id": "h", "sdk_run_id": "s", "turn_id": "t" * 64,
            "tool_causal_sources": [{"item_ordinal": 3, "effect_id": "e" * 40}] * 8,
            "messages": [
                {"role": "user", "content": "actual user turn"},
                {"role": "assistant", "content": "answering"},
                {"role": "tool", "content": body, "call_id": "c1", "name": "read"},
            ],
        }

    # Comfortably under the ceiling once the rest of the payload is counted:
    # untouched, byte for byte.
    fits = payload("L" * (limit - 4096))
    assert bound_terminal_payload(fits) == (fits, ())
    assert len(canonical_json(fits).encode("utf-8")) <= limit

    over = payload("L" * (limit + 1))
    bounded, elided = bound_terminal_payload(over)
    assert elided == (3,)
    assert len(canonical_json(bounded).encode("utf-8")) <= limit
    assert bounded["messages"][0] == over["messages"][0]
    assert {k: v for k, v in bounded.items() if k != "messages"} == {
        k: v for k, v in over.items() if k != "messages"}

    # When the *rest* of the payload is what overflows, giving up transcript
    # bodies buys nothing — so it gives up none of them.
    hopeless = payload("L" * 32)
    hopeless["tool_scope_sources"] = [{"item_ordinal": i, "blob": "S" * 64}
                                      for i in range(limit // 64)]
    assert len(canonical_json(hopeless).encode("utf-8")) > limit
    assert bound_terminal_payload(hopeless) == (hopeless, ())


def test_residual_unadmissible_observation_is_reported_at_its_source(caplog):
    """Fail-safe: a payload the degrade cannot fix is still written, but loudly.

    The transcript is not the only thing that can overflow (a very large
    `tool_scope_sources`, a credential-boundary key). Refusing to commit would
    leave the Run unsettled forever, so the row is written and the write point
    reports it with the SDK's own stable reason — identifiers and byte counts
    only, never transcript content.
    """
    import logging

    from deskpet.execution.primary_history import _warn_if_unadmissible, evidence_pair
    from deskpet.sdk_adapters.context_route import local_owner_auth

    subject = local_owner_auth().subject
    good, good_receipt = evidence_pair(
        subject, "sdk-ok", {"kind": "primary_run_terminal", "messages": []}, 1.0)
    with caplog.at_level(logging.WARNING, logger="deskpet.execution.primary_history"):
        _warn_if_unadmissible("sdk-ok", good, good_receipt)
    assert caplog.records == []

    # A credential-boundary key: rejected by the SDK on every read, and not
    # something the transcript degrade can repair.
    doomed, doomed_receipt = evidence_pair(
        subject, "sdk-doomed",
        {"kind": "primary_run_terminal", "authorization": "public-looking",
         "messages": [{"role": "user", "content": "SECRET_TRANSCRIPT_CANARY"}]},
        1.0)
    with caplog.at_level(logging.WARNING, logger="deskpet.execution.primary_history"):
        _warn_if_unadmissible("sdk-doomed", doomed, doomed_receipt)
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 1
    assert "primary_terminal_observation_unadmissible" in messages[0]
    assert "reason=evidence_credential_boundary_rejected" in messages[0]
    assert "message_count=1" in messages[0]
    assert "SECRET_TRANSCRIPT_CANARY" not in messages[0]


# --------------------------------------------------------------------------
# 2026-09-08 HM-TO-A6 读侧：省略过的终态观察必须仍然可读。
#
# b3682fe1 把写侧降级和 `transcript_matches` 一起引入，但只把两个校验点
# （primary_history.py 幂等分支、primary_context_pages）切了过去，历史读取器
# 里「从结算 Run 重建分组」的那一处仍然逐字节相等。真实 HM-TO-A6 native 跑里，
# 第一轮读了一个 40 KB 文件、终态观察被省略，下一轮历史读取就抛
# primary_history_transcript_mismatch 四次、前台驱动停摆
# （.local-test-evidence/2026-09-08/native-a6-b3682fe1/primary-ui-xmqudtzt/native.log
#  05:19:50Z 前后）。933df61e 修的就是那一处。
#
# 关键：测试 build() 里的 PrimaryForegroundContextPort **没有**接
# settled_run_reader，而 main.py 的产线装配接了——所以 b3682fe1 自带的端到端
# 用例读历史时根本没走到那个校验点。本用例按产线装配显式接上。
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_elided_tool_result_history_still_reads_against_settled_sdk_run(tmp_path, monkeypatch):
    """The production history read (with a settled-run reader) survives elision.

    A real turn calls a real tool and then answers past the SDK inline
    ceiling, so `record_terminal_observation` elides the transcript bodies.
    Reading that turn back the way `main.py` wires it — `PrimaryHistoryStore`
    with `settled_run_reader`, which re-reads the live SDK transcript and
    compares it against the archived `payload["messages"]` — must succeed and
    hand back the recorded markers, not raise
    `primary_history_transcript_mismatch`.
    """
    from simple_harness import CallId
    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage

    from deskpet.execution import primary_history as primary_history_module
    from deskpet.execution.primary_history import (
        ELISION_PREFIX,
        PrimaryHistoryStore,
        elided_content,
        terminal_observation_tx,
        transcript_matches,
    )
    from deskpet.memory.primary_visibility import inline_evidence_limit
    from tests.execution.test_primary_foreground_runtime import Provider, history_disclosure

    limit = inline_evidence_limit()
    body = "z" * (limit + 4096)  # the answer alone busts the whole budget

    class ToolThenHugeProvider(Provider):
        async def invoke(self, request, *, cancel):
            self.requests.append(request)
            if len(self.requests) == 1:
                return ProviderResponse(
                    request.request_id, Message(MessageRole.ASSISTANT, "Reading the actual file"),
                    tool_calls=(ProviderToolCall(CallId("actual-a6-search"), "tool_search", {}),),
                    model="model", usage=ProviderUsage(10, 10, 20),
                    opaque_continuation_ref="fixture-opaque")
            return ProviderResponse(
                request.request_id, Message(MessageRole.ASSISTANT, body),
                model="model", usage=ProviderUsage(10, 10, 20),
                opaque_continuation_ref="fixture-opaque")

    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    primary = (await service.open_primary())["primary_ref"]
    subject = local_owner_auth().subject
    runtime, stack, _ = await build(
        tmp_path, state, ToolThenHugeProvider(), provider_context_window=1_000_000)
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "a6", "Actual oversized tool turn"))
        assert await asyncio.wait_for(runtime._drive_once(), 30)

        host_run_id, sdk_run_id = _run_ids(state)
        _, settled = stack.read_settled_primary_run(
            sdk_run_id, current_text="Actual oversized tool turn")
        settled = list(settled)
        assert [m["role"] for m in settled] == ["user", "assistant", "tool", "assistant"]
        assert settled[2]["name"] == "tool_search"
        assert settled[3]["content"] == body  # the live SDK transcript is intact

        async with aiosqlite.connect(state) as db:
            db.row_factory = aiosqlite.Row
            _, payload = await terminal_observation_tx(
                db, host_run_id=host_run_id, sdk_run_id=sdk_run_id, subject=subject)
        stored = payload["messages"]
        # The archive really did lose bodies: strict equality is false here,
        # which is exactly what stalled the A6 driver.
        assert stored != settled
        assert transcript_matches(settled, stored)
        assert stored[0] == settled[0] and stored[1] == settled[1]
        assert stored[2]["content"] == elided_content(settled[2]["content"])
        assert stored[2]["name"] == "tool_search" and stored[2]["call_id"] == settled[2]["call_id"]
        assert stored[3]["content"] == elided_content(body)

        # The production wiring (main.py passes settled_run_reader; the test
        # `build()` does not, which is why this path had no coverage).
        history = await PrimaryHistoryStore(
            state, policy=runtime.history_policy,
            settled_run_reader=stack.read_settled_primary_run,
        ).read(subject=subject, primary_ref=primary,
               disclosure_context=history_disclosure(), before_sequence=2)
        assert len(history) == 1
        assert history[0]["messages"] == stored
        assert history[0]["terminal_state"] == "COMPLETED"
        markers = [m["content"] for m in history[0]["messages"]
                   if str(m["content"]).startswith(ELISION_PREFIX)]
        assert len(markers) == 2 and f"bytes={len(body.encode())}" in markers[1]

        # Load-bearing: with the pre-933df61e strict comparison back in place,
        # this same read raises the code that stalled the foreground driver.
        monkeypatch.setattr(primary_history_module, "transcript_matches",
                            lambda settled, stored: list(settled) == list(stored))
        with pytest.raises(RuntimeError, match="primary_history_transcript_mismatch"):
            await PrimaryHistoryStore(
                state, policy=runtime.history_policy,
                settled_run_reader=stack.read_settled_primary_run,
            ).read(subject=subject, primary_ref=primary,
                   disclosure_context=history_disclosure(), before_sequence=2)
    finally:
        await runtime.close()
        await stack.close()

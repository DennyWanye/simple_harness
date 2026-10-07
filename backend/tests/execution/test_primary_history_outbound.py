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
# 2026-09-10 删记忆 SDK：去掉 "late_suppression"。
@pytest.mark.parametrize("mode", ["missing_proof", "wrong_hash", "sent_unknown"])
async def test_late_history_denial_is_failed_while_sent_ambiguity_stays_unknown(tmp_path, mode):
    late_deny = mode != "sent_unknown"
    from simple_harness import RunId, RequestId
    from simple_harness.execution.provider_invocations import provider_invocation_id
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
        # sent_unknown：2026-09-08 F06（提交 4601610a2）起，发出后结果不明的调用不再让
        # 前台停在 waiting——Host 对同一 request 授权重发一次，二次仍不明就 cancel 收尾。
        # 所以本轮有进展、同一 request 物理发送 2 次、前台终态 CANCELLED；
        # 但"已发出的不明"仍须记 unknown，绝不能被改写成 failed（本用例原意）。
        assert progressed is True
        assert len(requests) == (1 if late_deny else 2)
        assert len({item for item in requests}) == 1  # same SDK run + same request id
        sdk_run_id, request_id = requests[0]
        record = stack._uow.read_provider_invocation(provider_invocation_id(RunId(sdk_run_id), RequestId(request_id)))
        assert record.state.value == ("failed" if late_deny else "unknown")
        assert len(sends) == (0 if late_deny else 2)
        if late_deny:
            assert record.error_code == "primary_history_disclosure_rejected"
            assert not await runtime._drive_once()
            assert len(requests) == 1 and sends == []
        else:
            with sqlite3.connect(state) as db:
                assert db.execute(
                    "SELECT terminal_state FROM foreground_terminal_receipts"
                ).fetchall() == [("CANCELLED",)]
    finally:
        await runtime.close()
        await stack.close()
        await client.aclose()


# 2026-09-10 removed with the Memory SDK: test_new_recall_four_tuple_checked_before_next_physical_provider


# --------------------------------------------------------------------------
# 2026-09-08 HM-TO-A6 根因侧：工具密集的一轮产生了 74196 字节的终态观察，越过
# Memory 的 64 KiB 内联上限。Host 收下了它，Memory 之后每一次读都拒绝，整条
# 主对话不可读、前台驱动永死。写入点必须先用 SDK 自己的受理规则校验，并作
# 确定性降级，让这一轮的终态观察既可受理、又不会被悄悄丢掉。
# --------------------------------------------------------------------------


# 2026-09-10 removed with the Memory SDK: test_oversized_transcript_degrades_instead_of_poisoning_memory


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


# 2026-09-10 removed with the Memory SDK: test_terminal_payload_budget_is_exact_not_a_reserve


# 2026-09-10 removed with the Memory SDK: test_residual_unadmissible_observation_is_reported_at_its_source


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


# 2026-09-10 removed with the Memory SDK: test_elided_tool_result_history_still_reads_against_settled_sdk_run

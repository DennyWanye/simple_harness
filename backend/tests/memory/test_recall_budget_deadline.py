# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""前台 typed recall 的 deadline：生产装配出来的那个 `RecallBudget`。

2026-09-08 HM-TO-A6（`plans/2026-09-08-hm-to-a6/DIAG-RECALL-TIMEOUT.md`）：
`human_memory_v7.py` 的前台预算是 `RecallBudget(8, 16_384, 2_048, 1_000)`。
实测 DB 侧端到端只要 24–31 ms（§2.2），但查询向量嵌入热态 206–237 ms、
warmup 之后第一次 1033 ms（§2.3）——单次嵌入就能吃掉 20%–100% 的预算，
没有任何抗抖动余量。契约允许到 2000 ms（`S3-cognitive-systems-recall.md:216`
`deadline_ms=1..2000`、`:105`「p95≤500ms/hard deadline 2s」），协议上限同为
2000（`memory_protocol.py` 的 `RecallBudget.__post_init__`）。

用例断言的是**真实 `typed_recall` 装配出来的 context**，不是源码常量、也不是
函数默认值（S5b 评审 F-05 的教训）。
"""
import pytest
import pytest_asyncio

from deskpet.memory.runtime_composition import compose_human_memory_runtime

# 实测口径（DIAG §2.2 / §2.3，本机 WeMM-Embedding-2B / mps）。
MEASURED_DB_SIDE_MS = 31.0          # 离线重放端到端最坏 30.5 ms
MEASURED_QUERY_EMBED_MS = 1_033.0   # warmup 之后第一次查询嵌入
PROTOCOL_MAX_DEADLINE_MS = 2_000


class _Captured(Exception):
    """捕获点：拿到 context 即停，不需要真的跑一次召回。"""


@pytest_asyncio.fixture
async def captured(tmp_path):
    runtime = compose_human_memory_runtime(
        tmp_path / "state.db", tmp_path / "memory.db",
        adapter_factory=lambda *_: pytest.fail("no analysis transport in this test"))
    seen = {}

    async def capture(manager, *, principal, context, plan, now, caller):
        seen["context"], seen["plan"], seen["caller"] = context, plan, caller
        raise _Captured

    runtime.operation_audit.execute_typed_recall = capture
    try:
        with pytest.raises(_Captured):
            await runtime.typed_recall(query="校对脚本 Python 版本",
                                       run_id="budget-probe", turn_ordinal=1)
        yield seen
    finally:
        await runtime.close()


def test_foreground_budget_uses_the_protocol_maximum_deadline(captured):
    budget = captured["context"].budget
    assert captured["caller"] == "foreground_recall"
    assert budget.deadline_ms == PROTOCOL_MAX_DEADLINE_MS
    # plan 与 context 必须是同一份预算，否则协议校验拿到的不是这里断言的值。
    assert captured["plan"].budget == budget
    # 其余维度不动：本次只放宽时间，不放宽可见内容的体量。
    assert (budget.max_items, budget.max_bytes, budget.max_tokens) == (8, 16_384, 2_048)


def test_deadline_covers_the_measured_cost_with_margin(captured):
    budget = captured["context"].budget
    measured = MEASURED_DB_SIDE_MS + MEASURED_QUERY_EMBED_MS
    assert budget.deadline_ms >= measured, (
        f"deadline={budget.deadline_ms}ms 装不下实测最坏前台成本 {measured:.0f}ms "
        f"（DB 侧 {MEASURED_DB_SIDE_MS}ms + 查询嵌入 {MEASURED_QUERY_EMBED_MS}ms）"
    )
    # 抬到协议上限就没有下一格了：实测再涨只能靠 SDK 把嵌入移出写锁
    # （0.6.27）与批处理化，不能再靠加预算。
    assert budget.deadline_ms <= PROTOCOL_MAX_DEADLINE_MS


def test_budget_is_accepted_by_the_installed_protocol():
    from simple_harness.runtime import RecallBudget

    RecallBudget(8, 16_384, 2_048, PROTOCOL_MAX_DEADLINE_MS)
    with pytest.raises(ValueError, match="deadline_ms exceeds protocol maximum"):
        RecallBudget(8, 16_384, 2_048, PROTOCOL_MAX_DEADLINE_MS + 1)

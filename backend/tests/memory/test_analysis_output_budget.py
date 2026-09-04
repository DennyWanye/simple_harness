"""analysis 输出预算必须容得下真实提案（acceptance A16）。

实测：8 轮 output_tokens 为 457/908/1111/1381/1561/1939 时记忆全部物化；
两次失败轮**恰好顶格 2048** —— 即被 max_output_tokens 截断，提案发不完整。
成功上界 1939 距旧上限仅剩 109 token，余量不足。
"""

from __future__ import annotations

import inspect

from deskpet.memory import memory_ingestion_outbox as mio

# 真实观测到的、能成功物化的最大输出量（.local-test-evidence/real-ui-channel/20260904T131322）。
OBSERVED_SUCCESS_CEILING = 1939
# 两次失败轮的顶格值 —— 预算必须显著高于它，否则同样的提案还会被截断。
OBSERVED_TRUNCATION_POINT = 2048


def _budget() -> int:
    sig = inspect.signature(mio.build_worker_config)
    return int(sig.parameters["max_output_tokens"].default)


def test_budget_clears_the_observed_truncation_point() -> None:
    assert _budget() > OBSERVED_TRUNCATION_POINT


def test_budget_leaves_real_headroom_above_the_success_ceiling() -> None:
    """不是凑一个刚好能过的数：要求至少 2 倍余量。"""
    assert _budget() >= OBSERVED_SUCCESS_CEILING * 2, (
        f"预算 {_budget()} 相对实测成功上界 {OBSERVED_SUCCESS_CEILING} 余量不足——"
        "提案稍长就会重新落回截断"
    )


def test_budget_is_not_unbounded() -> None:
    """余量不是越大越好：预算同时是成本与延迟的闸门。"""
    assert _budget() <= OBSERVED_SUCCESS_CEILING * 8

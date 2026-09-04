"""analysis 输出预算与 deadline 必须一起成立（acceptance A16）。

实测：8 轮 output_tokens 为 457/908/1111/1381/1561/1939 时记忆全部物化；
两次失败轮**恰好顶格 2048** —— 被 max_output_tokens 截断，提案发不完整。

**预算不能单独调**（独立评审 F-01 的 P0）：同批数据线性拟合 latency ≈ 24.1ms/token，
只提预算不动 deadline 会让新预算的上半段物理上到不了，而一旦触及就从
「succeeded + no_mutation（可观测）」翻转成 sent_unknown → dead_letter，比截断更糟。

**断言的是真实装配出来的 config，不是函数默认值**（评审 F-05：前一版只读
`inspect.signature(...).default`，把生产调用点写死 2048 后用例照样全绿——
用例守护不了生产）。
"""

from __future__ import annotations

import pytest

from deskpet.memory.memory_ingestion_outbox import build_worker_config

# 真实观测到的、能成功物化的最大输出量（.local-test-evidence/real-ui-channel/20260904T131322）。
OBSERVED_SUCCESS_CEILING = 1939
# 两次失败轮的顶格值 —— 预算必须显著高于它。
OBSERVED_TRUNCATION_POINT = 2048
# 8 轮线性拟合：latency ≈ -797ms + 24.1ms/token。取保守端。
OBSERVED_MS_PER_TOKEN = 24.1
OBSERVED_INTERCEPT_MS = 0


@pytest.fixture()
def budget():
    """走生产装配路径取预算——生产调用点改了这里必须跟着变。"""
    cfg = build_worker_config(
        provider_id="primary", model_id="m", model_config_hash="a" * 64
    )
    return cfg.analysis_budget


def test_budget_clears_the_observed_truncation_point(budget) -> None:
    assert budget.max_output_tokens > OBSERVED_TRUNCATION_POINT


def test_budget_leaves_real_headroom_above_the_success_ceiling(budget) -> None:
    """不是凑一个刚好能过的数：要求至少 2 倍余量。"""
    assert budget.max_output_tokens >= OBSERVED_SUCCESS_CEILING * 2, (
        f"预算 {budget.max_output_tokens} 相对实测成功上界 "
        f"{OBSERVED_SUCCESS_CEILING} 余量不足——提案稍长就会落回截断"
    )


def test_deadline_can_actually_carry_the_budget(budget) -> None:
    """P0 守护：预算的上半段必须在 deadline 内真的跑得完。

    否则「够写」换成了「写不完就 dead_letter」，失败模式反而更糟。
    """
    need_ms = OBSERVED_INTERCEPT_MS + budget.max_output_tokens * OBSERVED_MS_PER_TOKEN
    assert need_ms <= budget.deadline_ms, (
        f"预算 {budget.max_output_tokens} token 按实测 {OBSERVED_MS_PER_TOKEN}ms/token "
        f"约需 {need_ms/1000:.0f}s，而 deadline 只有 {budget.deadline_ms/1000:.0f}s——"
        "超出部分会撞超时，失败模式从可观测的 no_mutation 翻转成 dead_letter"
    )


def test_deadline_is_not_padded_far_beyond_the_budget(budget) -> None:
    """deadline 也不能无脑放大：它同时是卡死回合的上限。"""
    need_ms = OBSERVED_INTERCEPT_MS + budget.max_output_tokens * OBSERVED_MS_PER_TOKEN
    assert budget.deadline_ms <= need_ms * 2


def test_lease_still_outlives_the_deadline(budget) -> None:
    """AC-2③：lease 必须大于 deadline 加余量，否则超时前租约先掉。"""
    cfg = build_worker_config(provider_id="primary", model_id="m", model_config_hash="a" * 64)
    assert cfg.lease_seconds > budget.deadline_ms / 1000

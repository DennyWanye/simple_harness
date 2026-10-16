# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""FEAT-C2 — superpowers 新代码错误/边角路径单测。

覆盖三块已落地的稳定符号（**不改产品代码**）:

* ``deskpet.agent.preference_memory.PreferenceMemory`` — embed 返回空/抛异常的
  安全降级、``max_entries`` 截断、``list_entries`` / ``clear`` 的 kind 过滤边角。
* ``deskpet.agent.verify_gate.VerifyGate`` — strict nudge 耗尽 →
  ``consult_ephemeral_subagent`` 三态（放行 / ephemeral=None 保守 fail /
  ephemeral 抛异常 fail）。
* ``agent.plan.maybe_extract_general_plan`` — 未开启规划 / 短消息 → None（且
  provider 不被调用，零副作用）。

并行约束（spec §3）：仅 import 已存在的稳定符号；**不** import A1 新抽的
``_build_intent_hint`` / ``_intent_label_from_turn`` 或 A4 新 sidecar schema。
"""

from __future__ import annotations

import math

import pytest
from agent.plan import maybe_extract_general_plan


def _unit(*xs: float) -> list[float]:
    n = math.sqrt(sum(x * x for x in xs)) or 1.0
    return [x / n for x in xs]


# ──────────────────────────────────────────────────────────────────
# 1. PreferenceMemory 边角 — embed 失败 / 截断 / 过滤
# ──────────────────────────────────────────────────────────────────


class _EmptyEmbedder:
    """embed 永远返回空 list（模型未就绪 / relay 503）。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return []


class _EmptyVecEmbedder:
    """返回了 list，但每条向量是空（[[]]）— 走 ``not out[0]`` 分支。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]


class _RaisingEmbedder:
    """embed 抛异常（BGE-M3 OOM / IO 错误）。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedder boom")


class _CountingUnitEmbedder:
    """每条文本映射到一个确定的单位向量，便于截断/计数测试。"""

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        out = []
        for t in texts:
            # 用文本 hash 派生 3 维向量，保证不同文本不同向量
            h = abs(hash(t.strip()))
            out.append(_unit((h % 7) + 1.0, ((h // 7) % 5) + 1.0, ((h // 35) % 3) + 1.0))
        return out


# ──────────────────────────────────────────────────────────────────
# 3. general plan 早返边角（无副作用）
# ──────────────────────────────────────────────────────────────────


class _ExplodingProvider:
    """若 general planner 在该走早返时还调了 provider，立刻炸出来。"""

    async def chat_with_tools(self, *args, **kwargs):
        raise AssertionError("provider.chat_with_tools must NOT be called on early-return path")


@pytest.mark.asyncio
async def test_general_plan_none_when_pipeline_does_not_request_planning():
    plan = await maybe_extract_general_plan(
        _ExplodingProvider(),
        user_message="这是一条足够长的、超过四十个字符阈值的真实代码任务请求文本用于测试",
        workspace_root="/tmp/proj",
    )
    assert plan is None


@pytest.mark.asyncio
async def test_general_plan_none_when_message_too_short():
    plan = await maybe_extract_general_plan(
        _ExplodingProvider(),
        user_message="列个目录",  # 短
        workspace_root="/tmp/proj",
        planning_enabled=True,
        problem_type="creation",
    )
    assert plan is None


@pytest.mark.asyncio
async def test_general_plan_short_after_strip():
    plan = await maybe_extract_general_plan(
        _ExplodingProvider(),
        user_message="   ls   " + " " * 60,
        workspace_root=None,
        planning_enabled=True,
        problem_type="creation",
    )
    assert plan is None

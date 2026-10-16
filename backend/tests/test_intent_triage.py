# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-1 单测 — IntentTriage（决策4：Step1+3 合并，一次 analyze() LLM 调用）。

覆盖（plans/2026-06-24-... §6 WI-1 + 05 L1）：
  - Y-light：chitchat 由预分析 LLM(deepseek) 裸判后短路（删早期规则短路修 BUG-B；现调 1 次 LLM）
  - 非闲聊只调 1 次 LLM（await_count==1，证明 Step1+3 合一）
  - 复杂问题一次调用同时出 intent + contradiction；简单 factual_qa 返回 contradiction=None
  - ambiguity≥阈值 → needs_clarification 澄清出口
  - prior_task_type → problem_type 映射
  - attack_order 解析
  - LLM 失败/超时/畸形 JSON → safe-fail 保守 card（contradiction=None）
"""

from __future__ import annotations

import json

import pytest


def _complex_payload() -> str:
    return json.dumps(
        {
            "restated_intent": "修复登录报错",
            "problem_type": "debug",
            "ambiguity_score": 0.2,
            "clarifying_questions": [],
            "needs_investigation": True,
            "needs_decomposition": False,
            "requires_action_plan": True,
            "growth_signal_kind": "none",
            "contradiction": {
                "contradictions": [
                    {"id": 1, "desc": "token 过期", "severity": 0.8, "aspect": "认证"},
                    {"id": 2, "desc": "UI 文案误导", "severity": 0.3, "aspect": "前端"},
                ],
                "principal": 1,
                "principal_aspect": "认证链路",
                "attack_order": [1, 2],
                "rationale": "先解决 token 再修文案",
            },
        }
    )


def _chitchat_payload() -> str:
    return json.dumps(
        {
            "restated_intent": "打招呼闲聊",
            "problem_type": "chitchat",
            "ambiguity_score": 0.0,
            "clarifying_questions": [],
            "needs_investigation": False,
            "needs_decomposition": False,
            "requires_action_plan": False,
            "growth_signal_kind": "none",
            "contradiction": None,
        }
    )


# ───────────────────────────────────────────────────────────────────────────
# WI-2 / WI-4：闲聊快路径 allowlist（整句锚定，命中 → 0 次 LLM 短路）
# ───────────────────────────────────────────────────────────────────────────

_ALLOWLIST_SHORTCIRCUIT = [
    "你好",
    "谢谢",
    "晚安",
    "😄",
    "。。。",
    "你好呀~",
    "晚安啊",
    "在吗在吗",
    "早上好",
    "hi 你好",
    "嗯嗯",
    "哈哈",
]
_ALLOWLIST_NO_SHORTCIRCUIT = [
    "你好，帮我看下这段为什么报错",
    "崩了",
    "报错",
    "卡死",
    "光合作用为什么需要光",
    "在吗？我代码崩了",
    "谢谢，那这个报错怎么办",
    "hi 帮我 debug",
    "在吗？",
    "ok 那你帮我改一下",
    "早上代码崩了",
]


@pytest.mark.parametrize(
    "msg",
    [
        "修复 ReActDriver 边界 bug",
        "请修改 Harness 生命周期代码",
        "测试 Kernel 和 Driver 的边界实现",
    ],
)
def test_platform_self_description_does_not_capture_engineering_actions(msg: str) -> None:
    from deskpet.agent.lexicon import is_platform_self_description

    assert is_platform_self_description(msg) is False


# ───────────────────────────────────────────────────────────────────────────
# WI-3（退役校验）：仓内无 bypass flag 读取点
# ───────────────────────────────────────────────────────────────────────────
def test_no_bypass_flag_read_points_in_backend() -> None:
    """plan WI-3：DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT / _CLARIFICATION 已退役，
    backend 源码（非测试）应 0 读取点。防回归引入新短路旁路。"""
    import os
    from pathlib import Path

    backend = Path(__file__).resolve().parent.parent
    needles = (
        "DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT",
        "DESKPET_DISABLE_CLARIFICATION",
    )
    offenders = []
    for root, dirs, files in os.walk(backend):
        # 跳过虚拟环境 / 缓存 / 测试自身
        parts = set(Path(root).parts)
        if any(
            p.startswith(".") and p not in (".",) for p in Path(root).relative_to(backend).parts
        ):
            continue
        if "tests" in Path(root).relative_to(backend).parts:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            fp = Path(root) / fn
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for needle in needles:
                if needle in text:
                    offenders.append(str(fp))
    assert not offenders, f"发现已退役 bypass flag 读取点: {offenders}"

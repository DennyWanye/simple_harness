# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-1 单测 — IntentTriage（决策4：Step1+3 合并，一次 analyze() LLM 调用）。

覆盖（plans/2026-06-24-... §6 WI-1 + 05 L1）：
  - chitchat 纯规则短路（**0 次 LLM**，一票否决）
  - 非闲聊只调 1 次 LLM（await_count==1，证明 Step1+3 合一）
  - 复杂问题一次调用同时出 intent + contradiction；简单 factual_qa 返回 contradiction=None
  - ambiguity≥阈值 → needs_clarification 澄清出口
  - prior_task_type → problem_type 映射
  - attack_order 解析
  - LLM 失败/超时/畸形 JSON → safe-fail 保守 card（contradiction=None）
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

from deskpet.agent.intent_triage import (
    Contradiction, ContradictionMap, IntentCard, IntentTriage,
    contradiction_to_system_message, intent_to_system_message,
)


def _complex_payload() -> str:
    return json.dumps({
        "restated_intent": "修复登录报错",
        "problem_type": "debug",
        "ambiguity_score": 0.2,
        "clarifying_questions": [],
        "needs_investigation": True,
        "needs_decomposition": False,
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
    })


def test_chitchat_zero_llm_call() -> None:
    """★ 一票否决：闲聊纯规则短路，绝不调 LLM。"""
    mock_llm = AsyncMock(return_value="{}")
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze("你好呀今天天气真好", prior_task_type="chat"))
    assert card.short_circuit is True
    assert card.problem_type == "chitchat"
    assert card.contradiction is None
    mock_llm.assert_not_called()
    assert mock_llm.call_count == 0


def test_emotion_zero_llm_call() -> None:
    mock_llm = AsyncMock(return_value="{}")
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze("我心情不好", prior_task_type="emotion"))
    assert card.short_circuit is True
    assert mock_llm.call_count == 0


def test_complex_single_llm_call_with_contradiction() -> None:
    """非闲聊：只调 1 次 LLM，同时返回 intent + contradiction（决策4 合一证明）。"""
    mock_llm = AsyncMock(return_value=_complex_payload())
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze("我的登录功能报错了帮我看看", prior_task_type="code"))
    assert mock_llm.await_count == 1                 # ★ 只调一次
    assert card.problem_type == "debug"
    assert card.contradiction is not None            # 矛盾段在同一次调用产出
    assert card.contradiction.principal == 1
    assert card.contradiction.attack_order == [1, 2]
    assert len(card.contradiction.contradictions) == 2


def test_simple_factual_returns_null_contradiction() -> None:
    """简单 factual_qa：contradiction=None（即便 LLM 误填也不解析，因 problem_type 不触发）。"""
    payload = json.dumps({
        "restated_intent": "查个事实",
        "problem_type": "factual_qa",
        "ambiguity_score": 0.1,
        "clarifying_questions": [],
        "needs_investigation": True,
        "needs_decomposition": False,
        "contradiction": None,
    })
    mock_llm = AsyncMock(return_value=payload)
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze("中国首都是哪", prior_task_type="recall"))
    assert mock_llm.await_count == 1
    assert card.problem_type == "factual_qa"
    assert card.contradiction is None


def test_ambiguity_triggers_clarification() -> None:
    payload = json.dumps({
        "restated_intent": "?",
        "problem_type": "ambiguous",
        "ambiguity_score": 0.9,
        "clarifying_questions": ["你指的是 A 还是 B？"],
        "needs_investigation": False,
        "needs_decomposition": False,
        "contradiction": None,
    })
    mock_llm = AsyncMock(return_value=payload)
    triage = IntentTriage(mock_llm, clarify_threshold=0.7)
    card = asyncio.run(triage.analyze("那个东西", prior_task_type=None))
    assert card.needs_clarification is True
    assert card.clarifying_questions == ["你指的是 A 还是 B？"]
    assert card.short_circuit is False


def test_tasktype_to_problem_mapping() -> None:
    triage = IntentTriage(None)  # llm_call=None → safe-fail 走派生
    card = asyncio.run(triage.analyze("做个 PPT", prior_task_type="task"))
    assert card.problem_type == "creation"
    assert card.needs_decomposition is True


def test_safe_fail_on_llm_exception() -> None:
    async def _boom(_prompt: str) -> str:
        raise RuntimeError("relay 500")

    triage = IntentTriage(_boom)
    card = asyncio.run(triage.analyze("帮我调研下行业", prior_task_type="web_search"))
    assert card.problem_type == "research"        # safe-fail 用派生类型
    assert card.contradiction is None             # 不填矛盾段
    assert card.needs_clarification is False


def test_safe_fail_on_malformed_json() -> None:
    mock_llm = AsyncMock(return_value="这不是 JSON，是一段废话")
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze("修 bug", prior_task_type="code"))
    assert card.problem_type == "debug"           # fallback 到派生
    assert card.contradiction is None


def test_fenced_json_extraction() -> None:
    """三级容错：fenced ```json 围栏也能解析。"""
    fenced = "思考中...\n```json\n" + _complex_payload() + "\n```\n收工"
    mock_llm = AsyncMock(return_value=fenced)
    triage = IntentTriage(mock_llm)
    card = asyncio.run(triage.analyze("登录报错", prior_task_type="code"))
    assert card.contradiction is not None
    assert card.contradiction.principal == 1


def test_system_message_helpers() -> None:
    card = IntentCard(restated_intent="修复登录", problem_type="debug")
    msg = intent_to_system_message(card)
    assert "<意图>" in msg and "修复登录" in msg
    cmap = ContradictionMap(
        contradictions=[Contradiction(id=1, desc="token 过期", aspect="认证")],
        principal=1, principal_aspect="认证链路",
    )
    cmsg = contradiction_to_system_message(cmap)
    assert "<主要矛盾>" in cmsg and "token 过期" in cmsg and "认证链路" in cmsg

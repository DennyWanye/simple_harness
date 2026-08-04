# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""General-agent planning tests.

覆盖：
  - 未开启 planning → 不调用 provider
  - planning_enabled + 复杂 problem_type → 出计划
  - 简单 problem_type（factual_qa）→ 不出计划
  - attack_order 注入 prompt（计划首步对准 principal）
  - parallelizable 解析
  - 3 级 JSON 容错（fenced 围栏）
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

from agent.plan import (
    PLAN_SCHEMA, Plan, PlanStep, maybe_extract_general_plan,
)


_LONG_MSG = "我的登录功能一直报错，帮我系统排查一下到底哪里出了问题" * 2


def _provider(payload: str) -> AsyncMock:
    prov = AsyncMock()
    prov.chat_with_tools = AsyncMock(return_value={"content": payload})
    return prov


def _plan_payload(with_parallel: bool = False) -> str:
    step = {
        "title": "查 token",
        "detail": "检查 token 是否过期",
        "action_categories": ["filesystem_read"],
    }
    if with_parallel:
        step["parallelizable"] = True
    return json.dumps({"rationale": "先查认证", "steps": [step]})


def test_planning_disabled_returns_none() -> None:
    prov = _provider(_plan_payload())
    plan = asyncio.run(maybe_extract_general_plan(prov, _LONG_MSG, None))
    assert plan is None
    prov.chat_with_tools.assert_not_called()  # 早退，不调 LLM


def test_general_plan_uses_single_schema() -> None:
    prov = _provider(_plan_payload())
    plan = asyncio.run(
        maybe_extract_general_plan(
            prov,
            _LONG_MSG,
            "/proj",
            planning_enabled=True,
            problem_type="debug",
        )
    )
    assert isinstance(plan, Plan)
    _, kwargs = prov.chat_with_tools.call_args
    assert kwargs["response_format"] is PLAN_SCHEMA
    assert kwargs["response_format"]["json_schema"]["name"] == "general_agent_plan"


def test_general_plan_short_message_still_skips() -> None:
    prov = _provider(_plan_payload())
    plan = asyncio.run(
        maybe_extract_general_plan(
            prov,
            "ls",
            "/proj",
            planning_enabled=True,
            problem_type="debug",
        )
    )
    assert plan is None


def test_manual_authorization_plans_short_effectful_request() -> None:
    prov = _provider(_plan_payload())
    plan = asyncio.run(
        maybe_extract_general_plan(
            prov,
            "mkdir demo",
            "/proj",
            planning_enabled=False,
            authorization_required=True,
            problem_type="creation",
        )
    )
    assert isinstance(plan, Plan)
    prov.chat_with_tools.assert_called_once()


def test_manual_authorization_plans_factual_classification() -> None:
    prov = _provider(_plan_payload())
    plan = asyncio.run(
        maybe_extract_general_plan(
            prov,
            "run it",
            "/proj",
            authorization_required=True,
            problem_type="factual_qa",
        )
    )
    assert isinstance(plan, Plan)
    prov.chat_with_tools.assert_called_once()


def test_manual_authorization_skips_model_classified_chitchat() -> None:
    prov = _provider(_plan_payload())
    plan = asyncio.run(
        maybe_extract_general_plan(
            prov,
            "hello",
            "/proj",
            authorization_required=True,
            problem_type="chitchat",
        )
    )
    assert plan is None
    prov.chat_with_tools.assert_not_called()


def test_planning_enabled_complex_generates_plan() -> None:
    prov = _provider(_plan_payload(with_parallel=True))
    plan = asyncio.run(maybe_extract_general_plan(
        prov, _LONG_MSG, None,
        planning_enabled=True, problem_type="debug",
    ))
    assert isinstance(plan, Plan)
    _, kwargs = prov.chat_with_tools.call_args
    assert kwargs["response_format"] is PLAN_SCHEMA
    assert kwargs["response_format"]["json_schema"]["name"] == "general_agent_plan"
    # parallelizable 解析
    assert plan.steps[0].parallelizable is True
    assert plan.steps[0].action_categories == ("filesystem_read",)


def test_simple_problem_type_skips() -> None:
    prov = _provider(_plan_payload())
    plan = asyncio.run(maybe_extract_general_plan(
        prov, _LONG_MSG, None,
        planning_enabled=True, problem_type="factual_qa",
    ))
    assert plan is None
    prov.chat_with_tools.assert_not_called()


def test_attack_order_injected_into_prompt() -> None:
    prov = _provider(_plan_payload())
    asyncio.run(maybe_extract_general_plan(
        prov, _LONG_MSG, None,
        planning_enabled=True, problem_type="multi_task",
        attack_order=[2, 1],
        contradiction_descs={1: "token 过期", 2: "数据库连接超时"},
    ))
    args, _ = prov.chat_with_tools.call_args
    user_content = args[0][1]["content"]
    assert "主要矛盾攻击顺序" in user_content
    # attack_order=[2,1] → 首项是 id=2 的 desc
    assert "1. 数据库连接超时" in user_content
    assert "2. token 过期" in user_content


def test_parallelizable_defaults_false() -> None:
    prov = _provider(_plan_payload(with_parallel=False))
    plan = asyncio.run(maybe_extract_general_plan(
        prov, _LONG_MSG, None,
        planning_enabled=True, problem_type="creation",
    ))
    assert plan.steps[0].parallelizable is False


def test_fenced_json_fallback() -> None:
    """Provider 吐 ```json 围栏也能解析（3 级容错）。"""
    fenced = "思考中...\n```json\n" + _plan_payload() + "\n```"
    prov = _provider(fenced)
    plan = asyncio.run(maybe_extract_general_plan(
        prov, _LONG_MSG, None,
        planning_enabled=True, problem_type="debug",
    ))
    assert isinstance(plan, Plan)
    assert plan.steps[0].title == "查 token"


def test_general_schema_requires_parallelizable_and_action_categories() -> None:
    base_step = PLAN_SCHEMA["json_schema"]["schema"]["properties"]["steps"]["items"]
    assert "parallelizable" in base_step["properties"]
    assert "action_categories" in base_step["properties"]
    assert base_step["required"] == [
        "title",
        "detail",
        "parallelizable",
        "action_categories",
    ]

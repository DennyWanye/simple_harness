# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事故 J：模型可见的 ``memory_forget`` 必须永不以裸异常收场。

HM-TO-A6 turn 23（真实 DeepSeek）：用户说「把这条关系忘掉」，模型连打 18 次
``memory_forget``——6 次 ``{"query": ...}``、12 次 ``{}``——处理器分别静默失败与
抛 ``KeyError('fact_id')``，两种都被压成不可行动的 "Tool execution failed."，
Run 最终死于 ``react_repeated_tool_exceeded``。

这里锁死修复后的契约：
* 任何模型可控的参数形状都返回**确定性**的稳定码 + 可行动文案；
* 拒绝文案通过产品包装层 ``_result`` 原样抵达模型（不再是通用失败）；
* 合法 id 的授权/抑制入参逐字不变（遗忘只针对记忆，不改写会话记录）。
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from simple_harness import CallId
from simple_harness.tools import ToolOutcome
from simple_harness_memory import MemoryOwnershipConflict

from deskpet.sdk_adapters import tools as product_tools
from deskpet.tool_catalog.providers import (
    MEMORY_FORGET_IDENTITY_UNAVAILABLE,
    MEMORY_FORGET_INVALID_FACT_ID,
    MEMORY_FORGET_NATURAL_LANGUAGE_DISABLED,
    MEMORY_FORGET_STORE_UNAVAILABLE,
    MEMORY_FORGET_TARGET_REQUIRED,
    MEMORY_FORGET_UNKNOWN_FACT_ID,
    ToolCatalogDependencies,
    _dynamic_handlers,
)

# turn 23 里模型真正发出的自然语言参数（execution_effects.arguments_json 原文）。
INCIDENT_QUERY = "秋分资料整理这套校对流程按那个 Python 环境执行的这条关系"

IDENTITY = SimpleNamespace(
    deployment_id="deployment",
    household_id="household-a",
    actor_id="actor-a",
    session_id="session-a",
)


def _fact(fact_id: int, key: str, value: str) -> SimpleNamespace:
    return SimpleNamespace(id=fact_id, key=key, value=value)


def _dependencies(
    manager: SimpleNamespace, resolver: SimpleNamespace
) -> ToolCatalogDependencies:
    no_op = SimpleNamespace(
        replace_session_todos=lambda *_args: None,
        get=lambda *_args: None,
        mark_active=lambda *_args: None,
        recall_readonly=lambda *_args: None,
        resolve_for_run=lambda *_args: None,
        search=lambda *_args: None,
        describe=lambda *_args: None,
        suggestions=lambda *_args: None,
        activate=lambda *_args: None,
    )
    return ToolCatalogDependencies(
        no_op,
        lambda: None,
        no_op,
        lambda: None,
        no_op,
        no_op,
        no_op,
        SimpleNamespace(search=lambda *_args: None),
        memory_manager=manager,
        memory_identity_resolver=resolver,
    )


def _build_forget(manager: SimpleNamespace):
    resolver = SimpleNamespace(
        resolve=AsyncMock(side_effect=lambda session_id: IDENTITY)
    )
    return _dynamic_handlers(_dependencies(manager, resolver))["memory_forget"][0]


def _manager(
    *,
    facts: tuple[SimpleNamespace, ...] = (),
    forget_result: object = True,
    forget_error: BaseException | None = None,
) -> SimpleNamespace:
    forget = (
        AsyncMock(side_effect=forget_error)
        if forget_error is not None
        else AsyncMock(return_value=forget_result)
    )
    return SimpleNamespace(
        forget_fact=forget,
        list_facts=AsyncMock(return_value=list(facts)),
        read_fact=AsyncMock(return_value=None),
        remember_fact=AsyncMock(return_value=1),
    )


CONTEXT = SimpleNamespace(session_id="session-a", root_run_id="root", call_id="call")


@contextlib.contextmanager
def _sdk_call(call_id: str = "call"):
    token = product_tools._current_call_id.set(CallId(call_id))
    try:
        yield
    finally:
        product_tools._current_call_id.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "expected_code"),
    (
        ({}, MEMORY_FORGET_TARGET_REQUIRED),
        ({"deskpet_public_progress": "忘掉"}, MEMORY_FORGET_TARGET_REQUIRED),
        ({"fact_id": None}, MEMORY_FORGET_TARGET_REQUIRED),
        ({"query": INCIDENT_QUERY}, MEMORY_FORGET_NATURAL_LANGUAGE_DISABLED),
        ({"query": "   "}, MEMORY_FORGET_TARGET_REQUIRED),
        ({"fact_id": "abc"}, MEMORY_FORGET_INVALID_FACT_ID),
        ({"fact_id": True}, MEMORY_FORGET_INVALID_FACT_ID),
        ({"fact_id": 1.5}, MEMORY_FORGET_INVALID_FACT_ID),
        ({"fact_id": []}, MEMORY_FORGET_INVALID_FACT_ID),
    ),
)
async def test_model_controllable_arguments_never_raise(
    arguments: dict[str, object], expected_code: str
) -> None:
    manager = _manager(facts=(_fact(41, "proofreading_python_version", "Python 3.12"),))
    forget = _build_forget(manager)

    result = await forget(dict(arguments), CONTEXT)

    assert result["ok"] is False
    assert result["error_code"] == expected_code
    # 可行动：告诉模型有哪些 id、下一步传什么。
    assert "41" in result["public_message"]
    assert "fact_id" in result["public_message"]
    manager.forget_fact.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejection_without_candidates_tells_the_model_to_stop_retrying() -> None:
    """事故现场：legacy facts 表 0 行，模型必须被明确劝停而不是重试 18 次。"""

    manager = _manager(facts=())
    forget = _build_forget(manager)

    result = await forget({"query": INCIDENT_QUERY}, CONTEXT)

    assert result["error_code"] == MEMORY_FORGET_NATURAL_LANGUAGE_DISABLED
    message = result["public_message"]
    assert "Do not call memory_forget again" in message
    assert "memory panel" in message


@pytest.mark.asyncio
async def test_candidate_listing_failure_degrades_instead_of_crashing() -> None:
    manager = _manager()
    manager.list_facts = AsyncMock(side_effect=RuntimeError("store offline"))
    forget = _build_forget(manager)

    result = await forget({}, CONTEXT)

    assert result["error_code"] == MEMORY_FORGET_TARGET_REQUIRED
    assert "Do not call memory_forget again" in result["public_message"]


@pytest.mark.asyncio
async def test_unknown_fact_id_is_a_stable_rejection_not_a_false_receipt() -> None:
    manager = _manager(
        facts=(_fact(41, "proofreading_python_version", "Python 3.12"),),
        forget_result=False,
    )
    forget = _build_forget(manager)

    result = await forget({"fact_id": 9999}, CONTEXT)

    assert result["ok"] is False
    assert result["error_code"] == MEMORY_FORGET_UNKNOWN_FACT_ID
    assert "9999" in result["public_message"]
    assert "41" in result["public_message"]


@pytest.mark.asyncio
async def test_identity_failure_on_the_success_path_is_also_a_stable_rejection() -> None:
    """Host 不变量失败也不得以裸异常收场；语义仍是「什么都没遗忘」。"""

    manager = _manager()
    resolver = SimpleNamespace(
        resolve=AsyncMock(side_effect=RuntimeError("identity binding missing"))
    )
    forget = _dynamic_handlers(_dependencies(manager, resolver))["memory_forget"][0]

    result = await forget({"fact_id": 41}, CONTEXT)

    assert result["ok"] is False
    assert result["error_code"] == MEMORY_FORGET_IDENTITY_UNAVAILABLE
    assert "identity binding missing" not in result["public_message"]
    manager.forget_fact.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_failure_becomes_a_payload_free_stable_rejection() -> None:
    manager = _manager(forget_error=MemoryOwnershipConflict())
    forget = _build_forget(manager)

    result = await forget({"fact_id": 41}, CONTEXT)

    assert result["ok"] is False
    assert result["error_code"] == MEMORY_FORGET_STORE_UNAVAILABLE
    assert "MemoryOwnershipConflict" not in result["public_message"]


@pytest.mark.asyncio
async def test_valid_fact_id_keeps_the_authorization_arguments_unchanged() -> None:
    """r-journey 遗忘语义：只调 Memory 的 forget_fact，授权入参逐字不变。

    「遗忘只针对记忆，不针对会话记录」（CLAUDE.md 用户产品决定 2）：处理器不得
    触碰任何会话/历史面，成功回执里也不带历史改写。
    """

    manager = _manager(forget_result="forgotten")
    forget = _build_forget(manager)

    result = await forget({"fact_id": 41}, CONTEXT)

    assert result == {
        "ok": True,
        "forgotten": True,
        "receipt": "forgotten",
        "source_event_id": "explicit-memory-action/v1/root/call",
    }
    kwargs = manager.forget_fact.await_args.kwargs
    principal = kwargs["principal"]
    assert (
        principal.deployment_id,
        principal.household_id,
        principal.actor_id,
        principal.session_id,
    ) == ("deployment", "household-a", "actor-a", "session-a")
    assert manager.forget_fact.await_args.args == (41,)
    assert kwargs == {
        "reason": "",
        "principal": principal,
        "source_event_id": "explicit-memory-action/v1/root/call",
        "payload_hash": None,
    }
    # 成功路径只碰记忆遗忘这一个面。
    manager.list_facts.assert_not_awaited()
    manager.read_fact.assert_not_awaited()
    manager.remember_fact.assert_not_awaited()


@pytest.mark.asyncio
async def test_numeric_string_fact_id_is_accepted() -> None:
    manager = _manager()
    forget = _build_forget(manager)

    result = await forget({"fact_id": "41"}, CONTEXT)

    assert result["ok"] is True
    assert manager.forget_fact.await_args.args == (41,)


def test_published_description_no_longer_points_at_a_nonexistent_tool() -> None:
    """``memory_facts_list`` 是 UI 的 ws 路由，不是工具；模型照它做只能空手调用。"""

    from deskpet.tool_catalog.providers import build_explicit_product_tool_catalog

    catalog = build_explicit_product_tool_catalog(
        _dependencies(_manager(), SimpleNamespace(resolve=AsyncMock()))
    )
    registration = next(
        item for item in catalog.registrations if item.name == "memory_forget"
    )

    assert "memory_facts_list" not in registration.description
    assert "memory_write" in registration.description
    properties = registration.input_schema["properties"]
    assert "memory_write" in properties["fact_id"]["description"]
    assert "rejected" in properties["query"]["description"]


@pytest.mark.asyncio
async def test_rejection_reaches_the_model_as_a_stable_code_and_message() -> None:
    """产品包装层必须把拒绝原样透出，而不是通用 "Tool execution failed."。"""

    manager = _manager(facts=(_fact(41, "proofreading_python_version", "Python 3.12"),))
    forget = _build_forget(manager)

    raw = await forget({"query": INCIDENT_QUERY}, CONTEXT)
    with _sdk_call("call"):
        result = product_tools._result(raw)

    assert result.outcome is ToolOutcome.FAILED
    assert result.error_code == MEMORY_FORGET_NATURAL_LANGUAGE_DISABLED
    assert result.public_message != "Tool execution failed."
    assert "41" in result.public_message

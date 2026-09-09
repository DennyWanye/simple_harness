# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 Z 第 2 部分：预算耗尽时收尾作答，而不是把整轮打死。

原生证据 ``.local-test-evidence/2026-09-09/native-a6-run10/primary-ui-j5yjctfj/``
（``product-sdk-6ad6a40a…``，HM-TO-A6 第 10 次尝试第 6 轮，21 次 provider 调用）
最后一次装配：

    sdk_context_budget_exceeded planned=28494 effective=26752 protected=7920
    tool_schemas=6010 groups=1 ratio=1.65 protected_messages=1910
    open_group=20574 full_trim=True

open group 的组成（同一份证据）：20 条 assistant 工具调用回声 20.5 KB（因果链，
不可裁）、17 条分页 descriptor 9.6 KB、8 条省略通知 5.3 KB。全部降级都跑完之后仍
超 1742 token，Run 以 ``react_termination_limits`` 结束，用户什么也没拿到。

本文件用真实估算器 + 真实冻结预算复现这一形态：main 上是红（raise），修复后是绿
（注入一条确定性收尾指令、只保留本轮受保护部分与用户消息，请求装得下）。
"""
import pytest
from simple_harness import CallId
from simple_harness.contracts.messages import Message, MessageRole

from deskpet.sdk_adapters.context_partitions import (
    ContextBudgetExceeded, effective_input_budget,
)

from tests.execution.test_control_result_bound import (  # noqa: F401
    CURRENT_TEXT, FLASH_RATIO, WINDOW, flash_calibration,
)

# 证据里的组成（字节数取每类总量 / 条数）。
ECHOES, ECHO_BYTES = 20, 20_480 // 20
DESCRIPTORS, DESCRIPTOR_BYTES = 17, 9_830 // 17
NOTICES, NOTICE_BYTES = 8, 5_427 // 8
OPEN_GROUP_ITEMS = ECHOES + DESCRIPTORS + NOTICES
# 证据里的实际超额：28494 - 26752。
INCIDENT_OVERSHOOT = 1742


def _build(system_chars: int) -> tuple:
    """turn 6 的消息形态：受保护前缀 + 用户消息 + 不可裁的 open group。"""

    messages = [
        Message(MessageRole.SYSTEM, "S" * system_chars),
        Message(MessageRole.USER, CURRENT_TEXT),
    ]
    for index in range(ECHOES):
        messages.append(Message(MessageRole.ASSISTANT, "a" * ECHO_BYTES))
        if index < DESCRIPTORS:
            messages.append(Message(MessageRole.TOOL, "d" * DESCRIPTOR_BYTES,
                                    name="context_page_in",
                                    call_id=CallId("call_00_d%02d%s" % (index, "d" * 18))))
        if index >= ECHOES - NOTICES:
            messages.append(Message(MessageRole.TOOL, "n" * NOTICE_BYTES,
                                    name="tool_search",
                                    call_id=CallId("call_00_n%02d%s" % (index, "n" * 18))))
    # 最后一条是 tool 结果：这一组未终结，正是事件 Z 里那个不可裁的 open group。
    assert messages[-1].role is MessageRole.TOOL
    assert sum(1 for m in messages if m.role is MessageRole.TOOL) == DESCRIPTORS + NOTICES
    return tuple(messages)


def _plan(messages, **kwargs):
    from deskpet.sdk_adapters.context_authority import _plan_turn_messages

    return _plan_turn_messages(
        messages, WINDOW, tools=(), exact_tool_sources=True,
        model_id="deepseek-v4-flash", provider_turn_ordinal=21,
        allow_full_group_trim=True, **kwargs,
    )


@pytest.fixture
def incident(flash_calibration):
    """受保护前缀标定到事件 Z 的超额：全降级之后仍差 1742 token。"""

    effective = effective_input_budget(WINDOW)
    assert effective == 26752
    _, probe = _plan(_build(4), raise_on_overflow=False)
    needed = effective + INCIDENT_OVERSHOOT - probe["planned_input_tokens"]
    system_chars = 4 + needed * 4 * 1000 // int(FLASH_RATIO * 1000)
    messages = _build(system_chars)
    _, facts = _plan(messages, raise_on_overflow=False)
    assert facts["causal_groups"] == 1, "事件 Z 只剩一个 open group，裁无可裁"
    assert facts["budget_headroom"] < 0
    return messages, effective


# --------------------------------------------------------------------------
# 1. main 的行为：全降级跑完仍然 raise，整轮丢失。
# --------------------------------------------------------------------------


def test_incident_z_still_fails_closed_without_the_wrap_up(incident):
    messages, _ = incident
    with pytest.raises(ContextBudgetExceeded) as error:
        _plan(messages)
    assert "sdk_context_budget_exceeded" in str(error.value)


# --------------------------------------------------------------------------
# 2. 修复后：注入一条收尾指令，本轮装得下并且仍然回答用户。
# --------------------------------------------------------------------------


def test_wrap_up_makes_the_incident_turn_fit(incident):
    from deskpet.sdk_adapters.context_authority import (
        CONTEXT_BUDGET_WRAP_UP_ID, context_budget_wrap_up_message,
    )

    messages, effective = incident
    planned, facts = _plan(messages, wrap_up_message=context_budget_wrap_up_message())

    # (a) 请求装得下，并且是**因为**收尾路径把 open group 的因果链交了出去。
    assert facts["budget_headroom"] >= 0, facts
    assert facts["planned_input_tokens"] <= effective
    assert facts["wrap_up_injected"] == 1
    assert facts["open_group_items_dropped"] == OPEN_GROUP_ITEMS

    roles = [m.role.value for m in planned]
    # (b) 用户这一轮真正问的话必须还在，否则模型无从回答。
    assert CURRENT_TEXT in [m.content for m in planned]
    # (c) 因果链整条走人：没有孤儿 assistant 工具调用，也没有 tool 结果。
    assert "assistant" not in roles and "tool" not in roles
    # (d) 指令是模型可见的 system 消息，且带稳定 id。
    injected = [m for m in planned if CONTEXT_BUDGET_WRAP_UP_ID in m.content]
    assert len(injected) == 1
    assert injected[0].role is MessageRole.SYSTEM
    assert "Stop calling tools now" in injected[0].content


def test_wrap_up_message_is_deterministic_and_part_of_the_request(incident):
    from simple_harness import RequestId
    from simple_harness.execution.provider_invocations import provider_request_fingerprint
    from simple_harness.providers import ProviderRequest

    from deskpet.sdk_adapters.context_authority import context_budget_wrap_up_message

    first, second = context_budget_wrap_up_message(), context_budget_wrap_up_message()
    assert first.content == second.content
    assert first.metadata == second.metadata == {
        "source": "context_budget", "trust": "host_authority"
    }

    messages, _ = incident
    planned_a, _ = _plan(messages, wrap_up_message=first)
    planned_b, _ = _plan(messages, wrap_up_message=second)
    fingerprint = lambda planned: provider_request_fingerprint(  # noqa: E731
        ProviderRequest(RequestId("hash-only"), planned, tools=())
    )
    assert fingerprint(planned_a) == fingerprint(planned_b)
    # 指令确实进了被指纹覆盖的请求体，而不是旁路日志。
    assert any("context_budget_wrap_up" in m.content for m in planned_a)


def test_a_healthy_turn_is_untouched_by_the_wrap_up_parameter(flash_calibration):
    """没有预算压力时，收尾参数不改变任何取舍（只多一条受保护消息）。"""

    from deskpet.sdk_adapters.context_authority import context_budget_wrap_up_message

    messages = _build(4)[:4]
    plain, plain_facts = _plan(messages, raise_on_overflow=False)
    wrapped, wrapped_facts = _plan(
        messages, raise_on_overflow=False,
        wrap_up_message=context_budget_wrap_up_message(),
    )
    assert plain_facts["open_group_items_dropped"] == 0
    assert wrapped_facts["open_group_items_dropped"] == 0
    assert [m.content for m in plain] == [
        m.content for m in wrapped if "context_budget_wrap_up" not in m.content
    ]


# --------------------------------------------------------------------------
# 3. 触发规则：阈值 = 一个 react 步；每个 Run 只收尾一次。
# --------------------------------------------------------------------------


def test_threshold_is_one_react_step_and_fires_on_every_turn_that_needs_it():
    """2026-09-09 事件 AG：判据仍是「一个 react 步」，但不再是每个 Run 一次。

    证据 ``.local-test-evidence/2026-09-09/native-a6-run12/primary-ui-z9j48osx/``
    里 ``product-sdk-015ad2fe…`` 只收尾过一次：

        turn=13 headroom=1003 threshold=1200 open_group_items_dropped=0

    余量 1003 是**正数**——那一轮装得下，收尾只是一句劝告，一条也没裁；可它把这个
    Run 唯一的一次额度用光了。模型没听，继续 ``context_page_in``；第 14–18 轮余量
    1078 / 95 / 2316 / 1105 / 373 全都低于阈值却 ``wrap_up_injected=0``；第 19 轮
    真的超了 450 token 时，闩再次拒绝，Run 以 ``sdk_context_budget_exceeded`` ->
    ``react_termination_limits`` 结束，用户一个字也没拿到。

    收尾指令是确定性的、约 120 token 的 SYSTEM 消息，每轮重发既幂等又便宜；真正
    的界是 react 的 25 轮 / 600 s 上限，不是这道进程本地的闩（它还让第 N 轮的请求
    取决于本进程此前处理过哪些轮，本身就是重放上的隐患）。
    """

    from deskpet.sdk_adapters.context_authority import (
        CONTEXT_BUDGET_WRAP_UP_HEADROOM_TOKENS, ProductRunContextAuthority,
    )

    # 证据：open_group 20574 token / 45 条 ≈ 457，一个 react 步（回声 + 它的结果）
    # ≈ 915；阈值取其上取整，并覆盖收尾指令自身约 120 token 的开销。
    assert CONTEXT_BUDGET_WRAP_UP_HEADROOM_TOKENS == 1200
    assert 20_574 // OPEN_GROUP_ITEMS * 2 < CONTEXT_BUDGET_WRAP_UP_HEADROOM_TOKENS

    authority = ProductRunContextAuthority.__new__(ProductRunContextAuthority)
    authority._wrap_up_runs = set()
    # 还够一个步：不收尾。
    assert authority._should_wrap_up("run-a", CONTEXT_BUDGET_WRAP_UP_HEADROOM_TOKENS) is False
    # 事故的那一串余量：第 13 轮的劝告之后，第 14–18 轮每一轮都还得收尾，
    # 唯一的例外是真的够一个步的 2316。
    decisions = [authority._should_wrap_up("run-a", headroom)
                 for headroom in AG_HEADROOMS]
    assert decisions == [True, True, True, False, True, True], decisions
    # 而真正装不下的第 19 轮，无论前面收尾过多少次，都必须还能收尾。
    assert authority._should_wrap_up("run-a", -AG_OVERSHOOT) is True
    assert authority._should_wrap_up("run-a", -INCIDENT_OVERSHOOT) is True
    # 另一个 Run 不受影响。
    assert authority._should_wrap_up("run-b", -INCIDENT_OVERSHOOT) is True


# --------------------------------------------------------------------------
# 4. 事件 AG：13 次 context_page_in 之后的那一轮，仍然要能收尾
# --------------------------------------------------------------------------
#
# 失败 Run ``product-sdk-015ad2fe…`` 第 19 次装配：
#
#     sdk_context_budget_exceeded planned=27202 effective=26752 protected=8403
#     tool_schemas=6465 groups=1 ratio=1.65 protected_messages=1938
#     open_group=18799 full_trim=True
#
# open group 就是那 13 次 ``context_page_in``（每次一条 ~1.8 KB 的 control 结果）
# 加上它们的 assistant 回声与几条分页 descriptor。

AG_PAGE_INS, AG_PAGE_IN_BYTES = 13, 1_800
AG_DESCRIPTORS, AG_DESCRIPTOR_BYTES = 5, 578
AG_ECHO_BYTES = 1_024
AG_OPEN_GROUP_ITEMS = AG_PAGE_INS * 2 + AG_DESCRIPTORS
# 证据里的实际超额：27202 - 26752。
AG_OVERSHOOT = 450
# 证据里第 13–18 轮收据上的 ``budget_headroom``。
AG_HEADROOMS = (1003, 1078, 95, 2316, 1105, 373)


def _build_ag(system_chars: int) -> tuple:
    """第 6 轮第 19 次装配的形状：受保护前缀 + 用户消息 + 13 次翻页的 open group。"""

    messages = [
        Message(MessageRole.SYSTEM, "S" * system_chars),
        Message(MessageRole.USER, CURRENT_TEXT),
    ]
    for index in range(AG_PAGE_INS):
        messages.append(Message(MessageRole.ASSISTANT, "a" * AG_ECHO_BYTES))
        messages.append(Message(MessageRole.TOOL, "p" * AG_PAGE_IN_BYTES,
                                name="context_page_in",
                                call_id=CallId("call_00_p%02d%s" % (index, "p" * 18))))
        if index < AG_DESCRIPTORS:
            messages.append(Message(MessageRole.TOOL, "d" * AG_DESCRIPTOR_BYTES,
                                    name="read_file",
                                    call_id=CallId("call_00_d%02d%s" % (index, "d" * 18))))
    assert messages[-1].role is MessageRole.TOOL
    return tuple(messages)


def _plan_ag(messages, **kwargs):
    from deskpet.sdk_adapters.context_authority import _plan_turn_messages

    return _plan_turn_messages(
        messages, WINDOW, tools=(), exact_tool_sources=True,
        model_id="deepseek-v4-flash", provider_turn_ordinal=19,
        allow_full_group_trim=True, **kwargs,
    )


@pytest.fixture
def ag_incident(flash_calibration):
    """受保护前缀标定到事件 AG 的超额：全降级之后仍差 450 token。"""

    effective = effective_input_budget(WINDOW)
    _, probe = _plan_ag(_build_ag(4), raise_on_overflow=False)
    needed = effective + AG_OVERSHOOT - probe["planned_input_tokens"]
    messages = _build_ag(4 + needed * 4 * 1000 // int(FLASH_RATIO * 1000))
    _, facts = _plan_ag(messages, raise_on_overflow=False)
    assert facts["causal_groups"] == 1, "事故里 groups=1，历史早就裁光了"
    assert facts["budget_headroom"] < 0
    return messages, effective


def test_event_ag_thirteen_page_ins_do_not_fit_without_the_wrap_up(ag_incident):
    """main 的行为：全降级跑完仍然 raise，整轮丢失。"""

    messages, _ = ag_incident
    with pytest.raises(ContextBudgetExceeded) as error:
        _plan_ag(messages)
    assert "sdk_context_budget_exceeded" in str(error.value)


def test_event_ag_still_wraps_up_after_an_earlier_advisory(ag_incident):
    """事故的真正死因：第 13 轮那次**什么也没裁**的劝告吃掉了第 19 轮的救命额度。

    main 上 ``_should_wrap_up`` 在第 13 轮就把闩合上，之后一路返回 False，第 19 轮
    走 else 分支原样 raise——这条断言在 main 上是红的。
    """

    from deskpet.sdk_adapters.context_authority import (
        ProductRunContextAuthority, context_budget_wrap_up_message,
    )

    messages, effective = ag_incident
    authority = ProductRunContextAuthority.__new__(ProductRunContextAuthority)
    authority._wrap_up_runs = set()
    run_key = "product-sdk-015ad2fe"
    for headroom in AG_HEADROOMS:  # 第 13–18 轮，含那次正余量的劝告
        authority._should_wrap_up(run_key, headroom)

    _, probe = _plan_ag(messages, raise_on_overflow=False)
    # 标定按 token 取整，落在证据那 450 的一个 token 之内即可。
    assert abs(probe["budget_headroom"] + AG_OVERSHOOT) <= 1
    # (a) 第 19 轮必须还能收尾。
    assert authority._should_wrap_up(run_key, probe["budget_headroom"]) is True
    # (b) 收尾之后这一轮真的装得下——代价是交出 open group 的因果链。
    planned, facts = _plan_ag(messages, wrap_up_message=context_budget_wrap_up_message())
    assert facts["budget_headroom"] >= 0
    assert facts["planned_input_tokens"] <= effective
    assert facts["wrap_up_injected"] == 1
    assert facts["open_group_items_dropped"] == AG_OPEN_GROUP_ITEMS
    # (c) 用户那一问还在，13 条翻页结果和它们的回声全部走人。
    assert CURRENT_TEXT in [m.content for m in planned]
    roles = [m.role.value for m in planned]
    assert "assistant" not in roles and "tool" not in roles
    assert sum(1 for m in planned if "context_budget_wrap_up" in m.content) == 1

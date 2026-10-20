# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""快速聊天（AgentOS 迁移第 4 块）：同一引擎的轻量运行档，不进任务编排。

场景（对应交接验收矩阵 C01）：
- 普通问答：一次模型调用、没有工具，返回正文、用量与耗时；同一会话第二句能看到第一句。
- 只读查询：模型调一次应用注册的工具，调用记录在回执里。
- 必须调工具却没调：提醒一次后调了；仍不调就如实返回 tool_not_used。
- 月配额用完：拒绝，不调模型。
- 工具名单在会话第一句时定下；后面换名单被拒。
- 有副作用的事不在聊天里做：工具只有只读的，模型只能说"这要建任务"。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_orchestrator.host import HostSettings, OrchestrationHost, Principal
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider


def _device_tool(seen: list[dict[str, Any]]):
    from agent_orchestrator.host import DomainTool

    def status(arguments, context):
        seen.append({"arguments": dict(arguments), "context": context})
        return {"devices": [{"name": "客厅灯", "battery": 0}, {"name": "门锁", "battery": 80}]}

    return DomainTool(
        name="device_status",
        schema={
            "type": "object",
            "description": "查家里设备的电量",
            "properties": {"room": {"type": "string", "description": "房间，可不填"}},
            "additionalProperties": False,
        },
        invoke=status,
    )


def _chat_script(answers: list[Any]):
    """按顺序给回复：字符串是正文，二元组是工具调用；用完后重复最后一条。"""

    asked: list[Any] = []

    def reply(request):
        asked.append(request)
        index = min(len(asked) - 1, len(answers) - 1)
        answer = answers[index]
        return answer(request) if callable(answer) else answer

    reply.asked = asked  # type: ignore[attr-defined]
    return reply


async def _open(tmp_path, *, chat, tools=(), **settings):
    return await OrchestrationHost.open(
        tmp_path / "host",
        principal=Principal("u", "用户"),
        provider=ScriptedProvider(chat=chat),
        counter=FixtureWordCounter(),
        settings=HostSettings(**settings),
        tools=list(tools),
        require_acceptance=False,
    )


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=120))


def test_plain_question_is_one_model_call_without_tools(tmp_path):
    async def case():
        script = _chat_script(["你好！有什么要我查的？", "刚才你说了你好。"])
        host = await _open(tmp_path, chat=script)
        try:
            reply = await host.chat({"session_id": "s1", "text": "你好", "idempotency_key": "s1-1"})
            assert reply["text"] == "你好！有什么要我查的？"
            assert reply["model_calls"] == 1 and reply["tool_calls"] == []
            assert reply["usage"]["input_tokens"] > 0 and reply["usage"]["unknown"] is False
            assert reply["total_ms"] >= 0 and reply["nudged"] is False
            assert reply["session_id"] == "s1" and reply["turn_id"]
            # 同一会话第二句：同一个 Agent，第一句在它的历史里
            again = await host.chat(
                {"session_id": "s1", "text": "我刚才说了什么？", "idempotency_key": "s1-2"}
            )
            assert again["text"] == "刚才你说了你好。"
            assert again["agent_id"] == reply["agent_id"]
            last = script.asked[-1]
            assert any("你好" in str(m.content) for m in last.messages)
            # 同一个幂等键再发：原样返回，不再调模型
            replay = await host.chat(
                {"session_id": "s1", "text": "我刚才说了什么？", "idempotency_key": "s1-2"}
            )
            assert replay["turn_id"] == again["turn_id"] and len(script.asked) == 2
            rows = host.chat_history("s1")
            assert [r["turn_id"] for r in rows] == [reply["turn_id"], again["turn_id"]]
            # 月账上按真实用量结清了
            budget = host.global_budget_status()
            assert budget["used_tokens"] > 0 and budget["reserved_tokens"] == 0
        finally:
            await host.close()

    _run(case())


def test_read_only_query_calls_the_registered_tool(tmp_path):
    async def case():
        seen: list[dict[str, Any]] = []
        script = _chat_script([("device_status", {}), "客厅灯没电了。"])
        host = await _open(tmp_path, chat=script, tools=[_device_tool(seen)])
        try:
            reply = await host.chat(
                {
                    "session_id": "s2",
                    "text": "哪台设备没电了？",
                    "idempotency_key": "s2-1",
                    "tools": ["device_status"],
                }
            )
            assert reply["text"] == "客厅灯没电了。"
            assert reply["model_calls"] == 2
            [call] = reply["tool_calls"]
            assert call["tool"] == "device_status" and call["outcome"] == "succeeded"
            [invocation] = seen
            context = invocation["context"]
            assert context.mission_id is None and context.attempt_id == "chat-s2"
            assert context.principal_id == "u"
            # 换工具名单：会话的工具在第一句定下，不能换
            with pytest.raises(Exception, match="chat_tools_frozen"):
                await host.chat(
                    {"session_id": "s2", "text": "再查", "idempotency_key": "s2-2", "tools": []}
                )
        finally:
            await host.close()

    _run(case())


def test_require_tool_nudges_once_then_reports_honestly(tmp_path):
    async def case():
        seen: list[dict[str, Any]] = []
        # 第一回合不调工具就回答；提醒后调了工具再答
        script = _chat_script(["大概是客厅灯吧。", ("device_status", {}), "客厅灯没电了。"])
        host = await _open(tmp_path, chat=script, tools=[_device_tool(seen)])
        try:
            reply = await host.chat(
                {
                    "session_id": "s3",
                    "text": "哪台设备没电了？",
                    "idempotency_key": "s3-1",
                    "require_tool": True,
                }
            )
            assert reply["nudged"] is True and reply["tool_not_used"] is False
            assert reply["text"] == "客厅灯没电了。" and len(reply["tool_calls"]) == 1
            assert reply["model_calls"] == 3
            # 提醒后仍不调：如实返回
            stubborn = _chat_script(["我觉得是门锁。", "还是门锁。"])
            host2 = await _open(tmp_path / "two", chat=stubborn, tools=[_device_tool(seen)])
            try:
                answer = await host2.chat(
                    {
                        "session_id": "s4",
                        "text": "哪台设备没电了？",
                        "idempotency_key": "s4-1",
                        "require_tool": True,
                    }
                )
                assert answer["nudged"] is True and answer["tool_not_used"] is True
                assert answer["text"] == "还是门锁。" and answer["tool_calls"] == []
            finally:
                await host2.close()
        finally:
            await host.close()

    _run(case())


def test_exhausted_monthly_budget_refuses_before_calling_the_model(tmp_path):
    async def case():
        script = _chat_script(["不该被问到"])
        host = await _open(
            tmp_path,
            chat=script,
            global_monthly_max_tokens=20_000_000,
            default_mission_max_tokens=20_000_000,
            chat_max_model_calls=64,
        )
        try:
            with pytest.raises(Exception, match="budget_exhausted"):
                await host.chat({"session_id": "s5", "text": "你好", "idempotency_key": "s5-1"})
            assert script.asked == []
        finally:
            await host.close()

    _run(case())


def test_chat_pool_is_separate_from_mission_pools(tmp_path):
    async def case():
        host = await _open(tmp_path, chat=_chat_script(["好"]))
        try:
            status = host.status()
            chat = status["chat"]
            assert chat["available"] is True and chat["profile_id"].endswith("-chat-v1")
            # 任务不能选聊天池；任务池列表里没有它
            assert chat["profile_id"] not in {p["profile_id"] for p in status["context_profiles"]}
            rows = {p["profile_id"]: p for p in status["native_plane"]["profiles"]}
            assert rows[chat["profile_id"]]["owner_mode"] == "STANDALONE_CHAT"
            assert all(
                p["owner_mode"] == "MISSION" for k, p in rows.items() if k != chat["profile_id"]
            )
            # 聊天的执行库与工作区都在自己的目录下
            assert (tmp_path / "host" / "chat").is_dir()
        finally:
            await host.close()

    _run(case())


def test_cancel_takes_effect_before_the_next_model_call(tmp_path):
    """内核只在两次模型调用之间看取消意图：正在线路上的那次调用跑完（并计费），之后不再调模型。
    这里扣住第一次调用（它回一个工具调用）；取消后放行，工具跑完、第二次调用不再发，回合按失败收场。"""

    async def case():
        seen: list[dict[str, Any]] = []
        provider = ScriptedProvider(chat=_chat_script([("device_status", {}), "不该到这里"]))
        provider.held.add("chat")
        host = await OrchestrationHost.open(
            tmp_path / "host",
            principal=Principal("u", "用户"),
            provider=provider,
            counter=FixtureWordCounter(),
            settings=HostSettings(),
            tools=[_device_tool(seen)],
            require_acceptance=False,
        )
        try:
            turn = asyncio.create_task(
                host.chat({"session_id": "s6", "text": "慢慢答", "idempotency_key": "s6-1"})
            )
            await asyncio.wait_for(provider.entered.wait(), timeout=30)
            cancelled = await host.chat_cancel({"session_id": "s6"})
            assert cancelled["cancelled"] is True and cancelled["state"] == "pending"
            provider.release.set()
            reply = await asyncio.wait_for(turn, timeout=60)
            assert reply["state"] == "failed" and reply["error"], reply
            assert reply["model_calls"] == 1 and reply["usage"]["unknown"] is False
            # 没有在跑的回合时：如实说没有
            idle = await host.chat_cancel({"session_id": "s6"})
            assert idle["cancelled"] is False and idle["reason"] == "no_open_turn"
        finally:
            await host.close()

    _run(case())


def test_zero_usage_report_is_unknown_and_settles_at_the_upper_bound(tmp_path):
    """有的线路每次都回 0/0：不当零，按预留上限结清（可以多算不可以少算）。"""

    async def case():
        provider = ScriptedProvider(chat=_chat_script(["好"]))
        provider.usage_tokens = 0
        host = await OrchestrationHost.open(
            tmp_path / "host",
            principal=Principal("u", "用户"),
            provider=provider,
            counter=FixtureWordCounter(),
            settings=HostSettings(),
            require_acceptance=False,
        )
        try:
            reply = await host.chat({"session_id": "z", "text": "你好", "idempotency_key": "z-1"})
            assert reply["state"] == "committed" and reply["usage"]["unknown"] is True
            budget = host.global_budget_status()
            window = HostSettings().context_input_tokens + 131_072
            assert budget["reserved_tokens"] == 0
            assert budget["used_tokens"] == HostSettings().chat_max_model_calls * window
        finally:
            await host.close()

    _run(case())


def test_require_tool_reserves_two_turns(tmp_path):
    """提醒是第二个回合：预留按两回合算，月配额只够一回合时先拒绝。"""

    async def case():
        window = HostSettings().context_input_tokens + 131_072
        cap = HostSettings().chat_max_model_calls * window + 1_000
        host = await _open(
            tmp_path,
            chat=_chat_script(["好"]),
            global_monthly_max_tokens=cap,
            default_mission_max_tokens=cap,
        )
        try:
            with pytest.raises(Exception, match="budget_exhausted"):
                await host.chat(
                    {
                        "session_id": "r",
                        "text": "查",
                        "idempotency_key": "r-1",
                        "require_tool": True,
                    }
                )
            reply = await host.chat({"session_id": "r", "text": "查", "idempotency_key": "r-2"})
            assert reply["state"] == "committed"
        finally:
            await host.close()

    _run(case())


def test_closing_the_host_mid_turn_settles_and_the_session_survives(tmp_path):
    """回合在途时关宿主：预留不能永远占住；重开后同一会话还能聊。"""

    async def case():
        provider = ScriptedProvider(chat=_chat_script(["第一句的答案"]))
        provider.held.add("chat")
        root = tmp_path / "host"
        host = await OrchestrationHost.open(
            root,
            principal=Principal("u", "用户"),
            provider=provider,
            counter=FixtureWordCounter(),
            settings=HostSettings(),
            require_acceptance=False,
        )
        turn = asyncio.create_task(
            host.chat({"session_id": "s", "text": "你好", "idempotency_key": "k1"})
        )
        await asyncio.wait_for(provider.entered.wait(), 30)
        await host.close()
        with pytest.raises(BaseException):
            await asyncio.wait_for(turn, 30)
        provider.release.set()
        provider2 = ScriptedProvider(chat=_chat_script(["重开后的答案"]))
        host2 = await OrchestrationHost.open(
            root,
            principal=Principal("u", "用户"),
            provider=provider2,
            counter=FixtureWordCounter(),
            settings=HostSettings(),
            require_acceptance=False,
        )
        try:
            with host2.orchestrator.store.transaction():
                rows = host2.orchestrator.store.connection.execute(
                    "SELECT subject_id, state FROM budget_reservations"
                    " WHERE subject_id LIKE 'chat:%'"
                ).fetchall()
            assert rows and all(row[1] == "SETTLED" for row in rows), [tuple(r) for r in rows]
            assert host2.global_budget_status()["reserved_tokens"] == 0
            reply = await asyncio.wait_for(
                host2.chat({"session_id": "s", "text": "再问", "idempotency_key": "k2"}), 90
            )
            assert reply["state"] == "committed" and reply["text"] == "重开后的答案", reply
            assert reply["model_calls"] == 1
            # 上一个 Agent 卡在结果不明的模型调用上，被放弃；这句换了新 Agent，如实标出
            assert reply["history_reset"] is True
            third = await asyncio.wait_for(
                host2.chat({"session_id": "s", "text": "再问一次", "idempotency_key": "k3"}), 90
            )
            assert third["history_reset"] is False and third["agent_id"] == reply["agent_id"]
        finally:
            await host2.close()

    _run(case())

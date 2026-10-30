# SPDX-License-Identifier: Apache-2.0
"""用户停止回答之后，同一个会话还要能接着聊（AIPhone 2026-10-30 真机报告的"停止后会话报废"缺陷）。

现象：请求已交给线路、答案没回来时点"停止回答"，那次调用的费用记成"不明"。旧逻辑继续复用
这个执行者，而执行层只要预算里有一笔不明费用，下一次输入在调模型前就按超预算终止——
这个会话从此报废，重启也一样。要求：不明费用留在旧执行者账上（不清零、不重放），
会话换新一代执行者接着聊。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from test_chat import _chat_script, _device_tool

from agent_orchestrator.host import HostSettings, OrchestrationHost, Principal
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=180))


async def _open(tmp_path, provider, seen):
    return await OrchestrationHost.open(
        tmp_path / "host",
        principal=Principal("u", "用户"),
        provider=provider,
        counter=FixtureWordCounter(),
        settings=HostSettings(),
        tools=[_device_tool(seen)],
        require_acceptance=False,
    )


async def _cancel_in_flight(host, provider, session_id: str, key: str) -> dict[str, Any]:
    """发一句话，等它进了线路就取消，线路永远不回来：回合失败、费用不明。"""

    turn = asyncio.create_task(
        host.chat({"session_id": session_id, "text": "慢慢答", "idempotency_key": key})
    )
    await asyncio.wait_for(provider.entered.wait(), timeout=30)
    cancelled = await host.chat_cancel({"session_id": session_id})
    assert cancelled["cancelled"] is True
    reply = await asyncio.wait_for(turn, timeout=90)
    assert reply["state"] == "failed", reply
    assert reply["usage"]["unknown"] is True, reply
    provider.entered.clear()
    return reply


def test_session_continues_after_stop_with_unknown_charge(tmp_path):
    async def case():
        seen: list[dict[str, Any]] = []
        provider = ScriptedProvider(chat=_chat_script(["停止之后还能聊。", "再来也行。"]))
        provider.held.add("chat")
        host = await _open(tmp_path, provider, seen)
        try:
            stopped = await _cancel_in_flight(host, provider, "s", "s-1")
            # 旧 key 重试：返回原来的失败回执，线路上没有第二次请求
            asked_before = len(provider.asked)
            replay = await host.chat(
                {"session_id": "s", "text": "慢慢答", "idempotency_key": "s-1"}
            )
            assert replay["state"] == "failed" and replay["turn_id"] == stopped["turn_id"]
            assert len(provider.asked) == asked_before
            # 新 key：会话换一代执行者接着聊，不再报超预算
            provider.held.clear()
            reply = await host.chat({"session_id": "s", "text": "你好", "idempotency_key": "s-2"})
            assert reply["state"] == "committed", reply
            assert reply["text"] == "停止之后还能聊。"
            assert reply["history_reset"] is True
            assert reply["executor_replaced_reason"] == "unknown_charge_after_cancel"
            assert reply["agent_id"] != stopped["agent_id"]
            # 旧执行者的不明费用还在账上；新执行者是干净的
            from simple_harness.contracts.identity import RunId

            runtime = host.chat_service._pool().runtime
            old_snapshot = runtime.uow.read_provider_budget(
                RunId(runtime.binding(stopped["agent_id"]).run_id)
            )
            new_snapshot = runtime.uow.read_provider_budget(
                RunId(runtime.binding(reply["agent_id"]).run_id)
            )
            assert old_snapshot.has_unknown_charge is True
            assert new_snapshot.has_unknown_charge is False
            # 再来一句：同一代执行者继续，不再换代
            again = await host.chat({"session_id": "s", "text": "再来", "idempotency_key": "s-3"})
            assert again["state"] == "committed" and again["agent_id"] == reply["agent_id"]
            assert again["history_reset"] is False
        finally:
            await host.close()

    _run(case())


def test_replacement_survives_host_restart_and_happens_once(tmp_path):
    async def case():
        seen: list[dict[str, Any]] = []
        provider = ScriptedProvider(chat=_chat_script(["重启后还能聊。", "再来也行。"]))
        provider.held.add("chat")
        host = await _open(tmp_path, provider, seen)
        try:
            stopped = await _cancel_in_flight(host, provider, "s", "s-1")
        finally:
            await host.close()
        provider.held.clear()
        host = await _open(tmp_path, provider, seen)
        try:
            reply = await host.chat({"session_id": "s", "text": "你好", "idempotency_key": "s-2"})
            assert reply["state"] == "committed" and reply["history_reset"] is True
            assert reply["agent_id"] != stopped["agent_id"]
            again = await host.chat({"session_id": "s", "text": "再来", "idempotency_key": "s-3"})
            assert again["agent_id"] == reply["agent_id"]
        finally:
            await host.close()

    _run(case())


def test_stop_does_not_touch_a_running_delegation(tmp_path):
    """停止一句闲聊不影响同会话里在跑的后台任务：任务照常进收件箱。"""

    from agent_orchestrator.host import Session

    async def case():
        seen: list[dict[str, Any]] = []
        provider = ScriptedProvider(chat=_chat_script(["好的。"]))
        session = await Session.open(
            tmp_path / "host",
            Principal("u", "用户"),
            provider=provider,
            counter=FixtureWordCounter(),
            tools=[_device_tool(seen)],
            require_acceptance=False,
        )
        try:
            # 直接登记一条委派（不经模型），模拟"会话里已有一个在跑的后台任务"
            receipt = await session._delegations.delegate(
                "turn-earlier",
                "call-1",
                {"goal": "Write a report", "success_criteria": ["file:report.md"]},
            )
            provider.held.add("chat")
            chatting = asyncio.create_task(session.send("慢慢答", key="s-1"))
            await asyncio.wait_for(provider.entered.wait(), timeout=30)
            await session.interrupt()
            stopped = await asyncio.wait_for(chatting, timeout=90)
            assert stopped["state"] == "failed"
            [task] = session.tasks()
            assert task["mission_id"] == receipt["mission_id"]
            assert task["status"] != "CANCELLED"
            provider.held.clear()
            reply = await session.send("你好", key="s-2")
            assert reply["state"] == "committed"
        finally:
            await session.close()

    _run(case())


@pytest.mark.parametrize("moment", ["before_handoff", "in_flight"])
def test_stop_at_each_moment_then_new_key_continues(tmp_path, moment):
    """停止发生在线路接纳前后，之后同会话新 key 都能继续；只有在途那种费用才是不明。"""

    async def case():
        seen: list[dict[str, Any]] = []
        provider = ScriptedProvider(chat=_chat_script(["之后还能聊。"]))
        host = await _open(tmp_path, provider, seen)
        try:
            if moment == "in_flight":
                provider.held.add("chat")
                stopped = await _cancel_in_flight(host, provider, "s", "s-1")
                provider.held.clear()
                assert stopped["usage"]["unknown"] is True
            else:
                idle = await host.chat_cancel({"session_id": "s"})
                assert idle["cancelled"] is False and idle["reason"] == "no_open_turn"
            reply = await host.chat({"session_id": "s", "text": "你好", "idempotency_key": "s-2"})
            assert reply["state"] == "committed"
            assert reply["history_reset"] is (moment == "in_flight")
        finally:
            await host.close()

    _run(case())

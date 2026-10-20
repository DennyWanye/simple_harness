# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""逐字输出（AgentOS 迁移第 5 块）：模型正在生成的正文片段交给订阅者。

- 流解析器：每段正文叫一次回调；思考片段、工具参数不叫。
- OpenAI 协议提供方：真流（假传输）里 ``delta_sink`` 按顺序收到正文片段。
- 输出流：穿过回退 / 边际学习装饰器装到最里层；按运行号分发；``seq`` 按 generation 递增；
  一次调用结束发 ``end``、抛错发 ``error``；坏订阅者不影响调用。
- 聊天：``host.chat(..., on_delta=)`` 按顺序收到片段，拼起来等于正文；``first_output_ms`` 有值；
  全局订阅能看到带作用域的事件。
- 任务：执行者的片段带 mission_id / subject_id。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from agent_orchestrator.host import HostSettings, OrchestrationHost, Principal
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider
from agent_orchestrator.runtime.delta_router import OutputStream, StreamSignals, leaf_providers
from agent_orchestrator.runtime.model_fallback import FallbackProvider
from agent_orchestrator.runtime.openai_upper_bound_meter import (
    MarginLearningProvider,
    OpenAIUpperBoundCounter,
    RelayMargin,
)
from simple_harness.contracts import Message, MessageRole, RequestId
from simple_harness.providers import (
    CancelToken,
    OpenAICompatibleProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderServerError,
    Secret,
)
from simple_harness.providers.chat_stream import ChatStream


def _chunk(delta=None, *, finish=None, usage=None):
    return {
        "id": "response-1",
        "model": "fixture",
        "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
        "usage": usage,
    }


def _event(value) -> str:
    return "data: " + json.dumps(value, ensure_ascii=False) + "\n\n"


_USAGE = {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6}


def test_chat_stream_reports_content_pieces_only():
    pieces: list[str] = []
    stream = ChatStream(on_content=pieces.append)
    for line in (
        _event(_chunk({"role": "assistant", "reasoning_content": "想一想"}))
        + _event(_chunk({"content": "你"}))
        + _event(_chunk({"content": ""}))
        + _event(_chunk({"content": "好", "tool_calls": []}))
        + _event(_chunk(finish="stop", usage=_USAGE))
        + "data: [DONE]\n\n"
    ).split("\n"):
        stream.line(line)
    assert pieces == ["你", "好"] and stream.content.getvalue() == "你好"


class _Bytes(httpx.AsyncByteStream):
    def __init__(self, data: str) -> None:
        self.data = data.encode()

    async def __aiter__(self):
        for offset in range(0, len(self.data), 5):
            yield self.data[offset : offset + 5]

    async def aclose(self) -> None:
        return None


def test_openai_provider_streams_content_pieces_to_the_sink():
    data = (
        _event(_chunk({"role": "assistant", "reasoning_content": "私下想"}))
        + _event(_chunk({"content": "灯"}))
        + _event(_chunk({"content": "没电"}))
        + _event(_chunk({"content": "了。"}))
        + _event(_chunk(finish="stop", usage=_USAGE))
        + "data: [DONE]\n\n"
    )

    def transport(request):
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=_Bytes(data)
        )

    async def case():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            provider = OpenAICompatibleProvider(
                client, "https://fixture.invalid/v1", "fixture", Secret("secret"), stream=True
            )
            got: list[tuple[str, str]] = []
            provider.delta_sink = lambda request, text: got.append(
                (str(request.request_id.value), text)
            )
            request = ProviderRequest(
                RequestId("run-9:provider-turn:1"), (Message("user", "哪台没电？"),)
            )
            response = await provider.invoke(request, cancel=CancelToken())
            assert response.message.content == "灯没电了。"
            assert got == [
                ("run-9:provider-turn:1", "灯"),
                ("run-9:provider-turn:1", "没电"),
                ("run-9:provider-turn:1", "了。"),
            ]
            # 坏回调不影响这次调用
            provider.delta_sink = lambda request, text: 1 / 0
            response = await provider.invoke(request, cancel=CancelToken())
            assert response.message.content == "灯没电了。"

    asyncio.run(case())


class _Leaf:
    delta_sink = None

    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.target = type("T", (), {"adapter_key": "openai-compatible.chat-completions.v1"})()
        self.name, self.fail = name, fail

    async def invoke(self, request, *, cancel):
        if self.fail:
            raise ProviderServerError(public_message="boom")
        if self.delta_sink is not None:
            self.delta_sink(request, "你")
            self.delta_sink(request, "好")
        return ProviderResponse(
            request.request_id, Message(MessageRole.ASSISTANT, "你好"), finish_reason="stop"
        )


def _request(run: str, turn: int = 1) -> ProviderRequest:
    return ProviderRequest(RequestId(f"{run}:provider-turn:{turn}"), (Message("user", "x"),))


def test_output_stream_attaches_through_wrappers_and_signals_end_and_error():
    primary, backup = _Leaf("a"), _Leaf("b")
    counter = OpenAIUpperBoundCounter("gpt-6.1-sol", margin=RelayMargin())
    wrapped = MarginLearningProvider(
        FallbackProvider(primary, backup, primary_model="gpt-6.1-sol", fallback_model="x"),
        counter,
    )
    assert {leaf.name for leaf in leaf_providers(wrapped)} == {"a", "b"}
    stream = OutputStream(resolver=lambda run: {"scope": "mission", "mission_id": "m-" + run})
    provider = stream.attach(wrapped)
    assert isinstance(provider, StreamSignals) and provider.target is primary.target
    assert primary.delta_sink is stream and backup.delta_sink is stream
    assert stream.attach(provider) is provider  # 已经套过的不再套

    everything: list[dict[str, Any]] = []
    mine: list[dict[str, Any]] = []
    stream.subscribe(everything.append)
    unsubscribe = stream.subscribe(mine.append, run_id="run-1")
    stream.subscribe(lambda event: 1 / 0, run_id="run-1")  # 坏订阅者：只记日志

    async def case():
        response = await provider.invoke(_request("run-1"), cancel=CancelToken())
        assert response.message.content == "你好"
        await provider.invoke(_request("run-2"), cancel=CancelToken())

    asyncio.run(case())
    kinds = [(e["kind"], e.get("seq"), e.get("text")) for e in mine]
    assert kinds == [("piece", 1, "你"), ("piece", 2, "好"), ("end", 2, None)]
    assert mine[0]["v"] == 1 and mine[0]["generation"] == "run-1:provider-turn:1"
    assert mine[0]["scope"] == "mission" and mine[0]["mission_id"] == "m-run-1"
    assert mine[2]["finish"] == "stop" and mine[2]["tool_calls"] == 0
    assert [e["run_id"] for e in everything] == ["run-1"] * 3 + ["run-2"] * 3
    # 新一次调用 seq 从 1 重数
    assert [e["seq"] for e in everything if e["run_id"] == "run-2"] == [1, 2, 2]
    unsubscribe()
    primary.fail = True

    async def failing():
        with pytest.raises(ProviderServerError):
            await StreamSignals(primary, stream).invoke(_request("run-1", 2), cancel=CancelToken())

    asyncio.run(failing())
    assert mine[-1]["kind"] == "end"  # 退订后没收到
    assert everything[-1]["kind"] == "error" and everything[-1]["error"] == "provider_server_error"
    assert everything[-1]["generation"] == "run-1:provider-turn:2" and everything[-1]["seq"] == 0


class _StreamingScripted(ScriptedProvider):
    """脚本提供方：有 ``delta_sink`` 时先把正文按字送出去，再给回执。"""

    delta_sink = None

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        response = await super().invoke(request, cancel=cancel)
        content = response.message.content
        if self.delta_sink is not None and isinstance(content, str):
            for piece in content:
                self.delta_sink(request, piece)
        return response


def test_chat_turn_streams_pieces_in_order(tmp_path):
    async def case():
        provider = _StreamingScripted(chat=lambda request: "客厅灯没电了。")
        host = await OrchestrationHost.open(
            tmp_path / "host",
            principal=Principal("u", "用户"),
            provider=provider,
            counter=FixtureWordCounter(),
            settings=HostSettings(),
            require_acceptance=False,
        )
        try:
            assert provider.delta_sink is host.output_stream
            everything: list[dict[str, Any]] = []
            host.output_stream.subscribe(everything.append)
            pieces: list[dict[str, Any]] = []
            reply = await host.chat(
                {"session_id": "s1", "text": "哪台没电？", "idempotency_key": "s1-1"},
                on_delta=pieces.append,
            )
            assert reply["text"] == "客厅灯没电了。"
            assert "".join(p["text"] for p in pieces) == "客厅灯没电了。"
            assert [p["seq"] for p in pieces] == list(range(1, len("客厅灯没电了。") + 1))
            assert all(p["session_id"] == "s1" and p["turn_id"] == reply["turn_id"] for p in pieces)
            assert reply["first_output_ms"] is not None and 0 <= reply["first_output_ms"]
            assert reply["first_output_ms"] <= reply["total_ms"]
            # 全局订阅：带作用域的事件，最后一条是结束信号
            chat_events = [e for e in everything if e["run_id"] == reply["agent_id"]]
            assert chat_events[0]["scope"] == "chat" and chat_events[0]["session_id"] == "s1"
            assert chat_events[-1]["kind"] == "end" and chat_events[-1]["finish"] == "stop"
            # 回合结束后不再有本回合的订阅：再来片段 on_delta 收不到
            host.output_stream(_request(reply["agent_id"], 9), "多余")
            assert len(pieces) == len("客厅灯没电了。")
            # 不要片段也行
            again = await host.chat(
                {"session_id": "s1", "text": "再说一次", "idempotency_key": "s1-2"}
            )
            assert again["first_output_ms"] is None
        finally:
            await host.close()

    asyncio.run(asyncio.wait_for(case(), timeout=120))


def test_mission_worker_pieces_carry_the_mission_scope(tmp_path):
    from agent_orchestrator.host.offline import worker_reply

    class _Worker(_StreamingScripted):
        pass

    async def case():
        provider = _Worker(worker=worker_reply)
        host = await OrchestrationHost.open(
            tmp_path / "host",
            principal=Principal("u", "用户"),
            provider=provider,
            counter=FixtureWordCounter(),
            settings=HostSettings(),
            auto_mode=True,
            require_acceptance=False,
        )
        try:
            events: list[dict[str, Any]] = []
            host.output_stream.subscribe(events.append)
            mission_id = host.create_mission(
                {
                    "goal": "写一份 NOTES.md 列三条要点。",
                    "success_criteria": ["file:NOTES.md"],
                    "idempotency_key": "stream-1",
                }
            )["mission_id"]
            final = await host.run_until_settled(mission_id, timeout=240)
            assert final["snapshot"]["mission"]["status"] == "COMPLETED", final["snapshot"][
                "mission"
            ]
            scoped = [e for e in events if e.get("scope") == "mission"]
            assert scoped, [e["scope"] for e in events][:10]
            assert all(e["mission_id"] == mission_id for e in scoped)
            assert any(e["kind"] == "piece" and e.get("view") == "work" for e in scoped)
            assert all(e["kind"] in {"piece", "end", "error"} for e in events)
        finally:
            await host.close()

    asyncio.run(asyncio.wait_for(case(), timeout=300))


def test_global_subscription_turn_ids_follow_each_turn(tmp_path):
    """同一会话三句（第三句带提醒回合）：全局订阅收到的聊天片段，回合号等于各自回执里的回合号。"""

    async def case():
        answers = iter(["一", "二", "三没查", "三查了"])
        provider = _StreamingScripted(chat=lambda request: next(answers))
        host = await OrchestrationHost.open(
            tmp_path / "host",
            principal=Principal("u", "用户"),
            provider=provider,
            counter=FixtureWordCounter(),
            settings=HostSettings(),
            require_acceptance=False,
        )
        try:
            events: list[dict[str, Any]] = []
            host.output_stream.subscribe(events.append)
            one = await host.chat({"session_id": "s", "text": "一", "idempotency_key": "k1"})
            two = await host.chat({"session_id": "s", "text": "二", "idempotency_key": "k2"})
            three = await host.chat(
                {"session_id": "s", "text": "三", "idempotency_key": "k3", "require_tool": True}
            )
            assert three["nudged"] is True
            pieces = [e for e in events if e["kind"] == "piece" and e["scope"] == "chat"]
            by_text = {e["text"]: e["turn_id"] for e in pieces}
            assert by_text["一"] == one["turn_id"]
            assert by_text["二"] == two["turn_id"]
            assert by_text["三"] == three["turn_id"]  # 提醒回合的片段带提醒回合自己的回合号
            assert by_text["查"].endswith(":nudge")
            assert all(e["session_id"] == "s" for e in pieces)
        finally:
            await host.close()

    asyncio.run(asyncio.wait_for(case(), timeout=120))

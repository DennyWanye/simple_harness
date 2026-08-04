from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest


class _FakeWebSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        if self.fail:
            raise RuntimeError("socket closed")
        self.sent.append(message)


@pytest.mark.asyncio
async def test_broadcast_control_is_best_effort(monkeypatch):
    import main

    ok = _FakeWebSocket()
    bad = _FakeWebSocket(fail=True)
    monkeypatch.setattr(main, "_control_connections", {"ok": ok, "bad": bad})

    await main._broadcast_control({"type": "ppt_outline_proposed", "payload": {"outline_id": "o1"}})

    assert ok.sent == [{"type": "ppt_outline_proposed", "payload": {"outline_id": "o1"}}]


@pytest.mark.asyncio
async def test_workflow_outline_card_resumes_generic_decision_without_blocking(monkeypatch):
    import main

    oid = "workflow:run-1:0"
    decision = SimpleNamespace(
        decision_id="decision-1",
        run_id="run-1",
        kind="ppt_outline",
        prompt={"outline_id": oid},
        nonce="nonce-1",
        version=3,
    )
    resolved = []

    class _HumanStore:
        async def list_open_decisions(self, *, run_id):
            assert run_id == "run-1"
            return [decision]

    class _RunStore:
        async def get_run(self, run_id):
            assert run_id == "run-1"
            return {"session_id": "sid-1"}

    class _Service:
        human_store = _HumanStore()
        run_store = _RunStore()

        async def resolve_decision(self, decision_id, **kwargs):
            resolved.append((decision_id, kwargs))

    service = _Service()
    monkeypatch.setattr(
        main.service_context,
        "get",
        lambda name: service if name == "workflow_service" else None,
    )
    notices = []

    async def notify(sid, text):
        notices.append((sid, text))

    monkeypatch.setattr(main, "_ppt_notify_chat_bubble", notify)
    ws = _FakeWebSocket()
    monkeypatch.setattr(main, "_control_connections", {"panel": ws})

    handled = await main._handle_control_ws_message(
        {"type": "ppt_outline_decision", "payload": {"outline_id": oid, "action": "accept"}},
        session_id="sid-1",
        ws=ws,
    )
    await asyncio.sleep(0)

    assert handled is True
    assert resolved == [
        (
            "decision-1",
            {"nonce": "nonce-1", "response": {"action": "accept", "feedback": "", "reuse_id": None}, "expected_version": 3},
        )
    ]
    assert notices == [("sid-1", "大纲已确认，正在生成 PPT。完成后文件会发送到当前会话。")]
    assert ws.sent == [{
        "type": "ppt_outline_resolved",
        "payload": {
            "outline_id": oid,
            "session_id": "sid-1",
            "decision_status": main._ppt_outline_decision_notice("accept"),
        },
    }]


@pytest.mark.asyncio
async def test_workflow_outline_card_uses_kernel_decision_fence(monkeypatch):
    import main

    signalled = []

    async def signal(session_id, payload, response):
        signalled.append((session_id, payload, response))
        return SimpleNamespace(accepted=True, duplicate=False)

    async def legacy(*_args, **_kwargs):
        pytest.fail("canonical decision must not query the legacy workflow store")

    notices = []

    async def notify(sid, text):
        notices.append((sid, text))

    monkeypatch.setattr(main, "_signal_product_harness_decision", signal)
    monkeypatch.setattr(main, "_resolve_workflow_ppt_outline", legacy)
    monkeypatch.setattr(main, "_ppt_notify_chat_bubble", notify)
    monkeypatch.setattr(main, "_control_connections", {})

    payload = {
        "outline_id": "workflow:run-1:0",
        "action": "accept",
        "session_id": "sid-owner",
        "run_id": "run-1",
        "decision_id": "decision-1",
        "nonce": "nonce-1",
        "version": 4,
    }
    handled = await main._handle_control_ws_message(
        {"type": "ppt_outline_decision", "payload": payload},
        session_id="sid-1",
        ws=_FakeWebSocket(),
    )

    assert handled is True
    assert signalled == [
        (
            "sid-owner",
            payload,
            {"action": "accept", "feedback": "", "reuse_id": None},
        )
    ]
    assert notices == [
        ("sid-owner", main._ppt_outline_decision_notice("accept"))
    ]


@pytest.mark.asyncio
async def test_workflow_outline_rejected_fence_does_not_drop_control_socket(monkeypatch):
    import main

    async def reject(*_args, **_kwargs):
        raise RuntimeError("actor does not own the run session")

    monkeypatch.setattr(main, "_signal_product_harness_decision", reject)
    monkeypatch.setattr(main, "_control_connections", {})

    handled = await main._handle_control_ws_message(
        {
            "type": "ppt_outline_decision",
            "payload": {
                "outline_id": "workflow:run-1:0",
                "action": "accept",
                "session_id": "sid-owner",
                "run_id": "run-1",
                "decision_id": "decision-1",
                "nonce": "nonce-1",
                "version": 0,
            },
        },
        session_id="message-panel-main",
        ws=_FakeWebSocket(),
    )

    assert handled is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "feedback", "expected"),
    [
        ("accept", "", "大纲已确认，正在生成 PPT。完成后文件会发送到当前会话。"),
        (
            "modify",
            "把第二页改成时间线",
            "收到修改意见，正在修改 PPT 大纲。修改完成后会在当前会话展示新版本，请再次确认。",
        ),
        ("reuse", "", "已采用历史大纲，正在生成 PPT。完成后文件会发送到当前会话。"),
        ("cancel", "", "已取消本次 PPT 生成。"),
    ],
)
async def test_workflow_outline_decision_uses_action_specific_notice(
    monkeypatch, action, feedback, expected
):
    import main

    oid = "workflow:run-action:0"
    decision = SimpleNamespace(
        decision_id="decision-action",
        run_id="run-action",
        kind="ppt_outline",
        prompt={"outline_id": oid},
        nonce="nonce-action",
        version=1,
    )

    class _HumanStore:
        async def list_open_decisions(self, *, run_id):
            return [decision]

    class _RunStore:
        async def get_run(self, run_id):
            return {"session_id": "sid-action"}

    class _Service:
        human_store = _HumanStore()
        run_store = _RunStore()

        async def resolve_decision(self, *args, **kwargs):
            return None

    monkeypatch.setattr(
        main.service_context,
        "get",
        lambda name: _Service() if name == "workflow_service" else None,
    )
    notices = []

    async def notify(sid, text):
        notices.append((sid, text))

    monkeypatch.setattr(main, "_ppt_notify_chat_bubble", notify)
    monkeypatch.setattr(main, "_control_connections", {})

    handled = await main._handle_control_ws_message(
        {
            "type": "ppt_outline_decision",
            "payload": {"outline_id": oid, "action": action, "feedback": feedback},
        },
        session_id="sid-action",
        ws=_FakeWebSocket(),
    )
    await asyncio.sleep(0)

    assert handled is True
    assert notices == [("sid-action", expected)]


@pytest.mark.asyncio
async def test_workflow_outline_modify_rejects_empty_feedback_without_resolving(monkeypatch):
    import main

    started = []
    notices = []

    async def start(*args, **kwargs):
        started.append((args, kwargs))
        return "sid-1"

    async def notify(sid, text):
        notices.append((sid, text))

    monkeypatch.setattr(main, "_resolve_workflow_ppt_outline", start)
    monkeypatch.setattr(main, "_ppt_notify_chat_bubble", notify)

    assert await main._handle_control_ws_message(
        {
            "type": "ppt_outline_decision",
            "payload": {
                "outline_id": "workflow:run-empty:0",
                "action": "modify",
                "feedback": "   ",
            },
        },
        session_id="sid-1",
        ws=_FakeWebSocket(),
    )
    assert started == []
    assert notices == [("sid-1", "请先填写需要修改的大纲内容，再提交修改。")]


@pytest.mark.asyncio
async def test_workflow_outline_notify_projects_session_card_idempotently(monkeypatch):
    import main

    rows = []

    class _SDB:
        async def append_message(self, **kwargs):
            rows.append(kwargs)
            return 1

    monkeypatch.setattr(
        main.service_context,
        "get",
        lambda name: _SDB() if name == "session_db" else None,
    )
    monkeypatch.setattr(main, "list_history", lambda sid, limit=20: [])
    ws = _FakeWebSocket()
    monkeypatch.setattr(main, "_control_connections", {"panel": ws})

    await main._ppt_workflow_outline_notify(
        {
            "outline_id": "workflow:run-1:0",
            "session_id": "sid-1",
            "topic": "Harness",
            "outline_md": "# Outline",
            "sources_count": 4,
            "no_research": False,
        }
    )

    assert ws.sent[0]["type"] == "ppt_outline_proposed"
    assert ws.sent[0]["payload"]["session_id"] == "sid-1"
    assert rows[0]["workflow_event_id"] == "ppt-outline:workflow:run-1:0"


@pytest.mark.asyncio
async def test_ppt_notify_chat_bubble_persists_before_sending(monkeypatch):
    import main

    default_ws = _FakeWebSocket()
    panel_ws = _FakeWebSocket()
    monkeypatch.setattr(main, "_control_connections", {
        "default": default_ws,
        "message-panel-main": panel_ws,
    })
    rows = []
    vectors = []

    class _SDB:
        async def append_message(self, **kwargs):
            rows.append(kwargs)
            return 456

    class _VW:
        async def enqueue(self, msg_id, text):
            vectors.append((msg_id, text))

    def fake_get(name):
        if name == "session_db":
            return _SDB()
        if name == "vector_worker":
            return _VW()
        return None

    monkeypatch.setattr(main.service_context, "get", fake_get)

    await main._ppt_notify_chat_bubble("sid-1", "PPT 没做成：LLM HTTP 401 Unauthorized")

    assert rows == [
        {
            "session_id": "sid-1",
            "role": "assistant",
            "content": "PPT 没做成：LLM HTTP 401 Unauthorized",
        }
    ]
    assert vectors == [(456, "PPT 没做成：LLM HTTP 401 Unauthorized")]
    assert default_ws.sent[0]["type"] == "chat_response"
    assert panel_ws.sent == default_ws.sent
    assert default_ws.sent[0]["payload"]["session_id"] == "sid-1"


def test_ppt_outline_startup_expire_is_called(monkeypatch):
    import main

    calls = []
    monkeypatch.setattr(main, "expire_dangling_proposed", lambda: calls.append("expire") or 2)

    assert main._expire_ppt_outline_dangling_for_startup() == 2
    assert calls == ["expire"]


def test_ppt_pro_startup_wires_only_durable_workflow(monkeypatch):
    import main

    seen = {}
    monkeypatch.setattr(main.ppt_tools, "set_ppt_pro_services", lambda **kwargs: seen.update(kwargs))

    main._configure_ppt_production()

    assert set(seen) == {"workflow_starter"}
    assert seen["workflow_starter"] is None

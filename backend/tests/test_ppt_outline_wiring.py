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
async def test_ppt_outline_propose_accept_broadcasts_history_and_marks_status(monkeypatch):
    import main
    from deskpet.tools.ppt_tools import SlideOutline

    waiters = main.PPTOutlineWaiters()
    monkeypatch.setattr(main, "_PPT_OUTLINE_WAITERS", waiters)
    monkeypatch.setattr(main, "_ppt_pro_cfg", lambda: SimpleNamespace(outline_history=True, confirm_timeout_s=1))
    monkeypatch.setattr(main.uuid, "uuid4", lambda: SimpleNamespace(hex="outline-1"))

    saved = []
    statuses = []
    rows = []
    vectors = []
    monkeypatch.setattr(main, "save_outline", lambda *a, **k: saved.append(a) or True)
    monkeypatch.setattr(main, "list_history", lambda sid, limit=20: [{"outline_id": "old"}])
    monkeypatch.setattr(main, "mark_status", lambda oid, status: statuses.append((oid, status)) or True)

    class _SDB:
        async def append_message(self, **kwargs):
            rows.append(kwargs)
            return 789

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

    ws = _FakeWebSocket()
    monkeypatch.setattr(main, "_control_connections", {"panel": ws})

    task = asyncio.create_task(
        main._ppt_outline_propose(
            "sid-1",
            topic="Topic",
            slides=[SlideOutline(title="Slide 1")],
            sources_count=3,
            outline_md="# Outline",
            no_research=False,
        )
    )
    await asyncio.sleep(0)

    assert ws.sent[0]["type"] == "ppt_outline_proposed"
    payload = ws.sent[0]["payload"]
    assert payload["outline_id"] == "outline-1"
    assert payload["session_id"] == "sid-1"
    assert payload["history"] == [{"outline_id": "old"}]
    assert saved and saved[0][0:4] == ("outline-1", "sid-1", "Topic", saved[0][3])
    assert rows == [
        {
            "session_id": "sid-1",
            "role": "assistant",
            "content": "PPT 大纲确认 · Topic\n\n# Outline",
        }
    ]
    assert vectors == [(789, "PPT 大纲确认 · Topic\n\n# Outline")]

    assert waiters.resolve("outline-1", {"action": "accept"})
    assert await task == {"action": "accept"}
    assert statuses == [("outline-1", "accepted")]
    assert waiters.pop("outline-1") is None


@pytest.mark.asyncio
async def test_ppt_outline_propose_timeout_cancels(monkeypatch):
    import main
    from deskpet.tools.ppt_tools import SlideOutline

    monkeypatch.setattr(main, "_PPT_OUTLINE_WAITERS", main.PPTOutlineWaiters())
    monkeypatch.setattr(main, "_ppt_pro_cfg", lambda: SimpleNamespace(outline_history=False, confirm_timeout_s=0.01))
    monkeypatch.setattr(main.uuid, "uuid4", lambda: SimpleNamespace(hex="outline-timeout"))
    monkeypatch.setattr(main, "mark_status", lambda *a, **k: True)
    monkeypatch.setattr(main, "_control_connections", {"panel": _FakeWebSocket()})

    decision = await main._ppt_outline_propose(
        "sid-1",
        topic="Topic",
        slides=[SlideOutline(title="Slide 1")],
        sources_count=0,
        outline_md="# Outline",
        no_research=True,
    )

    assert decision == {"action": "cancel"}


@pytest.mark.asyncio
async def test_control_ws_ppt_outline_decision_resolves_once(monkeypatch):
    import main

    waiters = main.PPTOutlineWaiters()
    fut = asyncio.get_running_loop().create_future()
    waiters.add("outline-1", fut)
    monkeypatch.setattr(main, "_PPT_OUTLINE_WAITERS", waiters)
    ws = _FakeWebSocket()
    monkeypatch.setattr(main, "_control_connections", {"panel": ws})

    handled = await main._handle_control_ws_message(
        {"type": "ppt_outline_decision", "payload": {"outline_id": "outline-1", "action": "accept"}},
        session_id="sid-1",
        ws=ws,
    )

    assert handled is True
    assert fut.result() == {"action": "accept", "feedback": "", "reuse_id": None}
    assert ws.sent == [
        {"type": "ppt_outline_resolved", "payload": {"outline_id": "outline-1"}}
    ]

    assert await main._handle_control_ws_message(
        {"type": "ppt_outline_decision", "payload": {"outline_id": "outline-1", "action": "cancel"}},
        session_id="sid-1",
        ws=ws,
    )
    assert len(ws.sent) == 1


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
    monkeypatch.setattr(main, "_PPT_OUTLINE_WAITERS", main.PPTOutlineWaiters())
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
    assert ws.sent == [{"type": "ppt_outline_resolved", "payload": {"outline_id": oid}}]


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
    monkeypatch.setattr(main, "_PPT_OUTLINE_WAITERS", main.PPTOutlineWaiters())
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
    monkeypatch.setattr(main, "_PPT_OUTLINE_WAITERS", main.PPTOutlineWaiters())

    async def start(*args, **kwargs):
        started.append((args, kwargs))
        return "sid-1"

    async def notify(sid, text):
        notices.append((sid, text))

    monkeypatch.setattr(main, "_start_workflow_ppt_outline_resume", start)
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
async def test_ppt_artifact_push_uses_tool_result_and_sessiondb(monkeypatch):
    import main

    default_ws = _FakeWebSocket()
    panel_ws = _FakeWebSocket()
    monkeypatch.setattr(main, "_control_connections", {
        "default": default_ws,
        "message-panel-main": panel_ws,
    })
    rows = []

    class _SDB:
        async def append_message(self, **kwargs):
            rows.append(kwargs)
            return 123

    monkeypatch.setattr(main.service_context, "get", lambda name: _SDB() if name == "session_db" else None)

    artifacts = [{"kind": "file", "path": "deck.pptx"}]
    await main._ppt_artifact_push("sid-1", artifacts, "done")

    assert default_ws.sent[0]["type"] == "tool_result"
    assert panel_ws.sent == default_ws.sent
    payload = default_ws.sent[0]["payload"]
    assert payload["tool"] == "ppt_pro"
    assert payload["artifacts"] == artifacts
    assert payload["session_id"] == "sid-1"
    assert payload["text"] == "done"
    assert json.loads(rows[0]["content"])["artifacts"] == artifacts
    assert rows[0]["role"] == "tool"


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


def test_ppt_pro_startup_wires_all_services(monkeypatch):
    import main

    seen = {}
    monkeypatch.setattr(main.ppt_tools, "set_ppt_pro_services", lambda **kwargs: seen.update(kwargs))

    main._wire_ppt_pro_services_for_startup()

    assert seen["outline_propose"] is main._ppt_outline_propose
    assert seen["notifier"] is main._ppt_notify_chat_bubble
    assert seen["artifact_pusher"] is main._ppt_artifact_push
    assert seen["receipt_reporter"] is main._ppt_receipt_report
    assert callable(seen["run_blocking"])

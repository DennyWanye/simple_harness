"""Control ingress must remain responsive during a model send."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from host import control_session
from host.control_chat import _cc_chat


@pytest.mark.asyncio
async def test_send_does_not_block_interrupt_and_preserves_request_identity(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def handle(message, body):
        calls.append((message, body))
        if message == "session_send":
            entered.set()
            await release.wait()
        return {"state": "committed", "text": "done"}
    bridge = SimpleNamespace(handle=handle)
    service = SimpleNamespace(session_bridge=bridge, status=lambda: {"state": "available"})
    monkeypatch.setattr(control_session, "service_context", SimpleNamespace(get=lambda _: service))
    conn = SimpleNamespace(ws=SimpleNamespace(send_json=AsyncMock()))
    await _cc_chat(conn, {"request_id": "wire", "payload": {"text": "hi", "request_id": "input"}}, "chat")
    await asyncio.wait_for(entered.wait(), 1)
    try:
        await control_session._cc_session(conn, {"request_id": "stop", "payload": {}}, "session_interrupt")
        assert calls == [("session_send", {"text": "hi", "idempotency_key": "input"}), ("session_interrupt", {})]
        assert conn.ws.send_json.call_args.args[0]["payload"]["request_id"] == "stop"
    finally:
        release.set()
        await asyncio.gather(*control_session._sends)
    frames = [call.args[0] for call in conn.ws.send_json.call_args_list]
    assert frames[-1]["type"] == "session_send_response"
    assert frames[-1]["payload"]["request_id"] == "wire"


@pytest.mark.asyncio
async def test_session_messages_are_registered_on_the_real_control_router():
    from host.control_channel import _CONTROL_ROUTES
    from deskpet.orchestration.session_bridge import SESSION_MESSAGES

    assert (SESSION_MESSAGES, control_session._cc_session) in _CONTROL_ROUTES


@pytest.mark.asyncio
async def test_retired_primary_queue_cannot_start_another_text_engine():
    from host.control_memory import _cc_human_memory_request

    ws = SimpleNamespace(send_json=AsyncMock())
    await _cc_human_memory_request(SimpleNamespace(ws=ws), {
        "type": "human_memory_request", "request_id": "old", "operation": "queue.enqueue",
        "request": {"text": "legacy input"},
    }, "human_memory_request")
    assert ws.send_json.call_args.args[0]["payload"]["error"]["code"] == "human_memory_operation_unavailable"

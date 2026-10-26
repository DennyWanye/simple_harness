"""Exercise the desktop transport against the public Session contract."""

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from deskpet.orchestration.session_bridge import DesktopSessionBridge, project_session_event


@pytest.fixture
def bridge(tmp_path):
    session = SimpleNamespace(
        send=AsyncMock(return_value={"turn_id": "t", "state": "committed", "text": "hello"}),
        respond=Mock(return_value={"ok": True}),
        tasks=Mock(return_value=[]), task=Mock(return_value={"mission_id": "m", "status": "ACTIVE"}),
        cancel_task=Mock(return_value={"mission_id": "m", "cancel_requested": True}),
        inbox=Mock(return_value=[]), ack=Mock(return_value={"event_id": "e", "acked": True}),
        history=Mock(return_value=[]), budget=Mock(return_value={}), interrupt=AsyncMock(),
        close=AsyncMock(),
    )
    host = SimpleNamespace(root=tmp_path, open_session=AsyncMock(return_value=session))
    return DesktopSessionBridge(host), host, session


@pytest.mark.asyncio
async def test_one_host_session_and_stable_send_key(bridge):
    transport, host, session = bridge
    await asyncio.gather(transport.open(), transport.open())
    host.open_session.assert_awaited_once_with("desktop-main")
    response = await transport.handle("session_send", {"text": "hi", "idempotency_key": "k"})
    session.send.assert_awaited_once_with("hi", idempotency_key="k")
    assert response["text"] == "hello"


@pytest.mark.asyncio
async def test_human_request_id_is_distinct_from_transport_id_and_identity_is_refused(bridge):
    transport, _, session = bridge
    await transport.handle("session_respond", {
        "request_id": "human-request", "decision": "approve", "version": 12, "reason": "yes",
    })
    session.respond.assert_called_once_with("human-request", "approve", 12, reason="yes")
    with pytest.raises(ValueError):
        await transport.handle("session_ack", {"event_id": "e", "principal": "other"})
    session.ack.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,body,method,args", [
    ("tasks", {}, "tasks", ()), ("task", {"task_id": "m"}, "task", ("m",)),
    ("cancel_task", {"task_id": "m"}, "cancel_task", ("m",)),
    ("inbox", {}, "inbox", ()), ("ack", {"event_id": "e"}, "ack", ("e",)),
])
async def test_reads_cancellation_and_ack_use_session(bridge, operation, body, method, args):
    transport, _, session = bridge
    await transport.handle(f"session_{operation}", body)
    getattr(session, method).assert_called_once_with(*args)


def test_event_whitelist_drops_internal_fields_and_unknown_events():
    event = {"schema_version": 1, "event_id": "e", "session_id": "desktop-main", "seq": 2,
             "kind": "task_progress", "mission_id": "m", "internal": "private",
             "payload": {"type": "TaskStarted", "seq": 10, "payload": {"secret": "private"}}}
    projected = project_session_event(event)
    assert projected["payload"] == {"type": "TaskStarted", "seq": 10}
    assert "internal" not in projected
    assert project_session_event({**event, "kind": "internal_debug"}) is None


@pytest.mark.asyncio
async def test_replay_cursor_and_disconnect_only_stop_transport(bridge):
    transport, _, session = bridge
    cursors = []
    async def events(after=0):
        cursors.append(after)
        yield {"schema_version": 1, "event_id": "e", "session_id": "desktop-main", "seq": 8,
               "kind": "delegated", "mission_id": "m", "payload": {"mission_id": "m"}}
        await asyncio.Event().wait()
    session.events = events
    sent = []
    async def broadcast(envelope):
        sent.append(envelope)
    await transport.subscribe("socket", broadcast, after=7)
    await asyncio.sleep(0)
    await transport.unsubscribe("socket")
    assert cursors == [7] and sent[0]["type"] == "session_event"
    session.close.assert_not_awaited()
    await transport.close()
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_data_is_rejected_without_opening_a_new_session(bridge):
    transport, host, _ = bridge
    (host.root / "mission-notices.json").write_text("{}")
    with pytest.raises(ValueError, match="legacy_session_data"):
        await transport.open()
    host.open_session.assert_not_awaited()


def test_approval_version_is_exact_over_the_json_boundary():
    result = project_session_event({"kind": "needs_response", "payload": {
        "request_id": "r", "version": 1152921504606846975, "question": "approve?",
        "options": [{"label": "yes", "internal": "private"}], "raw_binding": "private",
    }})
    assert result["payload"]["version"] == "1152921504606846975"
    assert result["payload"]["options"] == [{"label": "yes"}]
    assert "raw_binding" not in result["payload"]


def test_sdk_text_delta_top_level_fields_reach_the_frontend():
    event = {"kind": "text_delta", "text": "stream piece", "generation": 3, "turn_id": "t",
             "seq": 5, "session_id": "desktop-main", "event_id": "e", "schema_version": 1}
    assert project_session_event(event)["payload"] == {"text": "stream piece", "generation": 3}


@pytest.mark.asyncio
@pytest.mark.parametrize("message,body", [
    ("session_send", {"text": "hi"}),
    ("session_send", {"text": "", "idempotency_key": "k"}),
    ("session_task", {"task_id": None}),
    ("session_respond", {"request_id": "r", "decision": "approve", "version": True}),
])
async def test_invalid_requests_never_reach_the_session(bridge, message, body):
    transport, host, _ = bridge
    with pytest.raises(ValueError, match="invalid_request"):
        await transport.handle(message, body)
    host.open_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_foreground_history_is_not_silently_replaced(bridge):
    _, host, _ = bridge
    path = host.root / "state.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE foreground_turns (text TEXT)")
        db.execute("INSERT INTO foreground_turns VALUES ('old input')")
    transport = DesktopSessionBridge(host, legacy_state_path=path)
    with pytest.raises(ValueError, match="legacy_session_data"):
        await transport.open()
    host.open_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_installed_sdk_factory_has_an_actionable_error(bridge):
    transport, host, _ = bridge
    del host.open_session
    with pytest.raises(ValueError, match="sdk_session_unavailable"):
        await transport.open()


@pytest.mark.asyncio
async def test_budget_references_do_not_expose_the_private_ledger_path(bridge):
    transport, _, session = bridge
    session.budget.return_value = {"accounting": "references_only", "chat_ledger": "/private/ledger",
                                   "result_model_calls": 16, "global": {"spent_tokens": 4}}
    result = await transport.handle("session_budget", {})
    assert "chat_ledger" not in result
    assert result["global"] == {"spent_tokens": 4}


@pytest.mark.asyncio
async def test_arbitration_fields_reach_the_public_session_without_losing_identity(bridge):
    transport, _, session = bridge
    await transport.handle("session_respond", {
        "request_id": "human-arbitration", "decision": "arbitrate",
        "version": "1152921504606846975", "ruling": "accept_worker",
        "basis": "Explicit auto acceptance policy", "nonce": "auto-nonce",
    })
    session.respond.assert_called_once_with(
        "human-arbitration", "arbitrate", 1152921504606846975,
        ruling="accept_worker", basis="Explicit auto acceptance policy", nonce="auto-nonce",
    )


def test_planning_choice_ids_survive_the_event_whitelist():
    event = project_session_event({"kind": "needs_response", "payload": {
        "request_id": "adjudicate-root:r", "version": 1, "request_kind": "planning",
        "options": [{"id": "pass", "label": "通过", "internal": "private"}],
    }})
    assert event["payload"]["options"] == [{"id": "pass", "label": "通过"}]

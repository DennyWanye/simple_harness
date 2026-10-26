"""Verify the real-process smoke's model and explicit auto response policy."""

from __future__ import annotations

import importlib.util
import asyncio
import json
from pathlib import Path

import pytest


@pytest.fixture
def smoke():
    path = Path(__file__).resolve().parents[2] / "scripts/acceptance/desktop_session_smoke.py"
    spec = importlib.util.spec_from_file_location("desktop_session_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_provider_uses_requested_model_without_overriding_default_thinking(smoke, monkeypatch):
    monkeypatch.setenv("DEEPSEEKER_BASEURL", "https://example.test/v1")
    monkeypatch.setenv("DEEPSEEKER_APIKEY", "test-only-credential")
    provider = smoke.environment_provider()
    assert provider["models"] == ["deepseek-v4.1-flash"]
    assert provider["default_model"] == "deepseek-v4.1-flash"
    assert provider["name"] == "DeepSeek chunfengapi"
    assert "thinking" not in provider


def test_planning_auto_selects_first_non_stopping_option_and_preserves_version(smoke):
    body = smoke.auto_response({
        "request_kind": "planning", "request_id": "question-1",
        "version": "1152921504606846975", "options": ["停止", "采用方案一", "采用方案二"],
    }, {})
    assert body["decision"] == "answer"
    assert body["answer"] == "采用方案一"
    assert body["request_id"] == "question-1"
    assert body["version"] == "1152921504606846975"


def test_planning_auto_submits_choice_id_instead_of_its_display_label(smoke):
    body = smoke.auto_response({
        "request_kind": "planning", "request_id": "adjudicate-root:r", "version": "1",
        "options": [{"id": "stop", "label": "停止"}, {"id": "pass", "label": "通过"}],
    }, {})
    assert body["answer"] == "pass"


@pytest.mark.parametrize("kind,decision,extra", [
    ("action", "approve", {}),
    ("review", "review_pass", {}),
    ("arbitration", "arbitrate", {"ruling": "accept_worker"}),
])
def test_auto_approval_uses_loaded_task_kind(smoke, kind, decision, extra):
    body = smoke.auto_response({
        "request_kind": "approval", "request_id": "human-1", "version": "9",
    }, {"detail": {"approvals": [{
        "request_id": "human-1", "kind": kind, "state": "PENDING",
        "options": ["accept_worker", "unresolved"],
    }]}})
    assert body["decision"] == decision
    assert body.items() >= extra.items()
    assert body["nonce"]
    if kind == "arbitration":
        assert body["basis"]


def test_missing_pending_approval_is_not_silently_approved(smoke):
    with pytest.raises(ValueError, match="pending approval"):
        smoke.auto_response({
            "request_kind": "approval", "request_id": "unknown", "version": "1",
        }, {"detail": {"approvals": []}})


def test_only_stopping_planning_options_cannot_be_called_success(smoke):
    with pytest.raises(ValueError, match="continuing option"):
        smoke.auto_response({
            "request_kind": "planning", "request_id": "q", "version": "1",
            "options": ["停止", "放弃"],
        }, {})


@pytest.mark.asyncio
async def test_send_receipt_and_terminal_events_survive_auto_response_reads(smoke, monkeypatch):
    import websockets

    class Socket:
        def __init__(self):
            self.frames = asyncio.Queue()
            self.sent = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def event(self, kind, payload=None):
            self.frames.put_nowait({"type": "session_event", "payload": {
                "kind": kind, "mission_id": "m", "seq": len(self.sent),
                "payload": payload or {},
            }})

        def reply(self, raw, data):
            self.frames.put_nowait({"type": f"{raw['type']}_response", "payload": {
                "request_id": raw["request_id"], "ok": True, "data": data,
            }})

        async def send(self, encoded):
            raw = json.loads(encoded)
            self.sent.append(raw)
            if raw["type"] == "session_events":
                self.reply(raw, {"subscribed": True})
            elif raw["request_id"] == "plain":
                self.reply(raw, {"state": "committed", "text": "hello"})
            elif raw["request_id"] == "delegate":
                self.delegation = raw
                self.event("delegated")
                self.event("needs_response", {
                    "request_kind": "planning", "request_id": "q", "version": "7",
                    "options": ["停止", "继续"],
                })
            elif raw["type"] == "session_task":
                self.reply(self.delegation, {"state": "committed", "text": "delegated"})
                self.reply(raw, {"mission_id": "m", "status": "COMPLETED"})
            elif raw["type"] == "session_respond":
                self.event("task_progress", {"type": "TaskStarted"})
                self.event("result_ready", {"status": "COMPLETED"})
                self.reply(raw, {"applied": True})

        async def recv(self):
            return json.dumps(await self.frames.get())

    socket = Socket()
    monkeypatch.setattr(websockets, "connect", lambda *args, **kwargs: socket)
    result = await asyncio.wait_for(smoke.exchange(12345), 1)
    assert result["文字"] == result["委派"] == "PASS"
    assert result["代答"][0]["applied"] is True
    assert result["代答"][0]["answer"] == "继续"
    assert result["代答"][0]["options"] == ["停止", "继续"]
    assert result["任务"]["mission_id"] == "m"


@pytest.mark.asyncio
async def test_failed_control_reply_preserves_safe_partial_run_summary(smoke, monkeypatch):
    import websockets

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def send(self, encoded):
            self.raw = json.loads(encoded)

        async def recv(self):
            return json.dumps({"type": "session_events_response", "payload": {
                "request_id": self.raw["request_id"], "ok": False,
                "error_code": "chat_unavailable",
            }})

    monkeypatch.setattr(websockets, "connect", lambda *args, **kwargs: Socket())
    result = {}
    with pytest.raises(RuntimeError):
        await smoke.exchange(12345, result=result)
    assert result["错误"] == ["events:chat_unavailable"]
    assert result["模型"] == "deepseek-v4.1-flash"

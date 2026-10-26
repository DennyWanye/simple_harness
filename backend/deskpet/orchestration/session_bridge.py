"""Desktop transport for the SDK Session; all durable state belongs to the SDK."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from contextlib import suppress
from typing import Any
from pathlib import Path

from .projection import project_detail

SESSION_ID = "desktop-main"
EVENT_FIELDS = ("schema_version", "event_id", "session_id", "seq", "kind", "turn_id",
                "delegation_id", "mission_id", "request_id")
PAYLOAD_FIELDS = {
    "input_committed": (),
    "text_delta": ("text", "generation"),
    "turn_committed": ("turn_id", "state", "text"),
    "delegated": ("delegation_id", "mission_id", "status"),
    "task_progress": ("seq", "type", "created_at", "task_id", "attempt_id", "actor_type", "summary"),
    "needs_response": ("request_id", "mission_id", "version", "request_kind", "question",
                       "options", "summary", "reason", "topic"),
    "result_ready": ("event_id", "mission_id", "delegation_id", "status", "source", "instruction",
                     "acked", "applied_turn_id"),
    "error": ("code",),
}
SESSION_MESSAGES = frozenset(f"session_{name}" for name in (
    "send", "events", "respond", "interrupt", "tasks", "task", "cancel_task", "history",
    "inbox", "ack", "budget",
))


def _pick(value: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: value[key] for key in fields if key in value}


def _display(value: Any) -> Any:
    if isinstance(value, str):
        return value[:8000]
    if isinstance(value, list):
        return [_display(item) for item in value[:32]]
    if isinstance(value, Mapping):
        return {key: _display(value[key]) for key in ("key", "text", "label", "value", "description")
                if key in value}
    return value if value is None or type(value) in (bool, int, float) else None


def project_session_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    kind = event.get("kind")
    if kind not in PAYLOAD_FIELDS:
        return None
    result = _pick(event, EVENT_FIELDS)
    payload = _pick(event if kind == "text_delta" else event.get("payload") or {}, PAYLOAD_FIELDS[kind])
    if kind == "needs_response":
        payload = {key: _display(value) for key, value in payload.items()}
        # Approval versions are 60-bit integers and must survive JavaScript exactly.
        if "version" in payload:
            payload["version"] = str(payload["version"])
    if kind == "text_delta":
        payload["text"] = str(payload.get("text") or "")
    result["payload"] = payload
    return result


def _task(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _pick(value, ("delegation_id", "mission_id", "turn_id", "cancel_requested", "status"))
    if value.get("snapshot"):
        result["detail"] = project_detail(value["snapshot"])
    return result


def _inbox(value: Mapping[str, Any]) -> dict[str, Any]:
    return _pick(value, PAYLOAD_FIELDS["result_ready"])


class DesktopSessionBridge:
    def __init__(self, host: Any, *, legacy_state_path: Path | None = None) -> None:
        self.host = host
        self.legacy_state_path = legacy_state_path
        self._session: Any = None
        self._lock = asyncio.Lock()
        self._readers: dict[Any, asyncio.Task[None]] = {}

    async def open(self) -> Any:
        async with self._lock:
            if self._session is None:
                if (self.host.root / "mission-notices.json").exists():
                    raise ValueError("legacy_session_data: 旧开发通知数据不兼容，请使用新的数据目录")
                path = self.legacy_state_path
                if path is not None and path.exists():
                    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
                        found = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='foreground_turns'").fetchone()
                        if found and db.execute("SELECT 1 FROM foreground_turns LIMIT 1").fetchone():
                            raise ValueError("legacy_session_data: 旧主对话数据不兼容，请使用新的数据目录")
                if not callable(getattr(self.host, "open_session", None)):
                    raise ValueError("sdk_session_unavailable: 安装的 SDK 未提供 host.open_session")
                self._session = await self.host.open_session(SESSION_ID)
            return self._session

    async def handle(self, message: str, body: Mapping[str, Any]) -> Any:
        operation = message.removeprefix("session_")
        fields = {
            "send": {"text", "idempotency_key"}, "respond": {"request_id", "decision", "version",
                "answer", "nonce", "attach_as_source", "reason", "note", "ruling", "basis"},
            "task": {"task_id"}, "cancel_task": {"task_id"}, "ack": {"event_id"},
            "history": {"limit"}, "interrupt": set(), "tasks": set(), "inbox": set(), "budget": set(),
        }
        if operation not in fields or set(body) - fields[operation]:
            raise ValueError("invalid_request")
        required = {
            "send": ("text", "idempotency_key"), "respond": ("request_id", "decision"),
            "task": ("task_id",), "cancel_task": ("task_id",), "ack": ("event_id",),
        }.get(operation, ())
        if any(not isinstance(body.get(key), str) or not body[key].strip() for key in required):
            raise ValueError("invalid_request")
        if operation == "respond":
            version = body.get("version")
            if not (type(version) is int and version >= 0) and not (
                isinstance(version, str) and version.isdecimal()
            ):
                raise ValueError("invalid_request")
        if operation == "history" and "limit" in body and (
            type(body["limit"]) is not int or body["limit"] < 1
        ):
            raise ValueError("invalid_request")
        session = await self.open()
        if operation == "send":
            reply = await session.send(body["text"], idempotency_key=body["idempotency_key"])
            return _pick(reply, ("turn_id", "state", "text", "error"))
        if operation == "respond":
            version = body["version"]
            if type(version) is not int and not (isinstance(version, str) and version.isdecimal()):
                raise ValueError("invalid_request")
            session.respond(body["request_id"], body["decision"], int(version),
                **{key: value for key, value in body.items() if key not in {"request_id", "decision", "version"}})
            return {"applied": True}
        if operation == "interrupt":
            await session.interrupt()
            return {"interrupted": True}
        if operation == "tasks":
            return [_task(row) for row in session.tasks()]
        if operation == "task":
            return _task(session.task(body["task_id"]))
        if operation == "cancel_task":
            return _task(session.cancel_task(body["task_id"]))
        if operation == "inbox":
            return [_inbox(row) for row in session.inbox()]
        if operation == "ack":
            return _inbox(session.ack(body["event_id"]))
        if operation == "history":
            return [_pick(row, ("turn_id", "input", "text"))
                    for row in session.history(limit=body.get("limit", 50))]
        return _pick(session.budget(), ("accounting", "result_model_calls", "global"))

    async def subscribe(self, key: Any, broadcast: Any, *, after: int = 0) -> None:
        if type(after) is not int or after < 0:
            raise ValueError("invalid_request")
        session = await self.open()
        await self.unsubscribe(key)

        async def relay() -> None:
            async for event in session.events(after=after):
                projected = project_session_event(event)
                if projected is not None:
                    await broadcast({"type": "session_event", "payload": projected})

        self._readers[key] = asyncio.create_task(relay(), name="desktop-session-events")

    async def unsubscribe(self, key: Any) -> None:
        reader = self._readers.pop(key, None)
        if reader is not None:
            reader.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await reader

    async def close(self) -> None:
        for key in list(self._readers):
            await self.unsubscribe(key)
        if self._session is not None:
            await self._session.close()


__all__ = ("DesktopSessionBridge", "SESSION_ID", "SESSION_MESSAGES", "project_session_event")

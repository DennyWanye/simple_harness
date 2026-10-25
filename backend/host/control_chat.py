"""Route desktop text messages to the SDK Session ingress."""

from __future__ import annotations

import uuid

from host.control_session import _cc_session


async def _cc_chat(conn, raw: dict, msg_type: str) -> None:
    payload = raw.get("payload") or {}
    await _cc_session(conn, {
        "request_id": raw.get("request_id") or payload.get("request_id"),
        "payload": {
            "text": payload.get("text", ""),
            "idempotency_key": payload.get("request_id") or raw.get("request_id") or uuid.uuid4().hex,
        },
    }, "session_send")

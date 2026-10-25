"""Control-channel ingress for the desktop's single SDK Session."""

from __future__ import annotations

import asyncio
from typing import Any

from host.runtime import service_context
from deskpet.orchestration.wiring import _broadcaster

_sends: set[asyncio.Task[Any]] = set()


async def _cc_session(conn: Any, raw: dict, message: str) -> None:
    async def execute() -> None:
        response: dict[str, Any] = {"request_id": raw.get("request_id"), "ok": False}
        try:
            service = service_context.get("orchestration")
            if service is None or service.status().get("state") not in {"available", "degraded"}:
                raise ValueError("orchestration_unavailable")
            body = raw.get("payload") or {}
            if not isinstance(body, dict):
                raise ValueError("invalid_request")
            if message == "session_events":
                if set(body) - {"after"}:
                    raise ValueError("invalid_request")
                await service.session_bridge.subscribe(
                    conn.ws, _broadcaster(lambda: [conn.ws]), after=body.get("after", 0)
                )
                data = {"subscribed": True}
            else:
                data = await service.session_bridge.handle(message, body)
            response.update(ok=True, data=data)
        except Exception as error:
            code = getattr(error, "code", None)
            if code is None:
                code = str(error) if isinstance(error, ValueError) else "session_unavailable"
            response.update(error_code=code, error="会话请求未完成，请刷新后重试。")
        try:
            await conn.ws.send_json({"type": f"{message}_response", "payload": response})
        except Exception:
            # Disconnect detaches presentation; the SDK still commits the send.
            pass

    if message == "session_send":
        task = asyncio.create_task(execute(), name="desktop-session-send")
        _sends.add(task)
        task.add_done_callback(_sends.discard)
    else:
        await execute()


__all__ = ("_cc_session",)

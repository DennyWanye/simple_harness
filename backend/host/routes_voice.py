"""语音 WebSocket：实时语音通道"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from host.runtime import logger
from host.state import host_state

voice_router = APIRouter()


class _StarletteRealtimeSocket:
    """Minimal adapter from Starlette frames to the SDK socket protocol."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._closed = False

    async def recv(self) -> str | bytes:
        message = await self._websocket.receive()
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            raise EOFError
        if message_type != "websocket.receive":
            raise RuntimeError("local websocket frame invalid")
        text = message.get("text")
        data = message.get("bytes")
        if isinstance(text, str) and data is None:
            return text
        if isinstance(data, bytes) and text is None:
            return data
        raise RuntimeError("local websocket frame invalid")

    async def send(self, message: str | bytes) -> None:
        if isinstance(message, str):
            await self._websocket.send_text(message)
        else:
            await self._websocket.send_bytes(message)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self._closed:
            return
        self._closed = True
        await self._websocket.close(code=code, reason=reason)


@voice_router.websocket("/ws/realtime-voice")
async def realtime_voice_channel(ws: WebSocket):
    await ws.accept()
    service = host_state.realtime_voice_service
    if service is None:
        await ws.close(code=1013, reason="realtime unavailable")
        return
    # 按需导入：重型包
    from simple_harness_service.realtime.transports import LocalAdmissionError

    peer_host = ws.client.host if ws.client is not None else ""
    try:
        await service.serve(
            _StarletteRealtimeSocket(ws),
            path=ws.url.path,
            origin=ws.headers.get("origin"),
            peer_host=peer_host,
        )
    except LocalAdmissionError as exc:
        logger.info("realtime_voice_local_rejected", stable_code=exc.code)
    except (EOFError, WebSocketDisconnect):
        logger.info("realtime_voice_local_closed")
    except Exception:  # noqa: BLE001
        logger.warning("realtime_voice_local_failed", stable_code="internal", exc_info=True)

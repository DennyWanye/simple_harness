"""语音 WebSocket：实时语音通道、音频通道与在途语音管线登记"""

from __future__ import annotations

import weakref

from deskpet.voice_runtime import VOICE_DISABLED_CODE, VOICE_DISABLED_MESSAGE
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pipeline.voice_pipeline import VoicePipeline

from host.auth import _validate_secret
from host.chat_delivery import _broadcast_default_chat_peers
from host.connections import _control_connections
from host.runtime import config, logger, service_context
from host.sdk_execution import _voice_run_host
from host.state import host_state
from host.tools_bootstrap import deskpet_tool_registry_v2, permission_gate_v2
from host.voice_bootstrap import _voice_runtime

# Track active voice pipelines by session so that a control-channel `interrupt`
# message can reach the audio-channel pipeline (they are separate WebSockets).
_voice_transports: weakref.WeakValueDictionary[str, object] = weakref.WeakValueDictionary()


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


@voice_router.websocket("/ws/audio")
async def audio_channel(ws: WebSocket):
    await ws.accept()
    if not _validate_secret(ws):
        try:
            await ws.close(code=4001, reason="invalid secret")
        except Exception:
            pass
        return

    if not _voice_runtime.enabled:
        await ws.send_json(
            {
                "type": "error",
                "payload": {
                    "code": VOICE_DISABLED_CODE,
                    "message": VOICE_DISABLED_MESSAGE,
                    "realtime": "pending",
                },
            }
        )
        await ws.close(code=4403, reason="voice temporarily disabled")
        return

    session_id = ws.query_params.get("session_id", "default")
    control_ws = _control_connections.get(session_id)

    # Each audio connection gets its own VAD instance (stateful)
    session_vad = _voice_runtime.create_session_vad()
    await session_vad.load()

    # VOICE-MSGPANEL-SYNC fix: 语音广播用「实时」解析的发起窗口 control_ws 作
    # originator，而非 audio 连接建立时的快照 —— backend respawn 后 audio_ws 常
    # 先于 control_ws 重连，快照会是 None/失效，导致守卫挡掉广播、或不能正确 skip
    # 主窗口（重复显示）。这里对齐文字路径（main.py chat handler 用实时 _ws）。
    async def _voice_broadcast(_origin_snapshot, _msg: dict) -> None:
        current_control_ws = _control_connections.get(session_id)
        # If the pipeline snapshot is still the live originator, it already
        # received point-to-point tool activity and should be skipped here.
        # After a reconnect the snapshot is None/stale, so broadcast to every
        # current control peer; otherwise the new originator silently misses
        # tool_call/tool_result while the tool itself still executes.
        originator = current_control_ws if _origin_snapshot is current_control_ws else None
        await _broadcast_default_chat_peers(originator, _msg)

    # V5 §2.3 + S1: voice pipeline routes through agent_engine (not llm directly)
    # so that S2 memory / S3 tools flow uniformly through voice and text paths.
    # P4-S21 #13: also pass the v2 tool stack (registry + permission gate +
    # raw LLM provider) so voice utterances run through AgentLoop and can
    # actually invoke tools — same code path text chat already uses.
    pipeline = VoicePipeline(
        vad=session_vad,
        asr=service_context.asr_engine,
        agent=service_context.agent_engine,
        tts=service_context.tts_engine,
        control_ws=control_ws,
        session_id=session_id,
        vad_threshold_during_tts=config.voice.vad_threshold_during_tts,
        min_speech_ms_during_tts=config.voice.min_speech_ms_during_tts,
        tts_cooldown_ms=config.voice.tts_cooldown_ms,
        service_context=service_context,
        tool_registry_v2=deskpet_tool_registry_v2,
        permission_gate_v2=permission_gate_v2,
        local_llm=host_state.local_llm,
        # VOICE-MSGPANEL-SYNC: 注入实时解析 originator 的语音广播器（_voice_broadcast
        # 在上面定义，闭包捕获 session_id，广播时实时取发起窗口 control_ws 作 skip 目标）。
        broadcast=_voice_broadcast,
        run_client=(
            host_state.sdk_ingress.run_client
            if host_state.sdk_ingress is not None and host_state.sdk_ingress.accepting
            else None
        ),
    )
    pipeline.bind_run_host_factory(_voice_run_host)
    # Register so control-channel `interrupt` messages can reach us.
    _voice_transports[session_id] = pipeline

    logger.info("audio channel connected", session_id=session_id)
    try:
        while True:
            data = await ws.receive_bytes()
            await pipeline.process_audio_chunk(data, ws)
    except WebSocketDisconnect:
        logger.info("audio channel disconnected", session_id=session_id)
    finally:
        if _voice_transports.get(session_id) is pipeline:
            _voice_transports.pop(session_id, None)

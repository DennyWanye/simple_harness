# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Product composition for the Service SDK Realtime subsystem.

This module owns product policy (relay origin, model, voice, local admission
secret and UI origins). Provider wire parsing, session ordering and local PCM
framing remain inside ``simple-harness-service-sdk``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

import structlog
from simple_harness_service.realtime import (
    LocalRealtimeChannelController,
    RealtimeClient,
    RealtimeDiagnostics,
    RealtimeError,
    RealtimeErrorCode,
    RealtimeOpenRequest,
    RealtimeProfile,
)
from simple_harness_service.realtime.adapters import QwenOmniAdapter
from simple_harness_service.realtime.connectors import (
    TokenSellerHttpsCredentialMinter,
)
from simple_harness_service.realtime.ports import RealtimeSession
from simple_harness_service.realtime.transports import (
    LOCAL_REALTIME_PATH,
    LOCAL_REALTIME_VERSION,
    LoopbackWebSocketRealtimeChannel,
    LoopbackWebSocketRealtimeHost,
    RelayWebSocketTransport,
    WebSocketConnection,
)

from deskpet.sdk_adapters.sdk_candidate import verify_service_candidate

logger = structlog.get_logger(__name__)

REALTIME_PUBLIC_MODEL = "qwen3.5-omni-realtime"
REALTIME_VOICE = "Tina"
REALTIME_INSTRUCTIONS = (
    "你是 Simple Harness 的实时语音助手。使用简洁、自然的中文回答，"
    "允许用户随时打断。"
)


class _MetadataDiagnosticSink:
    """Log only the SDK diagnostic allowlist; never audio or transcript data."""

    _AUDIO_PROGRESS_INTERVAL = 250

    def __init__(self) -> None:
        self._audio_totals: dict[str, dict[str, tuple[int, int]]] = {}

    def emit(self, event: Any) -> None:
        stage = event.stage.value
        if stage in {"input_audio", "output_audio"}:
            totals = self._audio_totals.setdefault(event.correlation, {})
            totals[stage] = (event.frame_count, event.byte_count)
            if (
                event.frame_count != 1
                and event.frame_count % self._AUDIO_PROGRESS_INTERVAL != 0
            ):
                return
            logger.info(
                "realtime_voice_audio_progress",
                correlation=event.correlation,
                direction="input" if stage == "input_audio" else "output",
                generation=event.generation,
                frame_count=event.frame_count,
                byte_count=event.byte_count,
                duration_ms=event.duration_ms,
            )
            return

        totals = self._audio_totals.get(event.correlation, {})
        input_frames, input_bytes = totals.get("input_audio", (0, 0))
        output_frames, output_bytes = totals.get("output_audio", (0, 0))
        logger.info(
            "realtime_voice_lifecycle",
            correlation=event.correlation,
            stage=stage,
            stable_code=(event.stable_code.value if event.stable_code else None),
            close_class=(event.close_class.value if event.close_class else None),
            generation=event.generation,
            frame_count=event.frame_count,
            byte_count=event.byte_count,
            duration_ms=event.duration_ms,
            input_frame_count=input_frames,
            input_byte_count=input_bytes,
            output_frame_count=output_frames,
            output_byte_count=output_bytes,
        )
        if stage == "local_closed":
            self._audio_totals.pop(event.correlation, None)


class _MissingCredentialOpener:
    async def _open_with_correlation(
        self, request: RealtimeOpenRequest, correlation: str
    ) -> RealtimeSession:
        del request, correlation
        raise RealtimeError(RealtimeErrorCode.UNAUTHENTICATED, retryable=False)


def relay_origin(endpoint: str) -> str:
    """Normalize the configured OpenAI-compatible ``/v1`` URL to an origin."""

    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") not in {"", "/v1"}
    ):
        raise ValueError("Realtime relay endpoint must be an HTTPS origin or /v1 URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Realtime relay endpoint has an invalid port") from exc
    host = parsed.hostname.lower()
    rendered_host = f"[{host}]" if ":" in host else host
    rendered_port = "" if port in (None, 443) else f":{port}"
    return f"https://{rendered_host}{rendered_port}"


def allowed_realtime_origins(vite_port: int = 5173) -> frozenset[str]:
    if not 1 <= vite_port <= 65_535:
        raise ValueError("vite_port is out of range")
    return frozenset(
        {
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
            f"http://127.0.0.1:{vite_port}",
            f"http://localhost:{vite_port}",
        }
    )


class RealtimeVoiceService:
    """Own local admission and create one SDK session per accepted call."""

    def __init__(
        self,
        *,
        shared_secret: str,
        relay_endpoint_supplier: Callable[[], str],
        api_key_supplier: Callable[[], str | None],
        allowed_origins: Iterable[str],
    ) -> None:
        verify_service_candidate()
        self._relay_endpoint_supplier = relay_endpoint_supplier
        self._api_key_supplier = api_key_supplier
        self._diagnostics = RealtimeDiagnostics(_MetadataDiagnosticSink())
        self._host = LoopbackWebSocketRealtimeHost(
            shared_secret,
            allowed_origins,
            max_connections=1,
        )
        self._closed = False

    async def serve(
        self,
        socket: WebSocketConnection,
        *,
        path: str,
        origin: str | None,
        peer_host: str,
    ) -> None:
        async def handle(channel: LoopbackWebSocketRealtimeChannel) -> None:
            minter: TokenSellerHttpsCredentialMinter | None = None
            api_key = self._api_key_supplier()
            if api_key:
                current_relay_origin = relay_origin(self._relay_endpoint_supplier())
                adapter = QwenOmniAdapter()
                profile = RealtimeProfile(
                    name="tokenseller-qwen-omni",
                    provider=adapter.capability.provider,
                    wire_protocol=adapter.capability.wire_protocol,
                    wire_version=adapter.capability.wire_version,
                    public_model=REALTIME_PUBLIC_MODEL,
                    voice=REALTIME_VOICE,
                    capability=adapter.capability,
                )
                minter = TokenSellerHttpsCredentialMinter(
                    current_relay_origin,
                    api_key,
                )
                opener: Any = RealtimeClient(
                    profile,
                    minter,
                    RelayWebSocketTransport(current_relay_origin),
                    adapter,
                    diagnostics=self._diagnostics,
                )
            else:
                opener = _MissingCredentialOpener()
            controller = LocalRealtimeChannelController(
                opener,
                diagnostics=self._diagnostics,
            )
            try:
                await controller.run(channel)
            finally:
                if minter is not None:
                    await minter.aclose()

        await self._host.run(
            socket,
            path=path,
            origin=origin,
            peer_host=peer_host,
            handler=handle,
        )

    def health_payload(self) -> dict[str, object]:
        snapshot = self._diagnostics.snapshot()
        return {
            "enabled": not self._closed,
            "path": LOCAL_REALTIME_PATH,
            "protocol_version": LOCAL_REALTIME_VERSION,
            "active_channels": self._host.active_channel_count,
            "diagnostic_events": snapshot.emitted_count,
            "diagnostic_drops": snapshot.dropped_count + snapshot.sink_drop_count,
        }

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._host.close()
        self._diagnostics.close(1.0)


__all__ = (
    "LOCAL_REALTIME_PATH",
    "LOCAL_REALTIME_VERSION",
    "REALTIME_INSTRUCTIONS",
    "REALTIME_PUBLIC_MODEL",
    "REALTIME_VOICE",
    "RealtimeVoiceService",
    "allowed_realtime_origins",
    "relay_origin",
)

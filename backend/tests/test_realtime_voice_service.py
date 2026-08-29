# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import json
from pathlib import Path

import pytest
from deskpet.realtime_voice import (
    LOCAL_REALTIME_PATH,
    LOCAL_REALTIME_VERSION,
    _MetadataDiagnosticSink,
    RealtimeVoiceService,
    allowed_realtime_origins,
    relay_origin,
)
from simple_harness_service.realtime import (
    RealtimeDiagnosticEvent,
    RealtimeDiagnosticStage,
)
from simple_harness_service.realtime.transports import LocalAdmissionError


class FakeSocket:
    def __init__(self, incoming: list[str | bytes]) -> None:
        self.incoming = list(incoming)
        self.sent: list[str | bytes] = []
        self.close_codes: list[int] = []

    async def recv(self) -> str | bytes:
        if not self.incoming:
            raise EOFError
        return self.incoming.pop(0)

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        del reason
        self.close_codes.append(code)


def _message(message_type: str, **values: object) -> str:
    return json.dumps({"type": message_type, **values}, separators=(",", ":"))


def test_relay_origin_accepts_only_https_origin_or_v1() -> None:
    assert relay_origin("https://chinzy.com/v1") == "https://chinzy.com"
    assert relay_origin("https://chinzy.com/") == "https://chinzy.com"
    with pytest.raises(ValueError):
        relay_origin("http://chinzy.com/v1")
    with pytest.raises(ValueError):
        relay_origin("https://chinzy.com/v1/other")


def test_service_construction_does_not_resolve_or_call_provider() -> None:
    calls: list[str] = []
    service = RealtimeVoiceService(
        shared_secret="local-secret",
        relay_endpoint_supplier=lambda: calls.append("endpoint") or "https://chinzy.com/v1",
        api_key_supplier=lambda: calls.append("key") or "provider-key",
        allowed_origins=allowed_realtime_origins(),
    )
    assert calls == []
    assert service.health_payload()["active_channels"] == 0


@pytest.mark.asyncio
async def test_wrong_origin_fails_before_provider_resolution() -> None:
    calls: list[str] = []
    service = RealtimeVoiceService(
        shared_secret="local-secret",
        relay_endpoint_supplier=lambda: calls.append("endpoint") or "https://chinzy.com/v1",
        api_key_supplier=lambda: calls.append("key") or "provider-key",
        allowed_origins=allowed_realtime_origins(),
    )
    socket = FakeSocket(
        [
            _message(
                "local.auth",
                version=LOCAL_REALTIME_VERSION,
                secret="local-secret",
            )
        ]
    )
    with pytest.raises(LocalAdmissionError, match="wrong_origin"):
        await service.serve(
            socket,
            path=LOCAL_REALTIME_PATH,
            origin="https://attacker.invalid",
            peer_host="127.0.0.1",
        )
    assert calls == []
    assert socket.close_codes == [1008]
    await service.close()


@pytest.mark.asyncio
async def test_missing_provider_key_becomes_stable_local_error() -> None:
    service = RealtimeVoiceService(
        shared_secret="local-secret",
        relay_endpoint_supplier=lambda: "https://chinzy.com/v1",
        api_key_supplier=lambda: None,
        allowed_origins=allowed_realtime_origins(),
    )
    socket = FakeSocket(
        [
            _message(
                "local.auth",
                version=LOCAL_REALTIME_VERSION,
                secret="local-secret",
            ),
            _message(
                "local.hello",
                version=LOCAL_REALTIME_VERSION,
                generation=1,
                correlation="corr_0123456789ABCDEFGHJKMNPQRS",
            ),
            _message(
                "call.start",
                generation=1,
                instructions="Fixture instructions.",
                required_features=[
                    "server_turn_detection",
                    "automatic_response",
                    "interruption",
                    "input_transcription",
                    "audio_output",
                ],
            ),
        ]
    )
    await service.serve(
        socket,
        path=LOCAL_REALTIME_PATH,
        origin="http://localhost:5173",
        peer_host="127.0.0.1",
    )
    sent = [json.loads(value) for value in socket.sent if isinstance(value, str)]
    assert [value["type"] for value in sent] == [
        "call.state",
        "call.state",
        "call.error",
        "call.closed",
    ]
    assert sent[2] == {
        "type": "call.error",
        "generation": 1,
        "code": "unauthenticated",
        "retryable": False,
    }
    assert socket.close_codes == [1000]
    await service.close()


def test_backend_route_uses_sdk_authority_path_and_keeps_legacy_disabled() -> None:
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(
        encoding="utf-8"
    )
    assert '@app.websocket("/ws/realtime-voice")' in source
    assert '@app.websocket("/ws/audio")' in source
    assert "if not _voice_runtime.enabled:" in source
    assert "Qwen" not in source[source.index('class _StarletteRealtimeSocket') :]


def test_diagnostic_sink_samples_audio_and_summarizes_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "deskpet.realtime_voice.logger.info",
        lambda name, **values: logged.append((name, values)),
    )
    sink = _MetadataDiagnosticSink()
    correlation = "corr_0123456789ABCDEFGHJKMNPQRS"
    for frame_count in range(1, 501):
        sink.emit(
            RealtimeDiagnosticEvent(
                correlation=correlation,
                stage=RealtimeDiagnosticStage.INPUT_AUDIO,
                generation=1,
                frame_count=frame_count,
                byte_count=frame_count * 1024,
            )
        )
    sink.emit(
        RealtimeDiagnosticEvent(
            correlation=correlation,
            stage=RealtimeDiagnosticStage.SESSION_TERMINAL,
            generation=1,
        )
    )
    sink.emit(
        RealtimeDiagnosticEvent(
            correlation=correlation,
            stage=RealtimeDiagnosticStage.LOCAL_CLOSED,
            generation=1,
        )
    )

    assert [name for name, _values in logged] == [
        "realtime_voice_audio_progress",
        "realtime_voice_audio_progress",
        "realtime_voice_audio_progress",
        "realtime_voice_lifecycle",
        "realtime_voice_lifecycle",
    ]
    assert logged[3][1]["input_frame_count"] == 500
    assert logged[3][1]["input_byte_count"] == 512_000
    assert sink._audio_totals == {}

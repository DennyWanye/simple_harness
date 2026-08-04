# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-11 contract tests for the test-only Voice -> Run API adapter seam."""

from __future__ import annotations

import ast
import asyncio
import inspect
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from pipeline.voice_pipeline import VoicePipeline


ROOT = Path(__file__).resolve().parents[3]


class _WS:
    def __init__(self) -> None:
        self.json_frames: list[dict[str, Any]] = []
        self.binary_frames: list[bytes] = []

    async def send_json(self, frame: dict[str, Any]) -> None:
        self.json_frames.append(frame)

    async def send_bytes(self, frame: bytes) -> None:
        self.binary_frames.append(bytes(frame))


class _ASR:
    def __init__(self, text: str = "hello from voice") -> None:
        self.text = text

    async def transcribe(self, _: bytes) -> str:
        return self.text


class _VAD:
    def __init__(self) -> None:
        self.threshold = 0.5

    def set_threshold(self, value: float) -> None:
        self.threshold = value


class _ForbiddenAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def chat_stream(self, *args: object, **kwargs: object) -> AsyncIterator[str]:
        del args, kwargs
        self.calls += 1
        raise AssertionError("injected RunClient must not enter legacy chat_stream")
        yield ""  # pragma: no cover - makes this an async generator


class _TTS:
    def __init__(self, chunks: tuple[bytes, ...] = (b"\x00\x00\x00\x00",)) -> None:
        self.texts: list[str] = []
        self.chunks = chunks

    async def synthesize_pcm_stream(self, text: str) -> AsyncIterator[bytes]:
        self.texts.append(text)
        for chunk in self.chunks:
            yield chunk


@dataclass(frozen=True)
class _Candidate:
    kind: str
    status: str
    driver_kind: str = "test"
    payload: Mapping[str, object] = field(default_factory=dict)
    correlation: Mapping[str, object] = field(default_factory=dict)
    error: Mapping[str, object] | None = None
    artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Event:
    event_id: str
    run_id: str
    root_run_id: str
    session_id: str
    durable_seq: int
    candidate: _Candidate
    created_at: float = 1.0
    live_cursor: None = None


@dataclass(frozen=True)
class _Handle:
    run_id: str
    events: AsyncIterator[_Event]
    session_id: str
    client: "_RunClient"

    async def signal(self, signal: Mapping[str, object]) -> object:
        return await self.client.signal(
            {"run_id": self.run_id, "expected_session_id": self.session_id},
            {"session_id": self.session_id, "venue": "voice"},
            signal,
        )

    async def cancel(self, reason: str) -> object:
        return await self.client.cancel(
            {"run_id": self.run_id, "expected_session_id": self.session_id},
            {"session_id": self.session_id, "venue": "voice"},
            reason,
        )

    async def close(self) -> None:
        await self.client.close(
            {"run_id": self.run_id, "expected_session_id": self.session_id},
            {"session_id": self.session_id, "venue": "voice"},
        )


_Script = Callable[[str, str, "_RunClient"], AsyncIterator[_Event]]


class _RunClient:
    def __init__(self, scripts: Mapping[str, _Script]) -> None:
        self.scripts = dict(scripts)
        self.starts: list[tuple[dict[str, object], dict[str, object], str]] = []
        self.signals: list[
            tuple[dict[str, object], dict[str, object], dict[str, object]]
        ] = []
        self.cancels: list[tuple[dict[str, object], dict[str, object], str]] = []
        self.closes: list[tuple[dict[str, object], dict[str, object]]] = []
        self.signal_received = asyncio.Event()
        self._venue_counts: dict[str, int] = {}
        self.active_refs: set[str] = set()

    async def start(
        self,
        request: Mapping[str, object],
        host: Mapping[str, object],
    ) -> _Handle:
        venue = str(host["venue"])
        session_id = str(host["session_id"])
        count = self._venue_counts.get(venue, 0) + 1
        self._venue_counts[venue] = count
        run_id = f"{venue}-run-{count}"
        self.active_refs.add(run_id)
        self.starts.append((dict(request), dict(host), run_id))
        return _Handle(
            run_id,
            self.scripts[venue](run_id, session_id, self),
            session_id,
            self,
        )

    async def signal(
        self,
        ref: Mapping[str, object],
        actor: Mapping[str, object],
        signal: Mapping[str, object],
    ) -> object:
        self.signals.append((dict(ref), dict(actor), dict(signal)))
        self.signal_received.set()
        return {"accepted": True, "decision_id": signal["decision_id"]}

    async def cancel(
        self,
        ref: Mapping[str, object],
        actor: Mapping[str, object],
        reason: str,
    ) -> object:
        self.cancels.append((dict(ref), dict(actor), reason))
        return {"accepted": True, "run_id": ref["run_id"]}

    async def close(
        self,
        ref: Mapping[str, object],
        actor: Mapping[str, object],
    ) -> None:
        self.closes.append((dict(ref), dict(actor)))
        self.active_refs.discard(str(ref["run_id"]))


def _event(
    run_id: str,
    session_id: str,
    seq: int,
    kind: str,
    status: str,
    *,
    payload: Mapping[str, object] | None = None,
    error: Mapping[str, object] | None = None,
    driver_kind: str = "react",
) -> _Event:
    return _Event(
        event_id=f"event-{run_id}-{seq}",
        run_id=run_id,
        root_run_id=run_id,
        session_id=session_id,
        durable_seq=seq,
        candidate=_Candidate(
            kind=kind,
            status=status,
            driver_kind=driver_kind,
            payload=payload or {},
            error=error,
        ),
    )


def _pipe(
    client: _RunClient,
    *,
    session_id: str = "voice-session",
    permission_gate: object | None = None,
    broadcast: object | None = None,
    tts: _TTS | None = None,
) -> tuple[VoicePipeline, _ForbiddenAgent, _TTS, _WS]:
    agent = _ForbiddenAgent()
    tts = tts or _TTS()
    control = _WS()
    pipe = VoicePipeline(
        vad=_VAD(),
        asr=_ASR(),
        agent=agent,
        tts=tts,
        control_ws=control,
        session_id=session_id,
        # Supplying the complete old tool stack proves the injected client is
        # selected before the legacy AgentLoop branch.
        service_context={},
        tool_registry_v2=object(),
        permission_gate_v2=permission_gate or object(),
        local_llm=object(),
        broadcast=broadcast,
        run_client=client,
    )
    return pipe, agent, tts, control


async def _wait_until(predicate: Callable[[], bool]) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("condition did not become true")


@pytest.mark.asyncio
async def test_short_answer_uses_run_start_and_drives_transcript_and_tts() -> None:
    async def script(run_id: str, session_id: str, _: _RunClient) -> AsyncIterator[_Event]:
        yield _event(
            run_id,
            session_id,
            1,
            "transcript",
            "succeeded",
            payload={"text": "short "},
        )
        yield _event(
            run_id,
            session_id,
            2,
            "final",
            "succeeded",
            payload={"text": "short answer"},
        )

    client = _RunClient({"voice": script})
    pipe, agent, tts, _ = _pipe(client)
    audio = _WS()

    result = await pipe._process_utterance(b"pcm", audio)

    assert result == "short answer"
    assert client.starts == [
        (
            {
                "text": "hello from voice",
                "mode": "companion",
                "canonical_messages": [
                    {"role": "user", "content": "hello from voice"}
                ],
                "proposed_tools": [],
                "payload": {
                    "loop": {
                        "user_request": "hello from voice",
                        "is_sentinel_run": False,
                    }
                },
            },
            {"session_id": "voice-session", "venue": "voice"},
            "voice-run-1",
        )
    ]
    assert agent.calls == 0
    assert tts.texts == ["short answer"]
    assert audio.binary_frames == [b"\x01\x00\x00\x00\x00"]
    assert any(
        frame.get("type") == "transcript"
        and frame["payload"] == {"text": "short answer", "role": "assistant"}
        for frame in audio.json_frames
    )
    assert client.closes == [
        (
            {"run_id": "voice-run-1", "expected_session_id": "voice-session"},
            {"session_id": "voice-session", "venue": "voice"},
        )
    ]
    assert client.active_refs == set()


@pytest.mark.asyncio
async def test_voice_catalog_is_not_misreported_as_proposed_tools() -> None:
    async def script(run_id: str, session_id: str, _: _RunClient) -> AsyncIterator[_Event]:
        yield _event(
            run_id, session_id, 1, "final", "succeeded", payload={"text": "ok"}
        )

    client = _RunClient({"voice": script})
    pipe, _agent, _tts, audio = _pipe(client)
    pipe._tool_registry_v2 = type(
        "Registry", (), {"list_tools": lambda self: ["write_file", "shell"]}
    )()

    await pipe._process_utterance(b"pcm", audio)

    assert client.starts[0][0]["proposed_tools"] == []


@pytest.mark.asyncio
async def test_streaming_tags_drive_live2d_but_never_reach_transcript_or_tts() -> None:
    raw = "Hello [emotion:happy] [action:wave]friend"

    async def script(run_id: str, session_id: str, _: _RunClient) -> AsyncIterator[_Event]:
        yield _event(
            run_id,
            session_id,
            1,
            "assistant_delta",
            "succeeded",
            payload={"delta": "Hello [emo", "provider": "cloud"},
        )
        yield _event(
            run_id,
            session_id,
            2,
            "assistant_delta",
            "succeeded",
            payload={"delta": "tion:happy] [action:wa"},
        )
        yield _event(
            run_id,
            session_id,
            3,
            "assistant_delta",
            "succeeded",
            payload={"delta": "ve]friend"},
        )
        yield _event(
            run_id,
            session_id,
            4,
            "final",
            "succeeded",
            payload={"text": raw},
        )

    client = _RunClient({"voice": script})
    loud_pcm = (8000).to_bytes(2, "little", signed=True) * 2
    pipe, _, tts, control = _pipe(client, tts=_TTS((loud_pcm,)))
    audio = _WS()

    result = await pipe._process_utterance(b"pcm", audio)

    assert result == "Hello  friend"
    assert tts.texts == ["Hello  friend"]
    assert all("[emotion:" not in text and "[action:" not in text for text in tts.texts)
    assistant = [
        frame["payload"]
        for frame in audio.json_frames
        if frame.get("type") == "transcript"
        and frame["payload"].get("role") == "assistant"
    ]
    assert assistant == [
        {"text": "Hello  friend", "role": "assistant", "provider": "cloud"}
    ]
    assert {frame["type"] for frame in control.json_frames} >= {
        "emotion_change",
        "action_trigger",
        "lip_sync",
    }
    assert next(
        frame for frame in control.json_frames if frame["type"] == "emotion_change"
    )["payload"] == {"value": "happy"}
    assert next(
        frame for frame in control.json_frames if frame["type"] == "action_trigger"
    )["payload"] == {"value": "wave"}
    assert next(
        frame for frame in control.json_frames if frame["type"] == "lip_sync"
    )["payload"]["amplitude"] == pytest.approx(1.0)
    assert client.active_refs == set()


@pytest.mark.asyncio
async def test_tool_failure_keeps_failed_status_and_final_reply_reaches_tts() -> None:
    async def script(run_id: str, session_id: str, _: _RunClient) -> AsyncIterator[_Event]:
        yield _event(
            run_id,
            session_id,
            1,
            "tool_outcome",
            "failed",
            payload={"tool": "write_file"},
            error={"code": "write_failed", "message": "disk full"},
        )
        yield _event(
            run_id,
            session_id,
            2,
            "final",
            "succeeded",
            payload={"text": "I could not write that file."},
        )

    client = _RunClient({"voice": script})
    pipe, _, tts, _ = _pipe(client)
    audio = _WS()

    await pipe._process_utterance(b"pcm", audio)

    failed = [
        frame["payload"]
        for frame in audio.json_frames
        if frame.get("type") == "run_event"
        and frame["payload"].get("kind") == "tool_outcome"
    ]
    assert len(failed) == 1
    assert failed[0]["status"] == "failed"
    assert failed[0]["error"] == {
        "code": "write_failed",
        "message": "disk full",
    }
    assert failed[0].get("ok") is None
    assert tts.texts == ["I could not write that file."]
    assert len(client.closes) == 1
    assert client.active_refs == set()


@pytest.mark.asyncio
async def test_permission_waiting_uses_run_signal_without_global_gate_mutation() -> None:
    async def script(
        run_id: str,
        session_id: str,
        client: _RunClient,
    ) -> AsyncIterator[_Event]:
        yield _event(
            run_id,
            session_id,
            1,
            "permission",
            "waiting",
            payload={"decision_id": "decision-1", "prompt": "allow write?"},
        )
        await client.signal_received.wait()
        yield _event(
            run_id,
            session_id,
            2,
            "final",
            "succeeded",
            payload={"text": "permission accepted"},
        )

    gate = type("Gate", (), {})()
    gate.current_source = "text"
    client = _RunClient({"voice": script})
    pipe, _, tts, _ = _pipe(client, permission_gate=gate)
    audio = _WS()
    task = asyncio.create_task(pipe._process_utterance(b"pcm", audio))
    await _wait_until(
        lambda: any(
            frame.get("type") == "run_event"
            and frame["payload"].get("status") == "waiting"
            for frame in audio.json_frames
        )
    )

    receipt = await pipe.signal_current_run(
        decision_id="decision-1",
        response={"allow": True},
        nonce="nonce-1",
        version=3,
    )
    await task

    assert receipt == {"accepted": True, "decision_id": "decision-1"}
    assert client.signals == [
        (
            {"run_id": "voice-run-1", "expected_session_id": "voice-session"},
            {"session_id": "voice-session", "venue": "voice"},
            {
                "decision_id": "decision-1",
                "response": {"allow": True},
                "nonce": "nonce-1",
                "version": 3,
            },
        )
    ]
    assert gate.current_source == "text"
    assert tts.texts == ["permission accepted"]
    assert len(client.closes) == 1


@pytest.mark.asyncio
async def test_durable_handoff_accepted_progress_are_not_final() -> None:
    release_final = asyncio.Event()

    async def script(run_id: str, session_id: str, _: _RunClient) -> AsyncIterator[_Event]:
        yield _event(
            run_id,
            session_id,
            1,
            "accepted",
            "accepted",
            payload={"profile": "deep_research"},
            driver_kind="workflow",
        )
        yield _event(
            run_id,
            session_id,
            2,
            "progress",
            "accepted",
            payload={"percent": 40},
            driver_kind="workflow",
        )
        await release_final.wait()
        yield _event(
            run_id,
            session_id,
            3,
            "final",
            "succeeded",
            payload={"text": "research complete"},
            driver_kind="workflow",
        )

    client = _RunClient({"voice": script})
    pipe, _, tts, _ = _pipe(client)
    audio = _WS()
    task = asyncio.create_task(pipe._process_utterance(b"pcm", audio))
    await _wait_until(
        lambda: len(
            [frame for frame in audio.json_frames if frame.get("type") == "run_event"]
        )
        >= 2
    )

    assert tts.texts == []
    assert not any(
        frame.get("type") == "transcript"
        and frame["payload"].get("role") == "assistant"
        for frame in audio.json_frames
    )
    release_final.set()
    await task

    events = [
        (frame["payload"]["kind"], frame["payload"]["status"])
        for frame in audio.json_frames
        if frame.get("type") == "run_event"
    ]
    assert events == [
        ("accepted", "accepted"),
        ("progress", "accepted"),
        ("final", "succeeded"),
    ]
    assert tts.texts == ["research complete"]
    assert len(client.closes) == 1


@pytest.mark.asyncio
async def test_failed_terminal_is_visible_but_never_spoken() -> None:
    async def script(run_id: str, session_id: str, _: _RunClient) -> AsyncIterator[_Event]:
        yield _event(
            run_id,
            session_id,
            1,
            "failed",
            "failed",
            payload={"text": "internal failure text must not be spoken"},
            error={"code": "provider_failed", "message": "Provider unavailable"},
        )

    client = _RunClient({"voice": script})
    pipe, _, tts, _ = _pipe(client)
    audio = _WS()

    result = await pipe._process_utterance(b"pcm", audio)

    assert result is None
    assert tts.texts == []
    assert any(
        frame.get("type") == "error"
        and frame["payload"] == {
            "message": "Provider unavailable",
            "run_id": "voice-run-1",
            "status": "failed",
        }
        for frame in audio.json_frames
    )
    assert len(client.closes) == 1


@pytest.mark.asyncio
async def test_barge_in_cancels_only_the_explicit_current_run_id() -> None:
    hold = asyncio.Event()

    async def script(run_id: str, session_id: str, _: _RunClient) -> AsyncIterator[_Event]:
        yield _event(run_id, session_id, 1, "accepted", "accepted")
        await hold.wait()

    client = _RunClient({"voice": script})
    pipe, _, _, _ = _pipe(client)
    audio = _WS()
    task = asyncio.create_task(pipe._process_utterance(b"pcm", audio))
    pipe._current_task = task
    await _wait_until(lambda: pipe._current_run_id == "voice-run-1")

    pipe.interrupt()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client.cancels == [
        (
            {"run_id": "voice-run-1", "expected_session_id": "voice-session"},
            {"session_id": "voice-session", "venue": "voice"},
            "voice_barge_in",
        )
    ]
    assert pipe._current_run_id is None
    assert pipe._current_task is None
    assert len(client.closes) == 1


@pytest.mark.asyncio
async def test_voice_cancel_does_not_cancel_simultaneous_text_run() -> None:
    text_release = asyncio.Event()
    voice_hold = asyncio.Event()

    async def text_script(
        run_id: str,
        session_id: str,
        _: _RunClient,
    ) -> AsyncIterator[_Event]:
        await text_release.wait()
        yield _event(
            run_id,
            session_id,
            1,
            "final",
            "succeeded",
            payload={"text": "text remains alive"},
        )

    async def voice_script(
        run_id: str,
        session_id: str,
        _: _RunClient,
    ) -> AsyncIterator[_Event]:
        yield _event(run_id, session_id, 1, "accepted", "accepted")
        await voice_hold.wait()

    client = _RunClient({"text": text_script, "voice": voice_script})
    text_handle = await client.start(
        {"text": "parallel text request"},
        {"session_id": "text-session", "venue": "text"},
    )
    text_events: list[_Event] = []

    async def consume_text() -> None:
        async for event in text_handle.events:
            text_events.append(event)

    text_task = asyncio.create_task(consume_text())
    pipe, _, _, _ = _pipe(client)
    voice_task = asyncio.create_task(pipe._process_utterance(b"pcm", _WS()))
    pipe._current_task = voice_task
    await _wait_until(lambda: pipe._current_run_id == "voice-run-1")

    pipe.interrupt()
    with pytest.raises(asyncio.CancelledError):
        await voice_task
    text_release.set()
    await text_task

    assert [event.run_id for event in text_events] == ["text-run-1"]
    assert client.cancels == [
        (
            {"run_id": "voice-run-1", "expected_session_id": "voice-session"},
            {"session_id": "voice-session", "venue": "voice"},
            "voice_barge_in",
        )
    ]
    assert len(client.closes) == 1


@pytest.mark.asyncio
async def test_transport_disconnect_cancels_closes_and_releases_current_ref() -> None:
    class _DisconnectingWS(_WS):
        async def send_json(self, frame: dict[str, Any]) -> None:
            if frame.get("type") == "run_event":
                raise ConnectionError("audio websocket disconnected")
            await super().send_json(frame)

    async def script(run_id: str, session_id: str, _: _RunClient) -> AsyncIterator[_Event]:
        yield _event(run_id, session_id, 1, "accepted", "accepted")
        await asyncio.sleep(60)

    client = _RunClient({"voice": script})
    pipe, _, tts, _ = _pipe(client)

    result = await pipe._process_utterance(b"pcm", _DisconnectingWS())

    assert result is None
    assert tts.texts == []
    assert client.cancels == [
        (
            {"run_id": "voice-run-1", "expected_session_id": "voice-session"},
            {"session_id": "voice-session", "venue": "voice"},
            "voice_transport_error",
        )
    ]
    assert client.closes == [
        (
            {"run_id": "voice-run-1", "expected_session_id": "voice-session"},
            {"session_id": "voice-session", "venue": "voice"},
        )
    ]
    assert pipe._current_run_id is None
    assert pipe._current_run_handle is None
    assert pipe._current_task is None
    assert client.active_refs == set()


@pytest.mark.asyncio
async def test_transcripts_and_typed_events_are_fanned_out_to_peer_transport() -> None:
    peer_frames: list[dict[str, Any]] = []

    async def broadcast(_: object, frame: dict[str, Any]) -> None:
        peer_frames.append(frame)

    async def script(run_id: str, session_id: str, _: _RunClient) -> AsyncIterator[_Event]:
        yield _event(
            run_id,
            session_id,
            1,
            "final",
            "succeeded",
            payload={"text": "shared answer", "served_by": "local"},
        )

    client = _RunClient({"voice": script})
    pipe, _, tts, _ = _pipe(client, broadcast=broadcast)
    audio = _WS()

    await pipe._process_utterance(b"pcm", audio)

    assert tts.texts == ["shared answer"]
    assert [frame["type"] for frame in peer_frames] == [
        "chat_v2_user_echo",
        "run_event",
        "chat_v2_final",
    ]
    assert peer_frames[0]["payload"] == {
        "session_id": "voice-session",
        "text": "hello from voice",
    }
    assert peer_frames[-1]["payload"] == {
        "session_id": "voice-session",
        "text": "shared answer",
    }
    assistant = next(
        frame["payload"]
        for frame in audio.json_frames
        if frame.get("type") == "transcript"
        and frame["payload"].get("role") == "assistant"
    )
    assert assistant["provider"] == "local"


def test_run_client_is_registered_by_r6_production_bootstrap() -> None:
    parameter = inspect.signature(VoicePipeline).parameters["run_client"]
    assert parameter.default is None
    assert inspect.signature(VoicePipeline).parameters["run_session"].default is None

    tree = ast.parse((ROOT / "backend" / "main.py").read_text(encoding="utf-8"))
    production_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "VoicePipeline"
    ]
    assert production_calls
    assert all("run_client" in {keyword.arg for keyword in call.keywords}
               for call in production_calls)
    assert all("run_session" not in {keyword.arg for keyword in call.keywords}
               for call in production_calls)

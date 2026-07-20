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
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def synthesize_pcm_stream(self, text: str) -> AsyncIterator[bytes]:
        self.texts.append(text)
        yield b"\x00\x00\x00\x00"


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


_Script = Callable[[str, str, "_RunClient"], AsyncIterator[_Event]]


class _RunClient:
    def __init__(self, scripts: Mapping[str, _Script]) -> None:
        self.scripts = dict(scripts)
        self.starts: list[tuple[dict[str, object], dict[str, object], str]] = []
        self.signals: list[
            tuple[dict[str, object], dict[str, object], dict[str, object]]
        ] = []
        self.cancels: list[tuple[dict[str, object], dict[str, object], str]] = []
        self.signal_received = asyncio.Event()
        self._venue_counts: dict[str, int] = {}

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
        self.starts.append((dict(request), dict(host), run_id))
        return _Handle(run_id, self.scripts[venue](run_id, session_id, self))

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
) -> tuple[VoicePipeline, _ForbiddenAgent, _TTS, _WS]:
    agent = _ForbiddenAgent()
    tts = _TTS()
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
            {"text": "hello from voice"},
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


def test_run_client_is_opt_in_and_not_registered_by_production_bootstrap() -> None:
    parameter = inspect.signature(VoicePipeline).parameters["run_client"]
    assert parameter.default is None

    tree = ast.parse((ROOT / "backend" / "main.py").read_text(encoding="utf-8"))
    production_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "VoicePipeline"
    ]
    assert production_calls
    assert all(
        "run_client" not in {keyword.arg for keyword in call.keywords}
        for call in production_calls
    )

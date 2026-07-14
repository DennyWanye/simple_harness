# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from typing import Any, AsyncIterator
from types import SimpleNamespace
import sys

import pytest

import pipeline.voice_pipeline as voice_pipeline
from pipeline.voice_pipeline import VoicePipeline

SID1 = "11111111-1111-4111-8111-111111111111"


class _FakeWS:
    def __init__(self) -> None:
        self.json_frames: list[dict] = []
        self.binary_frames: list[bytes] = []

    async def send_json(self, data: dict) -> None:
        self.json_frames.append(data)

    async def send_bytes(self, data: bytes) -> None:
        self.binary_frames.append(data)


class _FakeASR:
    async def transcribe(self, audio: bytes) -> str:
        return "/new research rust tokio"


class _FakeAgent:
    async def chat_stream(self, messages, *, session_id: str) -> AsyncIterator[str]:
        yield "legacy"


class _FakeTTS:
    async def synthesize_pcm_stream(self, text: str) -> AsyncIterator[bytes]:
        if False:
            yield b""


class _FakeVAD:
    threshold = 0.5

    def set_threshold(self, value: float) -> None:
        self.threshold = value


class _ServiceContext:
    def __init__(self, mapping: dict[str, Any]) -> None:
        self._mapping = mapping

    def get(self, name: str) -> Any:
        return self._mapping.get(name)


class _SessionDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.ensure_calls: list[tuple[str, dict[str, Any] | None]] = []

    async def ensure_session(
        self,
        session_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        self.ensure_calls.append((session_id, metadata))
        return session_id

    async def append_message(self, *, session_id: str, role: str, content: str) -> int:
        self.calls.append((session_id, role, content))
        return len(self.calls)


class _Assembler:
    enabled = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def assemble(self, **kwargs: Any):
        self.calls.append(kwargs)
        return None


class _ContextOSAssembler:
    enabled = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def assemble(self, **kwargs: Any):
        from deskpet.agent.assembler.bundle import ContextBundle
        from deskpet.tools.capabilities import ToolExposureIntent

        self.calls.append(kwargs)
        return ContextBundle(
            task_type="web_search",
            history=[{"role": "assistant", "content": "prior"}],
            tool_exposure_intent=ToolExposureIntent(
                direct_selectors=("toolset:web",),
            ),
        )


class _PreparedPlanner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def prepare_initial(self, bundle, **kwargs: Any):
        from deskpet.agent.assembler.bundle import PreparedContext
        from deskpet.tools.capabilities import PreparedToolSet

        self.calls.append({"bundle": bundle, **kwargs})
        tool_set = PreparedToolSet.create(
            scope_id="voice-scope",
            revision=1,
            registry_revision=7,
            direct=(),
            deferred=(),
            activated=(),
            denied_names=(),
            policy_fingerprint="policy",
            decisions=(),
        )
        prepared = PreparedContext(
            messages=[{"role": "user", "content": "prepared voice"}],
            tool_set=tool_set,
        )
        return SimpleNamespace(prepared_context=prepared)


class _ScopeStore:
    def __init__(self) -> None:
        self.calls = []

    def open(self, tool_set, eligibility, **kwargs):
        self.calls.append((tool_set, eligibility, kwargs))


class _PreparedLoop:
    def __init__(self) -> None:
        self.calls: list[tuple[list[dict], dict[str, Any]]] = []

    async def run(self, messages, **kwargs):
        from agent.agent_loop import FinalEvent

        self.calls.append((messages, kwargs))
        yield FinalEvent(type="final", task_id="voice", iteration=1, content="prepared")


class _ToolRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    def schemas(self, enabled_toolsets: Any = None) -> list[dict]:
        return [
            {
                "name": "deepresearch",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "user_request": {"type": "string"},
                    },
                },
            }
        ]

    async def execute_tool(
        self,
        name: str,
        params: dict[str, Any],
        session_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        self.calls.append((name, dict(params), session_id))
        return {"ok": True, "result": "tool ok", "error": None}


class _LocalLLM:
    model = "stub-model"
    base_url = "http://stub"

    def __init__(self) -> None:
        self.calls = 0

    async def chat_with_tools(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "deepresearch",
                        "arguments": {"topic": "drifted"},
                    }
                ],
                "stop_reason": "tool_use",
                "model": "stub",
                "usage": {},
            }
        return {
            "content": "final answer",
            "tool_calls": [],
            "stop_reason": "end_turn",
            "model": "stub",
            "usage": {},
        }


class _RecordingBroadcast:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict]] = []

    async def __call__(self, originator, msg) -> None:
        self.calls.append((originator, msg))


@pytest.mark.asyncio
async def test_voice_new_scope_uses_one_effective_sid_and_loop_user_request(monkeypatch):
    from deskpet.session.task_scope import TaskSessionManager

    monkeypatch.setattr(
        voice_pipeline,
        "task_session_manager",
        TaskSessionManager(id_factory=lambda: SID1),
    )
    sdb = _SessionDB()
    assembler = _Assembler()
    tools = _ToolRegistry()
    bc = _RecordingBroadcast()
    control = _FakeWS()
    local_llm = _LocalLLM()
    service_context = _ServiceContext(
        {
            "session_db": sdb,
            "context_assembler": assembler,
            "tool_router": tools,
        }
    )
    pipe = VoicePipeline(
        vad=_FakeVAD(),
        asr=_FakeASR(),
        agent=_FakeAgent(),
        tts=_FakeTTS(),
        control_ws=control,
        session_id="default",
        service_context=service_context,
        tool_registry_v2=tools,
        permission_gate_v2=object(),
        local_llm=local_llm,
        broadcast=bc,
    )

    response = await pipe._process_utterance(b"fake-pcm", _FakeWS())

    assert response == "final answer"
    assert sdb.ensure_calls == [
        (
            SID1,
            {
                "origin": "voice_task",
                "base_session_id": "default",
                "reason": "explicit_new",
            },
        )
    ]
    assert sdb.calls == [
        (SID1, "user", "research rust tokio"),
        (SID1, "assistant", "final answer"),
    ]
    assert assembler.calls[0]["session_id"] == SID1
    assert assembler.calls[0]["user_message"] == "research rust tokio"
    assert tools.calls == [
        (
            "deepresearch",
            {"topic": "drifted", "user_request": "research rust tokio"},
            SID1,
        )
    ]
    assert [msg["payload"]["session_id"] for _, msg in bc.calls] == [
        SID1,
        SID1,
    ]


@pytest.mark.asyncio
async def test_voice_context_os_uses_prepared_contract_and_isolated_venue(monkeypatch):
    planner = _PreparedPlanner()
    scopes = _ScopeStore()
    loop = _PreparedLoop()
    fake_main = SimpleNamespace(
        build_agent=lambda *args, **kwargs: loop,
        _get_receipt_store=lambda *args, **kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "main", fake_main)

    tools = _ToolRegistry()
    sdb = _SessionDB()
    assembler = _ContextOSAssembler()
    sc = _ServiceContext({
        "session_db": sdb,
        "context_assembler": assembler,
        "context_request_planner": planner,
        "tool_capability_scope_store": scopes,
    })
    app_config = SimpleNamespace(
        features=SimpleNamespace(context_os_v1=True),
        raw={"context": {"manager": {"v2_enabled": True}}},
        tools=SimpleNamespace(),
        workflows=SimpleNamespace(trace_enabled=False),
    )
    pipe = VoicePipeline(
        vad=_FakeVAD(),
        asr=_FakeASR(),
        agent=_FakeAgent(),
        tts=_FakeTTS(),
        session_id="voice-session",
        service_context=sc,
        tool_registry_v2=tools,
        local_llm=_LocalLLM(),
        app_config=app_config,
    )

    async def _no_codify():
        return None

    monkeypatch.setattr(pipe, "_maybe_codify_voice", _no_codify)
    result = await pipe._run_with_tools(
        "search this", _FakeWS(), session_id="voice-session"
    )

    assert result == "prepared"
    assert len(planner.calls) == 1
    call = planner.calls[0]
    assert call["eligibility"].mode == "voice"
    assert call["eligibility"].session_id == "voice-session"
    assert call["current_message_id"] == 1
    assert assembler.calls[0]["config"]["features"]["context_os_v1"] is True
    assert assembler.calls[0]["current_message_id"] == 1
    assert call["bundle"].tool_exposure_intent.direct_selectors == ("toolset:web",)
    assert any(
        fragment.fragment_id == "venue.voice.response"
        for fragment in call["bundle"].fragments
    )
    assert scopes.calls[0][1] is call["eligibility"]
    sent_messages, run_kwargs = loop.calls[0]
    assert sent_messages == [{"role": "user", "content": "prepared voice"}]
    assert run_kwargs["prepared_context"].tool_set is scopes.calls[0][0]
    assert run_kwargs["context_request_id"] == call["eligibility"].request_id

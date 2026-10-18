# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S10 E2E integration tests — FastAPI TestClient with the real app wiring.

These are NOT offline unit tests: they import the actual ``main.app``
which runs the full boot sequence (config, provider registration, crash
reporter, tier detection). What they DO avoid is the network/hardware
layer — LLM/ASR/TTS backends are replaced with in-process fakes via
service_context mutation, so the tests stay fast and hermetic.

Coverage goals (R20):
1. End-to-end chat roundtrip through agent + memory + tool registry.
2. Control-channel interrupt cancels the session run and acks.
3. Redacting memory wrapper actually catches secrets on the write path.
4. Tool registry confirmation gate rejects high-risk tools by default.
5. Unknown message type returns a structured error, not a crash.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from host.connections import _control_connections
from host.runtime import SHARED_SECRET, service_context
from main import app

# ---------------------------------------------------------------------
# Fakes — keep tests hermetic, no Ollama / Whisper / edge-tts required.
# ---------------------------------------------------------------------


class FakeLLM:
    """Stand-in for OpenAICompatibleProvider that streams a deterministic reply."""

    def __init__(self, reply: str = "hi from fake llm"):
        self.reply = reply
        self.last_messages: list[dict[str, str]] | None = None

    async def chat_stream(
        self, messages, *, temperature: float = 0.7, max_tokens: int = 2048
    ) -> AsyncIterator[str]:
        self.last_messages = list(messages)
        for ch in self.reply:
            yield ch

    async def health_check(self) -> bool:
        return True


@pytest.fixture
def fake_llm_agent(monkeypatch):
    """Swap the live agent for one backed by FakeLLM + real memory/tools.

    Yields the FakeLLM so tests can inspect what was sent / mutate reply.
    """
    from agent.providers.simple_llm import SimpleLLMAgent
    from agent.providers.tool_using import ToolUsingAgent

    fake = FakeLLM()
    registry = service_context.get("tool_router")
    new_agent = ToolUsingAgent(base=SimpleLLMAgent(fake, memory=None), registry=registry)

    old_agent = service_context.get("agent_engine")
    service_context.register("agent_engine", new_agent)
    try:
        yield fake
    finally:
        service_context.register("agent_engine", old_agent)


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


@pytest.mark.skip(
    reason="P4-S20-LLM-Unified: chat path no longer uses agent_engine."
    "chat_stream() / fake_llm_agent. Replace with AgentLoop fixture."
)
def test_full_chat_roundtrip_uses_fake_llm(fake_llm_agent):
    """Control WS → chat → agent streams → WS emits chat_response."""
    fake_llm_agent.reply = "你好，我是测试回复"
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/control",
        headers={"X-Shared-Secret": SHARED_SECRET},
        params={"session_id": "e2e_chat"},
    ) as ws:
        ws.receive_json()  # P3-S2: drain startup_status
        ws.send_json({"type": "chat", "payload": {"text": "ping"}})
        msg = ws.receive_json()

    assert msg["type"] == "chat_response"
    assert msg["payload"]["text"] == "你好，我是测试回复"
    # The user's text must have reached the LLM.
    assert fake_llm_agent.last_messages is not None
    assert fake_llm_agent.last_messages[-1]["content"] == "ping"


def test_unknown_message_type_returns_error(fake_llm_agent):
    """Defensive path — junk type doesn't crash the socket."""
    client = TestClient(app)
    with client.websocket_connect("/ws/control", headers={"X-Shared-Secret": SHARED_SECRET}) as ws:
        ws.receive_json()  # P3-S2: drain startup_status
        ws.send_json({"type": "nonsense_xyz"})
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert "nonsense_xyz" in msg["payload"]["message"]


def test_interrupt_acks_and_cancels_run(fake_llm_agent, monkeypatch):
    """控制通道的 interrupt 取消本会话的运行并回 interrupt_ack。"""
    import host.control_decisions as control_decisions

    cancelled: list[tuple[str, str, str]] = []

    async def _fake_cancel(session_id: str, run_id: str, *, reason: str) -> None:
        cancelled.append((session_id, run_id, reason))

    monkeypatch.setattr(control_decisions, "_cancel_product_harness_run", _fake_cancel)
    client = TestClient(app)
    with client.websocket_connect(
        f"/ws/control?secret={SHARED_SECRET}&session_id=e2e_interrupt"
    ) as ws:
        ws.receive_json()  # P3-S2: drain startup_status
        ws.send_json({"type": "interrupt", "payload": {"run_id": "run-1"}})
        ack = ws.receive_json()
    assert ack["type"] == "interrupt_ack"
    assert cancelled == [("e2e_interrupt", "run-1", "voice_interrupt")]


@pytest.mark.skip(
    reason="P4-S20-LLM-Unified: chat path no longer goes through "
    "fake_llm_agent.chat_stream(); secret redaction needs new fixture."
)
def test_redacting_memory_store_is_active(fake_llm_agent):
    """A user message containing a secret must be redacted before storage.

    This exercises the real RedactingMemoryStore wrapped around the
    SQLite backend — no mock. We inspect what the LLM actually saw on
    turn 2 (which reads from memory).
    """
    session = "e2e_redact"
    client = TestClient(app)
    with client.websocket_connect(f"/ws/control?secret={SHARED_SECRET}&session_id={session}") as ws:
        ws.receive_json()  # P3-S2: drain startup_status
        # Turn 1: send a fake API key. The agent just echoes.
        ws.send_json(
            {
                "type": "chat",
                "payload": {"text": "my token is sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
            }
        )
        ws.receive_json()

        # Turn 2: trigger a reply so memory is re-read into the prompt.
        ws.send_json({"type": "chat", "payload": {"text": "what did I say?"}})
        ws.receive_json()

    # The LLM on turn 2 saw the full prompt including history. Memory
    # should have rewritten the secret into a REDACTED marker.
    msgs = fake_llm_agent.last_messages or []
    serialized = " ".join(m.get("content", "") for m in msgs)
    assert "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" not in serialized
    assert "REDACTED" in serialized


def test_tool_registry_denies_high_risk_by_default():
    """S6 fail-closed: a requires_confirmation=True tool is blocked unless
    an explicit confirm callback approves it. We test via the public
    chat_stream path — the LLM emits <tool>NAME</tool>, the agent sees
    requires_confirmation=True, default confirm deny_all refuses it."""
    import asyncio

    from agent.providers.tool_using import ToolUsingAgent
    from tools.base import ToolSpec
    from tools.registry import ToolRegistry

    class DangerousTool:
        spec = ToolSpec(
            name="delete_all",
            description="pretend-destructive",
            requires_confirmation=True,
        )

        async def invoke(self, **kwargs):  # noqa: ARG002
            return "DESTROYED"

    registry = ToolRegistry()
    registry.register(DangerousTool())

    class _EmittingAgent:
        """Base agent that asks to call delete_all."""

        async def chat_stream(self, messages, *, session_id="default"):  # noqa: ARG002
            yield "sure <tool>delete_all</tool>"

    agent = ToolUsingAgent(base=_EmittingAgent(), registry=registry, inject_system_prompt=False)

    async def run():
        out = ""
        async for tok in agent.chat_stream(
            [{"role": "user", "content": "drop the table"}],
            session_id="sess",
        ):
            out += tok
        return out

    out = asyncio.run(run())
    assert "refused" in out.lower()
    assert "DESTROYED" not in out


def test_control_connection_tracked_and_released(fake_llm_agent):
    """The control connection must register itself in _control_connections
    for the session and vanish on disconnect."""
    session = "e2e_track"
    client = TestClient(app)
    with client.websocket_connect(f"/ws/control?secret={SHARED_SECRET}&session_id={session}") as ws:
        ws.receive_json()  # P3-S2: drain startup_status
        ws.send_json({"type": "ping"})
        ws.receive_json()
        assert session in _control_connections
    # After context exits the WS is closed; the server pops it in the
    # disconnect handler.
    assert session not in _control_connections

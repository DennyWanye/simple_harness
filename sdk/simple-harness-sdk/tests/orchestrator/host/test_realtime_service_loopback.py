# SPDX-License-Identifier: Apache-2.0
"""Real Service SDK lifecycle and tool acknowledgements over an in-memory wire."""

from __future__ import annotations

import asyncio
import json
import tomllib
from copy import deepcopy
from importlib.metadata import requires
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from test_realtime_bridge import Audio
from test_session import SPEC, make_session

from agent_orchestrator.host import VoiceProfile

pytest.importorskip("simple_harness_service.realtime")
from simple_harness_service.realtime.adapters.qwen_omni import QWEN_CAPABILITY, QwenOmniAdapter
from simple_harness_service.realtime.contracts import MintedRealtimeCredential, RealtimeError
from simple_harness_service.realtime.relay_control import RelayControlCodec
from simple_harness_service.realtime.session import ManagedRealtimeSession


class Connection:
    def __init__(self, normalize=False, mismatch=None, *, provider_message_ids=False, corrupt=None):
        self.queue = asyncio.Queue()
        self.sent = []
        self.counter = 0
        self.normalize = normalize
        self.mismatch = mismatch
        self.provider_message_ids = provider_message_ids
        self.corrupt = corrupt
        self.responses = 0

    async def event(self, kind, **fields):
        self.counter += 1
        await self.queue.put(
            json.dumps({"event_id": f"event_{self.counter}", "type": kind, **fields})
        )

    async def send_text(self, payload):
        value = json.loads(payload)
        self.sent.append(value)
        if value["type"] == "session.update":
            session = deepcopy(value["session"])
            session.pop("model")
            session["id"] = "fixture-session"
            session["turn_detection"].update(create_response=True, interrupt_response=True)
            if self.normalize:
                for tool in session["tools"]:
                    tool["parameters"].pop("description", None)
                    for field in tool["parameters"].get("properties", {}).values():
                        if field.get("type") == "array":
                            for key in ("items", "minItems", "maxItems"):
                                field.pop(key, None)
            if self.mismatch == "name":
                session["tools"][0]["name"] += "_unexpected"
            elif self.mismatch == "required":
                session["tools"][0]["parameters"]["required"] = ["unexpected"]
            elif self.mismatch == "properties":
                session["tools"][0]["parameters"]["properties"]["unexpected"] = {
                    "type": "string"
                }
            elif self.mismatch == "missing":
                session["tools"].pop()
            await self.event("session.updated", session=session)
        elif value["type"] == "conversation.item.create":
            item = {**deepcopy(value["item"]), "status": "completed"}
            if item["type"] == "message":
                if self.provider_message_ids:
                    item["id"] = f"item_provider_{self.counter}"
                if self.corrupt == "text":
                    item["content"][0]["text"] = "A different message"
                elif self.corrupt == "role":
                    item["role"] = "system" if item["role"] == "user" else "user"
                elif self.corrupt == "status":
                    item["status"] = "in_progress"
            item.setdefault("id", f"output-{self.counter}")
            await self.event("conversation.item.created", item=item)
        elif value["type"] == "response.create":
            self.responses += 1
            await self.event("response.created", response={"id": f"followup-{self.responses}"})
        elif value["type"].startswith("tokenseller."):
            await self.queue.put(None)

    async def receive_text(self):
        return await self.queue.get()

    async def close(self, code=1000, reason=""):
        await self.queue.put(None)


def factory(connection):
    def build(dispatcher):
        class Client:
            async def open(self, request):
                credential = MintedRealtimeCredential(
                    secret="fixture",
                    expires_at_ms=9999999999999,
                    websocket_path="/v1/realtime/qwen",
                    capability=QWEN_CAPABILITY,
                    capability_document={},
                    capability_digest="0" * 64,
                )
                session = ManagedRealtimeSession(
                    connection=connection,
                    adapter=QwenOmniAdapter(),
                    control=RelayControlCodec(),
                    credential=credential,
                    request=request,
                    correlation="corr_" + "0" * 26,
                    generation=1,
                    tool_dispatcher=dispatcher,
                    close_timeout=0.05,
                    tool_ack_timeout=0.5,
                )
                await session.start()
                return session

        return Client()

    return build


async def wait_for(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


async def tool_call(connection, response_id, name="delegate_task", arguments=None):
    await connection.event(
        "response.output_item.added",
        response_id=response_id,
        output_index=0,
        item={"id": "function-item", "type": "function_call"},
    )
    await connection.event(
        "response.function_call_arguments.done",
        response_id=response_id,
        item_id="function-item",
        output_index=0,
        call_id="call-1",
        name=name,
        arguments=json.dumps(SPEC if arguments is None else arguments),
    )
    await connection.event(
        "response.done",
        response={
            "id": response_id,
            "status": "completed",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "input_tokens_details": {"text_tokens": 10, "audio_tokens": 0},
                "output_tokens_details": {"text_tokens": 5, "audio_tokens": 0},
            },
        },
    )


def test_service_loopback_commits_voice_tool_result_ack_and_followup(tmp_path):
    async def run():
        host, session = make_session(tmp_path)
        connection = Connection()
        binding = await session.attach_voice(
            Audio(), VoiceProfile(client_factory=factory(connection))
        )
        try:
            await connection.event(
                "conversation.item.input_audio_transcription.completed",
                item_id="user-1",
                transcript="Write a report",
            )
            await connection.event("response.created", response={"id": "user-response"})
            await tool_call(connection, "user-response")
            await wait_for(lambda: any(v["type"] == "response.create" for v in connection.sent))
            outputs = [
                v
                for v in connection.sent
                if v["type"] == "conversation.item.create"
                and v["item"]["type"] == "function_call_output"
            ]
            assert len(outputs) == 1 and len(host.made) == 1
            assert json.loads(outputs[0]["item"]["output"])["status"] == "accepted"
            await wait_for(lambda: "followup-1" in binding._response_turns)
            assert binding._response_turns["followup-1"] == "voice:user-1"
            assert session.history()[-1]["input"] == "Write a report"
        finally:
            await binding.detach()

    asyncio.run(run())


def test_service_loopback_announcement_context_ack_keeps_control_commands_denied(tmp_path):
    async def run():
        host, session = make_session(tmp_path)
        connection = Connection()
        binding = await session.attach_voice(
            Audio(), VoiceProfile(client_factory=factory(connection))
        )
        try:
            await session.announce("A report is ready")
            await wait_for(lambda: "followup-1" in binding._response_turns)
            await tool_call(connection, "followup-1")
            await wait_for(lambda: connection.responses == 2)
            outputs = [
                v["item"]
                for v in connection.sent
                if v["type"] == "conversation.item.create"
                and v["item"]["type"] == "function_call_output"
            ]
            assert json.loads(outputs[0]["output"])["error"]["code"] == "permission_denied"
            assert not host.made
            assert len([v for v in connection.sent if v["type"] == "session.update"]) == 1
        finally:
            await binding.detach()

    asyncio.run(run())


def test_service_loopback_accepts_normalized_catalogue_with_original_dispatch_validation(tmp_path):
    async def run():
        host, session = make_session(tmp_path)
        connection = Connection(normalize=True)
        binding = await session.attach_voice(
            Audio(), VoiceProfile(client_factory=factory(connection))
        )
        try:
            assert binding.negotiation["status"] == "ready"
            await connection.event(
                "conversation.item.input_audio_transcription.completed",
                item_id="user-1",
                transcript="Write a report",
            )
            await connection.event("response.created", response={"id": "user-response"})
            # Provider normalization must not relax the local command schema.
            await tool_call(
                connection,
                "user-response",
                arguments={"goal": "Write a report", "success_criteria": []},
            )
            await wait_for(
                lambda: any(v["type"] == "conversation.item.create" for v in connection.sent)
            )
            outputs = [
                v["item"] for v in connection.sent if v["type"] == "conversation.item.create"
            ]
            assert json.loads(outputs[0]["output"])["error"] == "invalid_arguments"
            assert not host.made
        finally:
            await binding.detach()

    asyncio.run(run())


@pytest.mark.parametrize("mismatch", ["name", "required", "properties", "missing"])
def test_service_loopback_rejects_changed_tool_identity(tmp_path, mismatch):
    async def run():
        host, session = make_session(tmp_path)
        with pytest.raises(Exception, match="unsupported"):
            await session.attach_voice(
                Audio(), VoiceProfile(client_factory=factory(Connection(mismatch=mismatch)))
            )
        assert session._voice is None and not host.made

    asyncio.run(run())


def test_realtime_extra_uses_service_315_and_allows_current_harness_candidate():
    project = tomllib.loads((Path(__file__).resolve().parents[3] / "pyproject.toml").read_text())
    assert project["project"]["optional-dependencies"]["realtime"] == [
        "simple-harness-service-sdk[realtime]==0.3.15"
    ]
    service_requirements = [Requirement(value) for value in requires("simple-harness-service-sdk")]
    harness = next(req for req in service_requirements if req.name == "simple-harness-sdk")
    assert harness.specifier.contains("0.13.0.dev20260925+opt.218", prereleases=True)


def committed_history(session):
    row = session._commit_input("An earlier user request", "earlier")
    row["receipt"] = {"state": "committed", "text": "An earlier answer"}
    with session._store.transaction():
        session._store.put("turns", "earlier", row)


def test_service_loopback_restores_history_with_provider_message_id(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        committed_history(session)
        connection = Connection(provider_message_ids=True)
        binding = await session.attach_voice(
            Audio(), VoiceProfile(client_factory=factory(connection), max_reconnects=0)
        )
        try:
            assert binding.negotiation["status"] == "ready"
            messages = [e for e in connection.sent if e["type"] == "conversation.item.create"]
            assert len(messages) == 1 and messages[0]["item"]["type"] == "message"
            assert not any(e["type"] == "response.create" for e in connection.sent)
        finally:
            await binding.detach()

    asyncio.run(run())


def test_service_loopback_announces_after_provider_message_id_ack(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        connection = Connection(provider_message_ids=True)
        binding = await session.attach_voice(
            Audio(), VoiceProfile(client_factory=factory(connection), max_reconnects=0)
        )
        try:
            await session.announce("The report is ready")
            assert len([e for e in connection.sent if e["type"] == "response.create"]) == 1
            assert connection.sent[-1]["type"] == "response.create"
        finally:
            await binding.detach()

    asyncio.run(run())


def test_service_loopback_original_message_id_still_confirms_history_and_announcement(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        committed_history(session)
        connection = Connection()
        binding = await session.attach_voice(
            Audio(), VoiceProfile(client_factory=factory(connection), max_reconnects=0)
        )
        try:
            assert not any(e["type"] == "response.create" for e in connection.sent)
            await session.announce("The report is ready")
            assert len([e for e in connection.sent if e["type"] == "response.create"]) == 1
        finally:
            await binding.detach()

    asyncio.run(run())


@pytest.mark.parametrize("corrupt", ["text", "role", "status"])
def test_service_loopback_mismatched_message_cannot_trigger_response(tmp_path, corrupt):
    async def run():
        _, session = make_session(tmp_path)
        connection = Connection(provider_message_ids=True, corrupt=corrupt)
        binding = await session.attach_voice(
            Audio(), VoiceProfile(client_factory=factory(connection), max_reconnects=0)
        )
        try:
            with pytest.raises(RealtimeError):
                await session.announce("The report is ready")
            assert not any(e["type"] == "response.create" for e in connection.sent)
        finally:
            await binding.detach()

    asyncio.run(run())

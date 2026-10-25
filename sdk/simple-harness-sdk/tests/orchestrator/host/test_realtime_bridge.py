# SPDX-License-Identifier: Apache-2.0
"""Voice arbitration, durable history, and playback contracts."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_session import SPEC, make_session

from agent_orchestrator.host.realtime_bridge import VoiceBinding, VoiceProfile


@pytest.fixture(autouse=True)
def request_contract(monkeypatch):
    def request(binding):
        return SimpleNamespace(
            external_session_id=binding.session.session_id,
            tools=tuple({"name": name} for name in binding._catalogue),
        )

    monkeypatch.setattr(VoiceBinding, "_request", request, raising=False)


class Audio:
    def __init__(self):
        self.frames = asyncio.Queue()
        self.written = []
        self.position = 0

    async def read_pcm(self):
        return await self.frames.get()

    async def write_pcm(self, pcm):
        self.written.append(pcm)

    def played_audio_ms(self):
        return self.position


class Wire:
    def __init__(self, dispatcher, ack=True):
        self.dispatcher = dispatcher
        self.queue = asyncio.Queue()
        self.messages = []
        self.cancelled = 0
        self.closed = False
        self.ack = ack
        self.sent_audio = []

    async def events(self):
        yield SimpleNamespace(kind="SessionReady", tools_acknowledged=self.ack)
        while not self.closed:
            event = await self.queue.get()
            yield event

    async def append_message(self, text, *, role="user", trigger_response=False):
        self.messages.append((text, role, trigger_response))

    async def send_audio(self, pcm):
        self.sent_audio.append(pcm)

    async def cancel_response(self):
        self.cancelled += 1

    async def close(self):
        self.closed = True

    async def emit(self, kind, **fields):
        if kind == "OutputText":
            fields.update(item_id="out", output_index=0, content_index=0, is_delta=False)
        if kind == "ResponseFinished":
            fields["usage"] = None
        await self.queue.put(SimpleNamespace(kind=kind, **fields))
        await asyncio.sleep(0.02)


class Client:
    def __init__(self, ack=True):
        self.wires = []
        self.requests = []
        self.ack = ack

    def __call__(self, dispatcher):
        async def open(request):
            return await self.open(request, tool_dispatcher=dispatcher)

        return SimpleNamespace(open=open)

    async def open(self, request, *, tool_dispatcher):
        self.requests.append(request)
        wire = Wire(tool_dispatcher, self.ack)
        self.wires.append(wire)
        return wire


def test_voice_tool_ack_is_required_and_lock_released(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        client = Client(ack=False)
        with pytest.raises(Exception, match="unsupported"):
            await session.attach_voice(Audio(), VoiceProfile(client_factory=client))
        assert client.wires[0].closed
        assert session._voice is None and not session._send_lock.locked()

    asyncio.run(run())


def test_voice_transcripts_and_tools_are_committed_and_idempotent(tmp_path):
    async def run():
        host, session = make_session(tmp_path)
        client = Client()
        binding = await session.attach_voice(Audio(), VoiceProfile(client_factory=client))
        wire = client.wires[-1]
        assert {t["name"] for t in client.requests[0].tools} >= {
            "delegate_task",
            "task_status",
            "task_cancel",
        }
        await wire.emit("TranscriptCompleted", item_id="u1", text="Write a report")
        await wire.emit("ResponseStarted", response_id="r1")
        call = SimpleNamespace(
            call_id="c1",
            item_id="u1",
            name="delegate_task",
            arguments_json=json.dumps(SPEC),
            response_id="r1",
        )
        receipt = await wire.dispatcher.dispatch(call)
        assert await wire.dispatcher.dispatch(call) == receipt
        assert len(host.made) == 1
        await wire.emit("OutputText", response_id="r1", text="Accepted", is_delta=False)
        await wire.emit("ResponseFinished", response_id="r1", status="completed")
        assert session.history()[-1]["input"] == "Write a report"
        assert session.history()[-1]["text"] == "Accepted"
        assert session.history()[-1]["tool_calls"]
        events = session._store.events(0)
        assert all(e["schema_version"] == 1 for e in events)
        assert any(e.get("binding_id") == binding.binding_id for e in events)
        await binding.detach()

    asyncio.run(run())


def test_text_waits_until_detach_and_close_does_not_run_queued_text(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        called = []

        async def turn(body, **kwargs):
            called.append(body)
            return {"state": "committed", "turn_id": "t", "text": "Text reply"}

        session._chat = SimpleNamespace(turn=turn)
        binding = await session.attach_voice(Audio(), VoiceProfile(client_factory=Client()))
        pending = asyncio.create_task(session.send("Text", key="text"))
        await asyncio.sleep(0.02)
        assert not pending.done() and not called
        await binding.detach()
        assert (await pending)["text"] == "Text reply"

    asyncio.run(run())


def test_announcement_waits_for_user_model_and_actual_playback(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        audio, client = Audio(), Client()
        binding = await session.attach_voice(audio, VoiceProfile(client_factory=client))
        wire = client.wires[-1]
        await wire.emit("SpeechStarted", item_id="u1")
        handle = await session.announce("The report is ready")
        assert not any(m[2] for m in wire.messages)
        await wire.emit("SpeechStopped", item_id="u1")
        await wire.emit("ResponseStarted", response_id="user-reply")
        await wire.emit("ResponseFinished", response_id="user-reply", status="completed")
        await asyncio.sleep(0.04)
        assert any(m[2] for m in wire.messages)
        await wire.emit("ResponseStarted", response_id="r1")
        await wire.emit("OutputAudio", response_id="r1", data=b"\0" * 4800)
        await wire.emit("OutputAudioCompleted", response_id="r1")
        assert handle.transmission_finished and not handle.generated and not handle.done
        await wire.emit("ResponseFinished", response_id="r1", status="completed")
        assert not handle.done
        audio.position = 100
        assert (await asyncio.wait_for(handle.wait(), 1))["state"] == "played"
        assert handle.generated and handle.received_audio_ms == 100
        await binding.detach()

    asyncio.run(run())


def test_barge_in_preserves_unheard_content_and_never_cancels_task(tmp_path):
    async def run():
        host, session = make_session(tmp_path)
        audio, client = Audio(), Client()
        binding = await session.attach_voice(audio, VoiceProfile(client_factory=client))
        wire = client.wires[-1]
        handle = await session.announce("A long result")
        await wire.emit("ResponseStarted", response_id="r1")
        await wire.emit("OutputAudio", response_id="r1", data=b"\0" * 48000)
        audio.position = 120
        await binding.barge_in()
        assert wire.cancelled == 1 and not host.cancelled
        assert handle.interrupted and (await handle.wait())["played_audio_ms"] == 120
        await binding.barge_in()
        assert wire.cancelled == 1
        assert session._store.rows("voice_unheard")
        await wire.emit("TranscriptCompleted", item_id="u2", text="What happened?")
        assert any("120" in m[0] and not m[2] for m in wire.messages)
        await binding.detach()

    asyncio.run(run())


def test_reconnect_keeps_session_increments_generation_and_replays_no_audio(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        client = Client()
        binding = await session.attach_voice(
            Audio(), VoiceProfile(client_factory=client, reconnect_backoff=0, max_reconnects=1)
        )
        await client.wires[0].emit("TranscriptCompleted", item_id="u1", text="Hello")
        await client.wires[0].emit("ResponseStarted", response_id="r1")
        await client.wires[0].emit("OutputText", response_id="r1", text="Hi", is_delta=False)
        await client.wires[0].emit("ResponseFinished", response_id="r1", status="completed")
        await client.wires[0].emit("SessionFailed", code="disconnected")
        await asyncio.sleep(0.05)
        assert binding.generation == 2 and session.session_id == "s"
        assert client.wires[0].closed and len(client.wires) == 2
        assert any("Hello" in m[0] for m in client.wires[1].messages)
        await binding.detach()
        assert not session._closed
        again = await session.attach_voice(Audio(), VoiceProfile(client_factory=client))
        assert again.generation == 3
        await again.detach()

    asyncio.run(run())


def test_stale_dispatcher_cannot_execute_after_reconnect(tmp_path):
    async def run():
        host, session = make_session(tmp_path)
        client = Client()
        binding = await session.attach_voice(
            Audio(), VoiceProfile(client_factory=client, reconnect_backoff=0)
        )
        old = client.wires[0]
        await old.emit("TranscriptCompleted", item_id="old-item", text="Old request")
        await old.emit("SessionFailed", code="disconnected")
        await asyncio.sleep(0.04)
        result = await old.dispatcher.dispatch(
            SimpleNamespace(
                response_id="old-r",
                call_id="old-c",
                name="delegate_task",
                arguments_json=json.dumps(SPEC),
            )
        )
        assert json.loads(result)["error"]["code"] == "stale_generation"
        assert not host.made
        await binding.detach()

    asyncio.run(run())


def test_barge_in_after_generation_finished_tracks_ordinary_reply(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        client, audio = Client(), Audio()
        binding = await session.attach_voice(audio, VoiceProfile(client_factory=client))
        wire = client.wires[0]
        await wire.emit("TranscriptCompleted", item_id="u", text="Tell me a story")
        await wire.emit("ResponseStarted", response_id="r")
        await wire.emit("OutputText", response_id="r", text="A long story")
        await wire.emit("OutputAudio", response_id="r", data=b"x" * 48000)
        await wire.emit("ResponseFinished", response_id="r", status="completed")
        audio.position = 150
        await binding.barge_in()
        assert wire.cancelled == 0
        unheard = session._store.rows("voice_unheard")
        assert unheard and unheard[-1]["played_audio_ms"] == 150
        assert unheard[-1]["text"] == "A long story"
        assert session.history()[-1]["playback"]["played_audio_ms"] == 150
        await binding.detach()

    asyncio.run(run())


def test_readonly_tool_schema_context_limit_and_idempotency_conflict(tmp_path):
    from agent_orchestrator.host import DomainTool

    async def run():
        host, session = make_session(tmp_path)
        contexts = []
        host.tools["device_status"] = DomainTool(
            name="device_status",
            schema={
                "type": "object",
                "description": "Read device",
                "properties": {},
                "additionalProperties": False,
            },
            invoke=lambda arguments, context: contexts.append(context) or {"battery": 77},
        )
        client = Client()
        binding = await session.attach_voice(Audio(), VoiceProfile(client_factory=client))
        wire = client.wires[0]
        await wire.emit("TranscriptCompleted", item_id="u", text="Battery?")
        await wire.emit("ResponseStarted", response_id="r")
        call = SimpleNamespace(
            response_id="r", call_id="c", name="device_status", arguments_json="{}"
        )
        result = await wire.dispatcher.dispatch(call)
        assert json.loads(result) == {"battery": 77}
        assert await wire.dispatcher.dispatch(call) == result and len(contexts) == 1
        assert contexts[0].principal_id == "u" and contexts[0].tenant_id == "local"
        call.arguments_json = '{"principal_id":"other"}'
        assert "invalid_arguments" in await wire.dispatcher.dispatch(call)
        call.name, call.arguments_json = "task_status", "{}"
        assert "idempotency_conflict" in await wire.dispatcher.dispatch(call)
        await binding.detach()

    asyncio.run(run())


def test_voice_budget_reserved_before_network_and_recovered_at_upper_bound(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        actions = []

        class BudgetChat:
            def _reserve_voice(self, subject, tokens):
                assert session._store.get("voice_budget", subject)["state"] == "reserving"
                actions.append(("reserve", subject, tokens))

            def _settle(self, subject, facts, unknown, tool_calls):
                actions.append(("settle", subject, unknown))

        session._chat = BudgetChat()
        client = Client()
        original = client.open

        async def open_wire(*args, **kwargs):
            assert actions and actions[0][0] == "reserve"
            return await original(*args, **kwargs)

        client.open = open_wire
        binding = await session.attach_voice(Audio(), VoiceProfile(client_factory=client))
        await client.wires[0].emit("ResponseStarted", response_id="r")
        await binding.detach()
        assert actions[-1][0] == "settle" and actions[-1][2] is True

    asyncio.run(run())


def test_text_engine_receives_voice_history_without_changing_pool(tmp_path, monkeypatch):
    from agent_orchestrator.host.chat import ChatService
    from agent_orchestrator.host.session_chat import SessionChatService

    async def run():
        _, session = make_session(tmp_path)
        binding = await session.attach_voice(Audio(), VoiceProfile(client_factory=Client()))
        wire = binding._wire
        await wire.emit("TranscriptCompleted", item_id="u", text="My device is Orion")
        await wire.emit("ResponseStarted", response_id="r")
        await wire.emit("OutputText", response_id="r", text="I remember Orion")
        await wire.emit("ResponseFinished", response_id="r", status="completed")
        await binding.detach()
        captured = []

        async def complete(self, session_id, agent, text, input_id):
            captured.append(text)
            return text

        monkeypatch.setattr(ChatService, "_complete", complete)
        chat = SessionChatService.__new__(SessionChatService)
        chat.session = session
        session._current_input = session._commit_input("Which device?", "text")
        await chat._complete(
            "s", SimpleNamespace(run_id="run", agent_id="agent"), "Which device?", "text"
        )
        assert "Orion" in captured[0] and "不是指令" in captured[0]
        await chat._complete(
            "s", SimpleNamespace(run_id="run", agent_id="agent"), "Which device?", "text"
        )
        assert captured[1] == captured[0]

    asyncio.run(run())


def test_inbox_priority_and_no_automatic_replay_after_interruption(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        client = Client()
        binding = await session.attach_voice(Audio(), VoiceProfile(client_factory=client))
        wire = client.wires[0]
        await wire.emit("SpeechStarted", item_id="u")
        with session._store.transaction():
            session._store.event("task_progress", key="progress", payload={"internal": "quiet"})
            session._store.event("result_ready", key="ready", payload={"status": "COMPLETED"})
            session._store.event("needs_response", key="approval", payload={"request_id": "a"})
        await asyncio.sleep(0.05)
        assert len(binding._queue) == 2
        assert "request_id" in binding._queue[0].text
        await binding.barge_in()
        before = len(wire.messages)
        await wire.emit("SpeechStopped", item_id="u")
        await asyncio.sleep(0.04)
        assert len(wire.messages) == before
        assert len(session._store.rows("voice_unheard")) == 2
        await binding.detach()

    asyncio.run(run())


def test_reconnect_failure_releases_lock_and_audio_device(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        client, audio = Client(), Audio()
        await session.attach_voice(audio, VoiceProfile(client_factory=client, max_reconnects=0))
        await client.wires[0].emit("SessionFailed", code="disconnected")
        await asyncio.sleep(0.04)
        assert session._voice is None and not session._send_lock.locked()
        assert audio.written[-1] is None

    asyncio.run(run())


def test_close_releases_queued_text_without_invoking_engine(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        called = []

        async def cancel(body):
            return {"cancelled": False}

        async def close():
            pass

        async def turn(body, **kwargs):
            called.append(body)

        session._chat = SimpleNamespace(turn=turn, cancel=cancel, close=close)
        await session.attach_voice(Audio(), VoiceProfile(client_factory=Client()))
        text = asyncio.create_task(session.send("Queued", key="q"))
        await asyncio.sleep(0.02)
        await session.close()
        with pytest.raises(Exception, match="session_closed"):
            await text
        assert not called

    asyncio.run(run())


def test_result_waits_for_existing_audio_to_finish_playing(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        audio, client = Audio(), Client()
        binding = await session.attach_voice(audio, VoiceProfile(client_factory=client))
        wire = client.wires[0]
        await wire.emit("ResponseStarted", response_id="chat")
        await wire.emit("OutputAudio", response_id="chat", data=b"x" * 48000)
        await wire.emit("ResponseFinished", response_id="chat", status="completed")
        handle = await session.announce("Result ready")
        assert not any(m[2] for m in wire.messages)
        audio.position = 1000
        await asyncio.sleep(0.05)
        assert any(m[2] for m in wire.messages) and not handle.done
        await binding.detach()

    asyncio.run(run())


def test_new_connection_requires_new_input_before_tool_dispatch(tmp_path):
    async def run():
        host, session = make_session(tmp_path)
        client = Client()
        binding = await session.attach_voice(
            Audio(), VoiceProfile(client_factory=client, reconnect_backoff=0)
        )
        await client.wires[0].emit("TranscriptCompleted", item_id="u", text="Old request")
        await client.wires[0].emit("SessionFailed", code="disconnected")
        await asyncio.sleep(0.04)
        result = await client.wires[-1].dispatcher.dispatch(
            SimpleNamespace(
                response_id="r", call_id="c", name="delegate_task", arguments_json=json.dumps(SPEC)
            )
        )
        assert "permission_denied" in result and not host.made
        await binding.detach()

    asyncio.run(run())


def test_detach_releases_arbitration_even_when_audio_or_network_close_fails(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        audio, client = Audio(), Client()
        binding = await session.attach_voice(audio, VoiceProfile(client_factory=client))

        async def fail(value=None):
            raise OSError("device failed")

        audio.write_pcm = fail
        client.wires[0].close = fail
        await binding.detach()
        assert session._voice is None and not session._send_lock.locked()
        assert all(t.done() for t in binding._workers)

    asyncio.run(run())


def test_missing_extra_reports_unsupported_without_holding_session(tmp_path, monkeypatch):
    import builtins

    async def run():
        _, session = make_session(tmp_path)
        original = builtins.__import__

        def no_service(name, *args, **kwargs):
            if name.startswith("simple_harness_service"):
                raise ModuleNotFoundError(name)
            return original(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_service)
        monkeypatch.undo()
        # Use the real request constructor rather than the offline request port.
        monkeypatch.setattr(builtins, "__import__", no_service)
        binding = VoiceBinding(session, Audio(), VoiceProfile(client_factory=Client()))
        with pytest.raises(Exception, match="unsupported"):
            binding._request()

    asyncio.run(run())


def test_real_session_budget_is_reserved_settled_and_voice_visible_to_text(tmp_path):
    from agent_orchestrator.host import Principal, Session
    from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider

    async def run():
        requests = []

        def reply(request):
            requests.append(request)
            return "Text remembers Orion"

        session = await Session.open(
            tmp_path,
            principal=Principal("u", "User"),
            provider=ScriptedProvider(chat=reply),
            counter=FixtureWordCounter(),
            require_acceptance=False,
        )
        before = session._host.global_budget_status()
        client = Client()
        binding = await session.attach_voice(
            Audio(), VoiceProfile(client_factory=client, max_total_tokens=123456)
        )
        assert session._host.global_budget_status()["reserved_tokens"] == (
            before["reserved_tokens"] + 123456
        )
        wire = client.wires[0]
        await wire.emit("TranscriptCompleted", item_id="u", text="My device is Orion")
        await wire.emit("ResponseStarted", response_id="r")
        await wire.emit("OutputText", response_id="r", text="Remembered Orion")
        await wire.emit("ResponseFinished", response_id="r", status="completed")
        await binding.detach()
        after = session._host.global_budget_status()
        assert after["reserved_tokens"] == before["reserved_tokens"]
        assert after["used_tokens"] == before["used_tokens"] + 123456
        assert (await session.send("Which device?", key="q"))["state"] == "committed"
        assert "Orion" in str(requests[-1].messages)
        await session.close()

    asyncio.run(run())


def test_local_playback_positions_do_not_overlap_followup_responses(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        audio, client = Audio(), Client()
        binding = await session.attach_voice(audio, VoiceProfile(client_factory=client))
        wire = client.wires[0]
        await wire.emit("ResponseStarted", response_id="r1")
        await wire.emit("OutputAudio", response_id="r1", data=b"x" * 48000)
        await wire.emit("ResponseFinished", response_id="r1", status="completed")
        await wire.emit("ResponseStarted", response_id="r2")
        await wire.emit("OutputAudio", response_id="r2", data=b"x" * 48000)
        await wire.emit("ResponseFinished", response_id="r2", status="completed")
        audio.position = 1100
        await asyncio.sleep(0.04)
        assert binding._playback_handles["r1"].done
        assert not binding._playback_handles["r2"].done
        assert binding._playback_handles["r2"].played_audio_ms == 100
        await binding.detach()

    asyncio.run(run())


def test_detach_without_response_keeps_committed_user_input_and_text_can_continue(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        client = Client()
        binding = await session.attach_voice(Audio(), VoiceProfile(client_factory=client))
        await client.wires[0].emit("TranscriptCompleted", item_id="u", text="Remember Orion")
        await binding.detach()
        assert session.history()[-1]["input"] == "Remember Orion"

        async def turn(body, **kwargs):
            return {"state": "committed", "turn_id": "text-r", "text": "Orion"}

        session._chat = SimpleNamespace(turn=turn)
        assert (await session.send("Which device?", key="q"))["state"] == "committed"

    asyncio.run(run())


def test_result_announcement_is_not_a_new_task_authorization(tmp_path):
    async def run():
        host, session = make_session(tmp_path)
        client = Client()
        binding = await session.attach_voice(Audio(), VoiceProfile(client_factory=client))
        wire = client.wires[0]
        await wire.emit("TranscriptCompleted", item_id="u", text="Hello")
        await wire.emit("ResponseStarted", response_id="chat")
        await wire.emit("ResponseFinished", response_id="chat", status="completed")
        await asyncio.sleep(0.04)
        await session.announce("A result is ready")
        await wire.emit("ResponseStarted", response_id="announcement")
        result = await wire.dispatcher.dispatch(
            SimpleNamespace(
                response_id="announcement",
                call_id="c",
                name="delegate_task",
                arguments_json=json.dumps(SPEC),
            )
        )
        assert "permission_denied" in result and not host.made
        await binding.detach()

    asyncio.run(run())


def test_pcm_input_uses_service_sdk_send_audio(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        audio, client = Audio(), Client()
        binding = await session.attach_voice(audio, VoiceProfile(client_factory=client))
        await audio.frames.put(b"x" * 3200)
        await asyncio.sleep(0.04)
        assert client.wires[0].sent_audio == [b"x" * 3200]
        assert session._voice is binding
        await binding.detach()

    asyncio.run(run())


def test_detach_during_announcement_does_not_block_next_text(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        client = Client()
        binding = await session.attach_voice(Audio(), VoiceProfile(client_factory=client))
        await session.announce("Result ready")
        await client.wires[0].emit("ResponseStarted", response_id="announce")
        await binding.detach()

        async def turn(body, **kwargs):
            return {"state": "committed", "turn_id": "t", "text": "Hello"}

        session._chat = SimpleNamespace(turn=turn)
        assert (await session.send("Hello", key="text"))["state"] == "committed"

    asyncio.run(run())


def test_reconnect_context_has_bounded_summary_and_recent_turns(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        for index in range(10):
            key = f"text-{index}"
            row = session._commit_input(f"Earlier fact {index}", key)
            with session._store.transaction():
                row["receipt"] = {"state": "committed", "text": f"Answer {index}"}
                session._store.put("turns", key, row)
        client = Client()
        binding = await session.attach_voice(
            Audio(), VoiceProfile(client_factory=client, history_turns=2)
        )
        context = client.wires[0].messages[0][0]
        assert "Earlier fact 0" in context and "Earlier fact 9" in context
        assert '"recent_turns"' in context and '"summary"' in context
        assert not client.wires[0].messages[0][2]
        await binding.detach()

    asyncio.run(run())


def test_review_announcement_tool_followup_retains_non_authorizing_origin(tmp_path):
    async def run():
        host, session = make_session(tmp_path)
        audio, client = Audio(), Client()
        binding = await session.attach_voice(audio, VoiceProfile(client_factory=client))
        wire = client.wires[0]
        try:
            await wire.emit("TranscriptCompleted", item_id="u", text="Hello")
            await wire.emit("ResponseStarted", response_id="chat")
            await wire.emit("ResponseFinished", response_id="chat", status="completed")
            await asyncio.sleep(0.04)
            await session.announce("The task result is ready")
            await wire.emit("ResponseStarted", response_id="notice-1")
            first = await wire.dispatcher.dispatch(
                SimpleNamespace(
                    response_id="notice-1",
                    call_id="status",
                    name="task_status",
                    arguments_json="{}",
                )
            )
            assert "error" not in json.loads(first)
            await wire.emit("OutputAudio", response_id="notice-1", data=b"x" * 48000)
            await wire.emit("ResponseFinished", response_id="notice-1", status="completed")
            await wire.emit("ResponseStarted", response_id="notice-2")
            result = await wire.dispatcher.dispatch(
                SimpleNamespace(
                    response_id="notice-2",
                    call_id="create",
                    name="delegate_task",
                    arguments_json=json.dumps(SPEC),
                )
            )
            observed = {"result": json.loads(result), "missions_created": len(host.made)}
        finally:
            await binding.detach()
        assert "permission_denied" in result and not host.made, observed

    asyncio.run(run())


def test_review_late_completed_response_preserves_interrupted_playback(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        audio, client = Audio(), Client()
        binding = await session.attach_voice(audio, VoiceProfile(client_factory=client))
        wire = client.wires[0]
        try:
            await wire.emit("TranscriptCompleted", item_id="u", text="Tell me the result")
            await wire.emit("ResponseStarted", response_id="r")
            await wire.emit("OutputText", response_id="r", text="Partial")
            await wire.emit("OutputAudio", response_id="r", data=b"x" * 48000)
            audio.position = 120
            await binding.barge_in()
            await wire.emit("OutputText", response_id="r", text="Partial plus unheard conclusion")
            await wire.emit("ResponseFinished", response_id="r", status="completed")
            history = session.history()[-1]
            unheard = session._store.rows("voice_unheard")[-1]
            observed = {"history": history, "unheard": unheard}
        finally:
            await binding.detach()
        assert history.get("playback", {}).get("state") == "interrupted" and (
            unheard["text"] == "Partial plus unheard conclusion"
        ), observed

    asyncio.run(run())


def test_review_pending_announcement_cannot_play_over_user_speech(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        audio, client = Audio(), Client()
        binding = await session.attach_voice(audio, VoiceProfile(client_factory=client))
        wire = client.wires[0]
        try:
            await session.announce("A task result")
            assert any(message[2] for message in wire.messages)
            await wire.emit("SpeechStarted", item_id="new-input")
            assert binding._user_speaking
            await wire.emit("ResponseStarted", response_id="late-notice")
            await wire.emit("OutputAudio", response_id="late-notice", data=b"x" * 4800)
            observed = {
                "user_speaking": binding._user_speaking,
                "played_frame_sizes": [len(frame) for frame in audio.written if frame],
                "cancelled": wire.cancelled,
            }
        finally:
            await binding.detach()
        assert not observed["played_frame_sizes"], observed

    asyncio.run(run())


@pytest.mark.parametrize("command", ["delegate_task", "task_cancel"])
def test_review_announcement_origin_survives_multiple_followups_and_playback(tmp_path, command):
    async def run():
        host, session = make_session(tmp_path)
        text_turn = session._commit_input("Create a report", "task-input")
        task = session._delegations.delegate(text_turn["turn_id"], "task-create", SPEC)
        client = Client()
        binding = await session.attach_voice(Audio(), VoiceProfile(client_factory=client))
        wire = client.wires[0]
        try:
            await wire.emit("TranscriptCompleted", item_id="hello", text="Hello")
            await wire.emit("ResponseStarted", response_id="chat")
            await wire.emit("ResponseFinished", response_id="chat", status="completed")
            await asyncio.sleep(0.04)
            handle = await session.announce("The report has an update")
            for index in range(2):
                response_id = f"notice-{index}"
                await wire.emit("ResponseStarted", response_id=response_id)
                result = await wire.dispatcher.dispatch(
                    SimpleNamespace(
                        response_id=response_id,
                        call_id=f"status-{index}",
                        name="task_status",
                        arguments_json="{}",
                    )
                )
                assert "error" not in json.loads(result)
                await wire.emit("ResponseFinished", response_id=response_id, status="completed")
                await asyncio.sleep(0.04)
            assert handle.done
            await wire.emit("ResponseStarted", response_id="notice-final")
            result = await wire.dispatcher.dispatch(
                SimpleNamespace(
                    response_id="notice-final",
                    call_id="forbidden",
                    name=command,
                    arguments_json=json.dumps(
                        SPEC if command == "delegate_task" else {"task_id": task["mission_id"]}
                    ),
                )
            )
            assert json.loads(result)["error"]["code"] == "permission_denied"
            assert len(host.made) == 1 and not host.cancelled
            keys = {binding._response_turns[f"notice-{i}"] for i in range(2)}
            keys.add(binding._response_turns["notice-final"])
            assert len(keys) == 1
            assert session._store.get("turns", keys.pop())["source"] == "announcement"
        finally:
            await binding.detach()

    asyncio.run(run())


def test_review_unknown_response_cannot_borrow_last_user_authorization(tmp_path):
    async def run():
        host, session = make_session(tmp_path)
        client = Client()
        binding = await session.attach_voice(Audio(), VoiceProfile(client_factory=client))
        wire = client.wires[0]
        try:
            await wire.emit("TranscriptCompleted", item_id="u", text="Hello")
            await wire.emit("ResponseStarted", response_id="chat")
            await wire.emit("ResponseFinished", response_id="chat", status="completed")
            await wire.emit("ResponseStarted", response_id="unsolicited")
            for response_id in ("unsolicited", "never-started"):
                result = await wire.dispatcher.dispatch(
                    SimpleNamespace(
                        response_id=response_id,
                        call_id=response_id,
                        name="delegate_task",
                        arguments_json=json.dumps(SPEC),
                    )
                )
                assert json.loads(result)["error"]["code"] == "permission_denied"
            assert not host.made
        finally:
            await binding.detach()

    asyncio.run(run())


def test_review_user_tool_followup_keeps_its_original_input(tmp_path):
    async def run():
        host, session = make_session(tmp_path)
        client = Client()
        binding = await session.attach_voice(Audio(), VoiceProfile(client_factory=client))
        wire = client.wires[0]
        try:
            await wire.emit("TranscriptCompleted", item_id="work", text="Write a report")
            await wire.emit("ResponseStarted", response_id="user-1")
            await wire.dispatcher.dispatch(
                SimpleNamespace(
                    response_id="user-1",
                    call_id="status",
                    name="task_status",
                    arguments_json="{}",
                )
            )
            await wire.emit("ResponseFinished", response_id="user-1", status="completed")
            await wire.emit("ResponseStarted", response_id="user-2")
            result = await wire.dispatcher.dispatch(
                SimpleNamespace(
                    response_id="user-2",
                    call_id="delegate",
                    name="delegate_task",
                    arguments_json=json.dumps(SPEC),
                )
            )
            assert json.loads(result)["status"] == "accepted" and len(host.made) == 1
            assert binding._response_turns["user-1"] == binding._response_turns["user-2"]
        finally:
            await binding.detach()

    asyncio.run(run())


def test_review_late_text_refreshes_durable_playback_and_next_turn_context(tmp_path):
    from agent_orchestrator.host import Session

    async def run():
        host, session = make_session(tmp_path)
        audio, client = Audio(), Client()
        binding = await session.attach_voice(audio, VoiceProfile(client_factory=client))
        wire = client.wires[0]
        try:
            await wire.emit("TranscriptCompleted", item_id="u1", text="Give the result")
            await wire.emit("ResponseStarted", response_id="r")
            await wire.emit("OutputText", response_id="r", text="Partial")
            await wire.emit("OutputAudio", response_id="r", data=b"x" * 48000)
            audio.position = 120
            await binding.barge_in()
            await wire.emit("TranscriptCompleted", item_id="u2", text="Wait")
            await wire.emit("OutputText", response_id="r", text="Full unheard conclusion")
            await wire.emit("ResponseFinished", response_id="r", status="completed")
            rows = session._store.rows("voice_playback")
            assert len(rows) == 1 and rows[0]["response_id"] == "r"
            assert rows[0]["played_audio_ms"] == 120
            restored = Session(host, session_id="s")
            history = next(row for row in restored.history() if row["input"] == "Give the result")
            assert history["playback"]["state"] == "interrupted"
            assert history["text"] == "Full unheard conclusion"
            assert next(row for row in restored.history() if row["input"] == "Wait")["text"] == ""
            await wire.emit("TranscriptCompleted", item_id="u3", text="What did I miss?")
            assert any(
                "Full unheard conclusion" in text and not trigger
                for text, _, trigger in wire.messages
            )
            assert wire.cancelled == 1
        finally:
            await binding.detach()

    asyncio.run(run())


@pytest.mark.parametrize("stopped", [False, True])
def test_review_invalidated_pending_response_stays_muted_after_speech_stops(tmp_path, stopped):
    async def run():
        _, session = make_session(tmp_path)
        audio, client = Audio(), Client()
        binding = await session.attach_voice(audio, VoiceProfile(client_factory=client))
        wire = client.wires[0]
        try:
            handle = await session.announce("An old result")
            await wire.emit("SpeechStarted", item_id="u")
            if stopped:
                await wire.emit("SpeechStopped", item_id="u")
            await wire.emit("ResponseStarted", response_id="late")
            await wire.emit("OutputAudio", response_id="late", data=b"x" * 4800)
            await binding.barge_in()
            await wire.emit("ResponseFinished", response_id="late", status="completed")
            assert not any(audio.written) and wire.cancelled == 1
            assert (await handle.wait())["state"] == "interrupted"
            assert handle.response_id == "late"
            assert session._store.rows("voice_unheard")
        finally:
            await binding.detach()

    asyncio.run(run())


def test_review_media_exit_rejects_unbound_audio_while_user_is_speaking(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        audio, client = Audio(), Client()
        binding = await session.attach_voice(audio, VoiceProfile(client_factory=client))
        wire = client.wires[0]
        try:
            await wire.emit("SpeechStarted", item_id="u")
            await wire.emit("OutputAudio", response_id="unbound", data=b"x" * 4800)
            assert not any(audio.written)
        finally:
            await binding.detach()

    asyncio.run(run())


def test_review_media_exit_rechecks_invalidated_origin_after_playback_finished(tmp_path):
    async def run():
        _, session = make_session(tmp_path)
        audio, client = Audio(), Client()
        binding = await session.attach_voice(audio, VoiceProfile(client_factory=client))
        wire = client.wires[0]
        try:
            await wire.emit("TranscriptCompleted", item_id="old", text="Hello")
            await wire.emit("ResponseStarted", response_id="old-response")
            await wire.emit("OutputAudio", response_id="old-response", data=b"x" * 4800)
            await wire.emit("ResponseFinished", response_id="old-response", status="completed")
            audio.position = 100
            await asyncio.sleep(0.04)
            assert binding._playback_handles["old-response"].done
            await wire.emit("SpeechStarted", item_id="new")
            await wire.emit("SpeechStopped", item_id="new")
            count = len([frame for frame in audio.written if frame])
            await wire.emit("OutputAudio", response_id="old-response", data=b"x" * 4800)
            assert len([frame for frame in audio.written if frame]) == count
            assert wire.cancelled == 0
        finally:
            await binding.detach()

    asyncio.run(run())


def test_review_fresh_user_input_after_late_notice_can_delegate(tmp_path):
    async def run():
        host, session = make_session(tmp_path)
        audio, client = Audio(), Client()
        binding = await session.attach_voice(audio, VoiceProfile(client_factory=client))
        wire = client.wires[0]
        try:
            handle = await session.announce("An old result")
            await wire.emit("SpeechStarted", item_id="new")
            await wire.emit("ResponseStarted", response_id="late")
            await wire.emit("OutputAudio", response_id="late", data=b"x" * 4800)
            await wire.emit("ResponseFinished", response_id="late", status="completed")
            await wire.emit("TranscriptCompleted", item_id="new", text="Write a report")
            await wire.emit("SpeechStopped", item_id="new")
            await wire.emit("ResponseStarted", response_id="new-response")
            await wire.emit("OutputAudio", response_id="new-response", data=b"x" * 4800)
            result = await wire.dispatcher.dispatch(
                SimpleNamespace(
                    response_id="new-response",
                    call_id="delegate",
                    name="delegate_task",
                    arguments_json=json.dumps(SPEC),
                )
            )
            assert json.loads(result)["status"] == "accepted" and len(host.made) == 1
            assert handle.interrupted and wire.cancelled == 1
            assert len([frame for frame in audio.written if frame]) == 1
            assert binding._response_turns["new-response"] == "voice:new"
        finally:
            await binding.detach()

    asyncio.run(run())

# SPDX-License-Identifier: Apache-2.0
"""持久受理、会话串行与子会话追加输入的消费者契约。"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from test_session import SPEC, FakeHost
from test_session_replay import ReplayChat

from agent_orchestrator.host import Session


async def eventually(predicate, seconds=5):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.005)


class FakeChat(ReplayChat):
    available = True

    async def cancel(self, body):
        pass

    async def close(self):
        pass


class GatedChat(FakeChat):
    def __init__(self, session, gate=None, **kwargs):
        super().__init__(session, **kwargs)
        self.release = gate or asyncio.Event()
        self.entered = asyncio.Event()
        self.inputs = []
        self.running = self.peak = 0

    async def turn(self, body, **kwargs):
        self.inputs.append(body["text"])
        self.running += 1
        self.peak = max(self.peak, self.running)
        self.entered.set()
        try:
            await self.release.wait()
            return await super().turn(body, **kwargs)
        finally:
            self.running -= 1


@pytest.mark.parametrize("wait", [False, True])
def test_async_accept_is_durable_and_serial_with_sync_send(tmp_path, wait):
    async def case():
        session = Session(FakeHost(tmp_path), session_id="s")
        chat = session._chat = GatedChat(session)
        first = await asyncio.wait_for(session.send("one", key="a", wait=False), 1)
        assert first["status"] == "accepted" and first["turn_id"]
        with sqlite3.connect(session._store.path) as db:
            assert db.execute(
                "SELECT 1 FROM records WHERE namespace='queued_inputs' AND key=?",
                (first["input_id"],),
            ).fetchone()
            assert db.execute("SELECT 1 FROM events WHERE body LIKE '%input_accepted%'").fetchone()
        await chat.entered.wait()
        second = asyncio.create_task(session.send("two", key="b", wait=wait))
        await asyncio.sleep(0.02)
        assert chat.inputs == ["one"]
        if not wait:
            assert (await second)["status"] == "accepted"
        assert await session.send("one", key="a", wait=False) == first
        with pytest.raises(Exception, match="idempotency_conflict"):
            await session.send("changed", key="a", wait=False)
        chat.release.set()
        if wait:
            assert (await second)["state"] == "committed"
        await eventually(lambda: len(session.history()) == 2)
        assert chat.inputs == ["one", "two"] and chat.peak == 1
        assert [r["state"] for r in session.queued_inputs()] == ["done", "done"]
        assert session.queued_inputs()[0]["turn_id"] == first["turn_id"]
        assert any(e["kind"] == "input_queued" for e in session._store.events(0))
        await session.close()

    asyncio.run(case())


def test_parent_and_two_children_execute_without_waiting_for_each_other(tmp_path):
    async def case():
        parent = Session(FakeHost(tmp_path), session_id="main")
        await parent.create_child("a", command_id="a")
        await parent.create_child("b", command_id="b")
        sessions = [parent, parent.child("a"), parent.child("b")]
        for session in sessions:
            session._chat = GatedChat(session)
            await session.send("work", key="one", wait=False)
        await asyncio.wait_for(asyncio.gather(*(s._chat.entered.wait() for s in sessions)), 1)
        assert all(s._chat.running == 1 for s in sessions)
        for session in sessions:
            session._chat.release.set()
        await eventually(lambda: all(s.history() for s in sessions))
        await parent.close()

    asyncio.run(case())


@pytest.mark.parametrize("state", ["queued", "running"])
def test_reopen_executes_accepted_inputs_in_order(tmp_path, state):
    async def case():
        host = FakeHost(tmp_path)
        session = Session(host, session_id="s")
        session._chat = GatedChat(session)
        await session.send("one", key="a", wait=False)
        await session.send("two", key="b", wait=False)
        if state == "running":
            await session._chat.entered.wait()
        await session.close()
        reopened = Session(host, session_id="s")
        reopened._chat = FakeChat(reopened)
        await reopened.recover()
        await eventually(lambda: len(reopened.history()) == 2)
        assert [r["input"] for r in reopened.history()] == ["one", "two"]
        assert all(r["state"] == "done" for r in reopened.queued_inputs())
        await reopened.close()

    asyncio.run(case())


def test_directive_waits_for_mission_then_delegates_once_from_input_id(tmp_path):
    async def case():
        host = FakeHost(tmp_path)
        parent = Session(host, session_id="main")
        await parent.create_child("a", command_id="create")
        child = parent.child("a")
        child._chat = FakeChat(child, goal="Next report")
        original = await child._delegations.delegate("first-turn", "call", SPEC)
        receipt = await parent.child_send("a", "next work", command_id="next")
        await child.recover()
        await asyncio.sleep(0.03)
        assert child.queued_inputs()[0]["state"] == "queued" and child._chat.calls == 0
        host.finish(original["mission_id"])
        await child.recover()
        await eventually(lambda: child.queued_inputs()[0]["state"] == "done")
        assert len(host.made) == 2
        [nxt] = [r for r in child._store.rows("delegations") if r.get("source_input_id")]
        assert nxt["source_input_id"] == receipt["input_id"]
        assert (await parent.child_send("a", "next work", command_id="next")) == receipt
        await child.recover()
        assert len(host.made) == 2
        await parent.close()

    asyncio.run(case())


def test_real_host_async_accept_returns_before_provider_and_recovers_without_resubmit(tmp_path):
    from agent_orchestrator.host import Principal
    from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider

    asked = []

    def reply(request):
        asked.append(request)
        return "第一条完成"

    async def case():
        provider = ScriptedProvider(chat=reply)
        provider.held.add("chat")
        options = dict(
            principal=Principal("u", "User"),
            provider=provider,
            counter=FixtureWordCounter(),
            require_acceptance=False,
        )
        session = await Session.open(tmp_path, **options)
        try:
            one = await asyncio.wait_for(session.send("先做第一条", key="a", wait=False), 1)
            await asyncio.wait_for(provider.entered.wait(), 10)
            two = await asyncio.wait_for(session.send("再做第二条", key="b", wait=False), 1)
            assert one["status"] == two["status"] == "accepted"
            assert len(provider.asked) == 1 and not asked
            provider.release.set()
            await eventually(lambda: len(session.history()) == 2, seconds=15)
            assert [r["input"] for r in session.history()] == ["先做第一条", "再做第二条"]
            calls = len(asked)
        finally:
            provider.release.set()
            await session.close()
        reopened = await Session.open(tmp_path, **options)
        try:
            assert len(reopened.history()) == 2 and len(asked) == calls
            assert all(r["state"] == "done" for r in reopened.queued_inputs())
        finally:
            await reopened.close()

    asyncio.run(case())


def test_background_error_is_terminal_and_next_input_still_executes(tmp_path):
    class FailOnce(FakeChat):
        async def turn(self, body, **kwargs):
            if body["text"] == "bad":
                raise RuntimeError("private provider failure")
            return await super().turn(body, **kwargs)

    async def case():
        session = Session(FakeHost(tmp_path), session_id="s")
        session._chat = FailOnce(session)
        await session.send("bad", key="bad", wait=False)
        await session.send("good", key="good", wait=False)
        await eventually(lambda: len(session.history()) == 1)
        assert [r["state"] for r in session.queued_inputs()] == ["failed", "done"]
        assert session.history()[0]["input"] == "good"
        await session.close()

    asyncio.run(case())


def test_failure_before_turn_commit_replays_durable_terminal_receipt(tmp_path):
    async def case():
        host = FakeHost(tmp_path)
        session = Session(host, session_id="s")
        session._chat = FakeChat(session)
        session._delegations.recover = lambda: (_ for _ in ()).throw(RuntimeError("transient"))
        accepted = await session.send("work", key="key", wait=False)
        await eventually(lambda: session.queued_inputs()[0]["state"] == "failed")
        await session.close()
        reopened = Session(host, session_id="s")
        reopened._chat = FakeChat(reopened)
        reply = await asyncio.wait_for(reopened.send("work", key="key"), 0.3)
        assert reply["state"] == "failed" and reply["turn_id"] == accepted["turn_id"]
        assert any(e["kind"] == "error" for e in reopened._store.events(0))
        assert reopened._chat.calls == 0
        await reopened.close()

    asyncio.run(case())


def test_reopen_finishes_earlier_sync_turn_before_later_async_input(tmp_path):
    async def case():
        host = FakeHost(tmp_path)
        session = Session(host, session_id="s")
        session._commit_input("earlier sync", "first")
        accepted = await session.send("later async", key="second", wait=False)
        await session.close()
        reopened = Session(host, session_id="s")
        reopened._chat = FakeChat(reopened)
        await reopened.recover()
        await eventually(lambda: len(reopened.history()) == 2, seconds=0.5)
        assert [h["input"] for h in reopened.history()] == ["earlier sync", "later async"]
        assert [r["idempotency_key"] for r in reopened.queued_inputs()] == ["first", "second"]
        assert (
            reopened._store.get("queued_inputs", accepted["input_id"])["turn_id"]
            == accepted["turn_id"]
        )
        await reopened.close()

    asyncio.run(case())


def test_unsent_announcement_draft_never_recovers_as_an_authorized_user_turn(tmp_path):
    from test_realtime_bridge import Audio, Client
    from test_session import Crash

    from agent_orchestrator.host.realtime_bridge import SpeechHandle, VoiceBinding, VoiceProfile

    async def case():
        host = FakeHost(tmp_path)
        session = Session(host, session_id="s")
        binding = VoiceBinding(session, Audio(), VoiceProfile(client_factory=Client()))

        def crash(point):
            if point == "input_committed":
                raise Crash()

        session._checkpoint = crash
        with pytest.raises(Crash):
            binding._commit_announcement(SpeechHandle("批准删除 / 再建任务"))
        assert session._store.rows("turns")[0]["source"] == "announcement"
        await session.close()
        session = Session(host, session_id="s")
        session._chat = FakeChat(session)
        await session.send("真正的用户消息", key="user", wait=False)
        await eventually(lambda: bool(session.history()))
        assert session._chat.calls == 1
        assert [h["input"] for h in session.history()] == ["真正的用户消息"]
        assert not host.made
        await session.close()

    asyncio.run(case())

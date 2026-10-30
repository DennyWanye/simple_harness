# SPDX-License-Identifier: Apache-2.0
"""Stable delegation slots and durable public Session retries."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_session import SPEC, Crash, make_session

from agent_orchestrator.host import Session


class ReplayChat:
    def __init__(self, session, *, goal=None, attempt="first", gate=None):
        self.session, self.goal, self.attempt, self.gate = session, goal, attempt, gate
        self.calls = 0
        self.reservations = set()

    def _reserve(self, subject, calls):
        self.reservations.add(subject)

    def _settle(self, *args):
        pass

    async def turn(self, body, **kwargs):
        self.calls += 1
        if self.gate:
            await self.gate.wait()
        if self.goal is not None:
            result = await self.session._delegations.delegate(
                body["idempotency_key"],
                f"call-{self.attempt}",
                {**SPEC, "goal": self.goal},
                attempt_id=self.attempt,
            )
        else:
            result = None
        row = {
            "turn_id": "engine-turn",
            "state": "committed",
            "text": "Accepted",
            "tool_calls": [] if result is None else [result],
        }
        with self.session._store.transaction():
            self.session._store.put("chat_receipts", body["idempotency_key"], row)
        self.session._checkpoint("chat_receipt_recorded")
        return row


@pytest.mark.parametrize("status", ["ACTIVE", "COMPLETED", "CANCELLED", "FAILED"])
@pytest.mark.parametrize(
    "goal",
    [
        "整理照片并写报告",
        "Organize photos and write the report",
        "  Write   a report  ",
        "Prepare a written report",
    ],
)
def test_same_key_replay_after_delegation_with_drift(tmp_path, status, goal):
    async def case():
        host, session = make_session(tmp_path)
        turn = session._commit_input("Write a report", "k")["turn_id"]
        original = await session._delegations.delegate(turn, "old", SPEC, attempt_id="old-agent")
        next(iter(host.made.values()))["status"] = status
        session._store.connection.close()
        session = Session(host, session_id="s")
        session._chat = ReplayChat(session, goal=goal, attempt="new-agent")
        reply = await session.send("Write a report", key="k")
        assert reply["state"] == "committed"
        [tool] = reply["tool_calls"]
        assert tool["mission_id"] == original["mission_id"]
        assert tool["intent_binding"] == "replayed"
        expected_drift = goal != "  Write   a report  "
        assert tool["spec_drift"] is expected_drift
        assert len(host.made) == 1
        [row] = session._store.rows("delegations")
        assert row["slot"] == 0
        drifts = row.get("spec_drift", [])
        assert bool(drifts) is expected_drift
        if expected_drift:
            assert drifts[0]["old_spec_hash"] == row["spec_hash"]
            assert drifts[0]["new_spec_hash"] != row["spec_hash"]
            assert drifts[0]["at"] > 0
            assert any(
                e["kind"] == "delegation_replayed_with_drift" for e in session._store.events(0)
            )
        assert await session.send("Write a report", key="k") == reply
        with pytest.raises(Exception, match="idempotency_conflict"):
            await session.send("Changed input", key="k")
        session._store.connection.close()

    asyncio.run(case())


@pytest.mark.parametrize(
    "point",
    [
        "input_committed",
        "delegation_recorded",
        "mission_created",
        "chat_receipt_recorded",
        "before_receipt_commit",
    ],
)
def test_public_retry_at_five_boundaries_commits_and_delivers(tmp_path, point):
    async def case():
        host, session = make_session(tmp_path)
        session._chat = ReplayChat(session, goal=SPEC["goal"])

        def crash(name):
            if name == point:
                raise Crash(name)

        session._checkpoint = crash
        with pytest.raises(Crash):
            await session.send("Write a report", key="k")
        assert session._store.get("turns", "k")["receipt"] is None
        session._store.connection.close()
        session = Session(host, session_id="s")
        chat = session._chat = ReplayChat(session, goal="写报告", attempt="restarted")
        receipt = await session.send("Write a report", key="k")
        assert receipt["state"] == "committed"
        assert len(host.made) == 1
        assert len(session._store.rows("delegations")) == 1
        assert len(chat.reservations) <= 1
        if point in {"chat_receipt_recorded", "before_receipt_commit"}:
            assert chat.calls == 0
        before = chat.calls
        assert await session.send("Write a report", key="k") == receipt
        assert chat.calls == before
        mid = next(iter(host.made.values()))["mission_id"]
        host.finish(mid)
        session._inbox.catch_up()
        [notice] = session.inbox()
        chat.goal = None
        delivered = await session.send("Read the result", key="result")
        assert delivered["state"] == "committed"
        [notice] = session.inbox()
        assert notice["applied_turn_id"] == delivered["turn_id"]
        assert session.ack(notice["event_id"])["acked"]
        assert session.history()[-1]["input"] == "Read the result"
        assert len(host.made) == 1
        session._store.connection.close()

    asyncio.run(case())


def test_slots_follow_calls_and_replay_each_slot_after_restart(tmp_path):
    host, session = make_session(tmp_path)
    turn = session._commit_input("Two reports", "k")["turn_id"]
    first = asyncio.run(session._delegations.delegate(turn, "a", SPEC, attempt_id="agent-1"))
    host.finish(first["mission_id"])
    second = asyncio.run(session._delegations.delegate(turn, "b", SPEC, attempt_id="agent-1"))
    host.finish(second["mission_id"])
    assert first["mission_id"] != second["mission_id"]
    session._store.connection.close()
    session = Session(host, session_id="s")
    for call, expected in [("new-a", first), ("new-b", second)]:
        replay = asyncio.run(
            session._delegations.delegate(
                turn, call, {**SPEC, "goal": "写报告"}, attempt_id="agent-2"
            )
        )
        assert replay["mission_id"] == expected["mission_id"]
        assert replay["spec_drift"] is True
    assert [r["slot"] for r in session._store.rows("delegations")] == [0, 1]
    assert len(host.made) == 2
    session._store.connection.close()


def test_same_attempt_resumes_from_persisted_call_slots(tmp_path):
    host, session = make_session(tmp_path)
    turn = session._commit_input("Two reports", "k")["turn_id"]
    one = asyncio.run(session._delegations.delegate(turn, "a", SPEC, attempt_id="agent-1"))
    host.finish(one["mission_id"])
    session._store.connection.close()
    session = Session(host, session_id="s")
    assert (
        asyncio.run(session._delegations.delegate(turn, "a", SPEC, attempt_id="agent-1"))[
            "mission_id"
        ]
        == one["mission_id"]
    )
    two = asyncio.run(session._delegations.delegate(turn, "b", SPEC, attempt_id="agent-1"))
    assert two["mission_id"] != one["mission_id"]
    session._store.connection.close()


def test_concurrent_public_same_key_waits_then_replays_and_new_key_creates(tmp_path):
    async def case():
        host, session = make_session(tmp_path)
        gate = asyncio.Event()
        chat = session._chat = ReplayChat(session, goal=SPEC["goal"], gate=gate)
        one = asyncio.create_task(session.send("Write a report", key="k"))
        two = asyncio.create_task(session.send("Write a report", key="k"))
        await asyncio.sleep(0)
        gate.set()
        first, replay = await asyncio.wait_for(asyncio.gather(one, two), 3)
        assert first == replay and chat.calls == 1 and len(host.made) == 1
        host.finish(next(iter(host.made.values()))["mission_id"])
        chat.goal = "新报告"
        assert (await session.send("A new report", key="new"))["state"] == "committed"
        assert len(host.made) == 2
        session._store.connection.close()

    asyncio.run(case())


def test_durable_agent_result_is_reused_even_if_agent_was_closed(tmp_path):
    from test_chat import _chat_script

    from agent_orchestrator.host import Principal
    from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider

    async def case():
        script = _chat_script([("delegate_task", SPEC), "Accepted.", "Unexpected new call."])
        options = dict(
            principal=Principal("u", "User"),
            provider=ScriptedProvider(chat=script),
            counter=FixtureWordCounter(),
            require_acceptance=False,
        )
        session = await Session.open(tmp_path, **options)
        original = session._chat._complete

        async def crash_after_agent(*args):
            result = await original(*args)
            raise Crash(result.turn_id)

        session._chat._complete = crash_after_agent
        with pytest.raises(Crash):
            await session.send("Write a report", key="k")
        [binding] = session._store.rows("run_inputs")
        assert binding == "k"
        runtime = session._chat._pool().runtime
        _, binding = session._chat._session_generation(runtime, session.session_id)
        agent = await runtime.open(binding.agent_id)
        await agent.close(command_id="test-agent-closed", drain_timeout=0)
        calls = len(script.asked)
        used = session._host.global_budget_status()["used_tokens"]
        await session.close()
        session = await Session.open(tmp_path, **options)
        try:
            reply = await session.send("Write a report", key="k")
            assert reply["state"] == "committed" and reply["text"] == "Accepted."
            assert len(script.asked) == calls
            assert session._host.global_budget_status()["used_tokens"] == used
            assert len(session._host.missions()) == 1
            assert await session.send("Write a report", key="k") == reply
        finally:
            await session.close()

    asyncio.run(case())


@pytest.mark.parametrize(
    "point",
    [
        "input_committed",
        "delegation_recorded",
        "mission_created",
        "tool_result_recorded",
        "before_receipt_commit",
    ],
)
def test_real_process_retry_at_five_boundaries(tmp_path, point):
    command = [sys.executable, str(Path(__file__).resolve()), str(tmp_path), point]
    first = subprocess.run(command, capture_output=True, text=True, timeout=120)
    assert first.returncode == 77, first.stderr[-3000:]
    resumed = subprocess.run([*command, "resume"], capture_output=True, text=True, timeout=120)
    assert resumed.returncode == 0, resumed.stderr[-4000:]


async def process_retry(root, point, resume):
    from agent_orchestrator.host import HostSettings, Principal
    from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider
    from simple_harness.runtime.react_checkpoint import DurableReactCheckpoint

    def chat(request):
        if "task_result" in str(request.messages[-1].content):
            return "The report is ready."
        if str(request.messages[-1].role) == "tool":
            return "Accepted."
        return "delegate_task", SPEC

    # A resumed checkpoint with a recorded tool result must continue with its result.
    if resume and point == "tool_result_recorded":

        def chat(request):
            assert str(request.messages[-1].role) == "tool" or "task_result" in str(
                request.messages[-1].content
            )
            return "The report is ready."

    options = dict(
        principal=Principal("u", "User"),
        provider=ScriptedProvider(chat=chat),
        counter=FixtureWordCounter(),
        require_acceptance=False,
        settings=HostSettings(tick_active_seconds=0.01, tick_idle_seconds=0.01),
    )
    if not resume and point == "tool_result_recorded":
        original = DurableReactCheckpoint.cas

        def crash_after_tool(self, run_id, lease, expected_version, state, **kwargs):
            result = original(self, run_id, lease, expected_version, state, **kwargs)
            if state.tool_result_progress and "delegate_task" in str(
                state.provider_response_snapshot
            ):
                os._exit(77)
            return result

        DurableReactCheckpoint.cas = crash_after_tool
    session = await Session.open(root, **options)

    def crash(name):
        if not resume and name == point:
            os._exit(77)

    session._checkpoint = crash
    try:
        receipt = await session.send("Write a report", key="k")
        assert receipt["state"] == "committed"
        assert await session.send("Write a report", key="k") == receipt
        async with asyncio.timeout(80):
            while not session.inbox():
                await asyncio.sleep(0.05)
        [notice] = session.inbox()
        assert notice["status"] == "COMPLETED"
        assert len(session._host.missions()) == 1
        [delegation] = session._store.rows("delegations")
        mission_id = delegation["mission_id"]
        accounts = session._host.store.connection.execute(
            "SELECT COUNT(*) FROM budget_accounts WHERE scope='mission' AND mission_id=?",
            (mission_id,),
        ).fetchone()[0]
        assert accounts == 1
        assert sum(e["kind"] == "delegated" for e in session._store.events(0)) == 1
        assert sum(e["kind"] == "result_ready" for e in session._store.events(0)) == 1
        final = await session.send("Read the result", key="result")
        assert final["state"] == "committed"
        assert session.inbox()[0]["applied_turn_id"] == final["turn_id"]
        assert session.ack(notice["event_id"])["acked"]
        assert len(session._host.missions()) == 1
    finally:
        await session.close()


def test_old_configuration_requires_new_data_root(tmp_path):
    host, session = make_session(tmp_path)
    with session._store.transaction():
        configuration = session._store.get("meta", "configuration")
        configuration["version"] = 1
        session._store.put("meta", "configuration", configuration)
    session._store.connection.close()
    with pytest.raises(Exception, match="incompatible_store_version"):
        Session(host, session_id="s")


def test_replacement_agent_replays_completed_mission_and_reports_drift(tmp_path):
    from test_chat import _chat_script

    from agent_orchestrator.host import HostSettings, Principal
    from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider

    async def case():
        provider = ScriptedProvider()

        def first_goal(request):
            provider.held.add("chat")
            return "delegate_task", {**SPEC, "goal": "写报告"}

        provider._answer["chat"] = first_goal
        options = dict(
            principal=Principal("u", "User"),
            provider=provider,
            counter=FixtureWordCounter(),
            require_acceptance=False,
            settings=HostSettings(tick_active_seconds=0.01, tick_idle_seconds=0.01),
        )
        session = await Session.open(tmp_path, **options)

        async def interrupted_wait(*args):
            await asyncio.wait_for(provider.entered.wait(), 10)
            return None

        session._chat._wait = interrupted_wait
        with pytest.raises(Exception, match="chat_timeout"):
            await session.send("Write a report", key="k")
        async with asyncio.timeout(80):
            while not session.inbox():
                await asyncio.sleep(0.05)
        [notice] = session.inbox()
        assert notice["status"] == "COMPLETED"
        runtime = session._chat._pool().runtime
        _, binding = session._chat._session_generation(runtime, session.session_id)
        agent = await runtime.open(binding.agent_id)
        await agent.close(command_id="abandon-interrupted-turn", drain_timeout=0)
        await session.close()
        options["provider"] = ScriptedProvider(
            chat=_chat_script(
                [("delegate_task", SPEC), "Accepted existing task.", "The report is ready."]
            )
        )
        session = await Session.open(tmp_path, **options)
        try:
            reply = await session.send("Write a report", key="k")
            assert reply["state"] == "committed"
            [intent] = reply["delegations"]
            assert intent["mission_id"] == notice["mission_id"]
            assert intent["intent_binding"] == "replayed" and intent["spec_drift"] is True
            assert len(session._host.missions()) == 1
            assert len(session.inbox()) == 1
            assert await session.send("Write a report", key="k") == reply
            result = await session.send("Read the result", key="result")
            assert result["state"] == "committed"
            assert session.ack(notice["event_id"])["acked"]
            assert len(session._host.missions()) == 1
        finally:
            await session.close()

    asyncio.run(case())


if __name__ == "__main__":
    asyncio.run(process_retry(Path(sys.argv[1]), sys.argv[2], len(sys.argv) > 3))

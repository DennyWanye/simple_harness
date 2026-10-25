# SPDX-License-Identifier: Apache-2.0
"""Session contracts exercised against the real chat runtime and durable ledgers."""

from __future__ import annotations

import asyncio
import hashlib
import os
import sqlite3
import subprocess
import sys
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_chat import _chat_script, _device_tool

from agent_orchestrator.host import HostSettings, Principal, Session
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider

SPEC = {
    "goal": "Write a report",
    "success_criteria": ["file:report.md"],
    "reason": "Multiple steps",
}


def test_v1_instruction_bytes_and_pool_id_remain_frozen():
    from agent_orchestrator.deployment.native_pools import native_profile_id
    from agent_orchestrator.host.chat import CHAT_INSTRUCTIONS

    assert hashlib.sha256(CHAT_INSTRUCTIONS.encode()).hexdigest() == (
        "b601024f668a94c6dba9249a43ff1b38d154be06d6c9bd2afa3fccddd4ab0022"
    )
    assert native_profile_id(614400, chat=True) == "deepseek-native-600k-chat-v1"


@pytest.fixture(autouse=True)
def local_process_identity(monkeypatch):
    # The sandbox denies ps; only the process-identity port is replaced in these tests.
    monkeypatch.setattr(
        "agent_orchestrator.orchestrator.recovery_coordinator.process_identity",
        lambda pid: {"process_started_at": "session-test-process"} if pid == os.getpid() else None,
    )


class Crash(BaseException):
    pass


class FakeHost:
    def __init__(self, root):
        self.root = root
        self.principal = Principal("u", "User")
        self.tenant_id = "local"
        self.tools = {}
        self.made = {}
        self.event_rows = {}
        self.cancelled = []
        self.requests = []
        self.decisions = []

    def door(self, body):
        return deepcopy(body)

    def create_mission(self, body):
        key = body["idempotency_key"]
        created = key not in self.made
        if created:
            mid = f"mission-{len(self.made) + 1}"
            self.made[key] = {"mission_id": mid, "status": "ACTIVE", "body": deepcopy(body)}
            self.event_rows[mid] = []
        return {"mission_id": self.made[key]["mission_id"], "created": created, "spec_hash": key}

    def snapshot(self, mid):
        row = next(r for r in self.made.values() if r["mission_id"] == mid)
        return {
            "through_seq": len(self.event_rows[mid]),
            "state_version": 1,
            "snapshot": {
                "mission": {"id": mid, "status": row["status"]},
                "planning_questions": self.requests,
                "artifacts": [],
            },
        }

    def events(self, mid, *, after_seq=0, limit=100):
        rows = [e for e in self.event_rows[mid] if e["seq"] > after_seq][:limit]
        return {
            "events": rows,
            "has_more": False,
            "through_seq": rows[-1]["seq"] if rows else after_seq,
        }

    def approvals(self, mid=None):
        return []

    def cancel(self, mid):
        self.cancelled.append(mid)
        row = next(r for r in self.made.values() if r["mission_id"] == mid)
        row["status"] = "CANCELLED"
        return {"mission_id": mid, "status": "CANCELLED"}

    def answer_planning_question(self, body):
        self.decisions.append(body)
        return {"answered": True}

    def finish(self, mid):
        next(r for r in self.made.values() if r["mission_id"] == mid)["status"] = "COMPLETED"
        self.event_rows[mid].append(
            {
                "seq": 1,
                "id": "final-1",
                "type": "MissionCompleted",
                "payload": {"private": "must not escape"},
            }
        )


def make_session(tmp_path):
    host = FakeHost(tmp_path)
    return host, Session(host, session_id="s")


def test_input_replay_conflict_and_principal_binding(tmp_path):
    host, session = make_session(tmp_path)
    one = session._commit_input("hello", "k")
    assert session._commit_input("hello", "k") == one
    with pytest.raises(Exception, match="idempotency_conflict"):
        session._commit_input("different", "k")
    host.principal = Principal("other", "Other")
    with pytest.raises(Exception, match="permission_denied"):
        Session(host, session_id="s")


def test_missing_key_cannot_replay_the_literal_none_key(tmp_path):
    _, session = make_session(tmp_path)
    row = session._commit_input("hello", "None")
    with session._store.transaction():
        row["receipt"] = {"state": "committed", "text": "hello"}
        session._store.put("turns", "None", row)
    with pytest.raises(Exception, match="invalid_request"):
        asyncio.run(session.send("hello"))


def test_delegation_idempotency_matrix_and_busy(tmp_path):
    host, session = make_session(tmp_path)
    turn = session._commit_input("do it", "k")["turn_id"]
    first = session._delegations.delegate(turn, "c1", SPEC)
    assert session._delegations.delegate(turn, "c1", SPEC) == first
    assert session._delegations.delegate(turn, "c2", SPEC) == first
    with pytest.raises(Exception, match="idempotency_conflict"):
        session._delegations.delegate(turn, "c3", {**SPEC, "success_criteria": ["different"]})
    second_turn = session._commit_input("again", "k2")["turn_id"]
    with pytest.raises(Exception, match="busy"):
        session._delegations.delegate(second_turn, "c4", SPEC)
    host.finish(first["mission_id"])
    second = session._delegations.delegate(second_turn, "c4", SPEC)
    assert second["mission_id"] != first["mission_id"] and len(host.made) == 2


@pytest.mark.parametrize(
    "point", ["input_committed", "delegation_recorded", "mission_created", "inbox_written"]
)
def test_crash_recovery_at_each_durable_boundary(tmp_path, point):
    host, session = make_session(tmp_path)

    def fail(name):
        if name == point:
            raise Crash(name)

    session._checkpoint = fail
    if point == "input_committed":
        with pytest.raises(Crash):
            session._commit_input("do it", "k")
        session = Session(host, session_id="s")
    turn = session._commit_input("do it", "k")["turn_id"]
    if point in {"delegation_recorded", "mission_created"}:
        with pytest.raises(Crash):
            session._delegations.delegate(turn, "c1", SPEC)
        session = Session(host, session_id="s")
        session._delegations.recover()
    receipt = session._delegations.delegate(turn, "c2", SPEC)
    assert len(host.made) == 1
    host.finish(receipt["mission_id"])
    if point == "inbox_written":
        with pytest.raises(Crash):
            session._inbox.catch_up()
        session = Session(host, session_id="s")
    session._inbox.catch_up()
    session._inbox.catch_up()
    assert len(session.inbox()) == 1
    events = session._store.events(0)
    assert sum(e["kind"] == "result_ready" for e in events) == 1
    assert "must not escape" not in str(events)
    first = session._commit_input("read result", "result")
    replay = session._commit_input("read result", "result")
    assert first == replay and first["inbox_ids"]
    # Freezing an input is not evidence that its result reached committed history.
    assert session._commit_input("next", "next")["inbox_ids"] == first["inbox_ids"]
    assert all(n["applied_turn_id"] is None for n in session.inbox())


def test_cancel_creation_then_recover_and_completed_is_immutable(tmp_path):
    host, session = make_session(tmp_path)
    turn = session._commit_input("do it", "k")["turn_id"]

    def fail(point):
        if point == "delegation_recorded":
            raise Crash()

    session._checkpoint = fail
    with pytest.raises(Crash):
        session._delegations.delegate(turn, "c", SPEC)
    [pending] = session.tasks()
    session.cancel_task(pending["delegation_id"])
    session = Session(host, session_id="s")
    session._delegations.recover()
    [task] = session.tasks()
    assert host.cancelled == [task["mission_id"]]
    host.finish(task["mission_id"])
    session.cancel_task(task["mission_id"])
    assert len(host.cancelled) == 1
    with pytest.raises(Exception, match="permission_denied"):
        session.task("foreign")


def test_respond_version_scope_and_human_only_ack(tmp_path):
    host, session = make_session(tmp_path)
    turn = session._commit_input("do it", "k")["turn_id"]
    mid = session._delegations.delegate(turn, "c", SPEC)["mission_id"]
    host.requests = [{"question_id": "q", "mission_id": mid, "state": "PENDING", "version": 2}]
    with pytest.raises(Exception, match="stale_request"):
        session.respond("q", "answer", version=1, answer="yes")
    with pytest.raises(Exception, match="permission_denied"):
        session.respond("foreign", "approve", version=2)
    assert session.respond("q", "answer", version=2, answer="yes")["answered"]
    assert host.decisions[0]["expected_version"] == 2
    session._inbox.catch_up()
    host.requests[0]["state"] = "ANSWERED"
    with pytest.raises(Exception, match="stale_request"):
        session.respond("q", "answer", version=2, answer="yes")
    host.finish(mid)
    session._inbox.catch_up()
    [notice] = session.inbox()
    assert session.ack(notice["event_id"])["acked"]
    assert session.ack(notice["event_id"])["acked"]


def test_real_v2_pool_tools_history_budget_and_restore(tmp_path):
    async def case():
        seen = []
        script = _chat_script([("device_status", {}), "The light has no battery.", "I remember."])
        options = dict(
            principal=Principal("u", "User"),
            provider=ScriptedProvider(chat=script),
            counter=FixtureWordCounter(),
            tools=[_device_tool(seen)],
            require_acceptance=False,
        )
        session = await Session.open(tmp_path / "host", **options)
        try:
            reply = await session.send("Which device?", idempotency_key="k")
            assert reply["state"] == "committed" and seen
            assert session.tasks() == []
            assert "-chat-v2-" in session._chat.profile_id
            assert session._host.chat_service.profile_id.endswith("-chat-v1")
            assert await session.send("Which device?", idempotency_key="k") == reply
            budget = session.budget()
            assert budget["accounting"] == "references_only"
            assert session._host.global_budget_status()["reserved_tokens"] == 0
        finally:
            await session.close()
        session = await Session.open(tmp_path / "host", **options)
        try:
            restored = await session.send("Remember?", idempotency_key="k2")
            assert restored["agent_id"] == reply["agent_id"]
            assert any("Which device?" in str(m.content) for m in script.asked[-1].messages)
            stream = session.events(after=0)
            event = await asyncio.wait_for(anext(stream), 5)
            assert event["session_id"] == "default" and event["schema_version"] == 1
            await stream.aclose()
        finally:
            await session.close()

    asyncio.run(case())


def test_real_control_command_creates_one_mission_without_domain_tool(tmp_path):
    async def case():
        script = _chat_script([("delegate_task", SPEC), ("delegate_task", SPEC), "Accepted."])
        session = await Session.open(
            tmp_path / "host",
            principal=Principal("u", "User"),
            provider=ScriptedProvider(chat=script),
            counter=FixtureWordCounter(),
            require_acceptance=False,
        )
        try:
            reply = await session.send("Write a report", idempotency_key="k")
            assert reply["state"] == "committed", reply
            assert len(session.tasks()) == 1, (reply, session._chat._assembled.gateway.calls)
            assert "delegate_task" not in session._host.tools
            assert [c["outcome"] for c in reply["tool_calls"]] == ["succeeded", "succeeded"], (
                [
                    (c.get("error_code"), c.get("stage"))
                    for c in session._chat._assembled.gateway.calls
                ],
                str(session._host.orchestrator._recovery.report),
            )
            assert len(session._host.missions()) == 1
            assert await session.send("Write a report", idempotency_key="k") == reply
        finally:
            await session.close()

    asyncio.run(case())


def test_live_text_pieces_are_ordered_and_never_replayed(tmp_path):
    async def case():
        _, session = make_session(tmp_path)
        row = session._commit_input("hello", "k")
        session._current_input = row
        stream = session.events()
        assert (await anext(stream))["kind"] == "input_committed"
        waiting = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        session._piece({"text": "one", "generation": 1})
        session._piece({"text": "two", "generation": 1})
        with session._store.transaction():
            session._store.event("turn_committed", key="done", turn_id=row["turn_id"])
        one = await asyncio.wait_for(waiting, 2)
        two = await asyncio.wait_for(anext(stream), 2)
        done = await asyncio.wait_for(anext(stream), 2)
        assert [one["kind"], two["kind"], done["kind"]] == [
            "text_delta",
            "text_delta",
            "turn_committed",
        ]
        assert one["seq"] < two["seq"] < done["seq"]
        assert all(e["kind"] != "text_delta" for e in session._store.events(0))
        await stream.aclose()

    asyncio.run(case())


def test_approval_binding_version_expiration_and_identity_fields(tmp_path):
    host, session = make_session(tmp_path)
    turn = session._commit_input("do it", "k")["turn_id"]
    mid = session._delegations.delegate(turn, "c", SPEC)["mission_id"]
    approval = {
        "request_id": "a",
        "mission_id": mid,
        "state": "PENDING",
        "binding": {"revision": 1},
        "expires_at": 9999999999,
    }
    host.approvals = lambda mission_id: [approval]
    host.decide = lambda rid, decision, **fields: (
        host.decisions.append((rid, decision, fields)) or {"ok": True}
    )
    session._inbox.catch_up()
    event = next(e for e in session._store.events(0) if e["kind"] == "needs_response")
    version = event["payload"]["version"]
    with pytest.raises(Exception, match="invalid_request"):
        session.respond("a", "approve", version=version, mission_id="foreign")
    session.respond("a", "approve", version=version)
    assert "expected_version" not in host.decisions[0][2]
    approval["binding"] = {"revision": 2}
    with pytest.raises(Exception, match="stale_request"):
        session.respond("a", "approve", version=version)
    approval["expires_at"] = 1
    session._inbox.catch_up()
    current = [e for e in session._store.events(0) if e["kind"] == "needs_response"][-1]["payload"][
        "version"
    ]
    with pytest.raises(Exception, match="stale_request"):
        session.respond("a", "approve", version=current)
    host.approvals = lambda mission_id: []
    with pytest.raises(Exception, match="stale_request"):
        session.respond("a", "approve", version=current)


def test_real_mission_result_inbox_next_turn_and_cold_events(tmp_path):
    async def case():
        script = _chat_script([("delegate_task", SPEC), "Accepted.", "The report is ready."])
        options = dict(
            principal=Principal("u", "User"),
            provider=ScriptedProvider(chat=script),
            counter=FixtureWordCounter(),
            require_acceptance=False,
            settings=HostSettings(tick_active_seconds=0.01, tick_idle_seconds=0.01),
        )
        root = tmp_path / "host"
        session = await Session.open(root, **options)
        try:
            await session.send("Write a report", key="k")
            stream = session.events()
            result = None
            async with asyncio.timeout(60):
                async for event in stream:
                    if event["kind"] == "result_ready":
                        result = event
                        break
            await stream.aclose()
            assert result["payload"]["status"] == "COMPLETED"
            [task] = session.tasks()
            artifacts = task["snapshot"]["snapshot"]["artifacts"]
            assert session._host.artifact_read(artifacts[0]["id"])["content"]
            used_before = session._host.global_budget_status()["used_tokens"]
            from agent_orchestrator.orchestrator.budget_accounts import global_account_id

            store = session._host.store
            ledger = session._host.orchestrator.commit.ledger
            available = session._host.global_budget_status()["remaining_tokens"]
            with store.transaction():
                ledger.reserve(
                    account_id=global_account_id(store.now),
                    subject_id="test-budget-pressure",
                    tokens=available,
                    counts_attempt=False,
                    mission_id="chat",
                )
            assert session._host.global_budget_status()["remaining_tokens"] == 0
            assert session.task(task["mission_id"])["status"] == "COMPLETED"
            reply = await session.send("What happened?", key="k2")
            assert reply["state"] == "committed"
            assert any("task_result" in str(m.content) for m in script.asked[-1].messages)
            assert session._host.global_budget_status()["used_tokens"] - used_before == (
                reply["usage"]["input_tokens"] + reply["usage"]["output_tokens"]
            )
            assert len(session.inbox()) == 1
            with store.transaction():
                ledger.settle(subject_id="test-budget-pressure")
            assert session._host.global_budget_status()["reserved_tokens"] == 0
        finally:
            await session.close()
        session = await Session.open(root, **options)
        try:
            stream = session.events(after=result["seq"] - 1)
            restored = await asyncio.wait_for(anext(stream), 2)
            assert restored == result
            assert len(session.inbox()) == 1 and len(session.tasks()) == 1
            await stream.aclose()
        finally:
            await session.close()

    asyncio.run(case())


def test_sources_validation_and_untrusted_model_identity(tmp_path):
    _, session = make_session(tmp_path)
    turn = session._commit_input("do it", "k")["turn_id"]
    for spec in (
        {**SPEC, "principal_id": "other"},
        {**SPEC, "success_criteria": ["x"] * 13},
        {**SPEC, "sources": [{"path": "../escape", "content": "x"}]},
        {**SPEC, "sources": [{"path": str(i), "content": "x"} for i in range(9)]},
    ):
        with pytest.raises(Exception, match="invalid_arguments"):
            session._delegations.delegate(turn, "call", spec)
    assert session.tasks() == []


def test_result_reserve_covers_the_frozen_agent_limit(tmp_path):
    async def case():
        session = await Session.open(
            tmp_path / "host",
            principal=Principal("u", "User"),
            provider=ScriptedProvider(chat=_chat_script([("delegate_task", SPEC), "Accepted."])),
            counter=FixtureWordCounter(),
            require_acceptance=False,
        )
        try:
            await session.send("Write a report", key="k")
            [task] = session.tasks()
            subject = session._result_subject(task["delegation_id"])
            store = session._host.store
            reservation = store.connection.execute(
                "SELECT reserved_tokens FROM budget_reservations WHERE subject_id=?", (subject,)
            ).fetchone()
            window = session._host.settings.context_input_tokens + 131072
            assert reservation[0] == session.policy.max_model_calls * window
        finally:
            await session.close()

    asyncio.run(case())


def test_budget_refusal_does_not_leave_a_recovery_loop(tmp_path, monkeypatch):
    host, session = make_session(tmp_path)
    turn = session._commit_input("do it", "k")["turn_id"]
    from agent_orchestrator.host import SessionError

    def exhausted(row):
        raise SessionError("budget_exhausted")

    monkeypatch.setattr(session, "_reserve_result", exhausted)
    with pytest.raises(Exception, match="budget_exhausted"):
        session._delegations.delegate(turn, "c", SPEC)
    assert len(host.made) == 0
    session._delegations.recover()
    assert all(task["status"] == "REJECTED" for task in session.tasks())


def test_control_reconciliation_uses_durable_turn_binding(tmp_path):
    from simple_harness.contracts import CallId, RunId, freeze_json

    async def case():
        options = dict(
            principal=Principal("u", "User"),
            counter=FixtureWordCounter(),
            provider=ScriptedProvider(chat=_chat_script([("delegate_task", SPEC), "Accepted."])),
            require_acceptance=False,
        )
        session = await Session.open(tmp_path / "host", **options)
        try:
            reply = await session.send("Write a report", key="k")
            [call] = reply["tool_calls"]
            effect = SimpleNamespace(
                tool_name="delegate_task",
                arguments=freeze_json(SPEC),
                run_id=RunId(reply["agent_id"]),
                call_id=CallId(call["call_id"]),
            )
            before = session.tasks()[0]["mission_id"]
        finally:
            await session.close()
        session = await Session.open(tmp_path / "host", **options)
        try:
            observation = await session._chat._assembled.gateway.observe(effect)
            assert str(observation.state) == "completed"
            assert len(session.tasks()) == 1 and session.tasks()[0]["mission_id"] == before
        finally:
            await session.close()

    asyncio.run(case())


def test_polling_unchanged_mission_does_not_reproject_or_write(tmp_path):
    host, session = make_session(tmp_path)
    turn = session._commit_input("do it", "k")["turn_id"]
    mid = session._delegations.delegate(turn, "c", SPEC)["mission_id"]
    host.finish(mid)
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE events(mission_id TEXT, seq INTEGER)")
    connection.execute("INSERT INTO events VALUES(?,1)", (mid,))
    host.store = SimpleNamespace(connection=connection, read_view=nullcontext)
    reads = []
    original = host.events

    def events(*args, **kwargs):
        reads.append(mid)
        return original(*args, **kwargs)

    host.events = events
    session._inbox.catch_up()
    changes = session._store.connection.total_changes
    session._inbox.catch_up()
    assert reads == [mid]
    assert session._store.connection.total_changes == changes
    connection.close()


def test_combined_inbox_releases_every_result_allowance(tmp_path):
    async def case():
        session = await Session.open(
            tmp_path / "host",
            principal=Principal("u", "User"),
            provider=ScriptedProvider(chat=lambda request: "Both tasks stopped."),
            counter=FixtureWordCounter(),
            require_acceptance=False,
        )
        try:
            # Seed two completed input turns before the pump gets CPU time.
            for index in range(2):
                row = session._commit_input(f"request {index}", f"k{index}")
                receipt = session._delegations.delegate(row["turn_id"], f"c{index}", SPEC)
                session.cancel_task(receipt["mission_id"])
                with session._store.transaction():
                    row["receipt"] = {"state": "committed", "text": "Accepted."}
                    session._store.put("turns", row["idempotency_key"], row)
            reply = await session.send("Report both results", key="results")
            assert reply["state"] == "committed"
            assert len(session.inbox()) == 2
            assert session._host.global_budget_status()["reserved_tokens"] == 0
        finally:
            await session.close()

    asyncio.run(case())


@pytest.mark.parametrize(
    "point", ["input_committed", "delegation_recorded", "mission_created", "inbox_written"]
)
def test_process_exit_and_real_mission_recovery(tmp_path, point):
    command = [sys.executable, str(Path(__file__).resolve()), str(tmp_path), point]
    first = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert first.returncode == 77, first.stderr[-3000:]
    recovered = subprocess.run([*command, "resume"], capture_output=True, text=True, timeout=90)
    assert recovered.returncode == 0, recovered.stderr[-3000:]


async def process_worker(root, point, resume):
    import agent_orchestrator.orchestrator.recovery_coordinator as recovery

    # Process identity is the only OS port unavailable in the offline sandbox.
    recovery.process_identity = lambda pid: (
        {"process_started_at": "session-test-process"} if pid == os.getpid() else None
    )

    def answer(request):
        last = request.messages[-1]
        if str(last.role) == "tool":
            return "Accepted."
        if "task_result" in str(last.content):
            return "The report is ready."
        return "delegate_task", SPEC

    session = await Session.open(
        root,
        principal=Principal("u", "User"),
        provider=ScriptedProvider(chat=answer),
        counter=FixtureWordCounter(),
        require_acceptance=False,
        settings=HostSettings(tick_active_seconds=0.01, tick_idle_seconds=0.01),
    )

    def crash(name):
        if not resume and name == point:
            os._exit(77)

    session._checkpoint = crash
    try:
        await session.send("Write a report", key="original")
        stream = session.events()
        async with asyncio.timeout(75):
            async for event in stream:
                if event["kind"] == "result_ready":
                    break
        await stream.aclose()
        if resume:
            assert len(session._host.missions()) == 1
            assert len(session.inbox()) == 1
            reply = await session.send("What happened?", key="result")
            assert reply["state"] == "committed"
            assert len(session._host.missions()) == 1
            assert len([e for e in session._store.events(0) if e["kind"] == "result_ready"]) == 1
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(process_worker(Path(sys.argv[1]), sys.argv[2], len(sys.argv) > 3))

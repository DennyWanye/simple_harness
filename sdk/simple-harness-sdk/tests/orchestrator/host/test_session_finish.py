# SPDX-License-Identifier: Apache-2.0
"""Release blockers: rejected creation and recoverable result-delivery attempts."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_chat import _chat_script
from test_session import SPEC, Crash

from agent_orchestrator.host import HostRequestError, Principal, Session
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider
from agent_orchestrator.orchestrator.budget_accounts import global_account_id


def options(script):
    return dict(
        principal=Principal("finish-user", "User"),
        provider=ScriptedProvider(chat=script),
        counter=FixtureWordCounter(),
        require_acceptance=False,
    )


@pytest.mark.parametrize("paths", [("a.md", "a.md/child.md"), ("A/a.md", "a/b.md")])
def test_rejected_source_batch_does_not_poison_session_or_hold_budget(tmp_path, paths):
    async def case():
        spec = {**SPEC, "sources": [{"path": p, "content": "sample"} for p in paths]}
        script = _chat_script([("delegate_task", spec), "Rejected.", "Still available."])
        settings = options(script)
        session = await Session.open(tmp_path, **settings)
        try:
            reply = await session.send("Write a report", key="bad")
            assert reply["state"] == "committed"
            assert session._host.missions() == []
            assert session._host.global_budget_status()["reserved_tokens"] == 0
        finally:
            await session.close()
        session = await Session.open(tmp_path, **settings)
        try:
            assert (await session.send("Hello", key="good"))["state"] == "committed"
            turn = session._commit_input("New work", "new")["turn_id"]
            assert session._delegations.delegate(turn, "new-call", SPEC)["mission_id"]
        finally:
            await session.close()

    asyncio.run(case())


@pytest.mark.parametrize("known", [False, True])
def test_result_retry_process_exit_reconciles_only_claimed_attempts(tmp_path, known):
    command = [sys.executable, str(Path(__file__).resolve()), str(tmp_path), str(int(known))]
    crashed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    assert crashed.returncode == 77, crashed.stderr[-3000:]
    recovered = subprocess.run([*command, "resume"], capture_output=True, text=True, timeout=120)
    assert recovered.returncode == 0, recovered.stderr[-5000:]


async def result_retry_process(root, known, resume):
    import agent_orchestrator.orchestrator.recovery_coordinator as recovery

    recovery.process_identity = lambda pid: (
        {"process_started_at": "session-test-process"} if pid == os.getpid() else None
    )
    marker = root / "attempt.json"
    if not resume:
        session, settings, _, first = await session_with_result(root)
        provider = settings["provider"]
        provider.held.add("chat")

        async def unknown_result(agent, turn_id, deadline):
            await asyncio.wait_for(provider.entered.wait(), 10)
            return None

        session._chat._wait = unknown_result
        with pytest.raises(Exception, match="chat_timeout"):
            await session.send("Read results", key="retry")
        agent = await session._chat._pool().runtime.open(first["agent_id"])
        await agent.close(command_id="abandon-first", drain_timeout=0)
        await session.close()
        settings = options(_chat_script(["Recovered result."]))
        if not known:
            settings["provider"].held.add("chat")
        session = await Session.open(root, **settings)
        session._reserve_result({"delegation_id": "unclaimed"})

        def crash():
            attempts = session._store.rows("budget_attempts")
            subject = attempts[-1]
            assert subject.startswith("session-result:") and subject.count(":") == 2
            generation, binding = session._chat._session_generation(
                session._chat._pool().runtime, session.session_id
            )
            assert generation == 2
            reservation = session._host.orchestrator.commit.ledger.reservation(subject)
            marker.write_text(
                json.dumps(
                    {
                        "subject": subject,
                        "reservation": reservation,
                        "agent_id": binding.agent_id,
                        "used": session._host.global_budget_status()["used_tokens"],
                    }
                )
            )
            os._exit(77)

        if known:
            session._chat._close_books = lambda *args: crash()
        else:

            async def outbound(agent, turn_id, deadline):
                await asyncio.wait_for(settings["provider"].entered.wait(), 10)
                crash()

            session._chat._wait = outbound
        await session.send("Read results", key="retry")
        raise AssertionError("crash point not reached")

    saved = json.loads(marker.read_text())
    settings = options(_chat_script(["Recovered result.", "Next."]))
    session = await Session.open(root, **settings)
    try:
        store = session._host.store
        row = session._host.orchestrator.commit.ledger.reservation(saved["subject"])
        assert row["state"] == "SETTLED", row
        after = session._host.global_budget_status()["used_tokens"]
        delta = after - saved["used"]
        if known:
            assert 0 < delta < saved["reservation"]["reserved_tokens"]
        else:
            assert delta == saved["reservation"]["reserved_tokens"]
        session._chat._reconcile_reservations()
        assert session._host.global_budget_status()["used_tokens"] == after
        unclaimed = store.connection.execute(
            "SELECT state FROM budget_reservations WHERE subject_id='session-result:unclaimed'"
        ).fetchone()
        assert unclaimed[0] == "RESERVED"
        agent = await session._chat._pool().runtime.open(saved["agent_id"])
        await agent.close(command_id="abandon-crashed-retry", drain_timeout=0)
        reply = await session.send("Read results", key="retry")
        assert reply["state"] == "committed"
        reserved = store.connection.execute(
            "SELECT subject_id FROM budget_reservations WHERE state='RESERVED'"
        ).fetchall()
        assert [r[0] for r in reserved] == ["session-result:unclaimed"]
        assert all(n["applied_turn_id"] == reply["turn_id"] for n in session.inbox())
    finally:
        await session.close()
    session = await Session.open(root, **settings)
    try:
        before = session._host.global_budget_status()["used_tokens"]
        session._chat._reconcile_reservations()
        assert session._host.global_budget_status()["used_tokens"] == before
    finally:
        await session.close()


@pytest.mark.parametrize("code", ["conflict", "invalid_request", "sdk_refused", "not_found"])
def test_permanent_create_refusal_releases_hold_and_is_not_retried(tmp_path, monkeypatch, code):
    async def case():
        spec = {**SPEC, "sources": [{"path": "a.md", "content": "sample"}]}
        settings = options(_chat_script([("delegate_task", spec), "Rejected.", "Hello."]))
        session = await Session.open(tmp_path, **settings)
        calls = []

        def rejected(body):
            calls.append(body)
            raise HostRequestError(code, "source batch was rejected atomically")

        monkeypatch.setattr(session._host, "create_mission_with_sources", rejected)
        try:
            await session.send("Write a report", key="bad")
            assert len(calls) == 1
            assert session._host.global_budget_status()["reserved_tokens"] == 0
            [row] = session._store.rows("delegations")
            assert row["error"] == code
            session._delegations.recover()
            assert len(calls) == 1
        finally:
            await session.close()
        session = await Session.open(tmp_path, **settings)
        try:
            assert (await session.send("Hello", key="new"))["state"] == "committed"
            assert session._host.missions() == []
        finally:
            await session.close()

    asyncio.run(case())


def test_temporary_create_refusal_remains_recoverable(tmp_path, monkeypatch):
    async def case():
        session = await Session.open(
            tmp_path, **options(_chat_script([("delegate_task", SPEC), "Pending."]))
        )
        original = session._host.create_mission

        def unavailable(body):
            raise HostRequestError("orchestration_unavailable", "temporary outage")

        monkeypatch.setattr(session._host, "create_mission", unavailable)
        try:
            await session.send("Write a report", key="temporary")
            [row] = session._store.rows("delegations")
            assert not row.get("error")
            assert session._host.global_budget_status()["reserved_tokens"] > 0
            monkeypatch.setattr(session._host, "create_mission", original)
            session._delegations.recover()
            assert session.tasks()[0]["mission_id"]
            assert len(session._host.missions()) == 1
        finally:
            await session.close()

    asyncio.run(case())


async def session_with_result(root):
    script = _chat_script([("delegate_task", SPEC), "Accepted.", "Result received.", "Next."])
    settings = options(script)
    session = await Session.open(root, **settings)
    first = await session.send("Write a report", key="delegate")
    [task] = session.tasks()
    session.cancel_task(task["mission_id"])
    session._inbox.catch_up()
    assert session.inbox()
    return session, settings, script, first


def test_cancel_before_history_keeps_result_pending_across_restart(tmp_path, monkeypatch):
    async def case():
        session, settings, script, _ = await session_with_result(tmp_path)
        original = session._chat._complete
        before = len(script.asked)
        spent = session._host.global_budget_status()["used_tokens"]

        async def cancel_early(sid, agent, text, input_id):
            submitted = await agent.submit(text, input_id=input_id)
            session._chat._open_turns[sid] = (agent, submitted.turn_id)
            await session.interrupt()
            return await original(sid, agent, text, input_id)

        monkeypatch.setattr(session._chat, "_complete", cancel_early)
        try:
            reply = await session.send("Read results", key="cancelled")
            assert reply["state"] == "failed" and reply["model_calls"] == 0
            assert len(script.asked) == before
            assert all(n["applied_turn_id"] is None for n in session.inbox())
        finally:
            await session.close()
        session = await Session.open(tmp_path, **settings)
        try:
            assert all(n["applied_turn_id"] is None for n in session.inbox())
            reply = await session.send("Read them now", key="delivered")
            assert reply["state"] == "committed"
            assert any("task_result" in str(m.content) for m in script.asked[-1].messages)
            assert all(n["applied_turn_id"] == reply["turn_id"] for n in session.inbox())
            assert session._host.global_budget_status()["used_tokens"] - spent == (
                reply["usage"]["input_tokens"] + reply["usage"]["output_tokens"]
            )
            assert session._host.global_budget_status()["reserved_tokens"] == 0
        finally:
            await session.close()

    asyncio.run(case())


@pytest.mark.parametrize("exhausted", [False, True])
def test_unknown_result_timeout_retries_with_new_generation_and_budget(
    tmp_path, monkeypatch, exhausted
):
    async def case():
        session, settings, _, first = await session_with_result(tmp_path)
        provider = settings["provider"]
        provider.held.add("chat")

        async def no_result(agent, turn_id, deadline):
            await asyncio.wait_for(provider.entered.wait(), 10)
            return None

        monkeypatch.setattr(session._chat, "_wait", no_result)
        with pytest.raises(Exception, match="chat_timeout"):
            await session.send("Read results", key="unknown")
        agent = await session._chat._pool().runtime.open(first["agent_id"])
        await agent.close(command_id="abandon-unknown", drain_timeout=0)
        old_spend = session._host.global_budget_status()["used_tokens"]
        await session.close()
        provider.release.set()
        settings = options(_chat_script(["Recovered result.", "Next."]))
        session = await Session.open(tmp_path, **settings)
        try:
            if exhausted:
                store = session._host.store
                ledger = session._host.orchestrator.commit.ledger
                with store.transaction():
                    ledger.reserve(
                        account_id=global_account_id(store.now),
                        subject_id="pressure",
                        tokens=session._host.global_budget_status()["remaining_tokens"],
                        counts_attempt=False,
                        mission_id="chat",
                    )
                with pytest.raises(Exception, match="budget_exhausted"):
                    await session.send("Read results", key="unknown")
                with pytest.raises(Exception, match="budget_exhausted"):
                    await session.send("New input", key="new-input")
                assert all(n["applied_turn_id"] is None for n in session.inbox())
                with store.transaction():
                    ledger.settle(subject_id="pressure")
                reply = await session.send("Read results again", key="after-budget")
            else:
                reply = await session.send("Read results", key="unknown")
            assert reply["state"] == "committed"
            assert reply["agent_id"] != first["agent_id"]
            assert session._host.global_budget_status()["used_tokens"] >= old_spend
            assert (await session.send("Continue", key="next"))["state"] == "committed"
            assert len(session.tasks()) == 1
        finally:
            await session.close()

    asyncio.run(asyncio.wait_for(case(), 60))


def test_creation_receipt_loss_keeps_the_original_intent_recoverable(tmp_path, monkeypatch):
    async def case():
        session = await Session.open(
            tmp_path, **options(_chat_script([("delegate_task", SPEC), "Pending."]))
        )
        original = session._host.create_mission

        def lost_receipt(body):
            original(body)
            raise HostRequestError("conflict", "creation committed but receipt was lost")

        monkeypatch.setattr(session._host, "create_mission", lost_receipt)
        try:
            await session.send("Write a report", key="lost")
            assert len(session._host.missions()) == 1
            [row] = session._store.rows("delegations")
            assert not row.get("error")
            monkeypatch.setattr(session._host, "create_mission", original)
            session._delegations.recover()
            assert len(session.tasks()) == 1 and session.tasks()[0]["mission_id"]
            assert len(session._host.missions()) == 1
        finally:
            await session.close()

    asyncio.run(case())


def test_legacy_rejected_intent_is_finalized_during_the_first_reopen(tmp_path):
    async def case():
        settings = options(_chat_script(["Hello."]))
        session = await Session.open(tmp_path, **settings)
        turn = session._commit_input("Old request", "legacy")

        def interrupted(point):
            if point == "delegation_recorded":
                raise Crash()

        session._checkpoint = interrupted
        with pytest.raises(Crash):
            session._delegations.delegate(
                turn["turn_id"],
                "old-call",
                {
                    **SPEC,
                    "sources": [{"path": "a.md", "content": "sample"}],
                },
            )
        with session._store.transaction():
            [row] = session._store.rows("delegations")
            row["sources"].append(
                {"path": "sources/a.md/child.md", "content": "sample", "kind": "markdown"}
            )
            session._store.put("delegations", row["key"], row)
            turn["receipt"] = {"state": "committed", "text": "Rejected."}
            session._store.put("turns", "legacy", turn)
        session._reserve_result(row)
        await session.close()
        session = await Session.open(tmp_path, **settings)
        try:
            assert session._host.missions() == []
            assert session._host.global_budget_status()["reserved_tokens"] == 0
            assert session.tasks()[0]["status"] == "REJECTED"
            assert (await session.send("Hello", key="new"))["state"] == "committed"
        finally:
            await session.close()

    asyncio.run(case())


if __name__ == "__main__":
    asyncio.new_event_loop().run_until_complete(
        result_retry_process(Path(sys.argv[1]), bool(int(sys.argv[2])), len(sys.argv) > 3)
    )

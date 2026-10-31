# SPDX-License-Identifier: Apache-2.0
"""Owner 代次隔离；锁丢失后旧宿主不能再受理工作。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time

import pytest

from agent_orchestrator.host import OrchestrationHost, Principal, Session
from agent_orchestrator.host.instance_lock import InstanceLock
from agent_orchestrator.host.session_ledger import SessionError


def test_owner_generation_persists_and_conflict_does_not_advance(tmp_path):
    first, competing = InstanceLock(tmp_path), InstanceLock(tmp_path)
    assert first.acquire()
    try:
        assert first.owner_generation == 1
        assert not competing.acquire()
        assert competing.owner_generation == first.owner_generation
        assert first.acquire() and first.owner_generation == 1
    finally:
        first.release()
    assert competing.acquire()
    try:
        assert competing.owner_generation == 2
    finally:
        competing.release()
    reopened = InstanceLock(tmp_path)
    assert reopened.acquire()
    try:
        assert reopened.owner_generation == 3
    finally:
        reopened.release()


def test_instance_lock_detects_replaced_inode(tmp_path):
    lock = InstanceLock(tmp_path)
    assert lock.acquire()
    assert lock.held
    try:
        os.replace(tmp_path / ".instance.lock", tmp_path / "detached-lock")
        replacement = InstanceLock(tmp_path)
        assert replacement.acquire()
        try:
            deadline = time.monotonic() + 0.65
            while lock.held and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not lock.held
            assert replacement.owner_generation == 2
        finally:
            replacement.release()
    finally:
        lock.release()


def test_generation_serializes_across_processes(tmp_path):
    script = (
        "import sys; from agent_orchestrator.host.instance_lock import InstanceLock; "
        "lock=InstanceLock(sys.argv[1]); assert lock.acquire(); "
        "print(lock.owner_generation); lock.release()"
    )
    for expected in (1, 2):
        child = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            text=True,
            capture_output=True,
            check=True,
        )
        assert int(child.stdout) == expected


@pytest.mark.parametrize("public_entry", ["host", "session"])
def test_public_open_reports_owner_conflict_with_generation(tmp_path, public_entry):
    async def run():
        lock = InstanceLock(tmp_path)
        assert lock.acquire()
        try:
            principal = Principal("owner-user", "User")
            with pytest.raises(SessionError) as caught:
                if public_entry == "host":
                    await OrchestrationHost.open(tmp_path, principal=principal)
                else:
                    await Session.open(tmp_path, principal=principal)
            assert caught.value.code == "owner_conflict"
            assert caught.value.owner_generation == lock.owner_generation
            assert caught.value.public()["owner_generation"] == lock.owner_generation
        finally:
            lock.release()

    asyncio.run(run())


def test_old_host_refuses_new_work_and_emits_owner_lost(tmp_path):
    host = OrchestrationHost(tmp_path, principal=Principal("owner-user", "User"))
    assert host.lock.acquire()
    host.state = "available"
    events = []
    host._notify = events.append
    host.lock.release()
    newer = InstanceLock(tmp_path)
    assert newer.acquire()
    try:
        with pytest.raises(SessionError) as caught:
            host._refuse_new_work("new work")
        assert caught.value.code == "owner_conflict"
        assert host.state == "owner_lost"
        assert host.status()["owner_generation"] == 1
        assert events[-1]["kind"] == "owner_lost"
        with pytest.raises(SessionError):
            host._refuse_new_work("retry")
        assert len(events) == 1
    finally:
        newer.release()


def test_real_session_lost_owner_refuses_before_committing_input(tmp_path):
    from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider

    calls = []

    def must_not_call(request):
        calls.append(request)
        raise AssertionError("lost owner must not request a model")

    async def run():
        host = await OrchestrationHost.open(
            tmp_path,
            principal=Principal("owner-user", "User"),
            provider=ScriptedProvider(chat=must_not_call),
            counter=FixtureWordCounter(),
            require_acceptance=False,
        )
        newer = InstanceLock(tmp_path)
        try:
            session = await host.open_session("owner-session")
            generation = session.capabilities()["owner_generation"]
            assert generation == host.status()["owner_generation"] == 1
            before = session._store.get("meta", "sequence")
            stream = session.events(after=before)
            next_event = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            host.lock.release()
            assert newer.acquire() and newer.owner_generation == 2
            with pytest.raises(SessionError, match="owner_conflict"):
                await session.send("Do not accept this", key="lost", wait=False)
            assert session._store.rows("turns") == []
            assert session.queued_inputs() == []
            assert session._store.get("meta", "sequence") == before
            event = await asyncio.wait_for(next_event, timeout=1)
            assert event["kind"] == "owner_lost" and event["durable"] is False
            await stream.aclose()
            assert calls == []
        finally:
            await host.close()
            newer.release()

    asyncio.run(run())


def c1_session(tmp_path):
    from test_session import FakeHost

    host = FakeHost(tmp_path)
    return host, Session(host, session_id="main")


def test_c1_archive_refuses_live_delegation_but_close_preserves_it(tmp_path):
    from test_session import SPEC

    async def run():
        host, session = c1_session(tmp_path)
        task = await session._delegations.delegate("turn", "call", SPEC)
        with pytest.raises(SessionError, match="active_work"):
            session.archive(command_id="archive")
        assert not session._closed
        assert session.task(task["delegation_id"])["status"] == "ACTIVE"
        assert host.cancelled == []
        await session.close()
        assert session._closed and host.cancelled == []
        assert next(iter(host.made.values()))["status"] == "ACTIVE"
        reopened = Session(host, session_id="main")
        assert reopened.task(task["delegation_id"])["status"] == "ACTIVE"
        with pytest.raises(SessionError, match="active_work"):
            reopened.archive(command_id="archive")
        await reopened.close()

    asyncio.run(run())


def test_c1_archive_refuses_unconsumed_result_and_close_preserves_it(tmp_path):
    from test_session import SPEC

    async def run():
        host, session = c1_session(tmp_path)
        task = await session._delegations.delegate("turn", "call", SPEC)
        host.finish(task["mission_id"])
        session._inbox.catch_up()
        [notice] = session.inbox()
        session.ack(notice["event_id"])
        with pytest.raises(SessionError, match="active_work"):
            session.archive(command_id="archive")
        assert not session._closed and session.inbox()[0]["applied_turn_id"] is None
        await session.close()
        reopened = Session(host, session_id="main")
        assert reopened.inbox()[0]["applied_turn_id"] is None
        with pytest.raises(SessionError, match="active_work"):
            reopened.archive(command_id="archive")
        reopened.exclude_result(notice["event_id"], "可信宿主不采用此结果", command_id="exclude")
        assert reopened.archive(command_id="archive")["archived"] is True
        await reopened.close()
        assert session._closed and host.cancelled == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "namespace,state",
    [
        ("queued_inputs", "queued"),
        ("queued_inputs", "running"),
        ("continuation_results", "pending"),
        ("continuation_results", "queued"),
    ],
)
def test_c1_archive_refuses_queued_responsibility_and_close_preserves_it(
    tmp_path, namespace, state
):
    async def run():
        host, session = c1_session(tmp_path)
        row = {"input_id": "input", "event_id": "event", "kind": "directive", "state": state}
        with session._store.transaction():
            session._store.put(namespace, "row", row)
        with pytest.raises(SessionError, match="active_work"):
            session.archive(command_id="archive")
        assert not session._closed
        await session.close()
        assert session._store.get(namespace, "row")["state"] == state
        reopened = Session(host, session_id="main")
        assert reopened._store.get(namespace, "row")["state"] == state
        with pytest.raises(SessionError, match="active_work"):
            reopened.archive(command_id="archive")
        await reopened.close()

    asyncio.run(run())


def test_c1_close_leaves_running_input_recoverable(tmp_path):
    from test_session_async import GatedChat, eventually

    async def run():
        host, session = c1_session(tmp_path)
        chat = session._chat = GatedChat(session)
        accepted = await session.send("已受理输入", key="input", wait=False)
        await asyncio.wait_for(chat.entered.wait(), 2)
        with pytest.raises(SessionError, match="active_work"):
            session.archive(command_id="archive")
        await session.close()
        assert session._input_task.done()
        assert session.queued_inputs()[0]["state"] == "running"
        reopened = Session(host, session_id="main")
        reopened._chat = GatedChat(reopened)
        reopened._chat.release.set()
        await reopened.recover()
        await eventually(lambda: reopened.history())
        assert reopened.queued_inputs()[0]["input_id"] == accepted["input_id"]
        assert reopened.queued_inputs()[0]["state"] == "done"
        await reopened.close()

    asyncio.run(run())


def test_c1_archive_refuses_unfinished_goal_and_preserves_data_after_finish(tmp_path):
    async def run():
        host, session = c1_session(tmp_path)
        session.put_goal(
            {
                "goal_id": "g",
                "objective": "待验收目标",
                "completion_criteria": ["由宿主审阅"],
                "budget": {"turns": 2},
            },
            command_id="goal",
            expected_version=0,
        )
        with pytest.raises(SessionError, match="active_work"):
            session.archive(command_id="archive")
        assert not session._store.get("meta", "archived")
        session.finish_goal("g", "failed", command_id="finish", expected_version=1)
        receipt = session.archive(command_id="archive")
        assert receipt["archived"] is True
        assert session.archive(command_id="archive") == receipt
        assert session._store.get("goals", "g")["state"] == "failed"
        assert session._store.path.is_file()
        with pytest.raises(SessionError, match="not_found_or_forbidden"):
            Session(host, session_id="main")
        await session.close()

    asyncio.run(run())


def test_c1_real_host_archive_reopen_is_forbidden(tmp_path):
    from test_host_sessions import running_host

    async def run():
        host = await running_host(tmp_path, ["已完成会话回复"])
        try:
            session = await host.open_session("archivable")
            receipt = await session.send("请简短回复", key="one")
            assert receipt["state"] == "committed"
            archived = session.archive(command_id="archive")
            assert archived["archived"]
            assert session.history()[0]["text"] == "已完成会话回复"
            with pytest.raises(SessionError, match="not_found_or_forbidden"):
                await host.open_session("archivable")
            await session.close()
            with pytest.raises(SessionError, match="not_found_or_forbidden"):
                await host.open_session("archivable")
        finally:
            await host.close()

    asyncio.run(run())


def test_second_process_public_open_reports_current_owner_generation(tmp_path):
    import json

    script = """
import asyncio, json, sys
from agent_orchestrator.host import OrchestrationHost, Principal
from agent_orchestrator.host.session_ledger import SessionError
async def run():
    try:
        await OrchestrationHost.open(sys.argv[1], principal=Principal('owner-user', 'User'))
    except SessionError as error:
        print(json.dumps(error.public()))
    else:
        raise AssertionError('second process acquired owned root')
asyncio.run(run())
"""
    owner = InstanceLock(tmp_path)
    assert owner.acquire()
    try:
        child = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            text=True,
            capture_output=True,
            check=True,
            timeout=15,
        )
        assert json.loads(child.stdout) == {
            "code": "owner_conflict",
            "retryable": False,
            "safe_message": "owner_conflict",
            "owner_generation": owner.owner_generation,
        }
        assert owner.held and owner.owner_generation == 1
    finally:
        owner.release()


def test_instance_lock_caches_probes_for_half_a_second(tmp_path, monkeypatch):
    from agent_orchestrator.host import instance_lock as module

    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    attempts = []
    original = module._try_lock

    def probe(fd):
        attempts.append(fd)
        return original(fd)

    monkeypatch.setattr(module, "_try_lock", probe)
    lock = InstanceLock(tmp_path)
    assert lock.acquire()
    try:
        assert lock.held
        count = len(attempts)
        for _ in range(100):
            assert lock.held
        assert len(attempts) == count
        clock[0] += 0.499
        assert lock.held and len(attempts) == count
        clock[0] += 0.002
        assert lock.held and len(attempts) == count + 1
    finally:
        lock.release()
    assert not lock.held
    assert lock.acquire() and lock.owner_generation == 2
    try:
        assert lock.held and len(attempts) == count + 3
    finally:
        lock.release()


def test_cached_owner_loss_is_detected_within_half_a_second(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    owner = InstanceLock(tmp_path)
    assert owner.acquire() and owner.held
    replacement = InstanceLock(tmp_path)
    try:
        os.replace(tmp_path / ".instance.lock", tmp_path / "detached-lock")
        assert replacement.acquire()
        clock[0] += 0.499
        assert owner.held
        clock[0] += 0.002
        assert not owner.held
        assert replacement.owner_generation == 2
    finally:
        replacement.release()
        owner.release()


def test_real_host_reopen_exposes_second_owner_generation(tmp_path):
    from test_host_sessions import running_host

    async def run():
        first = await running_host(tmp_path, [])
        try:
            session = await first.open_session("generation")
            assert (
                first.status()["owner_generation"]
                == session.capabilities()["owner_generation"]
                == 1
            )
        finally:
            await first.close()
        second = await running_host(tmp_path, [])
        try:
            reopened = await second.open_session("generation")
            assert second.status()["owner_generation"] == 2
            assert reopened.capabilities()["owner_generation"] == 2
        finally:
            await second.close()

    asyncio.run(run())


def test_closed_root_rejects_child_without_recreating_ledger_handle(tmp_path):
    async def run():
        host, root = c1_session(tmp_path)
        child = await root.create_child("worker", command_id="child")
        child_id = child["child_session_id"]
        worker = root.child("worker")
        await root.close()
        assert "main" not in host._session_handles
        assert child_id not in host._session_handles
        with pytest.raises(SessionError, match="session_closed|not_found_or_forbidden"):
            worker._root_session()
        assert "main" not in host._session_handles
        assert child_id not in host._session_handles

    asyncio.run(run())


@pytest.mark.parametrize("state", ["queued", "running"])
def test_root_close_preserves_child_input_when_authorization_returns_during_cleanup(
    tmp_path, state
):
    from test_session_async import FakeChat, eventually

    from agent_orchestrator.host import AuthorizationDecision

    async def run():
        host, root = c1_session(tmp_path)
        await root.create_child("worker", command_id="child")
        worker = root.child("worker")
        worker._chat = FakeChat(worker)
        authorization_entered, authorization_release = asyncio.Event(), asyncio.Event()
        pump_started, pump_cancelling, pump_cleanup_release = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )

        class Port:
            async def decide(self, request):
                authorization_entered.set()
                await authorization_release.wait()
                return AuthorizationDecision.allow()

        async def controlled_pump():
            pump_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pump_cancelling.set()
                await pump_cleanup_release.wait()

        worker._authorization.port = Port()
        root._pump_task = asyncio.create_task(controlled_pump())
        await pump_started.wait()
        accepted = worker._accept_input("重开继续的输入", "queued")
        with worker._store.transaction():
            row = worker._store.get("queued_inputs", accepted["input_id"])
            row["state"] = state
            worker._store.put("queued_inputs", accepted["input_id"], row)
        worker._wake_inputs()
        await asyncio.wait_for(authorization_entered.wait(), 1)
        closing = asyncio.create_task(root.close())
        try:
            await asyncio.wait_for(pump_cancelling.wait(), 1)
            assert root._closed and not worker._closed
            authorization_release.set()
            await asyncio.wait_for(asyncio.shield(worker._input_task), 1)
            queued = worker._store.get("queued_inputs", accepted["input_id"])
            assert queued["state"] == state
            assert queued["receipt"] is None
            assert worker._chat.calls == 0
            assert not any(
                event["kind"] == "error"
                and event.get("payload", {}).get("code") == "session_closed"
                for event in worker._store.events(0)
            )
        finally:
            authorization_release.set()
            pump_cleanup_release.set()
            await asyncio.wait_for(closing, 1)

        reopened = Session(host, session_id="main")
        try:
            await reopened.recover()
            restored = reopened.child("worker")
            restored._chat = FakeChat(restored)
            await restored.recover()
            await eventually(lambda: restored.queued_inputs()[0]["state"] == "done")
            assert restored.queued_inputs()[0]["input_id"] == accepted["input_id"]
            assert restored._chat.calls == 1
            assert len(restored.history()) == 1
        finally:
            await reopened.close()

    asyncio.run(run())


def test_archive_refuses_while_a_direct_turn_is_in_flight(tmp_path):
    """空闲会话 send(wait=True) 走直通路径、不写排队输入；回合在途时归档必须拒绝，
    否则动作交给连接器后会话就被封死，回执无处对账。"""
    from test_chat import _chat_script

    from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider

    async def run():
        session = await Session.open(
            tmp_path / "host",
            Principal("u", "User"),
            provider=ScriptedProvider(chat=_chat_script(["好的。"])),
            counter=FixtureWordCounter(),
            require_acceptance=False,
        )
        gate = asyncio.Event()
        original = session._chat.turn

        async def slow_turn(request, **options):
            await gate.wait()
            return await original(request, **options)

        session._chat.turn = slow_turn
        try:
            sending = asyncio.create_task(session.send("做件小事", key="k1"))
            async with asyncio.timeout(30):
                while session._current_input is None:  # 首次回合要先起执行器，等到真正在途
                    await asyncio.sleep(0.05)
            with pytest.raises(SessionError, match="active_work"):
                session.archive(command_id="archive-early")
            gate.set()
            assert (await sending)["state"] == "committed"
            assert session.archive(command_id="archive-late")["archived"] is True
        finally:
            # 断言失败时也要放开回合，否则关闭会等在途回合，用例挂死而不是变红
            gate.set()
            if not sending.done():
                sending.cancel()
            await session.close()

    asyncio.run(asyncio.wait_for(run(), 120))

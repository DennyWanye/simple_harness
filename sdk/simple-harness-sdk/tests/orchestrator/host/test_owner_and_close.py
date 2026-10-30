# SPDX-License-Identifier: Apache-2.0
"""Owner 代次隔离；锁丢失后旧宿主不能再受理工作。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

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
    try:
        os.replace(tmp_path / ".instance.lock", tmp_path / "detached-lock")
        replacement = InstanceLock(tmp_path)
        assert replacement.acquire()
        try:
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

# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-16: one orchestration directory, one process (plan review P0-2).  The App has no
single-instance guard and the SDK treats live leases under the same owner as its own, so
a second process must stand aside: unavailable, no dispatch, no write.  A SIGKILLed holder
releases the lock (the kernel drops a flock with the process).

Draft written before the implementation (plan 2026-09-11 H2).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from deskpet.orchestration.lock import InstanceLock
from agent_orchestrator.testing.word_counter import FixtureWordCounter
from deskpet.orchestration.service import (
    OrchestrationRequestError,
    OrchestrationService,
    OrchestrationSettings,
)

from ._support import mission_count, notes_provider, notes_request

HOLD = (
    "import sys, time\n"
    "from pathlib import Path\n"
    "from deskpet.orchestration.lock import InstanceLock\n"
    "lock = InstanceLock(Path(sys.argv[1]))\n"
    "assert lock.acquire()\n"
    "Path(sys.argv[2]).write_text('held')\n"
    "time.sleep(3600)\n"
)


@pytest.mark.asyncio
async def test_second_service_on_the_same_directory_is_unavailable(orchestration_root, principal):
    first = OrchestrationService(
        orchestration_root, OrchestrationSettings(), provider=notes_provider(), principal=principal, drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await first.start()
    second = OrchestrationService(
        orchestration_root, OrchestrationSettings(), provider=notes_provider(), principal=principal, drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await second.start()
    try:
        assert first.status()["available"] is True
        status = second.status()
        assert status["available"] is False and "另一个实例" in status["reason"]
        with pytest.raises(OrchestrationRequestError) as refused:
            second.create_mission(notes_request("k-second"))
        assert refused.value.code == "orchestration_unavailable"
        assert mission_count(orchestration_root) == 0
    finally:
        await second.close()
        await first.close()


def test_a_killed_holder_releases_the_lock(tmp_path):
    root = tmp_path / "agent-orchestrator"
    root.mkdir()
    held = tmp_path / "held"
    child = subprocess.Popen(
        [sys.executable, "-c", HOLD, str(root), str(held)],
        cwd=Path(__file__).resolve().parents[2],
    )
    try:
        deadline = time.monotonic() + 30
        while not held.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert held.exists()
        assert InstanceLock(root).acquire() is False
    finally:
        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=10)
    lock = InstanceLock(root)
    assert lock.acquire() is True
    lock.release()

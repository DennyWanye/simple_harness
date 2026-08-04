# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""PermissionGate no longer owns product pending-decision state.

Durable product approvals live in Harness continuations and the
``execution_decisions`` table.  ``list_pending`` remains a compatibility
surface and must never recreate a process-local shadow registry.
"""

from __future__ import annotations

import asyncio

import pytest

from deskpet.permissions.gate import PermissionGate, PermissionGateConfig
from deskpet.types.skill_platform import PermissionResponse


@pytest.mark.asyncio
async def test_in_flight_prompt_does_not_create_process_local_pending_state() -> None:
    gate = PermissionGate(config=PermissionGateConfig(timeout_s=5.0))
    started = asyncio.Event()
    release = asyncio.Event()

    async def responder(req) -> PermissionResponse:
        started.set()
        await release.wait()
        return PermissionResponse(request_id=req.request_id, decision="allow")

    gate.set_responder(responder)
    task = asyncio.create_task(
        gate.check("shell", {"command": "echo hi"}, session_id="s1")
    )
    await asyncio.wait_for(started.wait(), timeout=2.0)

    assert gate.list_pending() == []
    assert gate.list_pending(session_id="s1") == []

    release.set()
    decision = await task
    assert decision.allow is True
    assert gate.list_pending() == []


@pytest.mark.asyncio
async def test_timeout_does_not_leave_process_local_pending_state() -> None:
    gate = PermissionGate(config=PermissionGateConfig(timeout_s=0.02))

    async def slow_responder(req) -> PermissionResponse:
        await asyncio.sleep(1.0)
        return PermissionResponse(request_id=req.request_id, decision="allow")

    gate.set_responder(slow_responder)
    decision = await gate.check("shell", {"command": "sleep"}, session_id="s1")

    assert decision.allow is False
    assert decision.source == "timeout"
    assert gate.list_pending() == []

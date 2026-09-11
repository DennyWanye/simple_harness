# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-8: an App restart continues the Mission (original §16.4 Durable Execution).

Draft written before the implementation (plan 2026-09-11 H2).  A finished Task is never
re-run and an Attempt's usage is imported at most once.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import notes_provider, notes_request


def _rows(root, sql: str) -> list[tuple]:  # type: ignore[no-untyped-def]
    with sqlite3.connect(root / "orchestrator.db") as db:
        return list(db.execute(sql).fetchall())


@pytest.mark.asyncio
async def test_restart_before_any_work_continues_to_completed(orchestration_root, principal):
    first = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
    )
    await first.start()
    created = first.create_mission(notes_request("k-restart-0"))
    await first.close()  # the App quits before the loop ever ran

    second = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
    )
    await second.start()
    try:
        await second.drain()
        assert second.mission_detail(created["mission_id"])["mission"]["status"] == "COMPLETED"
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_restart_mid_mission_does_not_rerun_finished_work(orchestration_root, principal):
    first = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(tick_active_seconds=0.05, tick_idle_seconds=0.2),
        provider=notes_provider(),
        principal=principal,
    )
    await first.start()
    created = first.create_mission(notes_request("k-restart-1"))
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:  # stop as soon as the Attempt exists
        if first.mission_detail(created["mission_id"])["attempts"]:
            break
        await asyncio.sleep(0.02)
    await first.close()

    second = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
    )
    await second.start()
    try:
        await second.drain()
        detail = second.mission_detail(created["mission_id"])
        assert detail["mission"]["status"] == "COMPLETED"
        completed = [t for t in detail["tasks"] if t["status"] == "COMPLETED"]
        assert len(completed) == 1
        # one Task, and at most one Attempt beyond the one in flight at the restart
        assert len(detail["attempts"]) <= 2
        usage_refs = [
            r[0]
            for r in _rows(
                orchestration_root,
                "SELECT usage_ref FROM cost_imports WHERE usage_ref IS NOT NULL",
            )
        ]
        assert len(usage_refs) == len(set(usage_refs))  # each usage imported at most once
    finally:
        await second.close()

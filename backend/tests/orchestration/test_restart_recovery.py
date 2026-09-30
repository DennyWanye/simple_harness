# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-8 / HA-15: an App quit is a SIGKILL (Tauri ``child.kill()``, plan review P0-3), so
recovery may never depend on ``close()``.  The service runs in a child process, the test
kills it with ``kill -9`` and starts a new service (a new owner) on the same directory.

* HA-8  — killed while the Mission waits on a person (every turn committed): the new
  process continues it; finished work is not re-run, each usage is imported once.
* HA-15 — killed in the middle of a model call: the turn's outcome is unknown; the new
  process shows that honestly (never "running normally" forever) and a takeover moves on.

Draft written before the implementation (plan 2026-09-11 H2).
"""

from __future__ import annotations

import asyncio
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_orchestrator.testing.fixtures import RoleScriptedProvider, critic_step
from deskpet.orchestration.service import (
    OrchestrationRequestError,
    OrchestrationService,
    OrchestrationSettings,
)

from ._support import (
    NOTES_TASK,  # noqa: F401 - documents the scripted Task shape
    SCRIPTED_LANE,
    _notes_worker,
    pending_approval_kinds,
)

BACKEND = Path(__file__).resolve().parents[2]


def _spawn(root: Path, scenario: str, marker: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "tests.orchestration._child_service", str(root), scenario, str(marker)],
        cwd=BACKEND,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _wait(predicate, seconds: float, what: str) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _kill9(child: subprocess.Popen) -> None:
    os.kill(child.pid, signal.SIGKILL)
    child.wait(timeout=10)
    assert child.returncode == -signal.SIGKILL


def _usage_refs(root: Path) -> list[str]:
    with sqlite3.connect(f"file:{root / 'orchestrator.db'}?mode=ro", uri=True) as db:
        try:
            rows = db.execute("SELECT usage_ref FROM cost_imports").fetchall()
        except sqlite3.OperationalError:
            return []
    return [str(r[0]) for r in rows if r[0] is not None]


def _restarted(root: Path, provider: RoleScriptedProvider, principal) -> OrchestrationService:  # type: ignore[no-untyped-def]
    return OrchestrationService(
        root,
        OrchestrationSettings(lease_seconds=2.0),
        provider=provider,
        principal=principal,
        drive=False,
        test_scenario=SCRIPTED_LANE,  # 与子进程同一夹具通道（见 _support）
    )


@pytest.mark.asyncio
async def test_killed_while_waiting_on_a_person_continues_without_rerunning(
    orchestration_root, principal, tmp_path
):
    marker = tmp_path / "review.marker"
    child = _spawn(orchestration_root, "review", marker)
    try:
        _wait(lambda: pending_approval_kinds(orchestration_root) == ["review"], 60, "a review request")
    finally:
        _kill9(child)

    service = _restarted(
        orchestration_root,
        RoleScriptedProvider({"critic": [critic_step(verdict="PASS", criteria_met=True)] * 3}),
        principal,
    )
    await service.start()
    try:
        assert service.owner.startswith("deskpet-orchestrator-")
        assert service.owner != (tmp_path / "review.marker.started").read_text(encoding="utf-8")
        mission_id = service.list_missions()[0]["id"]
        request = service.approvals(mission_id)[0]
        service.decide(request["request_id"], "review_pass", note="看过了")
        # the dead owner's lease must lapse first (original §17.6); then the new owner
        # re-verifies the *same* Attempt — nothing already submitted is re-run (P3.1-A07)
        deadline = time.monotonic() + 60
        detail = service.mission_detail(mission_id)
        while detail["mission"]["status"] != "COMPLETED" and time.monotonic() < deadline:
            await service.drain(timeout=5)
            await asyncio.sleep(0.5)
            detail = service.mission_detail(mission_id)
        assert detail["mission"]["status"] == "COMPLETED"
        assert len(detail["attempts"]) == 1  # the committed work was not re-run
        refs = _usage_refs(orchestration_root)
        assert len(refs) == len(set(refs))  # each usage imported at most once
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_killed_inside_a_model_call_is_shown_and_can_be_taken_over(
    orchestration_root, principal, tmp_path
):
    marker = tmp_path / "blocking.marker"
    child = _spawn(orchestration_root, "blocking", marker)
    try:
        _wait(marker.exists, 60, "the Worker's model call")
    finally:
        _kill9(child)

    service = _restarted(
        orchestration_root,
        RoleScriptedProvider(
            {
                "worker": _notes_worker() + _notes_worker(),
                "critic": [critic_step(verdict="PASS", criteria_met=True)] * 4,
            }
        ),
        principal,
    )
    await service.start()
    try:
        mission_id = service.list_missions()[0]["id"]
        # measured 2026-09-12: with a 2 s lease the SDK heartbeat reports the unknown turn as
        # blocked about 6 s after the new owner starts (journal §4); poll with a wide margin
        deadline = time.monotonic() + 45
        detail = service.mission_detail(mission_id)
        while (
            not detail["blocked"]
            and detail["mission"]["status"] != "COMPLETED"
            and time.monotonic() < deadline
        ):
            await service.drain(timeout=3)  # returns on idle *or* timeout (a blocked turn never idles)
            detail = service.mission_detail(mission_id)
        # unconditional (review P1-4): the killed turn never finishes by itself (diagnosed
        # 2026-09-12: still ACTIVE after 240 s), so the unknown outcome must be shown and a
        # takeover must move the Mission on
        blocked = detail["blocked"]
        assert blocked, f"an unknown turn must be shown, not left as silently running: {detail['mission']}"
        assert blocked[0]["reason"] == "turn_outcome_unknown"
        assert detail["mission"]["ui_state"] == "unknown"
        task_id = blocked[0]["task_id"]
        with pytest.raises(OrchestrationRequestError) as refused:  # a takeover needs a basis
            service.takeover(task_id, "retry_with_note", basis="  ")
        assert refused.value.code == "invalid_request"
        service.takeover(task_id, "retry_with_note", basis="App 被关闭，模型调用结果未知")
        deadline = time.monotonic() + 60
        detail = service.mission_detail(mission_id)
        while detail["mission"]["status"] != "COMPLETED" and time.monotonic() < deadline:
            await service.drain(timeout=5)
            detail = service.mission_detail(mission_id)
        assert detail["mission"]["status"] == "COMPLETED"
        assert detail["blocked"] == []
    finally:
        await service.close()

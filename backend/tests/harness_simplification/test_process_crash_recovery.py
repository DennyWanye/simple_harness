"""Process-level crash recovery for the production Harness ledger.

The ordinary fault matrix raises inside one Python process.  These cases kill
an independent interpreter at the exact UoW hook, so no context manager,
finally block, or aiosqlite worker can perform a graceful rollback for us.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import signal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import aiosqlite
import pytest

from deskpet.execution.contracts import (
    DecisionConflict,
    OutcomeStatus,
    RunEventCandidate,
    RunStatus,
)
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork
from tests.harness_simplification import test_fault_matrix as matrix


CRASH_EXIT_CODE = 86
DISPATCH_ACK_HASH = hashlib.sha256(b"hard-crash-dispatch-started").hexdigest()


def _settle_kwargs(*, expected_effect_version: int) -> dict[str, object]:
    return {
        "expected_effect_version": expected_effect_version,
        "attempt_no": 1,
        "worker_owner": "effect-worker",
        "worker_epoch": 1,
        "status": "succeeded",
        "outcome": {"ok": True, "external_write_count": 1},
        "receipt_ref": "receipt:hard-crash-effect-1",
        "artifact_refs": ("artifact:hard-crash-report",),
        "node_execution_id": "node-hard-crash",
        "checkpoint_ns": "graph",
        "checkpoint_id": "checkpoint-hard-crash",
        "expected_continuation_version": 1,
        "continuation_payload": {"state": "effect-settled-after-restart"},
        "event": RunEventCandidate(
            event_key="hard-crash-effect-settled",
            kind="tool.result",
            status=OutcomeStatus.SUCCEEDED,
            driver_kind="react",
        ),
        "deliveries": (matrix._delivery(),),
    }


def _hard_kill(point: str, *, hook: str, sentinel: Path) -> None:
    if point != hook:
        return
    sentinel.write_text(point, encoding="utf-8")
    if os.name == "posix":
        os.kill(os.getpid(), signal.SIGKILL)
    os._exit(CRASH_EXIT_CODE)  # pragma: no cover - Windows hard-exit fallback


async def _worker(
    scenario: str,
    db_path: Path,
    hook: str,
    sentinel: Path,
    external_marker: Path | None,
) -> None:
    uow = SqliteExecutionUnitOfWork(
        db_path,
        clock=lambda: 100.0,
        fault_injector=lambda point: _hard_kill(
            point,
            hook=hook,
            sentinel=sentinel,
        ),
    )
    if scenario == "permission":
        request = matrix._decision("decision-boundary", permission=True)
        await uow.commit_decision(
            matrix._signal(request),
            matrix._actor(),
            expected_continuation_version=1,
            continuation_payload={"state": "resumed"},
            resumed_event=RunEventCandidate(
                event_key="hard-crash-decision-resumed",
                kind="run.resumed",
                status=OutcomeStatus.ACCEPTED,
                driver_kind="react",
            ),
            deliveries=(matrix._delivery(),),
        )
        return
    if scenario == "effect_claim":
        request = matrix._decision("grant", permission=True)
        await uow.claim_tool_call(
            matrix._grant(request),
            matrix._actor(request.run_id),
            **matrix._effect_claim_kwargs(),
        )
        return
    if scenario == "effect_settle":
        if external_marker is None:
            raise ValueError("effect settlement worker requires an external marker")
        started = await uow.mark_effect_dispatch_started(
            "effect-1",
            1,
            0,
            "dispatch:hard-crash-effect-1",
            DISPATCH_ACK_HASH,
        )
        # This is the irreversible external effect.  Exclusive creation makes
        # a duplicate physical execution fail rather than hiding it.
        with external_marker.open("x", encoding="utf-8") as stream:
            stream.write("external-write:effect-1\n")
            stream.flush()
            os.fsync(stream.fileno())
        await uow.settle_effect(
            "effect-1",
            **_settle_kwargs(expected_effect_version=started.effect_version),
        )
        return
    if scenario == "child_terminal":
        await uow.finalize_child_and_enqueue_parent_signal(
            "operation:child",
            expected_version=1,
            terminal_status=RunStatus.COMPLETED,
            event=matrix._terminal_event("child"),
            value={"result": "ok"},
        )
        return
    if scenario == "root_terminal":
        await uow.commit_run_outcome(
            "final",
            expected_version=0,
            terminal_status=RunStatus.COMPLETED,
            event=matrix._terminal_event("final"),
            deliveries=(matrix._goal_delivery(),),
        )
        return
    raise ValueError(f"unknown process crash scenario: {scenario}")


async def _run_crashing_worker(
    *,
    scenario: str,
    path: Path,
    hook: str,
    external_marker: Path | None = None,
) -> Path:
    sentinel = path.with_name(f"{scenario}-{hook}.reached")
    command = (
        sys.executable,
        "-m",
        "tests.harness_simplification.test_process_crash_recovery",
        "--worker",
        scenario,
        "--db",
        str(path),
        "--hook",
        hook,
        "--sentinel",
        str(sentinel),
    )
    if external_marker is not None:
        command = (*command, "--external-marker", str(external_marker))
    environment = os.environ.copy()
    repo = Path(__file__).resolve().parents[3]
    environment["PYTHONPATH"] = os.pathsep.join(
        item
        for item in (
            str(repo / "backend"),
            str(repo),
            environment.get("PYTHONPATH", ""),
        )
        if item
    )
    completed = await asyncio.to_thread(
        subprocess.run,
        command,
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    expected_code = -signal.SIGKILL if os.name == "posix" else CRASH_EXIT_CODE
    assert completed.returncode == expected_code, completed.stdout + completed.stderr
    assert sentinel.read_text(encoding="utf-8") == hook
    return sentinel


async def _assert_integrity(path: Path) -> None:
    async with aiosqlite.connect(path) as db:
        row = await (await db.execute("PRAGMA integrity_check")).fetchone()
    assert row == ("ok",)


@pytest.mark.asyncio
async def test_permission_resolution_hard_crash_reopens_once(tmp_path: Path) -> None:
    path = tmp_path / "permission.db"
    healthy = await matrix._store(path)
    await healthy.create(matrix._spec("decision-boundary"))
    request = matrix._decision("decision-boundary", permission=True)
    await healthy.persist_react_boundary(
        "decision-boundary",
        0,
        {"state": "waiting"},
        request,
    )

    await _run_crashing_worker(
        scenario="permission",
        path=path,
        hook="decision_resolve_before_commit",
    )

    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    result = await restarted.commit_decision(
        matrix._signal(request),
        matrix._actor(),
        expected_continuation_version=1,
        continuation_payload={"state": "resumed"},
        resumed_event=RunEventCandidate(
            event_key="hard-crash-decision-resumed",
            kind="run.resumed",
            status=OutcomeStatus.ACCEPTED,
            driver_kind="react",
        ),
        deliveries=(matrix._delivery(),),
    )
    with pytest.raises(DecisionConflict, match="duplicate or late"):
        await restarted.commit_decision(
            replace(matrix._signal(request), expected_version=1),
            matrix._actor(),
            expected_continuation_version=1,
            continuation_payload={"state": "resumed"},
            resumed_event=RunEventCandidate(
                event_key="hard-crash-decision-resumed",
                kind="run.resumed",
                status=OutcomeStatus.ACCEPTED,
                driver_kind="react",
            ),
            deliveries=(matrix._delivery(),),
        )

    assert result[0].status.value == "allowed"
    counts = await matrix._counts(path)
    assert counts["decision"] == counts["event"] == counts["delivery"] == 1
    await _assert_integrity(path)


@pytest.mark.asyncio
async def test_effect_claim_hard_crash_does_not_consume_grant(tmp_path: Path) -> None:
    path = tmp_path / "effect-claim.db"
    _, request = await matrix._permission_ready(path)

    await _run_crashing_worker(
        scenario="effect_claim",
        path=path,
        hook="effect_claim_before_commit",
    )

    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    claim = await restarted.claim_tool_call(
        matrix._grant(request),
        matrix._actor(request.run_id),
        **matrix._effect_claim_kwargs(),
    )
    replay = await restarted.claim_tool_call(
        matrix._grant(request),
        matrix._actor(request.run_id),
        **matrix._effect_claim_kwargs(),
    )
    assert (claim.action, replay.action) == ("execute", "in_flight")
    counts = await matrix._counts(path)
    assert counts["effect"] == counts["attempt"] == 1
    await _assert_integrity(path)


@pytest.mark.asyncio
async def test_started_external_effect_hard_crash_is_not_reexecuted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "effect-settle.db"
    external_marker = tmp_path / "external-write.txt"
    healthy, request = await matrix._permission_ready(path)
    await healthy.claim_tool_call(
        matrix._grant(request),
        matrix._actor(request.run_id),
        **matrix._effect_claim_kwargs(),
    )
    await healthy.persist_react_boundary(
        "grant",
        0,
        {"state": "running-effect"},
    )

    await _run_crashing_worker(
        scenario="effect_settle",
        path=path,
        hook="effect_settle_before_commit",
        external_marker=external_marker,
    )

    assert external_marker.read_text(encoding="utf-8") == (
        "external-write:effect-1\n"
    )
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    handoff = await restarted.read_effect_handoff("effect-1")
    assert handoff is not None
    assert (handoff.status, handoff.handoff_state, handoff.effect_version) == (
        "running",
        "started",
        1,
    )
    replay_claim = await restarted.claim_tool_call(
        matrix._grant(request),
        matrix._actor(request.run_id),
        **matrix._effect_claim_kwargs(),
    )
    assert replay_claim.action != "execute"

    settled = await restarted.settle_effect(
        "effect-1",
        **_settle_kwargs(expected_effect_version=handoff.effect_version),
    )
    replay = await restarted.settle_effect(
        "effect-1",
        **_settle_kwargs(expected_effect_version=handoff.effect_version),
    )
    assert settled == replay
    assert external_marker.read_text(encoding="utf-8").count("external-write") == 1
    counts = await matrix._counts(path)
    assert counts["effect"] == counts["attempt"] == 1
    assert counts["event"] == counts["delivery"] == counts["link"] == 1
    await _assert_integrity(path)


@pytest.mark.asyncio
async def test_child_terminal_hard_crash_enqueues_one_terminal_signal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "child-terminal.db"
    _, intent = await matrix._scheduled_child(path)

    await _run_crashing_worker(
        scenario="child_terminal",
        path=path,
        hook="child_finalize_after_parent_signal",
    )

    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    result = await restarted.finalize_child_and_enqueue_parent_signal(
        intent.operation_id,
        expected_version=1,
        terminal_status=RunStatus.COMPLETED,
        event=matrix._terminal_event("child"),
        value={"result": "ok"},
    )
    replay = await restarted.finalize_child_and_enqueue_parent_signal(
        intent.operation_id,
        expected_version=1,
        terminal_status=RunStatus.COMPLETED,
        event=matrix._terminal_event("child"),
        value={"result": "ok"},
    )
    signals = await restarted.list_pending_child_signals("parent", limit=10)
    assert result.idempotent is False and replay.idempotent is True
    assert [item.kind for item in signals].count("terminal") == 1
    assert len({item.signal_id for item in signals}) == len(signals)
    await _assert_integrity(path)


@pytest.mark.asyncio
async def test_root_terminal_hard_crash_delivers_once(tmp_path: Path) -> None:
    path = tmp_path / "root-terminal.db"
    healthy = await matrix._store(path)
    await healthy.create(matrix._spec("final"))

    await _run_crashing_worker(
        scenario="root_terminal",
        path=path,
        hook="finalize_before_commit",
    )

    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    result = await restarted.commit_run_outcome(
        "final",
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=matrix._terminal_event("final"),
        deliveries=(matrix._goal_delivery(),),
    )
    replay = await restarted.commit_run_outcome(
        "final",
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=matrix._terminal_event("final"),
        deliveries=(matrix._goal_delivery(),),
    )
    assert result.idempotent is False and replay.idempotent is True
    counts = await matrix._counts(path)
    assert counts["event"] == counts["delivery"] == 1
    await _assert_integrity(path)


def _parse_worker_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--hook", required=True)
    parser.add_argument("--sentinel", type=Path, required=True)
    parser.add_argument("--external-marker", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _parse_worker_args()
    asyncio.run(
        _worker(
            arguments.worker,
            arguments.db,
            arguments.hook,
            arguments.sentinel,
            arguments.external_marker,
        )
    )

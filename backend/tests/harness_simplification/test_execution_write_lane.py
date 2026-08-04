from __future__ import annotations

import asyncio
import os
import sqlite3

import pytest

from deskpet.workflows.store.write_lane import (
    ExecutionCommittedAfterCancel,
    ExecutionWriteCancelled,
    ExecutionWriteLane,
    ExecutionWriteLanePoisoned,
    ExecutionWriteLaneState,
    ExecutionWriteOutcomeUnknown,
    get_execution_write_lane,
)


@pytest.mark.asyncio
async def test_process_path_lane_is_singleton_and_uses_full_durability(tmp_path):
    path = tmp_path / "execution.db"
    first = get_execution_write_lane(path)
    second = get_execution_write_lane(path)
    assert first is second

    await first.open()
    async with first.transaction() as db:
        synchronous = await (await db.execute("PRAGMA synchronous")).fetchone()
        journal_mode = await (await db.execute("PRAGMA journal_mode")).fetchone()
        assert int(synchronous[0]) == 2
        assert str(journal_mode[0]).lower() == "wal"

    await first.close()
    assert first.state is ExecutionWriteLaneState.CLOSED


@pytest.mark.asyncio
async def test_lane_serializes_transactions_without_merging_boundaries(tmp_path):
    lane = ExecutionWriteLane(tmp_path / "serialized.db")
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    order: list[str] = []

    async def first_writer() -> None:
        async with lane.transaction() as db:
            await db.execute("CREATE TABLE IF NOT EXISTS writes(value TEXT)")
            await db.execute("INSERT INTO writes VALUES('first')")
            order.append("first-entered")
            first_entered.set()
            await release_first.wait()
            order.append("first-leaving")

    async def second_writer() -> None:
        await first_entered.wait()
        async with lane.transaction() as db:
            order.append("second-entered")
            await db.execute("INSERT INTO writes VALUES('second')")

    first_task = asyncio.create_task(first_writer())
    second_task = asyncio.create_task(second_writer())
    await first_entered.wait()
    await asyncio.sleep(0)
    assert order == ["first-entered"]
    release_first.set()
    await asyncio.gather(first_task, second_task)

    assert order == ["first-entered", "first-leaving", "second-entered"]
    with sqlite3.connect(lane.path) as db:
        assert db.execute("SELECT value FROM writes ORDER BY rowid").fetchall() == [
            ("first",),
            ("second",),
        ]
    await lane.close()


@pytest.mark.asyncio
async def test_cancellation_before_commit_rolls_back_and_is_typed(tmp_path):
    lane = ExecutionWriteLane(tmp_path / "cancel-before.db")
    inserted = asyncio.Event()
    hold = asyncio.Event()

    async def writer() -> None:
        async with lane.transaction() as db:
            await db.execute("CREATE TABLE writes(value TEXT)")
            await db.execute("INSERT INTO writes VALUES('must-rollback')")
            inserted.set()
            await hold.wait()

    task = asyncio.create_task(writer())
    await inserted.wait()
    task.cancel()
    with pytest.raises(ExecutionWriteCancelled) as cancelled:
        await task
    assert cancelled.value.code == "write_cancelled_before_commit"
    assert lane.state is ExecutionWriteLaneState.ROLLED_BACK

    with sqlite3.connect(lane.path) as db:
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE name='writes'"
        ).fetchone() is None
    await lane.close()


@pytest.mark.asyncio
async def test_cancellation_during_begin_rolls_back_before_next_writer(
    tmp_path,
):
    begin_started = asyncio.Event()
    finish_begin = asyncio.Event()
    block_once = True

    async def fault(point: str) -> None:
        nonlocal block_once
        if point == "begin_started" and block_once:
            block_once = False
            begin_started.set()
            await finish_begin.wait()

    lane = ExecutionWriteLane(
        tmp_path / "cancel-begin.db",
        commit_deadline_seconds=1.0,
        fault_injector=fault,
    )

    async def cancelled_writer() -> None:
        async with lane.transaction() as db:
            await db.execute("CREATE TABLE unreachable(value TEXT)")

    task = asyncio.create_task(cancelled_writer())
    await begin_started.wait()
    task.cancel()
    finish_begin.set()
    with pytest.raises(ExecutionWriteCancelled) as cancelled:
        await task
    assert cancelled.value.code == "write_cancelled_before_commit"
    assert lane.state is ExecutionWriteLaneState.ROLLED_BACK

    async with lane.transaction() as db:
        await db.execute("CREATE TABLE writes(value TEXT)")
        await db.execute("INSERT INTO writes VALUES('next-writer')")
    with sqlite3.connect(lane.path) as db:
        assert db.execute("SELECT value FROM writes").fetchone() == (
            "next-writer",
        )
    await lane.close()


@pytest.mark.asyncio
async def test_cancellation_after_commit_started_reports_committed(tmp_path):
    commit_started = asyncio.Event()
    finish_commit = asyncio.Event()

    async def fault(point: str) -> None:
        if point == "commit_started":
            commit_started.set()
            await finish_commit.wait()

    lane = ExecutionWriteLane(
        tmp_path / "cancel-during.db",
        commit_deadline_seconds=1.0,
        fault_injector=fault,
    )

    async def writer() -> None:
        async with lane.transaction(result_ref="result:one") as db:
            await db.execute("CREATE TABLE writes(value TEXT)")
            await db.execute("INSERT INTO writes VALUES('committed')")

    task = asyncio.create_task(writer())
    await commit_started.wait()
    task.cancel()
    finish_commit.set()
    with pytest.raises(ExecutionCommittedAfterCancel) as committed:
        await task
    assert committed.value.code == "committed_after_cancel"
    assert committed.value.result_ref == "result:one"
    assert lane.state is ExecutionWriteLaneState.COMMITTED

    with sqlite3.connect(lane.path) as db:
        assert db.execute("SELECT value FROM writes").fetchone() == ("committed",)
    await lane.close()


@pytest.mark.asyncio
async def test_commit_deadline_poison_is_unknown_and_never_replayed(tmp_path):
    commit_returned = asyncio.Event()
    release_return = asyncio.Event()

    async def fault(point: str) -> None:
        if point == "commit_returned":
            commit_returned.set()
            await release_return.wait()

    lane = ExecutionWriteLane(
        tmp_path / "unknown.db",
        commit_deadline_seconds=0.05,
        fault_injector=fault,
    )

    async def writer() -> None:
        async with lane.transaction() as db:
            await db.execute("CREATE TABLE writes(value TEXT)")
            await db.execute("INSERT INTO writes VALUES('maybe')")

    task = asyncio.create_task(writer())
    await commit_returned.wait()
    with pytest.raises(ExecutionWriteOutcomeUnknown) as unknown:
        await task
    assert unknown.value.code == "write_outcome_unknown"
    assert lane.poisoned
    with pytest.raises(ExecutionWriteLanePoisoned):
        async with lane.transaction():
            pass

    release_return.set()
    await asyncio.sleep(0)
    with sqlite3.connect(lane.path) as db:
        assert db.execute("SELECT value FROM writes").fetchone() == ("maybe",)


@pytest.mark.asyncio
async def test_known_commit_failure_rolls_back_and_lane_can_continue(tmp_path):
    failed_once = False

    async def fault(point: str) -> None:
        nonlocal failed_once
        if point == "commit_started" and not failed_once:
            failed_once = True
            raise RuntimeError("known commit failure")

    lane = ExecutionWriteLane(
        tmp_path / "known-failure.db",
        fault_injector=fault,
    )
    with pytest.raises(RuntimeError, match="known commit failure"):
        async with lane.transaction() as db:
            await db.execute("CREATE TABLE writes(value TEXT)")
            await db.execute("INSERT INTO writes VALUES('rolled-back')")
    assert lane.state is ExecutionWriteLaneState.ROLLED_BACK

    async with lane.transaction() as db:
        await db.execute("CREATE TABLE writes(value TEXT)")
        await db.execute("INSERT INTO writes VALUES('recovered')")
    with sqlite3.connect(lane.path) as db:
        assert db.execute("SELECT value FROM writes").fetchall() == [("recovered",)]
    await lane.close()


@pytest.mark.asyncio
async def test_close_is_bounded_and_forked_process_cannot_reuse_lane(
    tmp_path, monkeypatch
):
    lane = ExecutionWriteLane(tmp_path / "fork-guard.db")
    await lane.open()

    with monkeypatch.context() as process:
        process.setattr(os, "getpid", lambda: lane._pid + 1)
        with pytest.raises(ExecutionWriteLanePoisoned):
            await lane.open()

    await asyncio.wait_for(lane.close(timeout=0.5), timeout=0.75)
    assert lane.state is ExecutionWriteLaneState.CLOSED

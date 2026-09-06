"""Real installed SDK decisions/terminal + Host leases; deterministic provider, no native.

Short real leases replace an eight-minute human wait. No synthetic terminal or
permission receipt is written: the existing production fixture creates them.
"""
import asyncio
import sqlite3
import time
from dataclasses import replace

import pytest

from deskpet.execution.foreground_queue import ControlKind, ForegroundQueueError, ForegroundQueueStore
from tests.execution import test_primary_decisions as decisions
from tests.execution.test_primary_foreground_runtime import assert_exact_sdk_terminal_identity

TTL = 1.2


async def setup(tmp_path, monkeypatch, *, wake=True):
    build = decisions.build
    async def short_lease(*args, **kwargs):
        runtime, stack, queue = await build(*args, **kwargs)
        runtime._lease_seconds = TTL
        return runtime, stack, queue
    monkeypatch.setattr(decisions, "build", short_lease)
    return await decisions.setup(tmp_path, wake=wake)


def rows(s, sql):
    with sqlite3.connect(s["state"]) as db:
        return db.execute(sql).fetchall()


async def close(s):
    await s["runtime"].close(timeout=0.1)
    assert s["runtime"]._lease_task is None
    assert s["runtime"]._driver is None or s["runtime"]._driver.done()
    await s["stack"].close()
    s["product"].connection.close()


async def stop(s):
    current = await s["queue"].current_snapshot(s["auth"].subject)
    receipt = await s["queue"].request_control(
        host_run_id=current.host_run_id, subject=s["auth"].subject,
        generation=current.generation, control_kind=ControlKind.STOP,
        reason="Explicit test stop", idempotency_key="lease-test-stop",
    )
    await s["runtime"].after_control(subject=s["auth"].subject)
    await asyncio.wait_for(s["runtime"].drain(), 10)
    return receipt


@pytest.mark.asyncio
async def test_multiple_real_waiting_windows_renew_unique_receipts_and_allow(tmp_path, monkeypatch):
    s = await setup(tmp_path, monkeypatch)
    try:
        seen = set()
        initial_expiry = s["current"].lease_expires_at
        for _ in range(8):
            pending = await decisions.pending(s)
            if not pending:
                break
            if len(seen) < 2:
                old = await s["queue"].current_snapshot(s["auth"].subject)
                # Actually cross the previously-issued lease's expiry, twice.
                await asyncio.sleep(max(TTL * 1.1, old.lease_expires_at - time.time() + 0.1))
                fresh = await s["queue"].current_snapshot(s["auth"].subject)
                assert time.time() > old.lease_expires_at
                assert fresh.lease_expires_at > time.time() > initial_expiry
                assert fresh.generation == s["current"].generation
            for item in pending:
                assert item["decision_id"] not in seen
                seen.add(item["decision_id"])
                result = await s["request"]("primary.decisions.respond", decisions.reply(s, item))
                assert result["payload"]["ok"]
            await s["runtime"]._ingress.wait_idle(s["current"].sdk_run_id)
            await asyncio.wait_for(s["runtime"].drain(), 10)
            if await s["queue"].current_snapshot(s["auth"].subject) is None:
                break
        assert len(seen) >= 2
        assert s["runtime"].last_error is None
        assert await s["queue"].current_snapshot(s["auth"].subject) is None
        assert len(s["provider"].requests) == 7
        files = list(s["configured"].glob("task-*/fresh.txt"))
        assert len(files) == 1 and files[0].read_text() == "created in exact task root"
        heartbeats = rows(s, "SELECT idempotency_key,expires_at FROM foreground_lease_receipts WHERE action='heartbeat' ORDER BY recorded_at")
        assert len(heartbeats) >= 3 and len({x[0] for x in heartbeats}) == len(heartbeats)
        assert all(a[1] < b[1] for a, b in zip(heartbeats, heartbeats[1:]))
        assert rows(s, "SELECT COUNT(*) FROM foreground_runs") == [(1,)]
        await assert_exact_sdk_terminal_identity(s["state"], s["stack"], s["target"]["primary_ref"])
    finally:
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["live_waiting", "expired_waiting", "expired_failed"])
async def test_stop_converges_exact_original_sdk_terminal_after_lease_recovery(tmp_path, monkeypatch, mode):
    s = await setup(tmp_path, monkeypatch, wake=False)
    try:
        old_item = (await decisions.pending(s))[0]
        old_generation = s["current"].generation
        if mode != "live_waiting":
            # Simulate this owner's lost process helper; no direct SQL edits.
            await s["runtime"]._stop_lease_keeper()
            if mode == "expired_failed":
                async def fail_provider(request, *, cancel):
                    raise RuntimeError("controlled provider failure after allowed route")
                monkeypatch.setattr(s["provider"], "invoke", fail_provider)
                response = await s["request"]("primary.decisions.respond", decisions.reply(s, old_item))
                assert response["payload"]["ok"]
                # Authorization schedules the actual SDK continuation on the
                # next loop turn; wait_idle before that handoff sees no driver.
                await asyncio.sleep(0)
                await s["runtime"]._ingress.wait_idle(s["current"].sdk_run_id)
                assert s["runtime"]._ingress.query(s["current"].sdk_run_id).state.value == "failed"
            current = await s["queue"].current_snapshot(s["auth"].subject)
            await asyncio.sleep(max(0, current.lease_expires_at - time.time()) + 0.1)
        async def cannot_reprepare(**_):
            raise AssertionError("recovery must not reconstruct consumed history")
        monkeypatch.setattr(s["runtime"]._context, "prepare", cannot_reprepare)
        before_calls = len(s["provider"].requests)
        await stop(s)
        assert s["runtime"].last_error is None
        assert await s["queue"].current_snapshot(s["auth"].subject) is None
        assert len(s["provider"].requests) == before_calls
        expected = "FAILED" if mode == "expired_failed" else "STOPPED"
        terminal = rows(s, "SELECT terminal_state,generation FROM foreground_terminal_receipts")
        assert terminal == [(expected, old_generation + (mode != "live_waiting"))]
        assert rows(s, "SELECT COUNT(*) FROM foreground_runs") == [(1,)]
        await assert_exact_sdk_terminal_identity(s["state"], s["stack"], s["target"]["primary_ref"])
        # Old permission target must not migrate to the reclaimed generation.
        rejected = await s["request"]("primary.decisions.respond", decisions.reply(s, old_item))
        assert rejected["payload"]["ok"] is False
        assert len(s["provider"].requests) == before_calls
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_delayed_old_heartbeat_cannot_renew_after_other_owner_reclaim(tmp_path, monkeypatch):
    s = await setup(tmp_path, monkeypatch, wake=False)
    entered, release = asyncio.Event(), asyncio.Event()
    original = s["queue"].heartbeat
    async def delayed(**kwargs):
        entered.set()
        await release.wait()
        return await original(**kwargs)
    monkeypatch.setattr(s["queue"], "heartbeat", delayed)
    try:
        item = (await decisions.pending(s))[0]
        await asyncio.wait_for(entered.wait(), 3)
        old = await s["queue"].current_snapshot(s["auth"].subject)
        await asyncio.sleep(max(0, old.lease_expires_at - time.time()) + 0.1)
        second = ForegroundQueueStore(s["state"])
        reclaimed = await second.reclaim_expired(host_run_id=old.host_run_id, new_owner_id="other-owner",
            expected_generation=old.generation, lease_seconds=10, idempotency_key="other-owner-reclaim")
        release.set()
        with pytest.raises(ForegroundQueueError, match="foreground_generation_stale"):
            await asyncio.wait_for(asyncio.shield(s["runtime"]._lease_task), 3)
        current = await second.current_snapshot(s["auth"].subject)
        assert current.owner_id == "other-owner" and current.generation == reclaimed.generation
        assert current.lease_expires_at == reclaimed.expires_at
        assert rows(s, "SELECT COUNT(*) FROM foreground_terminal_receipts") == [(0,)]
        rejected = await s["request"]("primary.decisions.respond", decisions.reply(s, item))
        assert rejected["payload"]["ok"] is False
        assert len(s["provider"].requests) == 1
    finally:
        release.set()
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unavailable", "foreign_identity"])
async def test_unknown_or_foreign_sdk_query_never_restarts_or_forges_terminal(tmp_path, monkeypatch, failure):
    s = await setup(tmp_path, monkeypatch, wake=False)
    try:
        await s["runtime"]._stop_lease_keeper()
        original = s["runtime"]._ingress.query
        def query(run_id):
            if failure == "unavailable":
                raise TimeoutError("controlled query unavailable")
            return replace(original(run_id), run_id="other-sdk-run")
        monkeypatch.setattr(s["runtime"]._ingress, "query", query)
        await s["runtime"].after_control(subject=s["auth"].subject)
        await asyncio.wait_for(s["runtime"].drain(), 5)
        assert s["runtime"].last_error is not None
        assert len(s["provider"].requests) == 1
        assert rows(s, "SELECT COUNT(*) FROM foreground_runs") == [(1,)]
        assert rows(s, "SELECT COUNT(*) FROM foreground_terminal_receipts") == [(0,)]
    finally:
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["work_error", "work_cancel", "success_then_read_error"])
async def test_keeper_cleanup_survives_snapshot_failure(tmp_path, monkeypatch, outcome):
    s = await setup(tmp_path, monkeypatch, wake=False)
    runtime = s["runtime"]
    await runtime._stop_lease_keeper()
    snapshot = await s["queue"].current_snapshot(s["auth"].subject)
    original = s["queue"].current_snapshot
    reads = 0
    async def unavailable(_subject):
        nonlocal reads
        reads += 1
        raise RuntimeError("controlled cleanup read failure")
    async def work(*_):
        monkeypatch.setattr(s["queue"], "current_snapshot", unavailable)
        if outcome == "work_error":
            raise ValueError("controlled work failure")
        if outcome == "work_cancel":
            raise asyncio.CancelledError()
    monkeypatch.setattr(runtime, "_drive_claimed", work)
    try:
        expected = {"work_error": ValueError, "work_cancel": asyncio.CancelledError,
                    "success_then_read_error": RuntimeError}[outcome]
        with pytest.raises(expected):
            await runtime._drive_with_lease(snapshot)
        assert reads == (1 if outcome == "success_then_read_error" else 0)
        assert runtime._lease_task is None
        monkeypatch.setattr(s["queue"], "current_snapshot", original)
        count = rows(s, "SELECT COUNT(*) FROM foreground_lease_receipts WHERE action='heartbeat'")
        await asyncio.sleep(TTL / 2)
        assert rows(s, "SELECT COUNT(*) FROM foreground_lease_receipts WHERE action='heartbeat'") == count
    finally:
        monkeypatch.setattr(s["queue"], "current_snapshot", original)
        await close(s)

from __future__ import annotations

import asyncio
import hashlib

import aiosqlite
import pytest

from deskpet.execution import RunContext, RunCreate, fingerprint_json
from deskpet.workflows.store import (
    RuntimeActivationError,
    SqliteExecutionUnitOfWork,
)


CAPABILITY_HASH = fingerprint_json({"tools": ["read"], "scope": "workspace"})


def _spec(run_id: str) -> RunCreate:
    return RunCreate(
        run_id=run_id,
        idempotency_key=f"root:session:request:{run_id}",
        context=RunContext(
            session_id="session",
            root_run_id=run_id,
            parent_run_id=None,
            request_id=f"request:{run_id}",
            turn_id=f"turn:{run_id}",
            venue="text",
            workspace={},
            capability_hash=CAPABILITY_HASH,
            provider_plan={},
            trace_id=f"trace:{run_id}",
            principal_id="user",
        ),
        payload_fingerprint=fingerprint_json({"run_id": run_id}),
        capability_fingerprint=CAPABILITY_HASH,
        driver_kind="react",
        profile_key="react_short",
        persistence_level="durable",
    )


async def _owner(path, run_id: str) -> tuple[str, int]:
    async with aiosqlite.connect(path) as db:
        row = await (
            await db.execute(
                """SELECT owner_kind,owner_generation FROM execution_runs
                WHERE run_id=?""",
                (run_id,),
            )
        ).fetchone()
    assert row is not None
    return str(row[0]), int(row[1])


async def _insert_direct_owner_row(
    path, *, run_id: str, owner_kind: str, owner_generation: int
) -> None:
    digest = "a" * 64
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT INTO execution_runs(
            run_id,schema_version,idempotency_key,session_id,root_run_id,parent_run_id,
            request_id,turn_id,venue,workspace_json,capability_hash,provider_plan_json,
            trace_id,principal_id,auth_epoch,payload_fingerprint,capability_fingerprint,
            driver_kind,profile_key,persistence_level,status,owner_kind,owner_generation,
            created_at,updated_at
            ) VALUES(?,1,?,?,?,NULL,?,?,'text','{}',?,'{}',?,?,0,?,?
            ,'react','react_short','durable','running',?,?,1.0,1.0)""",
            (
                run_id,
                f"idem:{run_id}",
                "session",
                run_id,
                f"request:{run_id}",
                f"turn:{run_id}",
                digest,
                f"trace:{run_id}",
                "user",
                digest,
                digest,
                owner_kind,
                owner_generation,
            ),
        )


@pytest.mark.asyncio
async def test_empty_manifest_activation_opens_generation_one_for_kernel_rows(tmp_path):
    path = tmp_path / "workflow.db"
    store = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)

    assert await store.scan_legacy_drain_manifest() == ()
    state = await store.activate_empty_runtime()
    created = await store.create(_spec("kernel-run"))

    assert state.phase == "open"
    assert state.generation == 1
    assert state.drain_count == 0
    assert state.drain_manifest_hash == hashlib.sha256(b"[]").hexdigest()
    assert created.created is True
    assert await _owner(path, "kernel-run") == ("kernel", 1)


@pytest.mark.asyncio
async def test_legacy_fixture_stays_legacy_generation_zero_before_activation(tmp_path):
    path = tmp_path / "legacy.db"
    store = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)

    await store.create(_spec("legacy-run"))

    assert await _owner(path, "legacy-run") == ("legacy", 0)
    assert (await store.get_runtime_state()).phase == "legacy"


@pytest.mark.asyncio
async def test_starts_fail_closed_while_runtime_is_draining_or_activated(tmp_path):
    path = tmp_path / "closed.db"
    store = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)

    draining = await store.begin_runtime_activation()
    assert draining.phase == "draining"
    with pytest.raises(RuntimeActivationError) as draining_error:
        await store.create(_spec("during-drain"))
    assert draining_error.value.code == "runtime_ingress_closed"

    activated = await store.activate_drained_runtime()
    assert activated.phase == "activated"
    with pytest.raises(RuntimeActivationError) as activated_error:
        await store.create(_spec("before-open"))
    assert activated_error.value.code == "runtime_ingress_closed"


@pytest.mark.asyncio
async def test_activation_is_idempotent_and_does_not_increment_generation(tmp_path):
    path = tmp_path / "repeat.db"
    store = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)

    first = await store.activate_empty_runtime()
    second = await store.activate_empty_runtime()

    assert second == first
    async with aiosqlite.connect(path) as db:
        rows = await (
            await db.execute("SELECT COUNT(*) FROM execution_legacy_drain_items")
        ).fetchone()
    assert rows == (0,)


@pytest.mark.asyncio
async def test_concurrent_activation_uses_one_generation_and_one_cas_result(tmp_path):
    path = tmp_path / "concurrent.db"
    first = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    second = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    await first.initialize()
    await second.initialize()

    states = await asyncio.gather(
        first.activate_empty_runtime(), second.activate_empty_runtime()
    )

    assert {(state.phase, state.generation) for state in states} == {("open", 1)}
    assert await first.get_runtime_state() == await second.get_runtime_state()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault_point", "phase_after_crash"),
    (
        ("activation_after_manifest", "legacy"),
        ("activation_after_draining", "legacy"),
        ("activation_after_activated", "draining"),
        ("activation_after_open", "activated"),
    ),
)
async def test_activation_crash_recovers_by_rolling_forward(
    tmp_path, fault_point: str, phase_after_crash: str
):
    path = tmp_path / f"{fault_point}.db"
    fired = False

    def fail_once(point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise RuntimeError(f"crash:{point}")

    crashing = SqliteExecutionUnitOfWork(
        path, clock=lambda: 100.0, fault_injector=fail_once
    )
    with pytest.raises(RuntimeError, match=f"crash:{fault_point}"):
        await crashing.activate_empty_runtime()

    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    assert (await restarted.get_runtime_state()).phase == phase_after_crash
    state = await restarted.activate_empty_runtime()
    assert (state.phase, state.generation) == ("open", 1)


@pytest.mark.asyncio
async def test_nonempty_manifest_is_durable_and_cannot_use_empty_activation(tmp_path):
    path = tmp_path / "nonempty.db"
    store = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    await store.create(_spec("legacy-active"))

    refs = await store.scan_legacy_drain_manifest()
    state = await store.begin_runtime_activation()

    assert [(item.source_kind, item.source_run_id) for item in refs] == [
        ("execution_run", "legacy-active")
    ]
    assert (state.phase, state.generation, state.drain_count) == ("draining", 0, 1)
    with pytest.raises(RuntimeActivationError) as error:
        await store.activate_empty_runtime()
    assert error.value.code == "legacy_drain_required"
    with pytest.raises(RuntimeActivationError) as incomplete:
        await store.activate_drained_runtime()
    assert incomplete.value.code == "legacy_drain_incomplete"


@pytest.mark.asyncio
async def test_schema_rejects_owner_that_does_not_match_runtime_fence(tmp_path):
    legacy_path = tmp_path / "illegal-legacy.db"
    legacy = SqliteExecutionUnitOfWork(legacy_path)
    await legacy.initialize()
    with pytest.raises(aiosqlite.IntegrityError, match="execution_owner_not_active"):
        await _insert_direct_owner_row(
            legacy_path,
            run_id="early-kernel",
            owner_kind="kernel",
            owner_generation=1,
        )

    open_path = tmp_path / "illegal-open.db"
    opened = SqliteExecutionUnitOfWork(open_path)
    await opened.activate_empty_runtime()
    with pytest.raises(aiosqlite.IntegrityError, match="execution_owner_not_active"):
        await _insert_direct_owner_row(
            open_path,
            run_id="late-legacy",
            owner_kind="legacy",
            owner_generation=0,
        )

    await opened.create(_spec("kernel-owned"))
    async with aiosqlite.connect(open_path) as db:
        with pytest.raises(aiosqlite.IntegrityError, match="execution_owner_immutable"):
            await db.execute(
                """UPDATE execution_runs SET owner_kind='legacy',owner_generation=0
                WHERE run_id='kernel-owned'"""
            )

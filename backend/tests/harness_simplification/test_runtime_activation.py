from __future__ import annotations

import asyncio
import hashlib

import aiosqlite
import pytest

from deskpet.execution.contracts import (
    AttachmentPolicy,
    ChildCommandIntent,
    RunContext,
    RunCreate,
    fingerprint_json,
)
from deskpet.workflows.store import (
    RuntimeActivationCommand,
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


def _child_intent(parent: RunCreate, child_id: str) -> ChildCommandIntent:
    child_request = {"task": child_id, "driver_kind": "react"}
    capability_hash = fingerprint_json({"tools": []})
    child_spec = RunCreate(
        run_id=child_id,
        idempotency_key=f"delegate:{parent.run_id}:{child_id}",
        context=RunContext(
            session_id=parent.context.session_id,
            root_run_id=parent.context.root_run_id,
            parent_run_id=parent.run_id,
            request_id=f"{parent.context.request_id}:child:{child_id}",
            turn_id=parent.context.turn_id,
            venue=parent.context.venue,
            workspace={},
            capability_hash=capability_hash,
            provider_plan={},
            trace_id=f"trace:{child_id}",
            principal_id=parent.context.principal_id,
        ),
        payload_fingerprint=fingerprint_json(child_request),
        capability_fingerprint=capability_hash,
        driver_kind="react",
        profile_key="react_short",
        persistence_level="durable",
        status="queued",
    )
    return ChildCommandIntent(
        operation_id=f"operation:{child_id}",
        parent_run_id=parent.run_id,
        command_id=f"command:{child_id}",
        child_spec=child_spec,
        child_request=child_request,
        capability_subset=(),
        attachment_policy=AttachmentPolicy.DETACHED,
        capability_snapshot_ref=capability_hash,
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
    state = await store.activate_runtime()
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

    def fail_after_activated(point: str) -> None:
        if point == "activation_after_activated":
            raise RuntimeError(point)

    crashing = SqliteExecutionUnitOfWork(
        path, clock=lambda: 100.0, fault_injector=fail_after_activated
    )
    with pytest.raises(RuntimeError, match="activation_after_activated"):
        await crashing.activate_runtime()
    draining = await store.get_runtime_state()
    assert draining.phase == "draining"
    with pytest.raises(RuntimeActivationError) as draining_error:
        await store.create(_spec("during-drain"))
    assert draining_error.value.code == "runtime_ingress_closed"

    def fail_after_open(point: str) -> None:
        if point == "activation_after_open":
            raise RuntimeError(point)

    crashing = SqliteExecutionUnitOfWork(
        path, clock=lambda: 100.0, fault_injector=fail_after_open
    )
    with pytest.raises(RuntimeError, match="activation_after_open"):
        await crashing.activate_runtime()
    activated = await store.get_runtime_state()
    assert activated.phase == "activated"
    # Bootstrap recovery is allowed before ingress opens.  It is deliberately
    # a separate boundary from create(), which must remain fail-closed here.
    assert await store.list_recoverable() == ()
    with pytest.raises(RuntimeActivationError) as activated_error:
        await store.create(_spec("before-open"))
    assert activated_error.value.code == "runtime_ingress_closed"


@pytest.mark.asyncio
async def test_activated_recovery_allows_only_fenced_existing_work(tmp_path):
    path = tmp_path / "activated-recovery.db"
    store = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    await store.activate_runtime()
    parent_spec = _spec("current-parent")
    parent = (await store.create(parent_spec)).record
    pending = _child_intent(parent_spec, "persisted-child")
    await store.commit_child_command(pending)

    async with aiosqlite.connect(path) as db:
        await db.execute(
            "UPDATE execution_runtime_state SET phase='activated',updated_at=100.0"
        )
        await db.commit()

    assert [item.run_id for item in await store.list_recoverable()] == [parent.run_id]
    lease = await store.recovery_scope(parent.run_id, owner="bootstrap")
    assert lease.run_id == parent.run_id
    assert await store.recovery_scope(lease, lease_seconds=None) is True
    continuation = await store.persist_react_boundary(
        parent.run_id, parent.version, {"checkpoint": "bootstrap"}
    )
    assert continuation.run_id == parent.run_id

    with pytest.raises(RuntimeActivationError) as new_start:
        await store.create(_spec("new-root"))
    assert new_start.value.code == "runtime_ingress_closed"
    with pytest.raises(RuntimeActivationError) as new_command:
        await store.commit_child_command(
            _child_intent(parent_spec, "not-persisted-child")
        )
    assert new_command.value.code == "runtime_ingress_closed"

    command = (await store.lease_child_commands(
        owner="bootstrap-child", limit=1, lease_seconds=30.0
    ))[0]
    scheduled = await store.schedule_child_command(
        command.operation_id,
        lease_owner="bootstrap-child",
        lease_epoch=command.schedule_lease_epoch,
    )
    assert await _owner(path, scheduled.child_run_id) == ("kernel", 1)
    await store.acknowledge_child_command(
        command.operation_id,
        lease_owner="bootstrap-child",
        lease_epoch=command.schedule_lease_epoch,
    )
    parents = await store.list_pending_child_signal_parents()
    assert [item.run_id for item in parents] == [parent.run_id]
    signals = await store.list_pending_child_signals(parent.run_id)
    assert len(signals) == 1

    async with aiosqlite.connect(path) as db:
        await db.execute(
            """UPDATE execution_runs SET status='completed',
            terminal_event_id='fixture-terminal',ended_at=100.0,updated_at=100.0
            WHERE run_id=?""",
            (parent.run_id,),
        )
        await db.commit()
    acknowledged = await store.ack_child_signal(signals[0].signal_id)
    assert acknowledged.delivered_at == 100.0


@pytest.mark.asyncio
async def test_recovery_scope_rejects_stale_generation_in_activated_phase(tmp_path):
    path = tmp_path / "stale-recovery.db"
    store = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    await store.activate_runtime()
    await store.create(_spec("generation-one"))
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """UPDATE execution_runtime_state
            SET phase='activated',generation=2,updated_at=100.0"""
        )
        await db.commit()

    with pytest.raises(RuntimeActivationError) as stale:
        await store.recovery_scope("generation-one", owner="bootstrap")
    assert stale.value.code == "run_owner_conflict"


@pytest.mark.asyncio
async def test_activation_is_idempotent_and_does_not_increment_generation(tmp_path):
    path = tmp_path / "repeat.db"
    store = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)

    first = await store.activate_runtime()
    second = await store.activate_runtime()

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
        first.activate_runtime(), second.activate_runtime()
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
        await crashing.activate_runtime()

    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    assert (await restarted.get_runtime_state()).phase == phase_after_crash
    state = await restarted.activate_runtime()
    assert (state.phase, state.generation) == ("open", 1)


@pytest.mark.asyncio
async def test_nonempty_manifest_is_durable_and_cannot_use_empty_activation(tmp_path):
    path = tmp_path / "nonempty.db"
    store = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    await store.create(_spec("legacy-active"))

    refs = await store.scan_legacy_drain_manifest()
    with pytest.raises(RuntimeActivationError) as error:
        await store.activate_runtime()
    state = await store.get_runtime_state()

    assert [(item.source_kind, item.source_run_id) for item in refs] == [
        ("execution_run", "legacy-active")
    ]
    assert (state.phase, state.generation, state.drain_count) == ("draining", 0, 1)
    assert error.value.code == "legacy_drain_required"
    with pytest.raises(RuntimeActivationError) as incomplete:
        await store.activate_runtime(require_empty=False)
    assert incomplete.value.code == "legacy_drain_incomplete"


@pytest.mark.asyncio
async def test_read_only_blocked_legacy_workflow_history_does_not_block_activation(
    tmp_path,
):
    path = tmp_path / "blocked-history.db"
    store = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    await store.initialize()
    async with aiosqlite.connect(path) as db:
        for run_id, recovery_action in (
            ("read-only-history", "read_only"),
            ("live-blocked-workflow", None),
        ):
            await db.execute(
                """INSERT INTO workflow_runs(
                run_id,trace_id,thread_id,checkpoint_ns,session_id,request_id,
                turn_id,workflow_name,workflow_version,manifest_hash,
                implementation_hash,capability_hash,state_schema_version,
                status,active_nodes_json,recovery_action,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'blocked','[]',?,?,?)""",
                (
                    run_id,
                    f"trace:{run_id}",
                    f"thread:{run_id}",
                    "",
                    "session",
                    f"request:{run_id}",
                    f"turn:{run_id}",
                    "ppt_pro",
                    "v1",
                    "manifest",
                    "implementation",
                    "capability",
                    1,
                    recovery_action,
                    1.0,
                    1.0,
                ),
            )
        await db.commit()

    refs = await store.scan_legacy_drain_manifest()

    assert [(item.source_kind, item.source_run_id) for item in refs] == [
        ("workflow_run", "live-blocked-workflow")
    ]


@pytest.mark.asyncio
async def test_legacy_drain_lease_is_fenced_reclaimable_and_requires_source_terminal(tmp_path):
    path = tmp_path / "leased-drain.db"
    now = 100.0
    store = SqliteExecutionUnitOfWork(path, clock=lambda: now)
    await store.create(_spec("legacy-active"))
    with pytest.raises(RuntimeActivationError, match="empty-runtime activation"):
        await store.activate_runtime()

    first = await store.activate_runtime(
        command=RuntimeActivationCommand.claim("worker-a", lease_seconds=10.0)
    )
    assert first is not None
    assert first.ref.source_run_id == "legacy-active"
    with pytest.raises(RuntimeActivationError) as active:
        await store.activate_runtime(command=RuntimeActivationCommand.settle(first))
    assert active.value.code == "legacy_drain_item_active"

    assert await store.activate_runtime(
        command=RuntimeActivationCommand.settle(first, error="legacy worker failed")
    ) is True
    second = await store.activate_runtime(
        command=RuntimeActivationCommand.claim("worker-b", lease_seconds=10.0)
    )
    assert second is not None
    assert second.epoch == first.epoch + 1
    assert await store.activate_runtime(
        command=RuntimeActivationCommand.settle(first, error="stale")
    ) is False

    async with aiosqlite.connect(path) as db:
        await db.execute(
            """UPDATE execution_runs
            SET status='completed',terminal_event_id='legacy-final',ended_at=?,updated_at=?
            WHERE run_id='legacy-active'""",
            (now, now),
        )
        await db.commit()
    assert await store.activate_runtime(
        command=RuntimeActivationCommand.settle(second)
    ) is True
    opened = await store.activate_runtime(require_empty=False)
    assert (opened.phase, opened.generation) == ("open", 1)


@pytest.mark.asyncio
async def test_activation_rollback_is_only_available_before_open_and_without_active_drain(tmp_path):
    draining_path = tmp_path / "rollback-draining.db"

    def fail_after_activated(point: str) -> None:
        if point == "activation_after_activated":
            raise RuntimeError(point)

    crashing = SqliteExecutionUnitOfWork(
        draining_path, clock=lambda: 100.0, fault_injector=fail_after_activated
    )
    with pytest.raises(RuntimeError, match="activation_after_activated"):
        await crashing.activate_runtime()
    store = SqliteExecutionUnitOfWork(draining_path, clock=lambda: 101.0)
    rolled_back = await store.activate_runtime(command=RuntimeActivationCommand.rollback())
    assert (rolled_back.phase, rolled_back.generation) == ("legacy", 0)

    active_path = tmp_path / "rollback-active-drain.db"
    active = SqliteExecutionUnitOfWork(active_path, clock=lambda: 100.0)
    await active.create(_spec("legacy-active"))
    with pytest.raises(RuntimeActivationError):
        await active.activate_runtime()
    with pytest.raises(RuntimeActivationError) as active_error:
        await active.activate_runtime(command=RuntimeActivationCommand.rollback())
    assert active_error.value.code == "legacy_drain_active"

    open_path = tmp_path / "rollback-open.db"
    opened = SqliteExecutionUnitOfWork(open_path, clock=lambda: 100.0)
    await opened.activate_runtime()
    with pytest.raises(RuntimeActivationError) as open_error:
        await opened.activate_runtime(command=RuntimeActivationCommand.rollback())
    assert open_error.value.code == "runtime_ingress_open"


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
    await opened.activate_runtime()
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

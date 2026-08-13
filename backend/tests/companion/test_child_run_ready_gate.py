# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import hashlib
import json

import pytest

from deskpet.capabilities.contracts import CapabilityScope
from deskpet.capabilities.platform import CapabilityPlatform
from deskpet.capabilities.run_catalog import SqliteRunCatalogLeasePreparer
from deskpet.capabilities.store import CapabilityStore
from deskpet.execution.contracts import (
    AttachmentPolicy,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunRef,
    RunStartSnapshotRecord,
    RunStatus,
    canonical_json,
    fingerprint_json,
)
from deskpet.harness.child_runs import ChildRunCoordinator
from deskpet.harness.contracts import RegisteredDriver, driver_catalog
from deskpet.harness.kernel import RunKernel
from deskpet.harness.ports import DelegateRun, DriverTerminalCandidate, JoinPolicy
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.harness.start_snapshot import start_extension_receipts
from deskpet.tools.build_identity import ExecutionBuildIdentity
from deskpet.tools.capabilities import (
    ToolCapabilityResolver,
    ToolEligibilityContext,
    ToolExposureIntent,
)
from deskpet.tools.registry import ToolRegistry
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


async def _handler(_args):
    return "ok"


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        "fixture_read",
        "core",
        {
            "name": "fixture_read",
            "description": "fixture",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        _handler,
        stable_handler_id="fixture.child.read.v1",
        execution_build_identity=ExecutionBuildIdentity(
            provider="core",
            handler_id="fixture.child.read.v1",
            build_digest="a" * 64,
            sources_manifest_hash="b" * 64,
            artifacts=(("fixture.py", "c" * 64),),
        ),
    )
    return registry


def _root_start(spec: RunCreate, lease) -> RunStartSnapshotRecord:
    messages = [{"role": "user", "content": "delegate"}]
    session_cursor = {"session_projection_cursor": 0}
    prepared_refs = {"prepared_tool_set_ref": "d" * 64}
    request = {"payload": {}, "text": "delegate"}
    capability_snapshot = {
        "run_catalog_content_stamp": lease.run_catalog_content_stamp,
        "process_catalog_stamp": lease.process_catalog_stamp,
        "catalog_snapshot_ref": lease.snapshot_ref,
        "capability_lease_intent_ref": lease.lease_intent_id,
        "prepared_tool_set_ref": "d" * 64,
        "capability_hash": spec.capability_fingerprint,
        "product_snapshot_ref": "product-root-1",
        "host_extensions": {},
    }
    provider_policy = {"provider_plan": {}}
    terminal_deliveries: list[dict[str, object]] = []
    payload = {
        "snapshot_schema_version": 1,
        "canonical_messages": messages,
        "session_cursor": session_cursor,
        "prepared_refs": prepared_refs,
        "sanitized_request": request,
        "run_context": spec.context.to_dict(),
        "run_spec": spec.to_dict(),
        "capability_snapshot": capability_snapshot,
        "provider_launch_policy": provider_policy,
        "terminal_deliveries": terminal_deliveries,
        "capability_lease_intent_ref": lease.lease_intent_id,
        "capability_lease_intent_hash": lease.lease_intent_hash,
    }
    return RunStartSnapshotRecord(
        run_id=spec.run_id,
        snapshot_schema_version=1,
        start_fingerprint=fingerprint_json(payload),
        canonical_messages_json=canonical_json(messages),
        session_cursor_json=canonical_json(session_cursor),
        prepared_refs_json=canonical_json(prepared_refs),
        sanitized_request_json=canonical_json(request),
        run_context_json=canonical_json(spec.context.to_dict()),
        run_spec_json=canonical_json(spec.to_dict()),
        capability_snapshot_json=canonical_json(capability_snapshot),
        capability_snapshot_hash=fingerprint_json(capability_snapshot),
        provider_launch_policy_json=canonical_json(provider_policy),
        terminal_deliveries_json=canonical_json(terminal_deliveries),
        terminal_deliveries_hash=fingerprint_json(terminal_deliveries),
        capability_lease_intent_ref=lease.lease_intent_id,
        capability_lease_intent_hash=lease.lease_intent_hash,
        created_at=100.0,
    )


async def _stack(tmp_path):
    uow = SqliteExecutionUnitOfWork(tmp_path / "execution.db")
    await uow.initialize()
    store = CapabilityStore(uow, clock=lambda: 100.0)
    await store.initialize()
    registry = _registry()
    platform = CapabilityPlatform(
        registry=registry,
        store=store,
        user_data_root=tmp_path / "capabilities",
        register_control_surface=False,
        first_party_pack_roots=(),
    )
    platform.run_catalog_lease_preparer = SqliteRunCatalogLeasePreparer(
        store=store,
        registry=registry,
        hub=platform.hub,
        process_instance_id="process-child-fixture",
    )
    tools = (
        ToolCapabilityResolver(registry)
        .resolve_draft(
            ToolExposureIntent(required_direct_names=("fixture_read",)),
            eligibility=ToolEligibilityContext(
                session_id="session-child",
                request_id="request-parent",
                task_type="chat",
            ),
        )
        .finalize(scope_id="scope-child")
    )
    root_id = "parent-ready-gate"
    lease = await platform.prepare_run_catalog_lease(
        scope=CapabilityScope.for_run(root_id, user_key="owner-child"),
        owner_key="owner-child",
        prepared_tool_set=tools,
        prepared_tool_set_fingerprint="d" * 64,
        run_id=root_id,
        root_run_id=root_id,
        request_id="request-parent",
        turn_id="turn-parent",
        owner_operation_id="run-start:parent-ready-gate:1",
    )
    capability_hash = "f" * 64
    spec = RunCreate(
        run_id=root_id,
        idempotency_key="root:parent-ready-gate",
        context=RunContext(
            session_id="session-child",
            root_run_id=root_id,
            parent_run_id=None,
            request_id="request-parent",
            turn_id="turn-parent",
            venue="text",
            workspace={},
            capability_hash=capability_hash,
            provider_plan={},
            trace_id="trace-parent",
            principal_id="principal-child",
            owner_key="companion:child:1",
            profile_generation=1,
            binding_epoch=1,
        ),
        payload_fingerprint=fingerprint_json({"text": "delegate"}),
        capability_fingerprint=capability_hash,
        driver_kind="react",
        profile_key="agent.general",
        persistence_level=PersistenceLevel.DURABLE,
        status=RunStatus.RUNNING,
    )
    start = _root_start(spec, lease)
    created = await uow.create_with_start_snapshot(
        spec,
        start,
        start_commit_extensions=(lease.start_commit_extension,),
    )
    stored = await uow.read_run_start_snapshot(root_id)
    assert stored is not None
    assert await lease.after_start_handshake.activate_after_start(
        created.record,
        start_snapshot=stored,
        extension_receipts=start_extension_receipts(stored),
    )
    coordinator = ChildRunCoordinator(uow, snapshot_leaser=platform)
    return uow, store, platform, created.record, lease, coordinator


def _delegate(*, spoof: bool = False) -> DelegateRun:
    request = {
        "task": "child work",
        "driver_kind": "react",
    }
    if spoof:
        request.update(
            {
                "capability_snapshot": {
                    "catalog_snapshot_ref": "0" * 64,
                },
                "capability_snapshot_lease": {
                    "lease_intent_id": "1" * 64,
                },
                "run_catalog_content_stamp": "2" * 64,
            }
        )
    return DelegateRun(
        run_id="parent-ready-gate",
        command_id="child-spoof" if spoof else "child-normal",
        child_request=request,
        route_hint="agent.general",
        capability_subset=("fixture_read",),
        attachment_policy=AttachmentPolicy.DETACHED,
        join_policy=JoinPolicy.DETACHED,
    )


@pytest.mark.asyncio
async def test_child_gets_independent_bound_lease_and_ignores_payload_spoof(
    tmp_path,
) -> None:
    uow, store, platform, parent, parent_lease, coordinator = await _stack(
        tmp_path
    )
    command = await coordinator.submit(parent, _delegate(spoof=True))
    start = await uow.read_run_start_snapshot(command.child_run_id)
    assert start is not None
    assert start.capability_lease_intent_ref
    assert start.capability_lease_intent_ref != parent_lease.lease_intent_id
    child_snapshot = json.loads(start.capability_snapshot_json)
    assert child_snapshot["catalog_snapshot_ref"] == parent_lease.snapshot_ref
    assert child_snapshot["capability_lease_intent_ref"] == (
        start.capability_lease_intent_ref
    )
    request = json.loads(start.sanitized_request_json)["payload"]
    assert "capability_snapshot" not in request
    assert "capability_snapshot_lease" not in request
    assert "run_catalog_content_stamp" not in request
    assert await store.snapshot_lease_fingerprints(
        snapshot_ref=parent_lease.snapshot_ref,
        run_id=command.child_run_id,
    )
    with pytest.raises(RuntimeError, match="snapshot_lease_not_ready"):
        await platform.require_run_catalog_ready(command.child_run_id)
    prepared = await coordinator.activate_child_snapshot_after_commit(start)
    assert prepared is not None
    await platform.require_run_catalog_ready(command.child_run_id)
    await uow.close()


class _CountingDriver:
    def __init__(self) -> None:
        self.starts = 0

    async def start(self, request):
        self.starts += 1
        yield DriverTerminalCandidate(request.run_id, "completed", "done")

    async def recover(self, run_id, recovery_lease):
        if False:
            yield DriverTerminalCandidate(run_id, "completed")

    async def signal(self, signal):
        if False:
            yield DriverTerminalCandidate(signal.run_id, "completed")

    async def cancel(self, run_id, reason):
        yield DriverTerminalCandidate(run_id, "cancelled")

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_spoofed_child_projection_fails_before_driver_provider_or_effect(
    tmp_path,
) -> None:
    uow, _store, platform, parent, _parent_lease, coordinator = await _stack(
        tmp_path
    )
    command = await coordinator.submit(parent, _delegate())
    leased = (
        await uow.lease_child_commands(
            owner="scheduler", limit=1, lease_seconds=30
        )
    )[0]
    scheduled = await uow.schedule_child_command(
        command.operation_id,
        lease_owner="scheduler",
        lease_epoch=leased.schedule_lease_epoch,
    )
    # Model a post-commit projection loss.  The immutable RunStart itself
    # cannot be spoofed (SQLite rejects updates), and the missing bound
    # projection must still stop the child before Driver entry.
    prepared_lease = platform._prepared_child_catalog_leases[
        command.child_run_id
    ]
    await prepared_lease.release_prepared()
    driver = _CountingDriver()
    kernel = RunKernel(
        uow=uow,
        router=None,
        profiles=ProfileRegistry(
            (ProfileSpec("agent.general", "agent.general", "react"),)
        ),
        root_profile_key="agent.general",
        drivers=driver_catalog(
            (RegisteredDriver("react", driver, durable_from_start=True),)
        ),
        child_runs=coordinator,
    )
    with pytest.raises(
        RuntimeError, match="child_catalog_projection_not_bound"
    ):
        await kernel._accept_precreated_child(scheduled)
    assert driver.starts == 0
    async with uow._read_connection() as db:
        provider_count = (
            await (
                await db.execute(
                    "SELECT COUNT(*) FROM execution_provider_invocations"
                )
            ).fetchone()
        )[0]
        effect_count = (
            await (
                await db.execute("SELECT COUNT(*) FROM execution_effects")
            ).fetchone()
        )[0]
    assert provider_count == 0
    assert effect_count == 0
    await uow.close()


@pytest.mark.asyncio
async def test_child_activation_rehydrates_lost_process_pin_after_commit(
    tmp_path,
) -> None:
    uow, store, platform, parent, parent_lease, coordinator = await _stack(
        tmp_path
    )
    command = await coordinator.submit(parent, _delegate())
    start = await uow.read_run_start_snapshot(command.child_run_id)
    assert start is not None
    await parent_lease.release_prepared()
    parent_state = await store.read_snapshot_projection_state(
        parent_lease.lease_intent_id
    )
    assert parent_state is not None
    assert parent_state.intent_status == "released"
    assert await store.snapshot_lease_fingerprints(
        snapshot_ref=parent_lease.snapshot_ref,
        run_id=command.child_run_id,
    )
    prepared = platform._prepared_child_catalog_leases.pop(
        command.child_run_id
    )
    await prepared._pin.rollback()
    await platform.snapshot_lease_ready_gate.unregister_pending(
        prepared.lease_intent_id,
        command.child_run_id,
    )

    recovered = await coordinator.activate_child_snapshot_after_commit(start)

    assert recovered is not None
    await platform.require_run_catalog_ready(command.child_run_id)
    state = await platform.store.read_snapshot_projection_state(
        start.capability_lease_intent_ref
    )
    assert state is not None
    assert state.intent_status == "bound"
    assert state.projection_status == "ready"
    await uow.close()


@pytest.mark.asyncio
async def test_child_with_unavailable_historical_tools_terminalizes_once(
    tmp_path,
) -> None:
    uow, store, platform, parent, parent_lease, coordinator = await _stack(
        tmp_path
    )
    command = await coordinator.submit(parent, _delegate())
    start = await uow.read_run_start_snapshot(command.child_run_id)
    assert start is not None
    prepared = platform._prepared_child_catalog_leases.pop(
        command.child_run_id
    )
    await prepared._pin.rollback()
    await platform.snapshot_lease_ready_gate.unregister_pending(
        prepared.lease_intent_id,
        command.child_run_id,
    )
    await parent_lease.release_prepared()
    assert platform.registry.unregister("fixture_read") is True
    assert platform.registry.retired_spec_fingerprints() == frozenset()

    leased = (
        await uow.lease_child_commands(
            owner="scheduler", limit=1, lease_seconds=30
        )
    )[0]
    scheduled = await uow.schedule_child_command(
        command.operation_id,
        lease_owner="scheduler",
        lease_epoch=leased.schedule_lease_epoch,
    )
    driver = _CountingDriver()
    kernel = RunKernel(
        uow=uow,
        router=None,
        profiles=ProfileRegistry(
            (ProfileSpec("agent.general", "agent.general", "react"),)
        ),
        root_profile_key="agent.general",
        drivers=driver_catalog(
            (RegisteredDriver("react", driver, durable_from_start=True),)
        ),
        child_runs=coordinator,
    )

    await kernel._accept_precreated_child(scheduled)

    terminal = await uow.query(
        RunRef(
            command.child_run_id,
            command.intent.child_spec.context.session_id,
        ),
        command.intent.child_spec.context.actor(),
    )
    assert terminal.status is RunStatus.FAILED
    assert driver.starts == 0
    # A later reconciliation tick sees the durable terminal and does not
    # attempt to rebuild the missing process pin again.
    await kernel._accept_precreated_child(scheduled)
    assert driver.starts == 0
    await uow.close()


@pytest.mark.asyncio
async def test_child_terminal_releases_its_own_lease_and_ready_gate(
    tmp_path,
) -> None:
    uow, store, platform, parent, parent_lease, coordinator = await _stack(
        tmp_path
    )
    command = await coordinator.submit(parent, _delegate())
    leased = (
        await uow.lease_child_commands(
            owner="scheduler", limit=1, lease_seconds=30
        )
    )[0]
    scheduled = await uow.schedule_child_command(
        command.operation_id,
        lease_owner="scheduler",
        lease_epoch=leased.schedule_lease_epoch,
    )

    async def terminal_observer(record, _event) -> None:
        start = await uow.read_run_start_snapshot(record.run_id)
        snapshot = (
            {}
            if start is None
            else json.loads(start.capability_snapshot_json)
        )
        snapshot_ref = str(snapshot.get("catalog_snapshot_ref") or "")
        await platform.hub.release_run_leases(
            record.run_id,
            known_snapshot_refs=(snapshot_ref,) if snapshot_ref else (),
        )
        await platform.retire_run_catalog_ready(record.run_id)

    driver = _CountingDriver()
    kernel = RunKernel(
        uow=uow,
        router=None,
        profiles=ProfileRegistry(
            (ProfileSpec("agent.general", "agent.general", "react"),)
        ),
        root_profile_key="agent.general",
        drivers=driver_catalog(
            (RegisteredDriver("react", driver, durable_from_start=True),)
        ),
        child_runs=coordinator,
        terminal_observer=terminal_observer,
    )
    await kernel._accept_precreated_child(scheduled)
    active = kernel._live.get(command.child_run_id)
    assert active is not None and active.task is not None
    await active.task

    start = await uow.read_run_start_snapshot(command.child_run_id)
    assert start is not None
    state = await store.read_snapshot_projection_state(
        start.capability_lease_intent_ref
    )
    assert state is not None
    assert state.intent_status == "released"
    assert await store.snapshot_lease_fingerprints(
        snapshot_ref=parent_lease.snapshot_ref,
        run_id=command.child_run_id,
    ) == ()
    with pytest.raises(RuntimeError, match="snapshot_lease_not_ready"):
        await platform.require_run_catalog_ready(command.child_run_id)
    await uow.close()


@pytest.mark.asyncio
async def test_child_precommit_crash_leaves_no_child_or_active_pin(
    tmp_path,
) -> None:
    uow, _store, platform, parent, _parent_lease, coordinator = await _stack(
        tmp_path
    )

    def crash(point: str) -> None:
        if point == "child_precreate_before_commit":
            raise RuntimeError("child precommit crash")

    uow._fault_injector = crash
    command = _delegate()
    child_run_id = "child-" + hashlib.sha256(
        (
            "execution-child-run|"
            f"{parent.run_id}|{command.command_id}"
        ).encode()
    ).hexdigest()[:32]
    with pytest.raises(RuntimeError, match="child precommit crash"):
        await coordinator.submit(parent, command)
    uow._fault_injector = None

    async with uow._read_connection() as db:
        command_count = (
            await (
                await db.execute(
                    "SELECT COUNT(*) FROM execution_child_commands "
                    "WHERE child_run_id=?",
                    (child_run_id,),
                )
            ).fetchone()
        )[0]
        run_count = (
            await (
                await db.execute(
                    "SELECT COUNT(*) FROM execution_runs WHERE run_id=?",
                    (child_run_id,),
                )
            ).fetchone()
        )[0]
        start_count = (
            await (
                await db.execute(
                    "SELECT COUNT(*) FROM execution_run_start_snapshots "
                    "WHERE run_id=?",
                    (child_run_id,),
                )
            ).fetchone()
        )[0]
        active_intent_count = (
            await (
                await db.execute(
                    "SELECT COUNT(*) "
                    "FROM capability_snapshot_lease_intents "
                    "WHERE run_id=? AND status IN ('prepared','bound')",
                    (child_run_id,),
                )
            ).fetchone()
        )[0]
        active_lease_count = (
            await (
                await db.execute(
                    "SELECT COUNT(*) FROM capability_snapshot_leases "
                    "WHERE run_id=? AND released_at IS NULL",
                    (child_run_id,),
                )
            ).fetchone()
        )[0]
    assert (
        command_count,
        run_count,
        start_count,
        active_intent_count,
        active_lease_count,
    ) == (0, 0, 0, 0, 0)
    assert child_run_id not in platform._prepared_child_catalog_leases
    assert child_run_id not in (
        platform.snapshot_lease_ready_gate._run_to_intent
    )
    await uow.close()

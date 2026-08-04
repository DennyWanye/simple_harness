from __future__ import annotations

import hashlib
import json

import pytest

from deskpet.capabilities.contracts import (
    CapabilityScope,
    CapabilityVersionDescriptor,
)
from deskpet.capabilities.hub import CapabilityHub, ToolRegistryCatalogSource
from deskpet.capabilities.store import CapabilityStore, CapabilityVersionRecord
from deskpet.execution.contracts import AttachmentPolicy
from deskpet.harness.child_runs import ChildRunCoordinator
from deskpet.harness.contracts import RegisteredDriver, driver_catalog
from deskpet.harness.kernel import HostContext, RunKernel, RunRequest, root_run_identity
from deskpet.harness.ports import DelegateRun, DriverTerminalCandidate, JoinPolicy
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.tools.registry import ToolRegistry, tool_spec_fingerprint
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _TerminalDriver:
    async def start(self, request):
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


class _DelegateDriver(_TerminalDriver):
    def __init__(self, snapshot_ref: str) -> None:
        self.snapshot_ref = snapshot_ref

    async def start(self, request):
        yield DelegateRun(
            request.run_id,
            "delegate-fixture",
            {
                "driver_kind": "react",
                "text": "child",
                "capability_snapshot_lease": {
                    "snapshot_ref": self.snapshot_ref,
                },
            },
            "agent.general",
            (),
            AttachmentPolicy.ATTACHED,
            JoinPolicy.JOIN_BEFORE_FINAL,
        )


async def _capability_runtime(tmp_path):
    uow = SqliteExecutionUnitOfWork(tmp_path / "execution.db")
    await uow.activate_runtime()
    store = CapabilityStore(uow, clock=lambda: 100.0)
    await store.initialize()
    registry = ToolRegistry()
    manifest_hash = _hash("manifest:lease-fixture")
    registry.register(
        "lease_fixture__echo",
        "capability:lease-fixture",
        {
            "name": "lease_fixture__echo",
            "description": "Lease fixture",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        lambda _args, _task: '{"ok":true}',
        source=f"capability:lease-fixture:1.0.0:{manifest_hash}",
        spec_version="capability-pack:1.0.0",
        runtime_provenance_ref=_hash("runtime:lease-fixture"),
    )
    spec = registry.catalog_snapshot().specs[0]
    descriptor = CapabilityVersionDescriptor(
        capability_id="lease-fixture",
        display_name="Lease fixture",
        version="1.0.0",
        kind="function_tool",
        source="fixture:lease-fixture@1.0.0",
        description="Exercises immutable snapshot ownership",
        aliases=("lease",),
        logical_tool_ids=("echo",),
        provider_tool_names=("lease_fixture__echo",),
        permission_categories=("filesystem_read",),
        effect_kinds=("read_only",),
        schema_hash=spec.schema_hash,
        manifest_hash=manifest_hash,
        health="healthy",
    )
    await store.record_version(
        CapabilityVersionRecord(
            descriptor=descriptor,
            install_path=tmp_path / "packs" / "lease-fixture" / "1.0.0",
            validation_status="healthy",
            expected_tool_fingerprints=(tool_spec_fingerprint(spec),),
            parent_version=None,
            parent_manifest_hash=None,
            derived_from_receipt_ref=None,
            created_at=100.0,
        )
    )
    await store.set_binding(
        scope="user",
        scope_key="default",
        pack_id=descriptor.capability_id,
        version=descriptor.version,
        manifest_hash=descriptor.manifest_hash,
        expected_generation=0,
    )
    hub = CapabilityHub(
        store=store,
        registry_source=ToolRegistryCatalogSource(registry),
    )
    return uow, store, hub, descriptor


def _profiles() -> ProfileRegistry:
    return ProfileRegistry(
        (
            ProfileSpec(
                "agent.general",
                "agent.general",
                "react",
            ),
        )
    )


def _host() -> HostContext:
    return HostContext(
        session_id="session-lease",
        principal_id="principal-lease",
        auth_epoch=1,
        capability_hash="c" * 64,
        available_capabilities=frozenset(),
        provider_plan=("fixture",),
        trace_id="trace-lease",
    )


@pytest.mark.asyncio
async def test_child_payload_cannot_forge_snapshot_lease_clone(
    tmp_path,
) -> None:
    uow, store, hub, descriptor = await _capability_runtime(tmp_path)
    request = RunRequest("delegate", "request-child-lease", "turn-1")
    run_id = root_run_identity(
        _host().session_id, request.request_id, request.turn_id
    )[1].run_id
    snapshot = await hub.snapshot_and_acquire_lease(
        run_id=run_id,
        root_run_id=run_id,
        scope=CapabilityScope.for_run(run_id),
    )
    driver = _DelegateDriver(snapshot.snapshot_ref)
    coordinator = ChildRunCoordinator(uow, snapshot_leaser=hub)
    kernel = RunKernel(
        uow=uow,
        router=None,
        profiles=_profiles(),
        root_profile_key="agent.general",
        drivers=driver_catalog(
            (RegisteredDriver("react", driver, durable_from_start=True),)
        ),
        child_runs=coordinator,
    )

    handle = await kernel.start(request, _host())
    active = kernel._live.get(handle.ref.run_id)
    assert active is not None and active.task is not None
    await active.task
    operation_id = _hash(
        f"execution-child-operation|{run_id}|delegate-fixture"
    )
    child = await uow.get_child_command(operation_id)
    assert child is not None
    assert await store.snapshot_lease_fingerprints(
        snapshot_ref=snapshot.snapshot_ref,
        run_id=child.child_run_id,
    ) == ()
    child_start = await uow.read_run_start_snapshot(child.child_run_id)
    assert child_start is not None
    child_capabilities = json.loads(
        child_start.capability_snapshot_json
    )
    assert "capability_snapshot_lease" not in child_capabilities
    assert "capability_lease_intent_ref" not in child_capabilities

    assert await hub.release_run_leases(run_id) == 1
    assert await hub.version_can_be_collected(
        pack_id=descriptor.capability_id,
        version=descriptor.version,
        manifest_hash=descriptor.manifest_hash,
    )
    assert await hub.release_run_leases(child.child_run_id) == 0
    assert await hub.version_can_be_collected(
        pack_id=descriptor.capability_id,
        version=descriptor.version,
        manifest_hash=descriptor.manifest_hash,
    )


@pytest.mark.asyncio
async def test_terminal_observer_releases_and_restart_reconciles_exact_run(
    tmp_path,
) -> None:
    uow, store, hub, descriptor = await _capability_runtime(tmp_path)
    host = _host()
    profiles = _profiles()

    async def run_once(request_id: str, *, observe: bool) -> str:
        request = RunRequest("finish", request_id, "turn-1")
        run_id = root_run_identity(
            host.session_id, request.request_id, request.turn_id
        )[1].run_id
        await hub.snapshot_and_acquire_lease(
            run_id=run_id,
            root_run_id=run_id,
            scope=CapabilityScope.for_run(run_id),
        )

        async def terminal_observer(record, _event):
            await hub.release_run_leases(record.run_id)

        driver = _TerminalDriver()
        kernel = RunKernel(
            uow=uow,
            router=None,
            profiles=profiles,
            root_profile_key="agent.general",
            drivers=driver_catalog(
                (RegisteredDriver("react", driver, durable_from_start=True),)
            ),
            terminal_observer=terminal_observer if observe else None,
        )
        handle = await kernel.start(request, host)
        active = kernel._live.get(handle.ref.run_id)
        assert active is not None and active.task is not None
        await active.task
        return run_id

    observed_run = await run_once("request-observed", observe=True)
    assert await store.active_snapshot_refs_for_run(observed_run) == ()

    crashed_run = await run_once("request-crash-window", observe=False)
    assert await store.active_snapshot_refs_for_run(crashed_run)
    assert await hub.reconcile_terminal_run_leases() == 1
    assert await store.active_snapshot_refs_for_run(crashed_run) == ()
    assert await hub.version_can_be_collected(
        pack_id=descriptor.capability_id,
        version=descriptor.version,
        manifest_hash=descriptor.manifest_hash,
    )

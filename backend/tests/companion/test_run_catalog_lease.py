# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from deskpet.capabilities.contracts import CapabilityScope
from deskpet.capabilities.platform import CapabilityPlatform
from deskpet.capabilities.run_catalog import (
    SqliteRunCatalogLeasePreparer,
    _capture_prepared_tool_set,
)
from deskpet.capabilities.store import CapabilityStore
from deskpet.execution.contracts import fingerprint_json
from deskpet.tools.build_identity import ExecutionBuildIdentity
from deskpet.tools.capabilities import (
    ToolCapabilityResolver,
    ToolEligibilityContext,
    ToolExposureIntent,
)
from deskpet.tools.registry import ToolRegistry, tool_spec_fingerprint
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
        stable_handler_id="fixture.read.v1",
        execution_build_identity=ExecutionBuildIdentity(
            provider="core",
            handler_id="fixture.read.v1",
            build_digest="a" * 64,
            sources_manifest_hash="b" * 64,
            artifacts=(("fixture.py", "c" * 64),),
        ),
    )
    return registry


def test_capture_reports_all_selected_tools_missing_build_identity() -> None:
    registry = ToolRegistry()
    schema = {
        "description": "fixture",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    }
    for name in ("missing_b", "missing_a"):
        registry.register(
            name,
            "core",
            {"name": name, **schema},
            _handler,
        )
    prepared = (
        ToolCapabilityResolver(registry)
        .resolve_draft(
            ToolExposureIntent(
                required_direct_names=("missing_b", "missing_a")
            ),
            eligibility=ToolEligibilityContext(
                session_id="session-missing",
                request_id="request-missing",
                task_type="chat",
            ),
        )
        .finalize(scope_id="scope-missing")
    )
    snapshot = registry.catalog_snapshot()

    with pytest.raises(
        RuntimeError,
        match="prepared_tool_build_identity_missing:missing_a,missing_b",
    ):
        _capture_prepared_tool_set(
            prepared,
            registry=registry,
            expected_external_ref="external-ref",
            expected_registry_revision=snapshot.revision,
        )


@pytest.mark.asyncio
async def test_run_catalog_lease_binds_and_releases_in_execution_transactions(
    tmp_path,
) -> None:
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
        process_instance_id="process-fixture",
    )
    prepared_tools = (
        ToolCapabilityResolver(registry)
        .resolve_draft(
            ToolExposureIntent(required_direct_names=("fixture_read",)),
            eligibility=ToolEligibilityContext(
                session_id="session-1",
                request_id="request-1",
                task_type="chat",
            ),
        )
        .finalize(scope_id="scope-1")
    )

    lease = await platform.prepare_run_catalog_lease(
        scope=CapabilityScope.for_run("run-1", user_key="owner-1"),
        owner_key="owner-1",
        prepared_tool_set=prepared_tools,
        prepared_tool_set_fingerprint="d" * 64,
        run_id="run-1",
        root_run_id="run-1",
        request_id="request-1",
        turn_id="turn-1",
        owner_operation_id="run-start:run-1:1",
    )
    with pytest.raises(TypeError):
        lease.prepared_tool_set_envelope["external_ref"] = "tampered"
    exact_tools = lease.prepared_tool_set_envelope["exact_tools"]
    assert isinstance(exact_tools, list)
    with pytest.raises(TypeError):
        exact_tools[0]["name"] = "tampered"
    assert (
        fingerprint_json(lease.prepared_tool_set_envelope)
        == lease.prepared_tool_set_capture_hash
    )
    assert len(lease.lease_entries) == lease.expected_entry_count
    with pytest.raises(TypeError):
        lease.lease_entries[0]["entry_kind"] = "tampered"
    assert registry._snapshot_spec_leases

    start = SimpleNamespace(
        run_id="run-1",
        start_fingerprint="e" * 64,
        capability_lease_intent_ref=lease.lease_intent_id,
        capability_lease_intent_hash=lease.lease_intent_hash,
    )
    async with store.write_transaction() as db:
        receipt = await lease.start_commit_extension.apply_start_commit(
            uow.bind(db),
            spec=SimpleNamespace(run_id="run-1"),
            start_snapshot=start,
        )
    assert receipt.ref == lease.lease_intent_id
    assert await lease.after_start_handshake.activate_after_start(
        SimpleNamespace(ref=SimpleNamespace(run_id="run-1")),
        start_snapshot=start,
        extension_receipts=(receipt,),
    )
    await platform.require_run_catalog_ready("run-1")

    async with store.write_transaction() as db:
        terminal_receipt = (
            await lease.terminal_commit_extension.apply_terminal_commit(
                uow.bind(db),
                record=SimpleNamespace(ref=SimpleNamespace(run_id="run-1")),
                terminal_event=SimpleNamespace(),
            )
        )
    await lease.after_terminal_cleanup.cleanup_after_terminal(
        SimpleNamespace(ref=SimpleNamespace(run_id="run-1")),
        SimpleNamespace(),
        extension_receipts=(terminal_receipt,),
    )
    assert registry._snapshot_spec_leases == {}

    async with store.read_connection() as db:
        row = await (
            await db.execute(
                """SELECT status FROM capability_snapshot_lease_intents
                   WHERE lease_intent_id=?""",
                (lease.lease_intent_id,),
            )
        ).fetchone()
    assert row["status"] == "released"
    await uow.close()


@pytest.mark.asyncio
async def test_sdk_root_catalog_activation_is_idempotent_after_durable_start(
    tmp_path,
) -> None:
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
        process_instance_id="process-sdk-root",
    )
    prepared_tools = (
        ToolCapabilityResolver(registry)
        .resolve_draft(
            ToolExposureIntent(required_direct_names=("fixture_read",)),
            eligibility=ToolEligibilityContext(
                session_id="session-sdk",
                request_id="request-sdk",
                task_type="host_control",
            ),
        )
        .finalize(scope_id="scope-sdk")
    )
    lease = await platform.prepare_run_catalog_lease(
        scope=CapabilityScope.for_run("run-sdk", user_key="owner-sdk"),
        owner_key="owner-sdk",
        prepared_tool_set=prepared_tools,
        prepared_tool_set_fingerprint="d" * 64,
        run_id="run-sdk",
        root_run_id="run-sdk",
        request_id="request-sdk",
        turn_id="turn-sdk",
        owner_operation_id="verify:attempt-sdk",
    )
    values = dict(
        lease_intent_id=lease.lease_intent_id,
        lease_intent_hash=lease.lease_intent_hash,
        capability_snapshot_ref=lease.snapshot_ref,
        run_catalog_content_stamp=lease.run_catalog_content_stamp,
        run_id=lease.run_id,
        root_run_id=lease.root_run_id,
        start_fingerprint="e" * 64,
        prepared_lease=lease,
    )

    first, replay = await asyncio.gather(
        platform.activate_sdk_root_snapshot_after_start(**values),
        platform.activate_sdk_root_snapshot_after_start(**values),
    )

    assert first.lease_intent_id == replay.lease_intent_id == lease.lease_intent_id
    await first.require_ready()
    state = await store.read_snapshot_projection_state(lease.lease_intent_id)
    assert state is not None
    assert state.intent_status == "bound"
    assert state.projection_status == "ready"
    await platform.retire_run_catalog_ready(lease.run_id)
    await uow.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "tool_name"),
    (
        ("builtin", "fixture_read"),
        ("mcp:fixture", "mcp_fixture_fixture_read"),
    ),
)
async def test_candidate_to_single_capture_rejects_registry_or_mcp_exact_change(
    tmp_path,
    source: str,
    tool_name: str,
) -> None:
    registry = ToolRegistry()
    schema = {
        "name": tool_name,
        "description": "fixture",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    }
    registry.register(
        tool_name,
        "core",
        schema,
        _handler,
        source=source,
        replace_allowed=True,
        stable_handler_id=f"{source}.fixture.v1",
        execution_build_identity=ExecutionBuildIdentity(
            provider=source,
            handler_id=f"{source}.fixture.v1",
            build_digest="a" * 64,
            sources_manifest_hash="b" * 64,
            artifacts=(("fixture.py", "c" * 64),),
        ),
    )
    candidate = (
        ToolCapabilityResolver(registry)
        .resolve_draft(
            ToolExposureIntent(required_direct_names=(tool_name,)),
            eligibility=ToolEligibilityContext(
                session_id="session-race",
                request_id="request-race",
                task_type="chat",
            ),
        )
        .finalize(scope_id="scope-race")
    )

    # The hot Registry/MCP replacement lands after candidate construction but
    # before the one catalog capture. Schema/name stay identical; executable
    # bytes change, so a revision-only or name-only check would be unsafe.
    registry.register(
        tool_name,
        "core",
        schema,
        _handler,
        source=source,
        replace_allowed=True,
        stable_handler_id=f"{source}.fixture.v1",
        execution_build_identity=ExecutionBuildIdentity(
            provider=source,
            handler_id=f"{source}.fixture.v1",
            build_digest="d" * 64,
            sources_manifest_hash="e" * 64,
            artifacts=(("fixture.py", "f" * 64),),
        ),
    )

    uow = SqliteExecutionUnitOfWork(tmp_path / f"{tool_name}.db")
    await uow.initialize()
    store = CapabilityStore(uow)
    await store.initialize()
    platform = CapabilityPlatform(
        registry=registry,
        store=store,
        user_data_root=tmp_path / f"{tool_name}-capabilities",
        register_control_surface=False,
        first_party_pack_roots=(),
    )
    platform.run_catalog_lease_preparer = SqliteRunCatalogLeasePreparer(
        store=store,
        registry=registry,
        hub=platform.hub,
        process_instance_id=f"process-{tool_name}",
    )

    with pytest.raises(
        RuntimeError, match="prepared_tool_set_registry_revision_stale"
    ):
        await platform.prepare_run_catalog_lease(
            scope=CapabilityScope.for_run("run-race", user_key="owner-race"),
            owner_key="owner-race",
            prepared_tool_set=candidate,
            prepared_tool_set_fingerprint="d" * 64,
            run_id="run-race",
            root_run_id="run-race",
            request_id="request-race",
            turn_id="turn-race",
            owner_operation_id="run-start:run-race:1",
        )

    async with store.read_connection() as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM capability_run_catalog_snapshots"
            )
        ).fetchone()
    assert row[0] == 0
    await uow.close()


@pytest.mark.parametrize(
    ("source", "tool_name"),
    (
        ("builtin", "fixture_read"),
        ("mcp:fixture", "mcp_fixture_fixture_read"),
    ),
)
def test_single_capture_never_mixes_registry_snapshot_with_live_spec(
    source: str,
    tool_name: str,
) -> None:
    schema = {
        "name": tool_name,
        "description": "fixture",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    }

    def build_registry(build_digest: str) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            tool_name,
            "core",
            schema,
            _handler,
            source=source,
            stable_handler_id=f"{source}.fixture.v1",
            execution_build_identity=ExecutionBuildIdentity(
                provider=source,
                handler_id=f"{source}.fixture.v1",
                build_digest=build_digest,
                sources_manifest_hash="b" * 64,
                artifacts=(("fixture.py", "c" * 64),),
            ),
        )
        return registry

    old_registry = build_registry("a" * 64)
    candidate = (
        ToolCapabilityResolver(old_registry)
        .resolve_draft(
            ToolExposureIntent(required_direct_names=(tool_name,)),
            eligibility=ToolEligibilityContext(
                session_id="session-race",
                request_id="request-race",
                task_type="chat",
            ),
        )
        .finalize(scope_id="scope-race")
    )
    old_catalog = old_registry.catalog_snapshot()
    old_spec = next(spec for spec in old_catalog.specs if spec.name == tool_name)
    replacement_registry = build_registry("d" * 64)

    class RegistryDuringCapture:
        live_get_calls = 0

        def catalog_snapshot(self):
            return old_catalog

        def get(self, name):
            self.live_get_calls += 1
            return replacement_registry.get(name)

    racing_registry = RegistryDuringCapture()
    captured = _capture_prepared_tool_set(
        candidate,
        registry=racing_registry,
        expected_external_ref="external-ref",
        expected_registry_revision=old_catalog.revision,
    )

    assert racing_registry.live_get_calls == 0
    assert captured.envelope["exact_tools"][0][
        "tool_spec_fingerprint"
    ] == tool_spec_fingerprint(old_spec)


@pytest.mark.asyncio
async def test_platform_installs_all_first_party_skill_packs_without_scripts(
    tmp_path,
) -> None:
    repository_root = __file__
    from pathlib import Path

    pack_parent = Path(repository_root).resolve().parents[3] / "capability-packs"
    roots = tuple(
        path
        for path in sorted(pack_parent.glob("skill-*"))
        if (path / "deskpet-pack.json").is_file()
    )
    uow = SqliteExecutionUnitOfWork(tmp_path / "execution.db")
    await uow.initialize()
    store = CapabilityStore(uow)
    await store.initialize()
    platform = CapabilityPlatform(
        registry=ToolRegistry(),
        store=store,
        user_data_root=tmp_path / "capabilities",
        register_control_surface=False,
        first_party_pack_roots=roots,
    )
    try:
        initialized = await asyncio.wait_for(platform.initialize(), timeout=120.0)
        assert len(roots) == 17
        assert len(initialized.first_party_installs) == 17
        assert all(
            result.operation.status == "succeeded"
            for result in initialized.first_party_installs
        )
        assert not any(
            candidate
            for path in roots
            for candidate in path.rglob("script.py")
        )
    finally:
        await platform.shutdown()
        await uow.close()

from __future__ import annotations

import hashlib

import aiosqlite
import pytest

from deskpet.capabilities.contracts import CapabilityVersionDescriptor
from deskpet.capabilities.store import (
    CapabilityStore,
    CapabilityVersionRecord,
)
from deskpet.permissions import legacy_auto_mode
from deskpet.permissions.legacy_auto_mode import (
    LEGACY_AUTO_MODE_SOURCE_KEY,
    import_legacy_auto_mode_once,
)
from deskpet.permissions.task_grants import ResourceSelector, TaskGrant
from deskpet.workflows.store import SqliteExecutionUnitOfWork
from deskpet.workflows.store import schema as workflow_schema


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _version_record(tmp_path) -> CapabilityVersionRecord:
    descriptor = CapabilityVersionDescriptor(
        capability_id="godot",
        display_name="Godot",
        version="1.0.0",
        kind="function_tool",
        source="local:test@1",
        description="Godot test capability",
        aliases=("game",),
        logical_tool_ids=("project_create",),
        provider_tool_names=("godot__project_create",),
        permission_categories=("filesystem_write",),
        effect_kinds=("staged_file",),
        schema_hash=_hash("schema"),
        manifest_hash=_hash("manifest"),
        health="healthy",
    )
    return CapabilityVersionRecord(
        descriptor=descriptor,
        install_path=tmp_path / "capabilities" / "godot" / "1.0.0",
        validation_status="healthy",
        expected_tool_fingerprints=(_hash("godot__project_create"),),
        parent_version=None,
        parent_manifest_hash=None,
        derived_from_receipt_ref=None,
        created_at=100.0,
    )


@pytest.mark.asyncio
async def test_uow_activation_installs_capability_schema_and_persists_dml(
    tmp_path,
) -> None:
    path = tmp_path / "execution.db"
    uow = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    runtime = await uow.activate_runtime()
    assert runtime is not None
    assert runtime.phase == "open"

    store = CapabilityStore(uow, clock=lambda: 101.0)
    assert store.path.resolve() == uow.path.resolve()
    operation = await store.create_operation(
        operation_id="operation-1",
        idempotency_key="install-godot-1",
        kind="install",
        request={"source": "local:test@1"},
        requested_scope="user",
        requested_scope_key="default",
    )
    version = _version_record(tmp_path)
    await store.record_version(version)
    binding = await store.set_binding(
        scope="user",
        scope_key="default",
        pack_id=version.descriptor.capability_id,
        version=version.descriptor.version,
        manifest_hash=version.descriptor.manifest_hash,
        expected_generation=0,
    )
    policy = await store.compare_and_set_policy_mode(
        "auto",
        expected_generation=0,
    )
    grant = TaskGrant(
        task_grant_id="grant-1",
        root_run_id="root-1",
        principal_id="user-1",
        resource_selectors=(
            ResourceSelector.filesystem(tmp_path / "workspace", "write"),
        ),
        permission_categories=("filesystem_write",),
        effect_kinds=("staged_file",),
        source="policy:auto",
        policy_generation=policy.generation,
        expires_at=500.0,
        version=1,
    )
    await store.put_task_grant(grant)

    restarted_uow = SqliteExecutionUnitOfWork(path, clock=lambda: 200.0)
    restarted_runtime = await restarted_uow.activate_runtime()
    assert restarted_runtime is not None
    assert restarted_runtime.phase == "open"
    restarted = CapabilityStore(restarted_uow, clock=lambda: 201.0)
    await restarted.initialize()

    assert await restarted.get_operation("operation-1") == operation
    assert await restarted.get_binding("user", "default", "godot") == binding
    assert await restarted.get_policy_state() == policy
    assert await restarted.get_task_grant("grant-1") == grant

    async with aiosqlite.connect(path) as db:
        version_row = await (await db.execute("PRAGMA user_version")).fetchone()
        counts = await (
            await db.execute(
                """SELECT
                   (SELECT COUNT(*) FROM capability_operations),
                   (SELECT COUNT(*) FROM capability_bindings),
                   (SELECT COUNT(*) FROM authorization_policy_state),
                   (SELECT COUNT(*) FROM task_grants)"""
            )
        ).fetchone()
    assert version_row == (workflow_schema.WORKFLOW_SCHEMA_VERSION,)
    assert counts == (1, 1, 1, 1)


@pytest.mark.asyncio
async def test_v10_execution_database_upgrades_in_place_once(tmp_path) -> None:
    path = tmp_path / "existing-execution.db"
    original_version = workflow_schema.WORKFLOW_SCHEMA_VERSION
    workflow_schema.WORKFLOW_SCHEMA_VERSION = 10
    try:
        old_uow = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
        old_runtime = await old_uow.activate_runtime()
        assert old_runtime is not None
        assert old_runtime.phase == "open"
    finally:
        workflow_schema.WORKFLOW_SCHEMA_VERSION = original_version

    async with aiosqlite.connect(path) as db:
        old_version = await (await db.execute("PRAGMA user_version")).fetchone()
        old_capability_table = await (
            await db.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name='capability_schema_state'"""
            )
        ).fetchone()
    assert old_version == (10,)
    assert old_capability_table is None

    upgraded_uow = SqliteExecutionUnitOfWork(path, clock=lambda: 200.0)
    upgraded_runtime = await upgraded_uow.activate_runtime()
    assert upgraded_runtime is not None
    assert upgraded_runtime.phase == "open"
    upgraded_store = CapabilityStore(upgraded_uow, clock=lambda: 201.0)
    await upgraded_store.create_operation(
        operation_id="survives-restart",
        idempotency_key="survives-restart",
        kind="activate",
        request={"capability": "godot"},
    )

    restarted_uow = SqliteExecutionUnitOfWork(path, clock=lambda: 300.0)
    await restarted_uow.activate_runtime()
    restarted_store = CapabilityStore(restarted_uow)
    assert await restarted_store.get_operation("survives-restart") is not None

    async with aiosqlite.connect(path) as db:
        version_row = await (await db.execute("PRAGMA user_version")).fetchone()
        migration_count = await (
            await db.execute(
                """SELECT COUNT(*) FROM workflow_schema_migrations
                   WHERE version=11"""
            )
        ).fetchone()
        runtime_state = await (
            await db.execute(
                """SELECT generation,phase FROM execution_runtime_state
                   WHERE singleton_id=1"""
            )
        ).fetchone()
    assert version_row == (workflow_schema.WORKFLOW_SCHEMA_VERSION,)
    assert migration_count == (1,)
    assert runtime_state == (1, "open")


@pytest.mark.asyncio
async def test_v11_execution_database_adds_refresh_snapshots_once(
    tmp_path,
) -> None:
    path = tmp_path / "existing-v11.db"
    original_version = workflow_schema.WORKFLOW_SCHEMA_VERSION
    workflow_schema.WORKFLOW_SCHEMA_VERSION = 11
    try:
        await workflow_schema.initialize_workflow_db(path)
    finally:
        workflow_schema.WORKFLOW_SCHEMA_VERSION = original_version

    await workflow_schema.initialize_workflow_db(path)
    async with aiosqlite.connect(path) as db:
        version_row = await (await db.execute("PRAGMA user_version")).fetchone()
        table_row = await (
            await db.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table'
                     AND name='capability_refresh_snapshots'"""
            )
        ).fetchone()
        migration_count = await (
            await db.execute(
                """SELECT COUNT(*) FROM workflow_schema_migrations
                   WHERE version=12"""
            )
        ).fetchone()
    assert version_row == (workflow_schema.WORKFLOW_SCHEMA_VERSION,)
    assert table_row == ("capability_refresh_snapshots",)
    assert migration_count == (1,)


@pytest.mark.asyncio
async def test_legacy_auto_mode_imports_once_then_sqlite_is_authoritative(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "execution.db"
    legacy_path = tmp_path / "permissions_auto_mode.json"
    legacy_path.write_text('{"enabled": true}', encoding="utf-8")
    first_fingerprint = hashlib.sha256(legacy_path.read_bytes()).hexdigest()

    uow = SqliteExecutionUnitOfWork(path)
    await uow.activate_runtime()
    store = CapabilityStore(uow, clock=lambda: 100.0)
    imported = await import_legacy_auto_mode_once(store, legacy_path)

    assert imported.consumed_now
    assert imported.record.outcome == "imported"
    assert imported.record.source_fingerprint == first_fingerprint
    assert imported.record.imported_mode == "auto"
    assert (imported.policy_state.mode, imported.policy_state.generation) == (
        "auto",
        1,
    )

    sqlite_policy = await store.compare_and_set_policy_mode(
        "manual",
        expected_generation=1,
    )
    legacy_path.write_text('{\n  "enabled": true\n}', encoding="utf-8")

    def unexpected_legacy_read(_path):
        raise AssertionError("legacy source must not be read after it is consumed")

    monkeypatch.setattr(
        legacy_auto_mode,
        "_observe_legacy_auto_mode",
        unexpected_legacy_read,
    )
    restarted = CapabilityStore(SqliteExecutionUnitOfWork(path), clock=lambda: 200.0)
    ignored = await import_legacy_auto_mode_once(restarted, legacy_path)

    assert not ignored.consumed_now
    assert ignored.record == imported.record
    assert ignored.policy_state == sqlite_policy
    assert await restarted.get_legacy_authorization_import(
        LEGACY_AUTO_MODE_SOURCE_KEY
    ) == imported.record


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("legacy_contents", "expected_outcome", "expected_error"),
    [
        (None, "missing", None),
        ('{"enabled": "yes"}', "invalid", "legacy_auto_mode_invalid_payload"),
        ("not-json", "invalid", "legacy_auto_mode_invalid_json"),
    ],
)
async def test_unusable_legacy_auto_mode_is_consumed_without_later_override(
    tmp_path,
    legacy_contents,
    expected_outcome,
    expected_error,
) -> None:
    path = tmp_path / "execution.db"
    legacy_path = tmp_path / "permissions_auto_mode.json"
    if legacy_contents is not None:
        legacy_path.write_text(legacy_contents, encoding="utf-8")

    store = CapabilityStore(SqliteExecutionUnitOfWork(path), clock=lambda: 100.0)
    first = await import_legacy_auto_mode_once(store, legacy_path)
    assert first.record.outcome == expected_outcome
    assert first.record.error_code == expected_error
    assert (first.policy_state.mode, first.policy_state.generation) == ("manual", 0)

    legacy_path.write_text('{"enabled": true}', encoding="utf-8")
    restarted = CapabilityStore(
        SqliteExecutionUnitOfWork(path),
        clock=lambda: 200.0,
    )
    second = await import_legacy_auto_mode_once(restarted, legacy_path)
    assert not second.consumed_now
    assert second.record == first.record
    assert (second.policy_state.mode, second.policy_state.generation) == ("manual", 0)

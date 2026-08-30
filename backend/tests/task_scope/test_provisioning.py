from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from deskpet.memory.schema import (
    InitializeError,
    initialize_human_memory_program_state_db,
)
from deskpet.task_scope.provisioning import (
    TaskScopeProvisioner,
    TaskScopeProvisionError,
    TaskScopeProvisionRequest,
)
from deskpet.task_scope.store import CanonicalTaskScopeStore, TaskScopeConflict


async def _scope(
    db_path: Path, scope_id: str = "scope-1", title: str = "Memory SDK / Upgrade"
) -> None:
    await CanonicalTaskScopeStore(db_path).create_task_scope(
        task_scope_id=scope_id,
        subject="actor-1",
        title=title,
    )


def _managed(
    scope_id: str = "scope-1",
    title: str = "Memory SDK / Upgrade",
    key: str = "provision-1",
) -> TaskScopeProvisionRequest:
    return TaskScopeProvisionRequest(
        task_scope_id=scope_id,
        title=title,
        idempotency_key=key,
        mode="managed",
        provenance="host_managed_policy",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault_stage",
    [
        "after_reserved_commit",
        "before_filesystem_create",
        "after_staging_ready",
        "after_filesystem_create",
        "after_filesystem_ready_commit",
        "before_committed_receipt",
        "after_committed_receipt",
    ],
)
async def test_every_provision_boundary_restarts_to_same_directory(
    tmp_path: Path, fault_stage: str
) -> None:
    db_path = tmp_path / "state.db"
    managed_root = tmp_path / "managed"
    await _scope(db_path)
    provisioner = TaskScopeProvisioner(db_path, managed_workspace_root=managed_root)

    def crash(stage: str) -> None:
        if stage == fault_stage:
            raise RuntimeError(f"crash:{stage}")

    with pytest.raises(RuntimeError, match="crash"):
        await provisioner.provision(_managed(), fault_inject=crash)
    receipt = await provisioner.provision(_managed())
    duplicate = await provisioner.provision(_managed())
    assert duplicate == receipt
    assert Path(receipt.task_home).parent == managed_root.resolve()
    assert Path(receipt.task_home).name.startswith("Memory-SDK-Upgrade--")
    assert (Path(receipt.task_home) / ".simple-harness-provision.json").is_file()
    with sqlite3.connect(db_path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM task_scope_provisions").fetchone()[0] == 1
        )
        assert (
            db.execute("SELECT COUNT(*) FROM task_scope_provision_receipts").fetchone()[
                0
            ]
            == 1
        )
        assert (
            db.execute("SELECT state FROM task_scope_provisions").fetchone()[0]
            == "committed"
        )
        assert db.execute(
            "SELECT COUNT(*) FROM task_workspace_binding_revisions"
        ).fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match="identity_immutable"):
            db.execute("UPDATE task_scope_provisions SET task_home='/tmp/other'")
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            db.execute("DELETE FROM task_scope_provisions")
    assert (
        len([path for path in managed_root.iterdir() if not path.name.startswith(".")])
        == 1
    )


@pytest.mark.asyncio
async def test_default_root_and_duplicate_titles_get_stable_distinct_homes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    home = tmp_path / "home"
    home.mkdir()
    await _scope(db_path, "scope-a", "同名任务")
    await _scope(db_path, "scope-b", "同名任务")
    provisioner = TaskScopeProvisioner(db_path, home_directory=home)
    first = await provisioner.provision(_managed("scope-a", "同名任务", "key-a"))
    second = await provisioner.provision(_managed("scope-b", "同名任务", "key-b"))
    assert Path(first.task_home).parent == home / "SimpleHarnessWorkSpace"
    assert Path(second.task_home).parent == home / "SimpleHarnessWorkSpace"
    assert first.task_home != second.task_home


@pytest.mark.asyncio
async def test_explicit_path_requires_trusted_existing_real_directory(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    await _scope(db_path)
    provisioner = TaskScopeProvisioner(db_path, app_data_root=tmp_path / "app-data")
    missing = tmp_path / "missing"
    request = TaskScopeProvisionRequest(
        task_scope_id="scope-1",
        title="Memory SDK / Upgrade",
        idempotency_key="explicit-1",
        mode="explicit",
        provenance="trusted_user_selection",
        explicit_path=str(missing),
    )
    with pytest.raises(TaskScopeProvisionError, match="explicit_path_not_found"):
        await provisioner.provision(request)
    with sqlite3.connect(db_path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM task_scope_provisions").fetchone()[0] == 0
        )
    with pytest.raises(TaskScopeProvisionError, match="explicit_provenance_rejected"):
        TaskScopeProvisionRequest(
            task_scope_id="scope-1",
            title="Memory SDK / Upgrade",
            idempotency_key="bad-provenance",
            mode="explicit",
            provenance="host_managed_policy",
            explicit_path=str(tmp_path),
        )
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    symlink_request = TaskScopeProvisionRequest(
        task_scope_id="scope-1",
        title="Memory SDK / Upgrade",
        idempotency_key="explicit-symlink",
        mode="explicit",
        provenance="trusted_project_picker",
        explicit_path=str(alias),
    )
    with pytest.raises(TaskScopeProvisionError, match="path_symlink_rejected"):
        await provisioner.provision(symlink_request)
    too_broad = TaskScopeProvisionRequest(
        task_scope_id="scope-1",
        title="Memory SDK / Upgrade",
        idempotency_key="explicit-root",
        mode="explicit",
        provenance="trusted_user_selection",
        explicit_path=str(Path(Path.cwd().anchor)),
    )
    with pytest.raises(TaskScopeProvisionError, match="explicit_path_too_broad"):
        await provisioner.provision(too_broad)


@pytest.mark.asyncio
async def test_explicit_project_metadata_or_app_data_is_separate_from_workspace_binding(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    first_db = tmp_path / "first.db"
    await _scope(first_db)
    first = await TaskScopeProvisioner(
        first_db, app_data_root=tmp_path / "app-1"
    ).provision(
        TaskScopeProvisionRequest(
            task_scope_id="scope-1",
            title="Memory SDK / Upgrade",
            idempotency_key="explicit-project",
            mode="explicit",
            provenance="trusted_project_picker",
            explicit_path=str(project),
        )
    )
    assert first.metadata_location == "project"
    assert (
        Path(first.task_home) == project / ".simple-harness" / "task-scopes" / "scope-1"
    )
    assert first.proposed_workspace_root == str(project.resolve())
    assert first.task_home != first.proposed_workspace_root

    second_db = tmp_path / "second.db"
    await _scope(second_db)
    app_root = tmp_path / "app-2"
    second = await TaskScopeProvisioner(second_db, app_data_root=app_root).provision(
        TaskScopeProvisionRequest(
            task_scope_id="scope-1",
            title="Memory SDK / Upgrade",
            idempotency_key="explicit-app-data",
            mode="explicit",
            provenance="trusted_user_selection",
            explicit_path=str(project),
            allow_project_metadata=False,
        )
    )
    assert second.metadata_location == "app_data"
    assert Path(second.task_home).parent == app_root.resolve()
    assert second.proposed_workspace_root == str(project.resolve())


@pytest.mark.asyncio
async def test_permission_failure_is_retryable_and_never_partial_authority(
    tmp_path: Path, monkeypatch
) -> None:
    import deskpet.task_scope.provisioning as module

    db_path = tmp_path / "state.db"
    await _scope(db_path)
    provisioner = TaskScopeProvisioner(
        db_path, managed_workspace_root=tmp_path / "managed"
    )
    original = module._write_marker_at

    def denied(path, value):
        raise PermissionError("denied")

    monkeypatch.setattr(module, "_write_marker_at", denied)
    with pytest.raises(TaskScopeProvisionError, match="filesystem_permission_denied"):
        await provisioner.provision(_managed())
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT state,failure_code FROM task_scope_provisions"
        ).fetchone() == (
            "failed_retryable",
            "filesystem_permission_denied",
        )
        assert (
            db.execute("SELECT COUNT(*) FROM task_scope_provision_receipts").fetchone()[
                0
            ]
            == 0
        )
    monkeypatch.setattr(module, "_write_marker_at", original)
    assert (await provisioner.provision(_managed())).task_scope_id == "scope-1"


@pytest.mark.asyncio
async def test_explicit_broken_task_home_symlink_cannot_escape_selected_root(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    metadata = project / ".simple-harness" / "task-scopes"
    metadata.mkdir(parents=True)
    outside.mkdir()
    redirect = metadata / "scope-1"
    redirect.symlink_to(outside / "redirected-home", target_is_directory=True)
    assert redirect.is_symlink() and not redirect.exists()
    await _scope(db_path)

    request = TaskScopeProvisionRequest(
        task_scope_id="scope-1",
        title="Memory SDK / Upgrade",
        idempotency_key="explicit-broken-link",
        mode="explicit",
        provenance="trusted_user_selection",
        explicit_path=str(project),
    )
    with pytest.raises(TaskScopeProvisionError, match="path_symlink_rejected"):
        await TaskScopeProvisioner(db_path).provision(request)
    assert not (outside / "redirected-home").exists()
    with sqlite3.connect(db_path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM task_scope_provisions").fetchone()[0] == 0
        )
        assert (
            db.execute("SELECT COUNT(*) FROM task_scope_provision_receipts").fetchone()[
                0
            ]
            == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["managed", "explicit"])
async def test_root_object_replacement_before_publish_never_commits_receipt(
    tmp_path: Path, mode: str
) -> None:
    db_path = tmp_path / "state.db"
    root = tmp_path / "root"
    moved = tmp_path / "root-original"
    root.mkdir()
    await _scope(db_path)
    if mode == "managed":
        provisioner = TaskScopeProvisioner(db_path, managed_workspace_root=root)
        request = _managed()
    else:
        provisioner = TaskScopeProvisioner(db_path)
        request = TaskScopeProvisionRequest(
            task_scope_id="scope-1",
            title="Memory SDK / Upgrade",
            idempotency_key="explicit-replaced-root",
            mode="explicit",
            provenance="trusted_project_picker",
            explicit_path=str(root),
        )
    replaced = False

    def replace_root(stage: str) -> None:
        nonlocal replaced
        if stage == "after_staging_ready" and not replaced:
            root.rename(moved)
            root.mkdir()
            replaced = True

    with pytest.raises(TaskScopeProvisionError, match="provision_root_identity_drift"):
        await provisioner.provision(request, fault_inject=replace_root)
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT state FROM task_scope_provisions").fetchone()[0] == (
            "failed_retryable"
        )
        assert (
            db.execute("SELECT COUNT(*) FROM task_scope_provision_receipts").fetchone()[
                0
            ]
            == 0
        )
    with pytest.raises(TaskScopeProvisionError, match="provision_root_identity_drift"):
        await provisioner.provision(request)


@pytest.mark.asyncio
async def test_missing_anchored_platform_primitives_fail_before_reservation(
    tmp_path: Path, monkeypatch
) -> None:
    import deskpet.task_scope.provisioning as module

    db_path = tmp_path / "state.db"
    managed_root = tmp_path / "managed"
    await _scope(db_path)

    def unsupported() -> None:
        raise TaskScopeProvisionError("anchored_materialization_unsupported")

    monkeypatch.setattr(
        module, "_require_anchored_materialization_support", unsupported
    )
    with pytest.raises(
        TaskScopeProvisionError, match="anchored_materialization_unsupported"
    ):
        await TaskScopeProvisioner(
            db_path, managed_workspace_root=managed_root
        ).provision(_managed())
    assert not managed_root.exists()
    with sqlite3.connect(db_path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM task_scope_provisions").fetchone()[0] == 0
        )


@pytest.mark.asyncio
async def test_idempotency_conflict_cannot_allocate_second_path(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    await _scope(db_path)
    provisioner = TaskScopeProvisioner(
        db_path, managed_workspace_root=tmp_path / "managed"
    )
    first = await provisioner.provision(_managed())
    with pytest.raises(TaskScopeConflict, match="provision_idempotency_conflict"):
        await provisioner.provision(_managed(key="different-key"))
    assert (
        len(
            [
                path
                for path in Path(first.task_home).parent.iterdir()
                if not path.name.startswith(".")
            ]
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["before_task_scope_provision_commit", "after_task_scope_provision_commit"]
)
async def test_v37_migration_fault_restarts_with_one_marker(
    tmp_path: Path, stage: str
) -> None:
    db_path = tmp_path / "state.db"

    def crash(value: str) -> None:
        if value == stage:
            raise RuntimeError(f"crash:{stage}")

    with pytest.raises(InitializeError):
        await initialize_human_memory_program_state_db(db_path, fault_inject=crash)
    await initialize_human_memory_program_state_db(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 38
        assert (
            db.execute("SELECT COUNT(*) FROM task_scope_provision_marker").fetchone()[0]
            == 1
        )

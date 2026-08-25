from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3
import uuid
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest

from deskpet.memory.migrator import DEFAULT_MIGRATIONS_DIR, ensure_v9
from deskpet.memory.project_session_upgrade import run_project_session_upgrade
from deskpet.memory.schema import InitializeError, initialize_state_db
from deskpet.session.project_binding import (
    ProjectBindingService,
    ProjectBoundWorkspace,
    ProjectMissingWorkspace,
    ProjectSessionError,
    ProjectlessWorkspace,
    SessionCreationService,
    resolve_registration,
)
from scripts.restore_state_db_backup import RestoreRefused, restore_state_db


async def _ready_db(path: Path) -> None:
    await initialize_state_db(path)


@pytest.mark.asyncio
async def test_v32_schema_is_atomic_and_binding_is_immutable(tmp_path: Path) -> None:
    db_path = tmp_path.resolve() / "state.db"
    await initialize_state_db(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 32
        assert db.execute(
            "SELECT phase FROM project_session_backfill_state"
        ).fetchone()[0] == "completed"
        sid, pid, now = str(uuid.uuid4()), str(uuid.uuid4()), 1.0
        db.execute("INSERT INTO sessions(id,created_at) VALUES(?,?)", (sid, now))
        db.execute(
            "INSERT INTO projects VALUES(?,?,?,?,?,1,?,?,?)",
            (pid, "p", str(tmp_path), "folder", "identity", now, now, now),
        )
        db.execute(
            "INSERT INTO session_project_bindings(session_id,project_id,execution_kind,binding_version,created_at) "
            "VALUES(?,?,'project_root',1,?)", (sid, pid, now),
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE session_project_bindings SET project_id=? WHERE session_id=?", (pid, sid))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM session_project_bindings WHERE session_id=?", (sid,))


def test_registration_git_root_dedupe_identity_and_explicit_folder(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "repo"
    child = root / "src"
    child.mkdir(parents=True)
    import subprocess
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    default = resolve_registration(str(child), "git_root")
    explicit = resolve_registration(str(child), "selected_folder")
    alias = root / "alias"
    alias.symlink_to(child, target_is_directory=True)
    assert default.canonical_root == str(root)
    assert default.root_kind == "git"
    assert explicit.canonical_root == str(child)
    assert resolve_registration(str(alias), "selected_folder").filesystem_identity == explicit.filesystem_identity


@pytest.mark.asyncio
async def test_atomic_create_replay_projectless_explicit_and_delete_terminal(tmp_path: Path) -> None:
    db_path = tmp_path.resolve() / "state.db"
    await _ready_db(db_path)
    binding_service = ProjectBindingService(db_path)
    creation = SessionCreationService(binding_service)
    project_dir = tmp_path.resolve() / "project"
    execution_dir = tmp_path.resolve() / "worktree"
    project_dir.mkdir(); execution_dir.mkdir()
    project, created = await binding_service.register_project(str(project_dir), "selected_folder")
    assert created
    plain = await creation.create_conversation_session(request_id="plain-1")
    assert isinstance(await binding_service.resolve_session(plain["session"]["session_id"]), ProjectlessWorkspace)
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT provider_id,preferred_model,binding_epoch FROM code_session_provider "
            "WHERE base_session_id=?",
            (plain["session"]["session_id"],),
        ).fetchone() == (None, None, 1)
    creation_with_production_validator = SessionCreationService(
        binding_service,
        provider_binding_validator=lambda *_args: (_ for _ in ()).throw(
            AssertionError("NULL provider binding must not be validated")
        ),
    )
    result = await creation_with_production_validator.create_conversation_session(
        request_id="create-1", project_id=project.project_id,
        source_session_id=plain["session"]["session_id"],
        execution_kind="explicit", execution_root=str(execution_dir),
    )
    replay = await creation.create_conversation_session(
        request_id="create-1", project_id=project.project_id,
        source_session_id=plain["session"]["session_id"],
        execution_kind="explicit", execution_root=str(execution_dir),
    )
    assert replay["replayed"] is True
    assert replay["session"]["session_id"] == result["session"]["session_id"]
    resolution = await binding_service.resolve_session(result["session"]["session_id"])
    assert isinstance(resolution, ProjectBoundWorkspace)
    assert resolution.project_root == str(project_dir)
    assert resolution.effective_root == str(execution_dir)
    first_handoff = await binding_service.claim_handoff(
        result["session"]["session_id"], "run-first"
    )
    assert first_handoff and first_handoff["source_session_id"] == plain["session"]["session_id"]
    assert await binding_service.claim_handoff(
        result["session"]["session_id"], "run-second"
    ) is None
    with pytest.raises(ProjectSessionError, match="request_id_conflict"):
        await creation.create_conversation_session(request_id="create-1", project_id=None)
    await creation.mark_deleted(result["session"]["session_id"])
    with sqlite3.connect(db_path) as db:
        deleted_epoch = db.execute(
            "SELECT epoch FROM session_delivery_state WHERE session_id=?",
            (result["session"]["session_id"],),
        ).fetchone()[0]
    await creation.mark_deleted(result["session"]["session_id"])
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT epoch FROM session_delivery_state WHERE session_id=?",
            (result["session"]["session_id"],),
        ).fetchone()[0] == deleted_epoch
    with pytest.raises(ProjectSessionError, match="session_deleted"):
        await creation.create_conversation_session(
            request_id="create-1", project_id=project.project_id,
            source_session_id=plain["session"]["session_id"],
            execution_kind="explicit", execution_root=str(execution_dir),
        )


@pytest.mark.asyncio
async def test_create_commits_owner_route_outbox_and_receipt_as_one_unit(
    tmp_path: Path,
) -> None:
    db_path = tmp_path.resolve() / "state.db"
    await _ready_db(db_path)
    from deskpet.memory.session_db import SessionDB

    session_db = SessionDB(db_path)
    await session_db.initialize()
    frozen = SimpleNamespace(
        owner=SimpleNamespace(profile_id="profile-1", profile_generation=1),
        binding_epoch=2,
    )
    service = ProjectBindingService(db_path, write_lock=session_db._write_lock)
    creation = SessionCreationService(
        service,
        session_db=session_db,
        owner_identity_resolver=lambda: frozen,
    )

    result = await creation.create_conversation_session(request_id="atomic-owner-1")
    sid = result["session"]["session_id"]
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT profile_id,profile_generation,binding_epoch,status "
            "FROM companion_session_owners WHERE session_id=?",
            (sid,),
        ).fetchone() == ("profile-1", 1, 2, "active")
        assert db.execute(
            "SELECT target_session_id,route_version,status FROM companion_projection_routes "
            "WHERE profile_id='profile-1' AND profile_generation=1"
        ).fetchone() == (sid, 1, "active")
        assert db.execute(
            "SELECT target_session_id,status FROM companion_projection_route_outbox"
        ).fetchone() == (sid, "pending")
        assert db.execute(
            "SELECT session_id,lifecycle FROM session_creation_receipts "
            "WHERE request_id='atomic-owner-1'"
        ).fetchone() == (sid, "active")

    project_root = tmp_path / "owner-project"
    explicit_root = tmp_path / "owner-explicit"
    project_root.mkdir(); explicit_root.mkdir()
    project, _ = await service.register_project(str(project_root), "selected_folder")
    explicit = await creation.create_conversation_session(
        request_id="atomic-explicit-replay",
        project_id=project.project_id,
        execution_kind="explicit",
        execution_root=str(explicit_root),
    )
    frozen.binding_epoch = 3
    explicit_root.rename(tmp_path / "owner-explicit-moved")
    replay = await creation.create_conversation_session(
        request_id="atomic-explicit-replay",
        project_id=project.project_id,
        execution_kind="explicit",
        execution_root=str(explicit_root),
    )
    assert replay["replayed"] is True
    assert replay["session"]["session_id"] == explicit["session"]["session_id"]

    with sqlite3.connect(db_path) as db:
        db.execute(
            "UPDATE code_session_provider SET provider_id=?,preferred_model=?,model_params=?,"
            "provider_incarnation_id=?,provider_config_revision=?,updated_at=? "
            "WHERE base_session_id=?",
            ("provider-1", "model-1", "{}", "incarnation-1", 7, 1.0, sid),
        )
    stale_provider = SessionCreationService(
        service,
        session_db=session_db,
        owner_identity_resolver=lambda: frozen,
        provider_binding_validator=lambda *_args: (_ for _ in ()).throw(
            RuntimeError("provider_binding_stale")
        ),
    )
    with pytest.raises(ProjectSessionError, match="provider_binding_stale"):
        await stale_provider.create_conversation_session(
            request_id="stale-provider-create", source_session_id=sid
        )

    def fail_after_all_writes(stage: str):
        if stage == "before_receipt":
            raise RuntimeError("fault_after_route_outbox")

    failing = SessionCreationService(
        service,
        session_db=session_db,
        owner_identity_resolver=lambda: frozen,
        fault_inject=fail_after_all_writes,
    )
    with pytest.raises(RuntimeError, match="fault_after_route_outbox"):
        await failing.create_conversation_session(request_id="atomic-owner-fail")
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT count(*) FROM session_creation_receipts "
            "WHERE request_id='atomic-owner-fail'"
        ).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    [
        "after_session_core",
        "after_owner_route",
        "after_binding_provider",
        "before_receipt",
        "after_receipt",
        "before_commit",
    ],
)
async def test_creation_precommit_fault_matrix_leaves_no_rows(
    tmp_path: Path, stage: str
) -> None:
    db_path = tmp_path.resolve() / "state.db"
    await _ready_db(db_path)
    from deskpet.memory.session_db import SessionDB

    session_db = SessionDB(db_path)
    await session_db.initialize()
    root = tmp_path / "fault-project"
    root.mkdir()
    binding_service = ProjectBindingService(
        db_path, write_lock=session_db._write_lock
    )
    project, _ = await binding_service.register_project(
        str(root), "selected_folder"
    )
    with sqlite3.connect(db_path) as db:
        catalog_revision_before = db.execute(
            "SELECT catalog_revision FROM project_session_catalog_state WHERE singleton=1"
        ).fetchone()[0]
    frozen = SimpleNamespace(
        owner=SimpleNamespace(profile_id="fault-owner", profile_generation=1),
        binding_epoch=1,
    )

    def crash(point: str):
        if point == stage:
            raise RuntimeError(f"crash:{stage}")

    creation = SessionCreationService(
        binding_service,
        session_db=session_db,
        owner_identity_resolver=lambda: frozen,
        fault_inject=crash,
    )
    with pytest.raises(RuntimeError, match=f"crash:{stage}"):
        await creation.create_conversation_session(
            request_id=f"fault-{stage}", project_id=project.project_id
        )
    with sqlite3.connect(db_path) as db:
        for table in (
            "sessions",
            "session_catalog_entries",
            "session_delivery_state",
            "memory_user_bindings",
            "companion_session_owners",
            "companion_owner_scope_versions",
            "companion_projection_routes",
            "companion_projection_route_outbox",
            "session_project_bindings",
            "code_session_provider",
            "session_context_usage_state_v2",
            "session_creation_receipts",
        ):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert db.execute(
            "SELECT catalog_revision FROM project_session_catalog_state WHERE singleton=1"
        ).fetchone()[0] == catalog_revision_before


@pytest.mark.asyncio
async def test_concurrent_duplicate_creation_replays_or_rejects_intent_change(
    tmp_path: Path,
) -> None:
    db_path = tmp_path.resolve() / "state.db"
    await _ready_db(db_path)
    binding_service = ProjectBindingService(db_path)
    creation = SessionCreationService(binding_service)
    root = tmp_path / "duplicate-project"
    root.mkdir()
    project, _ = await binding_service.register_project(str(root), "selected_folder")

    first, second = await asyncio.gather(
        creation.create_conversation_session(
            request_id="duplicate-request", project_id=project.project_id
        ),
        creation.create_conversation_session(
            request_id="duplicate-request", project_id=project.project_id
        ),
    )
    assert first["session"]["session_id"] == second["session"]["session_id"]
    assert sorted((first["replayed"], second["replayed"])) == [False, True]
    with pytest.raises(ProjectSessionError, match="request_id_conflict"):
        await creation.create_conversation_session(request_id="duplicate-request")
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
        assert db.execute(
            "SELECT count(*) FROM session_creation_receipts"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_creation_after_commit_fault_replays_single_terminal_result(
    tmp_path: Path,
) -> None:
    db_path = tmp_path.resolve() / "state.db"
    await _ready_db(db_path)

    def crash(stage: str):
        if stage == "after_commit":
            raise RuntimeError("lost-ack")

    service = ProjectBindingService(db_path)
    crashing = SessionCreationService(service, fault_inject=crash)
    with pytest.raises(RuntimeError, match="lost-ack"):
        await crashing.create_conversation_session(request_id="lost-ack")
    replay = await SessionCreationService(service).create_conversation_session(
        request_id="lost-ack"
    )
    assert replay["replayed"] is True
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM session_creation_receipts").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_creation_holds_provider_mutation_lock_through_commit(
    tmp_path: Path,
) -> None:
    db_path = tmp_path.resolve() / "state.db"
    await _ready_db(db_path)
    provider_lock = asyncio.Lock()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def pause_before_commit(stage: str) -> None:
        if stage == "before_commit":
            entered.set()
            await release.wait()

    service = SessionCreationService(
        ProjectBindingService(db_path),
        provider_mutation_lock=provider_lock,
        fault_inject=pause_before_commit,
    )
    create_task = asyncio.create_task(
        service.create_conversation_session(request_id="provider-lock-race")
    )
    await entered.wait()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(provider_lock.acquire(), timeout=0.02)
    release.set()
    await create_task
    await asyncio.wait_for(provider_lock.acquire(), timeout=0.2)
    provider_lock.release()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["before_delete_writes", "after_delete_tombstones", "before_delete_commit"]
)
async def test_delete_precommit_fault_rolls_back_all_tombstones(
    tmp_path: Path, stage: str
) -> None:
    db_path = tmp_path.resolve() / "state.db"
    await _ready_db(db_path)

    def crash(point: str):
        if point == stage:
            raise RuntimeError(f"crash:{stage}")

    from deskpet.memory.session_db import SessionDB

    session_db = SessionDB(db_path)
    await session_db.initialize()
    owner = SimpleNamespace(
        owner=SimpleNamespace(profile_id="delete-owner", profile_generation=1),
        binding_epoch=1,
    )
    service = ProjectBindingService(db_path, write_lock=session_db._write_lock)
    normal = SessionCreationService(
        service, session_db=session_db, owner_identity_resolver=lambda: owner
    )
    created = await normal.create_conversation_session(request_id=f"delete-{stage}")
    sid = created["session"]["session_id"]
    with sqlite3.connect(db_path) as db:
        outbox_before = db.execute(
            "SELECT count(*) FROM companion_projection_route_outbox"
        ).fetchone()[0]
        revision_before = db.execute(
            "SELECT catalog_revision FROM project_session_catalog_state WHERE singleton=1"
        ).fetchone()[0]
    crashing = SessionCreationService(
        service,
        session_db=session_db,
        owner_identity_resolver=lambda: owner,
        fault_inject=crash,
    )
    with pytest.raises(RuntimeError, match=f"crash:{stage}"):
        await crashing.mark_deleted(sid)
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT deleted_at,epoch FROM session_delivery_state WHERE session_id=?", (sid,)
        ).fetchone() == (None, 0)
        assert db.execute(
            "SELECT lifecycle FROM session_creation_receipts WHERE session_id=?", (sid,)
        ).fetchone()[0] == "active"
        assert db.execute(
            "SELECT status FROM companion_session_owners WHERE session_id=?", (sid,)
        ).fetchone()[0] == "active"
        assert db.execute(
            "SELECT status FROM companion_projection_routes WHERE target_session_id=?",
            (sid,),
        ).fetchone()[0] == "active"
        assert db.execute(
            "SELECT count(*) FROM companion_projection_route_outbox"
        ).fetchone()[0] == outbox_before
        assert db.execute(
            "SELECT catalog_revision FROM project_session_catalog_state WHERE singleton=1"
        ).fetchone()[0] == revision_before


@pytest.mark.asyncio
async def test_delete_after_commit_fault_replay_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path.resolve() / "state.db"
    await _ready_db(db_path)

    def crash(stage: str):
        if stage == "after_delete_commit":
            raise RuntimeError("delete-ack-lost")

    from deskpet.memory.session_db import SessionDB

    session_db = SessionDB(db_path)
    await session_db.initialize()
    owner = SimpleNamespace(
        owner=SimpleNamespace(profile_id="delete-ack-owner", profile_generation=1),
        binding_epoch=1,
    )
    service = ProjectBindingService(db_path, write_lock=session_db._write_lock)
    normal = SessionCreationService(
        service, session_db=session_db, owner_identity_resolver=lambda: owner
    )
    created = await normal.create_conversation_session(request_id="delete-ack")
    sid = created["session"]["session_id"]
    with pytest.raises(RuntimeError, match="delete-ack-lost"):
        await SessionCreationService(
            service,
            session_db=session_db,
            owner_identity_resolver=lambda: owner,
            fault_inject=crash,
        ).mark_deleted(sid)
    with sqlite3.connect(db_path) as db:
        epoch = db.execute(
            "SELECT epoch FROM session_delivery_state WHERE session_id=?", (sid,)
        ).fetchone()[0]
    await normal.mark_deleted(sid)
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT epoch FROM session_delivery_state WHERE session_id=?", (sid,)
        ).fetchone()[0] == epoch
        assert db.execute(
            "SELECT status FROM companion_session_owners WHERE session_id=?", (sid,)
        ).fetchone()[0] == "tombstoned"
        assert db.execute(
            "SELECT status FROM companion_projection_routes WHERE target_session_id=?",
            (sid,),
        ).fetchone()[0] == "tombstoned"


@pytest.mark.asyncio
async def test_catalog_pages_zero_message_pinned_stale_and_relocate(tmp_path: Path) -> None:
    db_path = tmp_path.resolve() / "state.db"
    await _ready_db(db_path)
    service = ProjectBindingService(db_path)
    create = SessionCreationService(service)
    root = tmp_path.resolve() / "project"
    root.mkdir()
    project, _ = await service.register_project(str(root), "selected_folder")
    first = await create.create_conversation_session(request_id="s1", project_id=project.project_id)
    await create.create_conversation_session(request_id="s2", project_id=project.project_id)
    page = await service.list_session_page(scope_kind="project", project_id=project.project_id, limit=1,
                                           pinned_session_id=first["session"]["session_id"])
    assert len(page["items"]) == 1 and page["next_cursor"]
    assert page["pinned"]["session_id"] == first["session"]["session_id"]
    second_page = await service.list_session_page(
        scope_kind="project", project_id=project.project_id, limit=1,
        cursor=page["next_cursor"], pinned_session_id=first["session"]["session_id"],
    )
    assert second_page["pinned"]["session_id"] == first["session"]["session_id"]
    await create.create_conversation_session(request_id="s3", project_id=project.project_id)
    with pytest.raises(ProjectSessionError, match="stale_cursor"):
        await service.list_session_page(scope_kind="project", project_id=project.project_id,
                                        cursor=page["next_cursor"])
    current = {
        "kind": "project_bound", "session_id": first["session"]["session_id"],
        "project_id": project.project_id, "project_root": str(root),
        "effective_root": str(root), "execution_kind": "project_root",
        "project_identity": project.filesystem_identity,
        "execution_identity": project.filesystem_identity, "project_revision": 1,
    }
    forged_root = tmp_path / "forged-root"; forged_root.mkdir()
    with pytest.raises((RuntimeError, ProjectSessionError), match="workspace_binding_stale"):
        await service.admit_run(
            {**current, "project_root": str(forged_root), "effective_root": str(forged_root)},
            "run-forged",
        )
    await service.admit_run(current, "run-active")
    moved = tmp_path.resolve() / "moved"
    root.rename(moved)
    missing = await service.resolve_session(first["session"]["session_id"])
    assert isinstance(missing, ProjectMissingWorkspace)
    with pytest.raises(ProjectSessionError, match="project_runs_active"):
        await service.relocate_project(project.project_id, str(moved), 1)
    await service.release_run("run-active")
    relocated = await service.relocate_project(project.project_id, str(moved), 1)
    assert relocated.canonical_root == str(moved) and relocated.project_revision == 2
    other = tmp_path.resolve() / "other"; other.mkdir()
    with pytest.raises(ProjectSessionError, match="project_identity_mismatch"):
        await service.relocate_project(project.project_id, str(other), 2)


@pytest.mark.asyncio
async def test_zero_message_delete_bumps_catalog_once_and_stales_cursor(
    tmp_path: Path,
) -> None:
    db_path = tmp_path.resolve() / "state.db"
    await _ready_db(db_path)
    service = ProjectBindingService(db_path)
    creation = SessionCreationService(service)
    first = await creation.create_conversation_session(request_id="zero-delete-1")
    await creation.create_conversation_session(request_id="zero-delete-2")
    page = await service.list_session_page(scope_kind="projectless", limit=1)
    assert page["next_cursor"]
    with sqlite3.connect(db_path) as db:
        before = db.execute(
            "SELECT catalog_revision FROM project_session_catalog_state"
        ).fetchone()[0]
    await creation.mark_deleted(first["session"]["session_id"])
    with sqlite3.connect(db_path) as db:
        after = db.execute(
            "SELECT catalog_revision FROM project_session_catalog_state"
        ).fetchone()[0]
    assert after == before + 1
    with pytest.raises(ProjectSessionError, match="stale_cursor"):
        await service.list_session_page(
            scope_kind="projectless", cursor=page["next_cursor"]
        )


@pytest.mark.asyncio
async def test_startup_reconciles_only_run_claims_without_durable_start(
    tmp_path: Path,
) -> None:
    db_path = tmp_path.resolve() / "state.db"
    await _ready_db(db_path)
    service = ProjectBindingService(db_path)
    creation = SessionCreationService(service)
    source = await creation.create_conversation_session(request_id="source")
    root = tmp_path.resolve() / "project"
    root.mkdir()
    project, _ = await service.register_project(str(root), "selected_folder")
    target = await creation.create_conversation_session(
        request_id="target",
        project_id=project.project_id,
        source_session_id=source["session"]["session_id"],
    )
    resolution = await service.resolve_session(target["session"]["session_id"])
    assert isinstance(resolution, ProjectBoundWorkspace)
    await service.admit_run({**asdict(resolution), "kind": "project_bound"}, "orphan-run")
    assert await service.claim_handoff(target["session"]["session_id"], "orphan-run")

    reconciled = await service.reconcile_orphan_run_claims(
        lambda _run_id: asyncio.sleep(0, result=False)
    )
    assert reconciled == {"admissions_removed": 1, "handoffs_removed": 1}
    assert await service.claim_handoff(target["session"]["session_id"], "durable-run")

    retained = await service.reconcile_orphan_run_claims(
        lambda run_id: asyncio.sleep(0, result=run_id == "durable-run")
    )
    assert retained == {"admissions_removed": 0, "handoffs_removed": 0}


@pytest.mark.asyncio
async def test_v31_backfill_resumes_after_chunk_commit_and_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path.resolve() / "state.db"
    old_migrations = tmp_path.resolve() / "migrations-v31"
    old_migrations.mkdir()
    for source in DEFAULT_MIGRATIONS_DIR.glob("*.sql"):
        if not source.name.startswith("024_"):
            shutil.copy2(source, old_migrations / source.name)
    await ensure_v9(db_path, migrations_dir=old_migrations)
    root = tmp_path.resolve() / "legacy"; root.mkdir()
    valid_sid, missing_sid = str(uuid.uuid4()), str(uuid.uuid4())
    with sqlite3.connect(db_path) as db:
        db.executemany("INSERT INTO sessions(id,created_at) VALUES(?,1)", [(valid_sid,), (missing_sid,)])
        db.executemany(
            "INSERT INTO code_sessions(base_session_id,code_session_id,project_root,project_name,created_at,last_active_at) "
            "VALUES(?,?,?,?,1,1)",
            [(valid_sid, "code-a", str(root), "legacy"),
             (missing_sid, "code-b", str(root / "gone"), "gone")],
        )
    await initialize_state_db(db_path)
    with sqlite3.connect(db_path) as db:
        before = db.execute("SELECT outcome_digest FROM project_session_backfill_state").fetchone()[0]
        outcomes = dict(db.execute("SELECT base_session_id,outcome FROM project_session_backfill_outcomes"))
        assert outcomes == {valid_sid: "migrated", missing_sid: "missing"}
        assert db.execute("SELECT count(*) FROM session_project_bindings").fetchone()[0] == 1
    await run_project_session_upgrade(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT outcome_digest FROM project_session_backfill_state").fetchone()[0] == before
        assert db.execute("SELECT count(*) FROM session_project_bindings").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_semantic_upgrade_crash_resume_and_guarded_restore(tmp_path: Path) -> None:
    db_path = tmp_path.resolve() / "state.db"
    old_migrations = tmp_path.resolve() / "migrations-v31"
    old_migrations.mkdir()
    for source in DEFAULT_MIGRATIONS_DIR.glob("*.sql"):
        if not source.name.startswith("024_"):
            shutil.copy2(source, old_migrations / source.name)
    await ensure_v9(db_path, migrations_dir=old_migrations)
    sid = str(uuid.uuid4())
    root = tmp_path.resolve() / "legacy"; root.mkdir()
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO sessions(id,created_at) VALUES(?,1)", (sid,))
        db.execute(
            "INSERT INTO code_sessions(base_session_id,code_session_id,project_root,project_name,created_at,last_active_at) "
            "VALUES(?,?,?,?,1,1)", (sid, "code", str(root), "legacy"),
        )
    backup = tmp_path.resolve() / "state.db.pre-v32.bak"
    shutil.copy2(db_path, backup)
    await ensure_v9(db_path)
    fired = False
    def fault(point: str) -> None:
        nonlocal fired
        if point == "after_scan_commit" and not fired:
            fired = True
            raise RuntimeError("crash")
    with pytest.raises(RuntimeError, match="crash"):
        await run_project_session_upgrade(db_path, backup_path=backup, fault_inject=fault)
    await run_project_session_upgrade(db_path, backup_path=backup)
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT phase FROM project_session_backfill_state").fetchone()[0] == "completed"
        assert db.execute("SELECT count(*) FROM session_project_bindings").fetchone()[0] == 1
    backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    with pytest.raises(RestoreRefused, match="sha256"):
        restore_state_db(state_db=str(db_path), backup=str(backup),
                         expected_sha256="0" * 64, confirm="RESTORE-STATE-DB")
    quarantine = restore_state_db(state_db=str(db_path), backup=str(backup),
                                  expected_sha256=backup_hash, confirm="RESTORE-STATE-DB")
    assert quarantine.is_file()
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 31


@pytest.mark.asyncio
async def test_v32_startup_checkpoints_wal_and_validates_backup_manifest(
    tmp_path: Path,
) -> None:
    db_path = tmp_path.resolve() / "state.db"
    old_migrations = tmp_path.resolve() / "migrations-v31"
    old_migrations.mkdir()
    for source in DEFAULT_MIGRATIONS_DIR.glob("*.sql"):
        if not source.name.startswith("024_"):
            shutil.copy2(source, old_migrations / source.name)
    await ensure_v9(db_path, migrations_dir=old_migrations)
    sid = str(uuid.uuid4())
    writer = sqlite3.connect(db_path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("INSERT INTO sessions(id,created_at) VALUES(?,1)", (sid,))
    writer.commit()
    assert db_path.with_name(f"{db_path.name}-wal").exists()
    await initialize_state_db(db_path)
    writer.close()

    backups = sorted(tmp_path.glob("state.db.bak.*"))
    backups = [path for path in backups if not path.name.endswith(".manifest.json")]
    assert len(backups) == 1
    backup = backups[0]
    manifest_path = backup.with_name(f"{backup.name}.manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest == {
        "format": "simple_harness.state-db-backup.v1",
        "source_name": "state.db",
        "backup_name": backup.name,
        "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        "size_bytes": backup.stat().st_size,
        "user_version": 31,
    }
    with sqlite3.connect(backup) as snapshot:
        assert snapshot.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert snapshot.execute(
            "SELECT count(*) FROM sessions WHERE id=?", (sid,)
        ).fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault_stage",
    [
        "before_backup",
        "after_backup",
        "before_ddl_commit",
        "after_ddl_commit",
        "before_scan",
        "after_scan_commit",
        "after_chunk_commit",
        "before_completion_commit",
        "after_completion_commit",
    ],
)
async def test_v32_startup_fault_matrix_restores_then_restarts_exactly_once(
    tmp_path: Path, fault_stage: str
) -> None:
    db_path = tmp_path.resolve() / "state.db"
    old_migrations = tmp_path.resolve() / "migrations-v31"
    old_migrations.mkdir()
    for source in DEFAULT_MIGRATIONS_DIR.glob("*.sql"):
        if not source.name.startswith("024_"):
            shutil.copy2(source, old_migrations / source.name)
    await ensure_v9(db_path, migrations_dir=old_migrations)
    sid = str(uuid.uuid4())
    root = tmp_path.resolve() / "legacy"
    root.mkdir()
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO sessions(id,created_at) VALUES(?,1)", (sid,))
        db.execute(
            "INSERT INTO code_sessions(base_session_id,code_session_id,project_root,"
            "project_name,created_at,last_active_at) VALUES(?,?,?,?,1,1)",
            (sid, "code", str(root), "legacy"),
        )
    fired = False

    def crash(point: str) -> None:
        nonlocal fired
        if point == fault_stage and not fired:
            fired = True
            raise RuntimeError(f"crash:{fault_stage}")

    expected_error = (
        RuntimeError
        if fault_stage in {"before_backup", "after_backup"}
        else InitializeError
    )
    with pytest.raises(expected_error, match=f"crash:{fault_stage}"):
        await initialize_state_db(db_path, fault_inject=crash)
    await initialize_state_db(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 32
        assert db.execute(
            "SELECT phase FROM project_session_backfill_state"
        ).fetchone()[0] == "completed"
        assert db.execute(
            "SELECT count(*) FROM session_project_bindings WHERE session_id=?", (sid,)
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT count(*) FROM project_session_backfill_outcomes "
            "WHERE base_session_id=?", (sid,)
        ).fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault_stage",
    ["before_scan", "after_scan_commit", "after_chunk_commit", "after_completion_commit"],
)
async def test_semantic_upgrade_fault_boundaries_resume_exactly_once(
    tmp_path: Path, fault_stage: str
) -> None:
    db_path = tmp_path.resolve() / "state.db"
    old_migrations = tmp_path.resolve() / "migrations-v31"
    old_migrations.mkdir()
    for source in DEFAULT_MIGRATIONS_DIR.glob("*.sql"):
        if not source.name.startswith("024_"):
            shutil.copy2(source, old_migrations / source.name)
    await ensure_v9(db_path, migrations_dir=old_migrations)
    sid = str(uuid.uuid4())
    root = tmp_path.resolve() / "legacy"
    root.mkdir()
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO sessions(id,created_at) VALUES(?,1)", (sid,))
        db.execute(
            "INSERT INTO code_sessions(base_session_id,code_session_id,project_root,"
            "project_name,created_at,last_active_at) VALUES(?,?,?,?,1,1)",
            (sid, "code", str(root), "legacy"),
        )
    backup = tmp_path.resolve() / "state.db.pre-v32.bak"
    shutil.copy2(db_path, backup)
    await ensure_v9(db_path)
    fired = False

    def crash(point: str) -> None:
        nonlocal fired
        if point == fault_stage and not fired:
            fired = True
            raise RuntimeError(f"crash:{fault_stage}")

    with pytest.raises(RuntimeError, match=f"crash:{fault_stage}"):
        await run_project_session_upgrade(
            db_path, backup_path=backup, fault_inject=crash
        )
    await run_project_session_upgrade(db_path, backup_path=backup)
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT phase FROM project_session_backfill_state"
        ).fetchone()[0] == "completed"
        assert db.execute(
            "SELECT count(*) FROM session_project_bindings WHERE session_id=?", (sid,)
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT count(*) FROM project_session_backfill_outcomes "
            "WHERE base_session_id=?", (sid,)
        ).fetchone()[0] == 1

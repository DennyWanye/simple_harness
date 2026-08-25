from __future__ import annotations

import asyncio
import hashlib
import shutil
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest

from deskpet.memory.migrator import DEFAULT_MIGRATIONS_DIR, ensure_v9
from deskpet.memory.project_session_upgrade import run_project_session_upgrade
from deskpet.memory.schema import initialize_state_db
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
    result = await creation.create_conversation_session(
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
    with pytest.raises(ProjectSessionError, match="request_id_conflict"):
        await creation.create_conversation_session(request_id="create-1", project_id=None)
    await creation.mark_deleted(result["session"]["session_id"])
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

    async def fail_after_all_writes(_db, _sid, _intent):
        raise RuntimeError("fault_after_route_outbox")

    failing = SessionCreationService(
        service,
        session_db=session_db,
        owner_identity_resolver=lambda: frozen,
        tx_participants=(fail_after_all_writes,),
    )
    with pytest.raises(RuntimeError, match="fault_after_route_outbox"):
        await failing.create_conversation_session(request_id="atomic-owner-fail")
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT count(*) FROM session_creation_receipts "
            "WHERE request_id='atomic-owner-fail'"
        ).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1


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
    await create.create_conversation_session(request_id="s3", project_id=project.project_id)
    with pytest.raises(ProjectSessionError, match="stale_cursor"):
        await service.list_session_page(scope_kind="project", project_id=project.project_id,
                                        cursor=page["next_cursor"])
    moved = tmp_path.resolve() / "moved"
    root.rename(moved)
    missing = await service.resolve_session(first["session"]["session_id"])
    assert isinstance(missing, ProjectMissingWorkspace)
    relocated = await service.relocate_project(project.project_id, str(moved), 1)
    assert relocated.canonical_root == str(moved) and relocated.project_revision == 2
    other = tmp_path.resolve() / "other"; other.mkdir()
    with pytest.raises(ProjectSessionError, match="project_identity_mismatch"):
        await service.relocate_project(project.project_id, str(other), 2)


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

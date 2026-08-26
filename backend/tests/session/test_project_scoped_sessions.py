from __future__ import annotations

import asyncio
import ast
import os
import sqlite3
import uuid
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest

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
from deskpet.workflows.startup_recovery import activate_and_recover_workflows


async def _ready_db(path: Path) -> None:
    await initialize_state_db(path)


@pytest.mark.asyncio
async def test_v33_schema_reset_completes_and_binding_is_immutable(tmp_path: Path) -> None:
    db_path = tmp_path.resolve() / "state.db"
    await initialize_state_db(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 33
        assert db.execute(
            "SELECT phase FROM legacy_session_reset_state"
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
async def test_registration_folder_errors_and_macos_rename_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path.resolve() / "state.db"
    await _ready_db(db_path)
    service = ProjectBindingService(db_path)
    plain = tmp_path.resolve() / "plain"
    different = tmp_path.resolve() / "different"
    plain.mkdir(); different.mkdir()

    project, created = await service.register_project(str(plain), "selected_folder")
    assert created and project.root_kind == "folder"
    assert project.canonical_root == str(plain)
    with sqlite3.connect(db_path) as db:
        baseline = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]

    file_path = tmp_path.resolve() / "not-a-directory.txt"
    file_path.write_text("x", encoding="utf-8")
    deleted = tmp_path.resolve() / "deleted"
    deleted.mkdir(); deleted.rmdir()
    for invalid in ("", str(file_path), str(deleted)):
        with pytest.raises(ProjectSessionError):
            await service.register_project(invalid, "selected_folder")
    original_access = os.access
    monkeypatch.setattr(
        os,
        "access",
        lambda path, mode: False if Path(path) == plain else original_access(path, mode),
    )
    with pytest.raises(ProjectSessionError, match="path_unreadable"):
        resolve_registration(str(plain), "selected_folder")
    monkeypatch.undo()

    moved = tmp_path.resolve() / "plain-renamed"
    plain.rename(moved)
    renamed = resolve_registration(str(moved), "selected_folder")
    other = resolve_registration(str(different), "selected_folder")
    assert renamed.filesystem_identity == project.filesystem_identity
    assert other.filesystem_identity != project.filesystem_identity
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == baseline


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
    assert await service.list_deleted_session_ids() == (sid,)
    attempts: list[str] = []

    async def fail_once(session_id: str) -> None:
        attempts.append(session_id)
        if len(attempts) == 1:
            raise RuntimeError("workflow-db-unavailable")

    assert await service.reconcile_deleted_session_runs(fail_once) == {
        "attempted": 1,
        "completed": 0,
        "failed": 1,
    }
    assert await service.reconcile_deleted_session_runs(fail_once) == {
        "attempted": 1,
        "completed": 1,
        "failed": 0,
    }
    assert attempts == [sid, sid]


@pytest.mark.asyncio
async def test_startup_reconciles_deleted_sessions_before_any_workflow_recovery() -> None:
    events: list[str] = []

    class Bindings:
        async def reconcile_deleted_session_runs(self, cancel):
            events.append("delete-scan")
            await cancel("deleted-session")
            return {"attempted": 1, "completed": 1, "failed": 0}

    class Runner:
        async def recover_expired(self):
            events.append("recover-expired")
            return ["expired"]

    class Workflows:
        runner = Runner()

        async def cancel_runs_for_session(self, session_id: str, *, reason: str):
            assert session_id == "deleted-session"
            assert reason == "session_deleted_startup_reconcile"
            events.append("cancel-deleted")
            return []

        async def activate_runtime(self, *, required_runtime_identities):
            assert tuple(required_runtime_identities) == ("runtime-a",)
            events.append("activate")

    class Launcher:
        async def recover_open_decision_events(self):
            events.append("recover-decisions")
            return []

        async def recover_due_deliveries(self, *, recover_claimed: bool):
            assert recover_claimed
            events.append("recover-deliveries")
            return []

        async def recover_pending(self):
            events.append("recover-pending")
            return []

        def start_dispatcher(self):
            events.append("dispatcher")

    result = await activate_and_recover_workflows(
        project_bindings=Bindings(),
        workflow_service=Workflows(),
        workflow_launcher=Launcher(),
        required_runtime_identities=("runtime-a",),
    )
    assert events == [
        "delete-scan", "cancel-deleted", "activate", "recover-expired",
        "recover-decisions", "recover-deliveries", "recover-pending", "dispatcher",
    ]
    assert result["delete_reconcile"] == {
        "attempted": 1, "completed": 1, "failed": 0,
    }


def test_lifespan_uses_the_single_ordered_workflow_recovery_composition() -> None:
    """Lock the production wiring, not only the orchestration helper."""

    main_path = Path(__file__).resolve().parents[2] / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))
    lifespan = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "lifespan"
    )
    calls = [node for node in ast.walk(lifespan) if isinstance(node, ast.Call)]

    def call_name(call: ast.Call) -> str:
        if isinstance(call.func, ast.Name):
            return call.func.id
        if isinstance(call.func, ast.Attribute):
            return call.func.attr
        return ""

    names = [call_name(call) for call in calls]
    assert names.count("activate_and_recover_workflows") == 1
    assert not set(names).intersection(
        {
            "activate_runtime",
            "recover_expired",
            "recover_open_decision_events",
            "recover_due_deliveries",
            "recover_pending",
            "start_dispatcher",
        }
    )
    composition = next(
        call for call in calls if call_name(call) == "activate_and_recover_workflows"
    )
    assert {keyword.arg for keyword in composition.keywords} == {
        "project_bindings",
        "workflow_service",
        "workflow_launcher",
        "required_runtime_identities",
    }


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
async def test_session_rename_bumps_catalog_and_stales_cursor(tmp_path: Path) -> None:
    from deskpet.memory.session_db import SessionDB

    db_path = tmp_path.resolve() / "state.db"
    session_db = SessionDB(db_path)
    await session_db.initialize()
    service = ProjectBindingService(db_path, write_lock=session_db._write_lock)
    creation = SessionCreationService(service, session_db=session_db)
    first = await creation.create_conversation_session(request_id="rename-1")
    await creation.create_conversation_session(request_id="rename-2")
    page = await service.list_session_page(scope_kind="projectless", limit=1)
    assert page["next_cursor"]

    await session_db.set_session_title(first["session"]["session_id"], "Renamed")

    with pytest.raises(ProjectSessionError, match="stale_cursor"):
        await service.list_session_page(
            scope_kind="projectless", cursor=page["next_cursor"]
        )
    refreshed = await service.list_session_page(scope_kind="projectless", limit=50)
    renamed = next(
        item for item in refreshed["items"]
        if item["session_id"] == first["session"]["session_id"]
    )
    assert renamed["title"] == "Renamed"
    await session_db.close()


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

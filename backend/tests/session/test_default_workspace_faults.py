from __future__ import annotations

import hashlib
import platform
import json
import sqlite3
from pathlib import Path

import pytest

from deskpet.memory.schema import initialize_state_db
from deskpet.session.project_binding import ProjectBindingService, SessionCreationService


def _identity(path: Path) -> str:
    stat = path.stat()
    return hashlib.sha256(
        f"v1\0{platform.system().lower()}\0{stat.st_dev}\0{stat.st_ino}".encode()
    ).hexdigest()


@pytest.mark.asyncio
async def test_crash_after_mkdir_replays_owned_allocation(tmp_path: Path) -> None:
    documents = tmp_path / "Documents"
    documents.mkdir()
    db_path = tmp_path / "state.db"
    await initialize_state_db(db_path)
    bindings = ProjectBindingService(db_path)
    raised = False

    async def crash(stage: str) -> None:
        nonlocal raised
        if stage == "workspace_after_mkdir" and not raised:
            raised = True
            raise RuntimeError("crash")

    kwargs = dict(
        documents_root=str(documents), documents_identity=_identity(documents),
        allow_test_projectless=False,
    )
    with pytest.raises(RuntimeError, match="crash"):
        await SessionCreationService(bindings, fault_inject=crash, **kwargs).create_conversation_session(request_id="resume-1")
    result = await SessionCreationService(bindings, **kwargs).create_conversation_session(request_id="resume-1")
    assert result["session"]["workspace_kind"] == "automatic"
    assert len(list((documents / "SimpleHarnessProjects").iterdir())) == 1


@pytest.mark.asyncio
async def test_missing_host_documents_fails_without_session(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    await initialize_state_db(db_path)
    bindings = ProjectBindingService(db_path)
    with pytest.raises(Exception, match="host_documents_unavailable"):
        await SessionCreationService(bindings, allow_test_projectless=False).create_conversation_session(request_id="missing-1")


@pytest.mark.asyncio
async def test_legacy_migration_failure_is_recoverable_and_never_half_bound(tmp_path: Path) -> None:
    documents = tmp_path / "Documents"
    documents.mkdir()
    db_path = tmp_path / "state.db"
    await initialize_state_db(db_path)
    sid = "legacy-fault"
    import sqlite3
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO sessions(id,created_at) VALUES(?,1)", (sid,))
    bindings = ProjectBindingService(db_path)
    raised = False

    async def crash(stage: str) -> None:
        nonlocal raised
        if stage == "workspace_after_mkdir" and not raised:
            raised = True
            raise RuntimeError("migration-crash")

    kwargs = dict(
        documents_root=str(documents), documents_identity=_identity(documents),
        allow_test_projectless=False,
    )
    with pytest.raises(RuntimeError, match="migration-crash"):
        await SessionCreationService(bindings, fault_inject=crash, **kwargs).migrate_projectless_session(sid)
    assert (await bindings.resolve_session(sid)).kind == "projectless"
    assert await SessionCreationService(bindings, **kwargs).migrate_projectless_session(sid) is True
    assert (await bindings.resolve_session(sid)).kind == "project"


@pytest.mark.asyncio
async def test_t2_failure_safely_compensates_empty_owned_leaf_and_retry_succeeds(tmp_path: Path) -> None:
    documents = tmp_path / "Documents"; documents.mkdir()
    db_path = tmp_path / "state.db"
    await initialize_state_db(db_path)
    bindings = ProjectBindingService(db_path)
    kwargs = dict(documents_root=str(documents), documents_identity=_identity(documents), allow_test_projectless=False)

    async def fail_t2(stage: str) -> None:
        if stage == "after_session_core":
            raise RuntimeError("t2-failure")

    with pytest.raises(RuntimeError, match="t2-failure"):
        await SessionCreationService(bindings, fault_inject=fail_t2, **kwargs).create_conversation_session(request_id="t2-safe")
    with sqlite3.connect(db_path) as db:
        state, path, attempts = db.execute(
            "SELECT state,directory_path,reconciliation_attempts FROM workspace_allocations WHERE request_id='t2-safe'"
        ).fetchone()
    assert state == "reserved"
    assert attempts == 1
    assert not Path(path).exists()
    assert (await SessionCreationService(bindings, **kwargs).create_conversation_session(request_id="t2-safe"))["session"]["workspace_kind"] == "automatic"


@pytest.mark.asyncio
async def test_t2_failure_preserves_leaf_with_user_content_and_fails_closed(tmp_path: Path) -> None:
    documents = tmp_path / "Documents"; documents.mkdir()
    db_path = tmp_path / "state.db"
    await initialize_state_db(db_path)
    bindings = ProjectBindingService(db_path)
    kwargs = dict(documents_root=str(documents), documents_identity=_identity(documents), allow_test_projectless=False)

    async def user_writes_then_fail(stage: str) -> None:
        if stage == "after_session_core":
            leaf = next((documents / "SimpleHarnessProjects").iterdir())
            (leaf / "user.txt").write_text("do not delete", encoding="utf-8")
            raise RuntimeError("t2-user-content")

    with pytest.raises(RuntimeError, match="t2-user-content"):
        await SessionCreationService(bindings, fault_inject=user_writes_then_fail, **kwargs).create_conversation_session(request_id="t2-user")
    leaf = next((documents / "SimpleHarnessProjects").iterdir())
    assert (leaf / "user.txt").read_text(encoding="utf-8") == "do not delete"
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT state FROM workspace_allocations WHERE request_id='t2-user'").fetchone()[0] == "compensation_required"
    with pytest.raises(Exception, match="workspace_compensation_required"):
        await SessionCreationService(bindings, **kwargs).create_conversation_session(request_id="t2-user")


@pytest.mark.asyncio
async def test_identity_drift_is_never_deleted_even_with_matching_marker(tmp_path: Path) -> None:
    documents = tmp_path / "Documents"; documents.mkdir()
    db_path = tmp_path / "state.db"
    await initialize_state_db(db_path)
    bindings = ProjectBindingService(db_path)
    kwargs = dict(documents_root=str(documents), documents_identity=_identity(documents), allow_test_projectless=False)

    async def replace_leaf_then_fail(stage: str) -> None:
        if stage == "after_session_core":
            leaf = next((documents / "SimpleHarnessProjects").iterdir())
            marker = leaf / ".simple-harness-workspace.json"
            marker_value = json.loads(marker.read_text(encoding="utf-8"))
            marker.unlink(); leaf.rmdir(); leaf.mkdir()
            marker.write_text(json.dumps(marker_value), encoding="utf-8")
            raise RuntimeError("identity-drift")

    with pytest.raises(RuntimeError, match="identity-drift"):
        await SessionCreationService(bindings, fault_inject=replace_leaf_then_fail, **kwargs).create_conversation_session(request_id="t2-drift")
    leaf = next((documents / "SimpleHarnessProjects").iterdir())
    assert leaf.exists()
    with sqlite3.connect(db_path) as db:
        state, error = db.execute("SELECT state,error_code FROM workspace_allocations WHERE request_id='t2-drift'").fetchone()
    assert (state, error) == ("compensation_required", "workspace_compensation_unsafe")


@pytest.mark.asyncio
async def test_startup_reconciliation_reclaims_t1_orphan(tmp_path: Path) -> None:
    documents = tmp_path / "Documents"; documents.mkdir()
    db_path = tmp_path / "state.db"
    await initialize_state_db(db_path)
    bindings = ProjectBindingService(db_path)
    kwargs = dict(documents_root=str(documents), documents_identity=_identity(documents), allow_test_projectless=False)

    async def crash_at_t1(stage: str) -> None:
        if stage == "workspace_after_identity_fence":
            raise RuntimeError("t1-crash")

    with pytest.raises(RuntimeError, match="t1-crash"):
        await SessionCreationService(bindings, fault_inject=crash_at_t1, **kwargs).create_conversation_session(request_id="t1-orphan")
    service = SessionCreationService(bindings, **kwargs)
    assert await service.reconcile_automatic_workspaces() == {
        "completed": 0, "reserved": 1, "compensation_required": 0,
    }
    assert (await service.create_conversation_session(request_id="t1-orphan"))["session"]["workspace_kind"] == "automatic"

from __future__ import annotations

import hashlib
import platform
import sqlite3
from pathlib import Path

import pytest

from deskpet.memory.schema import initialize_state_db
from deskpet.session.project_binding import ProjectBindingService, ProjectBoundWorkspace, SessionCreationService


def _identity(path: Path) -> str:
    stat = path.stat()
    raw = f"v1\0{platform.system().lower()}\0{int(stat.st_dev)}\0{int(stat.st_ino)}"
    return hashlib.sha256(raw.encode()).hexdigest()


@pytest.mark.asyncio
async def test_default_workspace_is_bound_but_listed_as_ordinary(tmp_path: Path) -> None:
    documents = tmp_path / "Documents"
    documents.mkdir()
    db_path = tmp_path / "state.db"
    await initialize_state_db(db_path)
    bindings = ProjectBindingService(db_path)
    creation = SessionCreationService(
        bindings,
        documents_root=str(documents),
        documents_identity=_identity(documents),
        allow_test_projectless=False,
    )

    result = await creation.create_conversation_session(request_id="ordinary-1")
    session = result["session"]
    assert session["session_kind"] == "projectless"
    assert session["workspace_kind"] == "automatic"
    root = Path(session["execution_root"])
    assert root.parent == documents / "SimpleHarnessProjects"
    assert root.is_dir()
    resolution = await bindings.resolve_session(session["session_id"])
    assert isinstance(resolution, ProjectBoundWorkspace)
    assert resolution.effective_root == str(root)

    page = await bindings.list_session_page(scope_kind="projectless")
    assert [item["session_id"] for item in page["items"]] == [session["session_id"]]
    assert page["items"][0]["workspace_kind"] == "automatic"
    assert (await bindings.list_project_page())["items"] == []
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT state FROM workspace_allocations").fetchone()[0] == "completed"


@pytest.mark.asyncio
async def test_selected_project_does_not_create_default_workspace(tmp_path: Path) -> None:
    documents = tmp_path / "Documents"
    selected = tmp_path / "selected"
    documents.mkdir(); selected.mkdir()
    db_path = tmp_path / "state.db"
    await initialize_state_db(db_path)
    bindings = ProjectBindingService(db_path)
    project, _ = await bindings.register_project(str(selected), "selected_folder")
    creation = SessionCreationService(
        bindings,
        documents_root=str(documents), documents_identity=_identity(documents),
        allow_test_projectless=False,
    )
    result = await creation.create_conversation_session(request_id="selected-1", project_id=project.project_id)
    assert result["session"]["execution_root"] == str(selected)
    assert not (documents / "SimpleHarnessProjects").exists()


@pytest.mark.asyncio
async def test_legacy_projectless_migrates_once_and_preserves_messages(tmp_path: Path) -> None:
    documents = tmp_path / "Documents"
    documents.mkdir()
    db_path = tmp_path / "state.db"
    await initialize_state_db(db_path)
    sid = "legacy-session"
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO sessions(id,created_at) VALUES(?,1)", (sid,))
        db.execute(
            "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,'user','keep-me',2)",
            (sid,),
        )
    bindings = ProjectBindingService(db_path)
    kwargs = dict(
        documents_root=str(documents), documents_identity=_identity(documents),
        allow_test_projectless=False,
    )
    assert await SessionCreationService(bindings, **kwargs).migrate_projectless_session(sid) is True
    # A cold-started service observes the durable binding and performs no copy.
    assert await SessionCreationService(bindings, **kwargs).migrate_projectless_session(sid) is False
    resolution = await bindings.resolve_session(sid)
    assert isinstance(resolution, ProjectBoundWorkspace)
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT content FROM messages WHERE session_id=?", (sid,)).fetchone()[0] == "keep-me"
        assert db.execute("SELECT COUNT(*) FROM workspace_allocations WHERE state='completed'").fetchone()[0] == 1

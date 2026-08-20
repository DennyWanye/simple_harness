# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S5: file_tools unit tests.

Focus areas:

* Happy-path read/write/glob/grep round-trip.
* Path-escape defence — absolute paths, ``..`` traversal, UNC paths,
  mixed-case ``C:/Windows/...``. All MUST come back as
  ``path outside workspace`` errors, with the handler NEVER touching
  real disk outside the workspace.

Each test points ``DESKPET_WORKSPACE_DIR`` at a fresh tmp directory so
the production ``%APPDATA%\\deskpet\\workspace\\`` stays untouched.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskpet.tools.registry import registry
from deskpet.tools import file_tools


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the workspace resolver at a disposable tmp dir."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("DESKPET_WORKSPACE_DIR", str(workspace))
    return workspace


def test_workspace_root_honors_bound_user_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DESKPET_WORKSPACE_DIR", raising=False)
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(tmp_path / "profile"))

    assert file_tools._workspace_root() == (tmp_path / "profile" / "workspace").resolve()


# ---------------------------------------------------------------------
# file_write / file_read round-trip
# ---------------------------------------------------------------------
def test_write_then_read_roundtrip(sandbox: Path):
    wres = json.loads(
        registry.dispatch("file_write", {"path": "a.txt", "content": "hi\n"})
    )
    assert wres["bytes_written"] == 3
    rres = json.loads(registry.dispatch("file_read", {"path": "a.txt"}))
    assert rres["content"] == "hi\n"
    assert rres["lines_read"] == 1


def test_write_creates_parent_dirs(sandbox: Path):
    res = json.loads(
        registry.dispatch(
            "file_write", {"path": "nested/dir/b.txt", "content": "x"}
        )
    )
    assert res["bytes_written"] == 1
    assert (sandbox / "nested" / "dir" / "b.txt").is_file()


@pytest.mark.asyncio
async def test_code_session_project_root_controls_write_and_staged_target(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    args = {
        "path": "plans/result.txt",
        "content": "ok",
        "_project_root": str(project),
    }

    result = json.loads(await file_tools._handle_file_write(args, "code-session"))
    registry.set_session_context("code-session", {"_project_root": str(project)})
    try:
        prepared = registry.prepare_call(
            "file_write",
            {"path": "plans/result.txt", "content": "ok"},
            "code-session",
            "stable-code-write",
        )
    finally:
        registry.set_session_context("code-session", None)

    assert result["path"] == "plans/result.txt"
    assert (project / "plans" / "result.txt").read_text(encoding="utf-8") == "ok"
    assert prepared.prepared_targets[0].final_path == str(
        (project / "plans" / "result.txt").resolve()
    )


@pytest.mark.asyncio
async def test_absolute_path_returned_by_workspace_prepare_can_write_inside_root(
    tmp_path: Path,
) -> None:
    project = (tmp_path / "project").resolve()
    project.mkdir()
    target = project / "generated" / "project.godot"

    result = json.loads(
        await file_tools._handle_file_write(
            {
                "path": str(target),
                "content": "config_version=5\n",
                "_project_root": str(project),
            },
            "code-session",
        )
    )

    assert result["path"] == "generated/project.godot"
    assert target.read_text(encoding="utf-8") == "config_version=5\n"


@pytest.mark.asyncio
async def test_absolute_path_cannot_expand_trusted_workspace(
    tmp_path: Path,
) -> None:
    project = (tmp_path / "project").resolve()
    project.mkdir()
    outside = (tmp_path / "outside.txt").resolve()

    result = json.loads(
        await file_tools._handle_file_write(
            {
                "path": str(outside),
                "content": "blocked",
                "_project_root": str(project),
            },
            "code-session",
        )
    )

    assert result == {"error": "path outside workspace", "retriable": False}
    assert not outside.exists()


def test_append_mode_accumulates(sandbox: Path):
    registry.dispatch("file_write", {"path": "x.txt", "content": "1\n"})
    registry.dispatch(
        "file_write", {"path": "x.txt", "content": "2\n", "mode": "append"}
    )
    r = json.loads(registry.dispatch("file_read", {"path": "x.txt"}))
    assert r["content"] == "1\n2\n"


def test_read_offset_and_limit(sandbox: Path):
    payload = "".join(f"line{i}\n" for i in range(10))
    registry.dispatch("file_write", {"path": "lines.txt", "content": payload})
    r = json.loads(
        registry.dispatch(
            "file_read", {"path": "lines.txt", "offset": 3, "limit": 2}
        )
    )
    assert r["content"] == "line3\nline4\n"
    assert r["lines_read"] == 2


def test_read_missing_file(sandbox: Path):
    r = json.loads(registry.dispatch("file_read", {"path": "ghost.txt"}))
    assert "error" in r
    assert r["retriable"] is False


def test_write_rejects_non_string_content(sandbox: Path):
    r = json.loads(
        registry.dispatch("file_write", {"path": "a", "content": 123})
    )
    assert r["error"] == "content must be a string"


def test_write_rejects_invalid_mode(sandbox: Path):
    r = json.loads(
        registry.dispatch(
            "file_write", {"path": "a", "content": "x", "mode": "zap"}
        )
    )
    assert "invalid mode" in r["error"]


# ---------------------------------------------------------------------
# Path-escape defence (requirements: safe file workspace)
# ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "evil",
    [
        "../../../etc/passwd",
        "..\\..\\..\\windows\\system.ini",
        "/etc/passwd",
        "C:/Windows/system.ini",
        "C:\\Windows\\system.ini",
        "\\\\server\\share\\file.txt",
        "//server/share/file.txt",
        "D:\\Users\\victim\\secret.txt",
    ],
)
def test_read_rejects_escaping_paths(sandbox: Path, evil: str):
    r = json.loads(registry.dispatch("file_read", {"path": evil}))
    assert r == {"error": "path outside workspace", "retriable": False}


@pytest.mark.parametrize(
    "evil",
    [
        "../../outside.txt",
        "..\\..\\outside.txt",
        "/tmp/x",
        "C:/Windows/host.ini",
    ],
)
def test_write_rejects_escaping_paths(sandbox: Path, evil: str):
    r = json.loads(
        registry.dispatch("file_write", {"path": evil, "content": "evil"})
    )
    assert r["error"] == "path outside workspace"
    # Defensive: verify nothing landed anywhere near sandbox parent.
    assert not list(sandbox.parent.glob("outside.txt"))


def test_glob_rejects_escaping_root(sandbox: Path):
    r = json.loads(
        registry.dispatch("file_glob", {"pattern": "*", "root": "../.."})
    )
    assert r["error"] == "path outside workspace"


def test_grep_rejects_escaping_path(sandbox: Path):
    r = json.loads(
        registry.dispatch(
            "file_grep",
            {"pattern": "root", "path": "/etc/passwd"},
        )
    )
    assert r["error"] == "path outside workspace"


def test_relative_dot_slash_is_allowed(sandbox: Path):
    """``./foo.txt`` is benign — it's the workspace root."""
    registry.dispatch("file_write", {"path": "./foo.txt", "content": "ok"})
    r = json.loads(registry.dispatch("file_read", {"path": "./foo.txt"}))
    assert r["content"] == "ok"


# ---------------------------------------------------------------------
# file_glob
# ---------------------------------------------------------------------
def test_glob_finds_files(sandbox: Path):
    (sandbox / "a.md").write_text("x")
    (sandbox / "dir").mkdir()
    (sandbox / "dir" / "b.md").write_text("y")
    (sandbox / "c.txt").write_text("z")
    r = json.loads(
        registry.dispatch("file_glob", {"pattern": "**/*.md"})
    )
    assert sorted(r["matches"]) == ["a.md", "dir/b.md"]
    assert r["count"] == 2
    assert r["skipped_dirs"] == []
    assert r["skipped_count"] == 0


def test_glob_skips_generated_and_heavy_dirs_by_default(sandbox: Path):
    (sandbox / "keep").mkdir()
    (sandbox / "keep" / "a.md").write_text("ok")
    for dirname in ("node_modules", "__pycache__", ".uv-cache"):
        hidden = sandbox / dirname
        hidden.mkdir()
        (hidden / "hidden.md").write_text("noise")

    r = json.loads(
        registry.dispatch("file_glob", {"pattern": "**/*.md"})
    )

    assert r["matches"] == ["keep/a.md"]
    assert r["count"] == 1
    assert set(r["skipped_dirs"]) == {"node_modules", "__pycache__", ".uv-cache"}
    assert r["skipped_count"] == 3


def test_glob_skips_nested_backend_assets_relative_path(sandbox: Path):
    (sandbox / "backend" / "assets" / "model").mkdir(parents=True)
    (sandbox / "backend" / "assets" / "model" / "hidden.md").write_text("noise")
    (sandbox / "backend" / "src").mkdir(parents=True)
    (sandbox / "backend" / "src" / "visible.md").write_text("ok")

    r = json.loads(
        registry.dispatch("file_glob", {"pattern": "**/*.md"})
    )

    assert r["matches"] == ["backend/src/visible.md"]
    assert r["count"] == 1
    assert r["skipped_dirs"] == ["backend/assets"]
    assert r["skipped_count"] == 1


def test_glob_allows_explicit_root_inside_default_skipped_dir(sandbox: Path):
    (sandbox / "node_modules" / "pkg").mkdir(parents=True)
    (sandbox / "node_modules" / "pkg" / "visible.md").write_text("ok")

    r = json.loads(
        registry.dispatch(
            "file_glob", {"pattern": "**/*.md", "root": "node_modules"}
        )
    )

    assert r["matches"] == ["node_modules/pkg/visible.md"]
    assert r["count"] == 1
    assert r["skipped_dirs"] == []
    assert r["skipped_count"] == 0


def test_glob_root_parameter_preserves_recursive_pathlib_semantics(sandbox: Path):
    (sandbox / "dir" / "nested").mkdir(parents=True)
    (sandbox / "dir" / "nested" / "a.md").write_text("ok")
    (sandbox / "other.md").write_text("nope")

    r = json.loads(
        registry.dispatch("file_glob", {"pattern": "**/*.md", "root": "dir"})
    )

    assert r["matches"] == ["dir/nested/a.md"]
    assert r["count"] == 1


def test_glob_directory_prefixed_recursive_pattern_matches_from_workspace_root(
    sandbox: Path,
):
    (sandbox / "dir" / "nested").mkdir(parents=True)
    (sandbox / "dir" / "nested" / "a.md").write_text("ok")
    (sandbox / "other" / "nested").mkdir(parents=True)
    (sandbox / "other" / "nested" / "a.md").write_text("nope")

    r = json.loads(
        registry.dispatch("file_glob", {"pattern": "dir/**/*.md"})
    )

    assert r["matches"] == ["dir/nested/a.md"]
    assert r["count"] == 1


def test_glob_non_recursive_pattern_stays_non_recursive(sandbox: Path):
    (sandbox / "a.md").write_text("ok")
    (sandbox / "dir").mkdir()
    (sandbox / "dir" / "b.md").write_text("nested")

    r = json.loads(registry.dispatch("file_glob", {"pattern": "*.md"}))

    assert r["matches"] == ["a.md"]
    assert r["count"] == 1


def test_glob_missing_root_returns_empty(sandbox: Path):
    r = json.loads(
        registry.dispatch(
            "file_glob", {"pattern": "*", "root": "no-such-dir"}
        )
    )
    assert r == {"matches": [], "count": 0}


# ---------------------------------------------------------------------
# file_grep
# ---------------------------------------------------------------------
def test_grep_returns_matching_lines(sandbox: Path):
    (sandbox / "log.txt").write_text(
        "info: starting\nerror: fatal\nwarn: mild\nerror: again\n"
    )
    r = json.loads(
        registry.dispatch(
            "file_grep", {"pattern": "error", "path": "log.txt"}
        )
    )
    assert r["count"] == 2
    assert r["matches"][0]["line"] == 2
    assert "fatal" in r["matches"][0]["text"]
    assert r["matches"][1]["line"] == 4


def test_grep_max_matches_caps_output(sandbox: Path):
    (sandbox / "log.txt").write_text("x\n" * 100)
    r = json.loads(
        registry.dispatch(
            "file_grep",
            {"pattern": "x", "path": "log.txt", "max_matches": 5},
        )
    )
    assert r["count"] == 5


def test_grep_invalid_regex(sandbox: Path):
    (sandbox / "f.txt").write_text("hi")
    r = json.loads(
        registry.dispatch(
            "file_grep", {"pattern": "[unterminated", "path": "f.txt"}
        )
    )
    assert r["error"].startswith("invalid regex")

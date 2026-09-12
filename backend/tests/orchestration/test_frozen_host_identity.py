"""Frozen identity must never describe the surrounding source checkout."""

import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from deskpet.orchestration import manifest


def identity():
    return {"schema": "host-build-identity-v1", "host_commit": "a" * 40,
            "host_dirty": False, "tracked_inputs_sha256": "b" * 64,
            "tracked_input_count": 1,
            "input_roots": ["backend", "tauri-app", "scripts", "capability-packs", "resources", "config.toml"]}


@pytest.mark.parametrize("dirty", [False, True])
def test_frozen_identity_uses_bundled_metadata_without_ambient_git(tmp_path, monkeypatch, dirty):
    payload = {**identity(), "host_dirty": dirty}
    (tmp_path / "host-build-identity.json").write_text(json.dumps(payload))
    monkeypatch.setattr(manifest.sys, "frozen", True, raising=False)
    monkeypatch.setattr(manifest.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(manifest, "__file__", str(tmp_path / "ambient/backend/deskpet/orchestration/manifest.py"))
    monkeypatch.setattr(manifest.subprocess, "run", lambda *a, **kw: pytest.fail("frozen runtime invoked ambient git"))
    assert manifest._host_commit() == payload["host_commit"]
    assert manifest._host_dirty() is dirty


@pytest.mark.parametrize("payload", [None, "{broken", "[]", json.dumps({**identity(), "host_commit": "not-a-commit"}),
    json.dumps({**identity(), "host_dirty": "false"}), json.dumps({**identity(), "tracked_inputs_sha256": None}),
    json.dumps({**identity(), "schema": "unknown"}), json.dumps({**identity(), "tracked_input_count": True}),
    json.dumps({**identity(), "input_roots": None})])
def test_missing_or_invalid_frozen_metadata_is_unknown_never_git(tmp_path, monkeypatch, payload):
    if payload is not None:
        (tmp_path / "host-build-identity.json").write_text(payload)
    monkeypatch.setattr(manifest.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(manifest.subprocess, "run", lambda *a, **kw: pytest.fail("frozen runtime invoked git"))
    assert manifest._host_commit() == "unknown"
    assert manifest._host_dirty() is None


def test_frozen_without_resource_root_does_not_fall_back_to_git(monkeypatch):
    monkeypatch.setattr(manifest.sys, "frozen", True, raising=False)
    monkeypatch.delattr(manifest.sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(manifest.subprocess, "run", lambda *a, **kw: pytest.fail("frozen runtime invoked git"))
    assert manifest._host_commit() == "unknown"
    assert manifest._host_dirty() is None


def test_source_mode_preserves_git_identity_calls(monkeypatch):
    monkeypatch.setattr(manifest.sys, "frozen", False, raising=False)
    monkeypatch.delattr(manifest.sys, "_MEIPASS", raising=False)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "c" * 40 if args[1] == "rev-parse" else " M backend/main.py\n")

    monkeypatch.setattr(manifest.subprocess, "run", run)
    assert manifest._host_commit() == "c" * 40
    assert manifest._host_dirty() is True
    assert [command[1] for command in calls] == ["rev-parse", "status"]


def build_capture():
    # Execute the real spec helper without importing Analysis/ML packages or
    # launching PyInstaller. Tests below use only a disposable Git repository.
    spec = Path(__file__).parents[2] / "deskpet-backend.spec"
    tree = ast.parse(spec.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_capture_host_build_identity")
    namespace = {"Path": Path, "subprocess": subprocess, "hashlib": hashlib, "os": os}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(spec), "exec"), namespace)
    return namespace[function.name]


@pytest.fixture
def clean_repo(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/main.py").write_text("print('build input')\n")
    git("add", "backend/main.py")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
    return tmp_path, git


def test_build_capture_is_deterministic_and_bound_to_clean_input_bytes(clean_repo):
    root, git = clean_repo
    capture = build_capture()
    before = capture(root)
    assert before == capture(root)
    assert before["host_commit"] == git("rev-parse", "HEAD").stdout.decode().strip()
    assert before["host_dirty"] is False
    assert before["tracked_input_count"] == 1
    assert len(before["tracked_inputs_sha256"]) == 64
    (root / "backend/main.py").write_text("print('new input')\n")
    git("add", "backend/main.py")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "changed fixture")
    assert capture(root)["tracked_inputs_sha256"] != before["tracked_inputs_sha256"]


@pytest.mark.parametrize("change", ["unstaged", "staged", "untracked"])
def test_build_refuses_dirty_runtime_inputs(clean_repo, change):
    root, git = clean_repo
    target = "backend/new.py" if change == "untracked" else "backend/main.py"
    (root / target).write_text("changed\n")
    if change == "staged":
        git("add", target)
    with pytest.raises(RuntimeError, match="dirty Host build inputs"):
        build_capture()(root)


def test_documentation_changes_do_not_block_runtime_build(clean_repo):
    root, _ = clean_repo
    (root / "plans").mkdir()
    (root / "plans/review.md").write_text("Review is still being updated.\n")
    assert build_capture()(root)["host_dirty"] is False

# SPDX-License-Identifier: Apache-2.0
"""目录内的独立改动、合法文件名以及 Git 拍底范围的回归。"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_delivery import _base, _change_set, _git, _produced

from agent_orchestrator.artifacts import workspace_base
from agent_orchestrator.artifacts.delivery import DeliveryLedger, apply_change_set
from agent_orchestrator.artifacts.workspace_base import (
    BaseRepo,
    DirectoryBase,
    GitRepoBase,
    git_blob_id,
)
from agent_orchestrator.host import OrchestrationHost


@pytest.mark.parametrize("kind", [DirectoryBase, GitRepoBase])
@pytest.mark.parametrize("stage", ["replace", "verify", "already"])
def test_sibling_changes_and_replay(tmp_path, monkeypatch, kind, stage):
    root = tmp_path / "repo"
    parent = root / "docs"
    parent.mkdir(parents=True)
    if kind is GitRepoBase:
        _git(root, "init", "-q")
    paths = ("docs/add.md", "docs/remove.md")
    for path in paths:
        (root / path).write_bytes(b"base")
    adapter = kind(root)
    repo = BaseRepo(tmp_path / "work.git")
    change = _change_set(repo, _base(repo, adapter), [_produced(p, b"delivery") for p in paths])
    ledger = DeliveryLedger(tmp_path / "receipts")
    host = SimpleNamespace(
        _workspace_base=adapter,
        _bases=lambda: repo,
        _deliveries=lambda: ledger,
        _change_set=lambda _: ({"status": "COMPLETED"}, change),
    )
    if stage == "already":
        for path in paths:
            (root / path).write_bytes(b"delivery")
    (parent / "old.pyc").touch()
    current = adapter._current
    changed = set()

    def read(path):
        blob = current(path)
        staging = any(parent.glob("*.harness"))
        in_stage = staging if stage == "replace" else blob == git_blob_id(b"delivery")
        if in_stage and path not in changed:
            if path == paths[0]:
                (parent / "new.pyc").touch()
            else:
                (parent / "old.pyc").unlink()
            changed.add(path)
        return blob

    monkeypatch.setattr(adapter, "_current", read)
    first = OrchestrationHost.apply_delivery(host, "mission", operation_key="deliver")
    replay = OrchestrationHost.apply_delivery(host, "mission", operation_key="deliver")
    assert changed == set(paths)
    assert not first["repeat"] and replay["repeat"]
    assert first["files"] == replay["files"] == ledger.receipts("mission")[0]["files"]
    expected = "already" if stage == "already" else "applied"
    assert [(r["path"], r["status"], r["reason"]) for r in first["files"]] == [
        (p, expected, None) for p in paths
    ]
    for row in first["files"]:
        assert row["base_blob"] == git_blob_id(b"base")
        assert row["current_blob"] == git_blob_id(b"delivery" if stage == "already" else b"base")
        assert Path(row["path"]).name in os.listdir(parent)
        assert (
            row["blob"]
            == git_blob_id((root / row["path"]).read_bytes())
            == git_blob_id(b"delivery")
        )


@pytest.mark.parametrize("kind", [DirectoryBase, GitRepoBase])
@pytest.mark.parametrize("size", [214, 230, 255])
def test_long_target(tmp_path, kind, size):
    root = tmp_path / "repo"
    root.mkdir()
    if kind is GitRepoBase:
        _git(root, "init", "-q")
    name = "a" * (size - 4) + ".txt"
    (root / name).write_bytes(b"base")
    adapter = kind(root)
    repo = BaseRepo(tmp_path / "work.git")
    change = _change_set(repo, _base(repo, adapter), [_produced(name, b"delivery")])
    [row] = apply_change_set(adapter, repo, change)
    assert (row["status"], row["reason"]) == ("applied", None)
    assert (root / name).read_bytes() == b"delivery"
    assert not list(root.glob("*.harness"))


def test_git_scope_skips_unlistable_sibling(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    for folder in ("owned", "other"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "f").write_bytes(b"base")
    _git(tmp_path, "add", "--all")
    listdir = os.listdir

    def list_readable(folder):
        if Path(folder) == tmp_path / "other":
            raise PermissionError("范围外目录只可穿越")
        return listdir(folder)

    monkeypatch.setattr(os, "listdir", list_readable)
    files = GitRepoBase(tmp_path).capture(["owned/"]).files
    assert {f.path: f.read() for f in files} == {"owned/f": b"base"}


def test_git_spelling_keys_scale_with_entries(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "core.precomposeunicode", "true")
    names = {f"cafe\u0301-{i}" for i in range(64)}
    for name in names:
        (tmp_path / name).touch()
    adapter = GitRepoBase(tmp_path)
    if "café-0".encode() not in adapter._run("ls-files", "-z", "--others").stdout:
        pytest.skip("此 Git 在该磁盘上未预组合工作树条目")
    calls = 0
    spelling_key = workspace_base.spelling_key

    def counted(name):
        nonlocal calls
        calls += 1
        return spelling_key(name)

    monkeypatch.setattr(workspace_base, "spelling_key", counted)
    assert {f.path for f in adapter.capture([]).files} == names
    assert calls <= 5 * len(names)


def test_recheck_rejects_temporary_parent_alias_blob(tmp_path, monkeypatch):
    parent = tmp_path / "Docs"
    parent.mkdir()
    (parent / "Doc").write_bytes(b"base")
    if not (tmp_path / "DOCS").exists():
        pytest.skip("此磁盘不通过目录别名读取")
    adapter = DirectoryBase(tmp_path)
    read = adapter._current

    def read_from_other_parent(path):
        parent.rename(tmp_path / "saved")
        alias = tmp_path / "DOCS"
        alias.mkdir()
        (alias / "Doc").write_bytes(b"delivery")
        try:
            return read(path)
        finally:
            (alias / "Doc").unlink()
            alias.rmdir()
            (tmp_path / "saved").rename(parent)

    monkeypatch.setattr(adapter, "_current", read_from_other_parent)
    result = adapter.write_file(
        "Docs/Doc", b"delivery", expected=git_blob_id(b"base"), executable=False
    )
    assert result.status == "refused"
    assert (parent / "Doc").read_bytes() == b"base"


@pytest.mark.parametrize("entry", ["file", "parent"])
def test_recheck_rejects_replacement_with_same_bytes(tmp_path, monkeypatch, entry):
    parent = tmp_path / "docs"
    parent.mkdir()
    target = parent / "doc"
    target.write_bytes(b"base")
    adapter = DirectoryBase(tmp_path)
    read = adapter._current

    def replace_while_reading(path):
        blob = read(path)
        if any(parent.glob("*.harness")):
            if entry == "parent":
                parent.rename(tmp_path / "saved")
                parent.mkdir()
                os.link(tmp_path / "saved/doc", target)
            else:
                target.rename(tmp_path / "saved")
                target.write_bytes(b"base")
        return blob

    monkeypatch.setattr(adapter, "_current", replace_while_reading)
    result = adapter.write_file(
        "docs/doc", b"delivery", expected=git_blob_id(b"base"), executable=False
    )
    assert result.status == "refused"
    assert target.read_bytes() == b"base"
    assert not list(tmp_path.rglob("*.harness"))


@pytest.mark.parametrize("user_saved", [False, True])
def test_only_own_staging_entry_is_used(tmp_path, monkeypatch, user_saved):
    target = tmp_path / "Doc"
    target.write_bytes(b"base")
    adapter = DirectoryBase(tmp_path)
    chmod = os.chmod

    def replace_staging(path, mode):
        chmod(path, mode)
        [staged] = tmp_path.glob("*.harness")
        staged.rename(tmp_path / "moved")
        staged.write_bytes(b"other")
        if user_saved:
            target.write_bytes(b"user edit")

    monkeypatch.setattr(os, "chmod", replace_staging)
    result = adapter.write_file("Doc", b"delivery", expected=git_blob_id(b"base"), executable=False)
    assert result.status == ("conflict" if user_saved else "refused")
    assert target.read_bytes() == (b"user edit" if user_saved else b"base")
    [other] = tmp_path.glob("*.harness")
    assert other.read_bytes() == b"other"


def test_parent_renamed_during_sibling_recheck(tmp_path, monkeypatch):
    parent = tmp_path / "Docs"
    parent.mkdir()
    (parent / "Doc").write_bytes(b"base")
    if not (tmp_path / "DOCS").exists():
        pytest.skip("此磁盘不通过目录别名读取")
    adapter = DirectoryBase(tmp_path)
    read, listdir = adapter._current, os.listdir
    rechecking = False

    def change_sibling(path):
        nonlocal rechecking
        blob = read(path)
        if any(parent.glob("*.harness")):
            (parent / "build.pyc").touch()
            rechecking = True
        return blob

    def rename_after_listing(folder):
        nonlocal rechecking
        names = listdir(folder)
        if rechecking and folder == tmp_path:
            rechecking = False
            parent.rename(tmp_path / "saved")
            (tmp_path / "saved").rename(tmp_path / "DOCS")
        return names

    monkeypatch.setattr(adapter, "_current", change_sibling)
    monkeypatch.setattr(os, "listdir", rename_after_listing)
    result = adapter.write_file(
        "Docs/Doc", b"delivery", expected=git_blob_id(b"base"), executable=False
    )
    assert result.status == "refused"
    assert (tmp_path / "DOCS/Doc").read_bytes() == b"base"
    assert not list(tmp_path.rglob("*.harness"))


def test_scoped_git_capture_uses_exact_worktree_directory(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "core.precomposeunicode", "true")
    folder = tmp_path / "cafe\u0301"
    folder.mkdir()
    (folder / "doc").write_bytes(b"base")
    _git(tmp_path, "add", "--all")
    listdir = os.listdir
    listed = []

    def record(folder):
        listed.append(folder)
        return listdir(folder)

    monkeypatch.setattr(os, "listdir", record)
    adapter = GitRepoBase(tmp_path)
    assert not adapter.capture(["café"]).files
    assert folder not in listed
    assert {f.path for f in adapter.capture([folder.name + "/doc"]).files} == {folder.name + "/doc"}


@pytest.mark.parametrize("atomic", [False, True])
def test_save_during_sibling_recheck_is_conflict(tmp_path, monkeypatch, atomic):
    target = tmp_path / "Doc"
    target.write_bytes(b"base")
    adapter = DirectoryBase(tmp_path)
    read, listdir = adapter._current, os.listdir
    rechecking = False

    def change_sibling(path):
        nonlocal rechecking
        blob = read(path)
        if any(tmp_path.glob("*.harness")):
            (tmp_path / "build.pyc").touch()
            rechecking = True
        return blob

    def save_during_recheck(folder):
        nonlocal rechecking
        names = listdir(folder)
        if rechecking and folder == tmp_path:
            rechecking = False
            if atomic:
                pending = tmp_path / "editor-save"
                pending.write_bytes(b"user edit")
                pending.replace(target)
            else:
                target.write_bytes(b"user edit")
        return names

    monkeypatch.setattr(adapter, "_current", change_sibling)
    monkeypatch.setattr(os, "listdir", save_during_recheck)
    result = adapter.write_file("Doc", b"delivery", expected=git_blob_id(b"base"), executable=False)
    assert (result.status, result.current) == ("conflict", git_blob_id(b"user edit"))
    assert target.read_bytes() == b"user edit"

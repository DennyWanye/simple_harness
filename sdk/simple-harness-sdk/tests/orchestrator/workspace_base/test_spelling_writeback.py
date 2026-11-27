# SPDX-License-Identifier: Apache-2.0
"""在实际目录与 Git 工作树中复现写回及判定的并发和枚举错误。"""

import errno
import os
from pathlib import Path

import pytest
from test_delivery import _base, _change_set, _git, _produced
from test_judgment_merge import judge

from agent_orchestrator.artifacts.delivery import DeliveryRefused, apply_change_set
from agent_orchestrator.artifacts.workspace_base import (
    BaseRepo,
    DirectoryBase,
    GitRepoBase,
    git_blob_id,
)


def prepare(tmp_path, path, kind=DirectoryBase):
    root = tmp_path / "Project"
    (root / path).parent.mkdir(parents=True)
    (root / path).write_bytes(b"base")
    if kind is GitRepoBase:
        _git(root, "init", "-q")
    adapter = kind(root)
    repo = BaseRepo(tmp_path / "work.git")
    return adapter, repo, _base(repo, adapter)


def test_save_during_last_spelling_scan(tmp_path, monkeypatch):
    adapter, repo, base = prepare(tmp_path, "Doc")
    change = _change_set(repo, base, [_produced("Doc", b"delivery")])
    listdir = os.listdir
    edits = []

    def save_while_listing(folder):
        names = listdir(folder)
        if any(name.endswith(".harness") for name in names):
            (adapter.root / "Doc").write_bytes(b"user edit")
            edits.append(True)
        return names

    monkeypatch.setattr(os, "listdir", save_while_listing)
    [row] = apply_change_set(adapter, repo, change)
    assert edits
    assert (row["status"], row["current_blob"]) == ("conflict", git_blob_id(b"user edit"))
    assert (adapter.root / "Doc").read_bytes() == b"user edit"


@pytest.mark.parametrize("kind", [DirectoryBase, GitRepoBase])
@pytest.mark.parametrize(
    "replacement", ["same_name", "alias", "missing_identity", "old_delivery", "same_directory"]
)
def test_snapshot_directory_identity(tmp_path, kind, replacement):
    adapter, repo, base = prepare(tmp_path, "config", kind)
    root = adapter.root
    if replacement == "missing_identity":
        base["source"] = {"kind": base["source"]["kind"], "location": adapter.location()}
    change = _change_set(repo, base, [_produced("config", b"delivery")])
    if replacement == "old_delivery":
        change.pop("root_id", None)
    if replacement == "same_directory":
        reopened = tmp_path / "PROJECT"
        if not reopened.exists():
            pytest.skip("磁盘不接受同一个目录的另一种大小写")
        assert root.samefile(reopened)
        assert apply_change_set(kind(reopened), repo, change)[0]["status"] == "applied"
        return
    if replacement in {"same_name", "alias"}:
        old = tmp_path / "saved"
        root.rename(old)
        root = root if replacement == "same_name" else tmp_path / "PROJECT"
        root.mkdir()
        if kind is GitRepoBase:
            _git(root, "init", "-q")
        (root / "config").write_bytes(b"base")
        assert not root.samefile(old)
    with pytest.raises(DeliveryRefused) as refused:
        apply_change_set(kind(root), repo, change)
    assert refused.value.code == "delivery_target_mismatch"
    assert (root / "config").read_bytes() == b"base"


@pytest.mark.parametrize("selection", ["tracked", "untracked", "index_before_rename"])
def test_git_capture_uses_worktree_spelling(tmp_path, selection):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "core.precomposeunicode", "true")
    name = "ReadMe" if selection == "index_before_rename" else "cafe\u0301"
    folder = root / name
    folder.mkdir()
    (folder / "Doc").write_bytes(b"base")
    if selection != "untracked":
        _git(root, "add", "--all")
    if selection == "index_before_rename":
        folder.rename(root / "saved")
        name = "README"
        (root / "saved").rename(root / name)
    relative = f"{name}/Doc"
    adapter = GitRepoBase(root)
    repo = BaseRepo(tmp_path / "work.git")
    base = _base(repo, adapter)
    assert repo.files(base["commit"]) == {relative: b"base"}
    change = _change_set(repo, base, [_produced(relative, b"delivery")])
    assert apply_change_set(adapter, repo, change)[0]["status"] == "applied"
    assert (root / relative).read_bytes() == b"delivery"


@pytest.mark.parametrize("stage", ["already", "verify"])
def test_hash_from_alias_during_read_is_rejected(tmp_path, monkeypatch, stage):
    adapter, repo, base = prepare(tmp_path, "Docs/ReadMe")
    change = _change_set(repo, base, [_produced("Docs/ReadMe", b"delivery")])
    root = adapter.root
    if stage == "already" and not (root / "DOCS/ReadMe").exists():
        pytest.skip("仅折叠大小写的磁盘能通过这个别名读取内容")
    original = adapter._current

    def replace_before_read(path):
        (root / "Docs").rename(root / "saved")
        (root / "DOCS").mkdir()
        (root / "DOCS/ReadMe").write_bytes(b"delivery")
        return original(path)

    if stage == "already":
        monkeypatch.setattr(adapter, "_current", replace_before_read)
    else:
        hashes = adapter.current_hashes

        def verify(paths):
            monkeypatch.setattr(adapter, "_current", replace_before_read)
            return hashes(paths)

        monkeypatch.setattr(adapter, "current_hashes", verify)
    [row] = apply_change_set(adapter, repo, change)
    if stage == "already":
        assert (row["status"], row["reason"]) == ("refused", "case_conflict")
        assert (root / "saved/ReadMe").read_bytes() == b"base"
    else:
        assert (row["status"], row["reason"]) == ("failed", "verify_mismatch")
    assert (root / "DOCS/ReadMe").read_bytes() == b"delivery"


@pytest.mark.parametrize(
    ("replacement", "reason"), [("cafe\u0301", "spelling_alias"), ("café", "parent_changed")]
)
def test_refusal_cleans_original_parent(tmp_path, monkeypatch, replacement, reason):
    adapter, repo, base = prepare(tmp_path, "café/Doc")
    change = _change_set(repo, base, [_produced("café/Doc", b"delivery")])
    root = adapter.root
    chmod = os.chmod

    def move_after_staging(path, mode):
        chmod(path, mode)
        (root / "café").rename(root / "saved")
        (root / replacement).mkdir()
        (root / replacement / "Doc").write_bytes(b"base")

    monkeypatch.setattr(os, "chmod", move_after_staging)
    [row] = apply_change_set(adapter, repo, change)
    assert (row["status"], row["reason"]) == ("refused", reason)
    for folder in ("saved", replacement):
        assert os.listdir(root / folder) == ["Doc"]
        assert (root / folder / "Doc").read_bytes() == b"base"


def test_unlistable_judgment_subtree(tmp_path, monkeypatch):
    scandir = os.scandir

    def deny(folder):
        if (
            isinstance(folder, (str, Path))
            and Path(folder).name == "Owned"
            and "verify" in str(folder)
        ):
            assert (Path(folder) / "Exact").read_bytes() == b"base"
            raise PermissionError(errno.EACCES, "无法列举", str(folder))
        return scandir(folder)

    monkeypatch.setattr(os, "scandir", deny)
    criteria = {
        "file:Owned/Exact": False,
        "file:Owned/absent": False,
        "file:OwnedElse/absent": False,
        "file:ok": True,
    }
    judge(
        tmp_path,
        monkeypatch,
        {"Owned/Exact": b"base", "ok": b"ok"},
        [],
        criteria=criteria,
        reasons={
            key: (
                "file unverifiable: Owned (PermissionError)"
                if key.startswith("file:Owned/")
                else "file exists"
                if met
                else "file missing"
            )
            for key, met in criteria.items()
        },
    )


def test_verify_error_is_local_to_one_path(tmp_path, monkeypatch):
    adapter, repo, base = prepare(tmp_path, "private/a")
    change = _change_set(repo, base, [_produced(name, b"delivery") for name in ("ok", "private/a")])
    hashes, listdir = adapter.current_hashes, os.listdir

    def deny(folder):
        if folder == adapter.root / "private":
            raise PermissionError(errno.EACCES, "无法列举", str(folder))
        return listdir(folder)

    def verify(paths):
        with monkeypatch.context() as patch:
            patch.setattr(os, "listdir", deny)
            return hashes(paths)

    monkeypatch.setattr(adapter, "current_hashes", verify)
    rows = apply_change_set(adapter, repo, change)
    assert [(r["path"], r["status"], r["reason"]) for r in rows] == [
        ("ok", "applied", None),
        ("private/a", "failed", "verify_failed"),
    ]
    assert all((adapter.root / row["path"]).read_bytes() == b"delivery" for row in rows)


def test_combining_mark_before_casefold(tmp_path):
    from agent_orchestrator.artifacts.paths import spelling_key

    exact, alias = "ᾼ\u0323.md", "α\u0323\u0345.md"
    (tmp_path / exact).write_bytes(b"delivery")
    assert spelling_key(exact) == spelling_key(alias)
    assert DirectoryBase(tmp_path).current_hashes([alias])[alias] is None


def test_temporary_alias_cannot_supply_already(tmp_path, monkeypatch):
    adapter, repo, base = prepare(tmp_path, "ReadMe")
    change = _change_set(repo, base, [_produced("ReadMe", b"delivery")])
    exact, alias = adapter.root / "ReadMe", adapter.root / "README"
    if not alias.exists():
        pytest.skip("仅折叠大小写的磁盘能通过这个别名读取内容")
    original = adapter._current

    def read_alias(path):
        exact.rename(adapter.root / "saved")
        alias.write_bytes(b"delivery")
        try:
            return original(path)
        finally:
            alias.unlink()
            (adapter.root / "saved").rename(exact)

    monkeypatch.setattr(adapter, "_current", read_alias)
    [row] = apply_change_set(adapter, repo, change)
    assert row["status"] == "refused"
    assert exact.read_bytes() == b"base"


def test_atomic_save_during_final_blob_read(tmp_path, monkeypatch):
    from agent_orchestrator.artifacts import workspace_base

    adapter, repo, base = prepare(tmp_path, "Doc")
    change = _change_set(repo, base, [_produced("Doc", b"delivery")])
    read = workspace_base.read_nofollow

    def save_while_reading(path):
        data = read(path)
        if any(adapter.root.glob("*.harness")):
            pending = adapter.root / "editor"
            pending.write_bytes(b"user edit")
            pending.replace(path)
        return data

    monkeypatch.setattr(workspace_base, "read_nofollow", save_while_reading)
    [row] = apply_change_set(adapter, repo, change)
    assert row["status"] in {"conflict", "refused", "failed"}
    assert (adapter.root / "Doc").read_bytes() == b"user edit"


def test_host_checks_identity_before_receipt_replay(tmp_path):
    from types import SimpleNamespace

    from agent_orchestrator.artifacts.delivery import DeliveryLedger
    from agent_orchestrator.host import HostRequestError, OrchestrationHost

    adapter, repo, base = prepare(tmp_path, "Doc")
    change = _change_set(repo, base, [_produced("Doc", b"delivery")])
    ledger = DeliveryLedger(tmp_path / "receipts")
    host = SimpleNamespace(
        _workspace_base=adapter,
        _bases=lambda: repo,
        _deliveries=lambda: ledger,
        _change_set=lambda _: ({"status": "COMPLETED"}, change),
    )

    def apply():
        return OrchestrationHost.apply_delivery(host, "mission", operation_key="once")

    assert apply()["files"][0]["status"] == "applied"
    assert apply()["repeat"]
    adapter.root.rename(tmp_path / "saved")
    adapter.root.mkdir()
    (adapter.root / "Doc").write_bytes(b"base")
    with pytest.raises(HostRequestError) as refused:
        apply()
    assert refused.value.code == "delivery_target_mismatch"
    assert (adapter.root / "Doc").read_bytes() == b"base"


def test_root_replaced_while_loading_delivery_blobs(tmp_path, monkeypatch):
    adapter, repo, base = prepare(tmp_path, "config")
    change = _change_set(repo, base, [_produced("config", b"delivery")])
    blobs = repo.blobs

    def replace_root(ids):
        adapter.root.rename(tmp_path / "saved")
        adapter.root.mkdir()
        (adapter.root / "config").write_bytes(b"base")
        return blobs(ids)

    monkeypatch.setattr(repo, "blobs", replace_root)
    [row] = apply_change_set(adapter, repo, change)
    assert (row["status"], row["reason"]) == ("refused", "delivery_target_mismatch")
    for folder in (adapter.root, tmp_path / "saved"):
        assert (folder / "config").read_bytes() == b"base"

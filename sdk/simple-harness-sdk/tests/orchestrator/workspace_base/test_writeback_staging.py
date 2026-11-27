# SPDX-License-Identifier: Apache-2.0
"""从交付回执、目录实物和下一次拍底核对暂存文件的生命周期。"""

import errno
import os
import stat
import struct
import subprocess
import sys

import pytest
from test_delivery import _change_set, _produced
from test_spelling_writeback import prepare

from agent_orchestrator.artifacts import _writeback_metadata
from agent_orchestrator.artifacts.delivery import apply_change_set
from agent_orchestrator.artifacts.workspace_base import DirectoryBase, GitRepoBase


@pytest.mark.skipif(os.name != "posix", reason="需要 POSIX 模式位")
@pytest.mark.parametrize("kind", [DirectoryBase, GitRepoBase])
@pytest.mark.parametrize("executable", [False, True])
@pytest.mark.parametrize(("mask", "execute_bits"), [(0o077, 0o100), (0o007, 0o110)])
def test_new_mode_follows_ordinary_creation(tmp_path, kind, executable, mask, execute_bits):
    adapter, _, _ = prepare(tmp_path, "notes", kind)
    old = os.umask(mask)
    try:
        control = adapter.root / "control"
        control.write_bytes(b"control")
        row = adapter.write_file("new", b"delivery", expected=None, executable=executable)
    finally:
        os.umask(old)
    expected = stat.S_IMODE(control.stat().st_mode) | (execute_bits if executable else 0)
    assert row.status == "applied"
    assert stat.S_IMODE((adapter.root / "new").stat().st_mode) == expected


@pytest.mark.skipif(sys.platform != "linux", reason="需要 Linux 目录默认 ACL")
@pytest.mark.parametrize("mask", [0, 6])
@pytest.mark.parametrize("executable", [False, True])
@pytest.mark.parametrize("kind", [DirectoryBase, GitRepoBase])
def test_new_file_inherits_effective_acl(tmp_path, kind, executable, mask):
    adapter, _, _ = prepare(tmp_path, "notes", kind)
    undef = 0xFFFFFFFF
    entries = [(1, 7, undef), (2, 6, 1234), (4, 0, undef), (16, mask, undef), (32, 0, undef)]
    acl = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *entry) for entry in entries)
    try:
        os.setxattr(adapter.root, "system.posix_acl_default", acl)
    except OSError as error:
        if error.errno == errno.ENOTSUP:
            pytest.skip("文件系统不支持默认 ACL")
        raise
    control = adapter.root / "control"
    control.write_bytes(b"control")
    if executable:
        mode = stat.S_IMODE(control.stat().st_mode)
        control.chmod(mode | ((mode & 0o444) >> 2))
    row = adapter.write_file("new", b"delivery", expected=None, executable=executable)
    target = adapter.root / "new"
    assert (row.status, target.read_bytes()) == ("applied", b"delivery")
    assert stat.S_IMODE(target.stat().st_mode) == stat.S_IMODE(control.stat().st_mode)
    assert os.getxattr(target, "system.posix_acl_access") == os.getxattr(
        control, "system.posix_acl_access"
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 macOS 继承 ACL")
@pytest.mark.parametrize("kind", [DirectoryBase, GitRepoBase])
@pytest.mark.parametrize("inherit", ["file_inherit", "directory_inherit"])
def test_inheritable_parent_refuses_before_staging(tmp_path, kind, inherit):
    adapter, repo, base = prepare(tmp_path, "shared/notes", kind)
    shared = adapter.root / "shared"
    change = _change_set(
        repo,
        base,
        [_produced(path, b"delivery") for path in ("shared/notes", "shared/new", "sibling")],
    )
    # 第一条只管目录本身，第二条才触发拒写。
    subprocess.run(["chmod", "+a", "everyone allow read", shared], check=True)
    subprocess.run(["chmod", "+a", f"everyone deny delete,{inherit}", shared], check=True)
    try:
        rows = apply_change_set(adapter, repo, change)
        captured = {item.path for item in adapter.capture(()).files}
        actual = (
            [(r["status"], r["reason"]) for r in rows],
            sorted(p.name for p in shared.iterdir()),
            captured,
        )
        assert actual == (
            [("refused", "metadata_unpreserved: acl")] * 2 + [("applied", None)],
            ["notes"],
            {"shared/notes", "sibling"},
        )
        assert (shared / "notes").read_bytes() == b"base"
    finally:
        for child in shared.iterdir():
            subprocess.run(["chmod", "-N", child], check=True)
        subprocess.run(["chmod", "-N", shared], check=True)


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 macOS 目录 ACL")
def test_non_inheriting_parent_allows_write(tmp_path):
    adapter, repo, base = prepare(tmp_path, "notes")
    subprocess.run(["chmod", "+a", "everyone deny delete", adapter.root], check=True)
    change = _change_set(
        repo, base, [_produced("new", b"delivery"), _produced("notes", b"delivery")]
    )
    try:
        assert [r["status"] for r in apply_change_set(adapter, repo, change)] == [
            "applied",
            "applied",
        ]
    finally:
        subprocess.run(["chmod", "-N", adapter.root], check=True)


@pytest.mark.parametrize("kind", [DirectoryBase, GitRepoBase])
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(
            "metadata", marks=pytest.mark.skipif(os.name != "posix", reason="需要 POSIX 元数据检查")
        ),
        "rename",
    ],
)
def test_cleanup_retains_reason_and_no_body(tmp_path, monkeypatch, kind, failure):
    adapter, repo, base = prepare(tmp_path, "notes", kind)
    change = _change_set(repo, base, [_produced("notes", b"delivery")])
    if os.name == "nt":
        # 改名时暂存文件也已设为只读，清理须保有原先的写句柄。
        (adapter.root / "notes").chmod(stat.S_IREAD)
    seen_sizes = []

    def reject(source_fd, staged_fd, *info):
        seen_sizes.append(os.fstat(staged_fd).st_size)
        return "metadata_unpreserved: gid"

    def fail_replace(*args, **kwargs):
        raise OSError(errno.EIO, "改名失败")

    def fail_unlink(*args, **kwargs):
        raise PermissionError(errno.EPERM, "暂存条目无法删除")

    with monkeypatch.context() as patch:
        if failure == "metadata":
            patch.setattr(_writeback_metadata, "replacement_problem", reject)
        else:
            patch.setattr(os, "replace", fail_replace)
        patch.setattr(os, "unlink", fail_unlink)
        [row] = apply_change_set(adapter, repo, change)
    [stage] = adapter.root.glob("*.harness")
    # 同后缀的用户文件仍须收入底。
    (adapter.root / ".notes.harness").write_bytes(b"user")
    captured = {entry.path for entry in adapter.capture(()).files}
    expected = (
        ("refused", "metadata_unpreserved: gid")
        if failure == "metadata"
        else ("failed", "write_failed: OSError")
    )
    assert (
        (row["status"], row["reason"]),
        row.get("cleanup_error"),
        stage.read_bytes(),
        captured,
        seen_sizes,
    ) == (
        expected,
        "unlink: PermissionError",
        b"",
        {"notes", ".notes.harness"},
        [0] if failure == "metadata" else [],
    )
    assert (adapter.root / "notes").read_bytes() == b"base"


@pytest.mark.skipif(os.name != "posix", reason="需要 POSIX 模式位")
@pytest.mark.parametrize("kind", [DirectoryBase, GitRepoBase])
def test_mode_silently_cleared_by_chmod(tmp_path, monkeypatch, kind):
    adapter, repo, base = prepare(tmp_path, "notes", kind)
    target = adapter.root / "notes"
    # Linux 复现 setgid 被清除；macOS 沙箱不允许设置 setgid，用所有者执行位验证同一路径。
    bit = stat.S_ISGID if sys.platform == "linux" else stat.S_IXUSR
    original = 0o755 | bit
    target.chmod(original)
    assert stat.S_IMODE(target.stat().st_mode) == original
    before = target.stat()
    change = _change_set(repo, base, [_produced("notes", b"delivery"), _produced("sibling", b"ok")])
    chmod = os.chmod
    # 模拟 chmod 返回成功却未保留请求的位。
    monkeypatch.setattr(os, "chmod", lambda fd, mode: chmod(fd, mode & ~bit))
    row, sibling = apply_change_set(adapter, repo, change)
    assert (row["status"], row["reason"], sibling["status"]) == (
        "refused",
        "metadata_unpreserved: mode",
        "applied",
    )
    assert (
        os.path.samestat(before, target.stat()) and stat.S_IMODE(target.stat().st_mode) == original
    )
    assert target.read_bytes() == b"base"
    assert not list(adapter.root.glob("*.harness"))


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 macOS 暂存 ACL")
def test_new_stage_checks_its_own_acl_before_body(tmp_path, monkeypatch):
    adapter, repo, base = prepare(tmp_path, "notes")
    change = _change_set(repo, base, [_produced("new", b"delivery")])
    open_file = os.open

    def inherit_after_check(path, *args, **kwargs):
        if not str(path).endswith(".harness"):
            return open_file(path, *args, **kwargs)
        subprocess.run(
            ["chmod", "+a", "everyone deny delete,file_inherit", adapter.root], check=True
        )
        try:
            return open_file(path, *args, **kwargs)
        finally:
            subprocess.run(
                ["chmod", "-a", "everyone deny delete,file_inherit", adapter.root], check=True
            )

    monkeypatch.setattr(os, "open", inherit_after_check)
    try:
        [row] = apply_change_set(adapter, repo, change)
        [stage] = adapter.root.glob("*.harness")
        assert (row["status"], row["reason"], row.get("cleanup_error"), stage.read_bytes()) == (
            "refused",
            "metadata_unpreserved: acl",
            "unlink: PermissionError",
            b"",
        )
        assert {item.path for item in adapter.capture(()).files} == {"notes"}
    finally:
        for p in adapter.root.iterdir():
            subprocess.run(["chmod", "-N", p], check=True)


@pytest.mark.skipif(os.name != "posix", reason="需要 POSIX 模式位")
def test_adding_execute_does_not_restore_revoked_read_write(tmp_path, monkeypatch):
    adapter, _, _ = prepare(tmp_path, "notes")
    fstat = os.fstat
    restricted = False

    def restrict_after_creation(fd):
        nonlocal restricted
        info = fstat(fd)
        if not restricted and stat.S_ISREG(info.st_mode):
            restricted = True
            assert info.st_mode & 0o600 == 0o600
            os.fchmod(fd, 0o400)
        return info

    monkeypatch.setattr(os, "fstat", restrict_after_creation)
    row = adapter.write_file("new", b"new", expected=None, executable=True)
    assert (row.status, row.reason) == ("refused", "metadata_unpreserved: mode")
    assert not (adapter.root / "new").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 macOS 文件标志")
def test_flagged_new_stage_is_refused(tmp_path, monkeypatch):
    adapter, _, _ = prepare(tmp_path, "notes")
    open_file = os.open

    def flag_new_stage(path, *args, **kwargs):
        fd = open_file(path, *args, **kwargs)
        if str(path).endswith(".harness"):
            os.chflags(adapter.root / path, stat.UF_NODUMP)
        return fd

    monkeypatch.setattr(os, "open", flag_new_stage)
    row = adapter.write_file("new", b"delivery", expected=None, executable=False)
    assert (row.status, row.reason) == ("refused", "metadata_unpreserved: flags")
    assert {p.name for p in adapter.root.iterdir()} == {"notes"}


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 macOS 目录 ACL")
def test_unreadable_parent_acl_is_refused(tmp_path, monkeypatch):
    import ctypes

    adapter, _, _ = prepare(tmp_path, "notes")
    read_acl = _writeback_metadata._libc.acl_get_fd

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            ctypes.set_errno(errno.EIO)
            return None
        return read_acl(fd)

    monkeypatch.setattr(_writeback_metadata._libc, "acl_get_fd", fail_directory)
    row = adapter.write_file("new", b"delivery", expected=None, executable=False)
    assert (row.status, row.reason) == ("refused", "metadata_unpreserved: acl")
    assert {p.name for p in adapter.root.iterdir()} == {"notes"}

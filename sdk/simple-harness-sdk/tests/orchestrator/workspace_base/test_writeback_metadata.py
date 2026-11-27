# SPDX-License-Identifier: Apache-2.0
"""在逐文件交付入口核对原件、暂存清理与兄弟文件的回执。"""

import errno
import os
import stat
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest
from test_delivery import _change_set, _produced
from test_spelling_writeback import prepare

from agent_orchestrator.artifacts import _xattrs
from agent_orchestrator.artifacts.delivery import apply_change_set
from agent_orchestrator.artifacts.workspace_base import DirectoryBase, GitRepoBase

darwin = pytest.mark.skipif(sys.platform != "darwin", reason="需要 macOS 文件元数据接口")
posix_writeback = pytest.mark.skipif(not _xattrs.AVAILABLE, reason="需要文件描述符属性接口")


def batch(tmp_path, kind=DirectoryBase):
    adapter, repo, base = prepare(tmp_path, "notes", kind)
    change = _change_set(repo, base, [_produced("notes", b"new"), _produced("other", b"other")])
    return adapter.root / "notes", lambda: apply_change_set(adapter, repo, change)


def check_refusal(target, apply, reason):
    before = target.stat()
    entries = set(os.listdir(target.parent))
    row, other = apply()
    assert (row["status"], row["reason"]) == ("refused", reason)
    assert target.read_bytes() == b"base" and os.path.samestat(before, target.stat())
    assert target.stat().st_ctime_ns == before.st_ctime_ns
    assert other["status"] == "applied" and (target.parent / "other").read_bytes() == b"other"
    assert set(os.listdir(target.parent)) == entries | {"other"}


@darwin
@pytest.mark.parametrize("kind", [DirectoryBase, GitRepoBase])
@pytest.mark.parametrize("acl_on_parent", [False, True], ids=["original", "inherited-by-stage"])
def test_extended_acl_refuses_only_replacement(tmp_path, kind, acl_on_parent, monkeypatch):
    target, apply = batch(tmp_path, kind)
    if acl_on_parent:
        # 父目录预检后才出现继承条目，仅本次创建的暂存文件获得 ACL。
        open_file = os.open

        def inherit_once(path, *args, **kwargs):
            if str(path).endswith(".harness"):
                monkeypatch.setattr(os, "open", open_file)
                subprocess.run(
                    ["chmod", "+a", "everyone deny writeextattr,file_inherit", target.parent],
                    check=True,
                )
                try:
                    return open_file(path, *args, **kwargs)
                finally:
                    subprocess.run(
                        ["chmod", "-a", "everyone deny writeextattr,file_inherit", target.parent],
                        check=True,
                    )
            return open_file(path, *args, **kwargs)

        monkeypatch.setattr(os, "open", inherit_once)
    else:
        subprocess.run(["chmod", "+a", "everyone deny writeextattr", target], check=True)
    before = subprocess.check_output(["ls", "-len", target])
    check_refusal(target, apply, "metadata_unpreserved: acl")
    assert subprocess.check_output(["ls", "-len", target]) == before


@darwin
def test_unreadable_acl_refuses_only_replacement(tmp_path, monkeypatch):
    import ctypes

    from agent_orchestrator.artifacts import _writeback_metadata

    target, apply = batch(tmp_path)

    get_acl = _writeback_metadata._libc.acl_get_fd

    def denied(fd):
        if not os.path.samestat(os.fstat(fd), target.stat()):
            return get_acl(fd)
        ctypes.set_errno(errno.EACCES)
        return None

    monkeypatch.setattr(_writeback_metadata._libc, "acl_get_fd", denied)
    check_refusal(target, apply, "metadata_unpreserved: acl")


@darwin
@pytest.mark.parametrize("flag", ["UF_HIDDEN", "UF_NODUMP", "UF_IMMUTABLE"])
def test_user_flag_refuses_only_replacement(tmp_path, flag):
    target, apply = batch(tmp_path)
    previous = target.stat().st_flags
    flags = previous | getattr(stat, flag)
    os.chflags(target, flags)
    try:
        check_refusal(target, apply, "metadata_unpreserved: flags")
        assert target.stat().st_flags == flags
    finally:
        os.chflags(target, previous)


@posix_writeback
def test_different_group_refuses_only_replacement(tmp_path):
    target, apply = batch(tmp_path)
    group = next(
        (gid for gid in [os.getgid(), *os.getgroups()] if gid != target.stat().st_gid), None
    )
    if group is None and os.getuid() == 0:
        group = target.stat().st_gid + 1
    if group is None:
        pytest.skip("当前用户没有可用于真实改组的第二个组")
    os.chown(target, -1, group)
    target.chmod(0o640)
    check_refusal(target, apply, "metadata_unpreserved: gid")
    assert target.stat().st_gid == group and stat.S_IMODE(target.stat().st_mode) == 0o640


@posix_writeback
def test_different_owner_refuses_only_replacement(tmp_path, monkeypatch):
    target, apply = batch(tmp_path)
    before = target.stat()
    fstat = os.fstat

    def owner_of_open_file(fd):
        info = fstat(fd)
        if not os.path.samestat(info, before):
            return info
        # 普通用户不能创建另一个用户的文件，只替换内核返回的所有者字段。
        fields = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
        return SimpleNamespace(**(fields | {"st_uid": info.st_uid + 1}))

    monkeypatch.setattr(os, "fstat", owner_of_open_file)
    check_refusal(target, apply, "metadata_unpreserved: uid")


@posix_writeback
@pytest.mark.parametrize("fail_source", [True, False], ids=["source", "stage"])
def test_final_attribute_read_error_refuses_replacement(tmp_path, monkeypatch, fail_source):
    target, apply = batch(tmp_path)
    source = target.stat()
    seen = set()
    listxattr = _xattrs.listxattr

    def fail_on_reread(fd):
        inode = os.fstat(fd)
        if os.path.samestat(inode, source) == fail_source and fd in seen:
            raise OSError(errno.EIO, "属性复读失败")
        seen.add(fd)
        return listxattr(fd)

    monkeypatch.setattr(_xattrs, "listxattr", fail_on_reread)
    check_refusal(target, apply, "xattr_unpreserved")


@posix_writeback
def test_identical_readonly_attribute_needs_no_write(tmp_path, monkeypatch):
    target, apply = batch(tmp_path)
    monkeypatch.setattr(_xattrs, "listxattr", lambda fd: ["security.readonly"])
    monkeypatch.setattr(_xattrs, "getxattr", lambda fd, name: b"label")

    def forbid_write(fd, name, value):
        raise OSError(errno.EPERM, "只读属性")

    monkeypatch.setattr(_xattrs, "setxattr", forbid_write)
    assert [row["status"] for row in apply()] == ["applied", "applied"]
    assert target.read_bytes() == b"new"


@pytest.mark.skipif(sys.platform != "linux", reason="需要 Linux POSIX ACL")
def test_visible_posix_acl_survives_replacement(tmp_path):
    target, apply = batch(tmp_path)
    # Linux 内核的 ACL 字节布局，额外授予一个具名用户读权限。
    undefined = 0xFFFFFFFF
    entries = [
        (1, 6, undefined),
        (2, 4, 1234),
        (4, 4, undefined),
        (16, 4, undefined),
        (32, 0, undefined),
    ]
    acl = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *entry) for entry in entries)
    name = "system.posix_acl_access"
    try:
        os.setxattr(target, name, acl)
    except OSError as error:
        if error.errno == errno.ENOTSUP:
            pytest.skip("文件系统不支持 POSIX ACL")
        raise
    assert os.getxattr(target, name) == acl
    assert [row["status"] for row in apply()] == ["applied", "applied"]
    assert target.read_bytes() == b"new" and os.getxattr(target, name) == acl

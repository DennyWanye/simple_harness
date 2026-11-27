# SPDX-License-Identifier: Apache-2.0
"""在真实工作树上核对交付回执、扩展属性以及同键重放。"""

import errno
import os
import subprocess
import sys

import pytest
from test_delivery import _change_set, _produced
from test_spelling_writeback import prepare

from agent_orchestrator.artifacts.delivery import DeliveryLedger, apply_change_set
from agent_orchestrator.artifacts.workspace_base import DirectoryBase, GitRepoBase

requires_xattrs = pytest.mark.skipif(
    sys.platform != "darwin" and not hasattr(os, "listxattr"), reason="本平台没有扩展属性接口"
)
CUSTOM = "user.delivery"


def set_attr(path, name, value):
    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/xattr", "-wx", name, value.hex(), path], check=True)
    else:
        os.setxattr(path, name, value)


def disk_attrs(path):
    if sys.platform == "darwin":
        names = subprocess.check_output(["/usr/bin/xattr", path], text=True).splitlines()
        return {
            name: bytes.fromhex(
                subprocess.check_output(["/usr/bin/xattr", "-px", name, path], text=True)
            )
            for name in names
        }
    return {name: os.getxattr(path, name) for name in os.listxattr(path)}


@requires_xattrs
@pytest.mark.parametrize("kind", [DirectoryBase, GitRepoBase])
def test_current_attributes_survive_delivery_and_replay(tmp_path, kind):
    adapter, repo, base = prepare(tmp_path, "notes", kind)
    change = _change_set(repo, base, [_produced("notes", b"delivered")])
    target = adapter.root / "notes"
    set_attr(target, CUSTOM, b"\x00user\xff")
    if sys.platform == "darwin":
        (target / "..namedfork/rsrc").write_bytes(b"edited after judgment\x00\xff")
        set_attr(target, "com.apple.quarantine", b"0081;00000001;simple_harness;")
    set_attr(target, "user.second", b"")
    expected = disk_attrs(target)

    def deliver():
        return DeliveryLedger(tmp_path / "ledger").apply(
            "approved",
            mission_id="m1",
            paths=None,
            run=lambda: apply_change_set(adapter, repo, change),
        )

    first = deliver()
    assert first["files"][0]["status"] == "applied"
    assert target.read_bytes() == b"delivered"
    assert disk_attrs(target) == expected
    set_attr(target, CUSTOM, b"edited after writeback")
    before_replay = target.stat()
    assert deliver() == {**first, "repeat": True}
    assert target.stat().st_ctime_ns == before_replay.st_ctime_ns
    assert disk_attrs(target) == {**expected, CUSTOM: b"edited after writeback"}


@requires_xattrs
@pytest.mark.parametrize("kind", [DirectoryBase, GitRepoBase])
@pytest.mark.parametrize(
    "operation,code",
    [
        ("listxattr", errno.EACCES),
        ("getxattr", errno.ENOTSUP),
        ("setxattr", errno.EACCES),
        ("setxattr", errno.ENOTSUP),
    ],
)
def test_attribute_errors_refuse_only_this_replacement(
    tmp_path, monkeypatch, kind, operation, code
):
    from agent_orchestrator.artifacts import _xattrs

    adapter, repo, base = prepare(tmp_path, "notes", kind)
    change = _change_set(repo, base, [_produced("notes", b"new"), _produced("other", b"other")])
    target = adapter.root / "notes"
    set_attr(target, CUSTOM, b"old")
    expected = disk_attrs(target)
    original = getattr(_xattrs, operation)

    def fail(*args):
        if operation == "setxattr" and code == errno.EACCES:
            # 即使值已到达暂存文件，系统调用一旦报错，本次替换也应拒绝。
            original(*args)
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(_xattrs, operation, fail)
    row, other = apply_change_set(adapter, repo, change)
    assert (row["status"], row["reason"]) == ("refused", "xattr_unpreserved")
    assert target.read_bytes() == b"base" and disk_attrs(target) == expected
    assert other["status"] == "applied" and CUSTOM not in disk_attrs(adapter.root / "other")
    assert sorted(os.listdir(adapter.root)) == sorted(
        ["notes", "other"] + ([".git"] if kind is GitRepoBase else [])
    )


@requires_xattrs
@pytest.mark.parametrize("interference", ["edit", "add", "silent_write_loss"])
def test_changes_during_copy_refuse_replacement(tmp_path, monkeypatch, interference):
    from agent_orchestrator.artifacts import _xattrs, workspace_base

    adapter, repo, base = prepare(tmp_path, "notes")
    change = _change_set(repo, base, [_produced("notes", b"new")])
    target = adapter.root / "notes"
    set_attr(target, CUSTOM, b"old")
    expected = disk_attrs(target)
    write = _xattrs.setxattr
    # 模拟两次更改落在文件系统的同一时间刻度，仍须靠属性字节发现并发编辑。
    version = workspace_base._version
    monkeypatch.setattr(
        workspace_base, "_version", lambda info: version(info)[:2] + (info.st_size,)
    )

    def interfere(fd, name, value):
        if os.fsdecode(name) == CUSTOM:
            if interference == "silent_write_loss":
                return
            changed = CUSTOM if interference == "edit" else "user.late"
            set_attr(target, changed, b"late")
            expected[changed] = b"late"
        write(fd, name, value)

    monkeypatch.setattr(_xattrs, "setxattr", interfere)
    [row] = apply_change_set(adapter, repo, change)
    assert (row["status"], row["reason"]) == ("refused", "xattr_unpreserved")
    assert target.read_bytes() == b"base" and disk_attrs(target) == expected
    assert os.listdir(adapter.root) == ["notes"]


@requires_xattrs
@pytest.mark.parametrize("listing", ["native", "empty", "unsupported"])
def test_plain_file_can_be_replaced(tmp_path, monkeypatch, listing):
    from agent_orchestrator.artifacts import _xattrs

    adapter, repo, base = prepare(tmp_path, "notes")
    change = _change_set(repo, base, [_produced("notes", b"new")])
    target = adapter.root / "notes"
    expected = disk_attrs(target)
    if listing == "empty":
        monkeypatch.setattr(_xattrs, "listxattr", lambda fd: [])
    if listing == "unsupported":

        def no_attributes(fd):
            raise OSError(errno.ENOTSUP, "文件系统不支持扩展属性")

        monkeypatch.setattr(_xattrs, "listxattr", no_attributes)
    [row] = apply_change_set(adapter, repo, change)
    assert row["status"] == "applied"
    assert target.read_bytes() == b"new" and disk_attrs(target) == expected


@requires_xattrs
def test_same_bytes_replacement_after_copy_is_refused(tmp_path, monkeypatch):
    adapter, repo, base = prepare(tmp_path, "notes")
    change = _change_set(repo, base, [_produced("notes", b"new")])
    target = adapter.root / "notes"
    chmod = os.chmod

    def replace_after_copy(fd, mode):
        chmod(fd, mode)
        replacement = tmp_path / "edited"
        replacement.write_bytes(b"base")
        set_attr(replacement, CUSTOM, b"new owner")
        replacement.replace(target)

    monkeypatch.setattr(os, "chmod", replace_after_copy)
    [row] = apply_change_set(adapter, repo, change)
    assert (row["status"], row["reason"]) == ("refused", "path_changed")
    assert target.read_bytes() == b"base" and disk_attrs(target)[CUSTOM] == b"new owner"


def test_writeback_without_a_platform_attribute_api(tmp_path, monkeypatch):
    from agent_orchestrator.artifacts import _xattrs

    adapter, repo, base = prepare(tmp_path, "notes")
    change = _change_set(repo, base, [_produced("notes", b"new")])
    monkeypatch.setattr(_xattrs, "AVAILABLE", False)
    monkeypatch.setattr(_xattrs, "snapshot", lambda fd: pytest.fail("不应调用扩展属性接口"))
    [row] = apply_change_set(adapter, repo, change)
    assert row["status"] == "applied" and (adapter.root / "notes").read_bytes() == b"new"

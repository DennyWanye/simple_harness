# SPDX-License-Identifier: Apache-2.0
"""底提交：宿主交来的底被拍成 SDK 工作仓库里的一个 git 提交（设计稿 §4.1–4.3）。

接缝是 :func:`capture_base` + :class:`BaseRepo`：给一个底适配器和范围、上限，得到一个底记录；
之后任何时候都能从提交号把同样的字节铺回一个目录。全部用真 git、临时目录，不联网。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agent_orchestrator.artifacts.workspace_base import (
    BaseRepo,
    BaseTooLarge,
    CaptureLimits,
    DirectoryBase,
    GitRepoBase,
    InMemoryBase,
    capture_base,
)

RESERVED = (".harness/sources/", ".assurance/", ".harness/accepted-outputs/")


def _no_secrets(_text: str) -> bool:
    return False


def _capture(repo: BaseRepo, base, **kwargs):
    return capture_base(
        base,
        repo,
        scope=kwargs.pop("scope", ()),
        limits=kwargs.pop("limits", CaptureLimits()),
        is_secret=kwargs.pop("is_secret", _no_secrets),
        reserved=kwargs.pop("reserved", RESERVED),
    )


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout


def test_git_is_probed_once_and_reports_its_version(tmp_path):
    probe = BaseRepo(tmp_path / "bases" / "work.git").probe()
    assert probe["ok"] is True, probe
    assert tuple(int(part) for part in probe["version"].split(".")[:2]) >= (2, 38)


def test_a_base_round_trips_through_a_commit_with_exact_bytes(tmp_path):
    repo = BaseRepo(tmp_path / "bases" / "work.git")
    files = {
        "ledger/__init__.py": b"",
        "ledger/book.py": b"class Book:\r\n    pass\r\n",  # 换行原样保留，不做任何转换
        "docs/说明 文档.md": "# 中文路径带空格\n".encode(),
        "bin/tool.sh": b"#!/bin/sh\necho hi\n",
    }
    record = _capture(repo, InMemoryBase(files, executable={"bin/tool.sh"}))
    assert len(record.commit) == 40
    assert record.files == 4 and record.withheld == {}
    manifest = repo.manifest(record.commit)
    assert set(manifest) == set(files)
    assert manifest["bin/tool.sh"].executable is True
    assert manifest["ledger/book.py"].readable is True

    target = tmp_path / "tree"
    repo.materialise(record.commit, target)
    for path, data in files.items():
        assert (target / path).read_bytes() == data
    assert os.access(target / "bin/tool.sh", os.X_OK)

    # 同样的内容再拍一次是同一个提交（拍快照是确定的）
    again = _capture(repo, InMemoryBase(files, executable={"bin/tool.sh"}))
    assert again.commit == record.commit


def test_withheld_files_never_enter_the_commit(tmp_path):
    repo = BaseRepo(tmp_path / "bases" / "work.git")
    files = {
        "app.py": b"print('ok')\n",
        ".env": b"SH_APIKEY=whatever\n",
        "config/.env.local": b"X=1\n",
        "keys/server.pem": b"not really\n",
        "notes.txt": b"token sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345\n",
        ".harness/sources/brief.md": b"collides with the SDK's reference root\n",
        "Readme.md": b"a\n",
        "README.md": b"b\n",
        "bad\udcffname.txt": b"not utf-8 on disk\n",
        "line\nbreak.txt": b"x\n",
    }

    def is_secret(text: str) -> bool:
        return "sk-" in text

    record = _capture(repo, InMemoryBase(files), is_secret=is_secret)
    assert set(repo.manifest(record.commit)) == {"app.py"}
    assert record.withheld == {
        ".env": "secret_name",
        "config/.env.local": "secret_name",
        "keys/server.pem": "secret_name",
        "notes.txt": "secret_content",
        ".harness/sources/brief.md": "reserved_path",
        "Readme.md": "case_conflict",
        "README.md": "case_conflict",
        "bad\udcffname.txt": "invalid_path",
        "line\nbreak.txt": "invalid_path",
    }


def test_binary_and_oversized_files_are_kept_but_marked_unreadable(tmp_path):
    repo = BaseRepo(tmp_path / "bases" / "work.git")
    big = b"x" * (512 * 1024 + 1)
    files = {"logo.png": b"\x89PNG\r\n\x1a\n\x00\x00", "data/big.txt": big, "ok.txt": b"ok\n"}
    record = _capture(repo, InMemoryBase(files))
    manifest = repo.manifest(record.commit)
    assert manifest["logo.png"].readable is False
    assert manifest["data/big.txt"].readable is False
    assert manifest["ok.txt"].readable is True
    target = tmp_path / "tree"
    repo.materialise(record.commit, target)
    assert (target / "data/big.txt").read_bytes() == big


def test_scope_keeps_only_the_named_prefixes(tmp_path):
    repo = BaseRepo(tmp_path / "bases" / "work.git")
    files = {
        "ledger/book.py": b"1\n",
        "ledger/money.py": b"2\n",
        "web/app.js": b"3\n",
        "x.md": b"4\n",
    }
    record = _capture(repo, InMemoryBase(files), scope=("ledger/", "x.md"))
    assert set(repo.manifest(record.commit)) == {"ledger/book.py", "ledger/money.py", "x.md"}
    assert record.scope == ("ledger/", "x.md")


def test_a_base_over_the_limits_is_refused_with_its_size(tmp_path):
    repo = BaseRepo(tmp_path / "bases" / "work.git")
    files = {f"f{i}.txt": b"0123456789" for i in range(5)}
    with pytest.raises(BaseTooLarge) as caught:
        _capture(repo, InMemoryBase(files), limits=CaptureLimits(max_files=4, max_bytes=1000))
    assert (caught.value.files, caught.value.bytes) == (5, 50)
    with pytest.raises(BaseTooLarge):
        _capture(repo, InMemoryBase(files), limits=CaptureLimits(max_files=10, max_bytes=49))


def test_a_directory_base_withholds_symlinks_and_skips_tool_caches(tmp_path):
    root = tmp_path / "plain"
    (root / "pkg" / "__pycache__").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text("x = 1\n")
    (root / "pkg" / "__pycache__" / "mod.cpython-311.pyc").write_bytes(b"\x00")
    (root / "outside.txt").symlink_to("/etc/hosts")
    repo = BaseRepo(tmp_path / "bases" / "work.git")
    record = _capture(repo, DirectoryBase(root))
    assert set(repo.manifest(record.commit)) == {"pkg/mod.py"}
    assert record.withheld == {"outside.txt": "symlink"}
    assert record.source == {"kind": "directory", "location": os.path.realpath(root)}


def test_a_git_repo_base_is_the_working_tree_without_ignored_files(tmp_path):
    user = tmp_path / "user-repo"
    user.mkdir()
    _git(user, "init", "-q")
    (user / ".gitignore").write_text(".env\nbuild/\n")
    (user / "ledger").mkdir()
    (user / "ledger" / "book.py").write_text("committed = True\n")
    _git(user, "add", ".")
    _git(user, "-c", "user.name=u", "-c", "user.email=u@x", "commit", "-qm", "init")
    head = _git(user, "rev-parse", "HEAD").strip()
    # 没提交的改动、未跟踪的新文件都算底；被忽略的不算；用户仓库的 .git 一点不改
    (user / "ledger" / "book.py").write_text("committed = False  # 改了没提交\n")
    (user / "ledger" / "money.py").write_text("new = 1\n")
    (user / ".env").write_text("SECRET=1\n")
    (user / "build").mkdir()
    (user / "build" / "out.bin").write_bytes(b"\x00")
    before = _git(user, "status", "--porcelain")
    refs_before = _git(user, "for-each-ref")

    repo = BaseRepo(tmp_path / "bases" / "work.git")
    record = _capture(repo, GitRepoBase(user))
    assert set(repo.manifest(record.commit)) == {".gitignore", "ledger/book.py", "ledger/money.py"}
    assert (
        repo.read(record.commit, "ledger/book.py") == "committed = False  # 改了没提交\n".encode()
    )
    assert record.source == {"kind": "git", "location": os.path.realpath(user), "head": head}
    assert _git(user, "status", "--porcelain") == before
    assert _git(user, "for-each-ref") == refs_before


def test_a_file_reached_through_a_linked_directory_is_withheld(tmp_path):
    # 核验发现：索引里记着 lib/conf.py，用户把 lib/ 换成了指向仓库外的软链（没提交）
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "conf.py").write_text("SECRET_OUTSIDE = 1\n")
    user = tmp_path / "user-repo"
    (user / "lib").mkdir(parents=True)
    (user / "lib" / "conf.py").write_text("inside = 1\n")
    (user / "app.py").write_text("x = 1\n")
    _git(user, "init", "-q")
    _git(user, "add", ".")
    _git(user, "-c", "user.name=u", "-c", "user.email=u@x", "commit", "-qm", "init")
    (user / "lib" / "conf.py").unlink()
    (user / "lib").rmdir()
    (user / "lib").symlink_to(outside)
    repo = BaseRepo(tmp_path / "bases" / "work.git")
    for base in (GitRepoBase(user), DirectoryBase(user)):
        record = _capture(repo, base)
        assert set(repo.manifest(record.commit)) == {"app.py"}, base
        assert (
            record.withheld.get("lib/conf.py") == "symlink"
            or record.withheld.get("lib") == "symlink"
        )


def test_the_users_global_excludes_are_honoured(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "global-ignore").write_text("secrets.json\n")
    (home / ".gitconfig").write_text(f"[core]\n\texcludesFile = {home / 'global-ignore'}\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    user = tmp_path / "user-repo"
    user.mkdir()
    _git(user, "init", "-q")
    (user / "app.py").write_text("x = 1\n")
    (user / "secrets.json").write_text('{"password": "hunter2"}\n')
    repo = BaseRepo(tmp_path / "bases" / "work.git")
    record = _capture(repo, GitRepoBase(user))
    assert set(repo.manifest(record.commit)) == {"app.py"}


def test_directories_that_differ_only_in_case_are_withheld_not_crashed(tmp_path):
    repo = BaseRepo(tmp_path / "bases" / "work.git")
    files = {"Docs/a.md": b"a\n", "docs/b.md": b"b\n", "x.md": b"x\n"}
    record = _capture(repo, InMemoryBase(files))
    assert set(repo.manifest(record.commit)) == {"x.md"}
    assert record.withheld == {"Docs/a.md": "case_conflict", "docs/b.md": "case_conflict"}


def test_the_work_repository_keeps_paths_that_differ_only_in_case(tmp_path):
    # 工作仓库本身逐字区分大小写（macOS 上 init 默认会打开 ignorecase）
    repo = BaseRepo(tmp_path / "bases" / "work.git")
    commit = repo.commit_files({"Docs/a.md": b"a\n", "docs/b.md": b"b\n"})
    assert set(repo.manifest(commit)) == {"Docs/a.md", "docs/b.md"}


def test_a_git_pointer_file_is_skipped(tmp_path):
    root = tmp_path / "worktree"
    (root / "sub").mkdir(parents=True)
    (root / ".git").write_text("gitdir: /somewhere/else\n")
    (root / "sub" / ".git").write_text("gitdir: ../.git/modules/sub\n")
    (root / "sub" / "mod.py").write_text("y = 2\n")
    repo = BaseRepo(tmp_path / "bases" / "work.git")
    record = _capture(repo, DirectoryBase(root))
    assert set(repo.manifest(record.commit)) == {"sub/mod.py"}
    assert not list(repo.path.glob("fast_import_crash_*"))


def test_an_unreadable_file_and_git_aliases_are_withheld_not_fatal(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "ok.py").write_text("x = 1\n")
    locked = root / "locked.txt"
    locked.write_text("secret\n")
    locked.chmod(0)
    (root / "git~1").mkdir()
    (root / "git~1" / "config").write_text("x\n")
    (root / ".git.").write_text("x\n")
    (root / ".git::$INDEX_ALLOCATION").write_text("x\n")
    (root / ".g\u200cit").write_text("x\n")
    try:
        repo = BaseRepo(tmp_path / "bases" / "work.git")
        record = _capture(repo, DirectoryBase(root))
        assert set(repo.manifest(record.commit)) == {"ok.py"}
        assert record.withheld == {
            "locked.txt": "unreadable",
            "git~1/config": "invalid_path",
            ".git.": "invalid_path",
            ".git::$INDEX_ALLOCATION": "invalid_path",
            ".g\u200cit": "invalid_path",
        }
        assert not list(repo.path.glob("fast_import_crash_*"))
    finally:
        locked.chmod(0o600)


def test_listing_a_users_repo_never_runs_its_fsmonitor(tmp_path):
    user = tmp_path / "user-repo"
    user.mkdir()
    _git(user, "init", "-q")
    marker = tmp_path / "fsmonitor-ran"
    hook = tmp_path / "fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    hook.chmod(0o755)
    _git(user, "config", "core.fsmonitor", str(hook))
    (user / "app.py").write_text("x = 1\n")
    repo = BaseRepo(tmp_path / "bases" / "work.git")
    record = _capture(repo, GitRepoBase(user))
    assert set(repo.manifest(record.commit)) == {"app.py"}
    assert not marker.exists()


def test_a_long_listing_is_summarised_by_top_level_directory():
    from agent_orchestrator.artifacts.workspace_base import workspace_listing

    paths = [f"web/asset{i}.js" for i in range(500)] + ["README.md", "ledger/book.py"]
    assert workspace_listing(paths) == ["README.md", "ledger/ (1 个文件)", "web/ (500 个文件)"]
    assert workspace_listing(["b.md", "a.md"]) == ["a.md", "b.md"]


def test_canonical_unicode_aliases_are_withheld_before_materialising_the_base(tmp_path):
    repo = BaseRepo(tmp_path / "work.git")
    files = {"café/one.md": b"one", "cafe\u0301/two.md": b"two", "safe.md": b"safe"}
    record = _capture(repo, InMemoryBase(files))
    assert repo.files(record.commit) == {"safe.md": b"safe"}
    assert set(record.withheld) == set(files) - {"safe.md"}

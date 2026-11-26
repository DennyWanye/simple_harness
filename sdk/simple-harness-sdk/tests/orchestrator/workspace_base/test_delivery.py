# SPDX-License-Identifier: Apache-2.0
"""任务交付：改动集与写回（设计稿 §4.7，P2a）。

两个接缝：

* :func:`build_change_set` + :func:`delivery_diff`：给底记录和任务的产物，得到相对底提交的改动集，
  工作仓库里钉一个交付提交（父提交 = 底提交），diff 由 git 在两个提交之间算；
* :func:`apply_change_set` + :class:`DeliveryLedger`：按改动集写回可写的底，逐文件三方比对，
  同一操作键不重放。

真 git、真临时目录，不联网。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agent_orchestrator.artifacts.delivery import (
    DeliveryLedger,
    DeliveryRefused,
    Produced,
    apply_change_set,
    build_change_set,
    delivery_diff,
    git_blob_id,
)
from agent_orchestrator.artifacts.workspace_base import (
    BaseError,
    BaseRepo,
    CaptureLimits,
    DirectoryBase,
    GitRepoBase,
    InMemoryBase,
    capture_base,
)

RESERVED = ("sources/", ".assurance/", "accepted-outputs/")
MONEY = b"def cents(x):\n    return int(x * 100)\n"
MONEY_NEW = b"def cents(x):\n    return round(x * 100)\n"
TEST_OLD = b"def test_cents():\n    assert True\n"


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout


def _base(repo: BaseRepo, adapter) -> dict:
    return capture_base(
        adapter,
        repo,
        scope=(),
        limits=CaptureLimits(),
        is_secret=lambda _t: False,
        reserved=RESERVED,
    ).to_json()


def _produced(path: str, data: bytes, task: str = "t1") -> Produced:
    return Produced(path=path, data=data, artifact_id=f"art-{path}", task_id=task)


def _change_set(repo, base, produced, **kwargs):
    return build_change_set(repo, base, produced, reserved=RESERVED, **kwargs)


def test_the_change_set_is_the_real_changes_against_the_base(tmp_path):
    repo = BaseRepo(tmp_path / "work.git")
    base = _base(
        repo,
        InMemoryBase(
            {"ledger/money.py": MONEY, "tests/test_money.py": TEST_OLD, "README.md": b"hi\n"},
            executable=["ledger/money.py"],
        ),
    )
    change = _change_set(
        repo,
        base,
        [
            _produced("ledger/money.py", MONEY_NEW),
            _produced("ledger/cli.py", b"print('cli')\n"),
            _produced("tests/test_money.py", TEST_OLD + b"# more\n"),
            _produced("tests/test_cli.py", b"def test_cli():\n    pass\n"),
            _produced("README.md", b"hi\n"),  # 与底相同：不是改动
            _produced("accepted-outputs/t2/report/REPORT.md", b"kept\n"),  # SDK 自己的路径
        ],
    )
    files = {item["path"]: item for item in change["files"]}
    assert sorted(files) == [
        "ledger/cli.py",
        "ledger/money.py",
        "tests/test_cli.py",
        "tests/test_money.py",
    ]
    assert files["ledger/money.py"]["op"] == "modify"
    assert files["ledger/money.py"]["base_blob"] == git_blob_id(MONEY)
    assert files["ledger/money.py"]["blob"] == git_blob_id(MONEY_NEW)
    assert files["ledger/cli.py"]["op"] == "add" and files["ledger/cli.py"]["base_blob"] is None
    # 改了底里已有的测试才单列；新写的测试不算
    assert files["tests/test_money.py"]["test"] is True
    assert files["tests/test_cli.py"]["test"] is False
    assert files["ledger/money.py"]["test"] is False
    assert change["base_commit"] == base["commit"]
    assert change["counts"] == {"files": 4, "added": 2, "modified": 2, "tests": 1, "refused": 0}
    # 交付提交的父提交是底提交；改过的文件保留原来的可执行位
    work = tmp_path / "work.git"
    assert _git(work, "rev-parse", f"{change['commit']}^").strip() == base["commit"]
    assert _git(work, "ls-tree", change["commit"], "ledger/money.py").startswith("100755")
    assert _git(work, "show", f"{change['commit']}:ledger/money.py") == MONEY_NEW.decode()


def test_the_same_delivery_gives_the_same_commit(tmp_path):
    repo = BaseRepo(tmp_path / "work.git")
    base = _base(repo, InMemoryBase({"a.py": b"a\n"}))
    first = _change_set(repo, base, [_produced("a.py", b"b\n")])
    again = _change_set(repo, base, [_produced("a.py", b"b\n")])
    assert first == again


def test_a_diff_is_computed_between_the_two_commits(tmp_path):
    repo = BaseRepo(tmp_path / "work.git")
    base = _base(repo, InMemoryBase({"ledger/money.py": MONEY, "logo.png": b"\x89PNG\0\0"}))
    change = _change_set(
        repo,
        base,
        [_produced("ledger/money.py", MONEY_NEW), _produced("ledger/cli.py", b"x = 1\n")],
    )
    diff = delivery_diff(repo, change, "ledger/money.py")
    assert "-    return int(x * 100)" in diff and "+    return round(x * 100)" in diff
    assert "+x = 1" in delivery_diff(repo, change, "ledger/cli.py")
    with pytest.raises(KeyError):
        delivery_diff(repo, change, "logo.png")  # 不在改动集里


def test_paths_the_base_withheld_or_that_clash_are_marked_not_to_be_written(tmp_path):
    repo = BaseRepo(tmp_path / "work.git")
    base = _base(repo, InMemoryBase({"Ledger/money.py": MONEY}))
    base["withheld"] = {"config/.env.local": "secret_name"}
    change = _change_set(
        repo,
        base,
        [
            _produced("ledger/money.py", MONEY_NEW),  # 与底里的 Ledger/ 只差大小写
            _produced("config/.env.local", b"X=1\n"),
            _produced("deploy/prod.pem", b"-----\n"),
        ],
    )
    refused = {item["path"]: item["refused"] for item in change["files"]}
    assert refused == {
        "ledger/money.py": "case_conflict",
        "config/.env.local": "withheld:secret_name",
        "deploy/prod.pem": "secret_name",
    }
    assert change["counts"]["refused"] == 3


def test_steps_whose_version_was_overridden_are_named(tmp_path):
    repo = BaseRepo(tmp_path / "work.git")
    base = _base(repo, InMemoryBase({"README.md": b"hi\n"}))
    change = _change_set(
        repo,
        base,
        [_produced("README.md", b"by t2\n", task="t2")],
        superseded={"README.md": ["t1"]},
    )
    [entry] = change["files"]
    assert entry["task_id"] == "t2" and entry["overridden_steps"] == ["t1"]


# ---------------------------------------------------------------- 写回


def _repo_with_files(root: Path, files: dict[str, bytes]) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    for path, data in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=u", "-c", "user.email=u@x", "commit", "-qm", "init")
    return root


def _delivery_on_a_repo(tmp_path):
    user = _repo_with_files(
        tmp_path / "user",
        {"ledger/money.py": MONEY, "README.md": b"hi\n", "notes.md": b"n\n"},
    )
    repo = BaseRepo(tmp_path / "work.git")
    adapter = GitRepoBase(user)
    base = _base(repo, adapter)
    change = _change_set(
        repo,
        base,
        [
            _produced("ledger/money.py", MONEY_NEW),
            _produced("ledger/cli.py", b"x = 1\n"),
            _produced("README.md", b"hello\n"),
            _produced("notes.md", b"by the task\n"),
        ],
    )
    return user, repo, adapter, change


def test_applying_writes_uncommitted_changes_and_never_touches_git(tmp_path):
    user, repo, adapter, change = _delivery_on_a_repo(tmp_path)
    head = _git(user, "rev-parse", "HEAD")
    index = (user / ".git" / "index").read_bytes()
    # 任务进行时用户自己改了 README.md；notes.md 改成了和任务一样的内容
    (user / "README.md").write_bytes(b"edited by me\n")
    (user / "notes.md").write_bytes(b"by the task\n")
    receipt = apply_change_set(adapter, repo, change)
    status = {item["path"]: item["status"] for item in receipt}
    assert status == {
        "README.md": "conflict",
        "ledger/cli.py": "applied",
        "ledger/money.py": "applied",
        "notes.md": "already",
    }
    conflict = next(item for item in receipt if item["path"] == "README.md")
    assert conflict["current_blob"] == git_blob_id(b"edited by me\n")
    assert (user / "README.md").read_bytes() == b"edited by me\n"  # 冲突不写
    assert (user / "ledger/money.py").read_bytes() == MONEY_NEW
    assert (user / "ledger/cli.py").read_bytes() == b"x = 1\n"
    # 只改工作目录：HEAD、索引原样，改动在 git status 里是未提交的
    assert _git(user, "rev-parse", "HEAD") == head
    assert (user / ".git" / "index").read_bytes() == index
    assert " M ledger/money.py" in _git(user, "status", "--porcelain")


def test_only_the_chosen_files_are_written(tmp_path):
    user, repo, adapter, change = _delivery_on_a_repo(tmp_path)
    receipt = apply_change_set(adapter, repo, change, paths=["ledger/cli.py"])
    assert [(item["path"], item["status"]) for item in receipt] == [("ledger/cli.py", "applied")]
    assert (user / "ledger/money.py").read_bytes() == MONEY
    with pytest.raises(DeliveryRefused):
        apply_change_set(adapter, repo, change, paths=["not/in/the/change.py"])


def test_a_path_through_a_symlink_or_a_refused_path_is_not_written(tmp_path):
    user = tmp_path / "user"
    outside = tmp_path / "outside"
    outside.mkdir()
    user.mkdir()
    (user / "a.py").write_bytes(b"a\n")
    repo = BaseRepo(tmp_path / "work.git")
    adapter = DirectoryBase(user)
    base = _base(repo, adapter)
    change = _change_set(
        repo,
        base,
        [_produced("linked/new.py", b"x\n"), _produced("deploy/prod.pem", b"k\n")],
    )
    (user / "linked").symlink_to(outside, target_is_directory=True)  # 任务期间多出来的软链
    receipt = {item["path"]: item for item in apply_change_set(adapter, repo, change)}
    assert receipt["linked/new.py"]["status"] == "refused"
    assert receipt["linked/new.py"]["reason"] == "symlink"
    assert receipt["deploy/prod.pem"]["status"] == "refused"
    assert not (outside / "new.py").exists()
    assert not (user / "deploy").exists()


def test_a_file_that_does_not_read_back_is_reported_failed(tmp_path):
    class Lossy(InMemoryBase):
        def write_file(self, path, data, *, expected, executable):
            outcome = super().write_file(path, data, expected=expected, executable=executable)
            self.files[path] = b"something else\n"  # 写进去的和读回来的不一样
            return outcome

    repo = BaseRepo(tmp_path / "work.git")
    adapter = Lossy({"a.py": b"a\n"})
    change = _change_set(repo, _base(repo, adapter), [_produced("a.py", b"b\n")])
    [item] = apply_change_set(adapter, repo, change)
    assert item["status"] == "failed" and item["reason"] == "verify_mismatch"


def test_the_ledger_never_replays_an_operation_key(tmp_path):
    user, repo, adapter, change = _delivery_on_a_repo(tmp_path)
    ledger = DeliveryLedger(tmp_path / "deliveries")
    calls = []

    def run():
        calls.append(1)
        return apply_change_set(adapter, repo, change)

    first = ledger.apply("op-1", mission_id="m1", paths=None, run=run)
    # 用户拿到回执后又改了文件：同一个键再调只返回原回执，不再写
    (user / "ledger/money.py").write_bytes(MONEY)
    again = ledger.apply("op-1", mission_id="m1", paths=None, run=run)
    assert again == {**first, "repeat": True}
    assert len(calls) == 1
    assert (user / "ledger/money.py").read_bytes() == MONEY
    with pytest.raises(DeliveryRefused) as refused:
        ledger.apply("op-1", mission_id="m1", paths=["README.md"], run=run)
    assert refused.value.code == "idempotency_conflict"


def test_a_crash_after_the_claim_is_resumed_safely(tmp_path):
    user, repo, adapter, change = _delivery_on_a_repo(tmp_path)
    ledger = DeliveryLedger(tmp_path / "deliveries")

    def crash():
        apply_change_set(adapter, repo, change, paths=["ledger/cli.py"])
        raise RuntimeError("进程在写回半路没了")

    with pytest.raises(RuntimeError):
        ledger.apply("op-2", mission_id="m1", paths=["ledger/cli.py", "ledger/money.py"], run=crash)
    resumed = ledger.apply(
        "op-2",
        mission_id="m1",
        paths=["ledger/cli.py", "ledger/money.py"],
        run=lambda: apply_change_set(
            adapter, repo, change, paths=["ledger/cli.py", "ledger/money.py"]
        ),
    )
    # 半路写过的文件现在已是目标：三方比对认出来，不会写两遍，也不会当冲突
    assert {item["path"]: item["status"] for item in resumed["files"]} == {
        "ledger/cli.py": "already",
        "ledger/money.py": "applied",
    }
    assert resumed["resumed"] is True


def test_a_new_file_that_clashes_in_case_with_a_users_new_directory_is_not_written(tmp_path):
    # 任务期间用户建了 Docs/；任务新写的 docs/guide.md 在不区分大小写的磁盘上会落进 Docs/
    user = tmp_path / "user"
    user.mkdir()
    (user / "a.py").write_bytes(b"a\n")
    repo = BaseRepo(tmp_path / "work.git")
    adapter = DirectoryBase(user)
    change = _change_set(repo, _base(repo, adapter), [_produced("docs/guide.md", b"# guide\n")])
    (user / "Docs").mkdir()
    [item] = apply_change_set(adapter, repo, change)
    assert (item["status"], item["reason"]) == ("refused", "case_conflict")
    assert os.listdir(user / "Docs") == []


# ---------------------------------------------------------------- 核验第一轮


def test_a_file_name_cannot_rewrite_the_delivery_commit(tmp_path):
    # 核验第一轮：文件名原样拼进 fast-import 流，带换行的名字能把别的文件在交付提交里改成另一份
    repo = BaseRepo(tmp_path / "work.git")
    base = _base(repo, InMemoryBase({"ledger/money.py": MONEY}))
    evil = "ledger/zz\ndata 0\n\nM 100644 inline ledger/money.py"
    change = _change_set(
        repo,
        base,
        [
            _produced("ledger/money.py", b"import os\n"),
            _produced(evil, MONEY_NEW),
            _produced('"q.py', b"q\n"),
            _produced("a\nb.py", b"b\n"),
        ],
    )
    refused = {item["path"]: item["refused"] for item in change["files"]}
    assert refused == {
        "ledger/money.py": None,
        evil: "invalid_path",
        '"q.py': "invalid_path",
        "a\nb.py": "invalid_path",
    }
    work = tmp_path / "work.git"
    # 交付提交里只有底 + 合法的改动，ledger/money.py 就是改动集说的那一份
    assert _git(work, "show", f"{change['commit']}:ledger/money.py") == "import os\n"
    listed = _git(work, "ls-tree", "-r", "--name-only", change["commit"]).splitlines()
    assert listed == ["ledger/money.py"]
    assert "+import os" in delivery_diff(repo, change, "ledger/money.py")


def test_the_delivery_commit_refuses_a_path_git_would_misread(tmp_path):
    repo = BaseRepo(tmp_path / "work.git")
    base = _base(repo, InMemoryBase({"a.py": b"a\n"}))
    with pytest.raises(BaseError):
        repo.commit_on(base["commit"], {"x\nM 100644 inline a.py": b"z\n"})


def test_a_delivery_is_written_only_to_the_base_it_was_taken_from(tmp_path):
    # 核验第一轮建议 1：同一数据根换了项目开宿主，任务新增的文件不该写进别的仓库
    first = tmp_path / "a"
    other = tmp_path / "b"
    for root in (first, other):
        root.mkdir()
        (root / "x.py").write_bytes(b"x\n")
    repo = BaseRepo(tmp_path / "work.git")
    change = _change_set(repo, _base(repo, DirectoryBase(first)), [_produced("new.py", b"n\n")])
    with pytest.raises(DeliveryRefused) as refused:
        apply_change_set(DirectoryBase(other), repo, change)
    assert refused.value.code == "delivery_target_mismatch"
    assert not (other / "new.py").exists()
    [item] = apply_change_set(DirectoryBase(first), repo, change)
    assert item["status"] == "applied"


def test_a_save_between_the_check_and_the_rename_is_not_overwritten(tmp_path, monkeypatch):
    # 核验第一轮建议 2：比对之后、改名之前用户存了盘
    import agent_orchestrator.artifacts.workspace_base as module

    user = tmp_path / "user"
    user.mkdir()
    (user / "a.py").write_bytes(b"a\n")
    repo = BaseRepo(tmp_path / "work.git")
    adapter = DirectoryBase(user)
    change = _change_set(repo, _base(repo, adapter), [_produced("a.py", b"task\n")])
    real = module.tempfile.mkstemp

    def editor_saves(*args, **kwargs):
        (user / "a.py").write_bytes(b"saved by me\n")
        return real(*args, **kwargs)

    monkeypatch.setattr(module.tempfile, "mkstemp", editor_saves)
    [item] = apply_change_set(adapter, repo, change)
    assert item["status"] == "conflict"
    assert (user / "a.py").read_bytes() == b"saved by me\n"
    assert sorted(os.listdir(user)) == ["a.py"]  # 临时文件不留


def test_writes_keep_the_files_mode_and_respect_what_is_on_disk(tmp_path):
    user = tmp_path / "user"
    (user / "pkg").mkdir(parents=True)
    (user / "run.sh").write_bytes(b"echo a\n")
    (user / "run.sh").chmod(0o755)
    repo = BaseRepo(tmp_path / "work.git")
    adapter = DirectoryBase(user)
    change = _change_set(
        repo,
        _base(repo, adapter),
        [
            _produced("run.sh", b"echo b\n"),
            _produced("pkg", b"now a file\n"),  # 底里没有这个文件，用户那里是目录
            _produced("notes/x.md", b"x\n"),
        ],
    )
    (user / "notes").write_bytes(b"n\n")  # 任务期间用户建了一个叫 notes 的文件
    receipt = {item["path"]: item for item in apply_change_set(adapter, repo, change)}
    assert receipt["run.sh"]["status"] == "applied"
    assert (user / "run.sh").stat().st_mode & 0o777 == 0o755
    assert receipt["pkg"]["status"] == "conflict" and (user / "pkg").is_dir()
    assert (receipt["notes/x.md"]["status"], receipt["notes/x.md"]["reason"]) == (
        "refused",
        "not_a_directory",
    )


def test_test_configuration_counts_as_a_test_change_and_secrets_are_not_written(tmp_path):
    repo = BaseRepo(tmp_path / "work.git")
    base = _base(
        repo, InMemoryBase({"conftest.py": b"import pytest\n", "pytest.ini": b"[pytest]\n"})
    )
    change = build_change_set(
        repo,
        base,
        [
            _produced("conftest.py", b"import pytest\npytest.skip(allow_module_level=True)\n"),
            _produced("pytest.ini", b"[pytest]\naddopts = -k nothing\n"),
            _produced("config.py", b"KEY = 'sk-live-0123456789abcdef0123456789'\n"),
        ],
        reserved=RESERVED,
        is_secret=lambda text: "sk-live-" in text,
    )
    files = {item["path"]: item for item in change["files"]}
    assert files["conftest.py"]["test"] is True and files["pytest.ini"]["test"] is True
    assert files["config.py"]["refused"] == "secret_content"


def test_a_broken_ledger_record_does_not_hide_the_others(tmp_path):
    user, repo, adapter, change = _delivery_on_a_repo(tmp_path)
    ledger = DeliveryLedger(tmp_path / "deliveries")
    ledger.apply(
        "op-1", mission_id="m1", paths=None, run=lambda: apply_change_set(adapter, repo, change)
    )
    (tmp_path / "deliveries" / "broken.json").write_text("")  # 断电后留下的空文件
    assert [r["operation_key"] for r in ledger.receipts("m1")] == ["op-1"]


def test_the_delivery_commit_is_checked_against_what_was_asked(tmp_path):
    # 底里 notes 是文件，再叠 notes/x.md：git 会把 notes 换成目录，提交与要写的对不上 → 拒
    repo = BaseRepo(tmp_path / "work.git")
    base = _base(repo, InMemoryBase({"notes": b"n\n"}))
    with pytest.raises(BaseError):
        repo.commit_on(base["commit"], {"notes/x.md": b"x\n"})

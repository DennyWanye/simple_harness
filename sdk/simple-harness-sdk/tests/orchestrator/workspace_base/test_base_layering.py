# SPDX-License-Identifier: Apache-2.0
"""底铺在尝试工作区、验收副本、判定树的最下层（设计稿 §4.3）。

R16 同构：使用方仓库里本来就有 ``ledger/``，任务只新写 ``ledger/cli.py`` 和 ``tests/test_cli.py``。
没有底时验收副本里只有这两个新文件，测试导入 ``ledger.book`` 失败；有底时同一份记录在
「底 + 本任务改动」上验收，测试通过。只有真改动被登记成产物。
"""

from __future__ import annotations

import asyncio

from agent_orchestrator.artifacts.workspace import WorkspaceManager
from agent_orchestrator.artifacts.workspace_base import CaptureLimits, InMemoryBase, capture_base
from agent_orchestrator.contracts import Budget, Task, TaskStatus
from agent_orchestrator.verification.deterministic_checks import code_test

REPO = {
    "ledger/__init__.py": b"",
    "ledger/money.py": b"def cents(text):\n    return int(round(float(text) * 100))\n",
    "ledger/book.py": (
        b"from ledger.money import cents\n\n"
        b"class Book:\n"
        b"    def __init__(self):\n        self.entries = []\n"
        b"    def add(self, amount):\n        self.entries.append(cents(amount))\n"
        b"    def total(self):\n        return sum(self.entries)\n"
    ),
}
CLI = (
    "import sys\nfrom ledger.book import Book\n\n"
    "def main(argv):\n    book = Book()\n    for amount in argv:\n        book.add(amount)\n"
    "    return book.total()\n"
)
TEST = (
    "from ledger.cli import main\n\n"
    "def test_total_in_cents():\n    assert main(['1.50', '2.25']) == 375\n"
)


def _task() -> Task:
    return Task(
        id="task-1",
        mission_id="mission-1",
        parent_task_ids=(),
        dependency_ids=(),
        goal="给记账项目加命令行",
        rationale="test",
        success_criteria=("pytest:tests/test_cli.py",),
        verification_policy=("format_check", "rule_check", "code_test", "critic_review"),
        allowed_tools=("workspace_write_file",),
        budget=Budget(max_tokens=1000, max_attempts=1),
        priority=1.0,
        status=TaskStatus.ACTIVE,
        version=1,
    )


def _changed(workspace, base_manifest):
    """登记规则的核心：相对起步状态（这里只有底）新建或改过的文件。"""

    return [
        artifact
        for artifact in workspace.snapshot(
            mission_id="mission-1", task_id="task-1", produced_by="worker"
        )
        if base_manifest.get(artifact.path) is None
        or base_manifest[artifact.path].sha256 != artifact.content_hash
    ]


def _verify(manager, artifacts, *, base):
    copy = manager.verification_copy(
        "attempt-1", seed={}, inputs={}, artifacts=artifacts, base=base
    )
    return asyncio.run(code_test(_task(), verification_copy=copy, timeout=120))


def test_the_r16_shape_passes_only_when_the_base_is_laid_underneath(tmp_path):
    manager = WorkspaceManager(tmp_path / "workspaces")
    record = capture_base(
        InMemoryBase(REPO),
        manager.bases,
        scope=(),
        limits=CaptureLimits(),
        is_secret=lambda _text: False,
    )
    workspace = manager.create("attempt-1", seed={}, base=record.commit)
    # 执行者起步就看得到仓库原有文件
    assert workspace.read_text("ledger/book.py") == REPO["ledger/book.py"].decode()
    workspace.write_text("ledger/cli.py", CLI)
    workspace.write_text("tests/test_cli.py", TEST)

    artifacts = _changed(workspace, manager.bases.manifest(record.commit))
    assert sorted(a.path for a in artifacts) == ["ledger/cli.py", "tests/test_cli.py"]

    without = _verify(manager, artifacts, base=None)
    assert without.status == "FAIL", without
    assert "ModuleNotFoundError" in str(without.detail) or "ImportError" in str(without.detail)

    with_base = _verify(manager, artifacts, base=record.commit)
    assert with_base.status == "PASS", with_base


def test_a_recorded_change_to_a_base_file_wins_over_the_base(tmp_path):
    manager = WorkspaceManager(tmp_path / "workspaces")
    record = capture_base(
        InMemoryBase(REPO),
        manager.bases,
        scope=(),
        limits=CaptureLimits(),
        is_secret=lambda _t: False,
    )
    workspace = manager.create("attempt-1", seed={}, base=record.commit)
    workspace.write_text("ledger/money.py", "def cents(text):\n    return 0\n")
    artifacts = _changed(workspace, manager.bases.manifest(record.commit))
    assert [a.path for a in artifacts] == ["ledger/money.py"]
    copy = manager.verification_copy(
        "attempt-1", seed={}, inputs={}, artifacts=artifacts, base=record.commit
    )
    assert copy.read_text("ledger/money.py") == "def cents(text):\n    return 0\n"
    assert copy.read_text("ledger/book.py") == REPO["ledger/book.py"].decode()


def test_a_retry_starts_from_the_previous_tree_not_a_fresh_base(tmp_path):
    manager = WorkspaceManager(tmp_path / "workspaces")
    record = capture_base(
        InMemoryBase(REPO),
        manager.bases,
        scope=(),
        limits=CaptureLimits(),
        is_secret=lambda _t: False,
    )
    first = manager.create("attempt-1", seed={}, base=record.commit)
    first.write_text("ledger/money.py", "changed = True\n")
    retry = manager.create("attempt-2", seed={}, base=record.commit, previous=first.root)
    assert retry.read_text("ledger/money.py") == "changed = True\n"


def test_the_judgment_tree_has_the_base_under_the_accepted_files(tmp_path):
    manager = WorkspaceManager(tmp_path / "workspaces")
    record = capture_base(
        InMemoryBase(REPO),
        manager.bases,
        scope=(),
        limits=CaptureLimits(),
        is_secret=lambda _t: False,
    )
    tree = manager.integrated_copy(
        "mission-1-judge",
        seed={},
        files={"ledger/cli.py": CLI.encode(), "tests/test_cli.py": TEST.encode()},
        base=record.commit,
    )
    assert sorted(tree.list_files()) == sorted([*REPO, "ledger/cli.py", "tests/test_cli.py"])


def test_the_verification_copy_is_rebuilt_from_records_after_the_tree_is_gone(tmp_path):
    # 工作区目录过保留期被清掉、或崩溃后：验收副本只靠底提交与登记的产物重建
    manager = WorkspaceManager(tmp_path / "workspaces")
    record = capture_base(
        InMemoryBase(REPO),
        manager.bases,
        scope=(),
        limits=CaptureLimits(),
        is_secret=lambda _t: False,
    )
    workspace = manager.create("attempt-1", seed={}, base=record.commit)
    workspace.write_text("ledger/cli.py", CLI)
    workspace.write_text("tests/test_cli.py", TEST)
    artifacts = _changed(workspace, manager.bases.manifest(record.commit))
    manager.remove("attempt-1")
    (manager.bases.path.parent / "manifests").rename(tmp_path / "manifests-gone")  # 缓存也丢了
    assert _verify(manager, artifacts, base=record.commit).status == "PASS"


BROKEN = {"tests/test_needs_network.py": b"def test_fetch():\n    assert False, 'needs network'\n"}
#: 使用方根目录的 pytest 配置与 conftest：沙箱里没有 pytest-cov、也没装 some_missing_plugin
HOSTILE_ROOT = {
    "pyproject.toml": b'[tool.pytest.ini_options]\naddopts = "--cov=ledger"\n',
    "conftest.py": b"import some_missing_plugin  # noqa\n",
}
RED = "def test_cli():\n    assert False\n"


def _criteria_task(criteria: tuple[str, ...]) -> Task:
    return Task(
        id="task-1",
        mission_id="mission-1",
        parent_task_ids=(),
        dependency_ids=(),
        goal="加命令行模块",
        rationale="test",
        success_criteria=criteria,
        verification_policy=("format_check", "rule_check", "code_test", "critic_review"),
        allowed_tools=("workspace_write_file",),
        budget=Budget(max_tokens=1000, max_attempts=1),
        priority=1.0,
        status=TaskStatus.ACTIVE,
        version=1,
    )


def _courtesy(
    tmp_path,
    *,
    writes: dict[str, str] | None = None,
    on_base: bool = True,
    base: dict[str, bytes] | None = None,
    criteria: tuple[str, ...] = ("file:ledger/cli.py",),
):
    manager = WorkspaceManager(tmp_path / "workspaces")
    record = capture_base(
        InMemoryBase({**REPO, **(BROKEN if base is None else base)}),
        manager.bases,
        scope=(),
        limits=CaptureLimits(),
        is_secret=lambda _t: False,
    )
    workspace = manager.create("attempt-1", seed={}, base=record.commit)
    workspace.write_text("ledger/cli.py", CLI)
    for path, content in (writes or {}).items():
        workspace.write_text(path, content)
    artifacts = _changed(workspace, manager.bases.manifest(record.commit))
    copy = manager.verification_copy(
        "attempt-1", seed={}, inputs={}, artifacts=artifacts, base=record.commit
    )
    result = asyncio.run(
        code_test(
            _criteria_task(criteria),
            verification_copy=copy,
            timeout=120,
            artifacts=artifacts,
            on_base=on_base,
        )
    )
    # 空目录用完就删，不留在工作区根下
    assert not [p for p in manager.root.iterdir() if "no-tests" in p.name]
    return result


def test_the_courtesy_run_on_a_base_ignores_the_users_own_red_tests(tmp_path):
    # 核验第二轮：只要求 file: 的步骤，顺手运行不该跑到使用方仓库里本来就红的测试
    whole_tree = _courtesy(tmp_path / "a", on_base=False)
    assert whole_tree.status == "FAIL"  # 没有底时的行为：整棵树都跑
    nothing_changed = _courtesy(tmp_path / "b")
    assert nothing_changed.status == "PASS", nothing_changed
    [run] = nothing_changed.detail["runs"]
    assert run["target"] is None and run["no_tests_collected"] is True and run["courtesy"]
    own_test = _courtesy(tmp_path / "c", writes={"tests/test_cli.py": TEST})
    assert own_test.status == "PASS", own_test
    [run] = own_test.detail["runs"]
    # 行的目标仍是 None（不替哪条 pytest:<路径> 作证），跑的是哪个文件另记
    assert run["target"] is None and run["courtesy_target"] == "tests/test_cli.py"


def test_the_courtesy_run_on_a_base_still_fails_on_the_steps_own_red_test(tmp_path):
    assert _courtesy(tmp_path, writes={"tests/test_cli.py": RED}).status == "FAIL"


def test_every_changed_test_file_runs_not_just_the_first(tmp_path):
    # 核验第三轮：改了两个测试文件，第二个是红的
    result = _courtesy(tmp_path, writes={"tests/test_a_cli.py": TEST, "tests/test_b_cli.py": RED})
    assert [r["courtesy_target"] for r in result.detail["runs"]] == [
        "tests/test_a_cli.py",
        "tests/test_b_cli.py",
    ]
    assert result.status == "FAIL", result


def test_a_bare_pytest_criterion_still_runs_the_whole_tree_on_a_base(tmp_path):
    # 核验第三轮：不带路径的 pytest: 是要求跑整棵树，不收窄
    red = _courtesy(tmp_path / "a", writes={"tests/test_cli.py": TEST}, criteria=("pytest:",))
    assert red.status == "FAIL", red  # 底里那条红测试被看见了
    green = _courtesy(
        tmp_path / "b",
        base={"tests/test_book.py": b"def test_ok():\n    pass\n"},
        criteria=("pytest:",),
    )
    assert green.status == "PASS", green  # 这一步没改测试，底里的绿测试照样算数
    assert [r["target"] for r in green.detail["runs"]] == [None]


def test_the_no_tests_run_does_not_load_the_users_root_config(tmp_path):
    # 核验第三轮：根目录 conftest 导入了沙箱里没有的包、pyproject 要 --cov
    result = _courtesy(tmp_path, base={**BROKEN, **HOSTILE_ROOT})
    assert result.status == "PASS", result


def test_a_test_named_file_without_tests_is_not_a_failure(tmp_path):
    # 核验第四轮：Django 式的 tests/test_settings.py 里没有测试函数，退出码 5
    alone = _courtesy(tmp_path / "a", writes={"tests/test_settings.py": "DEBUG = True\n"})
    assert alone.status == "PASS", alone
    assert alone.detail["courtesy_no_tests"] == ["tests/test_settings.py"]
    [run] = alone.detail["runs"]
    assert run["no_tests_collected"] is True  # 退回空目录那一次
    mixed = _courtesy(
        tmp_path / "b",
        writes={"tests/test_settings.py": "DEBUG = True\n", "tests/test_cli.py": TEST},
    )
    assert mixed.status == "PASS", mixed
    assert [r["courtesy_target"] for r in mixed.detail["runs"]] == ["tests/test_cli.py"]


def test_tests_deselected_by_the_users_config_are_not_a_failure(tmp_path):
    # 核验第四轮：使用方配置 -m 'not slow'，这一步写的测试全被反选
    config = {
        "pyproject.toml": (
            b"[tool.pytest.ini_options]\naddopts = \"-m 'not slow'\"\n"
            b'markers = ["slow: slow tests"]\n'
        )
    }
    slow = "import pytest\n\n@pytest.mark.slow\ndef test_cli():\n    assert True\n"
    result = _courtesy(tmp_path, base={**BROKEN, **config}, writes={"tests/test_cli.py": slow})
    assert result.status == "PASS", result
    assert result.detail["courtesy_no_tests"] == ["tests/test_cli.py"]

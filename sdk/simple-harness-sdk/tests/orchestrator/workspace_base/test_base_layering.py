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

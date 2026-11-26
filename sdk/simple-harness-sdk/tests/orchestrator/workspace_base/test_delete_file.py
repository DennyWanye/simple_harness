# SPDX-License-Identifier: Apache-2.0
"""删除文件（设计稿 §4.4 / §4.7，P2b 第 2 项）。

有底的任务里，能写文件的执行者也能删文件（``workspace_delete_file``）。删除跟着结果一起登记，
验收副本、判定树里那个文件不在；改动集里是 ``delete``，交付提交里它被删掉；写回按三方比对：
现状仍是底 → 删，已经没了 → ``already``，被改过 → ``conflict`` 不删。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_host_base import _host, _mission  # noqa: E402

from agent_orchestrator.artifacts.delivery import (  # noqa: E402
    Produced,
    apply_change_set,
    build_change_set,
    delivery_diff,
)
from agent_orchestrator.artifacts.workspace_base import (  # noqa: E402
    RESERVED_BASE_PREFIXES,
    BaseRepo,
    CaptureLimits,
    DirectoryBase,
    GitRepoBase,
    InMemoryBase,
    capture_base,
    git_blob_id,
)
from agent_orchestrator.governance.policies import effective_tools  # noqa: E402
from agent_orchestrator.testing.fixtures import package_of  # noqa: E402
from agent_orchestrator.testing.scripted_replies import (  # noqa: E402
    LayeredScriptedProvider,
    worker_reply,
)

OLD = b"def legacy():\n    return 1\n"
KEEP = b"def keep():\n    return 2\n"


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
        reserved=RESERVED_BASE_PREFIXES,
    ).to_json()


def test_delete_comes_with_write_only_on_a_base():
    from agent_orchestrator.governance.policies import DeploymentPolicy

    workspace = ("workspace_read_file", "workspace_write_file", "workspace_list")
    deployment = DeploymentPolicy(allowed_tools=(*workspace, "workspace_delete_file"))
    kwargs = dict(
        mission_tools=workspace,
        task_tools=workspace,
        role_tools=workspace,
        deployment=deployment,
    )
    assert "workspace_delete_file" not in effective_tools(**kwargs)
    assert "workspace_delete_file" in effective_tools(**kwargs, on_base=True)
    # 只读叶子不能删
    assert "workspace_delete_file" not in effective_tools(
        **kwargs, on_base=True, read_only_leaf=True
    )
    # 部署没开删除工具就没有
    closed = {**kwargs, "deployment": DeploymentPolicy(allowed_tools=workspace)}
    assert "workspace_delete_file" not in effective_tools(**closed, on_base=True)


def test_a_deletion_is_part_of_the_change_set_and_the_delivery_commit(tmp_path):
    repo = BaseRepo(tmp_path / "work.git")
    base = _base(
        repo,
        InMemoryBase(
            {"ledger/legacy.py": OLD, "ledger/keep.py": KEEP, "tests/test_legacy.py": b"x\n"}
        ),
    )
    change = build_change_set(
        repo,
        base,
        [Produced("ledger/new.py", b"n = 1\n", "a1", "t1")],
        reserved=RESERVED_BASE_PREFIXES,
        deleted={"ledger/legacy.py": "t1", "tests/test_legacy.py": "t1", "not/in/base.py": "t1"},
    )
    files = {item["path"]: item for item in change["files"]}
    assert sorted(files) == ["ledger/legacy.py", "ledger/new.py", "tests/test_legacy.py"]
    gone = files["ledger/legacy.py"]
    assert (gone["op"], gone["base_blob"], gone["blob"]) == ("delete", git_blob_id(OLD), None)
    assert files["tests/test_legacy.py"]["test"] is True  # 删已有的测试也单列
    assert change["counts"]["deleted"] == 2
    listed = _git(tmp_path / "work.git", "ls-tree", "-r", "--name-only", change["commit"])
    assert listed.splitlines() == ["ledger/keep.py", "ledger/new.py"]
    assert "-def legacy():" in delivery_diff(repo, change, "ledger/legacy.py")


def test_a_deletion_is_written_back_with_a_three_way_check(tmp_path):
    user = tmp_path / "user"
    (user / "ledger").mkdir(parents=True)
    for name, data in {"a.py": OLD, "b.py": OLD, "c.py": OLD}.items():
        (user / "ledger" / name).write_bytes(data)
    repo = BaseRepo(tmp_path / "work.git")
    adapter = DirectoryBase(user)
    change = build_change_set(
        repo,
        _base(repo, adapter),
        [],
        reserved=RESERVED_BASE_PREFIXES,
        deleted={"ledger/a.py": "t1", "ledger/b.py": "t1", "ledger/c.py": "t1"},
    )
    (user / "ledger/b.py").unlink()  # 用户自己已经删了
    (user / "ledger/c.py").write_bytes(OLD + b"# mine\n")  # 用户改过
    receipt = {item["path"]: item["status"] for item in apply_change_set(adapter, repo, change)}
    assert receipt == {
        "ledger/a.py": "applied",
        "ledger/b.py": "already",
        "ledger/c.py": "conflict",
    }
    assert not (user / "ledger/a.py").exists()
    assert (user / "ledger/c.py").read_bytes() == OLD + b"# mine\n"


def _deleting_worker(request):
    """写出 file: 条件点名的新文件，交结果前删掉底里的 ledger/legacy.py。"""

    outputs = list(package_of(request).get("task_contract", {}).get("outputs") or [])
    written = sum(1 for message in request.messages if "tool" in str(message.role).lower())
    tools = set(package_of(request).get("tools_and_permissions", {}).get("allowed_tools", ()))
    if written == len(outputs) and "workspace_delete_file" in tools:
        return ("workspace_delete_file", {"path": "ledger/legacy.py"})
    reply = worker_reply(request)
    if isinstance(reply, tuple) and reply[0] == "workspace_write_file":
        return ("workspace_write_file", {"path": reply[1]["path"], "content": "n = 1\n"})
    return reply


def _user_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    (root / "ledger").mkdir()
    (root / "ledger/legacy.py").write_bytes(OLD)
    (root / "ledger/keep.py").write_bytes(KEEP)
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=u", "-c", "user.email=u@x", "commit", "-qm", "init")
    return root


def test_a_mission_deletes_a_base_file_and_hands_the_deletion_back(tmp_path):
    user = _user_repo(tmp_path / "user")

    async def case():
        provider = LayeredScriptedProvider(worker=_deleting_worker)
        async with _host(
            tmp_path / "root", provider=provider, workspace_base=GitRepoBase(user)
        ) as host:
            created = host.create_mission(
                {
                    "goal": "用 new.py 取代 legacy.py",
                    "success_criteria": ["file:ledger/new.py"],
                    "idempotency_key": "delete",
                }
            )
            final = await host.run_until_settled(created["mission_id"], timeout=120.0)
            assert str(final["snapshot"]["mission"]["status"]) == "COMPLETED", final["snapshot"]
            change = _mission(host, created["mission_id"])["final_report"]["change_set"]
            ops = {item["path"]: item["op"] for item in change["files"]}
            assert ops == {"ledger/legacy.py": "delete", "ledger/new.py": "add"}
            receipt = host.apply_delivery(created["mission_id"], operation_key="k")
            assert {row["path"]: row["status"] for row in receipt["files"]} == {
                "ledger/legacy.py": "applied",
                "ledger/new.py": "applied",
            }
            assert not (user / "ledger/legacy.py").exists()
            assert (user / "ledger/keep.py").read_bytes() == KEEP
            assert " D ledger/legacy.py" in _git(user, "status", "--porcelain")

    asyncio.run(case())


def test_a_mission_without_a_base_has_no_delete_tool(tmp_path):
    seen: list[set[str]] = []

    def worker(request):
        allowed = package_of(request).get("tools_and_permissions", {}).get("allowed_tools", ())
        seen.append(set(allowed))
        return worker_reply(request)

    async def case():
        async with _host(
            tmp_path / "root", provider=LayeredScriptedProvider(worker=worker)
        ) as host:
            created = host.create_mission(
                {"goal": "写文件", "success_criteria": ["file:a.md"], "idempotency_key": "nb"}
            )
            await host.run_until_settled(created["mission_id"], timeout=120.0)
        assert seen and all("workspace_delete_file" not in tools for tools in seen)
        assert any("workspace_write_file" in tools for tools in seen)

    asyncio.run(case())


def test_deleting_a_protected_or_missing_file_is_refused(tmp_path):
    from simple_harness.contracts import CallId
    from simple_harness.tools import ToolCall

    from agent_orchestrator.artifacts.workspace import WorkspaceManager
    from agent_orchestrator.runtime.tool_gateway import WorkspaceBinding, WorkspaceToolGateway

    async def case():
        manager = WorkspaceManager(tmp_path / "workspaces")
        manager.create("attempt", seed={"tests/test_a.py": "x\n", "a.py": "a\n"})
        gateway = WorkspaceToolGateway(manager)
        gateway.bind(
            "w",
            WorkspaceBinding(
                "attempt",
                "work",
                True,
                ("workspace_delete_file",),
                protected=("tests/test_a.py",),
            ),
        )

        async def delete(path):
            return await gateway.execute(
                ToolCall(CallId(path), "workspace_delete_file", {"path": path}), {"run_id": "w"}
            )

        for path in ("tests/test_a.py", "missing.py", "tests"):
            refused = await delete(path)
            assert refused.value is None and refused.error_code, (path, refused)
        ok = await delete("a.py")
        assert ok.value == {"path": "a.py", "deleted": True}
        assert not (manager.get("attempt").root / "a.py").exists()
        assert (manager.get("attempt").root / "tests/test_a.py").exists()

    asyncio.run(case())

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


# ---------------------------------------------------------------- 核验第一轮


def test_a_file_replaced_by_a_directory_of_the_same_name(tmp_path):
    # 删掉 config，再写 config/settings.toml：交付提交先删后写，树里是新目录
    repo = BaseRepo(tmp_path / "work.git")
    base = _base(repo, InMemoryBase({"config": b"old\n", "a.py": b"a\n"}))
    change = build_change_set(
        repo,
        base,
        [Produced("config/settings.toml", b"x = 1\n", "a1", "t1")],
        reserved=RESERVED_BASE_PREFIXES,
        deleted={"config": "t1"},
    )
    listed = _git(tmp_path / "work.git", "ls-tree", "-r", "--name-only", change["commit"])
    assert listed.splitlines() == ["a.py", "config/settings.toml"]
    # 副本也先删后写
    from agent_orchestrator.artifacts.workspace import WorkspaceManager

    manager = WorkspaceManager(tmp_path / "ws", bases=repo)
    copy = manager.integrated_copy(
        "m",
        seed={},
        files={"config/settings.toml": b"x = 1\n"},
        base=base["commit"],
        deleted=["config"],
    )
    assert (copy.root / "config" / "settings.toml").read_bytes() == b"x = 1\n"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("ledger/legacy.py", "ledger/Legacy.py"),
        ("pkg/a.txt", "Pkg/a.txt"),
        ("docs/café.md", "docs/cafe\u0301.md"),
        ("café/a.txt", "cafe\u0301/a.txt"),
        ("pkg/deep/a.txt", "Pkg/deep/b.txt"),
    ],
)
def test_spelling_rename_preserves_both_views_and_refuses_either_selection(tmp_path, old, new):
    from agent_orchestrator.artifacts.workspace import WorkspaceManager

    user = tmp_path / "user"
    (user / old).parent.mkdir(parents=True)
    (user / old).write_bytes(OLD)
    repo = BaseRepo(tmp_path / "work.git")
    adapter = DirectoryBase(user)
    base = _base(repo, adapter)
    manager = WorkspaceManager(tmp_path / "ws", bases=repo)
    live = manager.create("attempt", seed={}, base=base["commit"])
    live.delete_file(old)
    live.write_bytes(new, KEEP)
    artifacts = live.snapshot(mission_id="m", task_id="t", produced_by="w")
    assert [a.path for a in artifacts] == [new]
    verify = manager.verification_copy(
        "attempt", artifacts=artifacts, base=base["commit"], deleted=[old]
    )
    judged = manager.integrated_copy(
        "m", seed={}, files={new: KEEP}, base=base["commit"], deleted=[old]
    )
    for tree in (verify, judged):
        assert tree.list_files() == [new]
        assert tree.read_bytes(new) == KEEP
    change = build_change_set(
        repo, base, [Produced(new, KEEP, "a", "t")], reserved=(), deleted={old: "t"}
    )
    assert repo.files(change["commit"]) == {new: KEEP}
    assert {r["path"]: r["refused"] for r in change["files"]} == {
        old: "case_rename",
        new: "case_rename",
    }
    for paths in ([old], [new], None):
        receipt = apply_change_set(adapter, repo, change, paths=paths)
        assert {(r["status"], r["reason"]) for r in receipt} == {("refused", "case_rename")}
        assert {
            p.relative_to(user).as_posix(): p.read_bytes() for p in user.rglob("*") if p.is_file()
        } == {old: OLD}


def test_copies_follow_the_recorded_deletions(tmp_path):
    from agent_orchestrator.artifacts.workspace import WorkspaceManager

    repo = BaseRepo(tmp_path / "work.git")
    base = _base(repo, InMemoryBase({"a.py": b"a\n", "b.py": b"b\n"}))
    manager = WorkspaceManager(tmp_path / "ws", bases=repo)
    verify = manager.verification_copy(
        "att", seed={}, inputs={}, artifacts=[], base=base["commit"], deleted=["a.py"]
    )
    assert sorted(verify.list_files()) == ["b.py"]
    judged = manager.integrated_copy("m", seed={}, files={}, base=base["commit"], deleted=["b.py"])
    assert sorted(judged.list_files()) == ["a.py"]


def test_a_file_that_is_deleted_and_written_again_is_not_a_deletion(tmp_path):
    repo = BaseRepo(tmp_path / "work.git")
    base = _base(repo, InMemoryBase({"a.py": b"a\n"}))
    change = build_change_set(
        repo,
        base,
        [Produced("a.py", b"rewritten\n", "a2", "t2")],
        reserved=RESERVED_BASE_PREFIXES,
        deleted={"a.py": "t1"},
    )
    assert [(item["path"], item["op"]) for item in change["files"]] == [("a.py", "modify")]


def test_no_deletion_is_inferred_from_how_the_disk_spells_names(tmp_path):
    # 核验第一轮：种子里 Docs/ 与 docs/ 在不区分大小写的磁盘上是同一个目录，列出来的写法和起步时不同；
    # 那不是删除。没有底的任务也根本不算删除
    async def case():
        async with _host(tmp_path / "root") as host:
            created = host.create_mission(
                {
                    "goal": "写说明",
                    "success_criteria": ["file:a.md"],
                    "idempotency_key": "seed",
                    "workspace_seed": {"Docs/guide.md": "g\n", "docs/notes.md": "n\n"},
                }
            )
            await host.run_until_settled(created["mission_id"], timeout=120.0)
            store = host.orchestrator.store
            events = store.list_events(created["mission_id"], types=["ResultSubmitted"])
            assert events and all("deleted" not in event.payload for event in events)

    asyncio.run(case())


GONE_TEST = (
    "from pathlib import Path\n\n"
    "def test_legacy_is_gone():\n"
    "    assert not (Path(__file__).parents[1] / 'ledger' / 'legacy.py').exists()\n"
)


def _deleting_test_worker(request):
    outputs = list(package_of(request).get("task_contract", {}).get("outputs") or [])
    written = sum(1 for message in request.messages if "tool" in str(message.role).lower())
    if written == len(outputs):
        return ("workspace_delete_file", {"path": "ledger/legacy.py"})
    reply = worker_reply(request)
    if isinstance(reply, tuple) and reply[0] == "workspace_write_file":
        path = reply[1]["path"]
        content = GONE_TEST if path.endswith(".py") and "test" in path else "n = 1\n"
        return ("workspace_write_file", {"path": path, "content": content})
    return reply


def test_verification_and_the_judgment_tree_see_the_deletion(tmp_path):
    # 验收副本与任务级判定树里都没有被删的文件：断言它不在的测试在两处都通过，任务才 COMPLETED
    async def case():
        provider = LayeredScriptedProvider(worker=_deleting_test_worker)
        async with _host(
            tmp_path / "root",
            provider=provider,
            workspace_base=InMemoryBase({"ledger/legacy.py": OLD, "ledger/keep.py": KEEP}),
        ) as host:
            if host.policy.code_execution != "sandboxed":
                pytest.skip("这台机器没有可用的隔离区，pytest: 要求不受理")
            created = host.create_mission(
                {
                    "goal": "删掉 legacy.py，并写测试确认它不在了",
                    "success_criteria": [
                        "file:tests/test_removed.py",
                        "pytest:tests/test_removed.py",
                    ],
                    "idempotency_key": "gone",
                }
            )
            final = await host.run_until_settled(created["mission_id"], timeout=120.0)
            assert str(final["snapshot"]["mission"]["status"]) == "COMPLETED", final["snapshot"]
            change = _mission(host, created["mission_id"])["final_report"]["change_set"]
            assert {"ledger/legacy.py": "delete"}.items() <= {
                item["path"]: item["op"] for item in change["files"]
            }.items()

    asyncio.run(case())


def test_final_add_check_preserves_the_users_concurrent_save(tmp_path, monkeypatch):
    adapter = DirectoryBase(tmp_path)
    chmod = os.chmod

    def save_after_temp_write(path, mode):
        chmod(path, mode)
        (tmp_path / "Report.md").write_bytes(OLD)
        if not (tmp_path / "report.md").exists():
            # 在区分大小写的盘上也覆盖最终比对时目标刚被占用的时序
            (tmp_path / "report.md").write_bytes(OLD)

    monkeypatch.setattr(os, "chmod", save_after_temp_write)
    result = adapter.write_file("report.md", KEEP, expected=None, executable=False)
    assert result.status == "conflict"
    assert (tmp_path / "Report.md").read_bytes() == OLD
    assert (tmp_path / "report.md").read_bytes() == OLD


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("docs/café.md", "docs/cafe\u0301.md"),
        ("Café/a", "cafe\u0301/b"),
    ],
)
def test_capture_withholds_both_unicode_aliases(tmp_path, first, second):
    repo = BaseRepo(tmp_path / "work.git")
    adapter = InMemoryBase({first: OLD, second: KEEP, "ok": KEEP})
    base = _base(repo, adapter)
    assert base["withheld"] == {first: "case_conflict", second: "case_conflict"}
    assert repo.files(base["commit"]) == {"ok": KEEP}
    assert adapter.files == {first: OLD, second: KEEP, "ok": KEEP}


@pytest.mark.parametrize(
    ("on_base", "readonly", "write", "count"),
    [
        (False, False, True, 0),
        (True, True, True, 0),
        (True, False, False, 0),
        (True, False, True, 1),
    ],
)
def test_explicit_delete_obeys_write_authority(on_base, readonly, write, count):
    from agent_orchestrator.governance.policies import DeploymentPolicy

    names = ("workspace_delete_file",) + (("workspace_write_file",) if write else ())
    tools = effective_tools(
        mission_tools=names,
        task_tools=names,
        role_tools=names,
        deployment=DeploymentPolicy(allowed_tools=names),
        on_base=on_base,
        read_only_leaf=readonly,
    )
    assert tools.count("workspace_delete_file") == count


def test_delete_clears_memory_executable_bit():
    adapter = InMemoryBase({"run": OLD}, executable=["run"])
    assert adapter.delete_file("run", expected=git_blob_id(OLD)).status == "applied"
    assert adapter.write_file("run", KEEP, expected=None, executable=False).status == "applied"
    assert not adapter.capture(()).files[0].executable


def _directory_delivery(tmp_path):
    user = tmp_path / "user"
    user.mkdir()
    (user / "config").write_bytes(OLD)
    adapter = DirectoryBase(user)
    repo = BaseRepo(tmp_path / "work.git")
    change = build_change_set(
        repo,
        _base(repo, adapter),
        [Produced("config/settings.toml", KEEP, "a", "t")],
        reserved=(),
        deleted={"config": "t"},
    )
    return user, adapter, repo, change


def test_directory_replacement_receipt_and_claim_recovery(tmp_path):
    from agent_orchestrator.artifacts.delivery import DeliveryLedger

    user, adapter, repo, change = _directory_delivery(tmp_path)

    def apply():
        return {r["path"]: r["status"] for r in apply_change_set(adapter, repo, change)}

    def crash():
        assert apply() == {"config": "applied", "config/settings.toml": "applied"}
        raise InterruptedError

    ledger = DeliveryLedger(tmp_path / "ledger")
    with pytest.raises(InterruptedError):
        ledger.apply("k", mission_id="m", paths=None, run=crash)
    receipt = DeliveryLedger(ledger.root).apply(
        "k", mission_id="m", paths=None, run=lambda: apply_change_set(adapter, repo, change)
    )
    assert receipt["resumed"] and receipt["counts"]["already"] == 2
    assert apply() == {"config": "already", "config/settings.toml": "already"}
    assert (user / "config/settings.toml").read_bytes() == KEEP


def test_users_own_directory_is_a_conflict(tmp_path):
    user, adapter, repo, change = _directory_delivery(tmp_path)
    (user / "config").unlink()
    (user / "config").mkdir()
    (user / "config/mine").write_bytes(OLD)
    rows = apply_change_set(adapter, repo, change)
    assert [r["status"] for r in rows] == ["conflict", "conflict"]
    assert os.listdir(user / "config") == ["mine"]
    assert (user / "config/mine").read_bytes() == OLD


def test_deletion_diff_only_contains_the_old_file(tmp_path):
    _, _, repo, change = _directory_delivery(tmp_path)
    diff = delivery_diff(repo, change, "config")
    assert "-def legacy():" in diff
    assert "settings.toml" not in diff


def test_adapter_capabilities_follow_selected_operations(tmp_path, monkeypatch):
    from agent_orchestrator.host import HostRequestError

    class WriteOnly:
        def __init__(self):
            self.base = InMemoryBase({"old": OLD})
            self.location = self.base.location
            self.write_file = self.base.write_file
            self.current_hashes = self.base.current_hashes
            self.capture = self.base.capture

    target = WriteOnly()
    repo = BaseRepo(tmp_path / "work.git")
    change = build_change_set(
        repo,
        _base(repo, target),
        [Produced("new", KEEP, "a", "t")],
        reserved=(),
        deleted={"old": "t"},
    )
    host = _host(tmp_path / "root", workspace_base=target)
    monkeypatch.setattr(host, "_bases", lambda: repo)
    monkeypatch.setattr(host, "_change_set", lambda _: ({"status": "COMPLETED"}, change))
    with pytest.raises(HostRequestError) as error:
        host.apply_delivery("m", operation_key="all")
    assert error.value.code == "delivery_unsupported"
    assert target.base.files == {"old": OLD}
    receipt = host.apply_delivery("m", operation_key="one", paths=["new"])
    assert receipt["counts"]["applied"] == 1
    assert target.base.files == {"old": OLD, "new": KEEP}


@pytest.mark.parametrize(
    ("old", "new", "status"),
    [
        ("ledger/legacy.py", "ledger/Legacy.py", "refused"),
        ("pkg/a.txt", "Pkg/a.txt", "refused"),
        ("docs/café.md", "docs/cafe\u0301.md", "refused"),
        ("config", "config/settings.toml", "applied"),
    ],
)
def test_mission_collects_replacement_and_completes_next_task(
    tmp_path, monkeypatch, old, new, status
):
    from agent_orchestrator.artifacts.workspace import WorkspaceManager

    views = {}

    def observe(method):
        def build(manager, *args, **kwargs):
            tree = method(manager, *args, **kwargs)
            views[method.__name__] = {p: tree.read_bytes(p) for p in tree.list_files()}
            return tree

        return build

    for method in ("verification_copy", "integrated_copy"):
        monkeypatch.setattr(WorkspaceManager, method, observe(getattr(WorkspaceManager, method)))
    user = tmp_path / "user"
    (user / old).parent.mkdir(parents=True)
    (user / old).write_bytes(OLD)

    def worker(request):
        outputs = package_of(request)["task_contract"]["outputs"]
        n = sum(1 for message in request.messages if "tool" in str(message.role).lower())
        if new in outputs:
            if n == 0:
                return ("workspace_delete_file", {"path": old})
            if n <= len(outputs):
                return ("workspace_write_file", {"path": outputs[n - 1], "content": KEEP.decode()})
        return worker_reply(request)

    async def case():
        async with _host(
            tmp_path / "root",
            workspace_base=DirectoryBase(user),
            provider=LayeredScriptedProvider(worker=worker),
        ) as host:
            mission = host.create_mission(
                {
                    "goal": "替换旧文件",
                    "success_criteria": [f"file:{new}"],
                    "idempotency_key": "replace",
                }
            )["mission_id"]
            # 驱动循环中任何一次异常都会使本用例失败
            final = await host.run_until_settled(mission, timeout=40, poll_seconds=0.05)
            assert str(final["snapshot"]["mission"]["status"]) == "COMPLETED"
            events = host.store.list_events(mission, types=["ResultSubmitted"])
            assert [e.payload.get("deleted") for e in events] == [[old]]
            assert views == {"verification_copy": {new: KEEP}, "integrated_copy": {new: KEEP}}
            change = host.delivery(mission)
            assert {r["path"]: r["op"] for r in change["files"]} == {old: "delete", new: "add"}
            assert host._bases().files(change["commit"]) == {new: KEEP}
            receipt = host.apply_delivery(mission, operation_key="k")
            assert {r["path"]: r["status"] for r in receipt["files"]} == {old: status, new: status}
            assert host.apply_delivery(mission, operation_key="k") == {**receipt, "repeat": True}
            expected = {old: OLD} if status == "refused" else {new: KEEP}
            assert {
                p.relative_to(user).as_posix(): p.read_bytes()
                for p in user.rglob("*")
                if p.is_file()
            } == expected
            other = host.create_mission(
                {"goal": "写说明", "success_criteria": ["file:a.md"], "idempotency_key": "other"}
            )["mission_id"]
            final = await host.run_until_settled(other, timeout=40, poll_seconds=0.05)
            assert str(final["snapshot"]["mission"]["status"]) == "COMPLETED"

    asyncio.run(case())


def test_initial_disk_normalisation_keeps_original_artifact_name(tmp_path):
    from agent_orchestrator.artifacts.workspace import WorkspaceManager

    original, disk = "café", "cafe\u0301"
    live = WorkspaceManager(tmp_path / "ws").create("attempt", seed={disk: "菜单"})
    if not live.resolve(original).is_file():
        pytest.skip("工作盘能分别表示两种规范拼写")
    aliases = live.initial_aliases([original])
    assert aliases == {disk: [original]}
    items = live.snapshot(mission_id="m", task_id="t", produced_by="w", aliases=aliases)
    assert [a.path for a in items] == [original]
    # 种子沿用磁盘拼写时，底里的名字也仍然在，不能收成删除。
    assert live.initial_aliases([original, disk]) == {disk: [disk, original]}


@pytest.mark.parametrize(
    ("saved_as", "status"), [("Report.md", "applied"), ("report.md", "failed")]
)
def test_delete_verifies_the_exact_name_after_a_user_save(tmp_path, saved_as, status):
    user = tmp_path / "user"
    user.mkdir()
    (user / "report.md").write_bytes(OLD)

    class ConcurrentSave(DirectoryBase):
        def delete_file(self, path, *, expected):
            outcome = super().delete_file(path, expected=expected)
            (self.root / saved_as).write_bytes(KEEP)
            return outcome

    adapter = ConcurrentSave(user)
    repo = BaseRepo(tmp_path / "work.git")
    change = build_change_set(
        repo, _base(repo, adapter), [], reserved=(), deleted={"report.md": "t"}
    )
    [row] = apply_change_set(adapter, repo, change)
    assert row["status"] == status
    assert row["reason"] == ("verify_mismatch" if status == "failed" else None)
    assert os.listdir(user) == [saved_as]
    assert (user / saved_as).read_bytes() == KEEP

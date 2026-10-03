# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P1 oracle: reopened publishing must respect all Missions' source storage.

HTN 补齐阶段 A′（分诊表 p33 行）：

* 两条删除：``reopened_code_action_cannot_publish_inside_other_missions_source_storage`` 与
  ``disjoint_publish_executes_with_or_without_source_missions``，由
  ``test_p33_source_runtime.py`` 里产品门口的重叠拒绝 / 不相交放行两条覆盖。
* 四条重写为两个产品同形主循环用例（产品部署 + 真实 ``FilePublishConnector``，任务真跑到"等人批准
  发布"）：
  1. 重启后部署的发布目录落进了证据存储：人批准以后交接被拒（``source_publish_root_overlap``），
     连接器一次都没被调用。原来分开的"撤销 / 已结束任务仍保护共享存储""没登记来源的任务也占着
     存储""执行器建好后新建的来源任务也看得见"三条并成这一条：守卫只看部署的物理存储根，
     从不读别的任务（``ActionExecutor._source_publish_refusal`` 的注释，2026-09-26 起每个任务都有
     ``sources/``），任务状态不再是变量。
  2. 外部发布已经落地、结果没记下来（连接器在提交后断线，对账时服务又连不上），重启后部署的发布
     目录被挪进了存储区：不再新发布，但已完成的那次照样对账成功，只发布过一次。
* 四条改 E（直接测守卫函数）：无物理根 / 发布连接器没有根时失败关闭、共享路径解析符号链接、
  自定义 CAS / 工作区根受保护。产品的编排服务总把 CAS 与工作区根交给执行器，"没有根"只在直接
  构造执行器时出现。
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.runtime.actions import ActionExecutor, publication_overlaps_storage
from agent_orchestrator.runtime.connectors import ConnectorTransportError
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

DEPLOYMENT = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
TARGET = "reports/weekly.md"
PUBLISH = "action:file_publish.publish:" + TARGET


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def observed(root, ledger, **behaviour: Any) -> FilePublishConnector:
    """A real ``FilePublishConnector`` that counts its calls (counters on the instance: the
    operation profile pins the exact connector type).  ``lookup_down=True``: the service is
    unreachable when asked to reconcile; ``fail_after`` is the connector's own fault point."""

    Path(root).mkdir(parents=True, exist_ok=True)
    publisher = FilePublishConnector(root, ledger)
    publisher.executions = publisher.lookups = 0
    if "fail_after" in behaviour:
        publisher.fail_after = behaviour["fail_after"]
    execute, lookup = publisher.execute, publisher.lookup

    def counted_execute(*args, **kwargs):
        publisher.executions += 1
        return execute(*args, **kwargs)

    def counted_lookup(*args, **kwargs):
        publisher.lookups += 1
        if behaviour.get("lookup_down"):
            raise ConnectorTransportError("file_publish: service unreachable")
        return lookup(*args, **kwargs)

    publisher.execute = counted_execute
    publisher.lookup = counted_lookup
    return publisher


def _confirm_completion(world: Any, mission_id: str) -> None:
    """确认页做的事（与产品同形用例三一致）：内容要求照单确认，发布作为必须完成的效果。"""

    workspace = world.control.snapshot(mission_id)["snapshot"]["operation_workspace"]
    actions = [c["id"] for c in workspace["criteria"] if c["statement"].startswith("action:")]
    content = [c["id"] for c in workspace["criteria"] if c["required"] and c["id"] not in actions]
    [obligation] = workspace["obligations"]
    milestone = next(m for m in workspace["milestones"] if m["id"] == "CONTENT_HASH_VERIFIED")
    ref = workspace["requirements_ref"]
    world.control.approve_operation_completion_spec({
        "mission_id": mission_id, "command_id": "confirm-publish-completion",
        "expected_requirements_ref": ref,
        "proposal": {
            "schema_version": 1, "mission_id": mission_id,
            "requirements_ref": {"id": ref["id"], "revision": ref["revision"], "content_hash": ref["content_hash"]},
            "mode": "REQUIRED_EFFECTS", "content_criterion_ids": content,
            "effects": [{
                "effect_key": "publish-weekly", "source_slot_key": "publish-weekly",
                "obligation_id": obligation["id"], "criterion_ids": actions,
                "required_milestone": milestone["id"],
                "milestone_policy_ref": milestone["milestone_policy_ref"],
                "evidence_policy_ref": milestone["evidence_policy_ref"],
            }],
        },
    })


async def _confirmed(world: Any) -> str:
    """建一个带发布的任务，确认完成映射（发布操作还没物化）。"""

    mission_id = world.create({"goal": "写一份周报 reports/weekly.md 并发布",
                               "success_criteria": ["file:" + TARGET, PUBLISH],
                               "idempotency_key": "publish-guard"})["mission_id"]
    await world.drain()
    _confirm_completion(world, mission_id)
    return mission_id


async def _pending_publish(world: Any, mission_id: str) -> dict[str, Any]:
    """主循环跑到"等人批准发布"（内容写好、系统准备好申请单、审阅通过）。"""

    for _ in range(20):
        await world.drain()
        pending = [a for a in world.control.approvals(mission_id) if a.get("state") == "PENDING"]
        if pending:
            return pending[0]
    raise AssertionError(("没有走到等人批准发布", world.store.list_actions(mission_id)))


def _ledger(publisher) -> list[dict[str, Any]]:
    path = publisher.ledger_path
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_publish_dir_moved_into_storage_after_restart_refuses_the_handoff(tmp_path):
    async def case():
        root, ledger = tmp_path / "root", tmp_path / "ledger"
        safe = observed(tmp_path / "published", ledger)
        async with product_world(root, LayeredScriptedProvider(), connectors={"file_publish": safe},
                                 deployment_policy=DEPLOYMENT) as world:
            mission_id = await _confirmed(world)
        # 重启：部署的发布目录换成了证据存储里的一个目录（工作区下面）。建任务门口的检查已经
        # 过去了；发布操作在这之后才按今天的部署物化（物化之后再换目录，先被"操作引用不可用"拦下）。
        inside = observed(root / "workspaces" / "published", ledger)
        async with product_world(root, LayeredScriptedProvider(), connectors={"file_publish": inside},
                                 deployment_policy=DEPLOYMENT) as world:
            approval = await _pending_publish(world, mission_id)
            decided = world.control.decide(approval["request_id"], "approve")
            assert decided["request_state"] in {"GRANTED", "APPROVED"}, decided
            for _ in range(3):
                await world.drain(timeout=5)
            [action] = {a["action_id"]: a for a in world.store.list_actions(mission_id)}.values()
            assert action["state"] != "SUCCEEDED" and action["handoffs"] == 0, action
            refused = [e for e in world.store.list_events(mission_id) if e.type == "ActionHandoffRefused"]
            assert refused and {e.payload["reason"] for e in refused} == {"source_publish_root_overlap"}
            assert world.loop._actions.last_refusal[action["action_key"]] == "source_publish_root_overlap"
        assert safe.executions == inside.executions == 0
        assert _ledger(inside) == [] and not any((root / "workspaces" / "published").rglob("*.md"))

    asyncio.run(case())


def test_completed_receipt_is_still_reconciled_even_when_new_publication_is_forbidden(tmp_path):
    async def case():
        root, ledger = tmp_path / "root", tmp_path / "ledger"
        # 外部发布在提交后断线（连接器自己的故障点），随后对账时服务也连不上
        before = observed(tmp_path / "published", ledger, fail_after="commit", lookup_down=True)
        async with product_world(root, LayeredScriptedProvider(), connectors={"file_publish": before},
                                 deployment_policy=DEPLOYMENT) as world:
            mission_id = await _confirmed(world)
            approval = await _pending_publish(world, mission_id)
            world.control.decide(approval["request_id"], "approve")
            for _ in range(10):
                await world.drain(timeout=3)
                states = {a["state"] for a in world.store.list_actions(mission_id)}
                if before.executions and "UNKNOWN" in states:
                    break
            assert before.executions == 1 and states == {"UNKNOWN"}, (states, before.executions)
        assert [e["state"] for e in _ledger(before)] == ["PREPARED", "COMMITTED"]
        # 运维把发布目录（连同已发布的文件）挪进了证据存储区；重启后不能再新发布
        moved = root / "workspaces" / "published"
        shutil.move(str(tmp_path / "published"), str(moved))
        after = observed(moved, ledger)
        async with product_world(root, LayeredScriptedProvider(), connectors={"file_publish": after},
                                 deployment_policy=DEPLOYMENT) as world:
            for _ in range(10):
                await world.drain(timeout=3)
                actions = world.store.list_actions(mission_id)
                if {a["state"] for a in actions} != {"UNKNOWN"}:
                    break
            settled = actions[-1]
            assert settled["state"] == "SUCCEEDED" and settled["handoffs"] == 1, settled
        assert before.executions == 1 and after.executions == 0 and after.lookups >= 1
        assert [e["state"] for e in _ledger(before)] == ["PREPARED", "COMMITTED"]
        assert len([p for p in moved.rglob("*") if p.is_file()]) == 1

    asyncio.run(case())


# ---------------------------------------------------------------- 守卫函数（E）

ACTION = {"connector": "file_publish"}


@pytest.mark.parametrize("missing", ["roots", "publisher_root"])
def test_guard_without_physical_roots_fails_closed(tmp_path, missing):
    """没有物理存储根（只在直接构造执行器时出现）或发布连接器没有根：失败关闭。"""

    if missing == "roots":
        publisher = FilePublishConnector(tmp_path / "published", tmp_path / "ledger")
        run = ActionExecutor(None, {"file_publish": publisher}, DEPLOYMENT, owner="no-roots")
    else:
        run = ActionExecutor(None, {"file_publish": object()}, DEPLOYMENT, owner="no-root-attr",
                             source_storage_roots=(tmp_path / "artifacts", tmp_path / "workspaces"))
    assert run._source_publish_refusal(ACTION) == "source_publish_root_unavailable"


@pytest.mark.parametrize(
    "destination,overlap", [("parent", True), ("child", True), ("sibling", False)]
)
def test_shared_path_helper_resolves_parent_symlinks_and_keeps_siblings_distinct(
    tmp_path, destination, overlap
):
    protected = tmp_path / "storage" / "sources"
    protected.mkdir(parents=True)
    sibling = tmp_path / "storage" / "sources-published"
    sibling.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(protected.parent, target_is_directory=True)
    target = {
        "parent": link,
        "child": link / "sources" / "nested",
        "sibling": link / "sources-published",
    }[destination]
    assert publication_overlaps_storage(target, (protected,)) is overlap


@pytest.mark.parametrize("which", ["cas", "workspaces", "disjoint"])
def test_explicit_custom_cas_and_workspace_roots_are_protected(tmp_path, which):
    roots = (tmp_path / "custom-cas", tmp_path / "custom-workspaces")
    target = {"cas": roots[0], "workspaces": roots[1] / "nested", "disjoint": tmp_path / "published"}[which]
    publisher = FilePublishConnector(target, tmp_path / "ledger")
    run = ActionExecutor(None, {"file_publish": publisher}, DEPLOYMENT, owner="custom",
                         source_storage_roots=roots)
    expected = None if which == "disjoint" else "source_publish_root_overlap"
    assert run._source_publish_refusal(ACTION) == expected
    # 只管发布连接器：别的连接器不受这道守卫影响
    assert run._source_publish_refusal({"connector": "other"}) is None

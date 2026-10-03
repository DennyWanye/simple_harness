# SPDX-License-Identifier: Apache-2.0
"""产品同形测试世界的代表用例三：带对外操作（发布文件）的任务（HTN 补齐阶段 A′ 第 2 步）。

产品流程见 plans/2026-09-28-system-operations/00-PLAN.md 第一部分"改完后，一个带发布的任务怎么走"：
建任务 → 人在确认页确认完成映射并选上必须完成的效果（自动模式不代签带 ``action:`` 的任务）→
内容步骤写出文件 → 系统按已批准效果自动准备申请单、审阅员通过 → 人点"批准" → 真实的
``FilePublishConnector`` 发布到授权目录 → 系统读回核对、结果审阅通过 → 任务完成。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import pytest

from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

TARGET = "reports/weekly.md"
PUBLISH = "action:file_publish.publish:" + TARGET


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _workspace(world: Any, mission_id: str) -> dict[str, Any]:
    return world.control.snapshot(mission_id)["snapshot"]["operation_workspace"]


def _confirm_completion(world: Any, mission_id: str) -> dict[str, Any]:
    """确认页做的事：内容要求照单确认，``action:`` 要求作为必须完成的效果，挂在根义务上，
    完成标准选"内容哈希一致"（与前端默认一致）。"""

    workspace = _workspace(world, mission_id)
    assert workspace["state"] == "CONFIRMATION_REQUIRED" and workspace["editable"] is True, workspace
    actions = [c["id"] for c in workspace["criteria"] if c["statement"].startswith("action:")]
    content = [c["id"] for c in workspace["criteria"] if c["required"] and c["id"] not in actions]
    [obligation] = workspace["obligations"]
    milestone = next(m for m in workspace["milestones"] if m["id"] == "CONTENT_HASH_VERIFIED")
    ref = workspace["requirements_ref"]
    return world.control.approve_operation_completion_spec({
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


def test_a_publishing_mission_completes_on_the_product_deployment(tmp_path):
    async def case():
        published = tmp_path / "published"
        published.mkdir()
        connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
        policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
        provider = LayeredScriptedProvider()
        async with product_world(tmp_path / "root", provider, connectors={"file_publish": connector},
                                 deployment_policy=policy) as world:
            created = world.create({"goal": "写一份周报 reports/weekly.md 并发布",
                                    "success_criteria": ["file:" + TARGET, PUBLISH],
                                    "idempotency_key": "operation-1"})
            mission_id = created["mission_id"]

            # 1～2. 自动模式不代签带 action: 的任务：确认页等着人
            await world.drain()
            assert str(world.store.get_mission(mission_id).status.value) == "CREATED"
            receipt = _confirm_completion(world, mission_id)
            assert receipt["authority"]["kind"] == "USER_CONFIRMED"

            # 3～4. 内容步骤写文件 → 系统准备申请单、审阅通过 → 等人批准
            approvals: list[dict[str, Any]] = []
            for _ in range(20):
                await world.drain()
                approvals = [a for a in world.control.approvals(mission_id) if a.get("state") == "PENDING"]
                if approvals:
                    break
            assert len(approvals) == 1, (world.store.get_mission(mission_id).status, world.store.list_actions(mission_id))
            assert not any(published.rglob("*.md"))  # 没批准之前什么都没发布

            # 5. 人点"批准"
            decided = world.control.decide(approvals[0]["request_id"], "approve")
            assert decided["request_state"] in {"GRANTED", "APPROVED"}, decided

            # 6～7. 真实连接器发布 → 读回核对 → 结果审阅 → 完成
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)

            # 全程只有一次发布：一个动作、一次交接、连接器账本里一条发布意图、目标目录里一个文件
            actions = world.store.list_actions(mission_id)
            assert len({a["action_id"] for a in actions}) == 1, actions
            action = actions[-1]
            assert (action["connector"], action["operation"], action["target"], action["state"]) == (
                "file_publish", "publish", TARGET, "SUCCEEDED"), action
            assert action["handoffs"] == 1 and action["reason_source"] == "system"
            ledger = [json.loads(line) for line in (tmp_path / "root" / "connectors" / "file_publish" / "ledger.jsonl")
                      .read_text(encoding="utf-8").splitlines() if line.strip()]
            assert [entry["state"] for entry in ledger if entry.get("state") == "PREPARED"] == ["PREPARED"], ledger
            files = [p for p in published.rglob("*") if p.is_file()]
            # 连接器只新建、不覆盖：发布名带内容版本号，回执里记着它
            assert [p.relative_to(published).as_posix() for p in files] == [action["receipt"]["service_ref"]]

            # 发布出去的字节与工作区里审过的那份产物逐字一致
            [artifact] = [a for a in world.store.list_mission_artifacts(mission_id) if a.path == TARGET]
            assert artifact.content_hash == action["params"]["content_hash"]
            workspace_bytes = world.loop.assembled.workspaces.artifact_store.read(artifact.content_hash)
            assert files[0].read_bytes() == workspace_bytes
            assert hashlib.sha256(workspace_bytes).hexdigest() == action["receipt"]["after"]["content_hash"]

    asyncio.run(case())


def test_a_fenced_publish_waits_instead_of_failing(tmp_path, monkeypatch):
    """阶段 B 裁决第 5 类：发布交接时恰好撞上改做法的围栏，动作留在可交接状态、下一轮再试，
    不以"动作失败"停任务；围栏结束后照常发布（暂时性只查错误码表那一列）。

    围栏用一次性的"已被围"注入：人批准之后的头三次交接核对报"已被围"，之后放行。

    **改坏检验**：错误码表里去掉"已被围"的暂时性 → 任务以"动作失败"停 → 变红。
    """
    from agent_orchestrator.orchestrator import taskgraph_dispatch
    from agent_orchestrator.storage.store import StoreConflict

    original = taskgraph_dispatch.require_taskgraph_unfenced
    fenced: list[str] = []
    approved = {"yes": False}

    def fenced_three_times(store, mission_id, task_id):  # type: ignore[no-untyped-def]
        if approved["yes"] and len(fenced) < 3:
            fenced.append(task_id)
            raise StoreConflict("TASKGRAPH_TARGET_FENCED")
        return original(store, mission_id, task_id)

    monkeypatch.setattr(taskgraph_dispatch, "require_taskgraph_unfenced", fenced_three_times)

    async def case():
        published = tmp_path / "published"
        published.mkdir()
        connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
        policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), connectors={"file_publish": connector},
                                 deployment_policy=policy) as world:
            mission_id = world.create({"goal": "写一份周报 reports/weekly.md 并发布",
                                       "success_criteria": ["file:" + TARGET, PUBLISH],
                                       "idempotency_key": "fenced-publish"})["mission_id"]
            await world.drain()
            _confirm_completion(world, mission_id)
            approvals: list[dict[str, Any]] = []
            for _ in range(20):
                await world.drain()
                approvals = [a for a in world.control.approvals(mission_id) if a.get("state") == "PENDING"]
                if approvals:
                    break
            approved["yes"] = True
            world.control.decide(approvals[0]["request_id"], "approve")
            mission = await world.run_until_settled(mission_id, rounds=30)
            assert len(fenced) == 3
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            [action] = world.store.list_actions(mission_id)[-1:]
            assert action["state"] == "SUCCEEDED" and action["handoffs"] == 1

    asyncio.run(case())


def test_the_confirmation_page_refuses_a_milestone_the_profile_cannot_reach(tmp_path):
    """阶段 B 裁决第 4 类：确认页提交了发布档案到不了的完成标准（"已送达"），当场按名拒收
    （``OP_CAPABILITY_UNSUPPORTED``），库里不写完成要求——不再收下以后让效果永远停在"等申请"。

    **改坏检验**：确认处不核对档案 → 收下了 → 变红。
    """
    from agent_orchestrator.api.facade import FacadeError
    from agent_orchestrator.storage.operation_completion_store import OperationCompletionStore

    async def case():
        published = tmp_path / "published"
        published.mkdir()
        connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
        policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), connectors={"file_publish": connector},
                                 deployment_policy=policy) as world:
            mission_id = world.create({"goal": "写一份周报 reports/weekly.md 并发布",
                                       "success_criteria": ["file:" + TARGET, PUBLISH],
                                       "idempotency_key": "delivered"})["mission_id"]
            await world.drain()
            workspace = _workspace(world, mission_id)
            assert "DELIVERED" not in {m["id"] for m in workspace["milestones"]}  # the page never offers it
            actions = [c["id"] for c in workspace["criteria"] if c["statement"].startswith("action:")]
            content = [c["id"] for c in workspace["criteria"] if c["required"] and c["id"] not in actions]
            [obligation] = workspace["obligations"]
            milestone = workspace["milestones"][0]
            ref = workspace["requirements_ref"]
            with pytest.raises(FacadeError) as refused:
                world.control.approve_operation_completion_spec({
                    "mission_id": mission_id, "command_id": "confirm-delivered",
                    "expected_requirements_ref": ref,
                    "proposal": {
                        "schema_version": 1, "mission_id": mission_id,
                        "requirements_ref": {"id": ref["id"], "revision": ref["revision"],
                                             "content_hash": ref["content_hash"]},
                        "mode": "REQUIRED_EFFECTS", "content_criterion_ids": content,
                        "effects": [{
                            "effect_key": "publish-weekly", "source_slot_key": "publish-weekly",
                            "obligation_id": obligation["id"], "criterion_ids": actions,
                            "required_milestone": "DELIVERED",
                            "milestone_policy_ref": milestone["milestone_policy_ref"],
                            "evidence_policy_ref": milestone["evidence_policy_ref"],
                        }],
                    },
                })
            assert refused.value.code == "OP_CAPABILITY_UNSUPPORTED"
            assert OperationCompletionStore(world.store).get_spec_exact(
                mission_id, ref["revision"], ref["content_hash"]) is None
            assert str(world.store.get_mission(mission_id).status.value) == "CREATED"

    asyncio.run(case())

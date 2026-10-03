# SPDX-License-Identifier: Apache-2.0
"""产品同形测试世界的代表用例三：带对外操作（发布文件）的任务（HTN 补齐阶段 A′ 第 2 步）。

产品流程见 plans/2026-09-28-system-operations/00-PLAN.md 第一部分"改完后，一个带发布的任务怎么走"：
建任务 → 人在确认页确认完成映射并选上必须完成的效果（自动模式不代签带 ``action:`` 的任务）→
内容步骤写出文件 → 系统按已批准效果自动准备申请单、审阅员通过 → 人点"批准" → 真实的
``FilePublishConnector`` 发布到授权目录 → 系统读回核对、结果审阅通过 → 任务完成。
"""
from __future__ import annotations

import asyncio
import os
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


def _pending_cards(world: Any, mission_id: str) -> list[dict[str, Any]]:
    return [a for a in world.control.approvals(mission_id) if a.get("state") == "PENDING"]


async def _until_card(world: Any, mission_id: str, seen: int) -> dict[str, Any]:
    for _ in range(30):
        await world.drain()
        cards = _pending_cards(world, mission_id)
        if cards and len(world.store.list_actions(mission_id)) > seen:
            return cards[0]
    raise AssertionError("no new approval card")


def _block_with_someone_elses_file(published: Any, mission_id: str, world: Any) -> None:
    """Someone else's file already sits at the exact name the pending version would publish to."""
    from pathlib import PurePosixPath

    from agent_orchestrator.runtime.connectors_publish import _name_for

    [action] = [a for a in world.store.list_actions(mission_id) if a["state"] == "AWAITING_APPROVAL"]
    name = _name_for(action["idempotency_key"], PurePosixPath(TARGET))
    (published / "reports").mkdir(parents=True, exist_ok=True)
    (published / "reports" / name).write_text("别人放的", encoding="utf-8")


@pytest.mark.parametrize("refusals", [1, 3])
def test_a_publish_the_service_refuses_is_proven_unapplied_and_offered_again(tmp_path, refusals):
    """阶段 B 裁决第 1 类：发布服务明确拒绝（目标处已有别人放的同名文件）。此前动作停在"未了结"，
    闸门关着，规划器和人都收不到。现在发布台账证明它从未落地，系统按原内容出新卡（卡上写着服务
    原文理由与第几次）；人再批准就发布。同一效果按原内容最多重交 2 次（共 3 张卡），仍没生效以
    "动作失败"具名停，详情列出每次的结局。别人的文件从来不被覆盖。

    **改坏检验**：档案里不登记对账适配器 → 动作停在未了结、没有新卡 → 变红。
    """

    async def case():
        published = tmp_path / "published"
        published.mkdir()
        connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
        policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), connectors={"file_publish": connector},
                                 deployment_policy=policy) as world:
            mission_id = world.create({"goal": "写一份周报 reports/weekly.md 并发布",
                                       "success_criteria": ["file:" + TARGET, PUBLISH],
                                       "idempotency_key": f"refused-{refusals}"})["mission_id"]
            await world.drain()
            _confirm_completion(world, mission_id)
            card = await _until_card(world, mission_id, 0)
            for attempt in range(1, refusals + 1):
                _block_with_someone_elses_file(published, mission_id, world)
                world.control.decide(card["request_id"], "approve")
                if attempt == 3:
                    break
                card = await _until_card(world, mission_id, attempt)
                [latest] = [a for a in world.store.list_actions(mission_id) if a["state"] == "AWAITING_APPROVAL"]
                assert latest["previous_attempt"]["outcome"] == "service_refused"
                assert "already exists" in latest["previous_attempt"]["reason"]
                assert card["summary"]["previous_attempt"] == latest["previous_attempt"]
            if refusals == 3:
                mission = await world.run_until_settled(mission_id, rounds=20)
                assert str(mission.status.value) == "FAILED" and mission.stop_reason == "action_failed"
                detail = mission.final_report["detail"]
                assert detail["reason"] == "publish_not_applied"
                assert [item["outcome"] for item in detail["attempts"]] == ["service_refused"] * 3
                assert len(world.store.list_actions(mission_id)) == 3  # never a fourth card
            else:
                world.control.decide(card["request_id"], "approve")
                mission = await world.run_until_settled(mission_id, rounds=20)
                assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
                ours = [p for p in published.rglob("*.md") if p.read_text(encoding="utf-8") != "别人放的"]
                assert len(ours) == 1
            others = [p for p in published.rglob("*.md") if p.read_text(encoding="utf-8") == "别人放的"]
            assert len(others) == refusals  # someone else's files are never overwritten

    asyncio.run(case())


def test_a_link_that_succeeded_before_an_error_is_never_proven_unapplied(tmp_path, monkeypatch):
    """核验阻断项（2026-10-03）：链接其实成功了，之后才报错（网络卷回复丢失、删临时文件失败），
    连接器照样在台账写"已放弃"。台账不能单独当证明：这个键自己的文件在、字节对，就不出"没落地"
    证明——不重交、不出新卡，同一份内容只发布一次，等人裁定。"""
    import agent_orchestrator.runtime.connectors_publish as connectors_publish

    real_link = os.link
    linked = {"n": 0}

    def link(src, dst, **kwargs):  # type: ignore[no-untyped-def]
        real_link(src, dst, **kwargs)
        if "dst_dir_fd" in kwargs and str(dst).startswith("weekly."):
            linked["n"] += 1
            if linked["n"] == 1:
                raise OSError(5, "Input/output error (the link applied, its reply was lost)")

    monkeypatch.setattr(connectors_publish.os, "link", link)

    async def case():
        published = tmp_path / "published"
        published.mkdir()
        connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
        policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), connectors={"file_publish": connector},
                                 deployment_policy=policy) as world:
            mission_id = world.create({"goal": "写一份周报 reports/weekly.md 并发布",
                                       "success_criteria": ["file:" + TARGET, PUBLISH],
                                       "idempotency_key": "linked-then-error"})["mission_id"]
            await world.drain()
            _confirm_completion(world, mission_id)
            card = await _until_card(world, mission_id, 0)
            world.control.decide(card["request_id"], "approve")
            for _ in range(10):
                await world.drain(timeout=5)
            actions = world.store.list_actions(mission_id)
            assert len(actions) == 1 and not _pending_cards(world, mission_id), actions  # no second card
            assert not [e for e in world.store.list_events(mission_id) if e.type == "ActionScopedReconciled"
                        and e.payload["outcome"] == "NOT_APPLIED_FINAL"]
            assert len(list(published.rglob("*.md"))) == 1  # published exactly once

    asyncio.run(case())


def test_an_unproven_failure_with_no_publisher_bound_lets_the_loop_go_idle(tmp_path):
    """核验阻断项（2026-10-03）：失败而没查清的发布，发布器已不在（目录撤销授权、重启后没接）时，
    对账这一轮什么也做不了——不许把它算作"有进展"让 ``run()`` 一直空转。"""
    from agent_orchestrator.runtime.connectors_publish import ConnectorTransportError

    async def case():
        published = tmp_path / "published"
        published.mkdir()
        connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")

        def down(key):  # type: ignore[no-untyped-def]
            raise ConnectorTransportError("down")

        connector.ledger_record = down  # type: ignore[method-assign]
        policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), connectors={"file_publish": connector},
                                 deployment_policy=policy) as world:
            mission_id = world.create({"goal": "写一份周报 reports/weekly.md 并发布",
                                       "success_criteria": ["file:" + TARGET, PUBLISH],
                                       "idempotency_key": "no-publisher"})["mission_id"]
            await world.drain()
            _confirm_completion(world, mission_id)
            card = await _until_card(world, mission_id, 0)
            _block_with_someone_elses_file(published, mission_id, world)
            world.control.decide(card["request_id"], "approve")
            for _ in range(10):
                await world.drain(timeout=5)
                if any(a["state"] == "FAILED" for a in world.store.list_actions(mission_id)):
                    break
            [action] = [a for a in world.store.list_actions(mission_id) if a["state"] == "FAILED"]
            action["reconcile_unavailable"], action["needs_human"] = 0, False
            world.store.put_action(action)
            world.loop._connectors = {}
            world.loop.commit._operation_materialization_runtime = None
            await asyncio.wait_for(world.loop.run(), timeout=10)  # returns: nothing to do is idle

    asyncio.run(case())


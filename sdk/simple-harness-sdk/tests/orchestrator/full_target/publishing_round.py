# SPDX-License-Identifier: Apache-2.0
"""带对外操作（发布文件）的任务上的一次规划修复回合：h1h 计划提交闸门用例的种子（HTN 补齐阶段 A′）。

照产品同形代表用例三（tests/orchestrator/product_world/test_operation.py）走到"申请单等人批准"：
建任务 → 人在确认页确认完成映射并选上发布效果 → 内容步骤写出文件、审阅通过 → 系统准备发布申请单。
这时第 1 版计划在用。规划器经主循环自己的入口被问一轮，答"给写文件那一步提后继步骤"；这个改动
碰到发布操作的生产者，要先经执行图收敛，所以第一遍收集只编译、不提交（决定停在 COMPILED），
提交在后面的主循环轮次里落定。用例在这之前安排外界的事（人点批准、另一连接持写锁、发布服务
掉线），再用 :func:`run_rounds` 推进主循环。

替身只有：脚本化模型回复、一个"发布服务调用中掉线"的连接器调用（``unknown_outcome=True``，
外界真会发生的事）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from agent_orchestrator.testing.product_world import ProductWorld, product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    package_of,
    planner_reply,
    review_input,
    review_reply,
)

TARGET = "reports/weekly.md"
PUBLISH = "action:file_publish.publish:" + TARGET
POLICY = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")


def confirm_completion(world: ProductWorld, mission_id: str) -> dict[str, Any]:
    """确认页做的事：内容要求照单确认，``action:`` 要求作为必须完成的发布效果挂在根义务上，
    完成标准选"内容哈希一致"（与代表用例三、前端默认一致）。"""

    workspace = world.control.snapshot(mission_id)["snapshot"]["operation_workspace"]
    assert workspace["state"] == "CONFIRMATION_REQUIRED", workspace
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


@dataclass
class PublishingRound:
    world: ProductWorld
    mission_id: str
    approval_id: str  # the publish request waiting for the person
    intent: Any  # the Planner round whose successor decision is COMPILED, not yet committed
    dispatch: Any

    @property
    def loop(self) -> Any:
        return self.world.loop

    def decision(self) -> dict[str, Any]:
        row = PlanningDecisionStore(self.loop.store).get_planning_decision_by_attempt(self.intent.intent_id, 0)
        assert row is not None
        return row

    def plan_revision(self) -> int:
        return int(self.dispatch.network(self.mission_id).plan_revision)

    def action_states(self) -> list[str]:
        return [str(action["state"]) for action in self.loop.store.list_actions(self.mission_id)]


async def _drain_until(world: ProductWorld, done: Any, *, rounds: int = 20) -> None:
    for _ in range(rounds):
        await world.drain()
        if done():
            return
    raise AssertionError("the publishing mission did not reach the expected state")


async def run_rounds(round_: PublishingRound, *, rounds: int = 40) -> dict[str, Any]:
    """Run main-loop rounds (with the deployment's duties) until the decision leaves COMPILED
    or ``rounds`` are used up; return the decision row."""

    for _ in range(rounds):
        await round_.world.deployment.between_cycles(auto=True)
        await round_.loop._cycle()
        await asyncio.sleep(0.01)
        if round_.decision()["status"] != "COMPILED":
            break
    return round_.decision()


@asynccontextmanager
async def publishing_round(root: Path, *, key: str, unknown_outcome: bool = False) -> AsyncIterator[PublishingRound]:
    published = root / "published"
    published.mkdir(parents=True)
    connector = FilePublishConnector(published, root / "world" / "connectors" / "file_publish")
    async with product_world(root / "world", LayeredScriptedProvider(), connectors={"file_publish": connector},
                             deployment_policy=POLICY) as world:
        store = world.store
        mission_id = world.create({"goal": "写一份周报 reports/weekly.md 并发布", "idempotency_key": key,
                                   "success_criteria": ["file:" + TARGET, PUBLISH]})["mission_id"]
        await world.drain()
        confirm_completion(world, mission_id)

        def pending() -> list[dict[str, Any]]:
            return [item for item in world.control.approvals(mission_id) if item.get("state") == "PENDING"]

        await _drain_until(world, pending)
        [approval] = pending()
        if unknown_outcome:
            def dropped(*_args: Any, **_kwargs: Any) -> Any:
                raise ConnectionError("the publishing service dropped the connection mid-call")

            connector.execute = dropped  # type: ignore[method-assign]
            world.control.decide(approval["request_id"], "approve")
            await _drain_until(world, lambda: any(a["state"] == "UNKNOWN" for a in store.list_actions(mission_id)))

        loop = world.loop
        dispatch = loop._dispatch_for(mission_id)
        network = dispatch.network(mission_id)
        assert int(network.plan_revision) == 1
        ordinal = store.connection.execute(
            "SELECT COUNT(*) FROM dispatch_intents WHERE mission_id=? AND kind='plan' AND subject_id LIKE ?",
            (mission_id, f"{mission_id}:planner:%")).fetchone()[0] + 1
        intent = await loop._create_planner_intent(mission_id, ordinal=ordinal)
        await world.deployment.between_cycles(auto=True)  # the deployment's planning grant (auto mode)
        package = intent.config["planning_package"]
        write = next(binding for binding in network.task_bindings if str(binding.form) == "primitive")

        def visible(kind: str, identity: Any) -> dict[str, Any]:
            return next(row for row in package["visible_refs"] if row["kind"] == kind and row["id"] == str(identity))

        task_type = next(spec for spec in dispatch.require_planning_world().catalog.task_types()
                         if spec.goal_signature == write.goal_signature)
        body = {
            "schema_version": 1, "decision_type": "REPAIR",
            "subject_key": next(row["subject_key"] for row in package["planning_subjects"]
                                if row["task_id"] == str(write.task_id)),
            "rationale": "周报引用了旧数据，用同类型的后继步骤重写。", "reason_refs": [], "assumptions": [],
            "uncertainties": [], "alternatives": [], "replan_triggers": [],
            "payload": {"repair_kind": "PROPOSE_SUCCESSOR", "old_task_ref": visible("task", write.task_id),
                        "obligation_ref": visible("obligation", write.obligation_id),
                        "goal_type_ref": task_type.task_type_ref.to_json(),
                        "bindings": {"goal": "按最新数据重写周报。"}},
        }
        await loop._collect_plan_decision(intent, object(), store.get_mission(mission_id),
                                          "<planning_decision>" + json.dumps(body, ensure_ascii=False)
                                          + "</planning_decision>", dispatch)
        round_ = PublishingRound(world, mission_id, approval["request_id"], intent, dispatch)
        if unknown_outcome:
            # 2026-10-06 第 2～4 批车道 O（A48，原计划 Assurance §7.2）：发布结果不明时根结论照常先形成，
            # 根职责随之了结；对这个根的修复提交按"职责已了结"被拒（OBLIGATION_NOT_OPEN），不编修订。
            assert round_.decision()["status"] == "REJECTED", round_.decision()
            assert round_.decision()["rejection_codes"] == ["OBLIGATION_NOT_OPEN"], round_.decision()
        else:
            assert round_.decision()["status"] == "COMPILED", round_.decision()
        yield round_


@dataclass
class RefusedFinalRound:
    """发布结果不明、根审阅判不通过之后的局面：根职责还开着，规划器被请来修复。"""

    world: ProductWorld
    mission_id: str
    connector: FilePublishConnector
    answered: list[str]  # 规划器为修复请求交出的决定（按次序，脚本化回复的正文）

    @property
    def loop(self) -> Any:
        return self.world.loop

    def repairs(self) -> list[dict[str, Any]]:
        rows = self.loop.store.connection.execute(
            "SELECT decision_id FROM planning_decisions WHERE decision_type='REPAIR' ORDER BY created_at").fetchall()
        found = [PlanningDecisionStore(self.loop.store).get_planning_decision(row["decision_id"]) for row in rows]
        return [row for row in found if row is not None]


@asynccontextmanager
async def refused_final_round(root: Path, *, key: str) -> AsyncIterator[RefusedFinalRound]:
    """发布服务调用中掉线（结果不明）后，任务级终审判"周报引用了旧数据"不通过。

    按原计划 Assurance §7.2，终审不等结果不明的效果；判不通过时根结论不形成，根职责还开着，
    系统请规划器修复。脚本化规划器的回答与 :func:`publishing_round` 一样：给写文件那一步提后继
    步骤。这一步是发布操作的生产者，所以计划改动要先经执行图收敛"先对账未决操作"。"""

    published = root / "published"
    published.mkdir(parents=True)
    connector = FilePublishConnector(published, root / "world" / "connectors" / "file_publish")
    seen: dict[str, Any] = {"refused": False, "write": None, "type_ref": None}
    answered: list[str] = []

    def reviewer(request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        if str((data.get("package") or {}).get("purpose")) == "MISSION_FINAL" and not seen["refused"]:
            seen["refused"] = True
            return review_reply(data, verdict="REJECTED", grade="FAIL", reason="脚本化审阅：周报引用了旧数据。")
        return review_reply(data)

    def planner(request: Any) -> Any:
        package = package_of(request)
        write = seen["write"]
        if not package.get("repair_requests") or write is None:
            return planner_reply(request)

        def visible(kind: str, identity: Any) -> dict[str, Any]:
            return next(row for row in package["visible_refs"] if row["kind"] == kind and row["id"] == str(identity))

        body = {
            "schema_version": 1, "decision_type": "REPAIR",
            "subject_key": next(row["subject_key"] for row in package["planning_subjects"]
                                if row["task_id"] == str(write.task_id)),
            "rationale": "周报引用了旧数据，用同类型的后继步骤重写。", "reason_refs": [], "assumptions": [],
            "uncertainties": [], "alternatives": [], "replan_triggers": [],
            "payload": {"repair_kind": "PROPOSE_SUCCESSOR", "old_task_ref": visible("task", write.task_id),
                        "obligation_ref": visible("obligation", write.obligation_id),
                        "goal_type_ref": seen["type_ref"], "bindings": {"goal": "按最新数据重写周报。"}},
        }
        reply = "<planning_decision>" + json.dumps(body, ensure_ascii=False) + "</planning_decision>"
        answered.append(reply)
        return reply

    async with product_world(root / "world", LayeredScriptedProvider(planner=planner, reviewer=reviewer),
                             connectors={"file_publish": connector}, deployment_policy=POLICY) as world:
        store = world.store
        mission_id = world.create({"goal": "写一份周报 reports/weekly.md 并发布", "idempotency_key": key,
                                   "success_criteria": ["file:" + TARGET, PUBLISH]})["mission_id"]
        await world.drain()
        confirm_completion(world, mission_id)

        def pending() -> list[dict[str, Any]]:
            return [item for item in world.control.approvals(mission_id) if item.get("state") == "PENDING"]

        await _drain_until(world, pending)
        [approval] = pending()
        dispatch = world.loop._dispatch_for(mission_id)
        write = next(binding for binding in dispatch.network(mission_id).task_bindings if str(binding.form) == "primitive")
        seen["type_ref"] = next(spec for spec in dispatch.require_planning_world().catalog.task_types()
                                if spec.goal_signature == write.goal_signature).task_type_ref.to_json()
        seen["write"] = write

        def dropped(*_args: Any, **_kwargs: Any) -> Any:
            raise ConnectionError("the publishing service dropped the connection mid-call")

        connector.execute = dropped  # type: ignore[method-assign]
        world.control.decide(approval["request_id"], "approve")
        await _drain_until(world, lambda: any(a["state"] == "UNKNOWN" for a in store.list_actions(mission_id)))
        await _drain_until(world, lambda: any(event.type == "TaskGraphConvergenceAdvanced"
                                              for event in store.list_events(mission_id)))
        yield RefusedFinalRound(world, mission_id, connector, answered)


__all__ = ("POLICY", "PUBLISH", "TARGET", "PublishingRound", "RefusedFinalRound", "confirm_completion",
           "publishing_round", "refused_final_round", "run_rounds")

# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""H4 结构修复：规划器发修复决定，经真实收集器预览、准入、提交（HTN 补齐阶段 A′ 迁移，2026-10-03）。

产品同形部署（``taskgraph_exec/production_fixture.enabled_world``）上主循环先真跑出第 1 版计划；
修复决定由脚本化规划器经主循环自己的入口（开规划回合 → 收集器）递交，不再直接调编译器或
裸 ``CommitService``。

迁移时删掉的原用例：
* "后继步骤提交出同义务、同预算的新步骤" → taskgraph_exec/test_successor_with_taskgraph::
  test_a_successor_for_a_leaf_commits_a_second_plan_revision_with_the_taskgraph_on；
* "两个做法消费者共享同一个在跑目标"（SHARE_ACTIVE）→ 产品"用户目标"世界没有可共享的任务类型，
  按分诊裁决⑥等阶段 D 带共享的 world_factory（记偏离）；
* "取消可选分支只放它自己的需求" → 可选分支要独立授权的步骤，产品编译从不带槽位授权
  （``slot_authorizations`` 无调用方），造不出来（记偏离）。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_FULL = Path(__file__).resolve().parent
for _extra in (_FULL, _FULL / "taskgraph_exec"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from production_fixture import CHAIN_CRITERIA, chain_planner, enabled_world  # noqa: E402

from agent_orchestrator.contracts import TaskStatus  # noqa: E402
from agent_orchestrator.contracts.models import sha256_hex  # noqa: E402
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore  # noqa: E402
from agent_orchestrator.testing.fixtures import package_of, role_of  # noqa: E402
from agent_orchestrator.testing.scripted_replies import (  # noqa: E402
    LayeredScriptedProvider,
    decision,
    planner_reply,
    review_input,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _repair(subject: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, "decision_type": "REPAIR", "subject_key": subject,
            "rationale": "修复已提交的计划。", "reason_refs": [], "assumptions": [], "uncertainties": [],
            "alternatives": [], "replan_triggers": [], "payload": payload}


class _Round:
    """一次规划回合的包：按任务号取主题键与可见引用。"""

    def __init__(self, intent: Any) -> None:
        self.intent = intent
        self.package = intent.config["planning_package"]

    def subject(self, task_id: Any) -> str:
        return next(row["subject_key"] for row in self.package["planning_subjects"] if row["task_id"] == str(task_id))

    def visible(self, kind: str, identity: Any) -> dict[str, Any]:
        return next(row for row in self.package["visible_refs"] if row["kind"] == kind and row["id"] == str(identity))


def _chain(network: Any) -> tuple[Any, Any, Any]:
    """两步链：第一步（无输入）、第二步（读第一步的交付）、采用的做法实例。"""

    write = next(b for b in network.task_bindings if str(b.form) == "primitive" and not b.input_ports)
    follow = next(b for b in network.task_bindings if str(b.form) == "primitive" and b.input_ports)
    instance = next(item for item in network.method_instances if network.is_adopted(item.instance_id))
    return write, follow, instance


async def _settled(world: Any, intent: Any) -> dict[str, Any]:
    """执行图开着时提交在后面几轮才落定；等决定离开 COMPILED。"""

    decisions = PlanningDecisionStore(world.store)
    row = await world.until(lambda: (lambda r: r if r is not None and r["status"] != "COMPILED" else None)(
        decisions.get_planning_decision_by_attempt(intent.intent_id, 0)))
    return row


def _successor_of(round_: _Round, world: Any, old: Any, *, obligation: dict[str, Any] | None = None) -> dict[str, Any]:
    task_type = next(spec for spec in world.dispatch.require_planning_world().catalog.task_types()
                     if spec.goal_signature == old.goal_signature)
    return _repair(round_.subject(old.task_id), {
        "repair_kind": "PROPOSE_SUCCESSOR", "old_task_ref": round_.visible("task", old.task_id),
        "obligation_ref": obligation or round_.visible("obligation", old.obligation_id),
        "goal_type_ref": task_type.task_type_ref.to_json(), "bindings": {"goal": "按当前资料重做这一步。"}})


@pytest.mark.parametrize("wrong", ("successor_takes_another_duty", "rebind_names_a_stale_requirement",
                                   "cancel_withdraws_a_required_step"))
def test_a_malformed_graph_repair_is_refused_by_name_and_the_plan_stays_as_it_was(tmp_path, wrong):
    """规划器写错的三种修复：后继步骤要转走原义务、改接输入时写错原绑定的哈希、取消必需的步骤。
    都在提交前按名拒绝，计划仍是第 1 版、原做法实例仍被采用。

    （原用例直接调编译器，断言编译器的拒绝；产品上：转走义务时规划器只能写包里没给的引用，
    在准入就以 ``REF_OUTSIDE_CONTEXT`` 拒——产品计划里只有根义务一个；另两种在预览编译拒，记偏离。）"""

    async def case() -> None:
        async with enabled_world(tmp_path, key=f"h4-{wrong}", planner=chain_planner, criteria=CHAIN_CRITERIA,
                                 hold_worker=True) as world:
            await world.commit_seed()
            network = world.dispatch.network(world.mission.id)
            write, follow, instance = _chain(network)
            round_ = _Round(await world.open_planner_round())
            if wrong == "successor_takes_another_duty":
                other = {**round_.visible("obligation", write.obligation_id), "id": "obl-other"}
                body, expected = _successor_of(round_, world, write, obligation=other), "/payload/obligation_ref"
            elif wrong == "rebind_names_a_stale_requirement":
                edge = network.data_requirements[0]
                body = _repair(round_.subject(follow.task_id), {
                    "repair_kind": "REBIND_INPUT", "consumer_task_ref": round_.visible("task", follow.task_id),
                    "producer_task_ref": round_.visible("task", write.task_id), "requirement_id": edge.requirement_id,
                    "expected_requirement_hash": "0" * 64, "output_port": edge.output_port})
                expected = "rebind DATA requirement is stale or belongs to another consumer"
            else:
                root = next(row["subject_key"] for row in round_.package["planning_subjects"]
                            if row["task_id"].startswith("user-root-"))
                body = _repair(root, {"repair_kind": "CANCEL_BRANCH", "step": "write",
                                      "method_instance_ref": round_.visible("method_instance", instance.instance_id)})
                expected = "cannot withdraw a required slot"
            await world.answer(round_.intent, body)
            row = PlanningDecisionStore(world.store).get_planning_decision_by_attempt(round_.intent.intent_id, 0)
            assert row is not None and row["status"] == "REJECTED", row
            assert expected in json.dumps(row["detail"], ensure_ascii=False), row
            if wrong == "successor_takes_another_duty":
                assert row["rejection_codes"] == ["REF_OUTSIDE_CONTEXT"]
            cold = world.dispatch.network(world.mission.id)
            assert int(cold.plan_revision) == 1 and cold.is_adopted(instance.instance_id)

    asyncio.run(case())


class _HoldMissionFinal(LayeredScriptedProvider):
    """根终审的那次审阅调用停在半路（一次很慢的模型调用），别的照常。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.final_asked = asyncio.Event()
        self.final_release = asyncio.Event()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        data = review_input(request) if role_of(request) == "unknown" else None
        if data is not None and str((data.get("package") or {}).get("purpose")) == "MISSION_FINAL":
            self.final_asked.set()
            await self.final_release.wait()
        return await super().invoke(request, cancel=cancel)


def test_a_successor_that_would_rewrite_an_accepted_downstream_step_is_refused(tmp_path):
    """两步都做完、都已验收，根终审还在路上；规划器给第一步提后继步骤，这会改写第二步的输入。
    已验收的工作是不可改写的历史：提交以 ``REPAIR_NOT_ALLOWED`` 拒（要改就给它也提后继），
    计划仍是第 1 版。"""

    async def case() -> None:
        provider = _HoldMissionFinal(planner=chain_planner)
        async with enabled_world(tmp_path, key="h4-accepted-downstream", criteria=CHAIN_CRITERIA,
                                 provider=provider) as world:
            await world.commit_seed()
            await world.until(provider.final_asked.is_set, timeout=60)
            try:
                write, follow, _instance = _chain(world.dispatch.network(world.mission.id))
                assert {world.store.get_task(str(item.task_id)).status for item in (write, follow)} == {
                    TaskStatus.COMPLETED}
                round_ = _Round(await world.open_planner_round())
                await world.answer(round_.intent, _successor_of(round_, world, write))
                row = await _settled(world, round_.intent)
                assert row["status"] == "COMMIT_REJECTED" and row["rejection_codes"] == ["REPAIR_NOT_ALLOWED"], row
                assert "accepted or missing dependent requires an explicit successor" in json.dumps(row["detail"])
                assert int(world.dispatch.network(world.mission.id).plan_revision) == 1
            finally:
                provider.final_release.set()

    asyncio.run(case())


def _with_a_spare_producer(context: dict[str, Any]) -> dict[str, Any]:
    """三步：write、spare 都"准备交付"（各管一条要求），continue 读 write 的交付。"""

    request = context["request"]

    def operator(suffix: str) -> dict[str, Any]:
        return next(item for item in request["operators"] if str(item["task_type_ref"]["id"]).endswith(suffix))

    def step(local_id: str, kind: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        return {"local_id": local_id, "task_type_ref": kind["task_type_ref"], "form": "primitive",
                "arguments": arguments, "required_capabilities": list(kind["required_capabilities"]),
                "obligation_relation": "refines_parent"}

    first, second, third = [item["id"] for item in request["criterion_evidence"]]
    identity = request["new_method_identity"]
    return {
        "schema_version": 1, "method_id": identity["method_id"], "method_version": identity["method_version"],
        "goal_type_ref": request["goal_type_ref"],
        "parameter_schema_ref": request["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [],
        "steps": [step("write", operator("prepare-delivery"), {}), step("spare", operator("prepare-delivery"), {}),
                  step("continue", operator("continue-delivery"),
                       {"delivery": {"op": "output", "step": "write", "port": "delivery"}})],
        "ordering": [{"before": "write", "after": "continue"}, {"before": "spare", "after": "continue"}],
        "required_capabilities": [], "expected_effects": [],
        "composition": {
            "criterion_links": [
                {"parent_criterion_id": first, "child_step": "write", "child_criterion_id": first,
                 "evidence_requirement": "write 写出第一份文件"},
                {"parent_criterion_id": second, "child_step": "spare", "child_criterion_id": second,
                 "evidence_requirement": "spare 写出第二份文件"},
                {"parent_criterion_id": third, "child_step": "continue", "child_criterion_id": third,
                 "evidence_requirement": "continue 读上游交付，写出第三份文件"},
            ],
            "outputs": {}, "finalizer_step": "continue", "independent_review_required": True,
        },
        "basis_refs": [],
    }


def _spare_planner(request: Any) -> Any:
    package = package_of(request)
    contexts = package.get("method_proposal_contexts") or []
    if contexts and not (package.get("method_selection") or [{}])[0].get("applicable"):
        return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                        {"method_proposal": {"method": _with_a_spare_producer(contexts[0]),
                                             "rationale": "两份先写，第三份读第一份。"}}, "三步。")
    return planner_reply(request)


def test_rebinding_an_input_to_another_declared_producer_commits_and_reads_back_cold(tmp_path):
    """规划器把 continue 的输入从 write 改接到 spare（同一输出端口）：提交第 2 版；冷读的网络里
    这条数据边的生产者换成 spare、结构不变；continue 换代（输入绑定修订、派发代各 +1），旧那一代
    按修订号仍读得到；spare 一字未动。同一轮回复再送一次：一字不写。"""

    async def case() -> None:
        async with enabled_world(tmp_path, key="h4-valid-rebind", planner=_spare_planner, hold_worker=True,
                                 criteria=("file:a.md", "file:b.md", "file:NOTES.md")) as world:
            await world.commit_seed()
            network = world.dispatch.network(world.mission.id)
            instance = next(item for item in network.method_instances if network.is_adopted(item.instance_id))
            occurrence = {child.slot_key: child.occurrence_id for child in instance.child_bindings}
            spare = network.binding_for_occurrence(occurrence["spare"])
            consumer = network.binding_for_occurrence(occurrence["continue"])
            [old] = network.data_requirements
            round_ = _Round(await world.open_planner_round())
            body = _repair(round_.subject(consumer.task_id), {
                "repair_kind": "REBIND_INPUT", "consumer_task_ref": round_.visible("task", consumer.task_id),
                "producer_task_ref": round_.visible("task", spare.task_id), "requirement_id": old.requirement_id,
                "expected_requirement_hash": sha256_hex(old.to_json()), "output_port": old.output_port})
            await world.answer(round_.intent, body)
            row = await _settled(world, round_.intent)
            assert row["status"] == "COMMITTED", row

            cold = world.dispatch.network(world.mission.id)
            assert int(cold.plan_revision) == 2
            [edge] = cold.data_requirements
            assert edge.consumer_occurrence == old.consumer_occurrence
            assert edge.producer_occurrence == occurrence["spare"] and edge.schema_ref == old.schema_ref
            rewritten = cold.binding_for_task(consumer.task_id)
            assert rewritten.input_binding_revision == consumer.input_binding_revision + 1
            assert rewritten.dispatch_generation == consumer.dispatch_generation + 1
            semantics = world.dispatch.semantics()
            assert semantics.get_task_semantics(str(consumer.task_id), int(consumer.contract_revision)) == consumer
            assert cold.binding_for_task(spare.task_id) == spare

            before = (row, world.store.connection.total_changes)
            await world.answer(round_.intent, body)
            after = (PlanningDecisionStore(world.store).get_planning_decision_by_attempt(round_.intent.intent_id, 0),
                     world.store.connection.total_changes)
            assert after == before
            assert int(world.dispatch.network(world.mission.id).plan_revision) == 2

    asyncio.run(case())

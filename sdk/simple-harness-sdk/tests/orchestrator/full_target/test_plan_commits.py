# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""计划提交：一版计划要么整版提交，要么一字不写（HTN 补齐阶段 A′ 迁移，2026-10-03）。

产品上计划只经一条路提交：主循环的收集器把规划器的回复预览、编译、准入，再把**预览编出来的
那份**命令交给 ``CommitService.commit_planning_revision``。所以本文件分三块：

* **产品同形世界**（``h1i_seed.reviewed`` / ``committed`` / ``product_world``，只有模型回复是脚本）：
  预览身份闸挡下一切篡改过的编译产物（裁决①冗余原则的金丝雀）；预览与提交之间人取消了任务 →
  提交被拒、一字未写；第一次提交留下的账（回执、读集索引、一条事件、不派发）以及任务结束后
  原样重放；修复时"同做法同参数再落地"按名拒绝（09-27 真机缺陷）且下一轮能恢复。
* **直接测函数（E）**：主体/范围核对、意图哈希与重放冲突、整数图版本闸（待定③，暂留）、
  读集检查器按渠道、任务网络快照与义务开口合同、扫源码"提交路径之外没人准入需求"。
* **暂留，供他人导入**：旧的裸 ``CommitService`` 构造器，等导入方迁完再删（见文件末节）。

迁移时删掉的原用例（按分诊表第三节第 3 小节；覆盖用例换芯后在产品路径上）：
默认分层两条 →【整圈】；同命令两次 → test_h1i_commit_recovery::test_i05_exit_after_durable_commit_*；
成环与其变异 → test_h1h_p02_compiler_cycles；结构预算与其变异 → test_h1h_preview_compiler_refusal；
如实申报、成功提交三条、首修订无可保留、两步生效变异 →【整圈】与本文件 RW-P4；数据需求落在新修订 →
assurance_exec/test_subgoal_outputs；写入半段回滚、生效在事务内、失败不留 PREPARED →
taskgraph_exec/test_commit_atomicity（"生效在事务内"那一档应补进该文件，不归本文件）；运行中替换
6 条 → 应并入 taskgraph_exec/test_successor_with_taskgraph（不归本文件）；细化开口需求来自槽位 →【子目标】；
管理纪元两条 → 裁决⑤提前随 D 删；RW-P2 篡改编译产物 18 条 → C 删除，由本文件预览身份闸金丝雀覆盖；
其余变异自检（篡改产品内部函数）不再保留，改坏检验列在迁移报告里。
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from simple_harness.contracts import canonical_json

_HTN_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "htn"
if str(_HTN_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_HTN_FIXTURES))

from h1i_seed import (  # noqa: E402
    committed,
    committing_round,
    plan_reply,
    refuse_tampered_first,
    reviewed,
    root_duty,
    root_task,
)
from htn_world import Env, method, out, param, root_network, step, task_binding  # noqa: E402
from scripted_plans import approve_content_only_completion  # noqa: E402

from agent_orchestrator.contracts import Budget, ContractError  # noqa: E402
from agent_orchestrator.contracts.evidence_state import ObservationRecord  # noqa: E402
from agent_orchestrator.contracts.htn import (  # noqa: E402
    AbsenceRead,
    BudgetInheritance,
    MethodRegistryStatus,
    ObligationOpening,
    ObligationRelation,
    OrderConstraint,
    ReadItem,
    ReadItemKind,
    ReleaseCondition,
    RunningWorkPolicy,
    ScopeEpochRead,
    SemanticReadSet,
    SupportSetRead,
    TaskBindingRewrite,
    TaskForm,
)
from agent_orchestrator.contracts.obligations import Obligation, ShapeChange  # noqa: E402
from agent_orchestrator.contracts.resolution import (  # noqa: E402
    AllExpr,
    Criterion,
    CriterionExpr,
    CriterionOrigin,
    EvaluationKind,
    RequirementClass,
    RequirementsRevision,
)
from agent_orchestrator.contracts.semantic_base import TypedRef, TypedRefKind, content_hash_of  # noqa: E402
from agent_orchestrator.graph.task_network import TaskNetworkSnapshot  # noqa: E402
from agent_orchestrator.orchestrator._read_set import (  # noqa: E402
    ReadSetChannelUnknown,
    SemanticReadSetChecker,
)
from agent_orchestrator.orchestrator.commit_service import CommitService, MissionSpec  # noqa: E402
from agent_orchestrator.orchestrator.commit_service import (  # noqa: E402
    CommitService as _PlainCommitService,
)
from agent_orchestrator.orchestrator.obligation_commits import (  # noqa: E402
    DEMAND_ADMITTED,
    DEMAND_WITHDRAWN,
)
from agent_orchestrator.orchestrator.plan_commits import (  # noqa: E402
    HIERARCHICAL_SEMANTICS,
    PLAN_REVISION_COMMITTED,
    SEMANTICS_KEY,
    CommitPlanCommand,
    PlanCommitRejected,
    PlanPrincipal,
)
from agent_orchestrator.planning.htn.applicability import assess_method  # noqa: E402
from agent_orchestrator.planning.htn.compiler import compile_refinement_bundle  # noqa: E402
from agent_orchestrator.planning.htn.grounding import ground_method  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.storage.obligation_store import ObligationStore  # noqa: E402
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore  # noqa: E402
from agent_orchestrator.storage.store import Store, StoreError  # noqa: E402
from agent_orchestrator.testing.fixtures import package_of  # noqa: E402
from agent_orchestrator.testing.product_world import product_world  # noqa: E402
from agent_orchestrator.testing.scripted_replies import (  # noqa: E402
    LayeredScriptedProvider,
    decision,
    planner_reply,
    retry_same_method,
    review_input,
    review_reply,
)

HEX_OTHER = "f" * 64


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _decision_row(loop: Any, intent_id: str) -> dict[str, Any]:
    row = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(intent_id, 0)
    assert row is not None, intent_id
    return row


def _events(loop: Any, mission_id: str, kind: str) -> list[Any]:
    return [event for event in loop.store.list_events(mission_id) if event.type == kind]


# =========================================== 产品同形世界：预览身份闸（RW-P2 / 裁决①金丝雀）
def _twin(original: Any, instance_id: str = "mi-twin") -> Any:
    """同一组槽位上的第二个做法实例。"""

    return dataclasses.replace(original, instance_id=instance_id, child_bindings=tuple(
        dataclasses.replace(child, instance_id=instance_id) for child in original.child_bindings))


def _without_the_delta(command: CommitPlanCommand) -> CommitPlanCommand:
    """只剩根目标的网络：不再含这次增量新加的步骤。"""

    network = command.network
    added = {spec.task_id for spec in command.delta.occurrences}
    root = next(binding for binding in network.task_bindings if binding.task_id not in added)
    return dataclasses.replace(command, network=dataclasses.replace(
        network,
        occurrences=tuple(spec for spec in network.occurrences if spec.task_id == root.task_id),
        task_bindings=(dataclasses.replace(root, adopted_method_instance_id=None),),
        order_constraints=(), data_requirements=(), typed_edges=(), method_instances=(),
        adopted_instance_ids=(), obligation_coverage=(), required_obligations=(),
    ))


def _looped(command: CommitPlanCommand) -> CommitPlanCommand:
    first, last = command.network.occurrences[0].occurrence_id, command.network.occurrences[-1].occurrence_id
    return dataclasses.replace(command, network=dataclasses.replace(command.network, order_constraints=(
        OrderConstraint(before=first, after=last, release_condition=ReleaseCondition.ACCEPTED),
        OrderConstraint(before=last, after=first, release_condition=ReleaseCondition.ACCEPTED))))


def _form_flipped(command: CommitPlanCommand) -> CommitPlanCommand:
    leaf = command.delta.occurrences[0]
    flipped = TaskForm.COMPOUND if leaf.form is TaskForm.PRIMITIVE else TaskForm.PRIMITIVE
    return dataclasses.replace(command, delta=dataclasses.replace(
        command.delta, occurrences=(dataclasses.replace(leaf, form=flipped), *command.delta.occurrences[1:])))


def _unasked_opening(command: CommitPlanCommand) -> CommitPlanCommand:
    """增量里多出一个义务开口，没有哪个槽位要这份工作。"""

    root = next(binding for binding in command.network.task_bindings
                if binding.task_id not in {spec.task_id for spec in command.delta.occurrences})
    opening = ObligationOpening(
        obligation_id="obl-nobody-asked", parent_obligation_id=root.obligation_id,
        relation=ObligationRelation.REFINES_PARENT, requirement_refs=tuple(root.requirement_refs),
        goal_signature=root.goal_signature, budget_inheritance=BudgetInheritance.INHERIT_PARENT_FUEL_SHARE,
        fuel_share=1)
    return dataclasses.replace(command, delta=dataclasses.replace(command.delta, obligation_openings=(opening,)))


#: 篡改编译产物的各档（原 RW-P2 用例各自想撞到的提交层检查写在右边）。
TAMPERED_COMPILATIONS = {
    # 别修订的证书 → 原 PLAN_REVISION_STALE
    "certificate_for_another_revision": lambda c: dataclasses.replace(
        c, network=dataclasses.replace(c.network, plan_revision=5)),
    # 别任务的网络 → 原 STRUCTURE_INVALID
    "network_of_another_mission": lambda c: dataclasses.replace(
        c, network=dataclasses.replace(c.network, mission_id="mission-elsewhere")),
    # 网络不含增量 → 原 PLAN_REVISION_STALE / 静默丢占用 → 原 PLAN_NOT_PRESERVED
    "network_without_the_delta": _without_the_delta,
    # 合并成环 → 原 STRUCTURE_INVALID
    "network_with_a_cycle": _looped,
    # 增量采用证书外的实例 / 第二个采用实例 → 原 OR_NOT_RESOLVED
    "delta_adopts_past_the_certificate": lambda c: dataclasses.replace(c, delta=dataclasses.replace(
        c.delta, method_instances=(*c.delta.method_instances, _twin(c.network.method_instances[0])))),
    # 增量与网络的绑定不一致 → 原 STRUCTURE_INVALID（commit-ready 一族）
    "delta_disagrees_with_the_bindings": _form_flipped,
    # 没有槽位要的开口 → 原 DEMAND_NOT_ADMITTED（开口 / 重开 / 需求准入一族）
    "delta_opens_a_duty_no_slot_asks_for": _unasked_opening,
}


@pytest.mark.parametrize("tampered", sorted(TAMPERED_COMPILATIONS))
def test_a_tampered_compilation_is_refused_by_the_preview_identity_gate_before_any_write(
    tmp_path, monkeypatch, tampered
):
    """裁决①冗余原则的金丝雀（动态一半）：真实收集器把命令交给计划提交入口时，入口先收到
    一份篡改过的编译产物（①a 包装器），被预览身份闸按名拒绝、一字未写；随后真命令照常提交。
    静态一半见 :func:`test_the_plan_commit_has_one_way_in_and_it_carries_the_preview`。
    提交层对同一份字节的结构 / 采用 / 开口 / 网络归属检查因此是重复路径，原用例记"C 删除"。"""

    tamper = TAMPERED_COMPILATIONS[tampered]
    refusals = refuse_tampered_first(
        monkeypatch, lambda command, principal, kwargs: (tamper(command), principal, kwargs),
        "PREVIEW_IDENTITY_STALE")

    async def case() -> None:
        async with reviewed(tmp_path, key=f"identity-{tampered}") as ((loop, mission, _w, _r, dispatch, _p), opener, _prov):
            await loop._collect_plan_decision(
                opener, object(), mission, plan_reply(opener.config["planning_package"]), dispatch)
            assert len(refusals) == 1 and "preview compilation identity changed" in refusals[0]
            assert _decision_row(loop, opener.intent_id)["status"] == "COMMITTED"
            assert [item.revision for item in HtnStore(loop.store).list_plan_revisions(mission.id)] == [1]

    asyncio.run(case())


def test_the_plan_commit_has_one_way_in_and_it_carries_the_preview():
    """金丝雀（静态一半）：src 里只有一个地方造计划提交命令（``build_command``，增量 / 网络 /
    任务绑定 / 预算申报 / 取代占用全取自预览的编译结果，结构预算取自已装的执行图策略）；
    ``commit_plan_revision`` 只被带身份闸的入口调用；带身份闸的入口只有两个调用方，都把
    预览编出的那份交进来；预览给出的编译哈希与身份闸核的是同一个公式。"""

    import agent_orchestrator

    root = Path(agent_orchestrator.__file__).parent
    sources = {str(path.relative_to(root)): path.read_text(encoding="utf-8") for path in root.rglob("*.py")}

    def callers(needle: str) -> list[str]:
        return sorted(name for name, text in sources.items()
                      if needle in text.replace(f"def {needle}", ""))

    assert callers("CommitPlanCommand(") == ["orchestrator/hierarchical_dispatch.py"]
    assert callers("commit_plan_revision(") == ["orchestrator/planning_admission_commits.py"]
    assert callers("commit_planning_revision(") == [
        "orchestrator/hierarchical_dispatch.py", "orchestrator/planning_backend_commit.py"]
    dispatch = sources["orchestrator/hierarchical_dispatch.py"]
    build = dispatch[dispatch.index("    def build_command("):]
    build = build[:build.index("\n    def ", 10)]
    for field in ("delta=compilation.delta", "network=compilation.network",
                  "task_bindings=compilation.task_bindings", "budget_requirement=compilation.budget_requirement",
                  "compilation.superseded_occurrences", "read_installed_graph_policy"):
        assert field in build, field
    preview = sources["planning/plan_preview.py"]
    gate = sources["orchestrator/planning_admission_commits.py"]
    assert '"delta": compilation.delta.to_json(),\n                "network": _source_snapshot_payload(compilation.network)' in preview
    assert '"delta": command.delta.to_json(),\n            "network": _source_snapshot_payload(command.network)' in gate
    # 产品部署不装规划求解器，第二个调用方（求解器通道）在产品路径上不开。
    from agent_orchestrator.runtime.assembly import OrchestratorConfig

    assert OrchestratorConfig.__dataclass_fields__["planning_backend"].default is None


# ============================ 产品同形世界：封包后提交前状态变了 / 任务结束后重放（RW-P1）
def test_a_reply_whose_mission_ended_between_preview_and_commit_is_refused_and_writes_nothing(tmp_path):
    """规划器的回复在收集器里、预览之后提交之前，人取消了任务：提交在它自己的事务里重读
    计划来源，按名拒绝，计划一版也没写。（分诊表期望 ``MISSION_NOT_WRITABLE``；产品上执行图
    参与方先查到来源已变，以 ``TASKGRAPH_PLAN_SOURCE_CHANGED`` 拒，记偏离。）"""

    async def case() -> None:
        async with reviewed(tmp_path, key="plan-commit-ended-in-window") as ((loop, mission, _w, _r, dispatch, product), opener, _prov):
            original = dispatch.preview_plan_proposal

            def preview_then_cancel(proposal: Any, *, inputs: Any) -> Any:
                result = original(proposal, inputs=inputs)
                assert product.control.cancel(mission.id)["changed"] is True
                return result

            dispatch.preview_plan_proposal = preview_then_cancel  # type: ignore[method-assign]
            try:
                await loop._collect_plan_decision(
                    opener, object(), mission, plan_reply(opener.config["planning_package"]), dispatch)
            finally:
                dispatch.preview_plan_proposal = original  # type: ignore[method-assign]
            row = _decision_row(loop, opener.intent_id)
            assert row["status"] == "COMMIT_REJECTED", row
            assert "TASKGRAPH_PLAN_SOURCE_CHANGED" in json.dumps(row["detail"]), row
            assert HtnStore(loop.store).list_plan_revisions(mission.id) == ()
            assert not _events(loop, mission.id, PLAN_REVISION_COMMITTED)
            assert str(loop.store.get_mission(mission.id).status.value) == "CANCELLED"

    asyncio.run(case())


# ============================== 产品同形世界：第一次提交留下的账 + 任务结束后重放（RW-P4）
def test_the_first_commit_leaves_one_receipt_one_event_an_indexed_read_set_and_no_dispatch(tmp_path):
    """主循环真跑出第 1 版计划（提做法 → 独立审阅 → 采用），执行者被扣住。

    * 回执：命令号、基于第 0 版出第 1 版、意图哈希 / 读集哈希与事件一致、产出身份；
    * 读集按这次增量入库并建了索引；
    * 恰好一条 ``PlanRevisionCommitted``，载荷是规范字节，新增占用都待派发、没有作废任何派发；
    * 提交只登记不派发：步骤行在提交里建好，第一次尝试在提交之后才由调度建；
    * 根义务的需求在建任务时由"任务本身"准入，写明主体与依据；产品做法的步骤都细化父义务，
      这次提交不开新义务、不另准入（槽位准入在产品上走不到，记偏离）；
    * 任务结束后把同一轮回复原样再送一次：决定行、事件、库一字不变（原 RW-P1 "结束后重放"）。
    """

    async def case() -> None:
        async with committed(tmp_path, key="plan-commit-ledger") as (loop, mission, _w, _r, dispatch, product):
            opener, raw = committing_round(loop, mission.id)
            semantics = HtnStore(loop.store)
            receipt = semantics.get_commit_receipt("plan:" + opener.intent_id)
            [event] = _events(loop, mission.id, PLAN_REVISION_COMMITTED)
            payload = event.payload
            assert (receipt.base_plan_revision, receipt.new_plan_revision) == (0, 1)
            assert receipt.intent_hash == payload["intent_hash"]
            assert receipt.read_set_hash == payload["read_set_hash"] == content_hash_of(receipt.read_set.to_json())
            assert receipt.output_identity["plan_revision"] == 1
            assert receipt.output_identity["pending_dispatch"] == payload["pending_dispatch"]
            assert receipt.detail["base_graph_version"] == 1

            proposal = payload["proposal_id"]
            stored = semantics.get_read_set(mission.id, proposal)
            assert stored.to_json() == receipt.read_set.to_json()
            kinds = {item["subject_type"] for item in semantics.list_read_set_items(mission.id, proposal)}
            assert {"requirements", "manager_epoch", "task", "method"} <= kinds

            assert payload["plan_revision"] == 1 and payload["base_plan_revision"] == 0
            assert payload["base_graph_version"] == 1 and payload["delta_id"] == receipt.delta_id
            assert payload["added_occurrences"] == sorted(payload["added_occurrences"])
            assert payload["pending_dispatch"] == payload["added_occurrences"]
            assert payload["revoked_dispatch_generations"] == {}
            row = loop.store.connection.execute(
                "SELECT payload_json FROM events WHERE mission_id = ? AND type = ?",
                (mission.id, PLAN_REVISION_COMMITTED)).fetchone()
            assert row[0] == canonical_json(json.loads(row[0]))

            committed_at = next(item.seq for item in _events(loop, mission.id, "PlanningDecisionEvaluated")
                                if item.payload.get("status") == "COMMITTED")
            materialised = set(payload["materialised_tasks"])
            assert materialised == {str(spec.task_id) for spec in dispatch.network(mission.id).occurrences
                                    if str(spec.task_id) != root_task(mission.id)}
            first_attempt = min(item.seq for item in _events(loop, mission.id, "AttemptCreated"))
            assert first_attempt > committed_at

            [root_admission] = _events(loop, mission.id, DEMAND_ADMITTED)
            assert root_admission.payload["obligation_id"] == root_duty(mission.id)
            assert root_admission.payload["principal"] == product.deployment.principal.principal_id
            assert root_admission.payload["requester"] == {"kind": "mission_root"}
            assert root_admission.payload["evidence"]["requirement_refs"] == ["c-user-1"]
            assert root_admission.seq < event.seq
            assert payload["opened_obligations"] == [] and payload["admitted_demands"] == []

            assert product.control.cancel(mission.id)["changed"] is True
            before = (_decision_row(loop, opener.intent_id), len(loop.store.list_events(mission.id)),
                      loop.store.connection.total_changes)
            await loop._collect_plan_decision(opener, object(), loop.store.get_mission(mission.id), raw, dispatch)
            after = (_decision_row(loop, opener.intent_id), len(loop.store.list_events(mission.id)),
                     loop.store.connection.total_changes)
            assert after == before
            assert semantics.get_commit_receipt("plan:" + opener.intent_id) == receipt

    asyncio.run(case())


# ============================ 产品同形世界：修复时同做法同参数再落地（09-27 真机缺陷）
def test_a_repair_that_re_grounds_the_adopted_method_is_refused_by_name_and_the_next_round_recovers(tmp_path):
    """唯一一步的内容被审阅打回；修复轮里规划器发 REPLACE_METHOD，换上的还是正在用的那个做法、
    同样的参数。实例号由（目标、占用、做法、参数）派生，与库里已采用的那个相同：提交按
    ``REPAIR_NOT_ALLOWED`` 具名拒绝（不再是唯一约束冲突的内部错误）、这一轮什么都没提交；
    规划器下一轮改为同做法重试，任务完成。"""

    state = {"rejected": False, "replaced": False}

    def reviewer(request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        if str((data.get("package") or {}).get("purpose")) == "TASK_CONTENT" and not state["rejected"]:
            state["rejected"] = True
            return review_reply(data, verdict="REJECTED", grade="FAIL", reason="脚本化审阅：要点不够具体。")
        return review_reply(data)

    def planner(request: Any) -> Any:
        package = package_of(request)
        if not package.get("repair_requests"):
            return planner_reply(request)
        if not state["replaced"]:
            state["replaced"] = True
            [goal] = [item for item in package["views"]["goals"]
                      if item.get("under_repair") and item.get("adopted_method")]
            subject = next(item["subject_key"] for item in package["planning_subjects"]
                           if item["occurrence_id"] == goal["occurrence_id"])
            instance = next(item for item in package["visible_refs"] if item["kind"] == "method_instance"
                            and item["id"] == goal["adopted_method"]["method_instance_id"])
            return decision(subject, "REPAIR", {
                "repair_kind": "REPLACE_METHOD", "rejected_method_instance": instance,
                "replacement_method_ref": dict(goal["adopted_method"]["method_ref"]), "bindings": goal["params"],
            }, "换成同一个做法、同样的参数。")
        return retry_same_method(request)

    async def case() -> None:
        provider = LayeredScriptedProvider(planner=planner, reviewer=reviewer)
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md，列出三条要点。", "idempotency_key": "re-ground",
                                       "success_criteria": ["file:NOTES.md"]})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=30)
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            evaluated = [item.payload for item in _events(world.loop, mission_id, "PlanningDecisionEvaluated")]
            [refused] = [item for item in evaluated if item.get("status") == "COMMIT_REJECTED"]
            assert refused["decision_type"] == "REPAIR" and refused["rejection_codes"] == ["REPAIR_NOT_ALLOWED"]
            assert "a repair must choose a different method or different parameters" in json.dumps(refused["detail"])
            with pytest.raises(StoreError):
                HtnStore(world.store).get_commit_receipt("plan:" + refused["request_id"])
            retried = [item for item in evaluated if item.get("decision_type") == "REPAIR"
                       and item.get("status") == "COMMITTED"]
            assert len(retried) == 1
            adopted = HtnStore(world.store).list_method_instances(mission_id, state="ADOPTED")
            assert len({str(item.instance_id) for item in adopted}) == 1

    asyncio.run(case())


# ================================== 经产品组装建任务：同一义务放弃后可再要、撤回要签名（D）
def test_a_duty_given_up_may_be_asked_for_again_and_a_withdrawal_must_be_signed_and_evidenced(tmp_path):
    """根义务的需求在建任务时由"任务本身"准入。撤回再准入是第二次准入（两条事件、两个编号），
    不是第一次的重放；没签名、签名全是空白、没写依据的撤回都被拒。"""

    async def case() -> None:
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), auto=False) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md。", "idempotency_key": "duty-again",
                                       "success_criteria": ["file:NOTES.md"]})["mission_id"]
            commit, duty = world.loop.commit, root_duty(mission_id)
            duties = ObligationStore(world.store)
            principal = world.deployment.principal.principal_id
            requester = {"kind": "mission_root"}
            assert len(_events(world.loop, mission_id, DEMAND_ADMITTED)) == 1
            for signer, evidence in (("", {"reason": "r"}), ("   ", {"reason": "r"}), (principal, {})):
                with pytest.raises(ContractError):
                    commit.withdraw_obligation_demand(mission_id, duty, principal=signer,
                                                      requester=requester, evidence=evidence)
            assert duties.account(mission_id, duty).has_admitted_demand is True

            commit.withdraw_obligation_demand(mission_id, duty, principal=principal, requester=requester,
                                              evidence={"reason": "the branch that asked for it retired"})
            assert duties.account(mission_id, duty).has_admitted_demand is False
            assert len(_events(world.loop, mission_id, DEMAND_WITHDRAWN)) == 1
            commit.admit_obligation_demand(mission_id, duty, principal=principal, requester=requester,
                                           evidence={"requirement_refs": ["c-user-1"]})
            assert duties.account(mission_id, duty).has_admitted_demand is True
            admissions = _events(world.loop, mission_id, DEMAND_ADMITTED)
            assert len(admissions) == 2, "asking again is a second act, not a replay of the first"
            assert len({item.id for item in admissions}) == 2

    asyncio.run(case())


# ============================================================ 直接测函数（E）
#: 纯构造用的根目标名（E 用；也被他人导入，见文件末节）。
ROOT_TASK = "task-root"
ROOT_DUTY = "obl-root"


def _env(mission: str) -> Env:
    env = Env(mission=mission)
    env.register_type(
        "plan.goal",
        form=TaskForm.COMPOUND,
        parameters=(("subject", "string"),),
        criteria=("c-root",),
        domain="plan",
    )
    env.register_type(
        "plan.leaf",
        parameters=(("subject", "string"),),
        outputs=(("result", "plan.result"),),
        capabilities=("plan.read",),
        domain="plan",
    )
    env.register_type(
        "plan.review",
        parameters=(("subject", "string"),),
        inputs=(("result", "plan.result", True),),
        outputs=(("verdict", "plan.verdict"),),
        capabilities=("plan.read",),
        domain="plan",
    )
    return env


def _outer(method_id: str = "plan.outer"):
    return method(
        method_id,
        "plan.goal",
        parameter_schema="plan.goal.params",
        steps=(
            step(
                "leaf",
                "plan.leaf",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("plan.read",),
            ),
            step(
                "review",
                "plan.review",
                TaskForm.PRIMITIVE,
                {"subject": param("subject"), "result": out("leaf", "result")},
                capabilities=("plan.read",),
            ),
        ),
        links=(("c-root", "review", "c-reviewed"),),
        finalizer="review",
    )


def _pure_bundle() -> tuple[Any, Any, Any]:
    """纯构造的一次细化编译结果（不碰库）：根目标 + 两步做法。"""

    env = _env("mission-e")
    contract = _outer()
    assert env.admit(contract).admitted
    binding = task_binding(env, "plan.goal", task_id=ROOT_TASK, obligation=ROOT_DUTY, parameters={"subject": "alpha"})
    report = assess_method(binding, contract, env.snapshot(), env.capabilities(), registry=env.predicates)
    draft = ground_method(binding, contract, {}, report, catalog=env.catalog, schemas=env.schemas)
    bundle = compile_refinement_bundle(draft, root_network(env, binding), method=contract, catalog=env.catalog,
                                       schemas=env.schemas, registry=env.registry, requirements_revision=0)
    return env, binding, bundle


def _pure_command(**changes: Any) -> CommitPlanCommand:
    _env_, _binding, bundle = _pure_bundle()
    command = CommitPlanCommand(command_id="cmd-1", mission_id="mission-e", delta=bundle.delta, network=bundle.network,
                                task_bindings=bundle.task_bindings, base_graph_version=1, issued_by="manager-1",
                                scope_id="mission", source={"intent_id": "plan-1"})
    return dataclasses.replace(command, **changes)


def test_the_default_spec_hashes_like_the_explicit_hierarchical_one():
    """A Host that names the mode and one that omits it send the same request."""

    spec = MissionSpec(goal="g", success_criteria=("file:a.md",), tenant_id="t", idempotency_key="hash")
    explicit = dataclasses.replace(spec, orchestration_semantics_version=HIERARCHICAL_SEMANTICS)
    assert spec.to_json()[SEMANTICS_KEY] == HIERARCHICAL_SEMANTICS
    assert spec.to_json() == explicit.to_json()


def test_an_unknown_semantics_version_is_refused_by_the_spec_itself():
    with pytest.raises(ContractError):
        MissionSpec(goal="g", success_criteria=("file:a.md",), tenant_id="t", idempotency_key="bad",
                    orchestration_semantics_version="v2-maybe")


@pytest.mark.parametrize(
    ("issued_by", "scope_id", "presenter", "reason"),
    (
        ("manager-1", "mission", PlanPrincipal("manager-2", "mission", 0), "PRINCIPAL_MISMATCH"),
        ("manager-1", "team-b", PlanPrincipal("manager-1", "mission", 0), "SCOPE_NOT_AUTHORIZED"),
        ("", "mission", PlanPrincipal("manager-1", "mission", 0), "PRINCIPAL_MISMATCH"),
        ("", "mission", PlanPrincipal("manager-2", "mission", 0), "PRINCIPAL_MISMATCH"),
        ("   ", "mission", PlanPrincipal("manager-1", "mission", 0), "PRINCIPAL_MISMATCH"),
    ),
    ids=("forged-presenter", "another-scope", "unsigned", "unsigned-any-presenter", "whitespace-issuer"),
)
def test_authorship_and_scope_are_stated_by_the_command_and_never_inferred(issued_by, scope_id, presenter, reason):
    """主体 / 范围核对（产品上的真实入口版见 test_h1h_authority_matrix::test_a03_*）。"""

    command = _pure_command(issued_by=issued_by, scope_id=scope_id)
    with pytest.raises(PlanCommitRejected) as caught:
        CommitService._authorize(command, presenter)
    assert caught.value.reason == reason


def test_a_replay_answers_the_same_intent_and_refuses_another_intent_under_the_same_id():
    """同一命令号：意图相同 → 原回执；意图不同（改了动作要做的东西，不是来源元数据）→ 冲突。"""

    command = _pure_command()
    same_payload = dataclasses.replace(command, source={"intent_id": "a-later-delivery"})
    other = dataclasses.replace(command, structure_budget=dataclasses.replace(command.structure_budget, budget_version=99))
    assert same_payload.intent_hash() == command.intent_hash()
    assert other.intent_hash() != command.intent_hash()

    receipt = SimpleNamespace(intent_hash=command.intent_hash())

    class _Receipts:
        def get_commit_receipt(self, command_id: str) -> Any:
            if command_id != command.command_id:
                raise StoreError(command_id)
            return receipt

    assert CommitService._replayed_receipt(_Receipts(), command, command.intent_hash()) is receipt
    assert CommitService._replayed_receipt(_Receipts(), dataclasses.replace(command, command_id="cmd-new"),
                                           command.intent_hash()) is None
    with pytest.raises(PlanCommitRejected) as caught:
        CommitService._replayed_receipt(_Receipts(), other, other.intent_hash())
    assert caught.value.reason == "COMMAND_PAYLOAD_CONFLICT"


@pytest.mark.parametrize(
    ("current", "base", "passes"),
    ((1, 1, True), (1, 7, False), (5, 1, False), (5, 4, False), (5, 2, False), (5, 9, False), (5, 5, True)),
)
def test_the_integer_graph_version_gate_is_equality_and_never_rebases(current, base, passes):
    """整数图版本闸（待定③：真正写上还是由计划修订号取代，D 前定；src 里只读不写，先改 E 暂留）。

    等号闸：落后、超前都拒，拒绝写明两个版本号；不存在"差不多就重放"的自动变基
    （原自动变基变异）。"""

    mission = SimpleNamespace(final_report={"graph_version": current})
    command = _pure_command(base_graph_version=base)
    if passes:
        assert CommitService._check_integer_gate(mission, command) is None
        return
    with pytest.raises(PlanCommitRejected) as caught:
        CommitService._check_integer_gate(mission, command)
    assert caught.value.reason == "GRAPH_VERSION_STALE"
    assert f"version {base}" in str(caught.value) and f"current is {current}" in str(caught.value)


def test_the_integer_gate_decides_before_the_read_set():
    """两道闸都过期时，便宜的那道先答（读源码：提交的检查顺序）。"""

    source = inspect.getsource(CommitService.commit_plan_revision)
    assert source.index("self._check_integer_gate(") < source.index("self._check_read_set(")


def _criterion(identifier: str = "c-1") -> Criterion:
    return Criterion(criterion_id=identifier, revision=1, origin=CriterionOrigin.USER_EXPLICIT,
                     statement=f"criterion {identifier} is satisfied",
                     requirement_class=RequirementClass.REQUIRED_OUTCOME, evaluation_kind=EvaluationKind.SEMANTIC)


def _observe(semantics: HtnStore, mission_id: str, name: str, key: str, at: int) -> None:
    semantics.insert_observation(mission_id, ObservationRecord(
        observation_id=name, proposition_key=key, polarity=True,
        source_ref=TypedRef(kind=TypedRefKind.OBSERVATION, id=name, revision=1, content_hash="a" * 64),
        observed_at_ms=at, recorded_at_ms=at))


def test_the_read_set_checker_refuses_a_stale_item_channel_by_channel_and_cannot_be_fooled_by_a_ghost(tmp_path):
    """读集检查器，按渠道（原读集 19 条合一，直接测 ``SemanticReadSetChecker.verify``）。

    任务经产品组装建出；各渠道的状态是这条用例写进库的测试数据（直接测检查函数，不当产品世界用）。
    每个渠道：刚读的项通过 → 状态变了就"过期"；库里没有的东西是"查不了"，不是"没变"。
    要求、目标契约、做法定义三个渠道在产品上预览到提交之间没有写入方（要求修订在阶段 E、目标契约
    只由计划提交自己改、做法停用已无调用方），原 RW-P1 那 4 条因此并到这里（记偏离）。
    验收渠道只测"查不了"一档：保证通道任务上正式审阅记录只能经审阅运行时导入写入，测试不能直接
    写出一条可被验收引用的记录；而产品计划编译器的读集本来就不带验收渠道（记偏离）。"""

    async def case() -> None:
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), auto=False) as world:
            store = world.store
            mission_id = world.create({"goal": "写一份 NOTES.md。", "idempotency_key": "read-set-channels",
                                       "success_criteria": ["file:NOTES.md"]})["mission_id"]
            semantics = HtnStore(store)
            checker = SemanticReadSetChecker(store, semantics, mission_id=mission_id)

            def current_requirements() -> int:
                return int(semantics.latest_requirements_revision(mission_id).revision)

            def verdict(**channels: Any) -> Any:
                return checker.verify(SemanticReadSet(requirements_revision=current_requirements(), **channels))

            def stale(**channels: Any) -> set[str]:
                found = verdict(**channels)
                assert not found.unresolved, found
                return {item.channel for item in found.stale}

            def unresolved(**channels: Any) -> tuple[str, ...]:
                found = verdict(**channels)
                assert not found.stale, found
                return found.unresolved

            # 要求修订
            read = SemanticReadSet(requirements_revision=current_requirements())
            assert checker.verify(read).ok
            semantics.insert_requirements_revision(RequirementsRevision(
                revision_id="requirements-moved", mission_id=mission_id,  # type: ignore[arg-type]
                revision=current_requirements() + 1, criteria=(_criterion(),),
                success_expression=AllExpr((CriterionExpr("c-1"),))))
            assert {item.channel for item in checker.verify(read).stale} == {"requirements"}

            # 目标契约
            goal = checker.read_item(ReadItemKind.TASK, root_task(mission_id))
            assert stale(goal_revisions=(goal,)) == set()
            binding = semantics.task_semantics_of(mission_id, root_task(mission_id))
            semantics.put_task_semantics(mission_id, dataclasses.replace(
                binding, contract_revision=int(binding.contract_revision) + 1, contract_hash=HEX_OTHER))
            assert stale(goal_revisions=(goal,)) == {"goal"}

            # 做法：定义变了、被停用；库里没有的做法查不了
            env = _env(mission_id)
            contract = _outer()
            assert env.admit(contract).admitted
            registration = env.registry.registration(contract.method_ref())
            semantics.register_method(contract, registration)
            method_read = ReadItem(kind=ReadItemKind.METHOD, id=contract.method_id,
                                   semantic_revision=contract.method_version,
                                   content_hash=contract.method_ref().content_hash)
            assert stale(method_revisions=(method_read,)) == set()
            assert stale(method_revisions=(dataclasses.replace(method_read, content_hash=HEX_OTHER),)) == {"method"}
            semantics.set_method_registration(dataclasses.replace(registration, status=MethodRegistryStatus.SUSPENDED))
            assert stale(method_revisions=(method_read,)) == {"method"}
            assert unresolved(method_revisions=(ReadItem(kind=ReadItemKind.METHOD, id="plan.nowhere",
                                                         semantic_revision=1, content_hash=HEX_OTHER),))

            # 观测：被后来的反记录取代即过期；不存在的事实查不了
            _observe(semantics, mission_id, "obs-1", "p-alpha", 10)
            observed = checker.read_item(ReadItemKind.FACT, "obs-1")
            assert stale(observation_revisions=(observed,)) == set()
            _observe(semantics, mission_id, "obs-2", "p-alpha", 20)
            assert stale(observation_revisions=(observed,)) == {"observation"}
            assert unresolved(observation_revisions=(ReadItem(kind=ReadItemKind.FACT, id="obs-ghost",
                                                              semantic_revision=0, content_hash=HEX_OTHER),))

            # 验收：库里没有的验收查不了（有效 / 不再有效两档见文件头偏离说明）
            assert unresolved(acceptance_revisions=(ReadItem(kind=ReadItemKind.ACCEPTANCE, id="acc-ghost",
                                                             semantic_revision=1, content_hash=HEX_OTHER),))

            # 义务：改了形就过期
            duty = checker.read_item(ReadItemKind.OBLIGATION, root_duty(mission_id))
            assert stale(obligation_revisions=(duty,)) == set()
            ObligationStore(store).note_shape_change(mission_id, root_duty(mission_id),  # type: ignore[arg-type]
                                                     ShapeChange.METHOD_SWITCHED, detail="another manager switched")
            assert stale(obligation_revisions=(duty,)) == {"obligation"}

            # 授权记录：改版即过期
            store.put_approval({"request_id": "auth-1", "kind": "plan", "mission_id": mission_id,
                                "subject_key": root_task(mission_id), "state": "granted", "version": 1})
            authority = checker.read_item(ReadItemKind.AUTHORITY, "auth-1")
            assert stale(authority_revisions=(authority,)) == set()
            store.put_approval({"request_id": "auth-1", "kind": "plan", "mission_id": mission_id,
                                "subject_key": root_task(mission_id), "state": "revoked", "version": 2})
            assert stale(authority_revisions=(authority,)) == {"authority"}

            # 支持集：成员变了，即便正面成员一个没动（C29）；库里没有的支持集查不了
            positive = (TypedRef(kind=TypedRefKind.OBSERVATION, id="obs-1", revision=1, content_hash="a" * 64), True)
            support = semantics.insert_justification_set(mission_id, "support-1", subject_kind="task",
                                                         subject_id=root_task(mission_id), members=[positive],
                                                         member_revision=1)
            support_read = SupportSetRead(support_set_id="support-1", revision=1, member_digest=support.member_digest)
            assert stale(support_sets=(support_read,)) == set()
            # 读的时候集合里还多一条反记录（成员摘要不同），正面成员一样
            counter = (TypedRef(kind=TypedRefKind.OBSERVATION, id="obs-2", revision=1, content_hash="c" * 64), False)
            other = semantics.insert_justification_set(mission_id, "support-2", subject_kind="task",
                                                       subject_id=root_task(mission_id), members=[positive, counter],
                                                       member_revision=1)
            assert stale(support_sets=(dataclasses.replace(support_read, member_digest=other.member_digest),)) \
                == {"support_set"}
            assert unresolved(support_sets=(SupportSetRead(support_set_id="support-ghost", revision=1,
                                                           member_digest="a" * 64),))

            # 有效性纪元：抬了即过期
            epoch = ScopeEpochRead(scope_id="evidence", validity_epoch=semantics.epoch(mission_id, "evidence"))
            assert stale(scope_epochs=(epoch,)) == set()
            semantics.bump_epoch(mission_id, "evidence", bumped_by="a later recheck")
            assert stale(scope_epochs=(epoch,)) == {"validity_epoch"}

            # 缺席：后来有了即过期；不认识的缺席谓词查不了
            absence = AbsenceRead(predicate="no_obligation", scope_id="obl-later", range_revision=0)
            assert stale(absences=(absence,)) == set()
            ObligationStore(store).register(Obligation(obligation_id="obl-later", mission_id=mission_id,  # type: ignore[arg-type]
                                                       requirement_refs=("c-user-1",), goal_signature_id="user-goal"),
                                            recursion_fuel=1)
            assert stale(absences=(absence,)) == {"absence"}
            with pytest.raises(ReadSetChannelUnknown):
                verdict(absences=(AbsenceRead(predicate="no_unicorns", scope_id="anywhere", range_revision=0),))

            # 一个渠道过期就够，其余过期的照样报出来
            both = verdict(goal_revisions=(goal,), scope_epochs=(epoch,))
            assert {item.channel for item in both.stale} == {"goal", "validity_epoch"}
            assert "goal" in both.stale_detail() and "validity_epoch" in both.stale_detail()

    asyncio.run(case())


def test_the_snapshot_itself_refuses_two_adopted_methods_over_one_occurrence_but_holds_an_unadopted_alternative():
    """任务网络快照合同：同一占用上两个已采用的做法实例造不出来（"alternatives are OR, not AND"）；
    只记下、不采用的替代实例是合法的。"""

    _env_, _binding, bundle = _pure_bundle()
    network = bundle.network
    twin = _twin(network.method_instances[0])
    with pytest.raises(ContractError) as caught:
        dataclasses.replace(network, method_instances=(*network.method_instances, twin),
                            adopted_instance_ids=(*network.adopted_instance_ids, twin.instance_id))
    assert "alternatives are OR, not AND" in str(caught.value)
    alternative = dataclasses.replace(network, method_instances=(*network.method_instances, twin))
    assert set(alternative.adopted_instance_ids) == set(network.adopted_instance_ids)
    assert isinstance(network, TaskNetworkSnapshot) and int(network.plan_revision) == 1


def test_an_opening_states_its_authority_and_a_model_cannot_claim_the_demand_was_admitted():
    """义务开口合同：独立授权的义务必须写明授权它的来源；"已有人要这份工作"是权力声明，
    模型提议里出现 ``demand_admitted`` 字段直接拒，诚实的载荷照常解码。"""

    _env_, binding, _bundle = _pure_bundle()
    with pytest.raises(ContractError, match="authorised it"):
        ObligationOpening(obligation_id="obl-independent", parent_obligation_id=ROOT_DUTY,  # type: ignore[arg-type]
                          relation=ObligationRelation.INDEPENDENT_AUTHORIZED, requirement_refs=("req-1",),
                          goal_signature=binding.goal_signature, budget_inheritance=BudgetInheritance.SEPARATE_GRANT,
                          grant_ref="grant-1")
    payload = ObligationOpening(obligation_id="obl-child", parent_obligation_id=ROOT_DUTY,  # type: ignore[arg-type]
                                relation="refines_parent", requirement_refs=("req-1",),
                                goal_signature=binding.goal_signature,
                                budget_inheritance=BudgetInheritance.INHERIT_PARENT_FUEL_SHARE, fuel_share=2).to_json()
    assert "demand_admitted" not in payload and "has_admitted_demand" not in payload
    with pytest.raises(ContractError, match="demand_admitted"):
        ObligationOpening.from_json({**payload, "demand_admitted": True})
    assert ObligationOpening.from_json(payload).obligation_id == "obl-child"


def test_nothing_outside_the_commit_path_admits_a_demand() -> None:
    """``demand_admitted`` 让一个占用可派发，翻它是授权动作，只能经一个有审计的入口。

    **改坏检验**：在 ``event_handler.py`` 里加一句裸的 ``ledger.admit_demand(...)``，本用例变红。"""

    import agent_orchestrator

    root = Path(agent_orchestrator.__file__).parent
    allowed = {
        "contracts/obligations.py",
        "storage/obligation_store.py",
        "orchestrator/obligation_commits.py",
        "planning/htn/compiler.py",
    }
    offenders = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "admit_demand(" in path.read_text(encoding="utf-8")
        and str(path.relative_to(root)) not in allowed
    )
    assert offenders == [], (
        "a demand may only be admitted through CommitService.admit_obligation_demand; "
        f"{offenders} name the ledger primitive directly"
    )


# =====================================================================================
# 暂留，供他人导入（2026-10-03）：下面是旧的裸 ``CommitService`` 构造器，本文件已不再使用
# （``ROOT_TASK`` / ``ROOT_DUTY`` / ``_env`` / ``_outer`` 在上面 E 一节，本文件自己也用）。
# 仍在导入的：operation_completion/test_completion_spec_approval、test_scoped_content_integrity、
# test_completion_scope_compiler，test_h1h_operation_matrix / _tenant / _current_gates，
# assurance_exec/_operation_world，scripts/assurance_seams/_assured_fixture 与
# critic-format-repair-seam（patch ``CommitService`` 这个模块名）。它们迁完后整节删除。
# =====================================================================================
TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list", "run_tests")
FUEL = 8


def _spec(key: str, *, mode: str) -> MissionSpec:
    return MissionSpec(
        goal="交付一个可验收的层次计划",
        success_criteria=("file:a.md",),
        tenant_id="tenant-p23a",
        idempotency_key=key,
        allowed_tools=TOOLS,
        budget=Budget(max_tokens=200_000, max_attempts=12),
        orchestration_semantics_version=mode,
    )


@dataclasses.dataclass
class World:
    service: CommitService
    mission: Any
    env: Env
    contract: Any
    binding: Any
    draft: Any
    bundle: Any
    command: CommitPlanCommand
    principal: PlanPrincipal

    @property
    def store(self) -> Store:
        return self.service.store

    @property
    def semantics(self) -> HtnStore:
        return HtnStore(self.service.store)

    @property
    def duties(self) -> ObligationStore:
        return ObligationStore(self.service.store)

    def commit(self, command: CommitPlanCommand | None = None, principal=None):
        return self.service.commit_plan_revision(
            command or self.command, principal or self.principal
        )


def _world(tmp_path, *, mode: str = HIERARCHICAL_SEMANTICS, key: str = "p23a",
           confirm_completion: bool = True) -> World:
    """``confirm_completion=False``：留给自己发布并确认完成要求的夹具（它们测的就是确认本身）。"""
    service = CommitService(Store.open(tmp_path / "orchestrator.db"))
    mission, _ = service.create_mission(_spec(key, mode=mode))
    env = _env(mission.id)
    contract = _outer()
    receipt = env.admit(contract)
    assert receipt.admitted, receipt.problems
    binding = task_binding(
        env,
        "plan.goal",
        task_id=ROOT_TASK,
        obligation=ROOT_DUTY,
        parameters={"subject": "alpha"},
    )
    network = root_network(env, binding)
    if mode == HIERARCHICAL_SEMANTICS:
        ObligationStore(service.store).register(
            Obligation(
                obligation_id=ROOT_DUTY,  # type: ignore[arg-type]
                mission_id=mission.id,
                requirement_refs=("req-1",),
                goal_signature_id="plan.goal",
            ),
            recursion_fuel=FUEL,
        )
        HtnStore(service.store).put_task_semantics(mission.id, binding)
        HtnStore(service.store).register_method(
            contract, env.registry.registration(contract.method_ref())
        )
        if confirm_completion and type(service) is _PlainCommitService:
            approve_content_only_completion(service, mission, binding, command_id=f"approve-{key}")
    report = assess_method(
        binding, contract, env.snapshot(), env.capabilities(), registry=env.predicates
    )
    draft = ground_method(binding, contract, {}, report, catalog=env.catalog, schemas=env.schemas)
    confirmed = HtnStore(service.store).latest_requirements_revision(mission.id)
    bundle = compile_refinement_bundle(
        draft,
        network,
        method=contract,
        catalog=env.catalog,
        schemas=env.schemas,
        registry=env.registry,
        requirements_revision=0 if confirmed is None else int(confirmed.revision),
    )
    command = CommitPlanCommand(
        command_id="cmd-1",
        mission_id=mission.id,
        delta=bundle.delta,
        network=bundle.network,
        task_bindings=bundle.task_bindings,
        base_graph_version=1,
        issued_by="manager-1",
        scope_id="mission",
        source={"intent_id": "plan-1"},
    )
    return World(
        service=service,
        mission=mission,
        env=env,
        contract=contract,
        binding=binding,
        draft=draft,
        bundle=bundle,
        command=command,
        principal=PlanPrincipal("manager-1", "mission", 0),
    )


def _second_revision(
    world: World, *, superseded=(), retired=(), command_id: str = "cmd-2"
) -> CommitPlanCommand:
    """A do-nothing second revision on top of the first (暂留，供他人导入)."""

    replaced_tasks = {
        str(spec.task_id)
        for spec in world.bundle.network.occurrences
        if spec.occurrence_id in set(superseded)
    }
    rewrites, bindings = [], []
    for binding in world.bundle.network.task_bindings:
        if str(binding.task_id) not in replaced_tasks:
            bindings.append(binding)
            continue
        stored = world.semantics.task_semantics_of(world.mission.id, str(binding.task_id))
        moved = dataclasses.replace(
            stored,
            contract_revision=int(stored.contract_revision) + 1,
            dispatch_generation=int(stored.dispatch_generation) + 1,
        )
        rewrites.append(TaskBindingRewrite(content_hash_of(stored.to_json()), moved))
        bindings.append(moved)
    delta = dataclasses.replace(
        world.command.delta,
        delta_id="delta-second",
        base_plan_revision=1,
        occurrences=(),
        method_instances=(),
        data_requirements=(),
        retired_instance_ids=tuple(retired),
        referenced_occurrences=tuple(
            spec.occurrence_id for spec in world.command.delta.occurrences
        ),
        binding_rewrites=tuple(rewrites),
    )
    network = dataclasses.replace(
        world.bundle.network, plan_revision=2, task_bindings=tuple(bindings)
    )
    return dataclasses.replace(
        world.command,
        command_id=command_id,
        delta=delta,
        network=network,
        task_bindings=(),
        superseded_occurrences=tuple(superseded),
        running_work_policy=(
            RunningWorkPolicy.REQUEST_STOP_THEN_RECONCILE
            if superseded
            else RunningWorkPolicy.RETAIN_IF_BINDINGS_UNCHANGED
        ),
    )

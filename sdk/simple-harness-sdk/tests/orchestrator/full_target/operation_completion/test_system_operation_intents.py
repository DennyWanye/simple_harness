"""2026-09-29（plans/2026-09-28-system-operations）：申请单由系统按已批准效果准备。

真机第四、五局：模型写申请单反复出错，把任务 12 次尝试耗光。现在内容步骤只写文件；内容
全部通过后，系统用确认页批准的"发布什么、发到哪里"和审过的那份真实文件生成申请单，以确认人
的身份提交；之后照旧：审阅 → 物化 → 等人批准。

2026-10-03（HTN 补齐阶段 A′）迁到产品同形世界后：
* "系统按审过的文件备申请单、以确认人身份提交、理由是系统写的、不重复提交""系统自己先装操作
  运行时再提交"由代表用例 3（``product_world/test_operation.py``）与 ``test_publish_variants.py``
  第一条覆盖；"缺文件问规划器""审阅判不下重交两次后停""审阅员拒绝停给人"见
  ``test_publish_variants.py``；
* "不需要批准的操作不由系统代办"删：产品唯一的发布连接器总要人批准，未启用的连接器在建任务时
  就被拒（connector_not_enabled），这条在产品上走不到；
* 下面两条纯函数（取哪一份文件）照旧；"只有确认人能带着槽位授权提交"在产品世界里重写。
"""
from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_orchestrator.contracts.operation_intents import (
    CompletionSlotV2,
    OperationIntentSourceKind,
    OperationIntentSourceV2,
    SubmitOperationIntentV2,
)
from agent_orchestrator.contracts.semantic_base import Provenance, TypedRef, TypedRefKind, content_hash_of
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.operation_intent_store import OperationIntentStore

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from publish_world import TARGET, publishing  # noqa: E402


def test_a_continued_file_is_taken_from_the_most_downstream_step():
    """审阅 2026-09-29：续写同一文件的两步都算"当前"，被判成多个匹配而卡住。上下游从计划
    网络的数据连线读——分层任务的 Task.dependency_ids 故意留空（第二轮审阅）。"""
    from agent_orchestrator.contracts.htn import TaskForm
    from agent_orchestrator.orchestrator.system_operations import _leaves, _sources

    def artifact(ident, path):
        return SimpleNamespace(id=ident, path=path, content_hash=ident * 2, verification_status="VERIFIED")

    first, second, other = artifact("a1", "README.md"), artifact("a2", "README.md"), artifact("a3", "notes.md")
    tasks = {"t1": SimpleNamespace(id="t1", dependency_ids=(), accepted_result_id="r1", outputs=()),
             "t2": SimpleNamespace(id="t2", dependency_ids=(), accepted_result_id="r2", outputs=())}
    results = {"r1": SimpleNamespace(artifacts=("a1", "a3")), "r2": SimpleNamespace(artifacts=("a2",))}
    artifacts = {a.id: a for a in (first, second, other)}
    store = SimpleNamespace(get_task=tasks.get, get_result=results.get, get_artifact=artifacts.get)
    acceptance = lambda *items: SimpleNamespace(  # noqa: E731
        validity="CURRENT", artifact_refs=tuple(SimpleNamespace(id=a.id, content_hash=a.content_hash) for a in items))
    members = (SimpleNamespace(occurrence_id="o1", task_id="t1", form=TaskForm.PRIMITIVE),
               SimpleNamespace(occurrence_id="o2", task_id="t2", form=TaskForm.PRIMITIVE))
    edges = [SimpleNamespace(producer_occurrence="o1", consumer_occurrence="o2")]  # o2 续写 o1 的交付

    def htn():
        return SimpleNamespace(
            active_plan_revision=lambda mission_id: SimpleNamespace(revision=1),
            list_plan_memberships=lambda mission_id, revision: members,
            list_order_constraints=lambda mission_id, revision: (),
            list_data_requirements=lambda mission_id, revision: tuple(edges),
            list_acceptances=lambda mission_id: (acceptance(first, other), acceptance(second)))

    graph = htn()
    [(chosen, _, leaf)] = _sources(store, graph, "m", _leaves(store, graph, "m"), "README.md")
    assert (chosen.id, leaf.task.id) == ("a2", "t2")
    # 两步互不相连：判断不了用哪一版，交给规划器
    edges.clear()
    graph = htn()
    assert len(_sources(store, graph, "m", _leaves(store, graph, "m"), "README.md")) == 2


def test_the_declared_producer_decides_which_file_is_published():
    """2026-09-29 第 5 批：计划里声明写出 README.md 的步骤（outputs 含它）是锚：在它和它下游
    里按路径完全相同取最下游一版；并行分支里同名文件、只是文件名相同的文件都不再干扰。"""
    from agent_orchestrator.contracts.htn import TaskForm
    from agent_orchestrator.orchestrator.system_operations import _leaves, _sources

    def artifact(ident, path):
        return SimpleNamespace(id=ident, path=path, content_hash=ident * 2, verification_status="VERIFIED")

    items = [artifact("a1", "README.md"), artifact("a2", "README.md"), artifact("a3", "README.md"),
             artifact("a4", "docs/README.md")]
    tasks = {
        "t1": SimpleNamespace(id="t1", accepted_result_id="r1", outputs=("README.md",)),  # 声明产出
        "t2": SimpleNamespace(id="t2", accepted_result_id="r2", outputs=()),  # t1 下游续写
        "t3": SimpleNamespace(id="t3", accepted_result_id="r3", outputs=()),  # 并行分支
    }
    results = {"r1": SimpleNamespace(artifacts=("a1",)), "r2": SimpleNamespace(artifacts=("a2", "a4")),
               "r3": SimpleNamespace(artifacts=("a3",))}
    by_id = {a.id: a for a in items}
    store = SimpleNamespace(get_task=tasks.get, get_result=results.get, get_artifact=by_id.get)
    members = tuple(SimpleNamespace(occurrence_id=f"o{n}", task_id=f"t{n}", form=TaskForm.PRIMITIVE)
                    for n in (1, 2, 3))
    graph = SimpleNamespace(
        active_plan_revision=lambda mission_id: SimpleNamespace(revision=1),
        list_plan_memberships=lambda mission_id, revision: members,
        list_order_constraints=lambda mission_id, revision: (),
        list_data_requirements=lambda mission_id, revision: (
            SimpleNamespace(producer_occurrence="o1", consumer_occurrence="o2"),),
        list_acceptances=lambda mission_id: (SimpleNamespace(validity="CURRENT", artifact_refs=tuple(
            SimpleNamespace(id=a.id, content_hash=a.content_hash) for a in items)),))
    [(chosen, _, leaf)] = _sources(store, graph, "m", _leaves(store, graph, "m"), "README.md")
    assert (chosen.id, leaf.task.id) == ("a2", "t2")
    # 没有声明时照旧：三个同名版本里 t1→t2 与 t3 互不相连，判断不了（多个匹配交给规划器）
    tasks["t1"].outputs = ()
    assert len(_sources(store, graph, "m", _leaves(store, graph, "m"), "README.md")) > 1


def test_only_the_confirming_person_can_carry_the_slot_authority(tmp_path):
    """系统以确认页那个人的身份、带着他批准的槽位提交申请单。换一个人带同一个槽位、或者确认人
    带一个没批准过的槽位，提交入口一律按名拒绝，什么都不写。"""

    async def run() -> None:
        async with publishing(tmp_path) as case:
            await case.until_approval()
            [row] = OperationIntentStore(case.store).for_mission(case.mission_id)
            assert row["source_kind"] == "AUTHORIZED_SLOT"
            assert row["principal_id"] == case.world.deployment.principal.principal_id
            [artifact] = [a for a in case.store.list_mission_artifacts(case.mission_id) if a.path == TARGET]
            assert row["candidate_artifact_id"] == artifact.id
            [acceptance] = HtnStore(case.store).list_acceptances(case.mission_id)
            spec_hash = case.store.connection.execute(
                "SELECT spec_hash FROM operation_completion_specs WHERE mission_id=?", (case.mission_id,)).fetchone()[0]
            command = SubmitOperationIntentV2(
                schema_version=2, mission_id=case.mission_id, idempotency_key="another-submission",
                intent_source=OperationIntentSourceV2(OperationIntentSourceKind.AUTHORIZED_SLOT,
                                                      origin_receipt_id=row["origin_receipt_id"],
                                                      slot_key=row["slot_key"]),
                candidate_artifact_ref=TypedRef(TypedRefKind.ARTIFACT, artifact.id, artifact.version,
                                                artifact.content_hash, Provenance.TOOL),
                prepared_acceptance_refs=(TypedRef(TypedRefKind.ACCEPTANCE, str(acceptance.acceptance_id), 1,
                                                   content_hash_of(acceptance.to_json()), Provenance.TOOL),),
                supersedes_intent_id=None,
                completion_slot=CompletionSlotV2(spec_hash=spec_hash, effect_key="publish-weekly"))
            tenant = case.world.deployment.tenant_id
            commit = case.world.loop.commit
            before = case.store.connection.total_changes
            with pytest.raises(ValueError, match="AUTHORIZED_SLOT authority differs"):
                commit.submit_operation_intent(command, tenant_id=tenant, principal=Principal("someone-else"))
            forged = dataclasses.replace(command, intent_source=OperationIntentSourceV2(
                OperationIntentSourceKind.AUTHORIZED_SLOT, origin_receipt_id=row["origin_receipt_id"],
                slot_key="another-effect"))
            with pytest.raises(ValueError, match="AUTHORIZED_SLOT authority differs"):
                commit.submit_operation_intent(forged, tenant_id=tenant,
                                               principal=Principal(row["principal_id"]))
            assert case.store.connection.total_changes == before
            assert len(OperationIntentStore(case.store).for_mission(case.mission_id)) == 1

    asyncio.run(run())

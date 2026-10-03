"""OCC-11：内容验收消费冻结的完成范围（2026-10-03 迁到产品同形世界，HTN 补齐阶段 A′）。

内容那一步由产品主循环真跑：执行者写文件、交结果、各层检查与独立审阅都记在库里，验收由产品自己
写下。这里守三件事：

* 内容投影只取这份结果已记录的检查层和它真实的产物身份，不含还没发生的对外效果；
* 内容验收是"准备好了"的本地工作，贡献记录里没有效果、没有交付回执；同一份结果再验收一次是只读
  重放；已验收的步骤不能再派一个执行者；
* 贡献记录写入点崩溃（产品自带的崩溃点）：验收、贡献、事件一起回滚，没有游离的验收留下。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from agent_orchestrator.orchestrator.commit_service import CommitRejected, Reservation
from agent_orchestrator.orchestrator.scoped_content_review import read_task_content_projection
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.operation_completion_store import OperationCompletionStore
from agent_orchestrator.storage.store import InjectedCrash

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from publish_world import TARGET, publishing, quick_waits  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    quick_waits(monkeypatch)


def _leaf(case):
    [leaf] = [t for t in case.store.list_tasks(case.mission_id) if t.accepted_result_id]
    return leaf


def test_occ11_projection_uses_only_verified_result_layers_and_artifact_identity(tmp_path) -> None:
    async def run() -> None:
        async with publishing(tmp_path) as case:
            await case.until_approval()
            leaf = _leaf(case)
            requirements = HtnStore(case.store).latest_requirements_revision(case.mission_id)
            spec_hash = case.store.connection.execute(
                "SELECT spec_hash FROM operation_completion_specs WHERE mission_id=?", (case.mission_id,)).fetchone()[0]
            projection = read_task_content_projection(case.store, case.mission_id, leaf.id, leaf.accepted_result_id)
            assert projection.requirements == requirements
            assert projection.spec.content_hash() == spec_hash
            assert str(projection.scope.role) == "CONTENT" and not projection.scope.owned_effect_keys
            assert tuple(item.criterion_id for item in projection.criteria) == ("c-user-1",)
            [artifact] = [a for a in case.store.list_mission_artifacts(case.mission_id) if a.path == TARGET]
            assert tuple(item.id for item in projection.artifacts) == (artifact.id,)
            assert projection.artifacts[0].content_hash == artifact.content_hash
            assert {row["layer"] for row in case.store.list_verifications(leaf.accepted_result_id)} >= {
                "critic_review"}

    asyncio.run(run())


def test_occ11_preparation_acceptance_is_scoped_replay_safe_and_not_an_effect(tmp_path) -> None:
    async def run() -> None:
        async with publishing(tmp_path) as case:
            await case.until_approval()
            leaf = _leaf(case)
            commit = case.world.loop.commit
            [acceptance] = HtnStore(case.store).list_acceptances(case.mission_id)
            contribution = OperationCompletionStore(case.store).get_acceptance_scope_exact(
                case.mission_id, str(acceptance.acceptance_id))
            assert contribution is not None
            document = contribution["document"]
            assert document.content_criterion_ids == ("c-user-1",)
            assert not document.effect_keys
            assert document.outcome_binding_id is None and document.delivery_receipt_ref is None
            [artifact] = [a for a in case.store.list_mission_artifacts(case.mission_id) if a.path == TARGET]
            assert tuple(item.id for item in document.output_artifact_refs) == (artifact.id,)
            before = case.store.connection.total_changes
            with pytest.raises(CommitRejected):
                commit.create_attempt(leaf.id, role="worker", model="agent-model", prompt_version="v1",
                                      context_version="scoped-content-v1",
                                      reservation=Reservation(tokens=1_000, cost_micros=0),
                                      intent_config={"message": "must not dispatch another worker"},
                                      input_hash="b" * 64)
            again = commit.accept_result(leaf.accepted_result_id, verifier_results=())
            assert again.accepted_result_id == leaf.accepted_result_id
            assert case.store.connection.total_changes == before
            assert len(OperationCompletionStore(case.store).list_scoped_contributions(
                case.mission_id, document.completion_scope_id)) == 1

    asyncio.run(run())


def test_occ11_contribution_fault_rolls_back_acceptance_and_scope_together(tmp_path) -> None:
    async def run() -> None:
        async with publishing(tmp_path) as case:
            case.store.arm("completion_acceptance_after_contribution:operation_completion")
            # 现状：崩溃逃出本轮主循环（与迁移裁决 B4 同一类）。
            with pytest.raises(InjectedCrash, match="completion_acceptance_after_contribution"):
                await case.run_until(lambda: False, timeout=60)
            assert HtnStore(case.store).list_acceptances(case.mission_id) == ()
            assert case.store.connection.execute(
                "SELECT count(*) FROM operation_acceptance_scopes WHERE mission_id=?",
                (case.mission_id,)).fetchone()[0] == 0
            assert all(t.accepted_result_id is None for t in case.store.list_tasks(case.mission_id))
            assert not case.events("AcceptanceCommitted") and case.actions() == []

    asyncio.run(run())

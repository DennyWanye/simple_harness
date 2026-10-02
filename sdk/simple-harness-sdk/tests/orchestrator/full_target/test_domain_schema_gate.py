"""删旧平面模式第三刀第 4 步：领域档案结构升到 2，旧结构冻结的任务被主循环按名停掉。

每个领域只留一份当前档案（删了 ``planner_floor``、冲突模板、综合策略、``adapters``），
``DOMAIN_SCHEMA_VERSION`` 1→2。开发期不做旧数据兼容：库里按旧结构冻结领域档案的任务，
``domain_for`` 读不出就拒绝；主循环每一轮开头的门把它以 ``unsupported_domain_profile``
停掉一次，同库其他任务不受影响（``_cycle`` 把拒绝当"库在变"整轮跳过，门不停它就会饿死
所有任务）。

**改坏检验**：去掉 ``_refuse_unsupported_contract`` 里的领域检查 → 第一条失败。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_orchestrator.contracts import MissionStatus
from agent_orchestrator.governance.domains import (
    CODE_PROFILE,
    DOMAIN_SCHEMA_VERSION,
    DomainProfileV1,
)
from agent_orchestrator.orchestrator.commit_service import CommitRejected, MissionSpec
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.testing.fixtures import RoleScriptedProvider

TENANT = "tenant-domain-gate"


def _spec(key: str) -> MissionSpec:
    return MissionSpec(goal="写一份说明", success_criteria=("说明写清楚",), tenant_id=TENANT,
                       idempotency_key=key)


def _freeze_old_schema(loop, mission_id: str) -> None:
    """把一条任务的冻结档案改写成结构 1 的形状（测试库，直接改行）。"""
    row = loop.store.connection.execute(
        "SELECT json FROM mission_domains WHERE mission_id=?", (mission_id,)).fetchone()
    body = json.loads(row[0])
    body.update(schema=1, planner_floor=[], synthesis_default_policy=[],
                conflict_template={"policy": [], "decides_with": "code_test", "probe": None})
    with loop.store.transaction():
        loop.store.connection.execute(
            "UPDATE mission_domains SET json=? WHERE mission_id=?",
            (json.dumps(body, ensure_ascii=False, sort_keys=True), mission_id))


def test_a_mission_frozen_under_the_old_profile_schema_is_stopped_by_name(tmp_path) -> None:
    async def case():
        provider = RoleScriptedProvider({})
        async with Orchestrator(OrchestratorConfig(evidence_root=tmp_path), provider) as loop:
            old, _ = loop.commit.create_mission(_spec("old-schema"))
            current, _ = loop.commit.create_mission(_spec("current-schema"))
            _freeze_old_schema(loop, old.id)
            with pytest.raises(CommitRejected, match="invalid frozen domain"):
                loop.commit.domain_for(old.id)

            await loop._cycle()

            stopped = loop.store.get_mission(old.id)
            assert stopped.status is MissionStatus.FAILED
            failed = [e for e in loop.store.list_events(old.id) if e.type == "MissionFailed"]
            assert len(failed) == 1
            assert "unsupported_domain_profile" in json.dumps(failed[0].payload)
            assert loop.store.get_mission(current.id).status is not MissionStatus.FAILED
            assert provider.calls == 0

            await loop._cycle()  # a second round does not stop it twice
            assert len([e for e in loop.store.list_events(old.id) if e.type == "MissionFailed"]) == 1

    asyncio.run(case())


def test_the_current_profile_round_trips_and_refuses_unknown_or_missing_fields() -> None:
    assert DOMAIN_SCHEMA_VERSION == 2
    body = CODE_PROFILE.to_json()
    assert DomainProfileV1.from_json(body) == CODE_PROFILE
    assert {"planner_floor", "conflict_template", "synthesis_default_policy", "adapters"}.isdisjoint(body)
    with pytest.raises(ValueError, match="unknown"):
        DomainProfileV1.from_json({**body, "planner_floor": []})
    missing = dict(body)
    missing.pop("context_wording")
    with pytest.raises(ValueError, match="missing"):
        DomainProfileV1.from_json(missing)
    with pytest.raises(ValueError, match="unsupported frozen domain schema"):
        DomainProfileV1.from_json({**body, "schema": 1})


def test_publishing_never_decodes_another_missions_frozen_profile(tmp_path) -> None:
    """核验员 2026-10-02 复现：发布前的资料存储重叠检查曾逐个解码库里所有任务的冻结档案，
    一条旧结构的任务就让每次发布都拒绝、主循环整轮跳过（全体饿死）。

    **改坏检验**：恢复 ``_source_publish_refusal`` 里遍历 ``domain_for`` 的捷径 → 本条失败。
    """
    async def case():
        async with Orchestrator(OrchestratorConfig(evidence_root=tmp_path), RoleScriptedProvider({})) as loop:
            old, _ = loop.commit.create_mission(_spec("old-for-publish"))
            loop.commit.create_mission(_spec("new-for-publish"))
            _freeze_old_schema(loop, old.id)
            await loop._cycle()
            assert loop.store.get_mission(old.id).status is MissionStatus.FAILED
            # Whatever the deployment's roots say, the guard answers without reading frozen profiles.
            answer = loop.actions._source_publish_refusal({"connector": "file_publish"})
            assert answer is None or isinstance(answer, str)

    asyncio.run(case())


def test_the_action_judgment_has_no_document_branch_left(tmp_path) -> None:
    """核验员 2026-10-02 复现：``_decide_actions`` 还调用已删的两个文档方法，带 ``action:``
    条件的任务一到判定就 ``AttributeError``，``run()`` 整个退出。没有装分层组装时它应以
    "组装缺失"拒绝，而不是找不到方法。"""
    from agent_orchestrator.contracts import ContractError

    async def case():
        async with Orchestrator(OrchestratorConfig(evidence_root=tmp_path), RoleScriptedProvider({})) as loop:
            mission, _ = loop.commit.create_mission(_spec("action-judgment"))
            with pytest.raises(ContractError, match="hierarchical_assembly_missing"):
                await loop._decide_actions(mission, [])

    asyncio.run(case())

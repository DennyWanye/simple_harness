"""删旧平面模式第三刀第 4 步：领域档案结构升到 2，旧结构冻结的任务被主循环按名停掉。

每个领域只留一份当前档案（删了 ``planner_floor``、冲突模板、综合策略、``adapters``），
``DOMAIN_SCHEMA_VERSION`` 1→2。开发期不做旧数据兼容：库里按旧结构冻结领域档案的任务，
``domain_for`` 读不出就拒绝；主循环每一轮开头的门把它以 ``unsupported_domain_profile``
停掉一次，同库其他任务不受影响（``_cycle`` 把拒绝当"库在变"整轮跳过，门不停它就会饿死
所有任务）。

**改坏检验**：去掉 ``_refuse_unsupported_contract`` 里的领域检查 → 第一条失败。

HTN 补齐阶段 A′：任务经产品同形部署建出（:func:`product_world`）；"按旧结构冻结的档案"是把已存
档案字节改写成结构 1（裁决①b1）。"没装分层组装"由一个执行池在、部署安装没接上的进程打开同一库造出
（``tests/orchestrator/product_assembly.py``）。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from h1i_seed import run_until
from product_assembly import unstarted

from agent_orchestrator.contracts import MissionStatus
from agent_orchestrator.governance.domains import (
    CODE_PROFILE,
    DOMAIN_SCHEMA_VERSION,
    DomainProfileV1,
)
from agent_orchestrator.orchestrator.commit_service import CommitRejected
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider
from agent_orchestrator.testing.fixtures import lift_immutable_guards


def _create(world, key: str) -> str:
    return world.create({"goal": "写一份说明", "success_criteria": ["file:NOTES.md"],
                         "idempotency_key": key})["mission_id"]


def _held_planner() -> LayeredScriptedProvider:
    provider = LayeredScriptedProvider()
    provider.held.add("planner")
    return provider


def _freeze_old_schema(loop, mission_id: str) -> None:
    """把一条任务的冻结档案改写成结构 1 的形状（测试库，直接改行）。"""
    row = loop.store.connection.execute(
        "SELECT json FROM mission_domains WHERE mission_id=?", (mission_id,)).fetchone()
    body = json.loads(row[0])
    body.update(schema=1, planner_floor=[], synthesis_default_policy=[],
                conflict_template={"policy": [], "decides_with": "code_test", "probe": None})
    with loop.store.transaction():
        lift_immutable_guards(loop.store.connection, "mission_domains")
        loop.store.connection.execute(
            "UPDATE mission_domains SET json=? WHERE mission_id=?",
            (json.dumps(body, ensure_ascii=False, sort_keys=True), mission_id))


def test_a_mission_frozen_under_the_old_profile_schema_is_stopped_by_name(tmp_path) -> None:
    async def case():
        provider = _held_planner()
        try:
            async with product_world(tmp_path / "root", provider) as world:
                loop = world.loop
                old, current = _create(world, "old-schema"), _create(world, "current-schema")
                _freeze_old_schema(loop, old)
                with pytest.raises(CommitRejected, match="invalid frozen domain"):
                    loop.commit.domain_for(old)

                await loop._cycle()

                stopped = loop.store.get_mission(old)
                assert stopped.status is MissionStatus.FAILED
                failed = [e for e in loop.store.list_events(old) if e.type == "MissionFailed"]
                assert len(failed) == 1
                assert "unsupported_domain_profile" in json.dumps(failed[0].payload)
                # the other Mission plans on (its planner call is held)
                await run_until(world, provider.entered.is_set)
                assert loop.store.get_mission(current).status is not MissionStatus.FAILED

                await loop._cycle()  # a second round does not stop it twice
                assert len([e for e in loop.store.list_events(old) if e.type == "MissionFailed"]) == 1
        finally:
            provider.release.set()

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
        provider = _held_planner()
        try:
            async with product_world(tmp_path / "root", provider) as world:
                loop = world.loop
                old = _create(world, "old-for-publish")
                _create(world, "new-for-publish")
                _freeze_old_schema(loop, old)
                await loop._cycle()
                assert loop.store.get_mission(old).status is MissionStatus.FAILED
                # Whatever the deployment's roots say, the guard answers without reading frozen profiles.
                answer = loop.actions._source_publish_refusal({"connector": "file_publish"})
                assert answer is None or isinstance(answer, str)
        finally:
            provider.release.set()

    asyncio.run(case())


def test_the_action_judgment_has_no_document_branch_left(tmp_path) -> None:
    """核验员 2026-10-02 复现：``_decide_actions`` 还调用已删的两个文档方法，带 ``action:``
    条件的任务一到判定就 ``AttributeError``，``run()`` 整个退出。没有装分层组装时它应以
    "组装缺失"拒绝，而不是找不到方法。"""
    from agent_orchestrator.contracts import ContractError

    root = tmp_path / "root"

    async def create() -> str:
        async with product_world(root, LayeredScriptedProvider()) as world:
            return world.create({"goal": "写一份说明", "success_criteria": ["file:NOTES.md"],
                                 "idempotency_key": "action-judgment"})["mission_id"]

    mission_id = asyncio.run(create())

    async def without_assembly():
        async with unstarted(root, LayeredScriptedProvider(), installed=False).orchestrator as loop:
            with pytest.raises(ContractError, match="hierarchical_assembly_missing"):
                await loop._decide_actions(loop.store.get_mission(mission_id), [])

    asyncio.run(without_assembly())

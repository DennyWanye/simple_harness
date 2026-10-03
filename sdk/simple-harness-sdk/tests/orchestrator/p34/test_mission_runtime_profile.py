"""任务选定的执行池：建任务时冻结，策略缓存复用与冷重开后照旧（HTN 补齐阶段 A′ 迁到产品同形部署）。

任务一律经产品那一份部署组装建出（:func:`product_world`；建任务时初始化根、绑定执行图、走保证通道），
执行池就是部署给的原生执行池（每个上下文尺寸一个池，可选另配思考池）。守的性质：

* 公开建任务时选的执行池冻结在任务上，所有角色都走它；不选的任务不写这个字段（规格哈希不变），
  走部署默认池；同一请求重放回原回执，换了选择是冲突；冷重开后各任务的路由照旧。
* 选了部署里没有的池：建任务被拒、什么都不留；带资料的批量建任务与编排服务自己的 ``create_mission`` 两个入口
  同样校验；提交层直接重放同一幂等键但换了池 → 冲突。
* 任务选的池在重开后的部署里没有了（例如思考池被关掉）→ 路由失败关闭，不悄悄换池。

偏离分诊表：原文件另有"选定的池与部署的按角色/按任务类型路由规则冲突"两段。产品部署（Host 与
:class:`UserMissionDeployment`）没有路由规则，这条闸门只有手配 ``routing=`` 的测试世界能碰到，
随旧搭法删去（闸门函数 ``Orchestrator._validate_mission_profile`` 的冲突分支在产品上走不到，列入孤儿核查）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from product_assembly import started

from agent_orchestrator.api.facade import FacadeError
from agent_orchestrator.api.missions import MissionRequestError
from agent_orchestrator.contracts import Budget, ContractError
from agent_orchestrator.deployment.native_pools import native_profile_id
from agent_orchestrator.orchestrator.commit_service import (
    CommitRejected,
    CommitService,
    MissionConflict,
    MissionSpec,
)
from agent_orchestrator.storage.store import Store
from agent_orchestrator.testing.product_world import TENANT
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

SMALL = native_profile_id(262_144)
LARGE = native_profile_id(524_288)
THINKING = native_profile_id(262_144, thinking=True)


def _request(key: str, **extra):
    return {
        "goal": "Write NOTES.md",
        "success_criteria": ["file:NOTES.md"],
        "idempotency_key": key,
        "budget": {"max_tokens": 2_000_000, "max_attempts": 4},
        **extra,
    }


def _world(root: Path, *, thinking: bool = False):
    """产品同形部署；``thinking`` 时部署另配一组思考池（产品的"思考模式"设置，2026-09-24），
    拼法见 ``tests/orchestrator/product_assembly.py``。"""

    return started(root, LayeredScriptedProvider(),
                   thinking_provider=LayeredScriptedProvider() if thinking else None)


def _route(loop, mission_id: str, role: str, kind: str | None = None) -> str:
    return loop._router_for(mission_id).route(role=role, task_kind=kind).profile_id


def test_public_create_freezes_profile_and_keeps_omitted_hash(tmp_path):
    root = tmp_path / "root"

    async def run():
        async with _world(root) as world:
            loop, control = world.loop, world.control
            old = control.create(_request("old"))
            new = control.create(_request("new", runtime_profile_id=LARGE))
            same = control.create(_request("new", runtime_profile_id=LARGE))
            assert old["created"] and new["created"] and not same["created"]
            assert same["spec_hash"] == new["spec_hash"]
            assert "runtime_profile_id" not in loop.store.get_mission(old["mission_id"]).final_report
            assert loop.store.get_mission(new["mission_id"]).final_report["runtime_profile_id"] == LARGE
            # 不选的任务走部署默认池；选了的任务所有角色都走它。
            assert _route(loop, old["mission_id"], "planner") == SMALL
            for role, kind in (("planner", None), ("worker", "code"), ("unknown", None)):
                assert _route(loop, new["mission_id"], role, kind) == LARGE
            # 两个任务同一个策略版本：路由缓存仍按所选的池分开。
            assert loop.policy_version_of(old["mission_id"]) == loop.policy_version_of(new["mission_id"])
            with pytest.raises(FacadeError) as changed:
                control.create(_request("new"))
            assert changed.value.code == "conflict"
            assert (
                MissionSpec(goal="x", success_criteria=("file:x",), tenant_id="t", idempotency_key="k")
                .to_json().get("runtime_profile_id") is None
            )
            return old["mission_id"], new["mission_id"]

    old_id, new_id = asyncio.run(run())

    async def reopen():
        async with _world(root) as world:
            assert _route(world.loop, old_id, "worker", "code") == SMALL
            assert _route(world.loop, new_id, "planner") == LARGE

    asyncio.run(reopen())


def test_selected_mission_fails_closed_if_its_pool_is_gone_on_reopen(tmp_path):
    root = tmp_path / "root"

    async def create():
        async with _world(root, thinking=True) as world:
            created = world.control.create(_request("selected", runtime_profile_id=THINKING))
            assert _route(world.loop, created["mission_id"], "planner") == THINKING
            return created["mission_id"]

    mission_id = asyncio.run(create())

    async def without_thinking():
        async with _world(root) as world:
            with pytest.raises(ContractError, match="not configured"):
                _route(world.loop, mission_id, "planner")

    asyncio.run(without_thinking())


def test_missing_profile_is_refused_and_rolls_back(tmp_path):
    async def run():
        async with _world(tmp_path / "root") as world:
            with pytest.raises(FacadeError) as missing:
                world.control.create(_request("missing", runtime_profile_id="no-such-pool"))
            assert missing.value.code == "invalid_request"
            assert world.store.list_missions() == []

    asyncio.run(run())


def test_source_and_direct_paths_validate_selection(tmp_path):
    async def run():
        async with _world(tmp_path / "root") as world:
            loop = world.loop
            receipt = world.control.create_with_sources(
                {"mission": _request("source", runtime_profile_id=LARGE), "sources": []}
            )
            assert _route(loop, receipt["mission_id"], "unknown") == LARGE
            with pytest.raises(MissionRequestError, match="not configured"):
                loop.create_mission(tenant_id=TENANT, request=_request("direct-missing", runtime_profile_id="missing"))
            assert len(loop.store.list_missions()) == 1
            with pytest.raises(MissionConflict):
                loop.commit.create_mission(
                    MissionSpec(goal="Write NOTES.md", success_criteria=("file:NOTES.md",), tenant_id=TENANT,
                                idempotency_key="source", allowed_tools=loop.config.deployment_policy.allowed_tools,
                                budget=Budget(max_tokens=2_000_000, max_attempts=4), runtime_profile_id=SMALL)
                )

    asyncio.run(run())


def test_bare_commit_cannot_bypass_profile_binding(tmp_path):
    store = Store.open(tmp_path / "bare.db")
    try:
        commit = CommitService(store)
        with pytest.raises(CommitRejected, match="requires an Orchestrator"):
            commit.create_mission(
                MissionSpec(goal="Write NOTES.md", success_criteria=("file:NOTES.md",), tenant_id="tenant",
                            idempotency_key="bare", runtime_profile_id=LARGE)
            )
        assert store.list_missions() == []
    finally:
        store.close()

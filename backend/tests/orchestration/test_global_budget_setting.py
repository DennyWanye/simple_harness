# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""第 2 批 H11：全局预算（原计划 §18.2 Global Budget、§28 "多 Mission 配额"）接进 Host 配置。

SDK 的全局账户早就有（``commit_service.py`` 的 ``GLOBAL_ACCOUNT``），Host 构造配置却一直不传
``global_budget``，所以这一层从来没开过。现在：

* ``[orchestration] global_max_tokens`` 进设置，默认 20 亿（= 100 个默认上限的任务；单任务 2000 万、
  单步 300 万不变，2026-09-26 用户决定），下限是单任务默认上限（否则默认任务一个都建不了）；
* Host 把它作为 ``Budget(max_tokens=…)`` 传给 SDK；第一个任务建立时全局账户打开，任务账户挂在它下面；
* 任务预算超过全局上限的新任务按 SDK 现有拒绝路径如实报（§18.2 子不超父 → ``invalid_request``）；
* 状态里报全局账户的上限 / 已预留 / 已结清 / 剩余，设置页据此写说明。

**改坏检验**：``service.py`` 构造 ``OrchestratorConfig`` 时不传 ``global_budget`` → 第二、三条变红。
"""
from __future__ import annotations

import pytest
from agent_orchestrator.testing.word_counter import FixtureWordCounter
from deskpet.orchestration.service import OrchestrationRequestError, OrchestrationService
from deskpet.orchestration.settings import OrchestrationSettings, load_settings

from ._support import notes_provider, notes_request

DEFAULT_GLOBAL = 2_000_000_000


def test_the_setting_has_a_default_a_floor_and_a_ceiling():
    assert OrchestrationSettings().global_max_tokens == DEFAULT_GLOBAL
    assert load_settings(None).global_max_tokens == DEFAULT_GLOBAL
    assert load_settings({"global_max_tokens": 50_000_000}).global_max_tokens == 50_000_000
    # 比单任务默认上限还小的全局预算会让默认任务一个都建不了：抬到单任务默认上限
    assert load_settings({"global_max_tokens": 1_000}).global_max_tokens == OrchestrationSettings().default_mission_max_tokens
    assert load_settings({"global_max_tokens": "big"}).global_max_tokens == DEFAULT_GLOBAL
    assert load_settings({"global_max_tokens": True}).global_max_tokens == DEFAULT_GLOBAL
    assert load_settings({"global_max_tokens": 10**15}).global_max_tokens == 10**12


async def _service(root, principal, monkeypatch, **overrides):  # type: ignore[no-untyped-def]
    # 工作树里装的是 opt.164 轮子，没有部署验收门的读法；按 test_deployment_manifest.py 的做法当作 VALIDATED
    monkeypatch.setattr(OrchestrationService, "_taskgraph_acceptance_status", staticmethod(lambda: "VALIDATED"))
    service = OrchestrationService(
        root, OrchestrationSettings(**overrides), provider=notes_provider(), principal=principal, drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await service.start()
    return service


@pytest.mark.asyncio
async def test_the_global_pool_is_handed_to_the_sdk_and_opens_above_the_first_mission(orchestration_root, principal, monkeypatch):
    service = await _service(orchestration_root, principal, monkeypatch)
    try:
        assert service._config.global_budget is not None
        assert service._config.global_budget.max_tokens == DEFAULT_GLOBAL
        assert service._orchestrator.commit._global_budget == service._config.global_budget
        before = service.status()["global_budget"]
        assert before == {"max_tokens": DEFAULT_GLOBAL, "opened": False, "reserved_tokens": 0,
                          "settled_tokens": 0, "remaining_tokens": DEFAULT_GLOBAL}

        mission_id = service.create_mission(notes_request("global-1"))["mission_id"]
        from agent_orchestrator.orchestrator.commit_service import GLOBAL_ACCOUNT, mission_account

        ledger = service._orchestrator.commit.ledger
        with service._orchestrator.store.transaction():
            pool = ledger.account(GLOBAL_ACCOUNT)
            mine = ledger.account(mission_account(mission_id))
        assert pool.scope == "global" and pool.limits.max_tokens == DEFAULT_GLOBAL
        assert mine.parent_id == GLOBAL_ACCOUNT  # §18.2：任务账户挂在全局账户下
        after = service.status()["global_budget"]
        assert after["opened"] is True and after["max_tokens"] == DEFAULT_GLOBAL
        assert after["remaining_tokens"] == DEFAULT_GLOBAL - after["reserved_tokens"] - after["settled_tokens"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_a_mission_budget_above_the_global_pool_is_refused_on_the_existing_path(orchestration_root, principal, monkeypatch):
    service = await _service(orchestration_root, principal, monkeypatch, global_max_tokens=30_000_000)
    try:
        with pytest.raises(OrchestrationRequestError) as refused:
            service.create_mission(notes_request("global-too-big", budget={"max_tokens": 40_000_000}))
        assert refused.value.code == "invalid_request"
        assert "§18.2" in str(refused.value)  # SDK 原话：子预算超过父预算
        assert service.list_missions() == []
        # 没超的照常建
        service.create_mission(notes_request("global-fits", budget={"max_tokens": 30_000_000}))
        assert len(service.list_missions()) == 1
    finally:
        await service.close()

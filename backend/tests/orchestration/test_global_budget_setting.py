# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""第 2 批 H11：全局预算（原计划 §18.2 Global Budget、§28 "多 Mission 配额"）接进 Host 配置；
车道 P（2026-10-06 晚用户定）：全局预算改为**按月配额**。

* ``[orchestration] global_monthly_max_tokens`` 进设置（旧名 ``global_max_tokens`` 直接删、不认），
  默认 20 亿（= 100 个默认上限的任务；单任务 2000 万、单步 1000 万（2026-10-10 用户定），2026-09-26 用户决定），
  下限是单任务默认上限（否则默认任务一个都建不了）；
* Host 把它作为 ``Budget(max_tokens=…)`` 传给 SDK；SDK 每个自然月一个全局总账
  ``budget:global:YYYY-MM``，当月第一个任务建立时打开，任务账户挂在建立当月的总账下；
* 任务预算超过月配额的新任务按 SDK 现有拒绝路径如实报（§18.2 子不超父 → ``invalid_request``）；
* 状态里报本月总账的月份、上限、已用、预留中、剩余，设置页据此写"本月全局预算"。

运行口径：Host 装的 SDK 轮子（opt.165）还没有按月总账，本文件跑时把工作树
``sdk/simple-harness-sdk/src`` 放在 PYTHONPATH 最前（见车道 P 记录）。

**改坏检验**：``service.py`` 构造 ``OrchestratorConfig`` 时不传 ``global_budget`` → 第二、三条变红。
"""
from __future__ import annotations

import pytest
from agent_orchestrator.testing.word_counter import FixtureWordCounter
from deskpet.orchestration.service import OrchestrationRequestError, OrchestrationService
from deskpet.orchestration.settings import OrchestrationSettings, load_settings

from agent_orchestrator.orchestrator.commit_service import global_account_id, mission_account

from ._support import notes_provider, notes_request

DEFAULT_GLOBAL = 2_000_000_000


def test_the_setting_has_a_default_a_floor_and_a_ceiling():
    assert OrchestrationSettings().global_monthly_max_tokens == DEFAULT_GLOBAL
    assert load_settings(None).global_monthly_max_tokens == DEFAULT_GLOBAL
    assert load_settings({"global_monthly_max_tokens": 50_000_000}).global_monthly_max_tokens == 50_000_000
    # 比单任务默认上限还小的月配额会让默认任务一个都建不了：抬到单任务默认上限
    assert load_settings({"global_monthly_max_tokens": 1_000}).global_monthly_max_tokens == OrchestrationSettings().default_mission_max_tokens
    assert load_settings({"global_monthly_max_tokens": "big"}).global_monthly_max_tokens == DEFAULT_GLOBAL
    assert load_settings({"global_monthly_max_tokens": True}).global_monthly_max_tokens == DEFAULT_GLOBAL
    assert load_settings({"global_monthly_max_tokens": 10**15}).global_monthly_max_tokens == 10**12
    # 旧名直接删：开发期不兼容旧配置，写旧名等于没写（仍是默认月配额）
    assert not hasattr(OrchestrationSettings(), "global_max_tokens")
    assert load_settings({"global_max_tokens": 50_000_000}).global_monthly_max_tokens == DEFAULT_GLOBAL


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
        month = global_account_id(service._orchestrator.store.now)
        before = service.status()["global_budget"]
        assert before == {"month": month.removeprefix("budget:global:"), "max_tokens": DEFAULT_GLOBAL,
                          "opened": False, "used_tokens": 0, "reserved_tokens": 0,
                          "remaining_tokens": DEFAULT_GLOBAL}

        mission_id = service.create_mission(notes_request("global-1"))["mission_id"]
        ledger = service._orchestrator.commit.ledger
        with service._orchestrator.store.transaction():
            pool = ledger.account(month)
            mine = ledger.account(mission_account(mission_id))
        assert pool.scope == "global" and pool.limits.max_tokens == DEFAULT_GLOBAL
        assert mine.parent_id == month  # §18.2：任务账户挂在建立当月的全局总账下
        after = service.status()["global_budget"]
        assert after["opened"] is True and after["max_tokens"] == DEFAULT_GLOBAL
        assert after["month"] == before["month"]
        assert after["remaining_tokens"] == DEFAULT_GLOBAL - after["reserved_tokens"] - after["used_tokens"]

        # 到了下个月：本月总账还没打开，额度是满的（上个月的用量留在上个月）
        store = service._orchestrator.store
        later = store.now + 40 * 86_400
        store._clock = lambda: later  # 库的时钟注入点
        next_month = service.status()["global_budget"]
        assert next_month["month"] == global_account_id(later).removeprefix("budget:global:") != after["month"]
        assert next_month["opened"] is False and next_month["used_tokens"] == 0
        assert next_month["remaining_tokens"] == DEFAULT_GLOBAL
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_a_mission_budget_above_the_global_pool_is_refused_on_the_existing_path(orchestration_root, principal, monkeypatch):
    service = await _service(orchestration_root, principal, monkeypatch, global_monthly_max_tokens=30_000_000)
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


@pytest.mark.asyncio
async def test_a_raised_monthly_quota_reaches_the_open_pool_on_restart(orchestration_root, principal, monkeypatch):
    """夜间 N3-24：调大月配额后重启编排服务（设置页提示"改 config.toml 并重启"；重启 = 新建服务），
    不建新任务，本月已打开的总账上限就是新值，状态里报的上限与账上一致。"""
    service = await _service(orchestration_root, principal, monkeypatch, global_monthly_max_tokens=30_000_000)
    try:
        service.create_mission(notes_request("global-restart", budget={"max_tokens": 20_000_000}))
        assert service.status()["global_budget"]["max_tokens"] == 30_000_000
    finally:
        await service.close()
    service = await _service(orchestration_root, principal, monkeypatch, global_monthly_max_tokens=90_000_000)
    try:
        month = global_account_id(service._orchestrator.store.now)
        with service._orchestrator.store.transaction():
            pool = service._orchestrator.commit.ledger.account(month)
        assert pool.limits.max_tokens == 90_000_000
        status = service.status()["global_budget"]
        assert status["opened"] is True and status["max_tokens"] == pool.limits.max_tokens
        assert len(service.list_missions()) == 1  # 没有新任务建立
    finally:
        await service.close()

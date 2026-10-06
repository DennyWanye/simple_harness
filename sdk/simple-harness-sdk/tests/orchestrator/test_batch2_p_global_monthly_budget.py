# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
"""第 2 批车道 P：全局预算改为按月配额（2026-10-06 晚用户定）。

原计划 §18.2 Global Budget 在 SDK 里是一个累计不归零的账户 ``budget:global``。现在：

* 每个自然月一个全局总账，编号 ``budget:global:YYYY-MM``（本机本地时间的月份，时刻取 store 时钟），
  只由 :func:`global_account_id` 算出；
* 任务建立时挂到"建立当月"的全局账户（没有就开），这个任务的全部用量都记在建立当月
  （**按任务建立月份计**）；
* 全局账户的上限 = 部署配置的月配额，配置改了，服务启动时（账本里已有的全部全局账户，不分月份）
  与新任务建立时随之改（夜间 N3-24：调大配额重启即生效，在跑的任务不用等新任务建立）；
* "是不是全局账户"只有一种判法（:func:`is_global_account`），全局预算用完的停止仍写 scope=global。
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "full_target"))

from _unknown_outcome_world import CONFIG, FaultyProvider, create, settle  # noqa: E402

from agent_orchestrator.contracts import Budget  # noqa: E402
from agent_orchestrator.governance.budgets import BudgetExhausted  # noqa: E402
from agent_orchestrator.orchestrator.commit_service import (  # noqa: E402
    global_account_id,
    is_global_account,
    mission_account,
)
from agent_orchestrator.testing.product_world import product_world  # noqa: E402


def _local(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> float:
    """本机本地时间的某一刻（与测试机所在时区无关）。"""
    return time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))


def _reserve(world: Any, mission_id: str, subject: str, tokens: int) -> None:
    with world.store.transaction():
        world.loop.commit.ledger.reserve(account_id=mission_account(mission_id), subject_id=subject, tokens=tokens,
                                         counts_attempt=False, mission_id=mission_id)


def _parent(world: Any, mission_id: str) -> str | None:
    with world.store.transaction():
        return world.loop.commit.ledger.account(mission_account(mission_id)).parent_id


def _world(root: Path, quota: int) -> Any:
    """产品同形世界（建任务要走部署组装与保证通道）；不驱动循环，只建任务、看账。"""
    return product_world(root, FaultyProvider({}), **CONFIG, global_budget=Budget(max_tokens=quota))


def test_the_account_id_is_the_local_month_and_only_global_accounts_are_global() -> None:
    assert global_account_id(_local(2026, 10, 6)) == "budget:global:2026-10"
    assert global_account_id(_local(2026, 10, 31, 23, 59)) == "budget:global:2026-10"
    assert global_account_id(_local(2026, 11, 1, 0, 1)) == "budget:global:2026-11"
    assert global_account_id(_local(2027, 1, 1)) == "budget:global:2027-01"
    assert is_global_account("budget:global:2026-10")
    assert not is_global_account(mission_account("mission-0123456789abcdef"))
    assert not is_global_account("budget:task-1")


def test_missions_of_one_month_share_one_pool_and_a_new_month_starts_full(tmp_path: Path) -> None:
    async def case() -> None:
        async with _world(tmp_path / "root", 12_000_000) as world:
            clock = [_local(2026, 10, 20)]
            world.store._clock = lambda: clock[0]  # 库的时钟注入点
            commit = world.loop.commit
            october = "budget:global:2026-10"
            first = create(world, "p-oct-1", max_tokens=8_000_000)
            second = create(world, "p-oct-2", max_tokens=8_000_000)
            assert _parent(world, first) == october and _parent(world, second) == october
            # ① 同月两个任务共用一个总账：第一个预留 800 万后，第二个再要 800 万超出 1200 万的月配额，被拒
            _reserve(world, first, "p-oct-1:a", 8_000_000)
            with pytest.raises(BudgetExhausted) as refused:
                _reserve(world, second, "p-oct-2:a", 8_000_000)
            assert refused.value.account_id == october and is_global_account(refused.value.account_id)
            pool = commit.global_account()
            assert pool is not None and pool.account_id == october and pool.remaining_tokens() == 4_000_000

            # ② 跨月：新任务挂到 11 月的新账户，额度是满的
            clock[0] = _local(2026, 11, 1, 0, 5)
            assert commit.global_account() is None  # 11 月还没有任务建立过
            third = create(world, "p-nov-1", max_tokens=8_000_000)
            november = commit.global_account()
            assert november is not None and november.account_id == "budget:global:2026-11"
            assert november.remaining_tokens() == 12_000_000
            assert _parent(world, third) == "budget:global:2026-11"
            _reserve(world, third, "p-nov-1:a", 8_000_000)
            # 按任务建立月份计：10 月建立的任务到 11 月接着用，仍记在 10 月的总账上（10 月只剩 400 万）
            with pytest.raises(BudgetExhausted) as late:
                _reserve(world, second, "p-oct-2:b", 5_000_000)
            assert late.value.account_id == october
            _reserve(world, second, "p-oct-2:c", 4_000_000)
            with world.store.transaction():
                assert commit.ledger.account(october).reserved_tokens == 12_000_000
                assert commit.ledger.account("budget:global:2026-11").reserved_tokens == 8_000_000
                # 夜间（N3c 发现）：费用报告的全局一栏读这个任务建立当月的总账，不是库里第一个全局账户
                assert commit.ledger.costs_report(second)["global"]["account_id"] == october
                assert commit.ledger.costs_report(third)["global"]["account_id"] == "budget:global:2026-11"

    asyncio.run(case())


def test_a_changed_monthly_quota_reaches_every_global_pool_on_start(tmp_path: Path) -> None:
    """夜间 N3-24：小配额下任务把本月总账用满 → 调大配额重启（同一个库）→ 不建新任务，账本里已有的
    全局账户（本月和上月）上限都已是新值，原任务能接着花；配额调小同样在启动时跟上（多算方向）。

    改坏检验：删掉 ``CommitService.__init__`` 里启动同步那一行，本条变红。"""

    september, october = _local(2026, 9, 15), _local(2026, 10, 20)

    async def case() -> None:
        async with _world(tmp_path / "root", 12_000_000) as world:
            clock = [september]
            world.store._clock = lambda: clock[0]
            create(world, "p-q-sep", max_tokens=8_000_000)
            clock[0] = october
            first = create(world, "p-q-1", max_tokens=8_000_000)
            second = create(world, "p-q-2", max_tokens=8_000_000)
            _reserve(world, first, "p-q-1:a", 8_000_000)
            _reserve(world, second, "p-q-2:a", 4_000_000)
            with pytest.raises(BudgetExhausted) as refused:  # 本月总账用满：原任务被旧上限挡住
                _reserve(world, second, "p-q-2:b", 3_000_000)
            assert refused.value.account_id == "budget:global:2026-10"
        async with _world(tmp_path / "root", 30_000_000) as world:  # 用户调大月配额后重启
            world.store._clock = lambda: october
            commit = world.loop.commit
            pool = commit.global_account()
            assert pool is not None and pool.account_id == "budget:global:2026-10"
            assert pool.limits.max_tokens == 30_000_000  # 没有新任务建立，启动时已跟上
            with world.store.transaction():
                assert commit.ledger.account("budget:global:2026-09").limits.max_tokens == 30_000_000
                assert pool.reserved_tokens == commit.ledger.account("budget:global:2026-10").reserved_tokens == 12_000_000
            _reserve(world, second, "p-q-2:b", 3_000_000)  # 原任务接着花（在它自己 800 万的任务上限内）
            assert commit.global_account().remaining_tokens() == 30_000_000 - 15_000_000
            assert world.store.connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 3  # 没建新任务
        async with _world(tmp_path / "root", 12_000_000) as world:  # 调小也在启动时跟上：余额为负，后续预留被拒
            world.store._clock = lambda: october
            pool = world.loop.commit.global_account()
            assert pool is not None and pool.limits.max_tokens == 12_000_000
            assert pool.remaining_tokens() == 12_000_000 - 15_000_000
            with pytest.raises(BudgetExhausted):
                _reserve(world, second, "p-q-2:c", 1)

    asyncio.run(case())


def test_a_new_mission_still_brings_this_months_pool_to_the_configured_quota(tmp_path: Path) -> None:
    """建任务那处同步保留（与启动同步调同一个小函数）：账上上限与配置不一致时，新任务建立把它拉回。"""

    at = _local(2026, 10, 20)

    async def case() -> None:
        async with _world(tmp_path / "root", 30_000_000) as world:
            world.store._clock = lambda: at
            create(world, "p-n-1", max_tokens=8_000_000)
            with world.store.transaction():  # 模拟账上上限与配置不一致
                world.loop.commit.ledger.set_limits("budget:global:2026-10", Budget(max_tokens=12_000_000))
            create(world, "p-n-2", max_tokens=8_000_000)
            assert world.loop.commit.global_account().limits.max_tokens == 30_000_000

    asyncio.run(case())


@pytest.fixture
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_a_spent_monthly_pool_stops_a_new_mission_with_the_global_scope(tmp_path: Path, _quick: None) -> None:
    """③ 本月总账已经用到只剩 1000 token（别的任务用掉的），新任务第一轮规划就停，停止详情写 scope=global。"""

    quota = 10_000_000

    async def case() -> dict[str, Any]:
        async with _world(tmp_path / "root", quota) as world:
            store = world.store
            month = global_account_id(store.now)
            with store.transaction():
                world.loop.commit.ledger.open_account(account_id=month, scope="global", parent_id=None,
                                                      mission_id="global", limits=Budget(max_tokens=quota))
                store.connection.execute("UPDATE budget_accounts SET settled_tokens=? WHERE account_id=?",
                                         (quota - 1_000, month))
            mission_id = create(world, "p-global-spent", max_tokens=8_000_000)
            assert await settle(world, mission_id, seconds=15)
            final = store.get_mission(mission_id)
            return {"month": month, "stop": final.stop_reason, "report": dict(final.final_report or {})}

    outcome = asyncio.run(case())
    assert outcome["stop"] == "budget_exhausted", outcome["report"]
    detail = outcome["report"]["detail"]
    assert detail["scope"] == "global", detail
    assert detail["account"] == outcome["month"]

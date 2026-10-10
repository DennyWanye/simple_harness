"""2026-10-11 用户决定 A：固定单步额度，不再平分任务池。原来取"固定额度"与"池子平分份额"中较小
的，多步任务里每步实际只有 500 万，用户定的 1000 万不生效。固定额度下，任务总额由账户链在每次
预留时挡住，守恒式改为"每个叶子的上限不超过池子"，不再把各行上限相加。"""
from __future__ import annotations

from types import SimpleNamespace

from agent_orchestrator.orchestrator.occurrence_tasks import Materialisation


def _leaves(cap: int, n: int):
    return tuple(SimpleNamespace(task=SimpleNamespace(budget=SimpleNamespace(max_tokens=cap))) for _ in range(n))


def test_fixed_allowance_leaves_may_sum_past_the_pool_but_none_exceeds_it():
    three = Materialisation(tasks=_leaves(10_000_000, 3), pool_tokens=20_000_000, share_tokens=10_000_000,
                            fixed_allowance=10_000_000, available_tokens=20_000_000, committed_tokens=0, funded_now=3)
    equation = three.conservation()
    assert equation["mode"] == "fixed_allowance" and equation["holds"] is True
    assert equation["granted_tokens"] == 30_000_000  # reported as it is; the account chain conserves the pool
    too_big = Materialisation(tasks=_leaves(25_000_000, 1), pool_tokens=20_000_000, share_tokens=20_000_000,
                              fixed_allowance=25_000_000, committed_tokens=0, funded_now=1)
    assert too_big.conservation()["holds"] is False


def test_even_share_still_sums_the_leaves_against_the_pool():
    three = Materialisation(tasks=_leaves(10_000_000, 3), pool_tokens=20_000_000, share_tokens=6_666_666,
                            committed_tokens=0, funded_now=3)
    equation = three.conservation()
    assert equation["mode"] == "even_share" and equation["holds"] is False

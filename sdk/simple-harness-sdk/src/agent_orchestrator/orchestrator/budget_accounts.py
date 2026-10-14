"""Budget account ids and the per-Attempt reservation (shared by the commit service and its readers)."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Reservation:
    tokens: int
    tool_calls: int = 0  # step 6 (D6-8): the Attempt's tool-call cap, reserved up front
    search_calls: int = 0  # 推后第 3 批 H08：这次尝试预留的检索次数


def mission_account(mission_id: str) -> str:
    return f"budget:{mission_id}"


#: step 6 (D6-1): §18.2 "Global Budget" above every Mission.  第 2 批车道 P（2026-10-06 用户定）：
#: 全局预算是按月配额——每个自然月一个全局总账 ``budget:global:YYYY-MM``，月初自然是一个新的空账。
GLOBAL_ACCOUNT_PREFIX = "budget:global:"


def global_account_id(at: float) -> str:
    """The Global Budget account of the calendar month ``at`` falls in (this machine's local
    time; ``at`` is a store-clock instant).  The one place that spells the account id."""

    return GLOBAL_ACCOUNT_PREFIX + time.strftime("%Y-%m", time.localtime(at))


def is_global_account(account_id: str) -> bool:
    """Whether ``account_id`` is a (monthly) Global Budget account — the one test for it."""

    return account_id.startswith(GLOBAL_ACCOUNT_PREFIX)


def task_account(task_id: str) -> str:
    return f"budget:{task_id}"

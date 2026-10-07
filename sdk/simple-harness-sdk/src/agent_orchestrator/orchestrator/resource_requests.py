# SPDX-License-Identifier: Apache-2.0
"""执行者申请更多资源的通道（推后第 3 批 H10；原文 §12.3"资源申请"、§15"申请更多预算"、§24 第 9 步）。

一条路，三步：

1. **提案**：结果信封带 ``resource_request``（只认 ``tool_calls``：这是 Harness 按尝试发放、执行者会用完的
   那一份；token 预留本来按需增长）。只能和 blocked / failure / no_progress 一起交。
2. **核额度与上限**：Harness 只回答两个数——要的数超不超上限（一次最多再要一份基础额度），账户链上
   还剩不剩。核的结果与申请原样写进 ``ResourceRequested``，和这次结果的 ``OutcomeRecorded`` 同一事务。
3. **交给判断方**：这一步失败本来就生成修复请求交规划器；请求明细带上申请与核的结果。规划器选"原样
   重试"就是批准：建下一次尝试时再核一次额度，把发放冻进派发配置并记 ``ResourceGranted``；额度这时
   不够就记 ``ResourceGrantLapsed``，按基础额度开工。不选重试就不发放。

Harness 不判"该不该给"，不按理由的文字分类，不自动批准。
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..contracts.models import ContractError

FIELD = "resource_request"
REQUESTED = "ResourceRequested"
GRANTED = "ResourceGranted"
LAPSED = "ResourceGrantLapsed"
DIMENSIONS = frozenset({"tool_calls"})
REASON_LIMIT = 500
#: 给规划器看的一句话：批准的方式就是原样重试。
IF_RETRIED = "若规划器选原样重试（RETRY_SAME_METHOD），下一次尝试的工具上限 = 基础额度 + 申请数（届时再核一次额度）。"


class GrantUnverified(ContractError):
    """派发配置里冻结的发放找不到对应的、核过的申请。"""


@dataclass(frozen=True, slots=True)
class ResourceRequest:
    dimension: str
    amount: int
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {"dimension": self.dimension, "amount": self.amount, "reason": self.reason}

    @classmethod
    def from_json(cls, value: object) -> ResourceRequest:
        if not isinstance(value, Mapping):
            raise ContractError("resource_request must be an object")
        unknown = set(value) - {"dimension", "amount", "reason"}
        if unknown:
            raise ContractError(f"resource_request has unknown fields: {sorted(unknown)}")
        dimension, amount, reason = value.get("dimension"), value.get("amount"), value.get("reason")
        if dimension not in DIMENSIONS:
            raise ContractError(f"resource_request.dimension must be one of {sorted(DIMENSIONS)}")
        if type(amount) is not int or amount < 1:
            raise ContractError("resource_request.amount must be a positive integer")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > REASON_LIMIT:
            raise ContractError(f"resource_request.reason must be non-blank text of at most {REASON_LIMIT} chars")
        return cls(dimension=str(dimension), amount=amount, reason=reason)


def pop_request(raw: dict[str, Any]) -> ResourceRequest | None:
    """从结果块上取下申请并校验形状（结果合同本身拒绝未知字段，所以先取下）。"""

    if FIELD not in raw:
        return None
    return ResourceRequest.from_json(raw.pop(FIELD))


def quota_check(request: ResourceRequest, *, base_cap: int, room: int | None) -> dict[str, Any]:
    """只核数：上限 = 基础额度（一次最多再要一份）；``room`` = 账户链上还没花掉的工具次数（不限为 None），
    要放得下"基础额度 + 申请数"。"""

    fits = request.amount <= base_cap and (room is None or base_cap + request.amount <= room)
    return {"base_cap": int(base_cap), "ceiling": int(base_cap), "available": room, "fits": bool(fits)}


def request_for_attempt(events: Iterable[Any], attempt_id: str) -> Any | None:
    """这次尝试的申请事件（每次尝试至多一条）。"""

    return next((event for event in events if event.type == REQUESTED and event.attempt_id == attempt_id), None)


def planner_detail(events: Iterable[Any], attempt_id: str | None) -> dict[str, Any]:
    """修复请求明细里给规划器看的那一段；这次尝试没申请就是空。"""

    if not attempt_id:
        return {}
    event = request_for_attempt(events, attempt_id)
    if event is None:
        return {}
    return {"resource_request": {"request": dict(event.payload["request"]), "check": dict(event.payload["check"]),
                                 "if_retried": IF_RETRIED}}


def verified_grant_amount(events: Iterable[Any], *, task_id: str, grant: Mapping[str, Any], base_cap: int) -> int:
    """派发核对：冻结的发放必须对得上库里同一步、同维度、同数量、核过、不超上限的申请。"""

    failed = grant.get("failed_attempt_id")
    event = None if not isinstance(failed, str) else request_for_attempt(events, failed)
    if event is None or event.task_id != task_id:
        raise GrantUnverified("TASKGRAPH_EXECUTION_RESOURCE_GRANT_UNVERIFIED: no matching request")
    request = dict(event.payload.get("request") or {})
    check = dict(event.payload.get("check") or {})
    amount = grant.get("amount")
    if (request.get("dimension") != grant.get("dimension") or type(amount) is not int
            or request.get("amount") != amount or check.get("fits") is not True or amount > base_cap):
        raise GrantUnverified("TASKGRAPH_EXECUTION_RESOURCE_GRANT_UNVERIFIED: request and grant differ")
    return amount


__all__ = (
    "DIMENSIONS",
    "FIELD",
    "GRANTED",
    "IF_RETRIED",
    "LAPSED",
    "REQUESTED",
    "GrantUnverified",
    "ResourceRequest",
    "planner_detail",
    "pop_request",
    "quota_check",
    "request_for_attempt",
    "verified_grant_amount",
)

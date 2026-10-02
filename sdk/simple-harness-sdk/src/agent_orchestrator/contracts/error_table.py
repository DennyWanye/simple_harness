# SPDX-License-Identifier: Apache-2.0
"""错误码表（TaskGraph 原计划 §12；HTN 补齐阶段 A 第 4 条）：跨边界的拒绝码各归一类。

只管秩序，不管语义：一个码属于哪一类、同一阶段几个码按什么先后报、在哪里停、算不算
"格式错"而消耗同一请求的那一次格式重问。给规划器的仍只是事实与字段路径——不按类型码
自动改计划（CLAUDE.md 核心思想）。

这一版只收跨边界的码：规划器反馈里的 :class:`PlanningDecisionRejectionCode`，以及执行图
对界面/操作员报出的 :class:`TaskGraphBoundaryCode`。内部守卫码（``StoreError`` 里的
``TASKGRAPH_*`` 等）以后再归。新加的跨边界码必须在这里登记，否则 :func:`classify` 拒绝。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from types import MappingProxyType

from .models import ContractError
from .planning_decisions import PlanningDecisionRejectionCode as P


class ErrorCategory(IntEnum):
    """§12 的类别；数值就是同一检查阶段里的报告先后（小的先报）。"""

    IDENTITY_PROTOCOL = 1
    REQUEST_STALE = 2
    SOURCE_UNAVAILABLE = 3
    AUTHORIZATION = 4
    METHOD_PARAMETER_PRECONDITION = 5
    STRUCTURE_DATA_COVERAGE = 6
    NEEDS_CONVERGENCE = 7
    BUDGET = 8
    COMMIT_CONFLICT = 9


class StopScope(StrEnum):
    """出了这个码以后停在哪里。"""

    #: 只拒这一份决定；规划器可以在同一轮或下一轮换一份。
    DECISION = "DECISION"
    #: 这次请求作废，按新请求重来（输入或授权已变）。
    REQUEST = "REQUEST"
    #: 受影响任务停止新派发，等修复（图损坏）。
    MISSION_DISPATCH = "MISSION_DISPATCH"
    #: 只出现在诊断里，不改运行。
    DIAGNOSTIC = "DIAGNOSTIC"


class TaskGraphBoundaryCode(StrEnum):
    """§12 左栏里执行图对界面/操作员报出的码。"""

    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    GRAPH_INTEGRITY = "GRAPH_INTEGRITY"
    WAITING_DATA = "WAITING_DATA"
    VALIDITY_RECHECK_PENDING = "VALIDITY_RECHECK_PENDING"
    DEFERRED = "DEFERRED"
    COMMAND_PAYLOAD_CONFLICT = "COMMAND_PAYLOAD_CONFLICT"
    HISTORICAL_STRUCTURE_UNAVAILABLE = "HISTORICAL_STRUCTURE_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ErrorEntry:
    category: ErrorCategory
    stop: StopScope
    #: 回复本身读不成一份决定：消耗同一请求唯一的一次格式重问。系统一侧的失败
    #: （来源读不到、内部合同错）不算。
    format_retry: bool = False


def _e(category: ErrorCategory, stop: StopScope = StopScope.DECISION, *, format_retry: bool = False) -> ErrorEntry:
    return ErrorEntry(category, stop, format_retry)


C = ErrorCategory
_PLANNING: dict[P, ErrorEntry] = {
    # 身份/协议：回复读不成一份决定的七个码消耗格式重问，其余是读出来了但不合协议。
    P.DECISION_BLOCK_MISSING: _e(C.IDENTITY_PROTOCOL, format_retry=True),
    P.MULTIPLE_DECISIONS: _e(C.IDENTITY_PROTOCOL, format_retry=True),
    P.MIXED_PROTOCOL_BLOCKS: _e(C.IDENTITY_PROTOCOL, format_retry=True),
    P.MALFORMED_DECISION: _e(C.IDENTITY_PROTOCOL, format_retry=True),
    P.UNKNOWN_FIELD: _e(C.IDENTITY_PROTOCOL, format_retry=True),
    P.MODEL_SET_SYSTEM_FIELD: _e(C.IDENTITY_PROTOCOL, format_retry=True),
    P.DECISION_TYPE_UNKNOWN: _e(C.IDENTITY_PROTOCOL, format_retry=True),
    P.DECISION_NOT_ENABLED_IN_PHASE: _e(C.IDENTITY_PROTOCOL),
    P.SUBJECT_NOT_IN_REQUEST: _e(C.IDENTITY_PROTOCOL),
    P.REF_OUTSIDE_CONTEXT: _e(C.IDENTITY_PROTOCOL),
    P.PACKAGE_HASH_MISMATCH: _e(C.IDENTITY_PROTOCOL),
    # 请求过期
    P.REQUEST_BINDING_STALE: _e(C.REQUEST_STALE, StopScope.REQUEST),
    P.METHOD_STALE: _e(C.REQUEST_STALE, StopScope.REQUEST),
    # 来源不可用：§12 来源缺失/读不完整对规划器报 INTERNAL_CONTRACT_ERROR
    P.INTERNAL_CONTRACT_ERROR: _e(C.SOURCE_UNAVAILABLE, StopScope.REQUEST),
    # 权限
    P.METHOD_NOT_AUTHORIZED: _e(C.AUTHORIZATION),
    P.AUTHORIZATION_REQUIRED: _e(C.AUTHORIZATION),
    P.CAPABILITY_MISSING: _e(C.AUTHORIZATION),
    # 方法/参数/前提
    P.METHOD_NOT_FOUND: _e(C.METHOD_PARAMETER_PRECONDITION),
    P.METHOD_RETIRED: _e(C.METHOD_PARAMETER_PRECONDITION),
    P.METHOD_REJECTED: _e(C.METHOD_PARAMETER_PRECONDITION),
    P.METHOD_INAPPLICABLE: _e(C.METHOD_PARAMETER_PRECONDITION),
    P.PARAMETER_INVALID: _e(C.METHOD_PARAMETER_PRECONDITION),
    P.EVIDENCE_REQUIRED: _e(C.METHOD_PARAMETER_PRECONDITION),
    P.EVIDENCE_CONFLICT: _e(C.METHOD_PARAMETER_PRECONDITION),
    P.OBLIGATION_NOT_OPEN: _e(C.METHOD_PARAMETER_PRECONDITION),
    P.REPAIR_NOT_ALLOWED: _e(C.METHOD_PARAMETER_PRECONDITION),
    P.REUSE_NOT_ALLOWED: _e(C.METHOD_PARAMETER_PRECONDITION),
    # 结构/数据/覆盖
    P.DATA_UNBOUND: _e(C.STRUCTURE_DATA_COVERAGE),
    P.STRUCTURE_INVALID: _e(C.STRUCTURE_DATA_COVERAGE),
    P.ORDER_CYCLE: _e(C.STRUCTURE_DATA_COVERAGE),
    P.REFINEMENT_CYCLE: _e(C.STRUCTURE_DATA_COVERAGE),
    P.COVERAGE_GAP: _e(C.STRUCTURE_DATA_COVERAGE),
    # 需收敛：合法候选，但旧工作/对外操作未决
    P.OPERATION_UNRESOLVED: _e(C.NEEDS_CONVERGENCE),
    P.RUNNING_WORK_NOT_RECONCILED: _e(C.NEEDS_CONVERGENCE),
    # 预算
    P.BUDGET_INSUFFICIENT: _e(C.BUDGET),
    P.PLANNING_BOUND_REACHED: _e(C.BUDGET),
}

T = TaskGraphBoundaryCode
_TASKGRAPH: dict[T, ErrorEntry] = {
    T.SOURCE_UNAVAILABLE: _e(C.SOURCE_UNAVAILABLE, StopScope.REQUEST),
    T.GRAPH_INTEGRITY: _e(C.STRUCTURE_DATA_COVERAGE, StopScope.MISSION_DISPATCH),
    T.WAITING_DATA: _e(C.STRUCTURE_DATA_COVERAGE),
    T.VALIDITY_RECHECK_PENDING: _e(C.REQUEST_STALE, StopScope.REQUEST),
    T.DEFERRED: _e(C.NEEDS_CONVERGENCE),
    T.COMMAND_PAYLOAD_CONFLICT: _e(C.COMMIT_CONFLICT),
    T.HISTORICAL_STRUCTURE_UNAVAILABLE: _e(C.SOURCE_UNAVAILABLE, StopScope.DIAGNOSTIC),
}

PLANNING_ERRORS: Mapping[P, ErrorEntry] = MappingProxyType(_PLANNING)
TASKGRAPH_ERRORS: Mapping[T, ErrorEntry] = MappingProxyType(_TASKGRAPH)

#: 消耗同一请求格式重问的规划器拒绝码。
FORMAT_RETRY_CODES: frozenset[P] = frozenset(code for code, entry in _PLANNING.items() if entry.format_retry)


def classify(code: object) -> ErrorEntry:
    """登记过的跨边界码的归类；没登记的码一律拒绝（§12 fail-closed）。"""

    if isinstance(code, P) or (isinstance(code, str) and code in P.__members__):
        return _PLANNING[P(str(code))]
    if isinstance(code, T) or (isinstance(code, str) and code in T.__members__):
        return _TASKGRAPH[T(str(code))]
    raise ContractError(f"ERROR_CODE_UNREGISTERED: {code!r}")


def ordered(codes: Iterable[P]) -> tuple[P, ...]:
    """去重后按类别先后排；同一类别保持发现顺序。"""

    unique = tuple(dict.fromkeys(codes))
    return tuple(sorted(unique, key=lambda code: classify(code).category))


__all__ = (
    "FORMAT_RETRY_CODES",
    "PLANNING_ERRORS",
    "TASKGRAPH_ERRORS",
    "ErrorCategory",
    "ErrorEntry",
    "StopScope",
    "TaskGraphBoundaryCode",
    "classify",
    "ordered",
)

# SPDX-License-Identifier: Apache-2.0
"""错误码表（TaskGraph 原计划 §12；HTN 补齐阶段 A 第 4 条）：跨边界的拒绝码各归一类。

只管秩序，不管语义：一个码属于哪一类、同一阶段几个码按什么先后报。以后哪道秩序（在哪里
停、算不算格式错）要按类型码决定，就在这里加一列并让生产代码读它——不另写第二份声明。给规划器的仍只是事实与字段路径——不按类型码
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
    #: 这条拒绝算不算规划器"答错"一次（计入同一问题的答错上限）。请求过期类不算：
    #: 规划器答的是一份在它作答期间变了的世界（纪元动了、计划换了版本），重问即可，
    #: 上限由任务总额度兜着（阶段 D 裁决 1.9）。
    charges_planner: bool = True


def _e(category: ErrorCategory) -> ErrorEntry:
    return ErrorEntry(category, charges_planner=category is not ErrorCategory.REQUEST_STALE)


def refusal_charges_planner(codes: object) -> bool:
    """Whether a planning refusal with these problem codes counts against the Planner.
    Only a refusal made *entirely* of registered request-stale codes does not."""

    names = [str(code) for code in codes or ()] if isinstance(codes, (list, tuple, set, frozenset)) else []
    if not names:
        return True
    for name in names:
        if name not in P.__members__:
            return True
        if _PLANNING[P(name)].charges_planner:
            return True
    return False


C = ErrorCategory
_PLANNING: dict[P, ErrorEntry] = {
    # 身份/协议
    P.DECISION_BLOCK_MISSING: _e(C.IDENTITY_PROTOCOL),
    P.MULTIPLE_DECISIONS: _e(C.IDENTITY_PROTOCOL),
    P.MIXED_PROTOCOL_BLOCKS: _e(C.IDENTITY_PROTOCOL),
    P.MALFORMED_DECISION: _e(C.IDENTITY_PROTOCOL),
    P.UNKNOWN_FIELD: _e(C.IDENTITY_PROTOCOL),
    P.MODEL_SET_SYSTEM_FIELD: _e(C.IDENTITY_PROTOCOL),
    P.DECISION_TYPE_UNKNOWN: _e(C.IDENTITY_PROTOCOL),
    P.DECISION_NOT_ENABLED_IN_PHASE: _e(C.IDENTITY_PROTOCOL),
    P.SUBJECT_NOT_IN_REQUEST: _e(C.IDENTITY_PROTOCOL),
    P.REF_OUTSIDE_CONTEXT: _e(C.IDENTITY_PROTOCOL),
    P.PACKAGE_HASH_MISMATCH: _e(C.IDENTITY_PROTOCOL),
    # 请求过期
    P.REQUEST_BINDING_STALE: _e(C.REQUEST_STALE),
    P.METHOD_STALE: _e(C.REQUEST_STALE),
    # 来源不可用：§12 来源缺失/读不完整对规划器报 INTERNAL_CONTRACT_ERROR
    P.INTERNAL_CONTRACT_ERROR: _e(C.SOURCE_UNAVAILABLE),
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
    T.SOURCE_UNAVAILABLE: _e(C.SOURCE_UNAVAILABLE),
    T.GRAPH_INTEGRITY: _e(C.STRUCTURE_DATA_COVERAGE),
    T.WAITING_DATA: _e(C.STRUCTURE_DATA_COVERAGE),
    T.VALIDITY_RECHECK_PENDING: _e(C.REQUEST_STALE),
    T.DEFERRED: _e(C.NEEDS_CONVERGENCE),
    T.COMMAND_PAYLOAD_CONFLICT: _e(C.COMMIT_CONFLICT),
    T.HISTORICAL_STRUCTURE_UNAVAILABLE: _e(C.SOURCE_UNAVAILABLE),
}

# 交接拒绝原因（2026-10-03 阶段 B 裁决第 5 类）：只登记"暂时性"这一列——暂时的拒绝让动作
# 留在可交接状态、下一轮再试；没登记的一律不是暂时的，交接被拒就按"动作失败"停任务。
_HANDOFF_TRANSIENT: frozenset[str] = frozenset({
    "taskgraph_target_fenced",  # 改做法的围栏还没解除：随围栏的决定提交或被拒而结束
})


#: 交接前核对发现这一步的有效性见证过期（纪元动了、所依据的验收不再当前）：同样不是动作
#: 失败，留在可交接状态；地基恢复后自然通过，不恢复则由卡死记录如实交给规划器。
HANDOFF_VALIDITY_STALE = "validity_stale:"


def handoff_refusal_transient(reason: object) -> bool:
    """A handoff refusal that ends by itself (the caller waits instead of stopping)."""

    return isinstance(reason, str) and (
        reason in _HANDOFF_TRANSIENT or reason.startswith(HANDOFF_VALIDITY_STALE))


# 一轮故障（TaskGraph 补全第一批，原计划 §12）：一个任务这一轮的工作冲出异常时，主循环只按
# 异常带的类型码查这张表，决定"数据损坏、当轮停这个任务"还是"原地重试、由连续次数上限兜住"。
# 不读异常文字。没带码的异常（库锁、磁盘、网络、程序错误）与带了却没登记的码一律原地重试
# （用户 2026-09-28：基础设施故障原地重试）；"未知码拒绝"落在登记上——带码的异常类都继承
# :class:`CodedFault`，它的码必须是 :class:`RoundFaultCode` 里登记过的（源码扫描用例守住）。


class RoundFaultHandling(StrEnum):
    CORRUPT_STOP = "CORRUPT_STOP"
    RETRY_IN_PLACE = "RETRY_IN_PLACE"


class RoundFaultCode(StrEnum):
    """异常对象上带的、决定一轮故障怎么处理的码。"""

    TASKGRAPH_HISTORY_INTEGRITY = "TASKGRAPH_HISTORY_INTEGRITY"
    # 执行投影排不出先后（有环）：沿用执行图对外的既有码字面
    PROJECTION_NOT_ORDERABLE = "projection_not_orderable"
    # 计划的意思读不全（不是环）：某一步没有语义绑定、或读不出唯一的根目标
    SEMANTIC_BINDING_MISSING = "semantic_binding_missing"
    ROOT_NOT_IDENTIFIED = "root_not_identified"
    TASKGRAPH_ATTEMPT_INPUT_INTEGRITY = "TASKGRAPH_ATTEMPT_INPUT_INTEGRITY"
    TASKGRAPH_SOURCE_INTEGRITY = "TASKGRAPH_SOURCE_INTEGRITY"
    STORED_RESULT_CORRUPT = "STORED_RESULT_CORRUPT"
    # 审阅回合的身份与冻结记录不符（启动绑定时发现）：重试也是同一个结果。
    SERVICE_TURN_IDENTITY_MISMATCH = "SERVICE_TURN_IDENTITY_MISMATCH"
    # 任务没绑定执行图、或绑定的内核版本这一版不认（开发库里的老任务）：重试也是同一个结果。
    TASKGRAPH_NOT_BOUND = "TASKGRAPH_NOT_BOUND"
    TASKGRAPH_KERNEL_UNSUPPORTED = "TASKGRAPH_KERNEL_UNSUPPORTED"


_ROUND_FAULT: dict[RoundFaultCode, RoundFaultHandling] = {
    RoundFaultCode.TASKGRAPH_HISTORY_INTEGRITY: RoundFaultHandling.CORRUPT_STOP,
    RoundFaultCode.PROJECTION_NOT_ORDERABLE: RoundFaultHandling.CORRUPT_STOP,
    RoundFaultCode.SEMANTIC_BINDING_MISSING: RoundFaultHandling.CORRUPT_STOP,
    RoundFaultCode.ROOT_NOT_IDENTIFIED: RoundFaultHandling.CORRUPT_STOP,
    RoundFaultCode.TASKGRAPH_ATTEMPT_INPUT_INTEGRITY: RoundFaultHandling.CORRUPT_STOP,
    RoundFaultCode.TASKGRAPH_SOURCE_INTEGRITY: RoundFaultHandling.CORRUPT_STOP,
    RoundFaultCode.STORED_RESULT_CORRUPT: RoundFaultHandling.CORRUPT_STOP,
    RoundFaultCode.SERVICE_TURN_IDENTITY_MISMATCH: RoundFaultHandling.CORRUPT_STOP,
    RoundFaultCode.TASKGRAPH_NOT_BOUND: RoundFaultHandling.CORRUPT_STOP,
    RoundFaultCode.TASKGRAPH_KERNEL_UNSUPPORTED: RoundFaultHandling.CORRUPT_STOP,
}


class CodedFault(Exception):
    """带类型码的异常的共同基类：子类在类上写 ``code = RoundFaultCode.…``。"""

    code: RoundFaultCode


def round_fault_handling(error: BaseException) -> tuple[RoundFaultHandling, str | None]:
    """(怎么处理, 异常带的码)。只看异常对象上的类型码，不读异常文字。"""

    code = getattr(error, "code", None) if isinstance(error, CodedFault) else None
    if isinstance(code, RoundFaultCode) and code in _ROUND_FAULT:
        return _ROUND_FAULT[code], str(code)
    return RoundFaultHandling.RETRY_IN_PLACE, None if code is None else str(code)


PLANNING_ERRORS: Mapping[P, ErrorEntry] = MappingProxyType(_PLANNING)
TASKGRAPH_ERRORS: Mapping[T, ErrorEntry] = MappingProxyType(_TASKGRAPH)
ROUND_FAULTS: Mapping[RoundFaultCode, RoundFaultHandling] = MappingProxyType(_ROUND_FAULT)

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
    "PLANNING_ERRORS",
    "ROUND_FAULTS",
    "CodedFault",
    "RoundFaultCode",
    "RoundFaultHandling",
    "round_fault_handling",
    "TASKGRAPH_ERRORS",
    "ErrorCategory",
    "ErrorEntry",
    "TaskGraphBoundaryCode",
    "classify",
    "handoff_refusal_transient",
    "ordered",
)

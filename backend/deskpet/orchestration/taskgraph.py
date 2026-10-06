# SPDX-License-Identifier: BUSL-1.1
"""Read-only TaskGraph verbs on the existing authenticated control transport."""
from __future__ import annotations

import logging

from collections.abc import Mapping
from typing import Any

from .service import OrchestrationRequestError

logger = logging.getLogger(__name__)


class TaskGraphRequestError(OrchestrationRequestError):
    def __init__(self, wire: Mapping[str, Any]) -> None:
        self.wire = dict(wire)
        super().__init__(str(wire["code"]), str(wire["detail"]))


def _invalid() -> None:
    raise OrchestrationRequestError("invalid_request", "执行图请求的字段或版本无效")


def read_taskgraph(service: Any, operation: str, request: Mapping[str, Any]) -> dict[str, Any]:
    required = {"snapshot": {"mission_id"}, "why_not_ready": {"mission_id", "occurrence_id"},
                "diff": {"mission_id", "from_revision", "to_revision"}, "convergence": {"mission_id"},
                "execution_snapshot": {"mission_id"}, "execution_detail": {"mission_id", "node_id"}}
    optional = {"snapshot": {"revision"}, "execution_snapshot": {"cursor", "limit"},
                "execution_detail": {"through_journal_seq"}}
    if operation not in required:
        _invalid()
    allowed = required[operation] | optional.get(operation, set())
    if not required[operation] <= set(request) or set(request) - allowed:
        _invalid()
    for key, value in request.items():
        if key in ("revision", "from_revision", "to_revision", "limit", "through_journal_seq"):
            if key in ("revision", "through_journal_seq", "limit") and value is None:
                continue
            if type(value) is not int or not 0 <= value <= 2**53 - 1:
                _invalid()
        elif key == "cursor" and value is None:
            continue
        elif not isinstance(value, str) or not value.strip() or len(value) > (4096 if key == "cursor" else 512):
            _invalid()
    mission_id = request["mission_id"]
    # Ownership is checked before optional SDK capability detection. Tenant and
    # principal are never accepted from an IPC body.
    service._require()._mission(mission_id)
    try:
        from agent_orchestrator.api.taskgraph import TaskGraphReadError
        from agent_orchestrator.graph.notification_contracts import TaskGraphErrorV1
        from agent_orchestrator.runtime.planning_operations import SourceUnavailable
        from agent_orchestrator.storage.taskgraph_store import GraphIntegrityError
        from agent_orchestrator.contracts.models import ContractError
        from agent_orchestrator.storage.store import StoreError
    except ImportError as error:
        raise OrchestrationRequestError("taskgraph_unavailable", "当前 SDK 尚未安装执行图读取接口") from error
    try:
        read_api = getattr(service._orchestrator, "taskgraph_read_api", None)
        if not callable(read_api):
            raise SourceUnavailable("taskgraph_read_assembly_missing")
        api = read_api(tenant_id=service.tenant_id, principal=service._principal)
        if operation == "snapshot":
            return api.snapshot(mission_id, revision=request.get("revision"))
        if operation == "why_not_ready":
            return api.why_not_ready(mission_id, request["occurrence_id"])
        if operation == "diff":
            return api.diff(mission_id, request["from_revision"], request["to_revision"])
        if operation == "execution_snapshot":
            limit = request.get("limit")
            return api.execution_snapshot(mission_id, cursor=request.get("cursor"),
                                          **({} if limit is None else {"limit": limit}))
        if operation == "execution_detail":
            return api.execution_detail(mission_id, request["node_id"],
                                        through_journal_seq=request.get("through_journal_seq"))
        return api.convergence(mission_id)
    except TaskGraphReadError as error:
        raise TaskGraphRequestError(error.error.to_json()) from error
    except (GraphIntegrityError, ContractError, SourceUnavailable, StoreError) as error:
        code = "GRAPH_INTEGRITY" if isinstance(error, (GraphIntegrityError, ContractError)) else "SOURCE_UNAVAILABLE"
        # 2026-09-25: the wire hides the cause on purpose (operator repair, not a model
        # hint), so the cause must at least reach the Host log or nobody can repair it.
        logger.warning("taskgraph %s read failed for %s: %s: %s", operation, mission_id, type(error).__name__, error)
        wire = TaskGraphErrorV1(origin="SYSTEM", stage="READ", code=code,
            detail="执行图来源不可读，请保留当前画面并重新读取。",
            retry_kind="OPERATOR_REPAIR" if code == "GRAPH_INTEGRITY" else "REQUERY",
            source_identity=None)
        raise TaskGraphRequestError(wire.to_json()) from error


#: 操作员拒绝码 → 给人看的原因（SDK 的两道安全检查不满足时如实告诉人，按钮不替人重试）。
_OPERATOR_REFUSALS = {
    "TASKGRAPH_CONVERGENCE_NOT_QUIESCENT": "旧尝试还没停下，不能放弃这次改计划；等它停下或先取消它",
    "TASKGRAPH_ABANDONMENT_OLD_DEMAND_CHANGED": "计划结构已经变了，不能再放弃这次改计划",
    "TASKGRAPH_CONVERGENCE_TERMINAL": "这次改计划已经结束了",
    "TASKGRAPH_CONVERGENCE_CAS_CONFLICT": "状态已经变了，请刷新后再试",
    "TASKGRAPH_FOLLOWUP_REPAIR_CONFLICT": "这条通知的状态已经变了，请刷新后再试",
}


def operate_taskgraph(service: Any, verb: str, request: Mapping[str, Any]) -> dict[str, Any]:
    """人在"改计划进度"面板上点的两个动作（HTN 补齐阶段 B 第 2 条）。

    只从控制通道进来——那是人的点击；主 Agent 的工具里没有这两个动词。本机用户身份就是启用
    执行图的那个人；每次点击生成自己的命令号，同一次点击重放不会做两遍。
    """
    import uuid

    fields = {"abandon_convergence": ("mission_id", "job_id", "expected_version", "reason"),
              "retry_notification": ("mission_id", "message_id", "expected_version", "reason")}
    if verb not in fields or set(request) != set(fields[verb]):
        _invalid()
    version = request["expected_version"]
    if type(version) is not int or not 1 <= version <= 2**53 - 1:
        _invalid()
    for key in fields[verb]:
        if key != "expected_version" and (not isinstance(request[key], str) or not request[key].strip()
                                          or len(request[key]) > (2048 if key == "reason" else 512)):
            _invalid()
    service._refuse_secrets(request["reason"])
    mission_id = request["mission_id"]
    service._require()._mission(mission_id)
    from agent_orchestrator.storage.store import StoreError

    command_id = f"ui-click-{verb}-{uuid.uuid4().hex}"
    try:
        operator = service._orchestrator.taskgraph_operator_api(tenant_id=service.tenant_id,
                                                                principal=service._principal)
        if verb == "abandon_convergence":
            receipt = operator.abandon_convergence(mission_id, request["job_id"], expected_version=version,
                                                   command_id=command_id, reason=request["reason"])
        else:
            receipt = operator.retry_notification(mission_id, request["message_id"], expected_version=version,
                                                  command_id=command_id, reason=request["reason"])
    except StoreError as error:  # StoreConflict is a StoreError
        # 只读异常上的类型码，不从文字里切（第 1 批 T02）；没带码的拒绝如实说"原因未具名"并记日志。
        code = getattr(error, "code", None)
        if not isinstance(code, str) or not code:
            logger.warning("taskgraph %s refused for %s without a typed code: %s", verb, mission_id, error)
            raise OrchestrationRequestError("taskgraph_refused", "没有做成：原因未具名") from error
        logger.info("taskgraph %s refused for %s: %s", verb, mission_id, code)
        raise OrchestrationRequestError("taskgraph_refused", _OPERATOR_REFUSALS.get(code, f"没有做成：{code}")) from error
    service.wake()
    return dict(receipt)


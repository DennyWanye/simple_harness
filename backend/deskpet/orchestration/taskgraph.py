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
                "diff": {"mission_id", "from_revision", "to_revision"}, "convergence": {"mission_id"}}
    if operation not in required:
        _invalid()
    allowed = required[operation] | ({"revision"} if operation == "snapshot" else set())
    if not required[operation] <= set(request) or set(request) - allowed:
        _invalid()
    for key, value in request.items():
        if key in ("revision", "from_revision", "to_revision"):
            if key == "revision" and value is None:
                continue
            if type(value) is not int or not 0 <= value <= 2**53 - 1:
                _invalid()
        elif not isinstance(value, str) or not value.strip() or len(value) > 512:
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

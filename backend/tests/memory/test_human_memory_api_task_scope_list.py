"""任务范围的精确打开：只经可信路径（主 Agent 的 context_route）由 Host 提供 live_probe。

公开 HUMAN 通道不再受理任务范围面板的列表、打开、视图与证据分页操作。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.human_memory_service import (
    AuthenticatedHostSnapshot,
    HumanMemoryHostServiceFactory,
    OpenTaskScopeRequest,
)
from deskpet.memory.schema import dispatch_startup_epoch


def _auth(subject: str) -> AuthenticatedHostSnapshot:
    return AuthenticatedHostSnapshot(subject, f"principal-{subject}", f"host:{subject}")


async def _call(factory, auth, operation, request):
    response = await handle_human_memory_command(
        {
            "type": "human_memory_request",
            "request_id": f"{operation}:{json.dumps(request, sort_keys=True)}",
            "operation": operation,
            "request": request,
        },
        factory=factory,
        auth=auth,
    )
    return response["payload"]


@pytest.mark.asyncio
async def test_trusted_open_task_scope_keeps_live_probe_and_panel_operations_are_gone(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    factory = HumanMemoryHostServiceFactory(
        path, await dispatch_startup_epoch(path, approved_fresh_lane=True)
    )
    alice = _auth("alice")
    created = await _call(
        factory,
        alice,
        "task_scope.create",
        {"fixture_key": "k1", "title": "整理照片库", "goal": "归档"},
    )
    assert created["ok"], created
    scope = created["result"]["scope_ref"]

    for operation in (
        "task_scope.search",
        "task_scope.list",
        "task_scope.open_exact",
        "task_scope.view",
        "task_scope.evidence_groups",
        "task_scope.evidence_page",
    ):
        rejected = await _call(factory, alice, operation, {"scope_ref": scope})
        assert not rejected["ok"], operation
        assert rejected["error"]["code"] == "human_memory_operation_unavailable", operation

    service = factory.bind(alice)
    plain = await service.open_task_scope(OpenTaskScopeRequest(scope, None, None))
    assert plain["drift_report"] is None and plain["drift_probe"] == "host_unavailable"
    trusted = await service.open_task_scope(OpenTaskScopeRequest(scope, {"head": "abc"}, None))
    assert trusted["drift_probe"] == "trusted_run"
    assert trusted["drift_report"] is not None and trusted["drift_report"]["drifted"] is True
    assert trusted["drift_report"]["changed_fields"] == ["head"]

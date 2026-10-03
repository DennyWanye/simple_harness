# SPDX-License-Identifier: BUSL-1.1
"""人在"改计划进度"面板上的两个动作（HTN 补齐阶段 B 第 2 条，2026-10-03）。

Host 只核对请求形状和任务归属，以本机用户身份调 SDK 操作员服务；每次点击生成自己的命令号。
SDK 的安全检查不满足时如实把原因告诉人，不替人重试。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from agent_orchestrator.storage.store import StoreConflict

from deskpet.orchestration.service import OrchestrationRequestError
from deskpet.orchestration.taskgraph import operate_taskgraph

M = "mission-x"


class _Operator:
    def __init__(self, refuse: str | None = None) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.refuse = refuse

    def abandon_convergence(self, mission_id, job_id, *, expected_version, command_id, reason):  # type: ignore[no-untyped-def]
        self.calls.append(("abandon", mission_id, job_id, expected_version, command_id, reason))
        if self.refuse:
            raise StoreConflict(self.refuse)
        return {"kind": "TaskGraphConvergenceAbandoned", "command_id": command_id}

    def retry_notification(self, mission_id, message_id, *, expected_version, command_id, reason):  # type: ignore[no-untyped-def]
        self.calls.append(("retry", mission_id, message_id, expected_version, command_id, reason))
        if self.refuse:
            raise StoreConflict(self.refuse)
        return {"kind": "TaskGraphNotificationRetryAuthorized", "command_id": command_id}


def _service(operator: _Operator, owned: frozenset[str] = frozenset({M})) -> Any:
    def mission(mission_id: str) -> None:
        if mission_id not in owned:
            raise OrchestrationRequestError("not_found", "任务不存在")
    woke = []
    return SimpleNamespace(
        _require=lambda: SimpleNamespace(_mission=mission), tenant_id="t", _principal="local-user",
        _orchestrator=SimpleNamespace(taskgraph_operator_api=lambda **kw: operator if kw == {
            "tenant_id": "t", "principal": "local-user"} else None),
        _refuse_secrets=lambda *values: None, wake=lambda: woke.append(True), woke=woke)


def test_each_click_reaches_the_operator_with_its_own_command_and_wakes_the_loop():
    operator = _Operator()
    service = _service(operator)
    body = {"mission_id": M, "job_id": "job-1", "expected_version": 3, "reason": "先按原计划做完"}
    first = operate_taskgraph(service, "abandon_convergence", body)
    second = operate_taskgraph(service, "retry_notification",
                               {"mission_id": M, "message_id": "msg-1", "expected_version": 6, "reason": "故障已排除"})
    assert [call[:4] for call in operator.calls] == [("abandon", M, "job-1", 3), ("retry", M, "msg-1", 6)]
    assert first["command_id"] != second["command_id"] and first["command_id"].startswith("ui-click-")
    assert service.woke == [True, True]


def test_a_refused_click_says_why_and_is_not_retried():
    operator = _Operator(refuse="TASKGRAPH_CONVERGENCE_CAS_CONFLICT")
    with pytest.raises(OrchestrationRequestError) as caught:
        operate_taskgraph(_service(operator), "abandon_convergence",
                          {"mission_id": M, "job_id": "job-1", "expected_version": 3, "reason": "放弃"})
    assert caught.value.code == "taskgraph_refused" and "刷新" in str(caught.value)
    assert len(operator.calls) == 1


@pytest.mark.parametrize("verb,body", [
    ("abandon_convergence", {"mission_id": M, "job_id": "j", "expected_version": 3}),
    ("abandon_convergence", {"mission_id": M, "job_id": "j", "expected_version": 0, "reason": "x"}),
    ("abandon_convergence", {"mission_id": M, "job_id": "j", "expected_version": 3, "reason": " "}),
    ("retry_notification", {"mission_id": M, "message_id": "m", "expected_version": "6", "reason": "x"}),
    ("retry_notification", {"mission_id": M, "message_id": "m", "expected_version": 6, "reason": "x",
                            "principal": "someone-else"}),
    ("cancel_attempt", {"mission_id": M}),
])
def test_bad_requests_never_reach_the_operator(verb, body):
    operator = _Operator()
    with pytest.raises(OrchestrationRequestError) as caught:
        operate_taskgraph(_service(operator), verb, body)
    assert caught.value.code == "invalid_request" and operator.calls == []


def test_ownership_is_checked_first():
    operator = _Operator()
    with pytest.raises(OrchestrationRequestError) as caught:
        operate_taskgraph(_service(operator, owned=frozenset()), "retry_notification",
                          {"mission_id": M, "message_id": "m", "expected_version": 6, "reason": "x"})
    assert caught.value.code == "not_found" and operator.calls == []

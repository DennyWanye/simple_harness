# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""推后第 2 批 U03：有公开合同的读动词，SDK 回复只能经 Host 投影出门。

原计划：Assurance §13.1（L350、L354）"DTO 完整见 host-*-v1.schema.json""非法 DTO 显示协议错误"；
HTN §17.2（L856）"后端输出权威投影；前端用同一公开 Schema 验证"。

假的 SDK 读接口吐出不合合同的回复（多字段、错枚举、坏错误回执），Host 必须拒绝：
回 ``protocol_error`` 一句大白话，不带数据、不带原回执。合格回复照常出门。
最后一条核"Host 用的包内 Schema 与仓库 SDK 源码（前端 import 的那一份）字节相同"。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from deskpet.orchestration.handlers import handle

HASH = "a" * 64
REPO_SDK = Path(__file__).resolve().parents[3] / "sdk" / "simple-harness-sdk" / "src" / "agent_orchestrator"


def _snapshot(**extra: Any) -> dict[str, Any]:
    body = {"schema_version": 1, "request_id": "s1", "mission_id": "m1", "view": "CURRENT", "snapshot_seq": 3,
            "root_incarnation_id": "root-1", "sdk_fingerprint": HASH, "host_fingerprint": "b" * 64,
            "items": [{"kind": "CRITERION", "id": "c-user-1", "history_state": "UNREVIEWED",
                       "current_use": "NOT_APPLICABLE", "reason_codes": [], "evidence_count": 0,
                       "artifact_ref": None}],
            "next_cursor": None, "truncated": False}
    body.update(extra)
    return body


def _convergence(**extra: Any) -> dict[str, Any]:
    body = {"schema_version": 2, "mission_id": "m1", "through_seq": 0, "jobs": [],
            "blocked_notifications": [], "complete": True}
    body.update(extra)
    return body


class _Control:
    def __init__(self, reply: Any) -> None:
        self.reply = reply

    def _mission(self, mission_id: str) -> None:
        return None

    def assurance_snapshot(self, body: dict[str, Any]) -> Any:
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class _GraphApi:
    def __init__(self, reply: Any) -> None:
        self.reply = reply

    def convergence(self, mission_id: str) -> Any:
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class _Orchestrator:
    def __init__(self, reply: Any) -> None:
        self.reply = reply

    def taskgraph_read_api(self, **_: Any) -> _GraphApi:
        return _GraphApi(self.reply)


class _Service:
    """只有读路径要用的几样；读法本身走 Host 的真代码（assurance.py / taskgraph.py）。"""

    quarantined = False
    tenant_id = "local"
    _principal = object()

    def __init__(self, reply: Any) -> None:
        self._control = _Control(reply)
        self._orchestrator = _Orchestrator(reply)

    def status(self) -> dict[str, Any]:
        return {"state": "available"}

    def _require(self) -> _Control:
        return self._control

    def assurance_read(self, verb: str, request: Any) -> Any:
        from deskpet.orchestration.assurance import read_assurance
        return read_assurance(self, verb, request)

    def taskgraph_read(self, operation: str, request: Any) -> Any:
        from deskpet.orchestration.taskgraph import read_taskgraph
        return read_taskgraph(self, operation, request)


def _assurance_request() -> dict[str, Any]:
    return {"request_id": "s1", "schema_version": 1, "mission_id": "m1", "view": "CURRENT",
            "at_event_seq": None, "cursor": None, "limit": 100}


def _refused_as_protocol_error(response: dict[str, Any]) -> None:
    payload = response["payload"]
    assert payload["ok"] is False, payload
    assert payload["error_code"] == "protocol_error"
    assert payload["error"] == "收到的数据格式不对，没有显示，请稍后重新读取。"
    assert "data" not in payload and "assurance_error" not in payload and "taskgraph_error" not in payload


@pytest.mark.asyncio
async def test_valid_assurance_reply_goes_out_as_a_fresh_copy() -> None:
    reply = _snapshot()
    response = await handle(_Service(reply), "mission_assurance_snapshot", _assurance_request())
    payload = response["payload"]
    assert payload["ok"] is True, payload
    assert payload["data"] == reply
    assert payload["data"] is not reply and payload["data"]["items"] is not reply["items"]


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", [
    {"tenant_id": "other"},                       # 合同外的字段
    {"view": "LIVE"},                             # 枚举外的值
    {"items": [{"kind": "CRITERION", "id": "c", "history_state": "x", "current_use": "GRANTED",
                "reason_codes": [], "evidence_count": 0, "artifact_ref": None}]},
    {"snapshot_seq": -1},                         # 越界
])
async def test_drifted_assurance_reply_is_refused_as_protocol_error(drift: dict[str, Any]) -> None:
    response = await handle(_Service(_snapshot(**drift)), "mission_assurance_snapshot", _assurance_request())
    assert response["type"] == "mission_assurance_snapshot_response"
    _refused_as_protocol_error(response)


@pytest.mark.asyncio
async def test_assurance_error_wire_is_checked_before_it_goes_out() -> None:
    from agent_orchestrator.api.facade import FacadeError

    good = FacadeError("NOT_FOUND", "no such review")
    good.wire = {"schema_version": 1, "request_id": "s1", "code": "NOT_FOUND", "message": "no", "retryable": False}
    response = await handle(_Service(good), "mission_assurance_snapshot", _assurance_request())
    assert response["payload"]["error_code"] == "NOT_FOUND"
    assert response["payload"]["assurance_error"] == good.wire

    bad = FacadeError("NOT_FOUND", "no such review")
    bad.wire = {**good.wire, "code": "SOMETHING_NEW"}
    _refused_as_protocol_error(await handle(_Service(bad), "mission_assurance_snapshot", _assurance_request()))


@pytest.mark.asyncio
async def test_drifted_taskgraph_view_is_refused_as_protocol_error() -> None:
    ok = await handle(_Service(_convergence()), "taskgraph.convergence", {"request_id": "g1", "mission_id": "m1"})
    assert ok["payload"]["ok"] is True and ok["payload"]["data"] == _convergence()
    for drift in ({"debug": {"rows": 1}}, {"schema_version": 1}, {"complete": "yes"}):
        response = await handle(_Service(_convergence(**drift)), "taskgraph.convergence",
                                {"request_id": "g2", "mission_id": "m1"})
        _refused_as_protocol_error(response)


@pytest.mark.asyncio
async def test_taskgraph_error_wire_is_checked_before_it_goes_out() -> None:
    from agent_orchestrator.api.taskgraph import TaskGraphReadError
    from agent_orchestrator.graph.notification_contracts import TaskGraphErrorV1

    wire = TaskGraphErrorV1(origin="SYSTEM", stage="READ", code="NOT_FOUND", detail="没有这个任务",
                            retry_kind="NONE", source_identity=None)
    response = await handle(_Service(TaskGraphReadError(wire)), "taskgraph.convergence",
                            {"request_id": "g3", "mission_id": "m1"})
    assert response["payload"]["error_code"] == "NOT_FOUND"
    assert response["payload"]["taskgraph_error"] == wire.to_json()

    class _Drifted(TaskGraphReadError):
        def __init__(self) -> None:
            Exception.__init__(self, "drifted")
            self.error = type("E", (), {"to_json": lambda _self: {**wire.to_json(), "stage": "ELSEWHERE"}})()

    _refused_as_protocol_error(await handle(_Service(_Drifted()), "taskgraph.convergence",
                                            {"request_id": "g4", "mission_id": "m1"}))


def test_host_package_schemas_are_the_repo_files_the_frontend_imports() -> None:
    """前端 ``tauri-app/src/ws/orchestrationContracts.ts`` import 仓库 SDK 源码里的文件；Host 运行时读已装包里的。
    两份字节必须相同，否则前后端核的不是同一份合同。"""
    import agent_orchestrator

    package = Path(agent_orchestrator.__file__).resolve().parent
    names = [
        "assurance/contracts/common.schema.json",
        "assurance/contracts/host-response-v1.schema.json",
        "assurance/contracts/host-review-response-v1.schema.json",
        "assurance/contracts/host-use-response-v1.schema.json",
        "assurance/contracts/host-error-v1.schema.json",
        "graph/schemas/taskgraph-view-v1.schema.json",
        "graph/schemas/taskgraph-explanation-v1.schema.json",
        "graph/schemas/taskgraph-diff-v1.schema.json",
        "graph/schemas/taskgraph-convergence-view-v2.schema.json",
        "graph/schemas/taskgraph-error-v1.schema.json",
    ]
    for name in names:
        assert (package / name).read_bytes() == (REPO_SDK / name).read_bytes(), name

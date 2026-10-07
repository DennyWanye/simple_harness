# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""推后第 2 批 U03：有公开合同的读动词，SDK 回复只能经 Host 投影出门。

原计划：Assurance §13.1（L350、L354）"DTO 完整见 host-*-v1.schema.json""非法 DTO 显示协议错误"；
HTN §17.2（L856）"后端输出权威投影；前端用同一公开 Schema 验证"。

假的 SDK 读接口吐出不合合同的回复（多字段、错枚举、坏错误回执），Host 必须拒绝：
回 ``protocol_error`` 一句大白话，不带数据、不带原回执。合格回复照常出门。
最后一条核"Host 用的包内 Schema 与仓库 SDK 源码（前端 import 的那一份）字节相同"。

推后第 3 批 U09 加四个动词：执行图主画面（``taskgraph.execution_snapshot``）、回合详情
（``taskgraph.execution_detail``）、任务列表（``mission_list``）、任务详情（``mission_get``）。
另有一条产品同形：脚本化任务整圈跑完，四个动词经 ``handle`` 读出的真实数据全部合合同。
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

    def execution_snapshot(self, mission_id: str, **_: Any) -> Any:
        return self.convergence(mission_id)

    def execution_detail(self, mission_id: str, node_id: str, **_: Any) -> Any:
        return self.convergence(mission_id)


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

    def list_missions(self, *, limit: int = 50) -> Any:
        return self._control.assurance_snapshot({})["missions"]  # handlers 外面再包一层 {"missions": …}

    def mission_detail(self, mission_id: str) -> Any:
        return self._control.assurance_snapshot({})


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
        # 推后第 3 批 U09
        "graph/schemas/taskgraph-execution-view-v1.schema.json",
        "graph/schemas/taskgraph-execution-detail-v1.schema.json",
        "assurance/contracts/host-mission-list-v1.schema.json",
        "assurance/contracts/host-mission-detail-v1.schema.json",
    ]
    for name in names:
        assert (package / name).read_bytes() == (REPO_SDK / name).read_bytes(), name


# ---------------------------------------------------------------- 推后第 3 批 U09：四个动词

TOKEN = {"plan_revision": 1, "through_seq": 3, "validity_epochs": [], "snapshot_hash": HASH, "manifest_hash": HASH}
_TURN = {"intent_id": "intent-1", "agent_id": "agent-1", "state": "SETTLED", "profile_id": None, "model": None}
_ATTEMPT = {"node_id": "attempt:a1", "kind": "attempt", "at_ms": 1000, "attempt_id": "a1", "task_id": "t1",
            "occurrence_id": "o1", "plan_revision": 1, "ordinal": 1, "status": "COMPLETED", "role": "worker",
            "turn": _TURN, "summary": None, "result_id": None}


def _execution_page(**extra: Any) -> dict[str, Any]:
    body = {"schema_version": 1, "mission_id": "m1", "view_mode": "CURRENT", "read_token": TOKEN, "graph": None,
            "occurrence_labels": None,
            "execution_cut": {"observed_at_ms": 1, "imported_through_seq": 3, "execution_hash": HASH,
                              "runtime_source_watermarks": [], "coverage": "COMPLETE"},
            "execution_nodes": [_ATTEMPT], "execution_edges": [], "next_cursor": None, "complete": True}
    body.update(extra)
    return body


def _execution_detail(**extra: Any) -> dict[str, Any]:
    body = {"schema_version": 1, "mission_id": "m1", "node": _ATTEMPT,
            "turn": {**_TURN, "coverage": "COMPLETE", "through_journal_seq": 4},
            "items": [{"t": "tool", "tool": "workspace_write_file", "ok": True, "path": "NOTES.md", "bytes": 28}],
            "hidden_items": 0}
    body.update(extra)
    return body


def _row(**extra: Any) -> dict[str, Any]:
    row = {"mission_id": "m1", "goal": "写一份 NOTES.md", "status": "ACTIVE", "stop_reason": None,
           "created_at": 1791365208.4, "pending_approvals": 0, "id": "m1", "blocked": False,
           "recovery_isolated": None, "task_counts": {"completed": 0, "total": 1}, "ui_state": "running"}
    row.update(extra)
    return row


def _detail(**extra: Any) -> dict[str, Any]:
    detail = {
        "mission": {"id": "m1", "goal": "写一份 NOTES.md", "status": "ACTIVE", "stop_reason": None,
                    "created_at": 1791365208.4, "version": 3, "budget": {"max_tokens": 100}, "allowed_tools": [],
                    "untrusted_sources": [], "ui_state": "running"},
        "tasks": [{"id": "t1", "goal": {"text": "写 NOTES.md", "source": "model"}, "status": "READY", "kind": "work",
                   "dependency_ids": [], "verification_policy": ["format_check"], "attempt_count": 0,
                   "failure_reason": None, "paused": False}],
        "attempts": [], "results": [], "artifacts": [], "actions": [], "approvals": [], "planning_questions": [],
        "planning_authorization_requests": [], "operation_workspace": None, "budget_by_duty": [],
        "unrefined_goals": [], "steps_no_longer_counting": [], "waiting_on": [], "blocked": [], "disputes": [],
        "mission_policy": {"version_id": "policy-1", "source": "active"},
        "usage": {"attempts": 0, "reserved_tokens": 0, "settled_tokens": 0, "ledger_version": 1},
        "event_count": 5, "through_seq": 9, "recovery_isolated": None}
    detail.update(extra)
    return detail


@pytest.mark.asyncio
@pytest.mark.parametrize("verb,request_body,good,drifted", [
    ("taskgraph.execution_snapshot", {"mission_id": "m1"}, _execution_page(),
     [_execution_page(execution_nodes=[{**_ATTEMPT, "debug_row": 1}]),        # 节点多字段
      _execution_page(execution_nodes=[{**_ATTEMPT, "kind": "magic"}]),       # 节点种类外
      _execution_page(complete="yes")]),
    ("taskgraph.execution_detail", {"mission_id": "m1", "node_id": "attempt:a1"}, _execution_detail(),
     [_execution_detail(items=[{"t": "tool", "tool": "x", "ok": True, "raw_args": {"k": 1}}]),   # 条目带原始参数
      _execution_detail(turn={**_TURN, "coverage": "LIVE", "through_journal_seq": 4}),
      _execution_detail(hidden_items=-1)]),
    ("mission_list", {}, {"missions": [_row()]},
     [{"missions": [_row(tenant_id="other")]},                                     # 合同外的字段
      {"missions": [_row(ui_state="done")]},                                       # 状态词表外
      {"missions": [_row(task_counts={"completed": "1", "total": 1})]}]),
    ("mission_get", {"mission_id": "m1"}, _detail(),
     [_detail(tasks=[{**_detail()["tasks"][0], "goal": "写 NOTES.md"}]),            # 模型的话没标来源
      _detail(event_count=None),
      _detail(internal_paths=["/tmp/x"])]),
])
async def test_the_four_u09_verbs_go_out_only_inside_their_public_contract(verb, request_body, good, drifted) -> None:
    ok = await handle(_Service(good), verb, {"request_id": "u1", **request_body})
    assert ok["payload"]["ok"] is True, ok["payload"]
    assert ok["payload"]["data"] == good and ok["payload"]["data"] is not good
    for body in drifted:
        response = await handle(_Service(body), verb, {"request_id": "u2", **request_body})
        assert response["type"] == f"{verb}_response"
        _refused_as_protocol_error(response)


@pytest.mark.asyncio
async def test_real_replies_of_a_finished_mission_fit_the_public_contracts(orchestration_root, principal, monkeypatch):
    """产品同形：Host 默认部署（分层 + 执行图 + 保证通道）上脚本化任务整圈跑完；四个动词经 ``handle``
    读出的真实数据全部合合同（合同不是照着假数据写的）。"""
    import asyncio

    from ._layered_lane import LayeredScriptedProvider, layered_service, notes_mission, quick_runtime, run_until_settled

    quick_runtime(monkeypatch)
    service = layered_service(orchestration_root, principal, LayeredScriptedProvider())
    await asyncio.wait_for(service.start(), 30)
    try:
        mission_id = service.create_mission(notes_mission())["mission_id"]
        await run_until_settled(service, mission_id)
        reads = [("mission_list", {}), ("mission_get", {"mission_id": mission_id}),
                 ("taskgraph.execution_snapshot", {"mission_id": mission_id})]
        replies = {verb: (await handle(service, verb, body))["payload"] for verb, body in reads}
        for verb, payload in replies.items():
            assert payload["ok"] is True, (verb, payload)
        nodes = replies["taskgraph.execution_snapshot"]["data"]["execution_nodes"]
        assert {node["kind"] for node in nodes} >= {"planning", "plan_revision", "attempt", "check", "review"}
        for node in nodes:
            detail = await handle(service, "taskgraph.execution_detail", {"mission_id": mission_id, "node_id": node["node_id"]})
            assert detail["payload"]["ok"] is True, (node["node_id"], detail["payload"])
    finally:
        await asyncio.wait_for(service.close(), 30)

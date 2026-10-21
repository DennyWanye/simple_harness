# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
"""步骤中途提议动作（AgentOS 迁移第 1、2 块）：只装宿主入口 + 脚本模型 + 假连接器走闭环。

场景（对应交接验收矩阵）：
- A01 搜索 → 提议归档（建任务时不知道目标）→ 自动代批 → 执行 → 同一步续做 → 任务完成；假存储真的变了。
- A02 连接器 ``prepare`` 整理参数，审批卡上是整理后的；交接前再整理一次对不上就不执行；名单外的提议要人批。
- A03 总要人批：拒绝 → 下一次尝试看到 REJECTED；过期 → EXPIRED；等批准时不调模型。
- A05 执行生效但回执丢了 → 查询找到 → 结案一次、续做。
- A07 只交接一次的桌面点击：超时 → 结果不明 → 不重发、等人裁定 → 裁定后续做；取消任务不动已交接的。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.host import HostSettings, OrchestrationHost, Principal
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider, worker_reply
from agent_orchestrator.runtime.connectors import (
    ActionPrepareContext,
    ConnectorRejected,
    ConnectorTransportError,
    OperationSpec,
    Receipt,
    params_hash,
)
from agent_orchestrator.testing.scripted_replies import package_of


class Photos:
    """假相册：按键去重、能查、封口；``prepare`` 把 ``to`` 规范成 ``/archive/<名字>``。"""

    name = "photos"
    supports_idempotency = True
    supports_reconciliation = True
    lookup_authority = "authoritative"
    lookup_fenced = True
    operations = {
        "move": OperationSpec("move", "L2", ("to",), description="把照片移到 to"),
        "share": OperationSpec(
            "share", "L2", ("with",), description="分享给 with", always_needs_approval=True
        ),
    }

    def __init__(self) -> None:
        self.files = {"/photos/giraffe-1.jpg", "/photos/giraffe-2.jpg", "/photos/cat.jpg"}
        self.ledger: dict[str, Receipt] = {}
        self.fenced: set[str] = set()
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.prepared: list[tuple[str, dict[str, Any]]] = []
        self.lose_receipt_once = False

    def normalize_target(self, target: str) -> str:
        return target.strip()

    def prepare(self, operation, target, params, *, context: ActionPrepareContext):
        assert isinstance(context, ActionPrepareContext)
        self.prepared.append((context.phase, dict(params)))
        if operation == "move":
            to = str(params.get("to", ""))
            if not to.startswith("/archive/"):
                to = "/archive/" + to.strip("/").split("/")[-1]
            return {**params, "to": to}
        return None

    def execute(self, operation, target, params, *, idempotency_key):
        if idempotency_key in self.fenced:
            raise ConnectorRejected("fenced")
        if idempotency_key not in self.ledger:
            self.calls.append((operation, target, dict(params)))
            if operation == "move":
                self.files.discard(target)
                self.files.add(params["to"])
            self.ledger[idempotency_key] = Receipt(
                idempotency_key,
                self.name,
                operation,
                target,
                params_hash(params),
                True,
                after={"operation": operation, **params},
            )
            if self.lose_receipt_once:
                self.lose_receipt_once = False
                raise ConnectorTransportError("receipt lost")
        return self.ledger[idempotency_key]

    def lookup(self, idempotency_key):
        found = self.ledger.get(idempotency_key)
        if found is None:
            self.fenced.add(idempotency_key)
        return found


class Desktop:
    """只交接一次的桌面点击：没有去重也没有查询；``hang`` 时点击卡住不返回（结果不明）。"""

    name = "desktop"
    delivery = "at_most_once"
    operations = {"click": OperationSpec("click", "L2", ("x", "y"), description="点一下")}

    def __init__(self) -> None:
        self.clicks: list[tuple[str, int, int]] = []
        self.hang = False

    def normalize_target(self, target: str) -> str:
        return target.strip()

    async def execute(self, operation, target, params, *, idempotency_key):
        self.clicks.append((target, int(params["x"]), int(params["y"])))
        if self.hang:
            await asyncio.sleep(5)  # 比连接器时限长得多：结果不明；但会自己结束，宿主能关
        return Receipt(idempotency_key, self.name, operation, target, params_hash(params), True)


def _search_tool(photos: Photos):
    from agent_orchestrator.host import DomainTool

    def search(arguments, context):
        word = str(arguments["q"])
        return {"matches": sorted(p for p in photos.files if word in p)}

    return DomainTool(
        name="photo_search",
        schema={
            "type": "object",
            "description": "按关键词搜相册",
            "properties": {"q": {"type": "string", "description": "关键词"}},
            "required": ["q"],
            "additionalProperties": False,
        },
        invoke=search,
    )


def _worker(plan: list[dict[str, Any]], seen: list[dict[str, Any]] | None = None):
    """按剧本做的执行者：每次尝试看工作包决定——先搜，再提议动作，最后写文件交结果。
    ``plan`` 每项 ``{"action_request": {...}}`` 是要提议的动作，按顺序，看 ``action_results``
    已有几条决定提到哪一个。"""

    def reply(request):
        package = package_of(request)
        if seen is not None:
            seen.append(package)
        contract = package.get("task_contract", {})
        results = package.get("action_results") or []
        tool_turns = sum(1 for m in request.messages if "tool" in str(m.role).lower())
        if (
            "photo_search" in (package.get("tools_and_permissions") or {}).get("allowed_tools", [])
            and tool_turns == 0
            and not results
            and plan
        ):
            return ("photo_search", {"q": "giraffe"})
        if len(results) < len(plan):
            step = plan[len(results)]
            envelope = {
                "task_id": contract.get("task_id", ""),
                "attempt_id": package.get("attempt", {}).get("attempt_id", ""),
                "outcome": "blocked",
                "summary": "需要先执行一个外部动作",
                "claims": [],
                "evidence": [],
                "artifacts": [],
                "proposed_tasks": [],
                "used_knowledge": [],
                "risks": [],
                "cost": {"tool_calls": tool_turns},
                "action_request": step["action_request"],
            }
            return (
                "<result_envelope>"
                + json.dumps(envelope, ensure_ascii=False)
                + "</result_envelope>"
            )
        return worker_reply(request)

    return reply


async def _open(tmp_path, *, connectors, tools=(), worker, auto=True, **settings):
    return await OrchestrationHost.open(
        tmp_path / "host",
        principal=Principal("u", "用户"),
        provider=ScriptedProvider(worker=worker),
        counter=FixtureWordCounter(),
        settings=HostSettings(max_action_level="L2", **settings),
        connectors=list(connectors),
        tools=list(tools),
        auto_mode=auto,
        require_acceptance=False,
    )


def _mission(host, *, allowed=("photos.move",), key="m-1"):
    return host.create_mission(
        {
            "goal": "找出长颈鹿照片并归档，然后写一份 NOTES.md 说明做了什么。",
            "success_criteria": ["file:NOTES.md"],
            "allowed_actions": list(allowed),
            "idempotency_key": key,
        }
    )["mission_id"]


def _events(host, mission_id, kind):
    out, after = [], 0
    while True:
        page = host.events(mission_id, after_seq=after, limit=200)
        items = page["events"]
        if not items:
            return [e for e in out if e["type"] == kind]
        out += items
        after = page.get("through_seq") or max(int(e["seq"]) for e in items)


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=300))


# ------------------------------------------------------------------ A01
def test_a_step_proposes_an_action_that_was_unknown_at_creation(tmp_path):
    async def case():
        photos = Photos()
        plan = [
            {
                "action_request": {
                    "connector": "photos",
                    "operation": "move",
                    "target": "/photos/giraffe-1.jpg",
                    "params": {"to": "giraffe-1.jpg"},
                    "reason": "搜索结果里的长颈鹿照片要归档",
                }
            },
        ]
        host = await _open(
            tmp_path, connectors=[photos], tools=[_search_tool(photos)], worker=_worker(plan)
        )
        try:
            mission_id = _mission(host)
            final = await host.run_until_settled(mission_id, timeout=240)
            assert final["snapshot"]["mission"]["status"] == "COMPLETED", final["snapshot"][
                "mission"
            ]
            # 模型真的调了搜索工具，再提了动作；假存储真的变了
            assert _events(host, mission_id, "ExternalToolCallSucceeded")
            assert photos.calls == [
                ("move", "/photos/giraffe-1.jpg", {"to": "/archive/giraffe-1.jpg"})
            ]
            assert (
                "/archive/giraffe-1.jpg" in photos.files
                and "/photos/giraffe-1.jpg" not in photos.files
            )
            [action] = host.step_actions(mission_id)
            assert action["state"] == "SUCCEEDED" and action["origin"] == "step"
            assert action["out_of_scope"] is False and action["params"] == {
                "to": "/archive/giraffe-1.jpg"
            }
            # 自动代批：决定者是主机，不是人
            decided = _events(host, mission_id, "ApprovalDecided") or _events(
                host, mission_id, "ApprovalGranted"
            )
            assert decided, [e["type"] for e in host.events(mission_id, limit=200)["events"]]
            # 续做时规划器没有被问"怎么修"
            assert not any(
                e["payload"].get("event_type") == "WorkerRejected"
                for e in _events(host, mission_id, "PlanningRepairRequested")
            )
            # 两次 prepare：提议前、交接前
            assert [phase for phase, _ in photos.prepared] == ["propose", "handoff"]
        finally:
            await host.close()

    _run(case())


# ------------------------------------------------------------------ A02
def test_prepare_normalises_params_and_a_mismatch_at_handoff_is_not_executed(tmp_path):
    async def case():
        photos = Photos()
        plan = [
            {
                "action_request": {
                    "connector": "photos",
                    "operation": "move",
                    "target": "/photos/giraffe-2.jpg",
                    "params": {"to": "x/giraffe-2.jpg"},
                    "reason": "归档",
                }
            },
        ]
        host = await _open(tmp_path, connectors=[photos], worker=_worker(plan))
        try:
            mission_id = _mission(host)
            # 让交接前的第二次整理和冻结的不一致
            original = photos.prepare

            def drifting(operation, target, params, *, context):
                out = original(operation, target, params, context=context)
                if context.phase == "handoff":
                    return {**out, "to": "/archive/elsewhere.jpg"}
                return out

            photos.prepare = drifting  # type: ignore[method-assign]
            final = await host.run_until_settled(mission_id, timeout=240)
            [action] = host.step_actions(mission_id)
            assert action["params"] == {"to": "/archive/giraffe-2.jpg"}  # 卡片上是规范化后的
            assert action["state"] == "FAILED" and action["error"] == "prepare_mismatch"
            assert photos.calls == []  # 没执行
            # 这一步照样续做并完成（剧本在看到 FAILED 结果后写文件）
            assert final["snapshot"]["mission"]["status"] == "COMPLETED"
        finally:
            await host.close()

    _run(case())


def test_an_out_of_scope_proposal_needs_a_person_even_in_auto_mode(tmp_path):
    async def case():
        photos = Photos()
        plan = [
            {
                "action_request": {
                    "connector": "photos",
                    "operation": "move",
                    "target": "/photos/cat.jpg",
                    "params": {"to": "cat.jpg"},
                    "reason": "顺手归档",
                }
            },
        ]
        host = await _open(tmp_path, connectors=[photos], worker=_worker(plan))
        try:
            mission_id = _mission(host, allowed=("photos.share",))  # move 不在名单里
            for _ in range(60):
                await host.drain(timeout=10)
                pending = [a for a in host.approvals(mission_id) if a["state"] == "PENDING"]
                if pending:
                    break
                await asyncio.sleep(0.05)
            [request] = pending
            assert request["person_required"] is True
            assert (
                request["summary"]["out_of_scope"] is True
                and request["summary"]["origin"] == "step"
            )
            assert photos.calls == []
            host.decide(request["request_id"], "approve", reason="看过了，可以")
            final = await host.run_until_settled(mission_id, timeout=240)
            assert final["snapshot"]["mission"]["status"] == "COMPLETED"
            assert [c[1] for c in photos.calls] == ["/photos/cat.jpg"]
        finally:
            await host.close()

    _run(case())


# ------------------------------------------------------------------ A03
def test_always_needs_approval_rejected_then_the_step_continues_with_the_fact(tmp_path):
    async def case():
        photos = Photos()
        plan = [
            {
                "action_request": {
                    "connector": "photos",
                    "operation": "share",
                    "target": "/photos/giraffe-1.jpg",
                    "params": {"with": "family"},
                    "reason": "分享",
                }
            },
        ]
        seen: list[dict[str, Any]] = []
        host = await _open(tmp_path, connectors=[photos], worker=_worker(plan, seen))
        try:
            mission_id = _mission(host, allowed=("photos.share",))
            provider = host.provider
            for _ in range(60):
                await host.drain(timeout=10)
                pending = [a for a in host.approvals(mission_id) if a["state"] == "PENDING"]
                if pending:
                    break
                await asyncio.sleep(0.05)
            [request] = pending
            asked_before = len(provider.asked)
            await host.drain(timeout=10)  # 等批准期间：没有模型调用
            assert len(provider.asked) == asked_before
            assert host.status()  # 宿主照常
            host.decide(request["request_id"], "reject", reason="不分享")
            final = await host.run_until_settled(mission_id, timeout=240)
            [action] = host.step_actions(mission_id)
            assert action["state"] == "REJECTED" and photos.calls == []
            assert final["snapshot"]["mission"]["status"] == "COMPLETED"
            # 下一次尝试的工作包里带着结构化结果与动作目录
            last = seen[-1]
            assert last["action_results"][0]["state"] == "REJECTED"
            # 驳回原因随结果交给下一次尝试（模型才知道该怎么改）
            assert last["action_results"][0]["decision_note"] == "不分享"
            assert any(a["operation"] == "share" for a in last["available_actions"]["actions"])
        finally:
            await host.close()

    _run(case())


# ------------------------------------------------------------------ A05
def test_a_lost_receipt_is_recovered_by_lookup_and_applied_once(tmp_path):
    async def case():
        photos = Photos()
        photos.lose_receipt_once = True
        plan = [
            {
                "action_request": {
                    "connector": "photos",
                    "operation": "move",
                    "target": "/photos/giraffe-1.jpg",
                    "params": {"to": "giraffe-1.jpg"},
                    "reason": "归档",
                }
            },
        ]
        host = await _open(tmp_path, connectors=[photos], worker=_worker(plan))
        try:
            mission_id = _mission(host)
            final = await host.run_until_settled(mission_id, timeout=240)
            [action] = host.step_actions(mission_id)
            assert action["state"] == "SUCCEEDED" and len(photos.calls) == 1
            assert _events(host, mission_id, "ActionOutcomeUnknown")
            assert any(
                e["payload"].get("verdict") == "COMPLETED"
                for e in _events(host, mission_id, "ActionReconciled")
            )
            assert final["snapshot"]["mission"]["status"] == "COMPLETED"
        finally:
            await host.close()

    _run(case())


# ------------------------------------------------------------------ A07
def test_an_at_most_once_click_that_times_out_waits_for_a_ruling_and_is_never_resent(tmp_path):
    async def case():
        desktop = Desktop()
        desktop.hang = True
        plan = [
            {
                "action_request": {
                    "connector": "desktop",
                    "operation": "click",
                    "target": "button:ok",
                    "params": {"x": 10, "y": 20},
                    "reason": "点确定",
                }
            },
        ]
        host = await _open(
            tmp_path,
            connectors=[desktop],
            worker=_worker(plan),
            connector_timeout_seconds=0.5,
        )
        try:
            mission_id = host.create_mission(
                {
                    "goal": "点确定，然后写 NOTES.md",
                    "success_criteria": ["file:NOTES.md"],
                    "allowed_actions": ["desktop.click"],
                    "idempotency_key": "m-click",
                }
            )["mission_id"]
            for _ in range(80):
                await host.drain(timeout=10)
                [action] = host.step_actions(mission_id) or [None]
                if action and action["state"] == "UNKNOWN" and action.get("needs_human"):
                    break
                await asyncio.sleep(0.1)
            assert action["state"] == "UNKNOWN" and action["needs_human"] is True
            assert action["handoffs"] == 1 and len(desktop.clicks) == 1
            waiting = (
                host.snapshot(mission_id)["snapshot"]["mission"].get("waiting_on") or host.status()
            )
            assert waiting  # 任务在等人
            host.resolve_unknown(action["action_key"], "succeeded", basis="看屏幕，点上了")
            final = await host.run_until_settled(mission_id, timeout=240)
            assert len(desktop.clicks) == 1  # 从未重发
            assert final["snapshot"]["mission"]["status"] == "COMPLETED"
        finally:
            await host.close()

    _run(case())


def test_cancelling_a_mission_cancels_unhanded_proposals_only(tmp_path):
    async def case():
        photos = Photos()
        plan = [
            {
                "action_request": {
                    "connector": "photos",
                    "operation": "share",
                    "target": "/photos/giraffe-1.jpg",
                    "params": {"with": "family"},
                    "reason": "分享",
                }
            },
        ]
        host = await _open(tmp_path, connectors=[photos], worker=_worker(plan))
        try:
            mission_id = _mission(host, allowed=("photos.share",))
            for _ in range(60):
                await host.drain(timeout=10)
                if [a for a in host.approvals(mission_id) if a["state"] == "PENDING"]:
                    break
                await asyncio.sleep(0.05)
            host.cancel(mission_id)
            await host.drain(timeout=10)
            [action] = host.step_actions(mission_id)
            assert action["state"] == "CANCELLED" and photos.calls == []
            assert all(a["state"] != "PENDING" for a in host.approvals(mission_id))
        finally:
            await host.close()

    _run(case())


def test_step_actions_do_not_consume_the_requirement_handoff_cap(tmp_path):
    """核验 2026-10-21：9 个中途动作超过要求动作的每任务交接上限（8）也照样执行；上限是它们自己的。"""

    async def case():
        photos = Photos()
        for i in range(9):
            photos.files.add(f"/photos/p{i}.jpg")
        plan = [
            {
                "action_request": {
                    "connector": "photos",
                    "operation": "move",
                    "target": f"/photos/p{i}.jpg",
                    "params": {"to": f"p{i}.jpg"},
                    "reason": f"归档第{i}张",
                }
            }
            for i in range(9)
        ]
        host = await _open(tmp_path, connectors=[photos], worker=_worker(plan))
        try:
            mission_id = _mission(host)
            final = await host.run_until_settled(mission_id, timeout=280)
            rows = host.step_actions(mission_id)
            assert [r["state"] for r in rows] == ["SUCCEEDED"] * 9, [
                (r["target"], r["state"], r.get("error")) for r in rows
            ]
            assert len(photos.calls) == 9
            assert final["snapshot"]["mission"]["status"] == "COMPLETED"
        finally:
            await host.close()

    _run(case())


# ------------------------------------------------------------------ 门口拒绝
@pytest.mark.parametrize(
    ("proposal", "fragment"),
    [
        (
            {
                "connector": "photos",
                "operation": "burn",
                "target": "x",
                "params": {},
                "reason": "r",
            },
            "unknown_operation",
        ),
        (
            {"connector": "nope", "operation": "move", "target": "x", "params": {}, "reason": "r"},
            "connector_not_enabled",
        ),
        (
            {
                "connector": "photos",
                "operation": "share",
                "target": "x",
                "params": {},
                "reason": "r",
            },
            "missing_params",
        ),
    ],
)
def test_a_proposal_the_door_refuses_is_reported_back_to_the_model(tmp_path, proposal, fragment):
    async def case():
        photos = Photos()
        host = await _open(
            tmp_path, connectors=[photos], worker=_worker([{"action_request": proposal}])
        )
        try:
            mission_id = _mission(host)
            final = await host.run_until_settled(mission_id, timeout=240)
            refused = _events(host, mission_id, "StepActionRefused")
            assert refused and refused[0]["payload"]["reason"].startswith(fragment)
            assert host.step_actions(mission_id) == [] and photos.calls == []
            assert final["snapshot"]["mission"]["status"] == "COMPLETED"
        finally:
            await host.close()

    _run(case())

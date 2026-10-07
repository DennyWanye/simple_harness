# SPDX-License-Identifier: Apache-2.0
"""推后第 3 批车道 R3：H10 执行者"申请更多资源"的通道（原文 §12.3、§15、§24 第 9 步）。

提案（结果信封里的 ``resource_request``）→ 核额度与上限（只核数）→ 交给规划器按现有修复路径判：
它选"原样重试"就是批准，下一次尝试的工具上限 = 基础额度 + 批准数，并有可审计的记录；不选就不给。
Harness 不判该不该给。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.orchestrator import resource_requests as rr


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


# ---------------------------------------------------------------- 提案的合同

def test_a_request_is_popped_from_the_block_and_checked_for_shape() -> None:
    raw = {"outcome": "blocked", rr.FIELD: {"dimension": "tool_calls", "amount": 5, "reason": "要读的文件太多"}}
    request = rr.pop_request(raw)
    assert rr.FIELD not in raw
    assert request is not None and request.to_json() == {"dimension": "tool_calls", "amount": 5,
                                                          "reason": "要读的文件太多"}
    assert rr.pop_request({"outcome": "blocked"}) is None


@pytest.mark.parametrize("bad", (
    {"dimension": "tokens", "amount": 5, "reason": "x"},        # token 预留本来按需增长，不申请
    {"dimension": "tool_calls", "amount": 0, "reason": "x"},
    {"dimension": "tool_calls", "amount": True, "reason": "x"},
    {"dimension": "tool_calls", "amount": 5, "reason": "  "},
    {"dimension": "tool_calls", "amount": 5, "reason": "x", "grant": True},
    "more please",
))
def test_a_malformed_request_is_a_contract_error(bad: Any) -> None:
    with pytest.raises(ContractError):
        rr.pop_request({rr.FIELD: bad})


# ---------------------------------------------------------------- 核额度：只核数

def test_the_check_is_only_numbers_against_the_ceiling_and_what_is_left() -> None:
    request = rr.ResourceRequest(dimension="tool_calls", amount=48, reason="x")
    assert rr.quota_check(request, base_cap=48, room=None) == {
        "base_cap": 48, "ceiling": 48, "available": None, "fits": True}
    over_ceiling = rr.ResourceRequest(dimension="tool_calls", amount=49, reason="x")
    assert rr.quota_check(over_ceiling, base_cap=48, room=None)["fits"] is False
    assert rr.quota_check(request, base_cap=48, room=95)["fits"] is False   # 48 + 48 > 95
    assert rr.quota_check(request, base_cap=48, room=96)["fits"] is True


# ---------------------------------------------------------------- 产品同形：申请 → 规划器重试 → 发放

def _blocked(request: Any, amount: int) -> str:
    from agent_orchestrator.testing.fixtures import package_of

    package = package_of(request)
    envelope = {
        "task_id": package.get("task_contract", {}).get("task_id", ""),
        "attempt_id": package.get("attempt", {}).get("attempt_id", ""),
        "outcome": "blocked", "summary": "工具次数不够，读不完所有文件",
        "claims": [], "evidence": [], "artifacts": [], "proposed_tasks": [], "used_knowledge": [],
        "risks": [], "cost": {"tool_calls": 0},
        rr.FIELD: {"dimension": "tool_calls", "amount": amount, "reason": "要读的文件比额度多"},
    }
    return "<result_envelope>" + json.dumps(envelope, ensure_ascii=False) + "</result_envelope>"


def _world(tmp_path: Any, *, retry: bool, amount: int = 5):
    from agent_orchestrator.testing.fixtures import package_of
    from agent_orchestrator.testing.product_world import product_world
    from agent_orchestrator.testing.scripted_replies import (
        LayeredScriptedProvider,
        decision,
        planner_reply,
        retry_same_method,
        worker_reply,
    )

    packages: list[dict[str, Any]] = []
    calls = {"worker": 0}

    def planner(request: Any) -> Any:
        package = package_of(request)
        if package.get("repair_requests"):
            packages.append(package)
            if retry:
                return retry_same_method(request)
            subject = package["planning_subjects"][0]["subject_key"]
            return decision(subject, "NO_CHANGE", {"reason": "不多给，先不改计划。"}, "不改计划。")
        return planner_reply(request)

    def worker(request: Any) -> Any:
        calls["worker"] += 1
        if calls["worker"] == 1:
            return _blocked(request, amount)
        return worker_reply(request)

    provider = LayeredScriptedProvider(planner=planner, worker=worker)
    return product_world(tmp_path / "root", provider), packages


def test_an_approved_request_raises_the_next_attempts_tool_cap_with_an_audit_trail(tmp_path: Any) -> None:
    async def case() -> dict[str, Any]:
        world_cm, packages = _world(tmp_path, retry=True)
        async with world_cm as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "r3-grant"})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=30)
            store = world.loop.store
            events = list(store.iter_events(mission_id))
            attempts = sorted((a for t in store.list_tasks(mission_id) for a in store.list_attempts(t.id)),
                              key=lambda a: a.created_at)
            configs = [store.get_intent_for_subject(a.id).config for a in attempts]
            return {"mission": mission, "events": events, "configs": configs, "packages": packages,
                    "base": world.loop._config.max_tool_calls_per_turn}

    seen = asyncio.run(case())
    assert str(seen["mission"].status) == "COMPLETED", seen["mission"].final_report
    requested = [e for e in seen["events"] if e.type == rr.REQUESTED]
    outcome = [e for e in seen["events"] if e.type == "OutcomeRecorded"]
    assert len(requested) == 1 and len(outcome) == 1
    assert requested[0].payload["request"] == {"dimension": "tool_calls", "amount": 5, "reason": "要读的文件比额度多"}
    assert requested[0].payload["check"]["fits"] is True
    assert requested[0].payload["result_id"] == outcome[0].payload.get("result_id", requested[0].payload["result_id"])
    # 同一个事务：两条事件挨着落库
    assert abs(int(requested[0].seq) - int(outcome[0].seq)) == 1
    # 修复请求的明细里带着申请与核的结果，规划器看得到
    contexts = [entry["request"]["context"] for package in seen["packages"]
                for entry in package["repair_requests"]]
    assert any(ctx.get("resource_request", {}).get("request", {}).get("amount") == 5 for ctx in contexts), contexts
    granted = [e for e in seen["events"] if e.type == rr.GRANTED]
    assert len(granted) == 1 and granted[0].payload["amount"] == 5
    first, second = seen["configs"][0], seen["configs"][1]
    assert first["max_tool_calls"] == seen["base"] and "resource_grant" not in first
    assert second["max_tool_calls"] == seen["base"] + 5
    assert second["resource_grant"]["amount"] == 5
    assert granted[0].attempt_id == second["attempt_id"]


def test_without_the_planners_retry_nothing_is_granted(tmp_path: Any) -> None:
    async def case() -> list[Any]:
        world_cm, packages = _world(tmp_path, retry=False)
        async with world_cm as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "r3-no-grant"})["mission_id"]
            for _ in range(12):
                await world.drain(timeout=20)
                if packages:
                    break
            await world.drain(timeout=20)
            return list(world.loop.store.iter_events(mission_id))

    events = asyncio.run(case())
    assert [e.type for e in events].count(rr.REQUESTED) == 1
    assert rr.GRANTED not in {e.type for e in events}


def test_a_request_with_a_candidate_result_is_refused_as_an_invalid_envelope(tmp_path: Any) -> None:
    from agent_orchestrator.testing.product_world import product_world
    from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, worker_reply

    calls = {"n": 0}

    def worker(request: Any) -> Any:
        reply = worker_reply(request)
        if isinstance(reply, str) and calls["n"] == 0:
            calls["n"] += 1
            body = json.loads(reply.removeprefix("<result_envelope>").removesuffix("</result_envelope>"))
            body[rr.FIELD] = {"dimension": "tool_calls", "amount": 5, "reason": "x"}
            return "<result_envelope>" + json.dumps(body, ensure_ascii=False) + "</result_envelope>"
        return reply

    async def case() -> list[Any]:
        async with product_world(tmp_path / "root", LayeredScriptedProvider(worker=worker)) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "r3-candidate"})["mission_id"]
            for _ in range(12):
                await world.drain(timeout=20)
                if any(e.type == "ResultRejected" for e in world.loop.store.iter_events(mission_id)):
                    break
            return list(world.loop.store.iter_events(mission_id))

    events = asyncio.run(case())
    rejected = [e for e in events if e.type == "ResultRejected"]
    assert rejected and rejected[0].payload["reason"] == "envelope_invalid", [e.payload for e in rejected]
    assert rr.REQUESTED not in {e.type for e in events}


# ---------------------------------------------------------------- 派发核对：冻结的发放必须有对应申请

def test_a_grant_without_a_matching_checked_request_is_refused() -> None:
    from types import SimpleNamespace

    def event(**payload: Any) -> Any:
        return SimpleNamespace(type=rr.REQUESTED, task_id="t1", attempt_id="a1", payload=payload)

    ok = event(request={"dimension": "tool_calls", "amount": 5, "reason": "x"},
               check={"fits": True, "base_cap": 48, "ceiling": 48, "available": None})
    grant = {"failed_attempt_id": "a1", "dimension": "tool_calls", "amount": 5}
    assert rr.verified_grant_amount([ok], task_id="t1", grant=grant, base_cap=48) == 5
    with pytest.raises(rr.GrantUnverified):
        rr.verified_grant_amount([], task_id="t1", grant=grant, base_cap=48)
    with pytest.raises(rr.GrantUnverified):  # 数量对不上
        rr.verified_grant_amount([ok], task_id="t1", grant={**grant, "amount": 6}, base_cap=48)
    with pytest.raises(rr.GrantUnverified):  # 核的时候没过
        unfit = event(request=ok.payload["request"], check={**ok.payload["check"], "fits": False})
        rr.verified_grant_amount([unfit], task_id="t1", grant=grant, base_cap=48)
    with pytest.raises(rr.GrantUnverified):  # 超过上限（基础额度）
        rr.verified_grant_amount([ok], task_id="t1", grant=grant, base_cap=4)
    with pytest.raises(rr.GrantUnverified):  # 别的步骤的申请
        rr.verified_grant_amount([ok], task_id="t2", grant=grant, base_cap=48)



# ---------------------------------------------------------------- 裁决第 4 件：用完工具次数时从拒绝话得知可申请

def test_the_tool_cap_refusal_tells_an_executor_how_to_ask_for_more(tmp_path: Any) -> None:
    """裁决 2026-10-07 第 4 件（偏离 #52）：不改模板、工具说明、上下文包；网关"工具次数用完"的拒绝话
    说明可在 blocked 结果里附 ``resource_request``，并给出上限数。审阅员那一支不变。

    注意（偏差单 R3-3）：产品路径上 SDK 的单回合上限与网关上限相同，回合在第 N+1 次调用前就被 SDK
    结束，执行者看不到这句话；这里在网关上直接核这句话本身。"""
    from agent_orchestrator.artifacts.workspace import WorkspaceManager
    from agent_orchestrator.runtime.tool_gateway import WorkspaceBinding, WorkspaceToolGateway
    from simple_harness.contracts import CallId
    from simple_harness.tools import ToolCall

    workspaces = WorkspaceManager(tmp_path / "ws")
    workspaces.create("m:task-1:attempt-1", seed={"a.md": "x"})
    gateway = WorkspaceToolGateway(workspaces)
    gateway.bind("run-1", WorkspaceBinding("m:task-1:attempt-1", "work", True, ("workspace_list",),
                                           max_tool_calls=2))

    async def case() -> Any:
        result = None
        for n in range(3):
            result = await gateway.execute(ToolCall(call_id=CallId(f"c{n}"), name="workspace_list",
                                                    arguments={}), {"run_id": "run-1"})
        return result

    third = asyncio.run(case())
    assert third.error_code == "tool_rate_limited"
    text = str(third.public_message)
    assert rr.FIELD in text and "blocked" in text and "tool_calls" in text and "2" in text, text
    assert "规划器" in text, text  # 给不给由规划器决定

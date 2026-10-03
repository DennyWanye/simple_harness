# SPDX-License-Identifier: Apache-2.0
"""人在审批卡上拒绝发布之后（2026-10-03 阶段 B 裁决第 2 类）。

此前：批准请求和动作改成"已拒绝"就完了——修复请求来源表里没有它，规划器收不到；系统代办看到
申请已进执行链就跳过；"操作效果待完成"仍被当成合法等待。任务一直停在"进行中"。

现在：人拒绝的理由是一段要理解的话（"标题要改成周报""先别发"），交给规划器——新的修复请求来源
"对外操作未生效"带上目标、理由原文、拒绝人、被拒几次、还剩几次。改内容、问人还是不改由它判断。
上限是秩序：每次拒绝只问一次；它回应了而内容（字节）没变就以"批准被拒"停；同一效果被拒
``SOURCE_REPAIR_CAP`` 次直接停；被拒过的同一份内容系统不再自动拿去问人。

**改坏检验**：修复请求来源表去掉"对外操作未生效"→ 第一条变红。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    one_step_method,
    planner_reply,
    worker_reply,
)

from test_operation import PUBLISH, TARGET, _confirm_completion, _pending_cards, _until_card

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
REASON = "标题要改成周报"


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


class _Provider(LayeredScriptedProvider):
    """规划器读到"对外操作未生效"的请求就换一个一步写完的做法（先提、审过再换）；
    ``rewrite`` 为真时，新做法那一步按理由改了内容，否则写出同样的字节。"""

    def __init__(self, *, rewrite: bool) -> None:
        self.rewrite = rewrite
        self.not_applied: list[dict[str, Any]] = []
        super().__init__(planner=self._planner, worker=self._worker)

    def _planner(self, request: Any) -> Any:
        package = package_of(request)
        repairs = [entry["request"] for entry in package.get("repair_requests") or ()
                   if (entry.get("request") or {}).get("trigger_source") == "OPERATION_NOT_APPLIED"]
        if not repairs:
            return planner_reply(request)
        self.not_applied.append(repairs[0]["context"])
        [goal] = [item for item in package["views"]["goals"] if item.get("under_repair") and item.get("adopted_method")]
        subject = next(item["subject_key"] for item in package["planning_subjects"]
                       if item["occurrence_id"] == goal["occurrence_id"])
        current = goal["adopted_method"]["method_ref"]
        alternatives = [item["method_ref"] for item in package["views"]["methods"]
                        if item["method_ref"] != current
                        and any(report["verdict"] == "APPLICABLE" and report["goal_occurrence_id"] == goal["occurrence_id"]
                                for report in item.get("applicability", ()))]
        if not alternatives:
            [context] = [item for item in package["method_proposal_contexts"] if item["subject_key"] == subject]
            return decision(subject, "PROPOSE_METHOD",
                            {"method_proposal": {"method": one_step_method(context), "rationale": "按理由重写。"}},
                            "用户拒绝了发布，按理由重写内容。")
        instance = next(item for item in package["visible_refs"]
                        if item["kind"] == "method_instance" and item["id"] == goal["adopted_method"]["method_instance_id"])
        return decision(subject, "REPAIR", {"repair_kind": "REPLACE_METHOD", "rejected_method_instance": instance,
                                            "replacement_method_ref": dict(alternatives[0]), "bindings": goal["params"]},
                        "换成按理由重写的做法。")

    def _worker(self, request: Any) -> Any:
        reply = worker_reply(request)
        if self.rewrite and self.not_applied and isinstance(reply, tuple):
            name, args = reply
            return name, {**args, "content": "# 周报\n\n- 一\n- 二\n- 三\n"}
        return reply


async def _drive_until(world: Any, predicate: Any, *, rounds: int = 60) -> None:
    for _ in range(rounds):
        if predicate():
            return
        await world.drain(timeout=5)
    assert predicate()


def _status(world: Any, mission_id: str) -> str:
    return str(world.store.get_mission(mission_id).status.value)


async def _rejected_once(tmp_path: Any, provider: _Provider) -> Any:
    published = tmp_path / "published"
    published.mkdir()
    connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
    policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
    world_cm = product_world(tmp_path / "root", provider, connectors={"file_publish": connector},
                             deployment_policy=policy)
    world = await world_cm.__aenter__()
    mission_id = world.create({"goal": "写一份周报 reports/weekly.md 并发布",
                               "success_criteria": ["file:" + TARGET, PUBLISH],
                               "idempotency_key": "rejected-" + str(provider.rewrite)})["mission_id"]
    await world.drain()
    _confirm_completion(world, mission_id)
    card = await _until_card(world, mission_id, 0)
    world.control.decide(card["request_id"], "reject", reason=REASON)
    return world_cm, world, mission_id, published


def _new_card(world: Any, mission_id: str) -> bool:
    return bool(_pending_cards(world, mission_id)) and len(world.store.list_actions(mission_id)) > 1


def test_a_rejected_publish_goes_to_the_planner_with_the_reason(tmp_path):
    async def case():
        provider = _Provider(rewrite=True)
        world_cm, world, mission_id, published = await _rejected_once(tmp_path, provider)
        try:
            await _drive_until(world, lambda: _new_card(world, mission_id) or _status(world, mission_id) in TERMINAL)
            assert provider.not_applied, "the planner was never told"
            context = provider.not_applied[0]
            assert context["rejection_reason"] == REASON and context["target"] == TARGET
            assert context["rejections"] == 1 and context["remaining"] == 1
            [card] = _pending_cards(world, mission_id)
            world.control.decide(card["request_id"], "approve")
            await _drive_until(world, lambda: _status(world, mission_id) in TERMINAL)
            assert _status(world, mission_id) == "COMPLETED"
            [path] = list(published.rglob("*.md"))
            assert path.read_text(encoding="utf-8").startswith("# 周报")
        finally:
            await world_cm.__aexit__(None, None, None)

    asyncio.run(case())


def test_a_publish_rejected_twice_stops_as_approval_rejected(tmp_path):
    async def case():
        provider = _Provider(rewrite=True)
        world_cm, world, mission_id, published = await _rejected_once(tmp_path, provider)
        try:
            await _drive_until(world, lambda: _new_card(world, mission_id) or _status(world, mission_id) in TERMINAL)
            [card] = _pending_cards(world, mission_id)
            world.control.decide(card["request_id"], "reject", reason="还是先别发")
            await _drive_until(world, lambda: _status(world, mission_id) in TERMINAL)
            mission = world.store.get_mission(mission_id)
            assert str(mission.stop_reason) == "approval_rejected"
            detail = mission.final_report["detail"]
            assert detail["reason"] == "operation_rejected"
            assert [item["reason"] for item in detail["rejections"]] == [REASON, "还是先别发"]
            # the planner read the first rejection (proposing, then replacing); the second one is the
            # cap: it was never asked about it
            assert {item["rejections"] for item in provider.not_applied} == {1}
            assert not list(published.rglob("*.md"))
        finally:
            await world_cm.__aexit__(None, None, None)

    asyncio.run(case())


def test_a_planner_answer_that_leaves_the_content_unchanged_stops(tmp_path):
    """规划器回应了（换了做法），新做法写出的字节和被拒的那份一样：不拿同一份内容再去问人。"""

    async def case():
        provider = _Provider(rewrite=False)
        world_cm, world, mission_id, published = await _rejected_once(tmp_path, provider)
        try:
            await _drive_until(world, lambda: _status(world, mission_id) in TERMINAL)
            mission = world.store.get_mission(mission_id)
            assert str(mission.stop_reason) == "approval_rejected"
            assert "内容未变" in mission.final_report["detail"]["explanation"]
            assert len(world.store.list_actions(mission_id)) == 1  # no second card for the same bytes
            assert not list(published.rglob("*.md"))
        finally:
            await world_cm.__aexit__(None, None, None)

    asyncio.run(case())

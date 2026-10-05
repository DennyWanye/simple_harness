# SPDX-License-Identifier: Apache-2.0
"""任务跑到一半资料换了版本（2026-10-05 真机 ``mission-d507…`` 与随后的裁决）。

真机上：三步都通过或在跑时资料换版，任务没有重做，旧版内容照样判完成——系统只把"结果里引用了这份
资料"的步骤算受影响（普通任务的执行者不写引用），审阅员手里也没有资料正文。现在：凡是派发时挂着旧版
的已通过步骤，连同新旧差异，一条请求交给规划器判；它判"不受影响"也是一种了结；终审等这件事问完再开；
审阅员的审查包写明执行者用的版本与现行版本，现行版本的正文在它的证据里。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.orchestrator.planning_repair_requests import (
    ADDRESSED,
    REQUESTED,
    SOURCE_CHANGE_ASSESSED,
)
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    REVIEWER,
    LayeredScriptedProvider,
    decision,
    planner_reply,
    review_input,
    reviewer_reply,
)

PATH = "sources/spec.md"
OLD, NEW = "# 规格\n价格：每月 18 元\n", "# 规格\n价格：每月 25 元\n"


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _events(world: Any, mission_id: str, event_type: str) -> list[Any]:
    return [e for e in world.store.list_events(mission_id) if e.type == event_type]


def _source_requests(world: Any, mission_id: str) -> list[Any]:
    return [e for e in _events(world, mission_id, REQUESTED) if str(e.payload["source_key"]).startswith("source:")]


def _supersede(world: Any, mission_id: str, expected: str) -> dict[str, Any]:
    proposal = world.control.supersede_source({
        "mission_id": mission_id, "path": PATH, "content": NEW, "kind": "markdown",
        "idempotency_key": "sup-spec-1", "expected_version_hash": expected})
    world.control.decide(proposal["request_id"], "approve", nonce="approve-sup-1")
    return proposal


def _source_evidence(review: dict[str, Any]) -> list[str]:
    return [row["content"] for row in review["evidence"] if row["ref"]["kind"] == "source"]


def test_a_source_replaced_under_an_accepted_step_is_put_to_the_planner_and_the_final_review_waits(tmp_path):
    """一步拿着第 1 版资料在跑时资料换成第 2 版，这一步随后通过验收。

    - 跑着的时候不问（等它跑完）；跑完后**一条**请求交给规划器：这一步挂的是旧版、没有引用过、新旧差异；
      不记"评估过、没影响"。
    - 规划器被问到的那一刻任务没有判完成、终审还没开。
    - 规划器答"不改，这一步不受影响"：请求了结，之后才终审、任务完成。
    - 这一步的审阅员看到两个版本号不同，证据里是现行版正文；终审的证据里也有现行版正文。

    **改坏检验**：只把引用过资料的结果算受影响（SRC-01）→ 不发请求 → 变红；终审不等这件事问完
    （SRC-02）→ 规划器被问到时终审已开 → 变红；"不改"不能了结这类请求（SRC-03）→ 任务到不了完成 → 变红。"""
    seen: dict[str, Any] = {"requests": [], "reviews": [], "answered": False}

    def planner(request: Any):  # type: ignore[no-untyped-def]
        package = package_of(request)
        sourced = [entry for entry in package.get("repair_requests") or ()
                   if str(entry.get("source_key")).startswith("source:")]
        if not sourced:
            return planner_reply(request)
        seen["requests"].append(sourced[0])
        if not seen["answered"]:
            return None  # 第一次被问到：测试先看一眼当时的状态
        step = sourced[0]["request"]["context"]["steps_on_old_version"][0]["task_id"]
        subject = next(s["subject_key"] for s in package["planning_subjects"] if s["task_id"] == step)
        return decision(subject, "NO_CHANGE", {"reason": "这一步写的是说明的结构，没有用到价格。"},
                        "资料只改了价格，这一步的产出不涉及价格。")

    def reviewer(request: Any):  # type: ignore[no-untyped-def]
        seen["reviews"].append(review_input(request))
        return reviewer_reply(request)

    async def case():
        provider = LayeredScriptedProvider(planner=planner, reviewer=reviewer)
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "按规格写一份说明", "success_criteria": ["file:notes/a.md"],
                                       "idempotency_key": "source-change-1"})["mission_id"]
            first = world.control.register_source({"mission_id": mission_id, "path": PATH, "content": OLD,
                                                   "kind": "markdown", "idempotency_key": "reg-spec-1"})
            for _ in range(20):
                await world.drain(timeout=5)
                if provider.entered.is_set():
                    break
            assert provider.entered.is_set()
            proposal = _supersede(world, mission_id, first["version_hash"])
            await world.drain(timeout=5)
            # 拿着旧版的尝试还在跑：不问、也不记"没影响"
            assert not _source_requests(world, mission_id) and not _events(world, mission_id, SOURCE_CHANGE_ASSESSED)

            provider.release.set()
            for _ in range(20):
                try:
                    await world.drain(timeout=10)
                except AssertionError:
                    pass
                if seen["requests"]:  # 规划器第一次被问到资料变更（脚本这一次不作答）
                    break
            assert len(_source_requests(world, mission_id)) == 1
            [asked] = _source_requests(world, mission_id)
            context = asked.payload["request"]["context"]
            [step] = context["steps_on_old_version"]
            assert (step["status"], step["cited"]) == ("ACCEPTED", False)
            assert asked.payload["request"]["trigger_refs"] == [step["result_id"]]
            assert (context["reason"], context["path"]) == ("source_superseded", PATH)
            assert (context["old_version"], context["new_version"]) == (first["version_hash"], proposal["version_hash"])
            assert "-价格：每月 18 元" in context["diff_excerpt"] and "+价格：每月 25 元" in context["diff_excerpt"]
            assert seen["requests"][0]["request"]["context"] == context  # 规划器看到的就是这些
            assert not _events(world, mission_id, SOURCE_CHANGE_ASSESSED)
            # 这时任务没有判完成，终审也没开：只有那一步的内容审查发生过
            assert str(world.store.get_mission(mission_id).status.value) == "ACTIVE"
            assert not _events(world, mission_id, "GoalResolutionCommitted")
            leaf_reviews = [r for r in seen["reviews"] if r and r["package"].get("source_versions")]
            assert [r["package"]["purpose"] for r in seen["reviews"] if r] == ["METHOD_PLAN", "TASK_CONTENT"]
            [leaf] = leaf_reviews
            assert leaf["package"]["source_versions"] == [{
                "path": PATH, "used_version": first["version_hash"], "current_version": proposal["version_hash"]}]
            assert _source_evidence(leaf) == [NEW]

            seen["answered"] = True
            status = ""
            for _ in range(30):
                await world.drain(timeout=20)
                status = str(world.store.get_mission(mission_id).status.value)
                if status in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
            assert status == "COMPLETED"
            [addressed] = [e for e in _events(world, mission_id, ADDRESSED)
                           if asked.payload["request_id"] in e.payload["repair_request_ids"]]
            assert (addressed.payload["decision_type"], addressed.payload["status"]) == ("NO_CHANGE", "NO_STATE_CHANGE")
            [resolved] = _events(world, mission_id, "GoalResolutionCommitted")
            assert addressed.seq < resolved.seq
            assert len(_source_requests(world, mission_id)) == 1
            final = [r for r in seen["reviews"] if r and r["package"]["purpose"] == "MISSION_FINAL"]
            assert final and all(_source_evidence(r) == [NEW] for r in final)

    asyncio.run(case())

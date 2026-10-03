# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 7 · slice D (D7-9', S7-07): takeover and comments — a person's word is kept as
HumanOverride with its basis, and it never widens what was authorised.

删旧平面模式 第三刀：两条"任务级审阅员与全局裁判意见相左 → 交给人"的平面整圈测试随平面删。
HTN 补齐阶段 A′：跑在产品同形世界上（``helpers_step07.ledger_world``：主循环真跑到第一版计划提交，
叶子的执行者正在跑、模型调用被扣住），人经用户门面接管与评论。原来三条接管用例（停止把依据记账、
带备注重试留在本步、接管不复活已结束的任务）合并为参数化主循环用例
:func:`test_s7_07_a_takeover_keeps_its_basis_and_never_revives_or_widens`；"已完成的步骤不能接管"
改由"停止后的任务与步骤不能再接管"守住（产品同形世界里步骤完成要走完审阅整圈，另见【整圈】）。"""

from __future__ import annotations

import asyncio
import json

import pytest
from helpers_step07 import candidate, ledger_world, run_until

from agent_orchestrator.api.facade import FacadeError
from agent_orchestrator.contracts import AttemptStatus, MissionStatus, TaskStatus
from agent_orchestrator.testing.scripted_replies import worker_reply

NOTE = "先读 docs/SPEC.md"


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _running(w):
    [attempt] = [a for a in w.store.list_attempts(w.leaf.id) if a.status is AttemptStatus.RUNNING]
    return attempt


@pytest.mark.parametrize("action", ("stop", "retry_with_note"))
def test_s7_07_a_takeover_keeps_its_basis_and_never_revives_or_widens(tmp_path, action):
    asked: list[str] = []

    def worker(request):
        asked.append(json.dumps([str(getattr(m, "content", "")) for m in request.messages], ensure_ascii=False))
        return worker_reply(request)

    async def case():
        async with ledger_world(tmp_path, worker=worker) as w:
            attempt = _running(w)  # the executor is working on the leaf right now (its model call held)
            budget = w.leaf.budget.max_attempts
            principal = w.product.deployment.principal.principal_id
            if action == "stop":
                record = w.control.takeover(w.leaf.id, "stop", basis="卡住 40 分钟没有进展")
                assert record["action"] == "takeover_stop"
                assert "no approval, tool, budget" in record["scope"]
                final = w.mission
                assert final.status is MissionStatus.FAILED and final.stop_reason == "human_override"
                assert final.final_report["detail"]["basis"] == "卡住 40 分钟没有进展"
                assert w.store.get_attempt(attempt.id).status is AttemptStatus.CANCELLED
                [event] = [e for e in w.store.list_events(w.mission_id) if e.type == "HumanOverride"]
                assert (event.actor_type, event.actor_id) == ("user", principal)
                # a takeover never revives an ended task or Mission
                for task in (w.leaf, w.root):
                    with pytest.raises(FacadeError) as refused:
                        w.control.takeover(task.id, "retry_with_note", basis="再来一次", note=NOTE)
                    assert refused.value.code == "refused"
                assert w.mission.status is MissionStatus.FAILED
                return
            w.control.takeover(w.leaf.id, "retry_with_note", basis="方向不对", note=NOTE)
            closed = w.store.get_attempt(attempt.id)
            assert closed.status is AttemptStatus.CANCELLED
            assert closed.failure["reason"] == "human_retry_with_note"
            [comment] = [e for e in w.store.list_events(w.mission_id) if e.type == "HumanCommentAdded"]
            assert comment.payload["target_id"] == w.leaf.id and comment.payload["text"] == NOTE
            assert w.mission.status is MissionStatus.ACTIVE
            task = w.store.get_task(w.leaf.id)
            assert task.status is TaskStatus.ACTIVE and task.budget.max_attempts == budget  # nothing widened
            with pytest.raises(FacadeError):  # only stop / retry_with_note exist
                w.control.takeover(w.leaf.id, "approve", basis="x")
            with pytest.raises(FacadeError):  # looks like a secret
                w.control.takeover(w.leaf.id, "stop", basis="sk-" + "a" * 40)
            # the retry stays within this step: the next Attempt is on the same leaf and its
            # executor is told the person's note
            w.provider.held.clear()
            w.provider.release.set()
            await run_until(w.product, lambda: len(asked) >= 2)
            attempts = w.store.list_attempts(w.leaf.id)
            assert [a.id for a in attempts][0] == attempt.id and len(attempts) == 2
            assert NOTE in asked[-1] and NOTE not in asked[0]
            assert {t.id for t in w.store.list_tasks(w.mission_id)} == {w.leaf.id, w.root.id}

    asyncio.run(case())


def test_comments_are_kept_as_data_on_a_task_or_a_request(tmp_path):
    async def case():
        async with ledger_world(tmp_path) as w:
            action = w.propose(candidate())
            w.control.comment(action["approval_request_id"], "上线窗口在周四")
            w.control.comment(w.leaf.id, "注意兼容旧客户端")
            request = w.store.get_approval(action["approval_request_id"])
            assert [c["text"] for c in request["comments"]] == ["上线窗口在周四"]
            assert request["state"] == "PENDING"  # a comment decides nothing
            texts = [e.payload["text"] for e in w.store.list_events(w.mission_id) if e.type == "HumanCommentAdded"]
            assert texts == ["上线窗口在周四", "注意兼容旧客户端"]
            assert w.store.get_action(action["action_key"])["state"] == "AWAITING_APPROVAL"

    asyncio.run(case())

# SPDX-License-Identifier: Apache-2.0
"""检查结果晚到：等着的审阅被它唤醒，不再调一次模型（推后第 1 批车道 P1a，A25 第 2 行）。

原计划事件消费表 ACTUAL_CHECK_AVAILABLE → REVIEW："已有的待办审阅槽按精确检查重评；不自动重复调模型"。
此前审阅导入只取当下已有的检查绑定：缺一项 → 检查门 UNKNOWN → 记"判不下来" → 换会话复审，第二次调模型。

产品同形世界造局：

* 规划器的一步做法把用户那条要求挂到这一步自己的准则 ``c-notes-written`` 上，这一步的内容审阅因此
  按它的检查层（格式、规则、测试）必检（CHECKED）；
* 外界只扣住"检查绑定写进库"这一个时机——等内容审阅回复的导入准备过一次以后再放行（检查导入方
  自己的写入口，屏障照常写资料变更事件）。

期望：

* 导入先记 ``CHECK_PENDING`` 等着，不导入、不复审；
* 检查绑定写入同一事务里屏障触发器写的资料变更事件（``source_table=assurance_check_bindings``）
  把同一项导入工作唤醒（最后一次领取的触发事件就是它），按精确检查导入；不另有检查事件
  （P1a 偏差裁决第 3 件）；
* 内容审阅只调一次模型，任务完成、验收一次。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _review_world import ReviewScript, quick_waits, reviewed_mission  # noqa: E402

import agent_orchestrator.testing.scripted_replies as scripted  # noqa: E402
from agent_orchestrator.orchestrator.assurance_review_consumer import AssuranceReviewConsumer  # noqa: E402
from agent_orchestrator.orchestrator.assurance_tick import AssuranceTick, AssuranceWait  # noqa: E402
from agent_orchestrator.orchestrator.commit_service import CommitService  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    quick_waits(monkeypatch)


def test_a_late_check_wakes_the_waiting_review_without_a_second_model_call(tmp_path, monkeypatch):
    state: dict = {"defer": True, "held": [], "prepared": [], "released": False}
    bind, prepare, tick = (CommitService.import_assurance_check_locked,
                           AssuranceReviewConsumer.prepare, AssuranceTick.tick)
    one_step = scripted.one_step_method

    def own_criterion_step(context):  # type: ignore[no-untyped-def]
        method = one_step(context)
        for link in method["composition"]["criterion_links"]:
            link["child_criterion_id"] = "c-notes-written"
        return method

    def content_review(store, work_key):  # type: ignore[no-untyped-def]
        review_key = work_key.removeprefix("review-import:").rpartition(":")[0]
        row = store.connection.execute(
            "SELECT json_extract(binding_json,'$.subject.purpose'), json_extract(binding_json,'$.check_requirements') "
            "FROM assurance_review_bindings WHERE review_key=?", (review_key,)).fetchone()
        return row is not None and row[0] == "TASK_CONTENT" and '"CHECKED"' in row[1]

    def held_bind(self, **command):  # type: ignore[no-untyped-def]
        # 外界时机：检查已跑完、结果已记，绑定这一步晚到
        if state["defer"]:
            state["held"].append((self, command))
            return None
        return bind(self, **command)

    async def watched_prepare(self, claim):  # type: ignore[no-untyped-def]
        result = await prepare(self, claim)
        if claim.work_key.startswith("review-import:") and content_review(self.store, claim.work_key):
            state["prepared"].append(result.reason if isinstance(result, AssuranceWait) else type(result).__name__)
        return result

    async def timed_tick(self):  # type: ignore[no-untyped-def]
        progressed = await tick(self)
        if state["prepared"] and not state["released"]:
            # 审阅回复的导入已准备过一次：放行扣住的检查绑定（检查导入方自己的写入口）
            state["released"], state["defer"] = True, False
            for commit, command in state["held"]:
                with commit.store.transaction():
                    bind(commit, **command)
        return progressed

    monkeypatch.setattr(scripted, "one_step_method", own_criterion_step)
    monkeypatch.setattr(CommitService, "import_assurance_check_locked", held_bind)
    monkeypatch.setattr(AssuranceReviewConsumer, "prepare", watched_prepare)
    monkeypatch.setattr(AssuranceTick, "tick", timed_tick)
    provider = ReviewScript()

    async def run() -> None:
        async with reviewed_mission(tmp_path, provider) as case:
            mission = await case.settle()
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            assert state["held"], "造局失效：检查绑定没有被扣住"
            assert provider.review_calls["TASK_CONTENT"] == 1
            assert not case.events("AssuranceReviewSecondOpinionRequested")
            assert len(case.events("AcceptanceCommitted")) == 1
            assert state["prepared"][0] == "CHECK_PENDING", state["prepared"]
            rows = case.store.connection.execute(
                "SELECT w.work_key, w.state, e.type, json_extract(e.payload_json,'$.source_table') "
                "FROM assurance_pending_work w JOIN events e "
                "ON e.event_id=w.trigger_event_id WHERE w.mission_id=? AND w.consumer='REVIEW' "
                "AND w.work_key LIKE 'review-import:%'", (case.mission_id,)).fetchall()
            [row] = [tuple(row[1:]) for row in rows if content_review(case.store, row[0])]
            # 同一项导入工作被检查绑定的屏障事件唤醒后做完（不是到点重看，也没有另起一项）
            assert row == ("DONE", "AssuranceEvidenceChanged", "assurance_check_bindings")

    asyncio.run(run())

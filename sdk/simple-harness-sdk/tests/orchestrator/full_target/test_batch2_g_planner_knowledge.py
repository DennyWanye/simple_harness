# SPDX-License-Identifier: Apache-2.0
"""第 2 批 K03：规划器能读黑板（原计划 §11 压缩循环"下一轮 Agent 使用"、§24 第 12 步 "Manager 重新判断
方向要看到全局视图"）。

做法二选一里选的是"规划包加一节"，不是给规划器工具：规划器回合是一次性的决定回合——模板
``tool_names=()``、每回合最多一次工具调用、没有网关绑定，整个请求包有哈希、``visible_refs`` 要能
重算；给它工具就得把它改成多回合、破坏"一轮回复只提出一个决定"的协议。所以黑板作为第十二个
视图 ``views.knowledge`` 进包：现在仍然当前的已验证知识（与读工具同一个判定）和审阅员核对过的
步骤摘要，按任务范围、按有效性过滤、有上限、超限计入 omitted_counts；规划器提示词说明怎么读。

**改坏检验**：规划视图读取不再装 ``knowledge``（改成空列表）→ 第三条变红。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from agent_orchestrator.planning.htn.planner_package import (
    MAX_KNOWLEDGE,
    VIEW_NAMES,
    knowledge_rows,
)
from agent_orchestrator.runtime.role_templates import (
    PLANNER_HIERARCHICAL,
    PLANNING_DECISION_PACKAGE_VERSION,
    hierarchical_planner_pairing_is_valid,
)


def _record(kid: str, created: float, **extra: Any) -> Any:
    fields: dict[str, Any] = dict(
        id=kid, version=1, key=f"k.{kid}", stance="affirms", verifier={"basis": "review_confirmed"},
        content=f"结论 {kid}", source_task="t1", created_at=created, assurance_level=None,
        validity_interval=None, permitted_uses=None)
    fields.update(extra)
    return SimpleNamespace(**fields)


def _summary(sid: str) -> dict[str, Any]:
    return {"layer": "summary", "id": sid, "source_task": "t1", "summary": f"摘要 {sid}",
            "summary_sha256": "x", "result_ref": "y", "artifacts": [{"path": "NOTES.md", "version": 1, "content_hash": "h"}],
            "checked_by": "assurance-review:1"}


def test_knowledge_is_the_twelfth_view_and_the_prompt_is_the_new_pair() -> None:
    assert VIEW_NAMES[-1] == "knowledge" and len(VIEW_NAMES) == 12
    # 改了包或提示词就换版本号：包 16 配提示词 v30（2026-10-09 第 4 条：集合端口可接多个上游的写法），旧对不再合法
    assert PLANNING_DECISION_PACKAGE_VERSION == 16
    assert PLANNER_HIERARCHICAL.prompt_version == "planner-hierarchical-v30"
    assert hierarchical_planner_pairing_is_valid("planner-hierarchical-v30", 16)
    assert not hierarchical_planner_pairing_is_valid("planner-hierarchical-v29", 16)
    prompt = PLANNER_HIERARCHICAL.instructions
    assert "views.knowledge：" in prompt and "layer=verified" in prompt and "layer=summary" in prompt
    assert "omitted_counts.knowledge" in prompt and "不要写进 reason_refs" in prompt
    # 规划器仍然没有工具：黑板是作为一节进包的
    assert PLANNER_HIERARCHICAL.tool_names == ()


def test_rows_lead_with_verified_knowledge_newest_first_then_summaries_and_are_capped() -> None:
    records = [_record("k-old", 1.0, assurance_level="ASSURANCE_1_1",
                       validity_interval={"valid_from_ms": 1, "valid_until_ms": None, "mission_epoch": 2}),
               _record("k-new", 5.0)]
    rows, omitted = knowledge_rows(records, [_summary("sum:r1"), _summary("sum:r2")])
    assert omitted == 0
    assert [(row["layer"], row["id"]) for row in rows] == [
        ("verified", "k-new"), ("verified", "k-old"), ("summary", "sum:r1"), ("summary", "sum:r2")]
    verified = rows[1]
    assert verified["ref"] == "k-old@1" and verified["content"] == "结论 k-old" and verified["basis"] == "review_confirmed"
    assert verified["assurance_level"] == "ASSURANCE_1_1" and verified["validity_interval"]["mission_epoch"] == 2
    assert rows[0]["assurance_level"] is None and rows[0]["permitted_uses"] is None
    summary = rows[2]
    assert summary["summary"] == "摘要 sum:r1" and summary["artifacts"] == [{"path": "NOTES.md", "version": 1}]
    assert summary["checked_by"] == "assurance-review:1" and "summary_sha256" not in summary
    # 这些行不带规划引用四元组
    assert not any(set(row) & {"observation_ref", "acceptance_ref", "method_ref"} for row in rows)

    many = [_record(f"k-{i:02d}", float(i)) for i in range(MAX_KNOWLEDGE + 3)]
    rows, omitted = knowledge_rows(many, [_summary("sum:r1")])
    assert len(rows) == MAX_KNOWLEDGE and omitted == 4
    assert rows[0]["id"] == f"k-{MAX_KNOWLEDGE + 2:02d}"  # the newest survive the cap


def _candidate(cid: str, status: str = "PROPOSED", marker: str = "未验证") -> dict[str, Any]:
    return {"id": cid, "status": status, "marker": marker, "key": f"k.{cid}", "content": f"线索 {cid}",
            "source_task": "t1", "evidence": ["e1"]}


def test_a_very_long_candidate_is_shown_as_a_bounded_preview_that_says_it_was_cut() -> None:
    """opt.167 发版评估建议第 5 条：候选结论一行带全文，单条很长会撑大规划包。与目录一样给
    正文设上限，截断时在末尾如实写明；短的原样不动。"""
    from agent_orchestrator.planning.htn.planner_package import CANDIDATE_CONTENT_LIMIT

    long = dict(_candidate("c-long"), content="很长的线索。" * 2000)
    rows, _ = knowledge_rows([], [], [long, _candidate("c-short")])
    cut, short = rows
    assert cut["content"].startswith(long["content"][:CANDIDATE_CONTENT_LIMIT])
    assert len(cut["content"]) < CANDIDATE_CONTENT_LIMIT + 60
    assert cut["content"].endswith(f"（候选结论共 {len(long['content'])} 字，这里只列前 {CANDIDATE_CONTENT_LIMIT} 字）")
    assert short["content"] == "线索 c-short"


def test_candidates_come_last_marked_as_leads_and_the_cap_drops_them_first() -> None:
    """夜间 N3-03：候选结论是第三层，排在已验证与摘要之后；16 行上限先裁掉候选。"""
    rows, omitted = knowledge_rows([_record("k-1", 1.0)], [_summary("sum:r1")],
                                   [_candidate("c-1"), _candidate("c-2", "DISPUTED", "有争议，不是事实")])
    assert omitted == 0
    assert [(row["layer"], row["id"]) for row in rows] == [
        ("verified", "k-1"), ("summary", "sum:r1"), ("candidate", "c-1"), ("candidate", "c-2")]
    assert rows[3] == {"layer": "candidate", "id": "c-2", "status": "DISPUTED", "marker": "有争议，不是事实",
                       "key": "k.c-2", "content": "线索 c-2", "source_task": "t1"}
    assert "ref" not in rows[2]  # 候选不可引用
    many = [_record(f"k-{i:02d}", float(i)) for i in range(MAX_KNOWLEDGE)]
    rows, omitted = knowledge_rows(many, [], [_candidate("c-1")])
    assert omitted == 1 and all(row["layer"] == "verified" for row in rows)
    prompt = PLANNER_HIERARCHICAL.instructions
    assert "layer=candidate" in prompt and "candidate 层是线索不是事实，不能当已验证依据" in prompt


# --------------------------------------------------------------------- the real package
from agent_orchestrator.testing.product_world import product_world  # noqa: E402
from agent_orchestrator.testing.scripted_replies import (  # noqa: E402
    LayeredScriptedProvider,
    review_input,
    review_reply,
    worker_reply,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _labels(data: dict[str, Any], kind: str) -> list[str]:
    return [item["label"] for item in data.get("evidence", ()) if item["ref"]["kind"] == kind]


def _confirming_reviewer(request: Any) -> Any:
    """Content reviews confirm the worker's first claim against the artifact; summaries are faithful."""
    data = review_input(request)
    if data is None:
        return None
    inner = data.get("package") or {}
    if str(inner.get("purpose")) != "TASK_CONTENT":
        return review_reply(data)
    body = json.loads(review_reply(data, summary={"faithful": True, "reason": "摘要与结果一致。"}))
    listed = inner.get("claims_to_confirm") or []
    if listed:
        body["claims"] = [{"claim_id": listed[0]["claim_id"], "confirmed": True,
                           "evidence_ids": _labels(data, "artifact")[:1], "reason": "文件确实写出，内容与结论一致。"}]
    return json.dumps(body, ensure_ascii=False)


def test_the_planner_package_of_a_mission_with_knowledge_lists_it_and_the_checked_summary(tmp_path):
    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(
                worker=worker_reply, reviewer=_confirming_reviewer)) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "kn-planner"})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert mission.status.value == "COMPLETED", mission.final_report
            store = world.store
            records = store.list_knowledge(mission_id)
            assert records, "the confirmed claim should have become knowledge"
            loop = world.loop
            mission = store.get_mission(mission_id)
            package = loop._hierarchical_planner_package(loop._new_mode(mission), mission, ordinal=99).package
            view = package["views"]["knowledge"]
            assert package["package_version"] == PLANNING_DECISION_PACKAGE_VERSION
            verified = [row for row in view if row["layer"] == "verified"]
            assert {row["id"] for row in verified} == {record.id for record in records}
            [row] = [row for row in verified if row["id"] == records[0].id]
            assert row["ref"] == f"{records[0].id}@{records[0].version}" and row["content"] == records[0].content
            assert row["assurance_level"] == records[0].assurance_level
            summaries = [row for row in view if row["layer"] == "summary"]
            assert summaries and all(row["summary"] and row["checked_by"] for row in summaries)
            # 规划请求的历史回合（任务刚开始、黑板还空）也带着这一节
            for intent in store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED", "SETTLED", "FAILED"):
                if intent.mission_id == mission_id and isinstance(intent.config.get("planning_package"), dict):
                    assert "knowledge" in intent.config["planning_package"]["views"]
            # 夜间 N3-03 候选层：已验证的那条只以 verified 出现一次——它的结论行即使还停在
            # SUPPORTED（候选状态），也不再作为候选列出；另留一条未验证的结论、一条被驳回的结论
            from dataclasses import replace
            from agent_orchestrator.contracts.state_machines import ClaimStatus

            [claim] = [c for c in store.list_mission_claims(mission_id) if c.id == records[0].id]
            store.upsert_claim(replace(claim, status=ClaimStatus.SUPPORTED))
            store.upsert_claim(replace(claim, id="claim-lead", content="可能还要一份索引", status=ClaimStatus.PROPOSED))
            store.upsert_claim(replace(claim, id="claim-rejected", content="被驳回的说法", status=ClaimStatus.REJECTED))
            leads = loop._hierarchical_planner_package(loop._new_mode(mission), mission, ordinal=98).package
            rows = leads["views"]["knowledge"]
            assert [row["layer"] for row in rows if row["id"] == records[0].id] == ["verified"]
            [lead] = [row for row in rows if row["layer"] == "candidate"]
            assert (lead["id"], lead["status"], lead["marker"], lead["content"]) == (
                "claim-lead", "PROPOSED", "未验证", "可能还要一份索引")
            assert not any(row["id"] == "claim-rejected" for row in rows)
            assert not any(item.get("id") == "claim-lead" for item in leads["visible_refs"])
            # 过时的知识不进包：把唯一一条的依据抹掉后再组包，verified 层为空
            store.upsert_knowledge(replace(records[0], support={}))
            again = loop._hierarchical_planner_package(loop._new_mode(mission), mission, ordinal=100).package
            assert [row for row in again["views"]["knowledge"] if row["layer"] == "verified"] == []

    asyncio.run(case())

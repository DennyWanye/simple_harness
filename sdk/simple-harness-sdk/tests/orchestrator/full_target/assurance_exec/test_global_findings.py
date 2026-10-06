# SPDX-License-Identifier: Apache-2.0
"""全局问题挡住完成（原计划 F04 后半，用户 2026-10-06 晚定补）。

原计划：全局安全 finding 必须映射到必须项，不能藏在未选分支里。原来的回复格式里每条 finding
都要点名一条准则，审阅员发现"不属于任何一条要求"的问题（比如代码里写着明文密钥）没处可写，
也就挡不住完成。

现在回复多一个 ``global_findings`` 列表（每项 ``severity`` + ``reason``，严重程度沿用三档）。
秩序规则只有一条，Harness 不判语义：审阅员报了一条 BLOCKER 的全局问题，必须项（没有必须项时是
全部准则）按不成立处理——这次审阅不会形成"接受"，走现有的打回路径；WARNING / INFO 不挡。
"是不是安全问题"由审阅员判断，系统不做关键词匹配。

**改坏检验**（AS-F04G）：``decide_review`` 不再把 BLOCKER 全局问题挂到必须项上 → 本文件判定与
产品两条用例变红。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.assurance.checks import (
    REVIEW_REPLY_SCHEMA_VERSION,
    CriterionPolicy,
    Formula,
    Grade,
    ReviewReply,
    decide_review,
    decode_review_reply,
)
from agent_orchestrator.assurance.codec import AssuranceError
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    planner_reply,
    retry_same_method,
    review_input,
    review_reply,
)

LEAK = "NOTES.md 里写着一段明文的服务端口令，任何拿到文件的人都能登录。"


def _reply(*, global_findings: Any = None, criteria=("a", "b"), verdict="ACCEPT") -> dict:
    body: dict[str, Any] = {
        "schema_version": REVIEW_REPLY_SCHEMA_VERSION, "verdict": verdict, "findings": [],
        "assessments": [{"criterion_id": name, "verdict": "PASS", "evidence_ids": [], "reason": "r",
                         "limitations": []} for name in criteria]}
    if global_findings is not None:
        body["global_findings"] = global_findings
    return body


def _refused(value: Any, code: str) -> None:
    with pytest.raises(AssuranceError) as raised:
        ReviewReply.from_json(value)
    assert raised.value.code == code, raised.value.code


def test_the_decoder_takes_global_findings_and_refuses_a_wrong_shape() -> None:
    assert ReviewReply.from_json(_reply()).global_findings == ()
    reply = ReviewReply.from_json(_reply(global_findings=[
        {"severity": "BLOCKER", "reason": LEAK}, {"severity": "INFO", "reason": "可以顺手加个目录。"}]))
    assert [(item.severity, item.reason) for item in reply.global_findings] == [
        ("BLOCKER", LEAK), ("INFO", "可以顺手加个目录。")]
    # 一致的宽容：整个回复外包一层代码围栏、全局问题里多写一个空字段，都照收
    fenced = "```json\n" + json.dumps(_reply(global_findings=[
        {"severity": "WARNING", "reason": "r", "criterion_id": None}]), ensure_ascii=False) + "\n```"
    assert decode_review_reply(fenced).global_findings[0].severity == "WARNING"
    # 格式不对的照拒
    _refused(_reply(global_findings=[{"severity": "FATAL", "reason": "r"}]), "ENUM_INVALID")
    _refused(_reply(global_findings=[{"severity": "BLOCKER"}]), "OBJECT_FIELDS_MISSING")
    _refused(_reply(global_findings=[{"severity": "BLOCKER", "reason": ""}]), "TEXT_INVALID")
    _refused(_reply(global_findings=[{"severity": "BLOCKER", "reason": "r", "criterion_id": "a"}]),
             "OBJECT_FIELDS_UNKNOWN")
    _refused(_reply(global_findings={"severity": "BLOCKER", "reason": "r"}), "ARRAY_INVALID")
    _refused(_reply(global_findings=[{"severity": "INFO", "reason": "r"}] * 17), "ARRAY_INVALID")
    # 点名一条准则的问题仍写在 findings 里，点名目录外的准则仍拒
    _refused({**_reply(), "findings": [{"criterion_id": "zzz", "severity": "BLOCKER", "reason": "r"}]},
             "FINDING_SCOPE")
    # 旧版本号不收（开发期不兼容旧回复）
    _refused({**_reply(), "schema_version": REVIEW_REPLY_SCHEMA_VERSION - 1}, "REVIEW_SCHEMA_VERSION")


def _decide(reply: dict, mandatory: tuple[str, ...]):
    policies = {name: CriterionPolicy(name, "SEMANTIC", ()) for name in ("a", "b")}
    formula = Formula.from_json({"any": [{"criterion": "a"}, {"criterion": "b"}]},
                                frozenset(policies))
    return decide_review(ReviewReply.from_json(reply), formula, mandatory, policies, {})


@pytest.mark.parametrize("mandatory", [("b",), ()])
def test_a_blocker_global_finding_fails_the_mandatory_criteria_and_nothing_less(mandatory) -> None:
    clean = _decide(_reply(), mandatory)
    assert clean.acceptable
    blocked = _decide(_reply(global_findings=[{"severity": "BLOCKER", "reason": LEAK}]), mandatory)
    assert not blocked.acceptable and blocked.success_witness == frozenset()
    # 有必须项时挂在必须项上；没有必须项时挂在全部准则上——一条成功分支也躲不过去
    failed = {name for name, value in blocked.effective_grades.items() if value is Grade.FAIL}
    assert failed == (set(mandatory) or {"a", "b"})
    for severity in ("WARNING", "INFO"):
        light = _decide(_reply(global_findings=[{"severity": severity, "reason": "r"}]), mandatory)
        assert light.acceptable and light.effective_grades == clean.effective_grades


def _run(tmp_path, severity: str) -> dict[str, Any]:
    seen = {"content": 0, "repairs": []}

    def planner(request: Any) -> Any:
        package = package_of(request)
        if package.get("repair_requests"):
            seen["repairs"].append(package["repair_requests"])
            return retry_same_method(request)
        return planner_reply(request)

    def reviewer(request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        if str((data.get("package") or {}).get("purpose")) == "TASK_CONTENT":
            seen["content"] += 1
            if seen["content"] == 1:
                # 每条准则都判成立、总结论也写"接受"，只多报一条全局问题
                return review_reply(data, global_findings=[{"severity": severity, "reason": LEAK}])
        return review_reply(data)

    async def case() -> dict[str, Any]:
        provider = LayeredScriptedProvider(planner=planner, reviewer=reviewer)
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": f"global-{severity}"})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=30)
            records = world.store.connection.execute(
                "SELECT verdict, record_json FROM review_records WHERE mission_id=? AND purpose='TASK_CONTENT'"
                " AND official=1 ORDER BY created_at", (mission_id,)).fetchall()
            return {"status": mission.status.value, "report": mission.final_report, "records": records, **seen}

    return asyncio.run(case())


@pytest.fixture()
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_a_blocker_global_finding_on_an_accept_reply_sends_the_step_back(tmp_path, _quick) -> None:
    run = _run(tmp_path, "BLOCKER")
    # 这一步没有凭那次"接受"完成：那次审阅记成打回，规划器被问修复，重做后第二次审阅才通过
    assert [row[0] for row in run["records"]] == ["REWORK", "ACCEPT"], run["records"]
    assert run["content"] == 2 and run["repairs"], run
    # 审阅员写的问题原话随着打回交到了后面（挂在不成立的那条准则的说明里）
    assert LEAK in run["records"][0][1]
    assert run["status"] == "COMPLETED", run["report"]


def test_a_warning_global_finding_does_not_block(tmp_path, _quick) -> None:
    run = _run(tmp_path, "WARNING")
    assert [row[0] for row in run["records"]] == ["ACCEPT"], run["records"]
    assert run["content"] == 1 and not run["repairs"]
    assert run["status"] == "COMPLETED", run["report"]

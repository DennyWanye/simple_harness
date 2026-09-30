"""任务级"文档覆盖"裁判：多步任务里没有评估的声称不否决（2026-10-01，真机 mission-4c97c315ed039e6f）。

规划器把任务拆成两步后，每一步的执行者都把**全部**任务判据声称了一遍，而验证只对本步契约里的判据出
评估；于是"别的步骤的判据"这条声称没有评估记录，裁判把它当作"缺有效评估"一票否决——尽管每条判据都
有另一步的 PASS 评估，整个任务因此判失败。现在：没有评估的声称不作数、也不否决，由有评估的贡献决定；
一条评估都没有才算缺。
"""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace

from agent_orchestrator.contracts import ClaimProposal, ClaimStatus, TaskStatus
from agent_orchestrator.governance.domains import DOC_PROFILE
from agent_orchestrator.verification import mission_coverage as judge
from agent_orchestrator.verification.assessments import mission_criterion_catalog

RULES = "summary.md 逐条概括 sources/policy.md 的全部规则并引用原文"
WEBSITE = "summary.md 末尾“与公司官网核对”一节的结论与公司官网当前公布的版本一致"


class _Store:
    path = __import__("pathlib").Path("/nonexistent/db")

    def __init__(self, tasks, claims):
        self._tasks, self._claims = tasks, claims

    def list_tasks(self, mission_id):
        return self._tasks

    def get_claim(self, claim_id):
        return self._claims.get(claim_id)


def _mission():
    return SimpleNamespace(id="mission", tenant_id="tenant", goal="写总结", version=1, status="ACTIVE",
                           success_criteria=("file:summary.md", RULES, WEBSITE), stop_conditions=())


def _run(monkeypatch, assessed):
    """Two completed tasks; each claims BOTH semantic criteria, each is assessed only for its own."""
    mission = _mission()
    catalog = {row["text"]: row["criterion_id"] for row in mission_criterion_catalog(mission)}
    tasks = [SimpleNamespace(id="task-rules", status=TaskStatus.COMPLETED, paused=False, accepted_result_id="result-rules"),
             SimpleNamespace(id="task-site", status=TaskStatus.COMPLETED, paused=False, accepted_result_id="result-site")]
    claims, bindings = {}, {}
    for task, own, other in ((tasks[0], RULES, WEBSITE), (tasks[1], WEBSITE, RULES)):
        result_id = task.accepted_result_id
        proposals = tuple(ClaimProposal(content=text, confidence=1.0, mission_criterion_ids=(catalog[text],))
                          for text in (own, other))
        for ordinal in (1, 2):
            claims[f"{result_id}:claim-{ordinal}"] = SimpleNamespace(id=f"{result_id}:claim-{ordinal}",
                                                                     status=ClaimStatus.VERIFIED)
        rows = ()
        if task.id in assessed:
            rows = (SimpleNamespace(claim_id=f"{result_id}:claim-1", criterion_id="task-criterion", verdict="PASS",
                                    evidence_refs=({"status": "resolved"},), receipt_id="assessment-" + task.id),)
        bindings[task.id] = (SimpleNamespace(result_id=result_id, task_id=task.id,
                                             envelope=SimpleNamespace(claims=proposals, limitations=())), rows)
    monkeypatch.setattr(judge, "accepted_assessments_for", lambda store, *, task: bindings[task.id])
    domain = dataclasses.replace(DOC_PROFILE, version="3")  # assessments without mission source binding
    verdicts = judge.mission_coverage(_Store(tasks, claims), mission, domain)
    return {row["text"]: row for row in verdicts["criteria"] if row["text"] in {RULES, WEBSITE}}


def test_a_claim_without_an_assessment_does_not_veto_the_other_tasks_pass(monkeypatch):
    by_text = _run(monkeypatch, assessed={"task-rules", "task-site"})
    assert by_text[RULES]["verdict"] == "PASS" and by_text[WEBSITE]["verdict"] == "PASS"
    assert by_text[RULES]["task_assessment_receipt_ids"] == ["assessment-task-rules"]
    assert by_text[WEBSITE]["task_assessment_receipt_ids"] == ["assessment-task-site"]
    assert "missing_valid_task_assessment" not in by_text[RULES]["reasons"]


def test_a_criterion_with_no_assessment_at_all_still_fails(monkeypatch):
    by_text = _run(monkeypatch, assessed={"task-rules"})
    assert by_text[RULES]["verdict"] == "PASS"
    assert by_text[WEBSITE]["verdict"] == "FAIL" and by_text[WEBSITE]["reasons"] == ["missing_valid_task_assessment"]

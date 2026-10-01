# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""文档任务"满足与否"以最终审查的结论为准，程序只做秩序检查（HTN 精简 片 D 第 2 项）。

此前文档领域里，程序的"文档覆盖"裁判排在已认证的最终审查结论前面：一条文字要求只有在某条
被接受的声称**原文与要求字面相等**时才算通过，"不确定"的要求占比超过阈值就按证据不足停机。
最终审查员逐条读过成果给出的结论被它盖掉。这是程序在判语义。

现在，走保证通道的任务（产品里的全部任务）：

* 文字要求满足与否 = 已认证的最终审查对这一条的结论；
* 程序只留秩序检查——引用能解析、资料是当前版本、声称状态可用、评估可用、"不确定"必须附
  说明。秩序检查不过，这一条不算满足，不管审阅员怎么说；
* 字面相等不再是通过条件，"不确定"占比不再停机；没有声称指向的要求不算秩序问题；
* ``cite:`` 这类确定性要求（有没有引用到那份资料）仍由程序判。

不走保证通道的旧平面模式不在这次范围里，行为不变（随那条线一起处理）。
"""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from agent_orchestrator.contracts import ClaimProposal, ClaimStatus, TaskStatus
from agent_orchestrator.governance.domains import DOC_PROFILE
from agent_orchestrator.verification import mission_coverage as judge
from agent_orchestrator.verification.assessments import mission_criterion_catalog
from agent_orchestrator.verification.mission_coverage import coverage_objection, document_judgment

RULES = "summary.md 逐条概括 sources/policy.md 的全部规则并引用原文"
OTHER = "summary.md 末尾附核对结论"


class _Store:
    path = __import__("pathlib").Path("/nonexistent/db")

    def __init__(self, tasks, claims):
        self._tasks, self._claims = tasks, claims

    def list_tasks(self, mission_id):
        return self._tasks

    def get_claim(self, claim_id):
        return self._claims.get(claim_id)


def _coverage(monkeypatch, *, content="我逐条概括了三条值班规则，每条都引了原文。", names_criterion=True,
              claim_status=ClaimStatus.VERIFIED, verdict="PASS", refs=({"status": "resolved"},),
              limitations=(), assured=True, share_limit=None):
    mission = SimpleNamespace(id="mission", tenant_id="tenant", goal="写总结", version=1, status="ACTIVE",
                              success_criteria=("file:summary.md", RULES, OTHER), stop_conditions=())
    catalog = {row["text"]: row["criterion_id"] for row in mission_criterion_catalog(mission)}
    task = SimpleNamespace(id="task-1", status=TaskStatus.COMPLETED, paused=False, accepted_result_id="result-1")
    proposal = ClaimProposal(content=content, confidence=1.0,
                             mission_criterion_ids=(catalog[RULES],) if names_criterion else ())
    claims = {"result-1:claim-1": SimpleNamespace(id="result-1:claim-1", status=claim_status)}
    rows = (SimpleNamespace(claim_id="result-1:claim-1", criterion_id="task-criterion", verdict=verdict,
                            evidence_refs=refs, receipt_id="assessment-1"),)
    binding = SimpleNamespace(result_id="result-1", task_id="task-1",
                              envelope=SimpleNamespace(claims=(proposal,), limitations=limitations))
    monkeypatch.setattr(judge, "accepted_assessments_for", lambda store, *, task: (binding, rows))
    domain = dataclasses.replace(DOC_PROFILE, version="3")
    if share_limit is not None:
        domain = dataclasses.replace(domain, completion_rules={
            **dict(domain.completion_rules), "inconclusive_share_limit": share_limit})
    report = judge.mission_coverage(_Store([task], claims), mission, domain, assured=assured)
    return {**{row["text"]: row for row in report["criteria"]}, "insufficient": report["insufficient"]}


# ------------------------------------------------------------------ 秩序检查本身


def test_a_sound_contribution_passes_the_order_check_whatever_its_wording(monkeypatch) -> None:
    row = _coverage(monkeypatch)[RULES]
    assert (row["verdict"], row["reasons"]) == ("PASS", [])
    assert row["task_assessment_receipt_ids"] == ["assessment-1"]


def test_off_the_assured_lane_the_same_contribution_still_needs_the_literal_wording(monkeypatch) -> None:
    row = _coverage(monkeypatch, assured=False)[RULES]
    assert row["verdict"] == "FAIL"
    assert row["reasons"] == ["candidate_without_task_uncertainty_or_content_binding"]


def test_a_requirement_nobody_claimed_is_not_an_order_failure(monkeypatch) -> None:
    row = _coverage(monkeypatch)[OTHER]
    assert (row["verdict"], row["reasons"]) == ("UNCLAIMED", [])


@pytest.mark.parametrize(("change", "reason"), [
    ({"refs": ({"status": "unresolved"},)}, "citation_not_resolved"),
    ({"refs": ()}, "citation_not_resolved"),
    ({"claim_status": ClaimStatus.REJECTED}, "claim_not_usable"),
    ({"verdict": "FAIL"}, "task_assessment_not_usable"),
    ({"verdict": "INCONCLUSIVE"}, "missing_task_limitations"),
])
def test_order_failures_are_named(monkeypatch, change, reason) -> None:
    row = _coverage(monkeypatch, **change)[RULES]
    assert (row["verdict"], row["reasons"]) == ("FAIL", [reason])


def test_declared_uncertainty_with_its_limitation_is_in_order(monkeypatch) -> None:
    limitation = SimpleNamespace(claim_id="claim:1", criterion_id="task-criterion", missing="官网当前版本没法核对")
    row = _coverage(monkeypatch, verdict="INCONCLUSIVE", limitations=(limitation,))[RULES]
    assert (row["verdict"], row["reasons"]) == ("INCONCLUSIVE", ["task_evidence_inconclusive"])
    assert row["limitations"]


def test_structural_requirements_are_left_to_their_own_checks(monkeypatch) -> None:
    assert _coverage(monkeypatch)["file:summary.md"]["verdict"] == "STRUCTURAL"


def test_the_share_of_uncertain_requirements_does_not_stop_an_assured_mission(monkeypatch) -> None:
    limitation = SimpleNamespace(claim_id="claim:1", criterion_id="task-criterion", missing="没法核对")
    uncertain = {"verdict": "INCONCLUSIVE", "limitations": (limitation,), "share_limit": 0.0}
    assert _coverage(monkeypatch, assured=False, **uncertain)["insufficient"] is True
    assert _coverage(monkeypatch, assured=True, **uncertain)["insufficient"] is False


# ------------------------------------------------------------------ 这一条最终算谁的


def _row(kind="free", verdict="PASS", reasons=()):
    return {"criterion_id": "crit-1", "kind": kind, "verdict": verdict, "reasons": list(reasons),
            "limitations": [], "task_assessment_receipt_ids": ["assessment-1"]}


def test_the_final_review_decides_a_text_requirement() -> None:
    passed = document_judgment(RULES, _row(), assured=True, grade="PASS")
    assert (passed["met"], passed["judge"], passed["source"]) == (True, "assurance_review", "certified_root_resolution")
    assert passed["task_assessment_receipt_ids"] == ["assessment-1"]
    rework = document_judgment(RULES, _row(), assured=True, grade="REWORK")
    assert (rework["met"], rework["judge"]) == (False, "assurance_review")
    missing = document_judgment(RULES, _row(), assured=True, grade=None)
    assert (missing["met"], missing["source"]) == (False, "unavailable")


@pytest.mark.parametrize("order", ["PASS", "INCONCLUSIVE", "UNCLAIMED"])
def test_a_clean_order_check_does_not_stand_in_for_the_review(order) -> None:
    judged = document_judgment(RULES, _row(verdict=order), assured=True, grade="REWORK")
    assert judged["met"] is False and judged["judge"] == "assurance_review"
    assert document_judgment(RULES, _row(verdict=order), assured=True, grade="PASS")["met"] is True


def test_an_order_failure_is_unmet_whatever_the_review_said() -> None:
    judged = document_judgment(RULES, _row(verdict="FAIL", reasons=("citation_not_resolved",)),
                               assured=True, grade="PASS")
    assert (judged["met"], judged["judge"], judged["verdict"]) == (False, "document_order_check", "FAIL")
    assert judged["reason"] == "citation_not_resolved"


def test_a_citation_requirement_stays_with_the_program() -> None:
    judged = document_judgment("cite:sources/policy.md", _row(kind="cite", verdict="PASS"),
                               assured=True, grade=None)
    assert (judged["met"], judged["judge"]) == (True, "document_coverage")


def test_off_the_assured_lane_nothing_changes() -> None:
    judged = document_judgment(RULES, _row(verdict="FAIL", reasons=("no_accepted_content_binding",)),
                               assured=False, grade="PASS")
    assert (judged["met"], judged["judge"], judged["verdict"]) == (False, "document_coverage", "FAIL")


# ------------------------------------------------------------------ 提交时的秩序


def test_the_commit_accepts_the_reviews_conclusion_and_refuses_an_order_failure() -> None:
    reviewed = {"criterion": RULES, "met": True, "judge": "assurance_review"}
    assert coverage_objection(reviewed, _row(), assured=True) is None
    assert coverage_objection({**reviewed, "met": False}, _row(), assured=True) is None
    assert coverage_objection(reviewed, _row(verdict="UNCLAIMED"), assured=True) is None
    failed = _row(verdict="FAIL", reasons=("claim_not_usable",))
    assert coverage_objection(reviewed, failed, assured=True) == "order_check_failed"
    stopped = {"criterion": RULES, "met": False, "judge": "document_order_check"}
    assert coverage_objection(stopped, failed, assured=True) is None
    # "秩序检查没过"不能凭空说：没有秩序问题时这个判法不成立
    assert coverage_objection(stopped, _row(), assured=True) == "order_check_not_failed"


def test_the_commit_keeps_the_old_rule_off_the_assured_lane_and_for_citations() -> None:
    claimed = {"criterion": RULES, "met": True, "judge": "assurance_review"}
    assert coverage_objection(claimed, _row(verdict="FAIL"), assured=False) == "disagrees_with_coverage"
    cite = {"criterion": "cite:sources/policy.md", "met": True, "judge": "document_coverage"}
    assert coverage_objection(cite, _row(kind="cite", verdict="FAIL"), assured=True) == "disagrees_with_coverage"
    assert coverage_objection(cite, _row(kind="cite", verdict="PASS"), assured=True) is None


def test_a_whole_judgment_is_reconciled_row_by_row() -> None:
    from agent_orchestrator.verification.mission_coverage import reconcile_document_judgments

    structural = {"criterion_id": "crit-0", "kind": "file", "verdict": "STRUCTURAL"}
    judgments = [{"criterion": "file:summary.md", "met": True, "judge": "rule_check"},
                 {"criterion": RULES, "criterion_id": "crit-1", "met": True, "judge": "assurance_review"}]
    assert reconcile_document_judgments(judgments, [structural, _row()], assured=True, refresh_stale=False) is None
    failed = _row(verdict="FAIL", reasons=("citation_not_resolved",))
    assert reconcile_document_judgments(
        judgments, [structural, failed], assured=True, refresh_stale=False) == "order_check_failed"
    assert judgments[1]["met"] is True, "without the refresh nothing is rewritten"
    # 旧平面口径下同一份判定不成立：通道参数不是摆设
    assert reconcile_document_judgments(
        judgments, [structural, _row(verdict="FAIL")], assured=False, refresh_stale=False) == "disagrees_with_coverage"


def test_a_kept_judgment_gone_stale_is_committed_as_the_fresh_failure() -> None:
    """留存的判定在等动作 / 批准期间资料换了版本：原本成立、现在秩序上不成立的那一条按现在的
    覆盖结果改成不满足后提交，不是拒收整次提交；编号对不上的判定不替它改。"""
    from agent_orchestrator.verification.mission_coverage import reconcile_document_judgments

    failed = _row(verdict="FAIL", reasons=("no_current_source_basis",))
    kept = [{"criterion": RULES, "criterion_id": "crit-1", "met": True, "judge": "assurance_review"}]
    assert reconcile_document_judgments(kept, [failed], assured=True, refresh_stale=True) is None
    assert (kept[0]["met"], kept[0]["judge"], kept[0]["reason"]) == (
        False, "document_order_check", "no_current_source_basis")
    forged = [{"criterion": RULES, "criterion_id": "someone-elses", "met": True, "judge": "assurance_review"}]
    assert reconcile_document_judgments(forged, [failed], assured=True, refresh_stale=True) == "order_check_failed"
    assert forged[0]["met"] is True


def test_a_rewritten_row_keeps_nothing_of_the_judgment_it_replaces() -> None:
    """独立核验提醒：三种判法带的字段不一样。就地改写时不清掉上一种判法的字段，界面上会对着
    已经满足的一行显示"未通过"，或对着秩序检查没过的一行写"出自已认证的根结论"。"""
    from agent_orchestrator.verification.mission_coverage import replace_document_judgment

    row = {"criterion": RULES, "met": False, "verdict": "FAIL", "judge": "document_order_check",
           "reason": "citation_not_resolved", "excluded_claim_ids": ["c1"]}
    replace_document_judgment(row, document_judgment(RULES, _row(), assured=True, grade="PASS"))
    assert (row["met"], row["judge"]) == (True, "assurance_review") and "verdict" not in row
    assert row["excluded_claim_ids"] == ["c1"], "what the caller keeps beside the judgment stays"
    replace_document_judgment(row, document_judgment(
        RULES, _row(verdict="FAIL", reasons=("claim_not_usable",)), assured=True, grade="PASS"))
    assert (row["met"], row["verdict"], row["judge"]) == (False, "FAIL", "document_order_check")
    assert "source" not in row


def test_off_the_assured_lane_a_stale_row_only_changes_its_three_fields() -> None:
    """旧平面口径下留存判定过期时，原来只改 met、verdict、reason 三样，别的字段不动——照旧。"""
    from agent_orchestrator.verification.mission_coverage import reconcile_document_judgments

    kept = [{"criterion": RULES, "criterion_id": "crit-1", "met": True, "verdict": "PASS",
             "judge": "document_coverage", "reason": "", "limitations": ["kept"],
             "task_assessment_receipt_ids": ["old-receipt"]}]
    failed = _row(verdict="FAIL", reasons=("no_current_source_basis",))
    assert reconcile_document_judgments(kept, [failed], assured=False, refresh_stale=True) is None
    assert kept == [{"criterion": RULES, "criterion_id": "crit-1", "met": False, "verdict": "FAIL",
                     "judge": "document_coverage", "reason": "no_current_source_basis",
                     "limitations": ["kept"], "task_assessment_receipt_ids": ["old-receipt"]}]


# ------------------------------------------------------------------ 接线


def _refreshed(monkeypatch, *, assured: bool, grade: str | None, verdict: str = "PASS"):
    from agent_orchestrator.orchestrator.event_handler import Orchestrator

    seen: dict = {}
    row = {**_row(verdict=verdict, reasons=("citation_not_resolved",) if verdict == "FAIL" else ()),
           "text": RULES, "excluded_claim_ids": [], "source_provenance_issues": []}

    def coverage(*args, **kwargs):
        seen.update(kwargs)
        return {"criteria": [row]}

    monkeypatch.setattr(judge, "mission_coverage", coverage)
    fake = SimpleNamespace(
        commit=SimpleNamespace(domain_for=lambda mission_id: DOC_PROFILE),
        store=None, assembled=SimpleNamespace(workspaces=SimpleNamespace(artifact_store=None)),
        _is_assured=lambda mission_id: assured, _new_mode=lambda mission: object(),
        _assured_root_grades=lambda mission, new_mode: None if grade is None else {RULES: grade})
    kept = [{"criterion": "file:summary.md", "met": True, "judge": "rule_check"},
            {"criterion": RULES, "met": True, "judge": "assurance_review"}]
    refreshed = Orchestrator._refresh_document_judgments(fake, SimpleNamespace(id="mission"), kept)
    assert seen["assured"] is assured, "the coverage is read for the lane the Mission is on"
    return refreshed


def test_a_kept_judgment_is_refreshed_through_the_same_rule(monkeypatch) -> None:
    structural, text = _refreshed(monkeypatch, assured=True, grade="PASS")
    assert structural == {"criterion": "file:summary.md", "met": True, "judge": "rule_check"}
    assert (text["met"], text["judge"]) == (True, "assurance_review")
    _, stale = _refreshed(monkeypatch, assured=True, grade="PASS", verdict="FAIL")
    assert (stale["met"], stale["judge"]) == (False, "document_order_check")
    _, legacy = _refreshed(monkeypatch, assured=False, grade="PASS", verdict="FAIL")
    assert (legacy["met"], legacy["judge"]) == (False, "document_coverage")


def test_the_judgment_and_its_commit_go_through_the_one_rule() -> None:
    """判定与提交两处都只调用这一条规则，不再各写一份"覆盖结果说了算"；覆盖结果按任务所在
    的通道读。"""
    import inspect

    from agent_orchestrator.orchestrator.commit_service import CommitService
    from agent_orchestrator.orchestrator.event_handler import Orchestrator

    evaluate = inspect.getsource(Orchestrator._evaluate_criteria)
    assert "document_judgment(" in evaluate and "assured=assured" in evaluate
    assert "assured=self._is_assured(mission.id)" in evaluate
    assert '"judge": "document_coverage"' not in evaluate
    # the document branch still comes before the generic assured branch, and now defers to it
    assert evaluate.index("document_judgment(") < evaluate.index('"judge": "assurance_review"')
    commit = inspect.getsource(CommitService.judge_mission)
    assert commit.count("reconcile_document_judgments(") == 1 and "coverage_objection(" not in commit
    call = commit[commit.index("reconcile_document_judgments("):]
    assert 'mutable_judgments, document_coverage["criteria"], assured=assured,' in call[:200]
    assert commit.index("assured = self._on_assured_lane(mission_id)") < commit.index("assured=assured,")
    for reader in (CommitService.stop_insufficient_mission, CommitService.document_handoff_refusal):
        assert "assured=self._on_assured_lane(mission_id)" in inspect.getsource(reader)

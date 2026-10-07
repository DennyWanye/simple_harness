# SPDX-License-Identifier: Apache-2.0
"""A group (reviews / acceptance / completion readers): plan cases A01–A18.

Pure cases (A01, A02, A04, A05, A09) drive the production codec, formula,
check gate and review decision directly. 2026-10-03（HTN 补齐阶段 A′）：运行时用例改在产品同形
世界里跑（产品那一份部署组装，模型回复是脚本，见 :mod:`_review_world`）；接缝用例（A04 执行器、
A10 取证工具、A12 格式修复）跑迁到产品同形世界的接缝脚本并断言它们的报告。

分诊表的处置：A13 / A14 / A17（内容验收只是"准备好了"、数据可读顺序不放、根要求的效果目录）并入
``operation_completion/test_publish_variants.py`` 第一条；A16（效果待办时不空转）同上；A18（直接
写终审绕过有效性闸）删：原用例要把产品的有效性服务关掉、替换准备函数才碰得到下游检查（裁决①
不许）；A07 里"伪造的作者集合"那一半删：靠替换产品读函数造状态。

A03 / A11 里"要求书第 2 版"的两半：原以"第 2 版在产品上没有写入方"删；用户改要求（HTN 补齐阶段 E）
上线后已有第 2 版写入方，2026-10-07（V08）回挂到 ``product_world/test_requirements_amend.py``：
A03 后半（真实用户确认新版 → 新一版加回执，旧版字节不动）→ ``test_amend_writes_everything_in_one_transaction``；
A11 后半（审阅冻结在第 1 版、回复晚到 → 原审阅与费用照常入账，不能批准第 2 版）→
``test_a_review_frozen_on_the_first_version_keeps_its_record_and_cost_but_approves_nothing``。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agent_orchestrator.assurance.checks import (
    CheckResult,
    CriterionPolicy,
    Formula,
    Grade,
    ReviewReply,
    decide_review,
    evaluate_check_gate,
)
from agent_orchestrator.assurance.codec import AssuranceError, canonical, decode, fingerprint
from agent_orchestrator.assurance.executor_checks import executor_run_facts
from agent_orchestrator.assurance.refs import AssuranceRef, Pin
from agent_orchestrator.assurance.reviews import AssuranceReviewBinding, ReviewRecordBinding
from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.contracts.resolution import (
    AllExpr,
    AnyExpr,
    CriterionExpr,
    criteria_only_under_any,
    hard_constraints_not_independent,
    parse_success_expression,
    unknown_expression_criteria,
)
from agent_orchestrator.orchestrator.assurance_purpose_reviews import (
    composition_subject,
    purpose_review_key,
)
from agent_orchestrator.orchestrator.assurance_review_import import review_subject_stopped
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.store import StoreConflict

SDK_ROOT = Path(__file__).resolve().parents[4]
SEAMS = SDK_ROOT / "scripts/assurance_seams"
sys.path.insert(0, str(SEAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review_world import ReviewScript, reviewed_mission  # noqa: E402

HASH = "a" * 64
ACCEPT_REPLY = {"schema_version": 5, "verdict": "ACCEPT", "assessments": [
    {"criterion_id": "criterion-report", "verdict": "PASS", "evidence_ids": [], "reason": "fixture", "limitations": []}],
    "findings": []}


def _refused(call, *codes):
    with pytest.raises(AssuranceError) as raised:
        call()
    assert raised.value.code in codes, (raised.value.code, codes)
    return raised.value.code


def _seam(name):
    completed = subprocess.run([sys.executable, str(SEAMS / name)], capture_output=True, text=True, timeout=900,
                               cwd=str(SDK_ROOT))
    assert completed.returncode == 0, (name, completed.stderr[-4000:])
    summary = json.loads(completed.stdout.strip().splitlines()[-1])
    assert summary["status"] == "PASS", summary
    return json.loads(Path(summary["evidence"]).read_text())


def spec_ref(name):
    return AssuranceRef("check_spec", Pin(name, 1, HASH))


def receipt_ref(name):
    return AssuranceRef("local_check_receipt", Pin(name, 0, HASH))


def result(name, grade=Grade.PASS, *, state="SUCCEEDED", valid=True, receipt=None):
    return CheckResult(spec_ref(name), receipt or receipt_ref("rcpt-" + name), state, grade, valid)


def reply(**grades):
    return ReviewReply.from_json({"schema_version": 5, "verdict": "ACCEPT", "findings": [], "assessments": [
        {"criterion_id": name, "verdict": str(grade.value if hasattr(grade, "value") else grade), "evidence_ids": [],
         "reason": "r", "limitations": []} for name, grade in grades.items()]})


def count(store, sql, *params):
    return store.connection.execute(sql, params).fetchone()[0]


# --------------------------------------------------------------------------- A01
def test_strict_boundary():
    good = canonical(ACCEPT_REPLY)
    assert ReviewReply.from_json(decode(good)).verdict == "ACCEPT"
    # Codec boundary: each malformed body is refused before any interpretation.
    _refused(lambda: decode('{"a": 1, "a": 2}'), "JSON_DUPLICATE_KEY")
    _refused(lambda: decode('{"a": NaN}'), "JSON_NUMBER_INVALID", "JSON_INVALID")
    _refused(lambda: decode('{"a": 1e400}'), "JSON_NUMBER_INVALID", "JSON_INTEGER_OVERFLOW", "JSON_INVALID")
    _refused(lambda: decode('{"a": 9007199254740992}'), "JSON_INTEGER_OVERFLOW")
    _refused(lambda: decode('{"a": "\\udc00"}'), "JSON_UNICODE", "JSON_INVALID")
    _refused(lambda: decode("[" * 200 + "]" * 200), "JSON_DEPTH_LIMIT")
    _refused(lambda: decode("x" * (256 * 1024 + 1)), "JSON_BYTES_LIMIT")
    _refused(lambda: canonical({"a": float("nan")}), "JSON_NUMBER_INVALID", "JSON_INVALID")
    _refused(lambda: canonical({1: "non-text key"}), "JSON_OBJECT_KEY", "JSON_INVALID")
    # Review reply: unknown verdict, bool version, wrong version, duplicate criterion,
    # foreign finding, unknown field, empty assessments.
    _refused(lambda: ReviewReply.from_json({**ACCEPT_REPLY, "verdict": "OFFICIAL"}), "ENUM_INVALID")
    _refused(lambda: ReviewReply.from_json({**ACCEPT_REPLY, "schema_version": True}), "INTEGER_INVALID")
    _refused(lambda: ReviewReply.from_json({**ACCEPT_REPLY, "schema_version": 1}), "REVIEW_SCHEMA_VERSION")
    _refused(lambda: ReviewReply.from_json({**ACCEPT_REPLY, "assessments": ACCEPT_REPLY["assessments"] * 2}),
             "DUPLICATE_CRITERION")
    _refused(lambda: ReviewReply.from_json({**ACCEPT_REPLY, "findings": [
        {"criterion_id": "other", "severity": "BLOCKER", "reason": "x"}]}), "FINDING_SCOPE")
    _refused(lambda: ReviewReply.from_json({**ACCEPT_REPLY, "extra": 1}), "OBJECT_FIELDS_UNKNOWN")
    _refused(lambda: ReviewReply.from_json({k: v for k, v in ACCEPT_REPLY.items() if k != "findings"}),
             "OBJECT_FIELDS_MISSING")
    _refused(lambda: ReviewReply.from_json({**ACCEPT_REPLY, "assessments": []}), "ARRAY_INVALID")
    _refused(lambda: ReviewReply.from_json({**ACCEPT_REPLY, "assessments": [
        {**ACCEPT_REPLY["assessments"][0], "verdict": "MAYBE"}]}), "ENUM_INVALID")
    # The official record binding refuses a raw output whose bytes do not hash to the pinned raw ref.
    body = {"schema_version": 2, "mission_id": "m", "review_key": "k",
            "package_ref": {"id": "p", "revision": 1, "content_hash": HASH},
            "record_ref": {"id": "r", "revision": 1, "content_hash": HASH}, "reviewer_agent_id": "reviewer",
            "reviewer_turn_ref": {"kind": "agent_turn_receipt", "pin": {"id": "t", "revision": 0, "content_hash": HASH}},
            "raw_output_ref": {"kind": "artifact", "pin": {"id": "raw", "revision": 1, "content_hash": HASH}},
            "raw_output_hash": "b" * 64, "codec_version": "assurance-review-reply-v3", "consumed_check_refs": [],
            "exposed_evidence_refs": [], "evidence_manifest_hash": HASH,
            "binding_read_set": [{"channel": "OBJECT", "key": "o", "fingerprint": HASH, "coverage": "COMPLETE"}],
            "disclosure_refs": [], "invocation_ordinal": 1}
    _refused(lambda: ReviewRecordBinding.from_json(body), "REVIEW_RAW_HASH_MISMATCH")
    ok = ReviewRecordBinding.from_json({**body, "raw_output_hash": HASH})
    assert ok.content_hash == fingerprint(ok.to_json())
    _refused(lambda: ReviewRecordBinding.from_json({**body, "raw_output_hash": HASH, "invocation_ordinal": 3}),
             "INTEGER_INVALID")
    _refused(lambda: ReviewRecordBinding.from_json({**body, "raw_output_hash": HASH, "schema_version": 3}),
             "REVIEW_RECORD_BINDING_VERSION")
    _refused(lambda: ReviewRecordBinding.from_json({**body, "raw_output_hash": HASH, "binding_read_set": [
        body["binding_read_set"][0], body["binding_read_set"][0]]}), "DUPLICATE_READ_KEY")


# --------------------------------------------------------------------------- A02
def test_formula_truth_tables():
    names = frozenset({"a", "b", "m"})
    all_of = Formula.from_json({"all": [{"criterion": "a"}, {"criterion": "b"}]}, names)
    any_of = Formula.from_json({"any": [{"criterion": "a"}, {"criterion": "b"}]}, names)
    grades = [Grade.PASS, Grade.FAIL, Grade.UNKNOWN]
    for ga in grades:
        for gb in grades:
            g = {"a": ga, "b": gb, "m": Grade.PASS}
            expect_all = (Grade.FAIL if Grade.FAIL in (ga, gb)
                          else (Grade.PASS if ga is gb is Grade.PASS else Grade.UNKNOWN))
            expect_any = (Grade.PASS if Grade.PASS in (ga, gb)
                          else (Grade.FAIL if ga is gb is Grade.FAIL else Grade.UNKNOWN))
            assert all_of.evaluate(g)[0] is expect_all, (ga, gb)
            assert any_of.evaluate(g)[0] is expect_any, (ga, gb)
    assert all_of.evaluate({"a": Grade.PASS, "b": Grade.PASS})[1] == {"a", "b"}
    assert any_of.evaluate({"a": Grade.FAIL, "b": Grade.PASS})[1] == {"b"}
    # Unknown criterion / empty formula / illegal node never become a formula.
    _refused(lambda: Formula.from_json({"criterion": "zz"}, names), "FORMULA_UNKNOWN_CRITERION")
    _refused(lambda: Formula.from_json({"all": []}, names), "ARRAY_INVALID", "FORMULA_INVALID")
    _refused(lambda: Formula.from_json({"not": {"criterion": "a"}}, names), "FORMULA_INVALID")
    _refused(lambda: Formula.from_json({"all": [{"criterion": "a"}], "any": []}, names), "FORMULA_INVALID")
    _refused(lambda: all_of.evaluate({"a": Grade.PASS}), "FORMULA_UNKNOWN_CRITERION")
    deep = {"criterion": "a"}
    for _ in range(20):
        deep = {"all": [deep]}
    _refused(lambda: Formula.from_json(deep, names), "FORMULA_LIMIT")
    # Mandatory criteria are ANDed on top and cannot be bypassed by an ANY branch.
    policies = {n: CriterionPolicy(n, "SEMANTIC", ()) for n in names}
    decision = decide_review(reply(a=Grade.FAIL, b=Grade.PASS, m=Grade.FAIL), any_of, ("m",), policies, {})
    assert not decision.acceptable and decision.effective_grades["m"] is Grade.FAIL
    decision = decide_review(reply(a=Grade.FAIL, b=Grade.PASS, m=Grade.UNKNOWN), any_of, ("m",), policies, {})
    assert not decision.acceptable  # unevaluated stays UNKNOWN, never PASS
    decision = decide_review(reply(a=Grade.FAIL, b=Grade.PASS, m=Grade.PASS), any_of, ("m",), policies, {})
    assert decision.acceptable and decision.success_witness == {"b", "m"}
    _refused(lambda: decide_review(reply(a=Grade.PASS, b=Grade.PASS, m=Grade.PASS), any_of, ("zz",), policies, {}),
             "MANDATORY_CRITERIA_INVALID")
    _refused(lambda: decide_review(reply(a=Grade.PASS, b=Grade.PASS), any_of, (), policies, {}),
             "POLICY_CATALOGUE_MISMATCH")
    # Approved requirements: an illegal success expression is refused at the contract layer.
    with pytest.raises(ContractError):
        AllExpr(())
    with pytest.raises(ContractError):
        parse_success_expression({"op": "all", "children": []})
    with pytest.raises(ContractError):
        parse_success_expression({"op": "xor", "children": [{"op": "criterion", "criterion_id": "a"}]})
    expression = parse_success_expression({"op": "any", "children": [
        {"op": "criterion", "criterion_id": "a"}, {"op": "criterion", "criterion_id": "b"}]})
    assert isinstance(expression, AnyExpr) and criteria_only_under_any(expression) == {"a", "b"}
    assert unknown_expression_criteria(expression, ()) == ("a", "b")



async def _completed(tmp_path, provider=None):
    provider = provider or ReviewScript()
    async with reviewed_mission(tmp_path, provider) as case:
        mission = await case.settle()
        assert str(mission.status.value) == "COMPLETED", mission.final_report
        yield case, provider


def _content_binding(store, mission_id):
    row = store.connection.execute(
        "SELECT * FROM assurance_review_bindings WHERE mission_id=? AND review_key LIKE 'assurance-content:%'",
        (mission_id,)).fetchone()
    assert row is not None
    return row


# --------------------------------------------------------------------------- A03
def test_requirement_authority(tmp_path):
    """要求书第 1 版由部署在建任务时写下；规划器一侧想改写同一版（删一条要求）被拒，字节不变。"""

    async def body():
        async with reviewed_mission(tmp_path, ReviewScript()) as case:
            store, mission_id = case.store, case.mission_id
            htn = HtnStore(store)
            original = htn.get_requirements_revision(mission_id, 1)
            row_sql = ("SELECT revision_json,content_hash,authority_subject FROM requirements_revisions "
                       "WHERE mission_id=? AND revision=1")
            original_row = tuple(store.connection.execute(row_sql, (mission_id,)).fetchone())
            reduced = dataclasses.replace(original, criteria=original.criteria[:0] or original.criteria[:1],
                                          success_expression=CriterionExpr(original.criteria[0].criterion_id),
                                          authority_subject="planner")
            with pytest.raises(StoreConflict):
                htn.insert_requirements_revision(reduced)
            assert tuple(store.connection.execute(row_sql, (mission_id,)).fetchone()) == original_row
            assert [r.revision for r in htn.list_requirements_revisions(mission_id)] == [1]
            # Hard constraints can never be moved under an ANY, and an expression may only name
            # catalogued criteria.
            assert hard_constraints_not_independent(original.success_expression, original.criteria) == ()
            assert unknown_expression_criteria(original.success_expression, original.criteria) == ()
            assert unknown_expression_criteria(AllExpr((CriterionExpr("ghost"),)), original.criteria) == ("ghost",)

    asyncio.run(body())


# --------------------------------------------------------------------------- A04
def test_check_receipt_scope():
    policy = CriterionPolicy("c", "CHECKED", ((spec_ref("fmt"), spec_ref("rule")),))
    good = {spec_ref("fmt"): result("fmt"), spec_ref("rule"): result("rule")}
    gate = evaluate_check_gate(policy, good)
    assert gate.grade is Grade.PASS and gate.reason == "CHECK_GROUP_SATISFIED"
    assert set(gate.consumed) == {receipt_ref("rcpt-fmt"), receipt_ref("rcpt-rule")}
    # The same real execution's receipt offered for another spec: identity mismatch.
    swapped = {spec_ref("fmt"): result("fmt"), spec_ref("rule"): result("fmt")}
    _refused(lambda: evaluate_check_gate(policy, swapped), "CHECK_RESULT_IDENTITY")
    # A receipt from another checkspec / target / manifest never satisfies: absent -> incomplete.
    partial = {spec_ref("fmt"): result("fmt"), spec_ref("other"): result("other")}
    gate = evaluate_check_gate(policy, partial)
    assert gate.grade is Grade.UNKNOWN and gate.reason == "CHECK_EVIDENCE_INCOMPLETE"
    # Real failure / ERROR / NOT_RUN / invalid source are never PASS.
    assert result("fmt", Grade.FAIL).effective is Grade.FAIL
    for state in ("ERROR", "CANCELLED", "NOT_RUN", "RUNNING"):
        assert result("fmt", state=state).effective is Grade.UNKNOWN
    assert result("fmt", valid=False).effective is Grade.UNKNOWN
    failed = {spec_ref("fmt"): result("fmt", Grade.FAIL), spec_ref("rule"): result("rule")}
    gate = evaluate_check_gate(policy, failed)
    assert gate.grade is Grade.FAIL and gate.reason == "CHECK_GROUPS_FAILED"
    _refused(lambda: CheckResult(receipt_ref("x"), receipt_ref("x"), "SUCCEEDED", Grade.PASS, True), "CHECK_REF_INVALID")
    _refused(lambda: CheckResult(spec_ref("x"), receipt_ref("x"), "DONE", Grade.PASS, True), "ENUM_INVALID")
    _refused(lambda: CheckResult(spec_ref("x"), receipt_ref("x"), "SUCCEEDED", "PASS", True), "CHECK_RESULT_TYPE")
    _refused(lambda: CriterionPolicy("c", "CHECKED", ((receipt_ref("x"),),)), "CHECK_SPEC_REF_REQUIRED")
    _refused(lambda: CriterionPolicy("c", "SEMANTIC", ((spec_ref("x"),),)), "SEMANTIC_HAS_CHECKS")
    _refused(lambda: CriterionPolicy("c", "CHECKED", ()), "CHECKED_GROUPS_REQUIRED")
    _refused(lambda: CriterionPolicy("c", "CHECKED", ((spec_ref("x"),), (spec_ref("x"),))), "DUPLICATE_CHECK_GROUP")
    # 执行器从没给出的事实不能当成一次运行：没有执行器回执的目标是 ERROR / UNKNOWN；退出码 0 但
    # 一个通过的用例编号都没有是 UNKNOWN（纯函数：部署里唯一的执行器总会给回执，产品路径够不到）。
    clean = {"execution_id": "synthetic", "kind": "process_only", "isolated": False, "environment_digest": "e" * 64,
             "effective_limits": {}, "exit_code": 0, "truncated": False, "timed_out": False,
             "limit_exceeded": None, "tree_killed": True, "residual_pids": [], "status": "ok"}
    run = {"target": "tests", "returncode": 0, "timed_out": False, "command": [], "passed": True,
           "stdout": "PASSED tests/test_fixture.py::test_report_ok"}
    scope = {"result_id": "result-x", "artifact_hashes": {}}
    state, grade, _ = executor_run_facts({"layer": "code_test", "detail": {"runs": [run], "observation_scope": scope}},
                                         layer="code_test")
    assert (state, grade) == ("ERROR", Grade.UNKNOWN)
    silent = dict(run, stdout="2 passed in 0.01s", receipt=clean)
    state, grade, _ = executor_run_facts({"layer": "code_test", "detail": {"runs": [silent], "observation_scope": scope}},
                                         layer="code_test")
    assert (state, grade) == ("SUCCEEDED", Grade.UNKNOWN)
    # 产品路径上的真实执行器接缝：code_test 经部署的执行器真起 pytest，回执只导入一次、验收不重跑。
    report = _seam("executor-check-seam.py")
    assert report["passing"]["binding_verdict"] == "PASS" and report["passing"]["imported_once"] is True
    assert report["passing"]["mission"] == "COMPLETED"
    assert report["failing"]["binding_verdict"] == "FAIL" and report["failing"]["mission"] != "COMPLETED"
    assert report["workspace_mutated"]["binding_verdict"] != "PASS"
    assert report["timeout"]["binding_verdict"] == "UNKNOWN" and report["timeout"]["state"] == "CANCELLED"


# --------------------------------------------------------------------------- A05
def test_any_branch_not_mandatory():
    names = frozenset({"a", "b", "privacy"})
    formula = Formula.from_json({"any": [{"criterion": "a"}, {"criterion": "b"}]}, names)
    policies = {"a": CriterionPolicy("a", "CHECKED", ((spec_ref("a-check"),),)),
                "b": CriterionPolicy("b", "CHECKED", ((spec_ref("b-check"),),)),
                "privacy": CriterionPolicy("privacy", "SEMANTIC", ())}
    b_ok = {spec_ref("b-check"): result("b-check")}
    # A fails or is untested; B really succeeded: B carries, A does not block.
    decision = decide_review(reply(a=Grade.FAIL, b=Grade.PASS, privacy=Grade.PASS), formula, ("privacy",), policies, b_ok)
    assert decision.acceptable and decision.success_witness == {"b", "privacy"}
    assert decision.consumed_receipts == (receipt_ref("rcpt-b-check"),)
    decision = decide_review(reply(a=Grade.PASS, b=Grade.PASS, privacy=Grade.PASS), formula, ("privacy",), policies, b_ok)
    assert decision.acceptable and decision.success_witness == {"b", "privacy"}
    assert decision.effective_grades["a"] is Grade.UNKNOWN  # model PASS without its real check is not PASS
    # Privacy failing blocks whatever the branches say.
    both = {**b_ok, spec_ref("a-check"): result("a-check")}
    decision = decide_review(reply(a=Grade.PASS, b=Grade.PASS, privacy=Grade.FAIL), formula, ("privacy",), policies, both)
    assert not decision.acceptable and decision.success_witness == frozenset()
    # Both branches complete: frozen formula order picks the first PASS, never model ranking.
    decision = decide_review(reply(a=Grade.PASS, b=Grade.PASS, privacy=Grade.PASS), formula, ("privacy",), policies, both)
    assert decision.success_witness == {"a", "privacy"} and decision.consumed_receipts == (receipt_ref("rcpt-a-check"),)
    # A BLOCKER finding on the chosen branch forces its grade to FAIL.
    blocked = ReviewReply.from_json({"schema_version": 5, "verdict": "ACCEPT", "assessments": [
        {"criterion_id": n, "verdict": "PASS", "evidence_ids": [], "reason": "r", "limitations": []}
        for n in ("a", "b", "privacy")],
        "findings": [{"criterion_id": "b", "severity": "BLOCKER", "reason": "leak"}]})
    decision = decide_review(blocked, formula, ("privacy",), policies, b_ok)
    assert not decision.acceptable and decision.effective_grades["b"] is Grade.FAIL
    # A non-ACCEPT verdict is never acceptable even when every grade passes.
    rework = ReviewReply.from_json({**ACCEPT_REPLY, "verdict": "REWORK", "assessments": [
        {"criterion_id": n, "verdict": "PASS", "evidence_ids": [], "reason": "r", "limitations": []}
        for n in ("a", "b", "privacy")]})
    assert not decide_review(rework, formula, ("privacy",), policies, both).acceptable


# --------------------------------------------------------------------------- A06
def test_review_dispatch_atomic(tmp_path):
    """内容审阅的准备是一个事务：钉住审阅材料那一步写失败（数据库写入故障），包、绑定、调用、钉、
    派发意图、预留一起回滚，模型一次没调；故障排除后只派一次、只调一次。"""

    provider = ReviewScript()

    async def body():
        async with reviewed_mission(tmp_path, provider) as case:
            connection = case.store.connection

            def counts():
                return {
                    "review_packages": connection.execute(
                        "SELECT count(*) FROM review_packages WHERE mission_id=? AND purpose='TASK_CONTENT'",
                        (case.mission_id,)).fetchone()[0],
                    **{table: connection.execute(f"SELECT count(*) FROM {table} WHERE review_key LIKE "  # noqa: S608
                                                 "'assurance-content:%'").fetchone()[0]
                       for table in ("assurance_review_bindings", "assurance_review_invocations", "assurance_blob_pins")},
                    "dispatch_intents": connection.execute(
                        "SELECT count(*) FROM dispatch_intents WHERE kind='critic' AND subject_id LIKE "
                        "'%assurance-content%'").fetchone()[0],
                    "budget_reservations": connection.execute(
                        "SELECT count(*) FROM budget_reservations WHERE subject_id LIKE '%assurance-content%'").fetchone()[0],
                }

            connection.execute("CREATE TRIGGER cut_a06 BEFORE INSERT ON assurance_blob_pins WHEN NEW.review_key "
                               "LIKE 'assurance-content:%' BEGIN SELECT RAISE(ABORT,'disk write failed'); END;")
            with pytest.raises(sqlite3.IntegrityError):  # 现状：写失败逃出本轮主循环（迁移裁决 B4）
                await case.run_until(lambda: case.status() == "COMPLETED", timeout=30)
            assert set(counts().values()) == {0} and not connection.in_transaction
            assert "TASK_CONTENT" not in provider.review_calls
            connection.execute("DROP TRIGGER cut_a06")
            mission = await case.settle()
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            after = counts()
            assert after["review_packages"] == after["assurance_review_invocations"] == 1
            assert after["dispatch_intents"] == 1 and provider.review_calls["TASK_CONTENT"] == 1

    asyncio.run(body())


# --------------------------------------------------------------------------- A07
def test_reviewer_independence(tmp_path):
    """审阅绑定写明作者是谁；正式记录里的审阅员不在作者之列，就是派发那次审阅调用的那个会话。"""

    async def body():
        async for case, _provider in _completed(tmp_path):
            store, mission_id = case.store, case.mission_id
            row = _content_binding(store, mission_id)
            producers = decode(row["binding_json"])["producer_agent_ids"]
            assert producers  # the author is named
            record_binding = decode(store.connection.execute(
                "SELECT b.binding_json FROM assurance_review_record_bindings b JOIN review_records r "
                "ON r.record_id=b.record_id WHERE r.mission_id=? AND r.package_id=? AND r.official=1",
                (mission_id, row["package_id"])).fetchone()[0])
            assert record_binding["reviewer_agent_id"] not in producers
            reviewer = store.connection.execute(
                "SELECT agent_id FROM dispatch_intents i JOIN assurance_review_invocations v "
                "ON v.dispatch_intent_id=i.intent_id WHERE v.review_key=? AND v.ordinal=1",
                (row["review_key"],)).fetchone()[0]
            assert reviewer == record_binding["reviewer_agent_id"]

    asyncio.run(body())


# --------------------------------------------------------------------------- A08
def test_review_source_binding(tmp_path):
    async def body():
        async for case, provider in _completed(tmp_path):
            store, mission_id = case.store, case.mission_id
            row = _content_binding(store, mission_id)
            record_id = store.connection.execute(
                "SELECT record_id FROM review_records WHERE mission_id=? AND package_id=? AND official=1",
                (mission_id, row["package_id"])).fetchone()[0]
            binding = decode(store.connection.execute(
                "SELECT binding_json FROM assurance_review_record_bindings WHERE record_id=?", (record_id,)).fetchone()[0])
            assert binding["mission_id"] == mission_id and binding["invocation_ordinal"] == 1
            # Every source is pinned: mission, turn receipt, raw output hash, package, ordinal, codec.
            base = ReviewRecordBinding.from_json(binding).content_hash
            for change in ({"mission_id": "other"},
                           {"reviewer_turn_ref": {"kind": "agent_turn_receipt",
                                                  "pin": {"id": "t2", "revision": 0, "content_hash": "c" * 64}}},
                           {"invocation_ordinal": 2}, {"codec_version": "assurance-review-reply-v1"},
                           {"package_ref": {**binding["package_ref"], "content_hash": "d" * 64}}):
                assert ReviewRecordBinding.from_json({**binding, **change}).content_hash != base
            _refused(lambda: ReviewRecordBinding.from_json({**binding, "raw_output_hash": "b" * 64}),
                     "REVIEW_RAW_HASH_MISMATCH")
            # One package, one official record, one invocation, one imported turn, one model call.
            assert HtnStore(store).official_review_record(row["package_id"]).record_id == record_id
            assert count(store, "SELECT COUNT(*) FROM assurance_review_invocations WHERE review_key=?",
                         row["review_key"]) == 1
            assert count(store, "SELECT COUNT(*) FROM review_records WHERE package_id=? AND official=1",
                         row["package_id"]) == 1
            assert provider.review_calls["TASK_CONTENT"] == 1

    asyncio.run(body())


# --------------------------------------------------------------------------- A09
def test_review_new_round_identity():
    key1 = purpose_review_key("MISSION_FINAL", "m", "package-1")
    assert key1 == purpose_review_key("MISSION_FINAL", "m", "package-1")  # same round, same key
    assert key1 != purpose_review_key("MISSION_FINAL", "m", "package-2")  # a real new round is a new package
    assert key1 != purpose_review_key("TASK_CONTENT", "m", "package-1")
    assert key1 != purpose_review_key("MISSION_FINAL", "m2", "package-1")
    assert key1.startswith("assurance-mission-final:")
    # A notification/event id is not a round: the key never takes one.
    with pytest.raises(TypeError):
        purpose_review_key("MISSION_FINAL", "m", "package-1", "event-7")  # type: ignore[call-arg]
    _refused(lambda: AssuranceReviewBinding.from_json({"schema_version": 2}), "OBJECT_FIELDS_MISSING")


# --------------------------------------------------------------------------- A10
def test_extra_evidence_exposure():
    report = _seam("evidence-tools-seam.py")
    complete = report["task_content_complete_read"]
    assert complete["record_id"] and complete["appended_label"] and complete["replay_added_batch"] is False
    assert len(complete["batches"]) >= 2 and complete["verdict"].endswith("ACCEPT")
    partial = report["task_content_partial_read"]
    # 只读了半页的那份资料从不进入任何披露批次，引用它被按名拒绝。
    assert partial["rejection"] == "UNEXPOSED_EVIDENCE" and partial["partial_label_disclosed"] is False
    assert report["mission_final_no_attempt"]["record_id"]


# --------------------------------------------------------------------------- A11
def test_scope_pinned_not_latest(tmp_path):
    """被审的那一次尝试结束以后，审阅主体就停了：晚到的回答不能再审它一遍。"""

    async def body():
        async for case, _provider in _completed(tmp_path):
            row = _content_binding(case.store, case.mission_id)
            bound = AssuranceReviewBinding(row["binding_json"])
            assert row["requirements_revision"] == 1
            assert count(case.store, "SELECT requirements_revision FROM acceptances WHERE mission_id=?",
                         case.mission_id) == 1
            assert review_subject_stopped(case.store, bound)

    asyncio.run(body())


# --------------------------------------------------------------------------- A12
def test_check_budget_and_format_bounds():
    report = _seam("critic-format-repair-seam.py")
    assert report["status"] == "PASS"
    counts = report["counts"]
    assert counts["official_records"] == 1 and counts["provider_calls"] == 2  # exactly one funded repair


# --------------------------------------------------------------------------- A15
def test_composition_not_all_children(tmp_path):
    """叶子都验收了，根（复合目标）也不因此就绪：组合审阅要根自己冻结的范围和采用的做法实例，
    叶子的审查包顶替不了。"""

    async def body():
        async with reviewed_mission(tmp_path, ReviewScript(hold="MISSION_FINAL")) as case:
            await case.run_until(lambda: bool(case.events("AcceptanceCommitted")))
            store, mission_id = case.store, case.mission_id
            dispatch = case.world.loop._dispatch_for(mission_id)
            network = dispatch.network(mission_id)
            root = str(network.root_occurrence_ids[0])
            leaves = [str(o.occurrence_id) for o in network.occurrences if str(o.occurrence_id) != root]
            assert set(leaves) <= set(map(str, dispatch.accepted_outputs(mission_id, network).completed_producers))
            assert not dispatch.terminal(mission_id)
            row = _content_binding(store, mission_id)
            package = HtnStore(store).get_review_package(str(row["package_id"]))
            _refused(lambda: composition_subject(store, mission_id=mission_id, package=package,
                                                 occurrence_id=root, accepted={}), "SOURCE_UNAVAILABLE")

    asyncio.run(body())

"""规划器格式三件（2026-09-30 用户同意）：v13 提示词、字段路径反馈、无损补齐。

真机里规划器回复被判"格式错"的主要原因：子结构（assumptions / uncertainties /
goal_type_ref）字段写不全，而同一请求的格式重试把原消息一字不差再发一遍，模型不知道
自己错在哪。这里钉住三件事：v13 把每个子结构的字段和填满的示例写清楚且示例能过解码；
重试和新请求都带上字段路径反馈；goal_type_ref 只缺 version 且 id + content_hash
在 successor_types 里唯一对上时由系统补齐（其它情况照旧严格拒绝）。
"""
from __future__ import annotations

import json

import pytest

from agent_orchestrator.contracts.planning_decisions import (
    AlternativeSummaryV1,
    AssumptionV1,
    PlanningFeedbackV1,
    PlanningUncertaintyV1,
    ReplanTriggerHintV1,
)
from agent_orchestrator.planning.decision_codec import (
    PlanningDecisionCodecError,
    parse_planning_decision,
)
from agent_orchestrator.planning.decision_feedback import (
    codec_field_path,
    feedback_from_decision,
    fill_missing_type_ref_fields,
)
from agent_orchestrator.runtime.role_templates import (
    HIERARCHICAL_PLANNER_VERSIONS,
    PLANNER_HIERARCHICAL_V12,
    PLANNER_HIERARCHICAL_V13,
    PLANNER_HIERARCHICAL_V14_VERSION,
    PLANNING_DECISION_PACKAGE_VERSION,
    PLANNING_DECISION_PROMPT_VERSION,
    hierarchical_planner_pairing_is_valid,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
BUDGETS = {
    "same_request_format_retries_remaining": 0,
    "planning_rounds_remaining": 3,
    "synthesis_asks_remaining": 0,
    "root_review_repairs_remaining": 1,
    "repeated_failure_before_escalation_remaining": None,
}


def _added() -> str:
    full = PLANNER_HIERARCHICAL_V13.instructions
    assert full.startswith(PLANNER_HIERARCHICAL_V12.instructions)
    return full[len(PLANNER_HIERARCHICAL_V12.instructions):]


def test_v13_is_the_prompt_new_missions_bind_on_package_8() -> None:
    # 2026-10-01 HTN 精简片 A：当前包升到第 9 版、只配 v14；v13 仍是第 8 版包的提示词。
    assert PLANNING_DECISION_PROMPT_VERSION == PLANNER_HIERARCHICAL_V14_VERSION
    assert PLANNER_HIERARCHICAL_V13.prompt_version in HIERARCHICAL_PLANNER_VERSIONS
    assert hierarchical_planner_pairing_is_valid(PLANNER_HIERARCHICAL_V13.prompt_version, 8)
    assert hierarchical_planner_pairing_is_valid(
        PLANNING_DECISION_PROMPT_VERSION, PLANNING_DECISION_PACKAGE_VERSION
    )


@pytest.mark.parametrize(
    "fields",
    [
        ("key", "statement", "required_for", "risk", "suggested_predicate_key"),
        ("statement", "severity", "affects"),
        ("method_ref", "label", "disposition", "reason"),
        ("description", "referenced_predicates", "suggested_decision"),
        ("id", "version", "content_hash"),
        ("kind", "id", "semantic_revision", "content_hash"),
    ],
    ids=["assumption", "uncertainty", "alternative", "replan_trigger", "goal_type_ref", "planning_ref"],
)
def test_v13_names_every_required_field_of_each_sub_structure(fields) -> None:
    added = _added()
    for name in fields:
        assert name in added, name


def test_the_listed_fields_match_the_contract() -> None:
    """Guard the list above against the contract moving under it."""

    samples = {
        AssumptionV1: {"key": "k", "statement": "s", "required_for": ["REFINE"], "risk": "LOW",
                       "suggested_predicate_key": None},
        PlanningUncertaintyV1: {"statement": "s", "severity": "LOW", "affects": ["x"]},
        AlternativeSummaryV1: {"method_ref": None, "label": "l", "disposition": "REJECTED", "reason": "r"},
        ReplanTriggerHintV1: {"description": "d", "referenced_predicates": [], "suggested_decision": "REPAIR"},
    }
    for kind, sample in samples.items():
        assert kind.from_json(sample).to_json().keys() == sample.keys()


def test_every_filled_example_in_v13_decodes() -> None:
    examples = [line for line in _added().splitlines() if line.startswith('{"schema_version"')]
    assert len(examples) >= 2
    for line in examples:
        decision = parse_planning_decision(f"<planning_decision>{line}</planning_decision>")
        body = json.loads(line)
        # A filled example: every optional list the model tends to get wrong is non-empty.
        assert body["assumptions"] or body["uncertainties"], line
        assert decision.to_json()["decision_type"] == body["decision_type"]


def test_v13_explains_the_feedback_the_retry_carries() -> None:
    added = _added()
    for word in ("previous_feedback", "field_path", "rejection_codes", "problems"):
        assert word in added, word


@pytest.mark.parametrize(
    ("error", "path"),
    [
        ("decision.assumptions[0] is missing required fields: ['key']", "/assumptions/0"),
        ("repair_successor.goal_type_ref is missing required fields: ['version']",
         "/payload/goal_type_ref"),
        ("uncertainty.affects must be a list", "/uncertainties"),
        ("assumption.risk must be one of ['HIGH', 'LOW', 'MEDIUM']", "/assumptions"),
        ("decision.payload.bindings must be an object", "/payload/bindings"),
        ("no <planning_decision> block in the output", None),
    ],
)
def test_a_codec_error_is_located_with_a_json_pointer(error, path) -> None:
    assert codec_field_path(error) == path


def test_an_unreadable_reply_becomes_field_path_feedback() -> None:
    row = {
        "decision_id": "pdec-1", "status": "UNREADABLE", "rejection_codes": ["MALFORMED_DECISION"],
        "detail": {"error": "decision.assumptions[0] is missing required fields: ['risk']"},
    }
    feedback = feedback_from_decision(row, budgets=BUDGETS)
    assert isinstance(feedback, PlanningFeedbackV1)
    body = feedback.to_json()
    assert body["status"] == "UNREADABLE"
    assert body["rejection_codes"] == ["MALFORMED_DECISION"]
    assert body["problems"][0]["field_path"] == "/assumptions/0"
    assert "risk" in body["problems"][0]["detail"]
    assert body["budgets"]["same_request_format_retries_remaining"] == 0


def test_an_admission_refusal_keeps_its_problems() -> None:
    problem = {"code": "PARAMETER_INVALID", "subject_ref": None, "field_path": "/payload/wait_for",
               "detail": "WAIT target has no matching producer", "expected": None, "observed": None}
    row = {"decision_id": "pdec-2", "status": "REJECTED", "rejection_codes": ["PARAMETER_INVALID"],
           "detail": {"problems": [problem]}}
    assert feedback_from_decision(row, budgets=BUDGETS).to_json()["problems"] == [problem]


def test_an_admission_refusal_without_problems_still_says_why() -> None:
    row = {"decision_id": "pdec-3", "status": "REJECTED", "rejection_codes": ["PARAMETER_INVALID"],
           "detail": {"decision_type": "WAIT", "reason": "WAIT target has no matching producer"}}
    problems = feedback_from_decision(row, budgets=BUDGETS).to_json()["problems"]
    assert problems and "no matching producer" in problems[0]["detail"]


@pytest.mark.parametrize("status", ["COMMITTED", "NO_STATE_CHANGE", "DECODED", "ADMITTED"])
def test_a_decision_that_was_not_refused_gives_no_feedback(status) -> None:
    row = {"decision_id": "pdec-4", "status": status, "rejection_codes": [], "detail": {}}
    assert feedback_from_decision(row, budgets=BUDGETS) is None


def _successor_package(*entries) -> dict:
    return {"successor_types": [{"task_type_ref": dict(entry), "form": "primitive"} for entry in entries]}


def _successor_raw(goal_type_ref) -> dict:
    return {"decision_type": "REPAIR", "payload": {"repair_kind": "PROPOSE_SUCCESSOR",
                                                   "goal_type_ref": dict(goal_type_ref)}}


def test_a_missing_version_is_filled_when_id_and_hash_match_one_type() -> None:
    package = _successor_package({"id": "doc.write", "version": 3, "content_hash": HASH_A},
                                 {"id": "doc.write", "version": 4, "content_hash": HASH_B})
    raw = _successor_raw({"id": "doc.write", "content_hash": HASH_A})
    filled, paths = fill_missing_type_ref_fields(raw, package)
    assert filled["payload"]["goal_type_ref"] == {"id": "doc.write", "version": 3, "content_hash": HASH_A}
    assert paths == ["/payload/goal_type_ref/version"]
    assert "version" not in raw["payload"]["goal_type_ref"]  # the input is never mutated


@pytest.mark.parametrize(
    "goal_type_ref",
    [
        {"id": "doc.write", "content_hash": "c" * 64},            # no type has that hash
        {"id": "doc.other", "content_hash": HASH_A},              # id and hash disagree
        {"id": "doc.write", "version": 9, "content_hash": HASH_A},  # wrong, not missing
        {"content_hash": HASH_A},                                 # two fields missing
    ],
)
def test_anything_but_one_unique_lossless_match_is_left_for_strict_decode(goal_type_ref) -> None:
    package = _successor_package({"id": "doc.write", "version": 3, "content_hash": HASH_A})
    raw = _successor_raw(goal_type_ref)
    filled, paths = fill_missing_type_ref_fields(raw, package)
    assert filled == raw and paths == []


def test_two_types_sharing_id_and_hash_are_ambiguous() -> None:
    package = _successor_package({"id": "doc.write", "version": 3, "content_hash": HASH_A},
                                 {"id": "doc.write", "version": 5, "content_hash": HASH_A})
    raw = _successor_raw({"id": "doc.write", "content_hash": HASH_A})
    assert fill_missing_type_ref_fields(raw, package) == (raw, [])


def test_other_decisions_are_never_touched() -> None:
    package = _successor_package({"id": "doc.write", "version": 3, "content_hash": HASH_A})
    raw = {"decision_type": "REFINE", "payload": {"goal_type_ref": {"id": "doc.write", "content_hash": HASH_A}}}
    assert fill_missing_type_ref_fields(raw, package) == (raw, [])


def test_the_codec_applies_a_fill_before_the_strict_decode() -> None:
    body = {
        "schema_version": 1, "decision_type": "REPAIR", "subject_key": "subject-a",
        "rationale": "r", "reason_refs": [], "assumptions": [], "uncertainties": [],
        "alternatives": [], "replan_triggers": [],
        "payload": {"repair_kind": "PROPOSE_SUCCESSOR",
                    "old_task_ref": {"kind": "task", "id": "t1", "semantic_revision": 1, "content_hash": HASH_B},
                    "obligation_ref": {"kind": "obligation", "id": "o1", "semantic_revision": 1,
                                       "content_hash": HASH_B},
                    "goal_type_ref": {"id": "doc.write", "content_hash": HASH_A}, "bindings": {}},
    }
    text = "<planning_decision>" + json.dumps(body) + "</planning_decision>"
    with pytest.raises(PlanningDecisionCodecError):
        parse_planning_decision(text)
    package = _successor_package({"id": "doc.write", "version": 3, "content_hash": HASH_A})
    seen: list[str] = []

    def fill(raw):
        filled, paths = fill_missing_type_ref_fields(raw, package)
        seen.extend(paths)
        return filled

    decision = parse_planning_decision(text, fill=fill)
    assert decision.to_json()["payload"]["goal_type_ref"]["version"] == 3
    assert seen == ["/payload/goal_type_ref/version"]

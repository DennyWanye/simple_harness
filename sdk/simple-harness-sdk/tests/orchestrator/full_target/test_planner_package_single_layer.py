# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""规划包只组装一层：九个视图，每件事只说一次（HTN 精简 片 C）。

此前规划请求是三层拼出来的：旧收集器先生成一份映射（``plan``、``method_library``、
``applicability``、``facts``、``operators``、``planning_rejected``……），转换层再从这份映射
转出九个视图，最后叠加层把两者**一起**发给模型——同一批事实发两遍；一步失败的完整记录
（审阅员意见连同整份提交内容）在 ``views.failures`` 和 ``repair_requests`` 里各放一份，
真实的包因此顶到 96 KiB 上限。这里钉住新包的四件事：

* 顶层只有协议字段、九个视图、待处理的请求与候选清单；旧映射的字段一个都不在；
* 做法、目标、已采用的做法实例各只在一个视图里出现，引用四元组与 ``visible_refs`` 的写法
  一致，可以原样照抄进决定；
* 一步失败的完整记录只在待处理的修复请求里，``views.failures`` 只是索引；
* 条数上限、整包大小上限照旧，裁掉的行如实记在 ``truncated`` / ``omitted_counts`` 里；
  目标、计划、预算这三样必须在，放不下就拒绝而不是悄悄裁掉。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from h1i_seed import reviewed

from agent_orchestrator.planning.htn import planner_package
from agent_orchestrator.planning.htn.planner_package import (
    MAX_ACCEPTED_RESULTS,
    MAX_FAILURES,
    MAX_PACKAGE_BYTES,
    VIEW_NAMES,
    PlannerPackageError,
    assemble_planner_package,
    failure_outline,
    goal_rows,
    method_rows,
    plan_row,
)
from agent_orchestrator.runtime.role_templates import (
    PLANNER_HIERARCHICAL,
    PLANNING_DECISION_PACKAGE_VERSION,
)
from simple_harness.contracts import canonical_json

#: What the package may carry at the top level.  Anything else is a second place for a
#: fact that already has one.
TOP_LEVEL = {
    "context_builder_version", "package_version", "role", "mode", "mission",
    "planning_protocol", "planning_subjects", "visible_refs", "previous_feedback",
    "decision_limits", "views", "repair_requests", "human_answers", "abandoned_plan_changes", "method_selection",
    "method_proposal_contexts", "sharing_candidates", "successor_types",
    "evidence_predicates", "truncated", "omitted_counts",
}

#: The intermediate mapping the views used to be converted from, and the fields that
#: repeated a view.
GONE = {
    "plan", "method_library", "applicability", "facts", "operators", "planning_rejected",
    "constraint", "output_contract", "planning_attempt", "active_method_instances",
    "data_rebind_candidates", "compensation_candidates",
}


def _first_request(tmp_path: Path, key: str) -> dict[str, Any]:
    """The request of the first round that can choose a method: the main loop opened it on
    the product's deployment after the planner's proposed method passed its independent
    review (``h1i_seed.reviewed``); the root goal is still open."""

    async def case() -> dict[str, Any]:
        async with reviewed(tmp_path, key=key) as (_seed, intent, _provider):
            return {"package": intent.config["planning_package"],
                    "message": intent.config["message"]["content"]}

    return asyncio.run(case())


def test_the_request_is_nine_views_and_nothing_twice(tmp_path: Path) -> None:
    package = _first_request(tmp_path, "single-layer-shape")["package"]
    assert set(package) == TOP_LEVEL
    assert not GONE & set(package)
    # (the package is read back from the stored intent, whose JSON keys are canonical)
    assert sorted(package["views"]) == sorted(VIEW_NAMES) and len(VIEW_NAMES) == 9
    assert package["package_version"] == PLANNING_DECISION_PACKAGE_VERSION

    # the open root goal is said once, in views.goals, with its parameters
    [goal] = [row for row in package["views"]["goals"] if row["open"]]
    assert goal["form"] == "compound" and goal["adopted_method"] is None and goal["params"]
    assert goal["subject_key"] in {row["subject_key"] for row in package["planning_subjects"]}
    assert [row["plan_revision"] for row in package["views"]["plans"]] == [0]

    # a method is said once, and its reference is spelled the way a decision quotes it
    methods = package["views"]["methods"]
    assert methods and all(row["method_ref"] in package["visible_refs"] for row in methods)
    assert all(set(row["method_ref"]) == {"kind", "id", "semantic_revision", "content_hash"}
               for row in methods)
    assert any(report["verdict"] == "APPLICABLE" for row in methods for report in row["applicability"])
    assert all({"steps", "parameters", "applicable_when", "rejected_reasons"} <= set(row)
               for row in methods)
    # the candidate list names the same methods, by id
    offered = {item["method_id"] for choice in package["method_selection"] for item in choice["applicable"]}
    assert offered <= {row["method_ref"]["id"] for row in methods}


def test_the_prompt_names_only_fields_the_package_has(tmp_path: Path) -> None:
    prompt = PLANNER_HIERARCHICAL.instructions
    for stale in ("plan.open_compound_goals", "method_library", "planning_rejected",
                  "data_rebind_candidates", "typed_parameters", "refined_goals_under_repair"):
        assert stale not in prompt, stale
    package = _first_request(tmp_path, "single-layer-prompt")["package"]
    for name in ("views", "method_selection", "method_proposal_contexts", "repair_requests",
                 "human_answers", "previous_feedback", "planning_subjects", "successor_types",
                 "sharing_candidates", "evidence_predicates"):
        assert name in prompt and name in package, name
    for view in ("goals", "plans", "methods", "failures"):
        assert f"      {view}：" in prompt, view


def test_a_failure_is_indexed_in_the_view_and_detailed_only_in_its_repair_request() -> None:
    """The verifier's record of a failed step carries the whole envelope it judged; the
    index keeps the layer and its one-line summary and nothing nested."""

    failure = {
        "failures": [{"layer": "rule_check", "status": "FAIL", "summary": "citation missing",
                      "detail": {"envelope": {"claims": ["x" * 4000]}, "assessment_binding": {"a": 1}}}],
        "reason": "verification_failed", "retryable": True, "evidence": ["a", "b"],
    }
    outline = failure_outline(failure)
    assert outline == {"reason": "verification_failed", "retryable": True,
                       "failures": [{"layer": "rule_check", "status": "FAIL", "summary": "citation missing"}]}
    assert len(json.dumps(outline)) < 200 < len(json.dumps(failure))
    assert failure_outline(None) == {}


class _Budget:
    def to_json(self) -> dict[str, Any]:
        return {"max_tokens": 1000}


class _Mission:
    id = "mission-stub"
    goal = "stub goal"
    success_criteria = ("file:a.md",)
    allowed_tools = ("read",)
    risk_level = "low"
    budget = _Budget()


class _Network:
    mission_id = _Mission.id
    plan_revision = 0
    root_occurrence_ids: tuple[str, ...] = ()
    required_obligations: tuple[str, ...] = ()
    occurrences: tuple[Any, ...] = ()
    task_bindings: tuple[Any, ...] = ()
    method_instances: tuple[Any, ...] = ()
    adopted_instance_ids: tuple[Any, ...] = ()
    order_constraints: tuple[Any, ...] = ()
    data_requirements: tuple[Any, ...] = ()

    def adopted_instance_for(self, occurrence_id: Any) -> Any:
        return None


def _assemble(**views: Any) -> dict[str, Any]:
    rows = {name: () for name in VIEW_NAMES}
    rows.update(goals=goal_rows(_Network()), plans=[plan_row(_Network())])
    rows.update(views)
    return assemble_planner_package(package_version=10, mission=_Mission(), network=_Network(),
                                    views=rows, sections={"repair_requests": []})


def test_the_assembler_needs_exactly_the_nine_views() -> None:
    with pytest.raises(PlannerPackageError, match="nine views"):
        assemble_planner_package(package_version=10, mission=_Mission(), network=_Network(),
                                 views={"goals": ()})
    assert not hasattr(planner_package, "hierarchical_planner_package")
    assert not hasattr(planner_package, "collect_planner_views")


def test_count_caps_keep_the_newest_rows_and_say_what_was_left_out() -> None:
    accepted = [{"acceptance_ref": None, "producer_occurrence": f"occ-{i}"} for i in range(MAX_ACCEPTED_RESULTS + 1)]
    failures = [{"source": "attempt", "reason": f"failure-{i}"} for i in range(MAX_FAILURES + 3)]
    package = _assemble(accepted_results=accepted, failures=failures)
    assert len(package["views"]["accepted_results"]) == MAX_ACCEPTED_RESULTS
    assert [row["reason"] for row in package["views"]["failures"]] == [
        f"failure-{i}" for i in range(MAX_FAILURES)]
    assert package["truncated"] is True
    assert package["omitted_counts"] == {"accepted_results": 1, "failures": 3}
    quiet = _assemble()
    assert quiet["truncated"] is False and quiet["omitted_counts"] == {}


def test_size_pressure_drops_optional_rows_with_a_receipt_and_never_the_mandatory_ones() -> None:
    heavy = [{"observation_ref": None, "proposition_key": str(i), "coverage": "x" * 6000} for i in range(24)]
    package = _assemble(facts=heavy)
    assert len(canonical_json(package).encode("utf-8")) <= MAX_PACKAGE_BYTES
    assert package["truncated"] is True and package["omitted_counts"]["facts"] > 0
    assert len(package["views"]["facts"]) == 24 - package["omitted_counts"]["facts"]
    # goals, the plan and the budgets are mandatory: a request that cannot hold them is refused
    with pytest.raises(PlannerPackageError, match="96 KiB"):
        _assemble(planning_budgets=[{"note": "x" * (MAX_PACKAGE_BYTES + 1)}])


def test_the_method_cap_is_per_goal_type_and_keeps_the_methods_that_can_run() -> None:
    class _Ref:
        def __init__(self, method_id: str) -> None:
            self.method_id, self.version, self.content_hash = method_id, 1, "a" * 64

        def to_json(self) -> dict[str, Any]:
            return {"method_id": self.method_id, "version": 1, "content_hash": self.content_hash}

    class _Contract:
        def __init__(self, signature: str, index: int) -> None:
            self.method_id, self.method_version = f"{signature}-{index:02d}", 1
            self.steps, self.required_capabilities, self.applicable_when = (), (), ()
            self.parameter_schema_ref = None

        def method_ref(self) -> _Ref:
            return _Ref(self.method_id)

    class _Registry:
        def methods_for_signature(self, signature: str) -> tuple[_Contract, ...]:
            return tuple(_Contract(signature, index) for index in range(13))

    rows, omitted = method_rows(_Registry(), ["ship", "review"], first=["ship-12"])
    assert omitted == 2 and len(rows) == 24
    for signature in ("ship", "review"):
        assert sum(row["goal_signature_id"] == signature for row in rows) == 12
    shipped = [row["method_ref"]["id"] for row in rows if row["goal_signature_id"] == "ship"]
    # alphabetically "ship-12" is the thirteenth; it can run now, so it is not the one cut
    assert shipped[0] == "ship-12" and "ship-11" not in shipped


def test_the_failure_index_lists_attempts_first_and_a_retry_finds_its_attempt() -> None:
    """独立核验指出的两处：①"规划被拒"的行没有尝试引用，核对"原样重做"点名的尝试是否在
    本次请求里时不能因为它崩；②规划连续被拒再多，也不能把步骤的失败尝试挤出索引。"""
    from agent_orchestrator.planning.htn.planner_package import attempt_is_indexed, failure_index

    attempts = [(float(i), {"source": "attempt", "reason": "FAILED",
                            "attempt_review_ref": {"kind": "attempt", "id": f"task-1:attempt-{i}"}})
                for i in range(1, 4)]
    rejections = [(100.0 + i, {"source": "planning", "reason": "proposal_unreadable",
                               "attempt_review_ref": None}) for i in range(MAX_FAILURES + 4)]
    rows = failure_index(attempts=attempts, planning=rejections)
    assert [row["source"] for row in rows[:3]] == ["attempt"] * 3
    assert [row["attempt_review_ref"]["id"] for row in rows[:3]] == [
        "task-1:attempt-3", "task-1:attempt-2", "task-1:attempt-1"]  # newest first
    package = _assemble(failures=rows)
    assert len(package["views"]["failures"]) == MAX_FAILURES
    assert attempt_is_indexed(package, "task-1:attempt-1")
    assert not attempt_is_indexed(package, "task-1:attempt-9")
    assert not attempt_is_indexed({}, "task-1:attempt-1")


def test_too_many_references_drop_optional_rows_before_the_request_is_refused() -> None:
    """An accepted result or a fact is optional: when the rows shown would name more
    than 128 references, those rows go first, with a receipt.  Only when the goals
    themselves need more is the request refused."""
    from agent_orchestrator.planning.htn.planner_package import MAX_VISIBLE_REFS

    def accepted(index: int) -> dict[str, Any]:
        return {"acceptance_ref": {"kind": "acceptance", "id": f"acc-{index:03d}",
                                   "semantic_revision": 1, "content_hash": "a" * 64}}

    methods = [{"method_ref": {"kind": "method", "id": f"m-{index:03d}", "semantic_revision": 1,
                               "content_hash": "b" * 64}} for index in range(MAX_VISIBLE_REFS - 4)]
    package = _assemble(methods=methods, accepted_results=[accepted(i) for i in range(10)])
    assert len(package["visible_refs"]) == MAX_VISIBLE_REFS
    assert len(package["views"]["accepted_results"]) == 4
    assert package["truncated"] is True and package["omitted_counts"] == {"accepted_results": 6}
    assert {row["acceptance_ref"]["id"] for row in package["views"]["accepted_results"]} == {
        ref["id"] for ref in package["visible_refs"] if ref["kind"] == "acceptance"}

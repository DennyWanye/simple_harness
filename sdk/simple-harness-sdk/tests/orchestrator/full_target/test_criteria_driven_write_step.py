# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3t: a criterion that needs added tests must be evidenced by a write-step tests port.

Grok fifth-batch H-L3-C1-r1 (SDK 0.12.2 candidate f2dfa64): hidden grader PASS,
Mission FAILED, ``stop_reason=no_dispatchable_work``, 115/320 calls.  Two root
reviews REJECTED for the same true finding: verify's REPORT.md claimed
``tests/test_concurrent_*.py`` was added and green, inspect/summarize on the same
tree proved the file absent.  The synthesised methods' write steps only declared
a ``patch`` port; the verify leaf (read-only) wrote the tests itself, those
writes never became accepted products, and after ``max_root_review_repairs``
the stall hid the bound under ``admitted_not_dispatched``.

This slice: the admission protocol refuses a method whose criterion evidence
requires added tests unless a write step declares a ``tests`` output port that
verify binds; the synthesizer prompt and request carry that requirement; a
read-only Worker is told to report a missing test as a finding; exhausting
root-review repairs is a named stop.

2026-10-03（HTN 补齐阶段 A′）：这里的主循环用例早已没有，旧的代码领域搭建（裸 ``CommitService``
建任务、``install_hierarchical(planning=)``）随之删掉；只留六条直接测函数的用例（覆盖层、做法
受理、请求材料、执行者提示词），代码领域做法只当素材。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_inspect_leaf_patch_input import _c1_method  # noqa: E402

from agent_orchestrator.planning.htn.seed_methods.loader import seed_content_hash  # noqa: E402
from agent_orchestrator.planning.htn.world import build_planning_world  # noqa: E402

TESTS_PATH = "tests/test_concurrent_contract.py"


def _four_step(method_id: str, *, suffix: str = "") -> dict[str, Any]:
    """facts → reproduce → apply-patch → verify; both root criteria hang on verify
    （原在 ``test_read_only_rewrite_bound``，那里的主循环用例已删，搬来这里作素材）。"""

    rename = {
        "read-facts": f"read-facts{suffix}",
        "reproduce": f"reproduce{suffix}",
        "apply-patch": f"apply-patch{suffix}",
        "verify": f"verify{suffix}",
    }
    body = copy.deepcopy(_c1_method())
    body["method_id"] = method_id
    kept = []
    for item in body["steps"]:
        if item["local_id"] not in rename:
            continue
        item["local_id"] = rename[item["local_id"]]
        for binding in item.get("arguments", {}).values():
            if isinstance(binding, dict) and binding.get("step") in rename:
                binding["step"] = rename[binding["step"]]
        kept.append(item)
    body["steps"] = kept
    body["ordering"] = [
        {"after": rename["reproduce"], "before": rename["read-facts"]},
        {"after": rename["apply-patch"], "before": rename["reproduce"]},
        {"after": rename["verify"], "before": rename["apply-patch"]},
    ]
    body["composition"] = {
        "criterion_links": [
            {
                "child_criterion_id": "c-tests-verified",
                "child_step": rename["verify"],
                "evidence_requirement": "verify-tests report after the applied patch",
                "parent_criterion_id": "c-test-passes",
            },
            {
                "child_criterion_id": "c-tests-verified",
                "child_step": rename["verify"],
                "evidence_requirement": "the report explains the applied change",
                "parent_criterion_id": "c-change-explained",
            },
        ],
        "finalizer_step": rename["verify"],
        "independent_review_required": True,
        "outputs": {},
    }
    body["required_capabilities"] = ["repo.read", "repo.write", "tests.run"]
    return body


def _type_ref(type_id: str, version: int) -> dict[str, Any]:
    return {
        "id": type_id,
        "version": version,
        "content_hash": seed_content_hash(type_id, version),
    }


def _bind_tests(method: dict[str, Any], *, require: bool) -> dict[str, Any]:
    """facts → apply-patch → verify, with or without a tests port on the write step."""

    body = copy.deepcopy(method)
    apply_id = next(
        item["local_id"]
        for item in body["steps"]
        if item["task_type_ref"]["id"] == "code.apply-patch"
    )
    verify_id = next(
        item["local_id"]
        for item in body["steps"]
        if item["task_type_ref"]["id"] == "code.verify-tests"
    )
    for item in body["steps"]:
        if item["local_id"] == apply_id:
            item["task_type_ref"] = _type_ref("code.apply-patch", 2 if require else 1)
        if item["local_id"] == verify_id:
            item["task_type_ref"] = _type_ref("code.verify-tests", 2 if require else 1)
            arguments = dict(item.get("arguments") or {})
            if require:
                arguments["tests"] = {"op": "output", "port": "tests", "step": apply_id}
            else:
                arguments.pop("tests", None)
            item["arguments"] = arguments
    for link in body["composition"]["criterion_links"]:
        if link["parent_criterion_id"] in {"c-test-passes", "c-contract-tests-pass"}:
            link["evidence_requirement"] = (
                "tests covering the user contract were added or turned from red "
                "to green and pass"
            )
    return body


def _tests_method(method_id: str, *, suffix: str = "") -> dict[str, Any]:
    return _bind_tests(_four_step(method_id, suffix=suffix), require=True)


def _missing_tests_method(method_id: str, *, suffix: str = "") -> dict[str, Any]:
    return _bind_tests(_four_step(method_id, suffix=suffix), require=False)


# ======================================================================================
# 1. The rule
# ======================================================================================


def test_overlay_places_new_test_files_that_are_not_on_the_seed() -> None:
    from agent_orchestrator.artifacts.bound_workspace import overlay_bound_producer_files
    from agent_orchestrator.artifacts.versioning import UpstreamInput
    from agent_orchestrator.contracts.models import Artifact

    producer = "task-apply"
    inputs = [UpstreamInput(producer, "applied.patch", "a" * 64, "artifact-patch")]
    tests = Artifact(
        id="artifact-tests",
        mission_id="m",
        task_id=producer,
        attempt_id=f"{producer}:1",
        type="file",
        path=TESTS_PATH,
        version=1,
        content_hash="b" * 64,
        size_bytes=8,
        produced_by="w",
        storage_uri="",
    )
    overlay = overlay_bound_producer_files(
        inputs,
        seed_paths={"metrics/collector.py", "tests/test_public_collector.py"},
        artifacts_by_producer={producer: [tests]},
    )
    assert TESTS_PATH in {item.path for item in overlay}


def test_overlay_drops_new_tests_written_by_a_read_only_leaf() -> None:
    """P2.3u P2-1: a read-only leaf's new tests/ files must not pre-lay downstream."""

    from agent_orchestrator.artifacts.bound_workspace import overlay_bound_producer_files
    from agent_orchestrator.artifacts.versioning import UpstreamInput
    from agent_orchestrator.contracts.models import Artifact

    producer = "task-verify"
    inputs = [UpstreamInput(producer, "REPORT.md", "a" * 64, "artifact-report")]
    tests = Artifact(
        id="artifact-tests",
        mission_id="m",
        task_id=producer,
        attempt_id=f"{producer}:1",
        type="file",
        path=TESTS_PATH,
        version=1,
        content_hash="b" * 64,
        size_bytes=8,
        produced_by="w",
        storage_uri="",
    )
    overlay = overlay_bound_producer_files(
        inputs,
        seed_paths={"metrics/collector.py"},
        artifacts_by_producer={producer: [tests]},
        read_only_producers={producer},
    )
    assert TESTS_PATH not in {item.path for item in overlay}


def test_the_method_context_carries_criterion_evidence() -> None:
    from agent_orchestrator.planning.htn.method_proposals import build_context

    env = build_planning_world("p23t-request", domains=("code",))
    goal = next(
        spec
        for spec in env.catalog.task_types()
        if spec.task_type_ref.id == "code.implement-contract"
    )
    from agent_orchestrator.contracts.htn import (
        ObligationId,
        TaskForm,
        TaskRef,
        TaskSemanticBindingV1,
    )

    binding = TaskSemanticBindingV1(
        task_id=TaskRef("task-root"),
        obligation_id=ObligationId("obl-root"),
        contract_revision=1,
        contract_hash="a" * 64,
        form=TaskForm.COMPOUND,
        goal_signature=goal.goal_signature,
        typed_parameters={"repository": "<workspace>"},
        requirement_refs=tuple(goal.goal_signature.coverage_criteria),
        semantic_scope="mission",
    )
    request = build_context(binding, env.capabilities(), env.registry, catalog=env.catalog)
    payload = request.to_json()
    evidence = payload["criterion_evidence"]
    ids = {item["id"] for item in evidence}
    assert "c-contract-tests-pass" in ids, evidence
    blob = " ".join(item["evidence_requirement"] for item in evidence)
    assert "tests" in blob
    apply_offer = next(
        item for item in payload["operators"] if item["task_type_ref"]["id"] == "code.apply-patch"
    )
    assert apply_offer["task_type_ref"]["version"] == 2
    assert "tests" in apply_offer["output_ports"]
    verify_offer = next(
        item for item in payload["operators"] if item["task_type_ref"]["id"] == "code.verify-tests"
    )
    assert verify_offer["task_type_ref"]["version"] == 2
    assert "tests" in verify_offer["input_ports"]


def _admit(env, body: dict[str, Any]):
    from agent_orchestrator.contracts.htn import MethodContract, RegistryAuthor
    from agent_orchestrator.planning.htn.registry import MethodProposal

    return env.registry.admit(
        MethodProposal(
            method=MethodContract.from_json(body),
            author=RegistryAuthor.MODEL,
        ),
        author=RegistryAuthor.MODEL,
        policy=env.policy(),
    )


def test_a_method_whose_criteria_mention_added_tests_is_not_refused_for_lacking_a_tests_port() -> None:
    """片 0 第 3 步（2026-10-01）：判据里出现"新增测试"之类的词，不再由程序强制要求测试端口。

    此前注册检查在判据文字里找关键词（"新增测试""contract test"……），找到就要求做法里有
    写类型步骤声明 ``tests`` 输出端口并被验证步骤接上，否则拒收。这是程序在假装理解语义：
    桌面没有这个端口，用户目标一写这几个字整局就规划不出来。测试写没写、够不够由审阅员判。
    """

    env = build_planning_world("p23t-admit", domains=("code",))
    receipt = _admit(env, _missing_tests_method("code.fix-by-patch-then-verify.no-tests"))
    assert receipt.admitted, receipt.problems


def test_a_method_that_declares_and_binds_the_tests_port_is_admitted() -> None:
    env = build_planning_world("p23t-admit-ok", domains=("code",))
    receipt = _admit(env, _tests_method("code.fix-by-patch-then-verify.with-tests"))
    assert receipt.admitted, receipt.problems


def test_the_read_only_worker_prompt_reports_missing_tests_as_findings() -> None:
    from agent_orchestrator.runtime.role_templates import WORKER_HIERARCHICAL

    text = WORKER_HIERARCHICAL.instructions
    assert "finding" in text.lower() or "finding" in text
    assert "不要自己" in text or "不要自己写" in text or "不要自己创建" in text

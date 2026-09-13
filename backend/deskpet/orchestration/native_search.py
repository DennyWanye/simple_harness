# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Bounded P34 source-native Mission Provider; controlled data, real SDK commits.

Callbacks carry no attempt counters or Store handles: actions derive from each SDK
request and its actual workspace tool replies. Cold reopening of a completed Mission
is covered; interruption during a Provider/tool call needs separate recovery proof.
This is not evidence of real-model reasoning.
"""

from __future__ import annotations

import json
from typing import Any

CASE = "p34-fragment-crossbranch"
TOOLS = ["workspace_read_file", "workspace_write_file", "workspace_list", "run_tests"]
PARTIAL = "partial A\n"
BRANCH = "branch B\n"
VALIDATED = "validated F\n"
CONSUMED = "C used F and B\n"
CONSUMER = "C complete\n"
FINAL = "synthesized C\n"
PROBE = "tests/test_consumer.py"
SYNTHESIS_PROBE = "tests/test_synthesis.py"


def native_search_mission() -> dict[str, Any]:
    """UI Mission input; the explicit hard Mission and synthesis bounds are retained."""
    return {
        "goal": "受控验证失败 A 的有效文件片段经独立 F 复核后与 B 合入 C，再由 S 读取已验证 C。",
        "success_criteria": ["file:final.md"],
        "domain": "code-v1",  # current code profile uses manager-v3
        "idempotency_key": CASE,
        "budget": {"max_tokens": 450_000, "max_attempts": 20},
        "synthesis": {
            "goal": "independently synthesize consumer C",
            "success_criteria": ["file:final.md", f"pytest:{SYNTHESIS_PROBE}"],
            "budget": {"max_tokens": 30_000, "max_attempts": 2},
        },
    }


def _values(request: Any) -> list[dict[str, Any]]:
    values = []
    for message in request.messages:
        if str(message.role) != "tool":
            continue
        reply = json.loads(message.content)
        value = reply.get("value")
        if not isinstance(value, dict):
            raise TypeError("controlled P34 tool call did not succeed")
        values.append(value)
    return values


def _written(request: Any, path: str) -> bool:
    return any(value.get("path") == path and "bytes" in value for value in _values(request))


def _read(request: Any, path: str, expected: str) -> tuple[str, dict[str, Any]] | None:
    pages = [value for value in _values(request)
             if value.get("path") == path and "content" in value]
    if not pages:
        return "workspace_read_file", {"path": path}
    content = "".join(page["content"] for page in pages)
    if pages[-1].get("next_offset") is not None:
        return "workspace_read_file", {"path": path, "offset": pages[-1]["next_offset"],
                                       "expected_sha256": pages[-1]["sha256"]}
    if content != expected:
        raise ValueError(f"actual workspace read differs from controlled P34 material: {path}")
    return None


def native_search_provider() -> Any:
    """Fresh Provider for native UI wiring or direct public-Orchestrator injection."""
    from agent_orchestrator.testing.fixtures import (
        RoleScriptedProvider,
        envelope_step,
        graph_proposal_step,
        package_of,
    )

    def planner(request: Any) -> str:
        package = package_of(request)
        if package["mission"]["success_criteria"] != ["file:final.md"]:
            raise ValueError("P34 Mission criteria changed")
        nodes = [
            {"key": "A", "goal": "origin A", "rationale": "leave an actual partial file",
             "dependencies": [], "success_criteria": ["file:good.md", "file:missing.md"],
             "verification_policy": ["format_check", "rule_check"],
             "allowed_tools": TOOLS, "outputs": ["good.md", "missing.md"],
             "priority": 0.5, "budget": {"max_tokens": 120_000, "max_attempts": 3}},
            {"key": "B", "goal": "independent B", "rationale": "independent branch",
             "dependencies": [], "success_criteria": ["file:b.md"],
             "verification_policy": ["format_check", "rule_check"],
             "allowed_tools": TOOLS, "outputs": ["b.md"],
             "priority": 0.1, "budget": {"max_tokens": 30_000, "max_attempts": 2}},
            {"key": "C", "goal": "consumer C", "rationale": "consume A replacement and B",
             "dependencies": ["A", "B"],
             "success_criteria": ["file:good.md", "file:consumer.md", f"pytest:{PROBE}"],
             "verification_policy": ["format_check", "rule_check", "code_test"],
             "allowed_tools": TOOLS, "outputs": ["good.md", "consumer.md", PROBE],
             "budget": {"max_tokens": 30_000, "max_attempts": 2}},
        ]
        return graph_proposal_step(nodes)

    def worker(request: Any) -> Any:
        package = package_of(request)
        if "fragment_scope" in package:
            mapping = package["fragment_scope"]["output_path_mapping"]
            mapped = mapping["good.md"]
            pending = _read(request, "good.md", PARTIAL)
            if pending is not None:
                return pending
            if not _written(request, mapped):
                return "workspace_write_file", {"path": mapped, "content": VALIDATED}
            return envelope_step(summary="F independently checked", artifacts=[mapped],
                                 claims=["validated the selected good.md fragment"])(request)
        goal = package["task_contract"]["goal"]
        if goal == "origin A":
            if not _written(request, "good.md"):
                return "workspace_write_file", {"path": "good.md", "content": PARTIAL}
            return envelope_step(summary="A partial", artifacts=["good.md"],
                                 claims=["good.md is partial"])(request)
        if goal == "independent B":
            if not _written(request, "b.md"):
                return "workspace_write_file", {"path": "b.md", "content": BRANCH}
            return envelope_step(summary="B independent", artifacts=["b.md"],
                                 claims=["independent B exists"])(request)
        if goal != "consumer C":
            raise ValueError(f"unexpected P34 Worker goal: {goal}")
        frozen = package["validated_fragment_input"]
        if not frozen["validation_result_id"]:
            raise ValueError("C received no accepted F result")
        mapped = frozen["material_refs"][0]["path"]
        for path, content in ((mapped, VALIDATED), ("b.md", BRANCH)):
            pending = _read(request, path, content)
            if pending is not None:
                return pending
        for path, content in (("good.md", CONSUMED), ("consumer.md", CONSUMER)):
            if not _written(request, path):
                return "workspace_write_file", {"path": path, "content": content}
        if not _written(request, PROBE):
            probe = (
                "from pathlib import Path\n\n"
                "def test_consumer_used_both_validated_inputs():\n"
                f"    assert Path({mapped!r}).read_text() == {VALIDATED!r}\n"
                f"    assert Path('b.md').read_text() == {BRANCH!r}\n"
                f"    assert Path('good.md').read_text() == {CONSUMED!r}\n"
                f"    assert Path('consumer.md').read_text() == {CONSUMER!r}\n"
            )
            return "workspace_write_file", {"path": PROBE, "content": probe}
        tests = [value for value in _values(request) if "passed" in value]
        if not tests:
            return "run_tests", {"path": PROBE}
        if tests[-1]["passed"] is not True:
            raise ValueError("actual C bounded probe failed")

        def cite_probe(body: dict[str, Any]) -> dict[str, Any]:
            body["claims"][0]["evidence"] = [f"pytest:{PROBE}"]
            body["evidence"].append(f"pytest:{PROBE}")
            return body

        return envelope_step(summary="C consumed F and B", artifacts=["good.md", "consumer.md", PROBE],
                             claims=["C incorporated accepted F and independent B"],
                             override=cite_probe)(request)

    def manager(request: Any) -> str:
        package = package_of(request)
        if "validated_fragment" not in package:
            catalog = package["fragment_validation"]
            [artifact] = [item for item in catalog["materials"]
                          if item["kind"] == "artifact" and item["path"] == "good.md"]
            [criterion] = [item["id"] for item in catalog["criteria"]
                           if item["text"] == "file:good.md"]
            body = {"schema_version": 1, "base_graph_version": package["graph_version"],
                    "proposal": {"schema_version": 1, "origin": catalog["origin"],
                                 "criterion_ids": [criterion], "claim_refs": [],
                                 "material_refs": [{"kind": "artifact",
                                                    "artifact_id": artifact["artifact_id"],
                                                    "content_hash": artifact["content_hash"],
                                                    "byte_start": 0,
                                                    "byte_end_exclusive": artifact["size_bytes"]}],
                                 "rationale": "independently check A's partial file"}}
            return "<fragment_validation_decision>" + json.dumps(body) + "</fragment_validation_decision>"
        accepted = package["validated_fragment"]
        if not accepted["validation_result_id"]:
            raise ValueError("Manager received no accepted F")
        tasks = {item["goal"]: item["task_id"] for item in accepted["tasks"]}
        body = {"base_graph_version": package["graph_version"],
                "rationale": "C waits for accepted F and independent B",
                "operations": [
                    {"op": "retarget_dependencies", "task_id": tasks["consumer C"],
                     "dependencies": [accepted["validation_task_id"], tasks["independent B"]]},
                    {"op": "cancel_task", "task_id": tasks["origin A"],
                     "reason": "whole A remains incomplete; preserve failed evidence"},
                ]}
        return "<graph_change_proposal>" + json.dumps(body) + "</graph_change_proposal>"

    def synthesis(request: Any) -> Any:
        package = package_of(request)
        verified = [item["id"] for item in package["verified_knowledge"]
                    if item.get("content") == "C incorporated accepted F and independent B"]
        if len(verified) != 1:
            raise ValueError("S did not receive exactly one verified C knowledge")
        pending = _read(request, "consumer.md", CONSUMER)
        if pending is not None:
            return pending
        if not _written(request, "final.md"):
            return "workspace_write_file", {"path": "final.md", "content": FINAL}
        if not _written(request, SYNTHESIS_PROBE):
            probe = (
                "from pathlib import Path\n\n"
                "def test_synthesis_reads_verified_consumer():\n"
                f"    assert Path('consumer.md').read_text() == {CONSUMER!r}\n"
                f"    assert Path('final.md').read_text() == {FINAL!r}\n"
            )
            return "workspace_write_file", {"path": SYNTHESIS_PROBE, "content": probe}
        tests = [value for value in _values(request) if "passed" in value]
        if not tests:
            return "run_tests", {"path": SYNTHESIS_PROBE}
        if tests[-1]["passed"] is not True:
            raise ValueError("actual S bounded probe failed")

        def cite_c(body: dict[str, Any]) -> dict[str, Any]:
            body["used_knowledge"] = verified
            body["claims"][0]["evidence"] = [f"pytest:{SYNTHESIS_PROBE}"]
            body["evidence"].append(f"pytest:{SYNTHESIS_PROBE}")
            return body

        return envelope_step(summary="S independently synthesized",
                             artifacts=["final.md", SYNTHESIS_PROBE],
                             claims=["final.md reflects independently verified C"],
                             override=cite_c)(request)

    # Each callback is request-derived. Queues are deliberately bounded per factory.
    return RoleScriptedProvider({"planner": [planner] * 4, "worker": [worker] * 48,
                                 "manager": [manager] * 8, "synthesizer": [synthesis] * 8})


__all__ = ("CASE", "native_search_mission", "native_search_provider")

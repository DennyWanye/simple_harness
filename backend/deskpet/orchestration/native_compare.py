# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Bounded, test-only P34 COMPARE fixture; policy evidence is not model quality.

All work goes through the SDK Provider, workspace tools and real Verifier.  The
policy fixture is installed only after an isolated Orchestrator has opened.
"""

from __future__ import annotations

import json
from typing import Any

CASE = "p34-approved-compare"
TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list", "run_tests")
RESULT = "result.txt"
PROBE = "tests/test_result.py"
COMBINED = "combined.md"
COPIES = ("comparison-inputs/first.txt", "comparison-inputs/second.txt")
VALUES = {1: "candidate A\n", 2: "candidate B\n"}
FINAL = "synthesis C used candidate A and candidate B\n"


def native_compare_mission(version_id: str | None = None) -> dict[str, Any]:
    """UI input.  Pass the approved version selected in MissionsView for COMPARE."""
    request: dict[str, Any] = {
        "goal": "受控比较两个已验证代码候选，并由 C 独立读取二者后交付结果。",
        "success_criteria": [f"file:{RESULT}", f"pytest:{PROBE}"],
        "domain": "code-v1",
        "idempotency_key": CASE,
        "budget": {"max_tokens": 8_000_000, "max_attempts": 4},  # 2026-09-30 旧式池删除：原生池下限 = 一轮 + 审阅（256K 约 59 万）
    }
    if version_id is not None:
        request["search_policy_version_id"] = version_id
    return request


def selection_policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "COMPARE_THEN_SYNTHESIZE",
        "max_candidates": 2,
        "deadline_seconds": 120.0,
        "tie_break": "verified_rank_then_result_id-v1",
        "synthesis_limit": 1,
        "on_deadline": "best_complete_else_stop",
        "synthesis_reserve": {"tokens": 16_000, "cost_micros": 0, "tool_calls": 0},
        "synthesis_attempts_reserved": 1,
    }


def install_compare_policy(orch: Any, principal: Any) -> str:
    """Public policy lifecycle, idempotent on a completed or interrupted reopen.

    Only call from the explicit document-ui fixture case after ``__aenter__``.
    The fixture evaluation certifies API plumbing alone, never candidate quality.
    """
    from agent_orchestrator.contracts.models import sha256_hex
    from agent_orchestrator.governance.promotion import code_versions

    base = orch.store.active_policy()
    if base is None:
        raise RuntimeError("COMPARE fixture requires an opened policy library")
    params = {**base["params"], "schema_version": 2,
              "candidates_per_task": 2, "search_selection": selection_policy()}
    label = {"oracle": CASE, "evidence_kind": "fixture",
             "scope": "public policy lifecycle only; no model quality claim"}
    existing = [p for p in orch.store.list_policy_proposals()
                if p.get("manifest", {}).get("oracle") == CASE]
    if len(existing) > 1:
        raise RuntimeError("multiple COMPARE fixture proposals in one library")
    if existing:
        proposal = existing[0]
        version = orch.store.get_policy_version(proposal["version_id"])
        if (version is None or version["params"].get("search_selection") != selection_policy()
                or version["params"].get("candidates_per_task") != 2):
            raise RuntimeError("COMPARE fixture policy changed")
        if proposal["version_id"] == base["version_id"]:
            if proposal["state"] != "PROMOTED":
                raise RuntimeError("COMPARE fixture active version lacks promotion")
            return proposal["version_id"]
        if proposal["base_version_id"] != base["version_id"]:
            raise RuntimeError("COMPARE fixture baseline changed")
    else:
        proposal = orch.commit.propose_policy(
            params, manifest=label, source="fixture", principal=principal,
        )
    if proposal["state"] == "PROPOSED":
        orch.commit.record_policy_evaluation(
            proposal["proposal_id"], verdict="PASSED",
            reasons=["FIXTURE policy lifecycle only; no model quality evaluation"],
            report_hash=sha256_hex(label), baseline_version_id=base["version_id"],
            code_versions=code_versions(), evidence_kind="fixture",
        )
        proposal = orch.store.get_policy_proposal(proposal["proposal_id"])
    if proposal["state"] == "PASSED":
        orch.commit.decide_policy(
            proposal["proposal_id"], principal=principal, decision="approve",
            nonce=CASE + "-approval",
        )
        proposal = orch.store.get_policy_proposal(proposal["proposal_id"])
    if proposal["state"] != "APPROVED":
        raise RuntimeError(f"COMPARE fixture proposal is {proposal['state']}")
    orch.commit.promote_policy(
        proposal["proposal_id"], principal=principal, cooldown_seconds=0,
        accept_fixture_evidence=True,
    )
    return proposal["version_id"]


def _values(request: Any) -> list[dict[str, Any]]:
    values = []
    for message in request.messages:
        if str(message.role) != "tool":
            continue
        reply = json.loads(message.content)
        value = reply.get("value")
        if not isinstance(value, dict):
            raise TypeError("controlled COMPARE tool call failed")
        values.append(value)
    return values


def _written(request: Any, path: str) -> bool:
    return any(v.get("path") == path and "bytes" in v for v in _values(request))


def native_compare_provider() -> Any:
    """Fresh bounded Provider for one UI Mission or direct public Orchestrator run."""
    from agent_orchestrator.testing.fixtures import (
        RoleScriptedProvider,
        envelope_step,
        graph_proposal_step,
        package_of,
    )

    task = {
        "key": "compare", "goal": "produce verified result", "rationale": "two complete candidates",
        "dependencies": [], "success_criteria": [f"file:{RESULT}", f"pytest:{PROBE}"],
        "verification_policy": ["format_check", "rule_check", "code_test"],
        "allowed_tools": list(TOOLS), "outputs": [RESULT, PROBE, COMBINED, *COPIES],
        "budget": {"max_tokens": 1_200_000, "max_attempts": 3},
    }

    def worker(request: Any) -> Any:
        ordinal = package_of(request)["attempt"]["ordinal"]
        if ordinal not in VALUES:
            raise ValueError(f"unexpected COMPARE candidate ordinal {ordinal}")
        value = VALUES[ordinal]
        if not _written(request, RESULT):
            return "workspace_write_file", {"path": RESULT, "content": value}
        if not _written(request, PROBE):
            probe = ("from pathlib import Path\n\n"
                     "def test_candidate_output():\n"
                     f"    assert Path({RESULT!r}).read_text() == {value!r}\n")
            return "workspace_write_file", {"path": PROBE, "content": probe}
        tests = [v for v in _values(request) if "passed" in v]
        if not tests:
            return "run_tests", {"path": PROBE}
        if tests[-1]["passed"] is not True:
            raise ValueError("actual candidate runtime test failed")

        def cite_test(body: dict[str, Any]) -> dict[str, Any]:
            body["claims"][0]["evidence"] = [f"pytest:{PROBE}"]
            body["evidence"].append(f"pytest:{PROBE}")
            return body

        return envelope_step(summary=f"candidate {ordinal} tested", artifacts=[RESULT, PROBE],
                             claims=[f"candidate {ordinal} passed its runtime probe"],
                             override=cite_test)(request)

    def synthesis(request: Any) -> Any:
        package = package_of(request)
        inputs = package["selection_inputs"]["inputs"]
        result_inputs = [v for v in inputs if v["path"].endswith("/" + RESULT)]
        if len(result_inputs) != 2 or len({v["path"] for v in result_inputs}) != 2:
            raise ValueError("C did not receive two distinct selected candidate results")
        paths = sorted(v["path"] for v in result_inputs)
        read_values = [v["content"] for v in _values(request)
                       if v.get("path") in paths and "content" in v]
        for path in paths:
            if not any(v.get("path") == path and "content" in v for v in _values(request)):
                return "workspace_read_file", {"path": path}
        if sorted(read_values) != sorted(VALUES.values()):
            raise ValueError("C did not read both actual candidate contents")
        # The Mission judge gets published artifacts, not C's temporary candidate
        # mounts. Publish the bytes actually read so its final probe is portable.
        for source, target in zip(paths, COPIES, strict=True):
            if not _written(request, target):
                content = next(v["content"] for v in _values(request)
                               if v.get("path") == source and "content" in v)
                return "workspace_write_file", {"path": target, "content": content}
        combined = "# C read both candidates\n" + "\n".join(paths) + "\n"
        if not _written(request, COMBINED):
            return "workspace_write_file", {"path": COMBINED, "content": combined}
        if not _written(request, RESULT):
            return "workspace_write_file", {"path": RESULT, "content": FINAL}
        if not _written(request, PROBE):
            probe = (
                "from pathlib import Path\n\n"
                "def test_synthesis_consumed_both_candidates():\n"
                f"    assert Path({COPIES[0]!r}).read_text() in {tuple(VALUES.values())!r}\n"
                f"    assert Path({COPIES[1]!r}).read_text() in {tuple(VALUES.values())!r}\n"
                f"    assert Path({COPIES[0]!r}).read_text() != Path({COPIES[1]!r}).read_text()\n"
                f"    assert Path({COMBINED!r}).read_text() == {combined!r}\n"
                f"    assert Path({RESULT!r}).read_text() == {FINAL!r}\n"
            )
            return "workspace_write_file", {"path": PROBE, "content": probe}
        tests = [v for v in _values(request) if "passed" in v]
        if not tests:
            return "run_tests", {"path": PROBE}
        if tests[-1]["passed"] is not True:
            raise ValueError("actual C runtime test failed")

        def cite_inputs(body: dict[str, Any]) -> dict[str, Any]:
            body["claims"][0]["evidence"] = [f"pytest:{PROBE}", COMBINED]
            body["evidence"].append(f"pytest:{PROBE}")
            return body

        return envelope_step(summary="C synthesized both candidates",
                             artifacts=[RESULT, PROBE, COMBINED, *COPIES],
                             claims=["C read both selected candidates and passed its runtime probe"],
                             override=cite_inputs)(request)

    return RoleScriptedProvider({
        "planner": [graph_proposal_step([task])] * 2,
        "worker": [worker] * 16,
        "synthesizer": [synthesis] * 16,
    }, model="agent-model")


__all__ = (
    "CASE",
    "COMBINED",
    "PROBE",
    "RESULT",
    "TOOLS",
    "install_compare_policy",
    "native_compare_mission",
    "native_compare_provider",
    "selection_policy",
)

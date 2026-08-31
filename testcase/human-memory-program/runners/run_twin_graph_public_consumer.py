#!/usr/bin/env python3
"""Fail-closed black-box assets for the display-only twin graph.

Self-check validates only independent DTO/hash/privacy known answers. Formal
product execution remains blocked until Task 6 freezes both the exact candidate
wheel identity and an exact package-root public manager seeding contract.
Canonical fixture rows are never passed to get_twin_graph_view.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any


BLOCKED_EXIT = 3
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _node_ids(graph: dict[str, Any]) -> set[str]:
    return {node["node_id"] for node in graph["nodes"]}


def _edge_ids(graph: dict[str, Any]) -> set[str]:
    return {edge["edge_id"] for edge in graph["edges"]}


def self_check(fixture_path: Path) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if fixture.get("fixture_revision") != 1:
        errors.append("fixture_revision must be 1")
    if fixture.get("quality_gate") != "NOT_RUN/BLOCKED":
        errors.append("quality gate must remain NOT_RUN/BLOCKED")
    consumer = fixture.get("public_consumer", {})
    if consumer.get("allowed_import_root") != "simple_harness_memory":
        errors.append("only the public simple_harness_memory package root is allowed")
    if consumer.get("public_graph_method") != "get_twin_graph_view":
        errors.append("the only graph read port must be get_twin_graph_view")
    if consumer.get("candidate_identity", {}).get("status") not in {
        "PENDING_TASK6_FINAL_PIN",
        "PINNED",
    }:
        errors.append("candidate identity status is invalid")
    seed_contract = consumer.get("public_manager_seed_contract", {})
    if seed_contract.get("status") not in {
        "PENDING_TASK6_PUBLIC_SEED_API_PIN",
        "PINNED",
    }:
        errors.append("public manager seed contract status is invalid")
    if "canonical rows must never be passed" not in seed_contract.get(
        "required_rule", ""
    ):
        errors.append("public DB seeding prohibition is not explicit")

    contract = fixture["dto_contract"]
    graph_fields = contract["graph_fields"]
    node_fields = contract["node_fields"]
    edge_fields = contract["edge_fields"]
    by_id: dict[str, dict[str, Any]] = {}
    for case in fixture["cases"]:
        case_id = case["id"]
        if "request" in case:
            errors.append(f"fixture rows exposed as graph request: {case_id}")
        if not (
            "canonical_setup_oracle" in case
            or "canonical_transition_oracle" in case
        ):
            errors.append(f"canonical setup/transition oracle missing: {case_id}")
        graph = case["expected_graph"]
        by_id[case_id] = case
        if list(graph) != graph_fields:
            errors.append(f"graph field order/schema mismatch: {case_id}")
        if [node["node_id"] for node in graph["nodes"]] != sorted(
            node["node_id"] for node in graph["nodes"]
        ):
            errors.append(f"node order mismatch: {case_id}")
        if [edge["edge_id"] for edge in graph["edges"]] != sorted(
            edge["edge_id"] for edge in graph["edges"]
        ):
            errors.append(f"edge order mismatch: {case_id}")
        nodes = _node_ids(graph)
        for node in graph["nodes"]:
            if list(node) != node_fields:
                errors.append(f"node field order/schema mismatch: {case_id}")
            if node["status"] not in contract["visible_record_classes"]:
                errors.append(f"non-visible node status: {case_id}/{node['node_id']}")
            if not isinstance(node["can_correct"], bool) or not isinstance(
                node["can_forget"], bool
            ):
                errors.append(f"invalid correction/forget capability: {case_id}")
            if not 0 <= node["confidence"] <= 1 or not node["source_refs"]:
                errors.append(f"invalid node confidence/source refs: {case_id}")
        for edge in graph["edges"]:
            if list(edge) != edge_fields:
                errors.append(f"edge field order/schema mismatch: {case_id}")
            if edge["source_node_id"] not in nodes or edge["target_node_id"] not in nodes:
                errors.append(f"edge references hidden node: {case_id}/{edge['edge_id']}")
            if not 0 <= edge["confidence"] <= 1 or not edge["source_refs"]:
                errors.append(f"invalid edge confidence/source refs: {case_id}")
        if not HEX64.fullmatch(case["expected_payload_sha256"]):
            errors.append(f"payload hash is not hex64: {case_id}")
        if _sha256_json(graph) != case["expected_payload_sha256"]:
            errors.append(f"payload hash mismatch: {case_id}")
        serialized = _canonical_bytes(graph).decode("utf-8")
        for canary in case.get("forbidden_output_canaries", []):
            if canary in serialized:
                errors.append(f"sensitive/stale canary leaked: {case_id}/{canary}")
        if set(case.get("excluded_node_ids", [])) & nodes:
            errors.append(f"excluded node visible: {case_id}")
        if set(case.get("excluded_edge_ids", [])) & _edge_ids(graph):
            errors.append(f"excluded edge visible: {case_id}")
        if set(case.get("must_exclude_node_ids", [])) & nodes:
            errors.append(f"transition-stale node visible: {case_id}")
        if not set(case.get("must_include_node_ids", [])).issubset(nodes):
            errors.append(f"transition-current node missing: {case_id}")

    rebuild = by_id["after-rebuild"]
    source = by_id[rebuild["must_match_case_payload"]]
    if _canonical_bytes(rebuild["expected_graph"]) != _canonical_bytes(
        source["expected_graph"]
    ):
        errors.append("rebuild graph is not byte-identical to canonical current view")
    isolation = fixture["agent_isolation_oracle"]
    if isolation["before"] != isolation["after"]:
        errors.append("graph changed recall/rank/context/tool control observation")
    if _sha256_json(isolation["before"]) != isolation["expected_control_sha256"]:
        errors.append("agent isolation known-answer hash mismatch")
    if isolation["graph_only_marker"] in _canonical_bytes(isolation["after"]).decode(
        "utf-8"
    ):
        errors.append("graph-only marker entered Agent control observation")
    if errors:
        return {"status": "FAIL", "errors": errors}
    return {
        "status": "PASS",
        "fixture_revision": fixture["fixture_revision"],
        "fixture_sha256": _sha256_bytes(fixture_path.read_bytes()),
        "checked_cases": len(fixture["cases"]),
        "checked_nodes": sum(
            len(case["expected_graph"]["nodes"]) for case in fixture["cases"]
        ),
        "checked_edges": sum(
            len(case["expected_graph"]["edges"]) for case in fixture["cases"]
        ),
        "payload_hashes_rebuilt": len(fixture["cases"]),
        "sensitive_canary_policy": "PASS",
        "canonical_db_seed_boundary": "PASS_FIXTURE_ROWS_NOT_GRAPH_INPUT",
        "agent_isolation_oracle": "PASS_REQUIRES_INDEPENDENT_HOST_RUNTIME_EVIDENCE",
        "product_execution": "NOT_RUN/BLOCKED",
        "quality_gate": fixture["quality_gate"],
    }


def execute_candidate(fixture: dict[str, Any]) -> dict[str, Any]:
    consumer = fixture["public_consumer"]
    if consumer["candidate_identity"]["status"] != "PINNED":
        return {
            "status": "NOT_RUN/BLOCKED",
            "reason": "Task 6 final Memory candidate identity is not pinned",
        }
    if consumer["public_manager_seed_contract"]["status"] != "PINNED":
        return {
            "status": "NOT_RUN/BLOCKED",
            "reason": "exact package-root public manager/backend seed contract is not pinned",
        }
    return {
        "status": "NOT_RUN/BLOCKED",
        "reason": (
            "fixture revision 1 contains no executable public seed operations; "
            "publish a new runner revision after the public evidence, mutation, "
            "suppression, close/reopen contract is frozen without changing the DTO oracle"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        default=str(Path(__file__).parents[1] / "fixtures" / "twin-graph-v1.json"),
    )
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--memory-wheel")
    parser.add_argument("--memory-wheel-sha256")
    parser.add_argument("--memory-source-commit")
    parser.add_argument("--manager-entrypoint")
    parser.add_argument("--artifact-dir")
    args = parser.parse_args()
    fixture_path = Path(args.fixture).resolve()
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    result = self_check(fixture_path) if args.self_check else execute_candidate(fixture)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] == "PASS":
        return 0
    if result["status"] == "NOT_RUN/BLOCKED":
        return BLOCKED_EXIT
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

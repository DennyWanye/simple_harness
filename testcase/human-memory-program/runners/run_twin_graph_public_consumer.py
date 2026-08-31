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
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
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


def _relation_fixture_self_check(fixture_path: Path) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if fixture.get("fixture_revision") != 1:
        errors.append("relation fixture_revision must be 1")
    if fixture.get("protocol_schema_version") != 5:
        errors.append("relation protocol schema must be 5")
    if fixture.get("quality_gate") != "NOT_RUN/BLOCKED":
        errors.append("relation quality gate must remain blocked before candidate pin")
    if fixture.get("allowed_import_roots") != [
        "simple_harness", "simple_harness_memory"
    ]:
        errors.append("relation imports must be limited to package roots")
    candidate = fixture.get("candidate_identity", {})
    if candidate.get("status") not in {"PENDING_POST_BUILD_PIN", "PINNED"}:
        errors.append("relation candidate identity status is invalid")
    if candidate.get("status") == "PINNED":
        for key in ("harness", "memory"):
            item = candidate.get(key, {})
            for field in ("wheel_sha256", "reproducible_second_wheel_sha256"):
                if not HEX64.fullmatch(item.get(field, "")):
                    errors.append(f"relation {key} {field} must be hex64")
            if item.get("wheel_sha256") != item.get("reproducible_second_wheel_sha256"):
                errors.append(f"relation {key} builds are not reproducible")
            if not item.get("version") or not item.get("source_commit"):
                errors.append(f"relation {key} identity is incomplete")
        if not HEX64.fullmatch(candidate.get("public_adapter_sha256", "")):
            errors.append("relation public adapter hash must be pinned")
    operations = fixture.get("strict_plan_oracle", {}).get("operations", [])
    plan_inputs = fixture.get("strict_plan_oracle", {}).get(
        "plan_constructor_inputs", {}
    )
    disclosure = plan_inputs.get("disclosure_context", {})
    if disclosure.get("run_id") != plan_inputs.get("run_id"):
        errors.append("relation disclosure run differs from plan run")
    if disclosure.get("subject") != plan_inputs.get("subject"):
        errors.append("relation disclosure subject differs from plan subject")
    if plan_inputs.get("evidence_ref_source") != (
        "exact EvidenceRef returned by the public evidence admission path"
    ):
        errors.append("relation plan must consume the public admitted EvidenceRef")
    operation_ids = [item.get("operation_id") for item in operations]
    if operation_ids != ["create-preference", "create-procedure", "create-relation"]:
        errors.append("relation operation order differs from the frozen value path")
    if len(operations) == 3:
        relation = operations[2]
        payload = relation.get("payload", {})
        if relation.get("depends_on_operation_ids") != [
            "create-preference", "create-procedure"
        ]:
            errors.append("relation dependencies are incomplete or unordered")
        if payload.get("semantic_kind") != "relation":
            errors.append("relation semantic discriminator is missing")
        if payload.get("relation_kind") != "applies_to":
            errors.append("only applies_to is frozen for v1")
        if "qualifiers" in payload:
            errors.append("v1 relation payload must not add qualifiers")
        preference_payload = operations[0].get("payload", {})
        procedure_payload = operations[1].get("payload", {})
        if preference_payload.get("semantic_kind") != "claim":
            errors.append("relation source must freeze a semantic claim")
        if preference_payload.get("qualifiers") != []:
            errors.append("semantic claim qualifiers must be explicit")
        if procedure_payload.get("proposed_risk_level") != "low":
            errors.append("procedure risk constructor input is missing")
        endpoint_ids = [
            payload.get(name, {}).get("operation_id")
            for name in ("source_endpoint", "target_endpoint")
        ]
        if endpoint_ids != ["create-preference", "create-procedure"]:
            errors.append("relation endpoint direction differs")
    views = fixture.get("expected_views", {})
    created = views.get("after_atomic_create", {})
    if created != {
        "node_count": 2,
        "edge_count": 1,
        "relation_memory_node_count": 0,
        "edge_relation_kinds": ["applies_to"],
        "edge_direction": "create-preference -> create-procedure",
        "source_memory_id": "must equal resolved source endpoint",
        "target_memory_id": "must equal resolved target endpoint",
        "payload_sha256": "must be hex64",
    }:
        errors.append("2-node/1-edge value oracle differs")
    if views.get("after_endpoint_suppression") != {
        "edge_count": 0,
        "payload_sha256": "must be hex64",
    }:
        errors.append("suppression must remove the knowledge edge")
    reopened = views.get("after_close_reopen", {})
    if reopened.get("edge_count") != 0 or reopened.get("must_equal_view") != (
        "after_endpoint_suppression"
    ) or reopened.get("payload_sha256") != (
        "must equal after_endpoint_suppression"
    ):
        errors.append("reopen must preserve the suppressed zero-edge view")
    contract = fixture.get("adapter_contract", {})
    if len(contract.get("mutation_receipt_fields", [])) != 9:
        errors.append("public mutation receipt evidence schema is incomplete")
    if len(contract.get("resolved_endpoint_fields", [])) != 6:
        errors.append("public resolved endpoint evidence schema is incomplete")
    trace = fixture.get("trace_oracle", {})
    if len(trace.get("required_link_kinds", [])) != 5:
        errors.append("public relation trace link kinds are incomplete")
    if len(trace.get("required_hashes", [])) != 5:
        errors.append("public relation trace hashes are incomplete")
    required_failures = fixture.get("fail_closed_oracles", {})
    if required_failures.get("missing_public_symbol") != "NOT_RUN/BLOCKED":
        errors.append("missing public symbol must block")
    if required_failures.get("private_import_or_sql_required") != "FAIL":
        errors.append("private access requirement must fail")
    if errors:
        return {"status": "FAIL", "errors": errors}
    return {
        "status": "PASS",
        "fixture_revision": 1,
        "fixture_sha256": _sha256_bytes(fixture_path.read_bytes()),
        "protocol_schema_version": 5,
        "operations": len(operations),
        "value_oracle": "2_NODES_1_APPLIES_TO_EDGE_RELATION_NODE_ZERO",
        "suppression_reopen_oracle": "ZERO_EDGE",
        "candidate_execution": "NOT_RUN/BLOCKED",
    }


def _relation_integrity_fixture_self_check(fixture_path: Path) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if fixture.get("fixture_revision") != 1:
        errors.append("relation integrity fixture_revision must be 1")
    if fixture.get("protocol_schema_version") != 5:
        errors.append("relation integrity protocol schema must be 5")
    if fixture.get("storage_schema_version") != 7:
        errors.append("relation integrity storage schema must be 7")
    if fixture.get("quality_gate") != "NOT_RUN/BLOCKED":
        errors.append("relation integrity quality gate must be blocked before execution")
    if fixture.get("allowed_relation_kinds") != ["applies_to"]:
        errors.append("v1 integrity matrix must freeze only applies_to")
    required_rejections = {
        "missing-dependency",
        "forward-dependency",
        "non-create-producer",
        "duplicate-operation-id",
        "unknown-relation-kind",
        "illegal-source-type",
        "illegal-target-type",
        "self-loop",
        "relation-as-endpoint",
        "cross-principal",
        "missing-endpoint",
        "stale-endpoint-revision",
        "contested-endpoint",
        "suppressed-endpoint",
        "missing-evidence",
        "classification-insufficient",
    }
    rejection_ids = {item.get("id") for item in fixture.get("rejection_cases", [])}
    if rejection_ids != required_rejections:
        errors.append("relation rejection matrix differs from the frozen exhaustive set")
    fault_seams = {
        item.get("seam") for item in fixture.get("transaction_fault_cases", [])
    }
    if fault_seams != {
        "before-relation-memory-insert",
        "after-relation-memory-before-knowledge-row",
        "after-knowledge-row-before-commit",
        "commit-before-ack",
    }:
        errors.append("relation transaction seam matrix is incomplete")
    lifecycle_ids = {item.get("id") for item in fixture.get("lifecycle_cases", [])}
    if lifecycle_ids != {
        "owner-suppressed",
        "owner-contested",
        "owner-superseded",
        "endpoint-contested",
        "endpoint-superseded",
        "endpoint-suppressed",
        "owner-expired",
        "endpoint-expired",
        "evidence-suppressed",
        "classification-restricted",
        "relation-corrected",
    }:
        errors.append("relation lifecycle invalidation matrix is incomplete")
    corruption_ids = {
        item.get("id") for item in fixture.get("restart_corruption_cases", [])
    }
    if corruption_ids != {
        "owner-mismatch",
        "domain-mismatch",
        "relation-hash-mismatch",
        "endpoint-foreign-key-mismatch",
    }:
        errors.append("relation reopen corruption matrix is incomplete")
    pre_admission = fixture.get("pre_admission_wire_cases", [])
    if len(pre_admission) != 1 or pre_admission[0].get("memory_call_count") != 0:
        errors.append("pre-admission malformed wire must make zero Memory calls")
    if not pre_admission or pre_admission[0].get("host_durable_audit_oracle") != (
        "NOT_RUN/BLOCKED_UNTIL_S5"
    ):
        errors.append("Host durable pre-admission audit gate is not explicit")
    retention = fixture.get("trace_and_retention_oracle", {})
    if retention.get("physical_delete_count") != 0:
        errors.append("raw evidence physical delete count must be zero")
    if len(retention.get("required_lineage", [])) != 5:
        errors.append("relation lineage oracle is incomplete")
    if len(retention.get("required_manifest_roots", [])) != 5:
        errors.append("relation manifest-root oracle is incomplete")
    execution = fixture.get("execution_contract", {})
    if execution.get("runner") != (
        "runners/run_semantic_relation_integrity_evidence.py"
    ):
        errors.append("relation integrity execution runner is not frozen")
    if len(execution.get("required_case_result_fields", [])) != 14:
        errors.append("relation integrity case evidence schema is incomplete")
    if execution.get("required_root_fields") != retention.get(
        "required_manifest_roots"
    ):
        errors.append("integrity evidence roots differ from manifest roots")
    if len(execution.get("required_row_fields", [])) != 5:
        errors.append("relation integrity row-cardinality schema is incomplete")
    if errors:
        return {"status": "FAIL", "errors": errors}
    return {
        "status": "PASS",
        "fixture_revision": 1,
        "fixture_sha256": _sha256_bytes(fixture_path.read_bytes()),
        "positive_endpoint_cases": len(fixture["positive_endpoint_cases"]),
        "rejection_cases": len(fixture["rejection_cases"]),
        "transaction_fault_cases": len(fixture["transaction_fault_cases"]),
        "replay_cases": len(fixture["replay_cases"]),
        "lifecycle_cases": len(fixture["lifecycle_cases"]),
        "restart_corruption_cases": len(fixture["restart_corruption_cases"]),
        "host_pre_admission_audit": "NOT_RUN/BLOCKED_UNTIL_S5",
        "product_execution": "NOT_RUN/BLOCKED",
    }


def self_check(
    fixture_path: Path,
    relation_fixture_path: Path,
    relation_integrity_fixture_path: Path,
) -> dict[str, Any]:
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
    if consumer.get("candidate_identity", {}).get("status") != "PINNED":
        errors.append("candidate identity must be PINNED")
    candidate = consumer.get("candidate_identity", {})
    if candidate.get("status") == "PINNED":
        for field in ("wheel_sha256", "reproducible_second_wheel_sha256"):
            if not HEX64.fullmatch(candidate.get(field, "")):
                errors.append(f"pinned candidate {field} must be hex64")
        if candidate.get("wheel_sha256") != candidate.get(
            "reproducible_second_wheel_sha256"
        ):
            errors.append("reproducible Task 6 wheel hashes differ")
        if not candidate.get("source_commit") or not candidate.get("version"):
            errors.append("pinned candidate source commit/version missing")
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
    relation_result = _relation_fixture_self_check(relation_fixture_path)
    if relation_result["status"] != "PASS":
        return relation_result
    relation_integrity_result = _relation_integrity_fixture_self_check(
        relation_integrity_fixture_path
    )
    if relation_integrity_result["status"] != "PASS":
        return relation_integrity_result
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
        "semantic_relation_oracle": relation_result,
        "semantic_relation_integrity_oracle": relation_integrity_result,
    }


def _python_in_venv(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def execute_candidate(
    fixture: dict[str, Any], relation_fixture: dict[str, Any], args: argparse.Namespace
) -> dict[str, Any]:
    del fixture  # The legacy DTO oracle is validated by self-check, never used as seed data.
    relation_pin = relation_fixture["candidate_identity"]
    if relation_pin["status"] != "PINNED":
        return {
            "status": "NOT_RUN/BLOCKED",
            "reason": "semantic relation dual-wheel candidate identity is not pinned",
        }
    supplied = {
        "harness": (
            args.harness_wheel,
            args.harness_wheel_sha256,
            args.harness_source_commit,
        ),
        "memory": (
            args.memory_wheel,
            args.memory_wheel_sha256,
            args.memory_source_commit,
        ),
    }
    wheel_paths: dict[str, Path] = {}
    for key, (path_value, supplied_hash, supplied_commit) in supplied.items():
        if not path_value:
            return {"status": "NOT_RUN/BLOCKED", "reason": f"candidate wheel missing: {key}"}
        wheel = Path(path_value).resolve()
        if not wheel.is_file() or wheel.suffix != ".whl":
            return {"status": "NOT_RUN/BLOCKED", "reason": f"candidate wheel unavailable: {key}"}
        actual_hash = _sha256_bytes(wheel.read_bytes())
        pin = relation_pin[key]
        if actual_hash != supplied_hash or actual_hash != pin["wheel_sha256"]:
            return {"status": "FAIL", "reason": f"candidate wheel hash mismatch: {key}"}
        if supplied_commit != pin["source_commit"]:
            return {"status": "FAIL", "reason": f"candidate source commit mismatch: {key}"}
        wheel_paths[key] = wheel
    if not args.manager_entrypoint:
        return {"status": "NOT_RUN/BLOCKED", "reason": "public Manager adapter is required"}
    adapter = Path(args.manager_entrypoint).resolve()
    if not adapter.is_file() or _sha256_bytes(adapter.read_bytes()) != relation_pin[
        "public_adapter_sha256"
    ]:
        return {"status": "FAIL", "reason": "public Manager adapter hash mismatch"}
    adapter_text = adapter.read_text(encoding="utf-8")
    forbidden = relation_fixture["adapter_contract"]["forbidden_source_tokens"]
    if any(token in adapter_text for token in forbidden):
        return {"status": "FAIL", "reason": "public Manager adapter crosses a frozen boundary"}
    if not args.artifact_dir:
        return {"status": "NOT_RUN/BLOCKED", "reason": "fresh artifact directory is required"}
    artifact_dir = Path(args.artifact_dir).resolve()
    if artifact_dir.exists():
        return {"status": "FAIL", "reason": "artifact directory must not pre-exist"}
    artifact_dir.parent.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir()
    uv = shutil.which("uv")
    if uv is None:
        return {"status": "NOT_RUN/BLOCKED", "reason": "isolated installer unavailable: uv"}
    with tempfile.TemporaryDirectory(prefix="hm-relation-public-") as temp_value:
        temp_root = Path(temp_value)
        venv = temp_root / "venv"
        create = subprocess.run(
            [uv, "venv", "--python", sys.executable, str(venv)],
            cwd=temp_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if create.returncode != 0:
            return {"status": "NOT_RUN/BLOCKED", "reason": "isolated venv creation failed"}
        python = _python_in_venv(venv)
        install = subprocess.run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                str(wheel_paths["harness"]),
                str(wheel_paths["memory"]),
            ],
            cwd=temp_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if install.returncode != 0:
            return {"status": "NOT_RUN/BLOCKED", "reason": "candidate wheel install failed"}
        env = os.environ.copy()
        env["PYTHONPATH"] = ""
        env["PYTHONNOUSERSITE"] = "1"
        probe_code = (
            "import importlib.metadata as m,json,simple_harness,simple_harness_memory;"
            "print(json.dumps({'harness':{'version':m.version('simple-harness-sdk'),"
            "'module_origin':simple_harness.__file__},'memory':{'version':"
            "m.version('simple-harness-memory-sdk'),'module_origin':"
            "simple_harness_memory.__file__}},sort_keys=True))"
        )
        probe = subprocess.run(
            [str(python), "-c", probe_code],
            cwd=temp_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0:
            return {"status": "FAIL", "reason": "installed package identity probe failed"}
        try:
            installed_identity = json.loads(probe.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            return {"status": "FAIL", "reason": "installed identity probe emitted invalid JSON"}
        for key in ("harness", "memory"):
            installed = installed_identity.get(key, {})
            pin = relation_pin[key]
            if installed.get("version") != pin["version"]:
                return {"status": "FAIL", "reason": f"installed version mismatch: {key}"}
            origin_value = installed.get("module_origin")
            if not isinstance(origin_value, str):
                return {"status": "FAIL", "reason": f"module origin missing: {key}"}
            origin = Path(origin_value).resolve()
            if venv.resolve() not in origin.parents:
                return {"status": "FAIL", "reason": f"module not loaded from clean venv: {key}"}
        run = subprocess.run(
            [
                str(python),
                str(adapter),
                "--fixture",
                str(Path(args.relation_fixture).resolve()),
                "--db",
                str(artifact_dir / "relation.sqlite"),
            ],
            cwd=temp_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if run.returncode != 0:
            return {
                "status": "FAIL",
                "reason": "public relation adapter failed",
                "candidate_exit_code": run.returncode,
                "stderr_tail": run.stderr[-1000:],
            }
        try:
            result = json.loads(run.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            return {"status": "FAIL", "reason": "public relation adapter emitted invalid JSON"}
        required = set(relation_fixture["adapter_contract"]["required_result_fields"])
        if set(result) != required or result.get("status") != "PASS":
            return {"status": "FAIL", "reason": "public relation result schema/status differs"}
        identity_fields = set(
            relation_fixture["adapter_contract"]["identity_fields"]
        )
        result_identity = result.get("identity")
        if not isinstance(result_identity, dict):
            return {"status": "FAIL", "reason": "adapter identity must be an object"}
        expected_identity: dict[str, dict[str, Any]] = {}
        for key in ("harness", "memory"):
            pin = relation_pin[key]
            expected_identity[key] = {
                "distribution": pin["distribution"],
                "version": pin["version"],
                "source_commit": pin["source_commit"],
                "wheel_sha256": pin["wheel_sha256"],
            }
            actual_identity = result_identity.get(key, {})
            if set(actual_identity) != identity_fields or actual_identity != expected_identity[key]:
                return {"status": "FAIL", "reason": f"adapter identity mismatch: {key}"}
        receipt = result["mutation_receipt"]
        if not isinstance(receipt, dict):
            return {"status": "FAIL", "reason": "mutation receipt must be an object"}
        if set(receipt) != set(
            relation_fixture["adapter_contract"]["mutation_receipt_fields"]
        ):
            return {"status": "FAIL", "reason": "mutation receipt schema differs"}
        receipt_oracle = relation_fixture["receipt_oracle"]
        for field in (
            "strict_atomic",
            "operation_count",
            "orphan_relation_row_count",
            "evidence_ids",
            "effective_privacy_class",
            "epistemic_status",
        ):
            if receipt.get(field) != receipt_oracle[field]:
                return {"status": "FAIL", "reason": f"mutation receipt oracle mismatch: {field}"}
        if not HEX64.fullmatch(receipt.get("receipt_hash", "")):
            return {"status": "FAIL", "reason": "mutation receipt hash is invalid"}
        owner = receipt.get("relation_owner", {})
        if not isinstance(owner, dict):
            return {"status": "FAIL", "reason": "relation owner must be an object"}
        if set(owner) != set(
            relation_fixture["adapter_contract"]["relation_owner_fields"]
        ):
            return {"status": "FAIL", "reason": "relation owner schema differs"}
        if (
            not isinstance(owner.get("memory_id"), str)
            or not owner["memory_id"]
            or not isinstance(owner.get("revision"), int)
            or owner["revision"] < 1
            or owner.get("semantic_kind") != "relation"
        ):
            return {"status": "FAIL", "reason": "relation exact owner is invalid"}
        endpoints = receipt.get("resolved_endpoints")
        if not isinstance(endpoints, list) or len(endpoints) != 2:
            return {"status": "FAIL", "reason": "resolved endpoint count differs"}
        expected_endpoints = (
            ("source", "create-preference", "semantic", "claim"),
            ("target", "create-procedure", "procedure", None),
        )
        endpoint_fields = set(
            relation_fixture["adapter_contract"]["resolved_endpoint_fields"]
        )
        for endpoint, expected_endpoint in zip(endpoints, expected_endpoints, strict=True):
            if set(endpoint) != endpoint_fields:
                return {"status": "FAIL", "reason": "resolved endpoint schema differs"}
            role, operation_id, memory_type, semantic_kind = expected_endpoint
            if (
                endpoint.get("role") != role
                or endpoint.get("operation_id") != operation_id
                or endpoint.get("memory_type") != memory_type
                or endpoint.get("semantic_kind") != semantic_kind
                or not isinstance(endpoint.get("memory_id"), str)
                or not endpoint["memory_id"]
                or not isinstance(endpoint.get("revision"), int)
                or endpoint["revision"] < 1
            ):
                return {"status": "FAIL", "reason": f"resolved endpoint differs: {role}"}
        if len({item["memory_id"] for item in endpoints} | {owner["memory_id"]}) != 3:
            return {"status": "FAIL", "reason": "relation owner/endpoints are not distinct"}
        expected = relation_fixture["expected_views"]["after_atomic_create"]
        created = result["after_atomic_create"]
        if not isinstance(created, dict):
            return {"status": "FAIL", "reason": "created graph view must be an object"}
        if set(created) != set(
            relation_fixture["adapter_contract"]["created_view_fields"]
        ):
            return {"status": "FAIL", "reason": "created graph view schema differs"}
        for field in (
            "node_count",
            "edge_count",
            "relation_memory_node_count",
            "edge_relation_kinds",
        ):
            if created.get(field) != expected[field]:
                return {"status": "FAIL", "reason": f"relation value oracle mismatch: {field}"}
        if created.get("edge_direction") != expected["edge_direction"]:
            return {"status": "FAIL", "reason": "relation edge direction differs"}
        if created.get("source_memory_id") != endpoints[0]["memory_id"]:
            return {"status": "FAIL", "reason": "graph source differs from resolved endpoint"}
        if created.get("target_memory_id") != endpoints[1]["memory_id"]:
            return {"status": "FAIL", "reason": "graph target differs from resolved endpoint"}
        if not HEX64.fullmatch(created.get("payload_sha256", "")):
            return {"status": "FAIL", "reason": "created graph payload hash is invalid"}
        suppressed = result["after_endpoint_suppression"]
        if not isinstance(suppressed, dict):
            return {"status": "FAIL", "reason": "suppressed graph view must be an object"}
        if set(suppressed) != set(
            relation_fixture["adapter_contract"]["suppressed_view_fields"]
        ):
            return {"status": "FAIL", "reason": "suppressed graph view schema differs"}
        if suppressed.get("edge_count") != 0:
            return {"status": "FAIL", "reason": "suppression did not remove edge"}
        if not HEX64.fullmatch(suppressed.get("payload_sha256", "")):
            return {"status": "FAIL", "reason": "suppressed graph payload hash is invalid"}
        if result["after_close_reopen"] != result["after_endpoint_suppression"]:
            return {"status": "FAIL", "reason": "reopen view differs after suppression"}
        trace = result["trace"]
        if not isinstance(trace, dict):
            return {"status": "FAIL", "reason": "relation trace must be an object"}
        if set(trace) != set(relation_fixture["adapter_contract"]["trace_fields"]):
            return {"status": "FAIL", "reason": "relation trace schema differs"}
        links = trace.get("links")
        required_link_kinds = relation_fixture["trace_oracle"]["required_link_kinds"]
        if (
            not isinstance(links, list)
            or [item.get("kind") for item in links if isinstance(item, dict)]
            != required_link_kinds
            or not all(set(item) == {"kind", "from_ref", "to_ref"} for item in links)
        ):
            return {"status": "FAIL", "reason": "relation trace lineage differs"}
        relation_ref = f'{owner["memory_id"]}@{owner["revision"]}'
        if links[0] != {
            "kind": "evidence_to_plan",
            "from_ref": "evidence-relation-1",
            "to_ref": "relation-plan-1",
        } or links[1] != {
            "kind": "plan_to_operation",
            "from_ref": "relation-plan-1",
            "to_ref": "create-relation",
        } or links[2] != {
            "kind": "operation_to_relation_memory",
            "from_ref": "create-relation",
            "to_ref": relation_ref,
        }:
            return {"status": "FAIL", "reason": "relation trace head differs"}
        if links[3]["from_ref"] != relation_ref or links[4]["from_ref"] != links[3]["to_ref"]:
            return {"status": "FAIL", "reason": "relation trace owner/knowledge chain differs"}
        if not links[3]["to_ref"] or not links[4]["to_ref"]:
            return {"status": "FAIL", "reason": "relation trace tail refs are empty"}
        hashes = trace.get("hashes")
        if not isinstance(hashes, dict) or set(hashes) != set(
            relation_fixture["trace_oracle"]["required_hashes"]
        ) or not all(HEX64.fullmatch(value) for value in hashes.values()):
            return {"status": "FAIL", "reason": "relation trace hashes differ"}
        result["installed_identity"] = installed_identity
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        default=str(Path(__file__).parents[1] / "fixtures" / "twin-graph-v1.json"),
    )
    parser.add_argument(
        "--relation-fixture",
        default=str(
            Path(__file__).parents[1] / "fixtures" / "semantic-relations-v1.json"
        ),
    )
    parser.add_argument(
        "--relation-integrity-fixture",
        default=str(
            Path(__file__).parents[1]
            / "fixtures"
            / "semantic-relation-integrity-v1.json"
        ),
    )
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--memory-wheel")
    parser.add_argument("--memory-wheel-sha256")
    parser.add_argument("--memory-source-commit")
    parser.add_argument("--harness-wheel")
    parser.add_argument("--harness-wheel-sha256")
    parser.add_argument("--harness-source-commit")
    parser.add_argument("--manager-entrypoint")
    parser.add_argument("--artifact-dir")
    args = parser.parse_args()
    fixture_path = Path(args.fixture).resolve()
    relation_fixture_path = Path(args.relation_fixture).resolve()
    relation_integrity_fixture_path = Path(args.relation_integrity_fixture).resolve()
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    relation_fixture = json.loads(relation_fixture_path.read_text(encoding="utf-8"))
    result = (
        self_check(
            fixture_path,
            relation_fixture_path,
            relation_integrity_fixture_path,
        )
        if args.self_check
        else execute_candidate(fixture, relation_fixture, args)
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] == "PASS":
        return 0
    if result["status"] == "NOT_RUN/BLOCKED":
        return BLOCKED_EXIT
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Verify the complete semantic-relation integrity evidence index fail-closed.

This verifier never manufactures product evidence. Before post-build pinning or
when any frozen case artifact is absent it returns NOT_RUN/BLOCKED.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

BLOCKED_EXIT = 3
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _case_oracles(fixture: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in fixture["positive_endpoint_cases"]:
        result[item["id"]] = item["oracle"]
    for item in fixture["rejection_cases"]:
        result[item["id"]] = item["reason_code"]
    for item in fixture["pre_admission_wire_cases"]:
        result[item["id"]] = (
            f'{item["harness_oracle"]}; host={item["host_durable_audit_oracle"]}; '
            f'memory_calls={item["memory_call_count"]}; delta={item["durable_state_delta"]}'
        )
    for group in (
        "transaction_fault_cases",
        "replay_cases",
        "lifecycle_cases",
        "restart_corruption_cases",
    ):
        for item in fixture[group]:
            result[item["id"]] = item["terminal_oracle"]
    return result


def self_check(fixture_path: Path) -> dict[str, Any]:
    fixture = _fixture(fixture_path)
    errors: list[str] = []
    if fixture.get("fixture_revision") != 1:
        errors.append("fixture_revision must be 1")
    if fixture.get("protocol_schema_version") != 5:
        errors.append("protocol schema must be 5")
    if fixture.get("storage_schema_version") != 7:
        errors.append("storage schema must be 7")
    if fixture.get("quality_gate") != "NOT_RUN/BLOCKED":
        errors.append("quality gate must remain blocked before candidate execution")
    cases = _case_oracles(fixture)
    if len(cases) != 40:
        errors.append("integrity matrix must contain exactly 40 unique cases")
    expected_total = sum(
        len(fixture[name])
        for name in (
            "positive_endpoint_cases",
            "rejection_cases",
            "pre_admission_wire_cases",
            "transaction_fault_cases",
            "replay_cases",
            "lifecycle_cases",
            "restart_corruption_cases",
        )
    )
    if len(cases) != expected_total:
        errors.append("integrity case ids must be unique")
    contract = fixture.get("execution_contract", {})
    required_fields = contract.get("required_case_result_fields", [])
    if len(required_fields) != 14 or len(set(required_fields)) != 14:
        errors.append("case result fields must be 14 unique names")
    if errors:
        return {"status": "FAIL", "errors": errors}
    return {
        "status": "PASS",
        "fixture_sha256": _sha256(fixture_path),
        "frozen_case_count": len(cases),
        "candidate_execution": "NOT_RUN/BLOCKED",
    }


def _candidate_identity(
    fixture: Mapping[str, Any], args: argparse.Namespace
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    candidate = fixture.get("candidate_identity", {})
    if candidate.get("status") != "PINNED":
        return None, {
            "status": "NOT_RUN/BLOCKED",
            "reason": "semantic relation integrity candidate is not pinned",
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
    for key, (path_value, digest, commit) in supplied.items():
        if not path_value:
            return None, {
                "status": "NOT_RUN/BLOCKED",
                "reason": f"candidate wheel missing: {key}",
            }
        path = Path(path_value).resolve()
        if not path.is_file() or _sha256(path) != digest:
            return None, {"status": "FAIL", "reason": f"wheel hash mismatch: {key}"}
        pin = candidate.get(key, {})
        if digest != pin.get("wheel_sha256") or commit != pin.get("source_commit"):
            return None, {"status": "FAIL", "reason": f"candidate pin mismatch: {key}"}
    return dict(candidate), None


def verify(fixture_path: Path, evidence_index_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    fixture = _fixture(fixture_path)
    candidate, failure = _candidate_identity(fixture, args)
    if failure is not None:
        return failure
    if not evidence_index_path.is_file():
        return {"status": "NOT_RUN/BLOCKED", "reason": "integrity evidence index missing"}
    if not args.artifact_root:
        return {"status": "NOT_RUN/BLOCKED", "reason": "artifact root missing"}
    artifact_root = Path(args.artifact_root).resolve()
    if not artifact_root.is_dir():
        return {"status": "NOT_RUN/BLOCKED", "reason": "artifact root unavailable"}
    index = json.loads(evidence_index_path.read_text(encoding="utf-8"))
    if index.get("fixture_sha256") != _sha256(fixture_path):
        return {"status": "FAIL", "reason": "evidence index fixture hash mismatch"}
    if index.get("candidate_identity") != candidate:
        return {"status": "FAIL", "reason": "evidence index candidate identity mismatch"}
    expected_oracles = _case_oracles(fixture)
    cases = index.get("cases")
    if not isinstance(cases, list) or len(cases) != len(expected_oracles):
        return {"status": "NOT_RUN/BLOCKED", "reason": "integrity case evidence incomplete"}
    by_id = {item.get("case_id"): item for item in cases if isinstance(item, Mapping)}
    if set(by_id) != set(expected_oracles) or len(by_id) != len(cases):
        return {"status": "NOT_RUN/BLOCKED", "reason": "integrity case ids incomplete or duplicated"}
    contract = fixture["execution_contract"]
    required_fields = set(contract["required_case_result_fields"])
    root_fields = set(contract["required_root_fields"])
    row_fields = set(contract["required_row_fields"])
    for case_id, expected_oracle in expected_oracles.items():
        item = by_id[case_id]
        if set(item) != required_fields:
            return {"status": "FAIL", "reason": f"case evidence schema differs: {case_id}"}
        if item["status"] != "PASS":
            return {"status": "FAIL", "reason": f"case is not PASS: {case_id}"}
        if item["expected_terminal_oracle"] != expected_oracle:
            return {"status": "FAIL", "reason": f"case oracle differs: {case_id}"}
        if not isinstance(item["observed_outcome_or_reason"], str) or not item[
            "observed_outcome_or_reason"
        ]:
            return {"status": "FAIL", "reason": f"case observation missing: {case_id}"}
        if item["candidate_identity"] != candidate:
            return {"status": "FAIL", "reason": f"case candidate differs: {case_id}"}
        for name in ("before_roots", "after_roots"):
            roots = item[name]
            if not isinstance(roots, Mapping) or set(roots) != root_fields:
                return {"status": "FAIL", "reason": f"case roots differ: {case_id}/{name}"}
            if not all(HEX64.fullmatch(value) for value in roots.values()):
                return {"status": "FAIL", "reason": f"case root hash invalid: {case_id}/{name}"}
        for name in ("before_row_cardinality", "after_row_cardinality"):
            rows = item[name]
            if not isinstance(rows, Mapping) or set(rows) != row_fields:
                return {"status": "FAIL", "reason": f"case row schema differs: {case_id}/{name}"}
            if not all(isinstance(value, int) and value >= 0 for value in rows.values()):
                return {"status": "FAIL", "reason": f"case row count invalid: {case_id}/{name}"}
        before = item["raw_evidence_hash_before"]
        after = item["raw_evidence_hash_after"]
        if not HEX64.fullmatch(before) or before != after:
            return {"status": "FAIL", "reason": f"raw evidence changed: {case_id}"}
        reopen = item["reopen_result"]
        if not isinstance(reopen, Mapping) or reopen.get("status") != "PASS":
            return {"status": "FAIL", "reason": f"reopen result missing: {case_id}"}
        ref = item["evidence_artifact_ref"]
        if not isinstance(ref, str) or not ref:
            return {"status": "FAIL", "reason": f"artifact ref missing: {case_id}"}
        artifact = (artifact_root / ref).resolve()
        if artifact_root not in artifact.parents or not artifact.is_file():
            return {"status": "FAIL", "reason": f"artifact unavailable: {case_id}"}
        if not HEX64.fullmatch(item["evidence_artifact_sha256"]):
            return {"status": "FAIL", "reason": f"artifact hash invalid: {case_id}"}
        if _sha256(artifact) != item["evidence_artifact_sha256"]:
            return {"status": "FAIL", "reason": f"artifact hash mismatch: {case_id}"}
    return {
        "status": "PASS",
        "fixture_sha256": _sha256(fixture_path),
        "evidence_index_sha256": _sha256(evidence_index_path),
        "candidate_identity": candidate,
        "verified_case_count": len(cases),
        "raw_evidence_retention": "PASS_BYTE_IDENTICAL_ALL_CASES",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        default=str(
            Path(__file__).parents[1]
            / "fixtures"
            / "semantic-relation-integrity-v1.json"
        ),
    )
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--evidence-index")
    parser.add_argument("--artifact-root")
    parser.add_argument("--harness-wheel")
    parser.add_argument("--harness-wheel-sha256")
    parser.add_argument("--harness-source-commit")
    parser.add_argument("--memory-wheel")
    parser.add_argument("--memory-wheel-sha256")
    parser.add_argument("--memory-source-commit")
    args = parser.parse_args()
    fixture_path = Path(args.fixture).resolve()
    if args.self_check:
        result = self_check(fixture_path)
    elif not args.evidence_index:
        result = {"status": "NOT_RUN/BLOCKED", "reason": "evidence index missing"}
    else:
        result = verify(fixture_path, Path(args.evidence_index).resolve(), args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] == "PASS":
        return 0
    if result["status"] == "NOT_RUN/BLOCKED":
        return BLOCKED_EXIT
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Black-box runner for the frozen Typed Recall public-consumer contract.

Self-check validates only the independent fixture oracle. Execute mode installs
candidate wheels into an isolated virtual environment and invokes one callable
exported directly from an allowed public package root. It never imports source
checkouts, private submodules, repositories, or SQL helpers.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any
import venv


BLOCKED_EXIT = 3
HEX64 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_AUTHORITY_EVENTS = {
    "suppression",
    "revoke",
    "supersede",
    "contest",
    "classification_change",
    "policy_hash_change",
    "short_source_invalidation",
    "short_source_expiry",
    "short_source_cleanup",
    "result_expiry",
    "context_expiry",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _set_path(value: dict[str, Any], path: str, replacement: Any) -> None:
    parts = path.split(".")
    cursor: dict[str, Any] = value
    for part in parts[:-1]:
        cursor = cursor[part]
    cursor[parts[-1]] = replacement


def _request_hash(prefix: str, request: dict[str, Any]) -> str:
    prefix_bytes = prefix.replace("\\0", "\0").encode("utf-8")
    return _sha256_bytes(prefix_bytes + _canonical_bytes(request))


def _iter_hash_fields(value: Any, path: str = "$") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if (
                isinstance(item, str)
                and (key.endswith("_hash") or key.endswith("_sha256"))
            ):
                found.append((child, item))
            found.extend(_iter_hash_fields(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_iter_hash_fields(item, f"{path}[{index}]"))
    return found


def _iter_ordinals(value: Any, path: str = "$") -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key == "ordinal" and isinstance(item, int):
                found.append((child, item))
            found.extend(_iter_ordinals(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_iter_ordinals(item, f"{path}[{index}]"))
    return found


def _check_conflict_hashes(fixture: dict[str, Any], errors: list[str]) -> None:
    oracle = fixture["conflict_write_oracle"]
    payloads = oracle["canonical_payloads"]
    hashes = oracle["hashes"]
    for name in ("incumbent", "challenger", "replacement"):
        expected = hashes[f"{name}_content"]
        actual = _sha256_json(payloads[name])
        if actual != expected:
            errors.append(f"conflict content hash mismatch: {name}")

    evidence = {
        "incumbent": ["evidence-user-python-311"],
        "challenger": ["evidence-user-python-312"],
        "resolution": ["evidence-user-resolution"],
    }
    for name, ids in evidence.items():
        if _sha256_json(ids) != hashes[f"{name}_evidence_set"]:
            errors.append(f"conflict evidence-set hash mismatch: {name}")

    create = oracle["create_case"]
    group = {
        "conflict_group_id": "conflict-python-1",
        "principal_id": create["principal_id"],
        "memory_id": create["memory_id"],
        "incumbent_revision": create["target_current_revision"],
        "challenger_revision": create["challenger_revision"],
        "members": [
            {
                "ordinal": member["ordinal"],
                "role": member["role"],
                "memory_id": create["memory_id"],
                "revision": member["revision"],
                "content_hash": member["content_hash"],
                "evidence_set_hash": member["evidence_set_hash"],
            }
            for member in create["expected_members"]
        ],
    }
    actual_group_hash = _sha256_json(group)
    if actual_group_hash != create["expected_group_hash"]:
        errors.append("conflict group hash mismatch")

    content_by_kind = {
        "select-incumbent": hashes["incumbent_content"],
        "replacement": hashes["replacement_content"],
        "terminal-supersede": hashes["challenger_content"],
        "terminal-suppress": hashes["challenger_content"],
    }
    for case in oracle["resolution_cases"]:
        canonical = {
            "group_hash": actual_group_hash,
            "resolution_kind": case["resolution_kind"],
            "new_revision": case["new_revision"],
            "content_hash": content_by_kind[case["resolution_kind"]],
            "evidence_set_hash": hashes["resolution_evidence_set"],
        }
        if _sha256_json(canonical) != case["resolution_hash"]:
            errors.append(f"resolution hash mismatch: {case['id']}")


def _check_request_hashes(fixture: dict[str, Any], errors: list[str]) -> None:
    oracle = fixture["request_hash_oracle"]
    base = oracle["base_request"]
    prefix = oracle["domain_prefix_utf8_with_nul"]
    if _request_hash(prefix, base) != oracle["base_hash"]:
        errors.append("base request hash mismatch")
    mutation_hashes: set[str] = set()
    for mutation in oracle["one_field_mutations"]:
        candidate = copy.deepcopy(base)
        _set_path(candidate, mutation["path"], mutation["value"])
        actual = _request_hash(prefix, candidate)
        if actual != mutation["expected_hash"]:
            errors.append(f"request mutation hash mismatch: {mutation['path']}")
        mutation_hashes.add(actual)
    if len(mutation_hashes) != len(oracle["one_field_mutations"]):
        errors.append("request mutation hashes are not unique")
    if oracle["base_hash"] in mutation_hashes:
        errors.append("request mutation retained the base hash")


def _check_budget_oracle(fixture: dict[str, Any], errors: list[str]) -> None:
    for case in fixture["budget_oracle"]["literal_cases"]:
        text = case["canonical_json"]
        parsed = json.loads(text)
        canonical = _canonical_bytes(parsed)
        if canonical.decode("utf-8") != text:
            errors.append(f"budget canonical JSON mismatch: {case['id']}")
        byte_count = len(canonical)
        codepoints = len(text)
        tokens = max(1, codepoints, math.ceil(byte_count / 3))
        if byte_count != case["utf8_bytes"]:
            errors.append(f"budget byte count mismatch: {case['id']}")
        if codepoints != case["unicode_codepoints"]:
            errors.append(f"budget codepoint count mismatch: {case['id']}")
        if tokens != case["token_estimate"]:
            errors.append(f"budget token estimate mismatch: {case['id']}")
        if _sha256_bytes(canonical) != case["canonical_sha256"]:
            errors.append(f"budget canonical hash mismatch: {case['id']}")


def _check_projection_hashes(fixture: dict[str, Any], errors: list[str]) -> None:
    for case in fixture["minimal_projection_oracle"]:
        if _sha256_json(case["payload"]) != case["payload_hash"]:
            errors.append(f"minimal projection hash mismatch: {case['memory_type']}")


def _check_ranking(fixture: dict[str, Any], errors: list[str]) -> None:
    oracle = fixture["ranking_oracle"]
    weights = oracle["weights"]
    scored: list[tuple[dict[str, Any], float]] = []
    for candidate in oracle["candidates"]:
        score = sum(
            float(weights[lane]) / (int(oracle["rrf_k"]) + int(rank))
            for lane, rank in candidate["lane_ranks"].items()
        )
        if f"{score:.12f}" != candidate["expected_score_12dp"]:
            errors.append(f"RRF score mismatch: {candidate['id']}")
        if len(candidate["lane_ranks"]) != candidate["matched_lane_count"]:
            errors.append(f"matched lane count mismatch: {candidate['id']}")
        scored.append((candidate, score))

    def stable_key(item: tuple[dict[str, Any], float]) -> tuple[Any, ...]:
        candidate, score = item
        return (
            -round(score, 12),
            -candidate["matched_lane_count"],
            candidate["typed_source_time"],
            candidate["source_kind"],
            candidate["memory_type"] or "",
            candidate["source_ref"],
            candidate["source_revision"],
        )

    actual_order = [candidate["id"] for candidate, _ in sorted(scored, key=stable_key)]
    if actual_order != oracle["expected_stable_order"]:
        errors.append(f"stable order mismatch: {actual_order}")
    cap = oracle["cap_overflow"]
    computed_cap = min(128, max(32, 8 * int(cap["max_items"])))
    if computed_cap != cap["computed_per_source_type_lane_cap"]:
        errors.append("candidate cap formula mismatch")
    if cap["input_ids"][computed_cap:] != cap["expected_dropped"]:
        errors.append("candidate cap overflow oracle mismatch")


def _check_authority_event_receipts(fixture: dict[str, Any], errors: list[str]) -> None:
    common = fixture["authority_event_common_binding"]
    for case in fixture["authority_event_cases"]:
        receipt = {
            "event": case["event"],
            "before_epoch": case["before_epoch"],
            "after_epoch": case["after_epoch"],
            "result_hash": common["result_hash"],
            "snapshot_hash": common["snapshot_hash"],
            "provider_attempt": common["provider_attempt"],
            "outcome": case["expected_old_result_use"],
        }
        if _sha256_json(receipt) != case["expected_receipt_hash"]:
            errors.append(f"authority event receipt hash mismatch: {case['event']}")
        if not HEX64.fullmatch(case["before_policy_hash"]):
            errors.append(f"authority event before policy hash invalid: {case['event']}")
        if not HEX64.fullmatch(case["after_policy_hash"]):
            errors.append(f"authority event after policy hash invalid: {case['event']}")


def _check_exhaustive_axes(fixture: dict[str, Any], errors: list[str]) -> tuple[int, int]:
    contract = fixture["exhaustive_axis_contract"]
    epistemic = contract["epistemic_verification"]
    epistemic_total = (
        len(epistemic["memory_types"])
        * len(epistemic["epistemic_values"])
        * len(epistemic["verification_values"])
    )
    disclosure = contract["disclosure"]
    disclosure_total = (
        len(disclosure["recipients"])
        * len(disclosure["purposes"])
        * len(disclosure["privacy_values"])
    )
    if epistemic["unlisted_expected"] != "INELIGIBLE":
        errors.append("epistemic unlisted combinations must fail closed")
    if disclosure["unlisted_expected"] != "INELIGIBLE":
        errors.append("disclosure unlisted combinations must fail closed")
    denied_common = fixture["eligibility_common_expectations"]["INELIGIBLE"]
    if denied_common != {
        "enters_rank_input": False,
        "rank_input_delta": 0,
        "public_candidate_count_delta": 0,
        "forbidden_canary_hits": 0,
    }:
        errors.append("common ineligible pre-rank/canary oracle mismatch")
    disclosure_pairs: set[tuple[str, str]] = set()
    for case in fixture["disclosure_cases"]:
        pair = (case["recipient"], case["purpose"])
        if pair in disclosure_pairs:
            errors.append(f"duplicate disclosure row: {pair}")
        disclosure_pairs.add(pair)
        outcomes = set(case["allowed"]) | set(case["denied"])
        if outcomes != set(disclosure["privacy_values"]):
            errors.append(f"disclosure row does not cover all privacy values: {pair}")
    for case in fixture["lifecycle_cases"]:
        if case["expected"] == "INELIGIBLE" and "reason" not in case:
            errors.append(f"ineligible lifecycle cell lacks reason: {case['id']}")
    return epistemic_total, disclosure_total


def self_check(fixture_path: Path) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if fixture.get("fixture_revision") != 2:
        errors.append("fixture_revision must be 2")
    if fixture.get("quality_gate") != "NOT_RUN/BLOCKED":
        errors.append("semantic quality gate must remain NOT_RUN/BLOCKED")
    for path, value in _iter_hash_fields(fixture):
        if not HEX64.fullmatch(value):
            errors.append(f"not lowercase hex64: {path}")
    for path, value in _iter_ordinals(fixture):
        if value < 1:
            errors.append(f"ordinal must start at one: {path}")
    for case in fixture["source_binding_cases"]:
        if "selected" in case:
            actual = [item["ordinal"] for item in case["selected"]]
            if actual != list(range(1, len(actual) + 1)):
                errors.append(f"non-contiguous source ordinals: {case['id']}")
    _check_conflict_hashes(fixture, errors)
    _check_request_hashes(fixture, errors)
    _check_budget_oracle(fixture, errors)
    _check_projection_hashes(fixture, errors)
    _check_ranking(fixture, errors)
    _check_authority_event_receipts(fixture, errors)
    epistemic_cells, disclosure_cells = _check_exhaustive_axes(fixture, errors)
    events = {case["event"] for case in fixture["authority_event_cases"]}
    missing_events = REQUIRED_AUTHORITY_EVENTS - events
    if missing_events:
        errors.append(f"missing authority events: {sorted(missing_events)}")
    combined = next(case for case in fixture["unsupported_cases"] if case["id"] == "combined-all")
    required_precedence = [
        "UNSUPPORTED_SELECTOR_EVENT",
        "UNSUPPORTED_SELECTOR_ENVIRONMENT",
        "UNSUPPORTED_SELECTOR_TASK_PHASE",
        "UNSUPPORTED_MODE_EXACT",
        "UNSUPPORTED_MODE_TEMPORAL",
        "UNSUPPORTED_MODE_GRAPH",
    ]
    if combined["ordered_reasons"] != required_precedence:
        errors.append("combined unsupported precedence mismatch")
    if errors:
        return {"status": "FAIL", "fixture": str(fixture_path), "errors": errors}
    return {
        "status": "PASS",
        "fixture": str(fixture_path),
        "fixture_sha256": _sha256_bytes(fixture_path.read_bytes()),
        "fixture_revision": fixture["fixture_revision"],
        "quality_gate": fixture["quality_gate"],
        "checked_request_mutations": len(fixture["request_hash_oracle"]["one_field_mutations"]),
        "checked_eligibility_cells": len(fixture["eligibility_cases"]),
        "checked_authority_events": len(fixture["authority_event_cases"]),
        "checked_budget_literals": len(fixture["budget_oracle"]["literal_cases"]),
        "checked_lifecycle_cells": len(fixture["lifecycle_cases"]),
        "generated_epistemic_verification_cells": epistemic_cells,
        "generated_disclosure_cells": disclosure_cells,
        "total_eligibility_matrix_cells": (
            len(fixture["eligibility_cases"])
            + len(fixture["lifecycle_cases"])
            + epistemic_cells
            + disclosure_cells
            + len(fixture["attribute_floor_cases"])
        ),
    }


def _python_in_venv(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _execute_candidate(args: argparse.Namespace, fixture: dict[str, Any]) -> dict[str, Any]:
    if not args.harness_wheel or not args.memory_wheel or not args.consumer_entrypoint:
        return {
            "status": "NOT_RUN/BLOCKED",
            "reason": "candidate wheels and --consumer-entrypoint are required",
        }
    wheels = [Path(args.harness_wheel).resolve(), Path(args.memory_wheel).resolve()]
    for wheel in wheels:
        if not wheel.is_file() or wheel.suffix != ".whl":
            return {"status": "NOT_RUN/BLOCKED", "reason": f"candidate wheel missing: {wheel}"}
    module_name, separator, callable_name = args.consumer_entrypoint.partition(":")
    if separator != ":" or module_name not in fixture["public_consumer"]["allowed_import_roots"]:
        return {
            "status": "NOT_RUN/BLOCKED",
            "reason": "entrypoint must be CALLABLE exported directly from an allowed public package root",
        }
    artifact_dir = Path(args.artifact_dir).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hm-typed-recall-consumer-") as tmp:
        tmp_path = Path(tmp)
        venv_dir = tmp_path / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
        python = _python_in_venv(venv_dir)
        install = subprocess.run(
            [str(python), "-m", "pip", "install", "--no-deps", *(str(wheel) for wheel in wheels)],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=False,
        )
        if install.returncode != 0:
            return {"status": "NOT_RUN/BLOCKED", "reason": "candidate wheel install failed"}
        probe = (
            "import importlib,json,sys;"
            "m=importlib.import_module(sys.argv[1]);"
            "f=getattr(m,sys.argv[2]);"
            "r=f(fixture_path=sys.argv[3],artifact_dir=sys.argv[4]);"
            "print(json.dumps(r,sort_keys=True))"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = ""
        env["PYTHONNOUSERSITE"] = "1"
        run = subprocess.run(
            [str(python), "-c", probe, module_name, callable_name, str(Path(args.fixture).resolve()), str(artifact_dir)],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if run.returncode != 0:
            return {"status": "NOT_RUN/BLOCKED", "reason": "public consumer callable unavailable or failed"}
        try:
            result = json.loads(run.stdout)
        except json.JSONDecodeError:
            return {"status": "FAIL", "reason": "public consumer did not emit one JSON result"}
        if not isinstance(result, dict) or result.get("status") not in {"PASS", "FAIL", "NOT_RUN/BLOCKED"}:
            return {"status": "FAIL", "reason": "public consumer returned an invalid result envelope"}
        if result.get("status") == "PASS":
            required = fixture["public_consumer"]["required_artifacts"]
            missing = [name for name in required if not (artifact_dir / name).is_file()]
            if missing:
                return {"status": "FAIL", "reason": f"missing required artifacts: {missing}"}
            expected_fixture_hash = _sha256_bytes(Path(args.fixture).resolve().read_bytes())
            if result.get("fixture_sha256") != expected_fixture_hash:
                return {"status": "FAIL", "reason": "consumer result fixture hash mismatch"}
            if result.get("fixture_revision") != fixture["fixture_revision"]:
                return {"status": "FAIL", "reason": "consumer result fixture revision mismatch"}
        return result


def main() -> int:
    default_fixture = Path(__file__).resolve().parents[1] / "fixtures" / "typed-recall-v2.json"
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=default_fixture)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--harness-wheel")
    parser.add_argument("--memory-wheel")
    parser.add_argument("--consumer-entrypoint")
    parser.add_argument("--artifact-dir", default=".local-test-evidence/typed-recall-public-consumer")
    args = parser.parse_args()
    checked = self_check(args.fixture.resolve())
    if checked["status"] != "PASS":
        print(json.dumps(checked, ensure_ascii=False, sort_keys=True))
        return 1
    if args.self_check:
        print(json.dumps(checked, ensure_ascii=False, sort_keys=True))
        return 0
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    result = _execute_candidate(args, fixture)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] == "PASS":
        return 0
    if result["status"] == "NOT_RUN/BLOCKED":
        return BLOCKED_EXIT
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

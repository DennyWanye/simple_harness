#!/usr/bin/env python3
"""Oracle and execution-layer gate for the frozen Typed Recall contract.

Self-check validates the independent 401-cell oracle and its exact 391 public /
10 source-integration partition.  Execute mode verifies exact candidate wheel
identities, then remains fail-closed until both validation-side evidence layers
are supplied.  Product test helpers, source imports, private submodules,
repositories, and SQL helpers are never accepted as clean-wheel evidence.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
from typing import Any


BLOCKED_EXIT = 3
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
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


def _classify_candidate_exit(phase: str, returncode: int) -> str:
    if returncode == 0:
        return "CONTINUE"
    if phase in {"install", "identity"}:
        return "NOT_RUN/BLOCKED"
    return "FAIL"


def _classify_executed_result(status: object) -> str:
    """An invoked product callable may not downgrade its own failure to NOT_RUN."""

    return "PASS" if status == "PASS" else "FAIL"


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


def _parse_rfc3339(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


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
        projected = {
            key: case["source_record"][key]
            for key in case["allowed_payload_fields"]
        }
        if projected != case["payload"]:
            errors.append(f"minimal projection stripping mismatch: {case['memory_type']}")
        if set(case["payload"]) != set(case["allowed_payload_fields"]):
            errors.append(f"minimal projection field set mismatch: {case['memory_type']}")
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
            -_parse_rfc3339(candidate["typed_source_time"]).timestamp(),
            candidate["source_kind"],
            candidate["memory_type"] or "",
            candidate["source_ref"],
            candidate["source_revision"] or 0,
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

    def explicit_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
        return (
            -float(candidate["rrf_score_12dp"]),
            -candidate["matched_lane_count"],
            -_parse_rfc3339(candidate["typed_source_time"]).timestamp(),
            candidate["source_kind"],
            candidate["memory_type"] or "",
            candidate["source_ref"],
            candidate["source_revision"] or 0,
        )

    for case in oracle["tie_break_cases"]:
        actual = [item["id"] for item in sorted(case["candidates"], key=explicit_key)]
        if actual != case["expected_order"]:
            errors.append(f"tie-break mismatch: {case['id']} -> {actual}")
    for case in oracle["vector_degradation_cases"]:
        if set(case["expected_executed_lanes"]) & set(case["unavailable_lanes"]):
            errors.append(f"unavailable vector lane executed: {case['id']}")
        if case["fallback_injected_source_count"] != 0:
            errors.append(f"vector degradation injected fallback source: {case['id']}")


def _check_authority_event_receipts(fixture: dict[str, Any], errors: list[str]) -> None:
    common = fixture["authority_event_common_binding"]
    canonical_fields = common["receipt_hash_canonical_fields"]
    for case in fixture["authority_event_cases"]:
        receipt = {
            "event": case["event"],
            "before_epoch": case["before_epoch"],
            "after_epoch": case["after_epoch"],
            "before_policy_hash": case["before_policy_hash"],
            "after_policy_hash": case["after_policy_hash"],
            "evaluated_at": case.get("evaluated_at", common["evaluated_at"]),
            "authorized_at": case.get("authorized_at", common["authorized_at"]),
            "authority_expires_at": case.get(
                "authority_expires_at", common["authority_expires_at"]
            ),
            "context_expires_at": case.get(
                "context_expires_at", common["context_expires_at"]
            ),
            "use_at": case.get("use_at", common["use_at"]),
            "decision_hash": common["decision_hash"],
            "result_hash": common["result_hash"],
            "item_hashes": common["item_hashes"],
            "snapshot_hash": common["snapshot_hash"],
            "run_id": common["run_id"],
            "turn_id": common["turn_id"],
            "continuation_id": common["continuation_id"],
            "provider_attempt": common["provider_attempt"],
            "outcome": case["expected_old_result_use"],
        }
        if list(receipt) != canonical_fields:
            errors.append("authority receipt canonical field order/schema mismatch")
        if _sha256_json(receipt) != case["expected_receipt_hash"]:
            errors.append(f"authority event receipt hash mismatch: {case['event']}")
        if not HEX64.fullmatch(case["before_policy_hash"]):
            errors.append(f"authority event before policy hash invalid: {case['event']}")
        if not HEX64.fullmatch(case["after_policy_hash"]):
            errors.append(f"authority event after policy hash invalid: {case['event']}")
    by_id = {case["id"]: case for case in fixture["context_use_cases"]}
    known = fixture["context_use_known_answer_hashes"]
    for case in fixture["context_use_cases"]:
        source = by_id[case["receipt_replay_of"]] if "receipt_replay_of" in case else case
        receipt = source["receipt"]
        if list(receipt) != canonical_fields:
            errors.append(f"context-use canonical schema mismatch: {case['id']}")
        actual = _sha256_json(receipt)
        if actual != known[case["id"]] or actual != case["expected_receipt_hash"]:
            errors.append(f"context-use known-answer hash mismatch: {case['id']}")


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


def self_check(fixture_path: Path, execution_layers_path: Path) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if fixture.get("fixture_revision", 0) >= 4:
        # The legacy synthetic-artifact self-check stays disabled from rev4 on, but the
        # execution-layers ROUTING oracle is revision independent and still runs, so the
        # partition is checked instead of silently skipped. It can only report; a routing
        # failure is surfaced as FAIL and a clean routing never turns this into PASS.
        routing = _validate_execution_layers(fixture_path=fixture_path, fixture=fixture,
                                             execution_layers_path=execution_layers_path)
        if routing["status"] != "PASS":
            return routing
        return {"status": "NOT_RUN/BLOCKED", "passed_cells": [],
                "reason": "rev4 uses independent A2 oracle regressions; legacy 401 synthetic artifact self-check is disabled",
                "execution_layers_fixture": routing["execution_layers_fixture"],
                "execution_layers_fixture_sha256": routing["execution_layers_fixture_sha256"],
                "execution_layers_routing": "PASS",
                "clean_wheel_public_manager_cells": routing["clean_wheel_public_manager_cells"],
                "source_exact_commit_integration_cells": routing["source_exact_commit_integration_cells"],
                "combined_exact_union_cells": routing["combined_exact_union_cells"]}
    errors: list[str] = []
    if fixture.get("fixture_revision") != 3:
        errors.append("fixture_revision must be 3")
    if fixture.get("quality_gate") != "NOT_RUN/BLOCKED":
        errors.append("semantic quality gate must remain NOT_RUN/BLOCKED")
    if not all(fixture.get("fixture_self_check", {}).values()):
        errors.append("all frozen fixture self-check requirements must remain enabled")
    if _classify_candidate_exit("execute", 1) != "FAIL":
        errors.append("executed candidate nonzero must classify as FAIL")
    if _classify_candidate_exit("identity", 3) != "NOT_RUN/BLOCKED":
        errors.append("missing public candidate identity must classify as NOT_RUN/BLOCKED")
    if _classify_executed_result("NOT_RUN/BLOCKED") != "FAIL":
        errors.append("executed callable must not downgrade failure to NOT_RUN/BLOCKED")
    state_oracle = fixture.get("state_hash_oracle", {})
    if state_oracle.get("executed_callable_blocked_outcome") != "FAIL":
        errors.append("executed callable blocked outcome must remain FAIL")
    if state_oracle.get("zero_state_delta_relation") != "EXACT_UNCHANGED":
        errors.append("zero state delta must require an exact unchanged terminal")
    if state_oracle.get("fault_relation") != "EXACT_ALL_OLD_OR_ALL_NEW":
        errors.append("fault state must require an exact old-or-new terminal")
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
    reject_ids = {case["id"] for case in fixture["conflict_write_oracle"]["reject_cases"]}
    required_conflict_negatives = {
        "contest-cross-memory", "contest-one-member", "contest-three-members"
    }
    if not required_conflict_negatives <= reject_ids:
        errors.append("missing cross-memory/cardinality conflict negatives")
    tamper_ids = {case["id"] for case in fixture["conflict_write_oracle"]["recall_cases"]}
    if not {
        "contested-tampered-one-member-reopen",
        "contested-tampered-three-members-reopen",
        "contested-tampered-cross-memory-reopen",
    } <= tamper_ids:
        errors.append("missing conflict tamper/reopen cases")
    if not any(
        case["id"] == "valid-until-null-unbounded"
        and case["valid_until"] is None
        and case["expected"] == "ELIGIBLE"
        for case in fixture["eligibility_cases"]
    ):
        errors.append("missing valid_until=null eligible boundary")
    floors = {
        (case["recipient"], case["attribute"])
        for case in fixture["attribute_floor_cases"]
    }
    for recipient in ("HOUSEHOLD", "TASK_COLLABORATOR"):
        for attribute in ("identity", "relationship", "family", "health", "location", "financial"):
            if (recipient, attribute) not in floors:
                errors.append(f"missing attribute floor: {recipient}/{attribute}")
    if errors:
        return {"status": "FAIL", "fixture": str(fixture_path), "errors": errors}
    try:
        expected_product_cells = _expected_product_cells(fixture)
    except (KeyError, TypeError, ValueError) as exc:
        return {"status": "FAIL", "fixture": str(fixture_path), "errors": [f"product cell oracle invalid: {exc}"]}
    layer_check = _validate_execution_layers(
        fixture_path=fixture_path,
        fixture=fixture,
        execution_layers_path=execution_layers_path,
    )
    if layer_check["status"] != "PASS":
        return layer_check
    artifact_validator_errors = _artifact_validator_self_check(fixture_path, fixture)
    if artifact_validator_errors:
        return {"status": "FAIL", "fixture": str(fixture_path), "errors": artifact_validator_errors}
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
        "expected_public_artifact_cells": sum(len(cells) for cells in expected_product_cells.values()),
        "execution_layers_fixture": str(execution_layers_path),
        "execution_layers_fixture_sha256": layer_check["execution_layers_fixture_sha256"],
        "clean_wheel_public_manager_cells": layer_check["clean_wheel_public_manager_cells"],
        "source_exact_commit_integration_cells": layer_check["source_exact_commit_integration_cells"],
        "combined_exact_union_cells": layer_check["combined_exact_union_cells"],
        "artifact_validator_self_check": "PASS",
        "artifact_validator_negative_cases": 5,
        "candidate_exit_classification": {
            "missing_identity": "NOT_RUN/BLOCKED",
            "executed_nonzero": "FAIL",
            "executed_returned_blocked": "FAIL",
        },
    }


def _expected_product_cells(fixture: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    lanes: dict[str, dict[str, dict[str, Any]]] = {
        lane: {} for lane in fixture["public_consumer"]["lane_artifacts"]
    }

    def add(lane: str, cell_id: str, observed: Any, outcome: str, reason: str = "", query_count: int = 1) -> None:
        if cell_id in lanes[lane]:
            raise ValueError(f"duplicate frozen cell id: {lane}/{cell_id}")
        lanes[lane][cell_id] = {
            "outcome": outcome,
            "reason": reason,
            "candidate_query_count": query_count,
            "forbidden_canary_hits": 0,
            "observed": observed,
        }

    for case in fixture["source_binding_cases"]:
        add("protocol", case["id"], case, case["expect"], query_count=0 if case.get("candidate_query_started") is False else 1)
    for case in fixture["protocol_negative_cases"] + fixture["result_page_cases"]:
        add("protocol", case["id"], case, case["expected"], query_count=0 if case.get("candidate_query_started") is False else 1)
    conflict = fixture["conflict_write_oracle"]
    add("conflict-state", conflict["create_case"]["id"], conflict["create_case"], "CONTEST_CREATED", query_count=0)
    for case in conflict["reject_cases"]:
        add("conflict-state", case["id"], case, "REJECTED", case["expected_reason"], 0)
    for case in conflict["resolution_cases"]:
        add("conflict-state", case["id"], case, "RESOLVED", query_count=0)
    for case in conflict["recall_cases"]:
        add("conflict-state", case["id"], case, case["expect"], case.get("reason", ""), 0 if "ZERO" in case["expect"] or "CORRUPT" in case["expect"] else 1)

    for case in fixture["eligibility_cases"] + fixture["lifecycle_cases"] + fixture["attribute_floor_cases"]:
        cell_id = case.get("id") or f"attribute:{case['recipient']}:{case['attribute']}"
        outcome = case["expected"]
        add("eligibility", cell_id, case, outcome, case.get("reason", ""), 1 if outcome.startswith("ELIGIBLE") else 0)
    epi = fixture["exhaustive_axis_contract"]["epistemic_verification"]
    for memory_type in epi["memory_types"]:
        for epistemic in epi["epistemic_values"]:
            for verification in epi["verification_values"]:
                observed = {"memory_type": memory_type, "epistemic": epistemic, "verification": verification}
                outcome, reason = epi["unlisted_expected"], epi["unlisted_reason"]
                for rule in fixture["epistemic_verification_cases"]:
                    if memory_type in rule["memory_types"] and epistemic == rule["epistemic"] and verification == rule["verification"]:
                        outcome, reason = rule["expected"], rule.get("reason", "")
                cell_id = epi["cell_id_format"].format(**observed)
                add("eligibility", cell_id, observed, outcome, reason, 1 if outcome.startswith("ELIGIBLE") else 0)
    disclosure = fixture["exhaustive_axis_contract"]["disclosure"]
    rows = {(row["recipient"], row["purpose"]): row for row in fixture["disclosure_cases"]}
    for recipient in disclosure["recipients"]:
        for purpose in disclosure["purposes"]:
            for privacy in disclosure["privacy_values"]:
                observed = {"recipient": recipient, "purpose": purpose, "privacy": privacy}
                row = rows.get((recipient, purpose))
                allowed = row is not None and privacy in row["allowed"]
                outcome = "ELIGIBLE" if allowed else disclosure["unlisted_expected"]
                reason = "" if allowed else (row or {}).get("reason", disclosure["unlisted_reason"])
                cell_id = disclosure["cell_id_format"].format(**observed)
                add("eligibility", cell_id, observed, outcome, reason, 1 if allowed else 0)

    for case in fixture["authority_event_cases"]:
        add("current-use", f"authority:{case['event']}", case, case["expected_old_result_use"], query_count=0)
    for case in fixture["context_use_cases"]:
        add("current-use", f"context:{case['id']}", case, case.get("expect", case.get("receipt", {}).get("outcome", "")), query_count=0)
    for case in fixture["unsupported_cases"]:
        add("unsupported-replay", case["id"], case, case["outcome"], ",".join(case["ordered_reasons"]), 0)
    for case in fixture["request_hash_oracle"]["one_field_mutations"]:
        add("unsupported-replay", f"request-hash:{case['path']}", case, "HASH_MATCH", query_count=0)
    add("unsupported-replay", "exact-replay", fixture["request_hash_oracle"]["exact_replay"], fixture["request_hash_oracle"]["exact_replay"]["expected"], query_count=0)
    add("unsupported-replay", "conflicting-replay", fixture["request_hash_oracle"]["conflicting_replay"], fixture["request_hash_oracle"]["conflicting_replay"]["expected"], query_count=0)

    ranking = fixture["ranking_oracle"]
    add("selection-budget", "ranking-order", {"candidates": ranking["candidates"], "expected_order": ranking["expected_stable_order"]}, "ORDER_MATCH")
    for case in ranking["tie_break_cases"]:
        add("selection-budget", case["id"], case, "ORDER_MATCH")
    for case in ranking["vector_degradation_cases"]:
        add("selection-budget", case["id"], case, case["outcome"], case["expected_audit_reason"])
    for case in ranking["dedupe_cases"]:
        add("selection-budget", f"dedupe:{case['id']}", case, "COUNT_MATCH")
    for case in fixture["minimal_projection_oracle"]:
        add("selection-budget", f"projection:{case['memory_type']}", case, "EXACT_MINIMAL_PROJECTION")
    for case in fixture["budget_oracle"]["literal_cases"]:
        add("selection-budget", f"budget:{case['id']}", case, "LITERAL_LIMITS_MATCH")
    add("selection-budget", "budget:greedy", fixture["budget_oracle"]["greedy_case"], "GREEDY_MATCH")
    add("selection-budget", "budget:deadline", fixture["budget_oracle"]["deadline_case"], "DEADLINE_EXCEEDED", query_count=0)
    for seam in fixture["durability_oracle"]["fault_seams"]:
        add("fault-recovery", f"fault:{seam}", {"seam": seam}, "ALL_OLD_OR_ALL_NEW", query_count=0)
    return lanes


def _execution_layers_revision_errors(layers: dict[str, Any]) -> list[str]:
    """Derive the acceptable execution-layers revision from the file's own lineage.

    The previous implementation carried a literal allowlist ``(1, 2, 3, 4, 5)``, which went
    stale the moment the sealed layers file was bumped (it is at revision 15 today) and made
    the ``--self-check`` routing path fail for a reason that has nothing to do with routing.
    The revision is not an arbitrary number: ``layers_change_lineage`` is the sealed record of
    every bump, so the declared ``fixture_revision`` must be exactly the ``layers_revision_to``
    of the newest lineage entry, the entries must form one ascending unit-step chain, and the
    same entries must carry the typed-recall fixture revision they pinned.  An empty lineage
    means the file has never been amended and must still be revision 1.  Nothing here reads a
    hard-coded revision, so a future sealed bump that follows the rules needs no code change,
    while a revision that does not match its own lineage is still rejected.
    """

    errors: list[str] = []
    revision = layers.get("fixture_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        return ["execution-layers revision must be a positive integer"]
    lineage = layers.get("layers_change_lineage")
    if not isinstance(lineage, list) or any(not isinstance(row, dict) for row in lineage):
        return ["execution-layers change lineage must be a list of entries"]
    if not lineage:
        if revision != 1:
            errors.append("execution-layers revision above 1 requires a change lineage entry")
        return errors
    previous_to: int | None = None
    for entry in lineage:
        start, end = entry.get("layers_revision_from"), entry.get("layers_revision_to")
        for value in (start, end):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                errors.append("execution-layers lineage revision is not a positive integer")
                return errors
        if end != start + 1:
            errors.append("execution-layers lineage entry is not a single-step bump")
        if previous_to is not None and start != previous_to:
            errors.append("execution-layers lineage entries are not a contiguous chain")
        if not HEX64.fullmatch(str(entry.get("previous_layers_sha256", ""))):
            errors.append("execution-layers lineage previous SHA-256 must be lowercase hex64")
        previous_to = end
    if previous_to != revision:
        errors.append("execution-layers revision does not match its own change lineage")
    pinned = lineage[-1].get("typed_recall_fixture_revision_to")
    if pinned != layers.get("typed_recall_fixture_revision"):
        errors.append("execution-layers lineage does not pin the declared typed recall revision")
    return errors


def _validate_execution_layers(
    *,
    fixture_path: Path,
    fixture: dict[str, Any],
    execution_layers_path: Path,
) -> dict[str, Any]:
    """Validate the frozen 391 public / 10 source-integration partition.

    This is a routing oracle only.  It cannot turn either execution layer into
    PASS and it cannot replace product artifacts from either layer.
    """

    try:
        layers = json.loads(execution_layers_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "FAIL",
            "execution_layers_fixture": str(execution_layers_path),
            "errors": [f"invalid execution-layers fixture: {type(exc).__name__}"],
        }
    errors: list[str] = []
    if layers.get("schema_version") != 1:
        errors.append("execution-layers schema/revision unsupported")
    errors.extend(_execution_layers_revision_errors(layers))
    if layers.get("quality_gate") != "NOT_RUN/BLOCKED":
        errors.append("execution-layers semantic quality gate must remain NOT_RUN/BLOCKED")
    if layers.get("typed_recall_fixture") != fixture_path.name:
        errors.append("execution-layers typed recall filename pin mismatch")
    if layers.get("typed_recall_fixture_revision") != fixture.get("fixture_revision"):
        errors.append("execution-layers typed recall revision pin mismatch")
    if layers.get("typed_recall_fixture_sha256") != _sha256_bytes(fixture_path.read_bytes()):
        errors.append("execution-layers typed recall SHA-256 pin mismatch")

    expected = _expected_product_cells(fixture)
    all_ids = sorted(
        f"{lane}/{cell_id}"
        for lane, cells in expected.items()
        for cell_id in cells
    )
    integration = layers.get("source_exact_commit_integration", {})
    integration_ids = integration.get("exact_cells")
    if not isinstance(integration_ids, list) or not all(
        isinstance(cell_id, str) for cell_id in integration_ids
    ):
        errors.append("source integration exact cell list is invalid")
        integration_ids = []
    if integration_ids != sorted(integration_ids):
        errors.append("source integration exact cell list must be sorted")
    if len(integration_ids) != len(set(integration_ids)):
        errors.append("source integration exact cell list contains duplicates")
    all_set = set(all_ids)
    integration_set = set(integration_ids)
    if not integration_set <= all_set:
        errors.append("source integration list contains an unknown frozen cell")
    public_ids = sorted(all_set - integration_set)

    def lane_counts(ids: list[str]) -> dict[str, int]:
        counts = {lane: 0 for lane in expected}
        for lane_cell_id in ids:
            lane, _, _ = lane_cell_id.partition("/")
            if lane not in counts:
                errors.append(f"unknown lane in partition: {lane_cell_id}")
                continue
            counts[lane] += 1
        return counts

    def check_partition(
        name: str,
        section: dict[str, Any],
        ids: list[str],
    ) -> None:
        if section.get("count") != len(ids):
            errors.append(f"{name} cell count mismatch")
        if section.get("sorted_lane_cell_ids_sha256") != _sha256_json(ids):
            errors.append(f"{name} sorted cell-id hash mismatch")
        if "lane_counts" in section and section.get("lane_counts") != lane_counts(ids):
            errors.append(f"{name} lane counts mismatch")

    check_partition("all", layers.get("all_cells", {}), all_ids)
    check_partition(
        "clean-wheel public manager",
        layers.get("clean_wheel_public_manager", {}),
        public_ids,
    )
    check_partition("source integration", integration, sorted(integration_ids))
    combined = layers.get("combined_gate", {})
    if combined.get("status") != "BLOCKED_UNTIL_BOTH_LAYERS_EXECUTE":
        errors.append("combined gate must remain blocked until both layers execute")
    if combined.get("required_exact_union_count") != len(all_ids):
        errors.append("combined exact union count mismatch")
    if combined.get("required_exact_union_sha256") != _sha256_json(all_ids):
        errors.append("combined exact union hash mismatch")
    if combined.get("duplicate_cell_count") != 0 or combined.get("missing_cell_count") != 0:
        errors.append("combined gate must freeze zero duplicate/missing cells")
    if combined.get("semantic_quality_gate") != "NOT_RUN/BLOCKED":
        errors.append("combined semantic quality gate must remain NOT_RUN/BLOCKED")

    for identity_name in ("candidate_harness_identity", "candidate_memory_identity"):
        identity = layers.get("clean_wheel_public_manager", {}).get(identity_name, {})
        if identity.get("status") != "PINNED":
            errors.append(f"{identity_name} must be PINNED")
        if not HEX64.fullmatch(str(identity.get("wheel_sha256", ""))):
            errors.append(f"{identity_name} wheel SHA-256 must be lowercase hex64")
        if not HEX40.fullmatch(str(identity.get("source_commit", ""))):
            errors.append(f"{identity_name} source commit must be lowercase hex40")
    if errors:
        return {
            "status": "FAIL",
            "execution_layers_fixture": str(execution_layers_path),
            "errors": errors,
        }
    return {
        "status": "PASS",
        "execution_layers_fixture": str(execution_layers_path),
        "execution_layers_fixture_sha256": _sha256_bytes(execution_layers_path.read_bytes()),
        "clean_wheel_public_manager_cells": len(public_ids),
        "source_exact_commit_integration_cells": len(integration_ids),
        "combined_exact_union_cells": len(all_ids),
        "layers": layers,
    }


def _expected_state_hashes(
    fixture: dict[str, Any],
    *,
    lane: str,
    cell_id: str,
    observed: Any,
) -> tuple[str, frozenset[str], str]:
    """Return the frozen before hash, allowed after hashes, and relation name.

    The digest is a fixture-owned public-state terminal identity.  Reject cells
    with ``state_delta=0`` must remain byte-identical.  Fault seams may finish
    at exactly the frozen old or new terminal, never an arbitrary third state.
    Every other cell is bound to the exact frozen changed terminal.
    """

    oracle = fixture["state_hash_oracle"]
    if fixture.get("fixture_revision") != 3:
        raise ValueError("legacy cell tags are not actual state hashes for revised fixtures")
    fields = oracle["canonical_payload_fields"]
    prefix = oracle["domain_prefix_utf8_with_nul"].replace("\\0", "\0").encode("utf-8")

    def terminal_hash(terminal: str) -> str:
        payload = {
            "fixture_id": fixture["fixture_id"],
            "fixture_revision": fixture["fixture_revision"],
            "lane": lane,
            "cell_id": cell_id,
            "terminal": terminal,
        }
        if list(payload) != fields:
            raise ValueError("state hash canonical field order differs")
        return _sha256_bytes(prefix + _canonical_bytes(payload))

    old_hash = terminal_hash(oracle["terminal_values"]["before"])
    new_hash = terminal_hash(oracle["terminal_values"]["after"])
    if lane == "fault-recovery":
        return old_hash, frozenset((old_hash, new_hash)), oracle["fault_relation"]
    if isinstance(observed, dict) and observed.get("state_delta") == 0:
        return old_hash, frozenset((old_hash,)), oracle["zero_state_delta_relation"]
    return old_hash, frozenset((new_hash,)), oracle["default_relation"]


def _validate_candidate_artifacts(
    artifact_dir: Path,
    fixture_path: Path,
    fixture: dict[str, Any],
    result: dict[str, Any],
    identity: dict[str, Any],
    invocation_started: datetime,
) -> dict[str, Any] | None:
    if fixture.get("fixture_revision") != 3:
        return {"status": "FAIL", "reason": "legacy synthetic state commitments cannot admit revised fixture artifacts"}
    expected_fixture_hash = _sha256_bytes(fixture_path.read_bytes())
    index_path = artifact_dir / "evidence-index.json"
    if index_path.is_symlink():
        return {"status": "FAIL", "reason": "evidence index must not be a symlink"}
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "FAIL", "reason": f"invalid evidence-index.json: {type(exc).__name__}"}
    required_index = set(fixture["public_consumer"]["artifact_schema"]["required_index_fields"])
    if set(index) != required_index:
        return {"status": "FAIL", "reason": "evidence index fields are not exact"}
    if index["schema_version"] != 1 or not isinstance(index["root_run_id"], str) or not index["root_run_id"]:
        return {"status": "FAIL", "reason": "evidence index schema/root_run_id invalid"}
    if index["fixture_revision"] != fixture["fixture_revision"] or index["fixture_sha256"] != expected_fixture_hash:
        return {"status": "FAIL", "reason": "evidence index fixture pin mismatch"}
    try:
        started = _parse_rfc3339(index["started_at"])
        completed = _parse_rfc3339(index["completed_at"])
    except (TypeError, ValueError):
        return {"status": "FAIL", "reason": "evidence index timestamp invalid"}
    if started < invocation_started or completed < started or index_path.stat().st_mtime < invocation_started.timestamp():
        return {"status": "FAIL", "reason": "stale or reversed evidence index timestamps"}
    if index["candidate_identity"] != identity:
        return {"status": "FAIL", "reason": "candidate identity mismatch"}

    expected_cells = _expected_product_cells(fixture)
    expected_artifacts = fixture["public_consumer"]["lane_artifacts"]
    artifact_rows = index["artifacts"]
    if not isinstance(artifact_rows, list) or len(artifact_rows) != len(expected_artifacts) or {row.get("lane") for row in artifact_rows} != set(expected_artifacts):
        return {"status": "FAIL", "reason": "evidence index lane set mismatch"}
    expected_files = set(expected_artifacts.values()) | {"evidence-index.json"}
    if {path.name for path in artifact_dir.iterdir() if path.is_file()} != expected_files:
        return {"status": "FAIL", "reason": "artifact directory exact file set mismatch"}
    indexed_cells = index["cells"]
    expected_pairs = {(lane, cell_id) for lane, cells in expected_cells.items() for cell_id in cells}
    if not isinstance(indexed_cells, list) or len({(row.get("lane"), row.get("cell_id")) for row in indexed_cells}) != len(indexed_cells):
        return {"status": "FAIL", "reason": "duplicate or invalid evidence index cell IDs"}
    if {(row.get("lane"), row.get("cell_id")) for row in indexed_cells} != expected_pairs:
        return {"status": "FAIL", "reason": "evidence index exact cell set mismatch"}

    index_by_pair = {(row["lane"], row["cell_id"]): row for row in indexed_cells}
    required_cell_fields = set(fixture["public_consumer"]["artifact_schema"]["required_cell_fields"]) | {"observed"}
    for row in artifact_rows:
        lane = row.get("lane")
        if set(row) != set(fixture["public_consumer"]["artifact_schema"]["required_artifact_index_fields"]):
            return {"status": "FAIL", "reason": f"artifact index fields mismatch: {lane}"}
        relative = row.get("relative_path")
        if relative != expected_artifacts[lane] or Path(relative).is_absolute() or ".." in Path(relative).parts:
            return {"status": "FAIL", "reason": f"unsafe or wrong artifact path: {lane}"}
        path = artifact_dir / relative
        if path.is_symlink() or not path.is_file() or path.resolve().parent != artifact_dir.resolve() or _sha256_bytes(path.read_bytes()) != row.get("sha256"):
            return {"status": "FAIL", "reason": f"artifact hash mismatch: {lane}"}
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
            a_started = _parse_rfc3339(artifact["started_at"])
            a_completed = _parse_rfc3339(artifact["completed_at"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return {"status": "FAIL", "reason": f"invalid lane artifact: {lane}"}
        required_lane_fields = set(fixture["public_consumer"]["artifact_schema"]["required_lane_fields"])
        if set(artifact) != required_lane_fields or artifact["lane"] != lane:
            return {"status": "FAIL", "reason": f"lane artifact fields mismatch: {lane}"}
        if artifact["fixture_revision"] != fixture["fixture_revision"] or artifact["fixture_sha256"] != expected_fixture_hash:
            return {"status": "FAIL", "reason": f"lane artifact fixture pin mismatch: {lane}"}
        if a_started < invocation_started or a_completed < a_started or path.stat().st_mtime < invocation_started.timestamp():
            return {"status": "FAIL", "reason": f"stale lane artifact: {lane}"}
        if row["started_at"] != artifact["started_at"] or row["completed_at"] != artifact["completed_at"]:
            return {"status": "FAIL", "reason": f"artifact index timestamps mismatch: {lane}"}
        product_cells = artifact["cells"]
        if not isinstance(product_cells, list) or {cell.get("cell_id") for cell in product_cells} != set(expected_cells[lane]):
            return {"status": "FAIL", "reason": f"lane exact cell set mismatch: {lane}"}
        if len({cell["cell_id"] for cell in product_cells}) != len(product_cells):
            return {"status": "FAIL", "reason": f"duplicate lane cell: {lane}"}
        for cell in product_cells:
            if set(cell) != required_cell_fields:
                return {"status": "FAIL", "reason": f"cell fields mismatch: {lane}/{cell.get('cell_id')}"}
            expected = expected_cells[lane][cell["cell_id"]]
            for field in ("outcome", "reason", "candidate_query_count", "forbidden_canary_hits", "observed"):
                if cell[field] != expected[field]:
                    return {"status": "FAIL", "reason": f"oracle mismatch: {lane}/{cell['cell_id']}/{field}"}
            if not HEX64.fullmatch(cell["before_hash"]) or not HEX64.fullmatch(cell["after_hash"]):
                return {"status": "FAIL", "reason": f"invalid durable hash: {lane}/{cell['cell_id']}"}
            expected_before, allowed_after, relation = _expected_state_hashes(
                fixture,
                lane=lane,
                cell_id=cell["cell_id"],
                observed=expected["observed"],
            )
            if cell["before_hash"] != expected_before or cell["after_hash"] not in allowed_after:
                return {
                    "status": "FAIL",
                    "reason": f"durable state relation mismatch: {lane}/{cell['cell_id']}/{relation}",
                }
            indexed = index_by_pair[(lane, cell["cell_id"])]
            expected_indexed = {
                "lane": lane,
                "cell_id": cell["cell_id"],
                "outcome": cell["outcome"],
                "reason": cell["reason"],
                "candidate_query_count": cell["candidate_query_count"],
                "forbidden_canary_hits": cell["forbidden_canary_hits"],
                "before_hash": cell["before_hash"],
                "after_hash": cell["after_hash"],
                "artifact_relative_path": relative,
                "artifact_sha256": row["sha256"],
            }
            if set(indexed) != set(fixture["public_consumer"]["artifact_schema"]["required_index_cell_fields"]) or indexed != expected_indexed:
                return {"status": "FAIL", "reason": f"evidence index cell mismatch: {lane}/{cell['cell_id']}"}
    index_hash = _sha256_bytes(index_path.read_bytes())
    if result.get("evidence_index_sha256") != index_hash:
        return {"status": "FAIL", "reason": "consumer result evidence-index hash mismatch"}
    return None


def _artifact_validator_self_check(fixture_path: Path, fixture: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    fixture_hash = _sha256_bytes(fixture_path.read_bytes())
    expected = _expected_product_cells(fixture)
    identity = {
        "harness": {"distribution": "simple-harness-sdk", "version": "0.test", "module_origin": "/isolated/site-packages/simple_harness/__init__.py", "wheel_sha256": "1" * 64, "source_commit": "fixture-self-check"},
        "memory": {"distribution": "simple-harness-memory-sdk", "version": "0.test", "module_origin": "/isolated/site-packages/simple_harness_memory/__init__.py", "wheel_sha256": "2" * 64, "source_commit": "fixture-self-check"},
    }
    invocation = datetime.now(timezone.utc) - timedelta(seconds=1)
    started_at = (invocation + timedelta(milliseconds=100)).isoformat().replace("+00:00", "Z")
    completed_at = (invocation + timedelta(milliseconds=200)).isoformat().replace("+00:00", "Z")
    with tempfile.TemporaryDirectory(prefix="hm-artifact-validator-self-check-") as tmp:
        root = Path(tmp)
        artifact_rows: list[dict[str, Any]] = []
        index_cells: list[dict[str, Any]] = []
        for lane, relative in fixture["public_consumer"]["lane_artifacts"].items():
            cells = []
            for cell_id, oracle in expected[lane].items():
                before_hash, allowed_after, relation = _expected_state_hashes(
                    fixture,
                    lane=lane,
                    cell_id=cell_id,
                    observed=oracle["observed"],
                )
                if relation == fixture["state_hash_oracle"]["fault_relation"]:
                    after_hash = max(allowed_after)
                else:
                    after_hash = next(iter(allowed_after))
                cell = {
                    "cell_id": cell_id,
                    **oracle,
                    "before_hash": before_hash,
                    "after_hash": after_hash,
                }
                cells.append(cell)
            artifact = {"lane": lane, "fixture_revision": fixture["fixture_revision"], "fixture_sha256": fixture_hash, "started_at": started_at, "completed_at": completed_at, "cells": cells}
            path = root / relative
            path.write_bytes(_canonical_bytes(artifact))
            artifact_hash = _sha256_bytes(path.read_bytes())
            artifact_rows.append({"lane": lane, "relative_path": relative, "sha256": artifact_hash, "started_at": started_at, "completed_at": completed_at})
            for cell in cells:
                index_cells.append({
                    "lane": lane,
                    "cell_id": cell["cell_id"],
                    "outcome": cell["outcome"],
                    "reason": cell["reason"],
                    "candidate_query_count": cell["candidate_query_count"],
                    "forbidden_canary_hits": cell["forbidden_canary_hits"],
                    "before_hash": cell["before_hash"],
                    "after_hash": cell["after_hash"],
                    "artifact_relative_path": relative,
                    "artifact_sha256": artifact_hash,
                })
        index = {"schema_version": 1, "root_run_id": "self-check", "fixture_revision": fixture["fixture_revision"], "fixture_sha256": fixture_hash, "started_at": started_at, "completed_at": completed_at, "candidate_identity": identity, "artifacts": artifact_rows, "cells": index_cells}
        index_path = root / "evidence-index.json"
        index_path.write_bytes(_canonical_bytes(index))
        result = {"evidence_index_sha256": _sha256_bytes(index_path.read_bytes())}
        if _validate_candidate_artifacts(root, fixture_path, fixture, result, identity, invocation) is not None:
            errors.append("known-good evidence index/artifacts rejected")
        good_index = copy.deepcopy(index)

        def write_index(candidate: dict[str, Any]) -> dict[str, str]:
            index_path.write_bytes(_canonical_bytes(candidate))
            return {"evidence_index_sha256": _sha256_bytes(index_path.read_bytes())}

        def rewrite_cell_hash(
            *, lane: str, cell_id: str, field: str, value: str
        ) -> tuple[dict[str, Any], dict[str, str]]:
            candidate = copy.deepcopy(good_index)
            relative = fixture["public_consumer"]["lane_artifacts"][lane]
            path = root / relative
            artifact = json.loads(path.read_text(encoding="utf-8"))
            target = next(cell for cell in artifact["cells"] if cell["cell_id"] == cell_id)
            target[field] = value
            path.write_bytes(_canonical_bytes(artifact))
            artifact_hash = _sha256_bytes(path.read_bytes())
            next(row for row in candidate["artifacts"] if row["lane"] == lane)[
                "sha256"
            ] = artifact_hash
            for row in candidate["cells"]:
                if row["lane"] == lane:
                    row["artifact_sha256"] = artifact_hash
                if row["lane"] == lane and row["cell_id"] == cell_id:
                    row[field] = value
            return candidate, write_index(candidate)

        def restore_lane(lane: str) -> None:
            relative = fixture["public_consumer"]["lane_artifacts"][lane]
            artifact_row = next(row for row in good_index["artifacts"] if row["lane"] == lane)
            cells = [
                cell
                for cell in index_cells
                if cell["lane"] == lane
            ]
            artifact = {
                "lane": lane,
                "fixture_revision": fixture["fixture_revision"],
                "fixture_sha256": fixture_hash,
                "started_at": artifact_row["started_at"],
                "completed_at": artifact_row["completed_at"],
                "cells": [
                    {
                        "cell_id": cell["cell_id"],
                        **expected[lane][cell["cell_id"]],
                        "before_hash": cell["before_hash"],
                        "after_hash": cell["after_hash"],
                    }
                    for cell in cells
                ],
            }
            (root / relative).write_bytes(_canonical_bytes(artifact))

        first_artifact = root / next(iter(fixture["public_consumer"]["lane_artifacts"].values()))
        original_artifact = first_artifact.read_bytes()
        first_artifact.write_bytes(original_artifact + b"\n")
        if _validate_candidate_artifacts(root, fixture_path, fixture, result, identity, invocation) is None:
            errors.append("tampered artifact accepted")
        first_artifact.write_bytes(original_artifact)
        if _validate_candidate_artifacts(root, fixture_path, fixture, result, identity, datetime.now(timezone.utc) + timedelta(days=1)) is None:
            errors.append("stale artifacts accepted")
        missing_index = copy.deepcopy(good_index)
        missing_index["cells"].pop()
        missing_cell_result = write_index(missing_index)
        if _validate_candidate_artifacts(root, fixture_path, fixture, missing_cell_result, identity, invocation) is None:
            errors.append("missing evidence-index cell accepted")

        unchanged_lane = "protocol"
        unchanged_id = "strict-v3-rejected"
        unchanged_before, unchanged_allowed, _ = _expected_state_hashes(
            fixture,
            lane=unchanged_lane,
            cell_id=unchanged_id,
            observed=expected[unchanged_lane][unchanged_id]["observed"],
        )
        assert unchanged_allowed == frozenset((unchanged_before,))
        _, changed_allowed, _ = _expected_state_hashes(
            fixture,
            lane=unchanged_lane,
            cell_id=unchanged_id,
            observed={},
        )
        drift_hash = next(iter(changed_allowed))
        _, drift_result = rewrite_cell_hash(
            lane=unchanged_lane,
            cell_id=unchanged_id,
            field="after_hash",
            value=drift_hash,
        )
        if _validate_candidate_artifacts(
            root, fixture_path, fixture, drift_result, identity, invocation
        ) is None:
            errors.append("zero-state-delta durable drift accepted")
        restore_lane(unchanged_lane)
        write_index(good_index)

        fault_lane = "fault-recovery"
        fault_id = "fault:decision-header"
        rogue_hash = "f" * 64
        _, fault_allowed, _ = _expected_state_hashes(
            fixture,
            lane=fault_lane,
            cell_id=fault_id,
            observed=expected[fault_lane][fault_id]["observed"],
        )
        if rogue_hash in fault_allowed:
            rogue_hash = "e" * 64
        _, fault_result = rewrite_cell_hash(
            lane=fault_lane,
            cell_id=fault_id,
            field="after_hash",
            value=rogue_hash,
        )
        if _validate_candidate_artifacts(
            root, fixture_path, fixture, fault_result, identity, invocation
        ) is None:
            errors.append("fault half-state durable hash accepted")
        restore_lane(fault_lane)
        write_index(good_index)
    return errors


def _execute_candidate(
    args: argparse.Namespace,
) -> dict[str, Any]:
    import importlib.util

    path = Path(__file__).with_name("typed_recall_bridge.py")
    spec = importlib.util.spec_from_file_location("typed_recall_bridge", path)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    try:
        fixture = bridge.read_json(args.fixture)
        execution_layers = bridge.read_json(args.execution_layers)
        return bridge.execute(args, execution_layers, _expected_product_cells(fixture))
    except (bridge.BridgeError, OSError, ValueError, KeyError, TypeError) as exc:
        return {"status": "FAIL", "reason": str(exc), "passed_cells": []}


def main() -> int:
    default_fixture = Path(__file__).resolve().parents[1] / "fixtures" / "typed-recall-v3.json"
    default_execution_layers = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "typed-recall-execution-layers-v1.json"
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=default_fixture)
    parser.add_argument(
        "--execution-layers",
        type=Path,
        default=default_execution_layers,
    )
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--harness-wheel")
    parser.add_argument("--harness-wheel-sha256")
    parser.add_argument("--harness-source-commit")
    parser.add_argument("--memory-wheel")
    parser.add_argument("--memory-wheel-sha256")
    parser.add_argument("--memory-source-commit")
    parser.add_argument("--consumer-entrypoint")
    parser.add_argument("--artifact-dir", default=".local-test-evidence/typed-recall-public-consumer")
    parser.add_argument("--consumer-python", help="Borrow an installed interpreter; verify exact wheel files in an isolated child. Default: clean venv.")
    parser.add_argument("--observe-candidate", action="store_true", help="Execute supplied candidate despite historical pin differences; cannot award PASS or repin fixtures.")
    parser.add_argument("--source-adapter", help="Validation-side Python file exporting async run(request, workspace) for the 10 source cells.")
    parser.add_argument("--source-checkout", help="Exact clean Memory checkout for the source adapter.")
    parser.add_argument("--cell", action="append", default=None, help="Run an original cell in a bounded batch; repeat as needed. Full401 inventory remains; unselected cells are not current evidence.")
    parser.add_argument("--child-timeout", type=float, default=60.0)
    args = parser.parse_args()
    if args.self_check:
        checked = self_check(args.fixture.resolve(), args.execution_layers.resolve())
        print(json.dumps(checked, ensure_ascii=False, sort_keys=True))
        return 0 if checked["status"] == "PASS" else BLOCKED_EXIT if checked["status"] == "NOT_RUN/BLOCKED" else 1
    result = _execute_candidate(args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] == "PASS":
        return 0
    if result["status"] == "NOT_RUN/BLOCKED":
        return BLOCKED_EXIT
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

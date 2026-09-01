#!/usr/bin/env python3
"""Deterministic black-box value oracle for S4 foreground Runtime execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from s4_execution_runner_common import (
    assert_no_forbidden_keys,
    canonical_bytes,
    expect_error,
    expect_ok,
    inspect_wheel,
    invoke,
    load_adapter,
    load_fixture,
    payload,
    require_evidence_dir,
    require_fields,
    sha256_bytes,
    write_result,
)


HERE = Path(__file__).resolve().parent
DEFAULT_FIXTURE = HERE.parent / "fixtures" / "s4-runtime-execution-v1.json"


def _assert_composition(snapshot: dict[str, Any], fixture: dict[str, Any]) -> None:
    if snapshot.get("epoch") != "HUMAN":
        raise AssertionError("fresh composition did not select HUMAN epoch")
    ports = set(snapshot.get("registered_ports", []))
    missing = set(fixture["required_human_ports"]) - ports
    if missing:
        raise AssertionError(f"HUMAN composition missing real ports: {sorted(missing)}")
    if snapshot.get("fixture_ports"):
        raise AssertionError("production composition reported fixture ports")
    absent = snapshot.get("legacy_future_registered_ports", [])
    if set(absent).intersection(fixture["required_human_ports"]):
        raise AssertionError("legacy/future composition registered HUMAN-only ports")


def _assert_execution(
    current: dict[str, Any],
    *,
    fixture: dict[str, Any],
    scope_ref: str,
    delivery_key: str,
    wheel_identity: dict[str, str],
) -> None:
    require_fields(
        current,
        {
            "host_run_id",
            "sdk_run_id",
            "owner_id",
            "generation",
            "delivery_key",
            "state",
            "draft",
            "claim",
            "authority",
            "route",
            "start_snapshot",
            "react_checkpoint",
            "wheel_identity",
        },
        "current execution",
    )
    if current["delivery_key"] != delivery_key:
        raise AssertionError("scheduler did not execute the oldest queued turn")
    if current.get("task_scope_id") != scope_ref:
        raise AssertionError("execution opened the wrong TaskScope")
    if current.get("sdk_start_count") != 1:
        raise AssertionError("one Host Run must produce exactly one durable SDK start")
    draft = current["draft"]
    if draft.get("executable") is not False:
        raise AssertionError("pre-claim preparation draft must be inert")
    forbidden_draft = {
        "provider_authority",
        "tool_authority",
        "start_authority",
        "signal_authority",
        "effect_authority",
    }
    if forbidden_draft.intersection(draft):
        raise AssertionError("inert draft carried executable authority")
    claim = current["claim"]
    authority = current["authority"]
    exact = (
        current["host_run_id"],
        current["sdk_run_id"],
        current["owner_id"],
        current["generation"],
    )
    observed = (
        authority.get("host_run_id"),
        authority.get("sdk_run_id"),
        authority.get("owner_id"),
        authority.get("generation"),
    )
    if observed != exact or claim.get("draft_hash") != draft.get("draft_hash"):
        raise AssertionError("execution authority is not bound to exact claim/generation")
    route = current["route"]
    expected_route = fixture["expected_route"]
    if route.get("outcome") != expected_route["outcome"]:
        raise AssertionError("ordinary SDK start was not ROUTED_TASK")
    if route.get("origin") != expected_route["origin"]:
        raise AssertionError("ordinary SDK start did not carry host_initial provenance")
    require_fields(
        route,
        {
            "run_id",
            "task_scope_id",
            "binding_set_receipt_ref",
            "binding_set_receipt_hash",
            "host_authority_ref",
            "host_authority_hash",
            "route_hash",
        },
        "initial route",
    )
    if route["run_id"] != current["sdk_run_id"] or route["task_scope_id"] != scope_ref:
        raise AssertionError("initial route identity mismatch")
    if current["start_snapshot"].get("route_hash") != route["route_hash"]:
        raise AssertionError("start snapshot omitted exact initial route hash")
    if current["react_checkpoint"].get("route_hash") != route["route_hash"]:
        raise AssertionError("ReAct checkpoint drifted from initial route")
    runtime_wheel = current["wheel_identity"]
    for key in ("filename", "sha256", "distribution", "version"):
        if runtime_wheel.get(key) != wheel_identity[key]:
            raise AssertionError(f"runtime wheel identity mismatch: {key}")
    require_fields(
        runtime_wheel,
        {"source_commit", "module_origin", "public_contract_manifest_hash"},
        "runtime wheel identity",
    )
    if current.get("session_db_read_count") != 0:
        raise AssertionError("foreground Runtime composition read legacy SessionDB")


def run(
    fixture: dict[str, Any],
    adapter: Any,
    adapter_identity: dict[str, str],
    artifact_dir: Path,
    wheel_identity: dict[str, str],
    expected_source_commit: str,
) -> dict[str, Any]:
    subject = fixture["subject"]
    wrong_actor = fixture["wrong_actor"]
    scope_a = fixture["task_scopes"]["a"]
    scope_b = fixture["task_scopes"]["b"]
    workspace = fixture["workspace"]
    errors = fixture["stable_errors"]

    expect_ok(
        invoke(
            adapter,
            "host.reset_fresh",
            {
                "data_format": fixture["data_format"],
                "provider": "deterministic-text-terminal",
                "session_db_reads": "forbidden",
            },
        ),
        "host.reset_fresh",
    )
    composition = payload(
        expect_ok(invoke(adapter, "host.composition_snapshot", {}), "composition")
    )
    _assert_composition(composition, fixture)
    runtime_identity = payload(
        expect_ok(invoke(adapter, "runtime.identity", {}), "runtime.identity")
    )
    for key in ("filename", "sha256", "distribution", "version"):
        if runtime_identity.get(key) != wheel_identity[key]:
            raise AssertionError(f"runtime exact-wheel identity mismatch: {key}")
    if runtime_identity.get("source_commit") != expected_source_commit:
        raise AssertionError("runtime source commit does not match exact candidate")

    create_a = payload(
        expect_ok(
            invoke(adapter, "task_scope.create", {"scope": scope_a}),
            "task_scope.create:A",
        )
    )
    create_b = payload(
        expect_ok(
            invoke(adapter, "task_scope.create", {"scope": scope_b}),
            "task_scope.create:B",
        )
    )
    scope_a_ref = str(create_a.get("scope_ref") or "")
    scope_b_ref = str(create_b.get("scope_ref") or "")
    if not scope_a_ref or not scope_b_ref or scope_a_ref == scope_b_ref:
        raise AssertionError("A/B must have distinct exact TaskScope refs")
    expect_ok(
        invoke(
            adapter,
            "task_scope.append_deterministic_events",
            {
                "scope_ref": scope_a_ref,
                "count": scope_a["event_count"],
                "canary": scope_a["canary"],
            },
        ),
        "append events:A",
    )
    expect_ok(
        invoke(
            adapter,
            "task_scope.save_checkpoint",
            {"scope_ref": scope_a_ref, "checkpoint": scope_a["checkpoint"]},
        ),
        "save checkpoint:A",
    )

    direct = invoke(
        adapter,
        "binding.append",
        {"scope_ref": scope_a_ref, "root": workspace["manual_root"]},
    )
    challenge_required = expect_error(
        direct, "manual direct append", errors["manual_authorization_required"]
    )
    challenge_ref = payload(challenge_required).get("challenge_ref")
    if not challenge_ref:
        raise AssertionError("Manual direct append omitted challenge ref")
    proposal = payload(
        expect_ok(
            invoke(
                adapter,
                "binding.propose_manual",
                {
                    "scope_ref": scope_a_ref,
                    "root": workspace["manual_root"],
                    "interaction": "authenticated-user-request",
                },
            ),
            "binding.propose_manual",
        )
    )
    require_fields(
        proposal,
        {"proposal_ref", "challenge_ref", "nonce", "expires_at", "evidence_ref"},
        "manual proposal",
    )
    expect_error(
        invoke(
            adapter,
            "binding.decide_manual",
            {
                "challenge_ref": proposal["challenge_ref"],
                "nonce": proposal["nonce"],
                "actor": wrong_actor,
                "decision": "allow",
            },
        ),
        "manual wrong actor",
        errors["wrong_actor"],
    )
    manual = payload(
        expect_ok(
            invoke(
                adapter,
                "binding.decide_manual",
                {
                    "challenge_ref": proposal["challenge_ref"],
                    "nonce": proposal["nonce"],
                    "actor": subject,
                    "decision": "allow",
                },
            ),
            "manual allow",
        )
    )
    require_fields(
        manual,
        {"decision_ref", "grant_ref", "binding_set_receipt_ref", "receipt_hash"},
        "manual allow",
    )
    replay = payload(
        expect_ok(
            invoke(
                adapter,
                "binding.decide_manual",
                {
                    "challenge_ref": proposal["challenge_ref"],
                    "nonce": proposal["nonce"],
                    "actor": subject,
                    "decision": "allow",
                },
            ),
            "manual replay",
        )
    )
    if canonical_bytes(replay) != canonical_bytes(manual):
        raise AssertionError("Manual exact replay did not return the original result")

    first_turn, second_turn = fixture["queue"]["turns"]
    expect_ok(
        invoke(adapter, "queue.enqueue", {"scope_ref": scope_a_ref, **first_turn}),
        "queue.enqueue:q1",
    )
    expect_ok(
        invoke(adapter, "queue.enqueue", {"scope_ref": scope_a_ref, **second_turn}),
        "queue.enqueue:q2",
    )
    current = payload(
        expect_ok(
            invoke(adapter, "execution.await_current", {"state": "RUNNING"}),
            "execution.await_current:q1",
        )
    )
    _assert_execution(
        current,
        fixture=fixture,
        scope_ref=scope_a_ref,
        delivery_key=first_turn["delivery_key"],
        wheel_identity=wheel_identity,
    )
    first_identity = (current["host_run_id"], current["sdk_run_id"])

    auto = payload(
        expect_ok(
            invoke(
                adapter,
                "binding.append_auto_current",
                {"run_ref": current["host_run_id"], "root": workspace["auto_root"]},
            ),
            "binding.append_auto_current",
        )
    )
    require_fields(
        auto,
        {
            "run_ref",
            "generation",
            "context_snapshot_ref",
            "configuration_revision",
            "binding_set_receipt_ref",
        },
        "Auto binding",
    )
    if auto["run_ref"] != current["host_run_id"] or auto["generation"] != current["generation"]:
        raise AssertionError("Auto binding did not use exact current Run generation")
    expect_error(
        invoke(
            adapter,
            "binding.append_auto_current",
            {"run_ref": current["host_run_id"], "root": workspace["outside_root"]},
        ),
        "Auto outside workspace",
        errors["auto_outside_workspace"],
    )
    expect_error(
        invoke(
            adapter,
            "effect.project",
            {"run_ref": current["host_run_id"], "effect": "write-canary"},
        ),
        "multi-root project effect",
        errors["root_selector_required"],
    )

    restart = payload(expect_ok(invoke(adapter, "host.cold_restart", {}), "cold restart"))
    recovered = payload(
        expect_ok(
            invoke(adapter, "execution.await_current", {"state": "RUNNING"}),
            "execution.await_current:recovered-q1",
        )
    )
    if (recovered["host_run_id"], recovered["sdk_run_id"]) != first_identity:
        raise AssertionError("cold restart created a different Host or SDK Run")
    if recovered.get("sdk_start_count") != 1:
        raise AssertionError("cold restart repeated the SDK start")
    expect_ok(
        invoke(
            adapter,
            "runtime.finish_current",
            {"sdk_run_id": recovered["sdk_run_id"], "terminal": "COMPLETED"},
        ),
        "runtime.finish_current:q1",
    )
    second = payload(
        expect_ok(
            invoke(adapter, "execution.await_current", {"state": "RUNNING"}),
            "execution.await_current:q2",
        )
    )
    _assert_execution(
        second,
        fixture=fixture,
        scope_ref=scope_a_ref,
        delivery_key=second_turn["delivery_key"],
        wheel_identity=wheel_identity,
    )
    if second["host_run_id"] == first_identity[0] or second["sdk_run_id"] == first_identity[1]:
        raise AssertionError("second turn reused the prior terminal Run identity")

    audit = payload(expect_ok(invoke(adapter, "execution.audit", {}), "execution.audit"))
    sequence = audit.get("lifecycle_sequence", [])
    required_sequence = fixture["required_execution_sequence"]
    cursor = 0
    for item in sequence:
        if cursor < len(required_sequence) and item == required_sequence[cursor]:
            cursor += 1
    if cursor != len(required_sequence):
        raise AssertionError("execution audit omitted or reordered required lifecycle facts")
    if audit.get("session_db_read_count") != 0:
        raise AssertionError("execution audit recorded a forbidden SessionDB read")
    sets = set(audit.get("v44_audit_sets", []))
    if not set(fixture["required_v44_audit_sets"]).issubset(sets):
        raise AssertionError("execution audit omitted required v44 raw sets")
    assert_no_forbidden_keys(
        audit, set(fixture["forbidden_audit_keys"]), "execution audit"
    )

    return {
        "schema_version": "1.0",
        "scenario": "HM-S4-TO-VALUE",
        "fixture_id": fixture["fixture_id"],
        "fixture_sha256": sha256_bytes(canonical_bytes(fixture)),
        "adapter": adapter_identity,
        "wheel_identity": runtime_identity,
        "scope_refs": {"a": scope_a_ref, "b": scope_b_ref},
        "first_run": {"host_run_id": first_identity[0], "sdk_run_id": first_identity[1]},
        "second_run": {
            "host_run_id": second["host_run_id"],
            "sdk_run_id": second["sdk_run_id"],
        },
        "manual_binding_receipt_hash": manual["receipt_hash"],
        "auto_binding_receipt_ref": auto["binding_set_receipt_ref"],
        "restart": restart,
        "audit_hash": sha256_bytes(canonical_bytes(audit)),
        "status": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--sdk-wheel", type=Path)
    parser.add_argument("--sdk-wheel-sha256")
    parser.add_argument("--sdk-version")
    parser.add_argument("--sdk-source-commit")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    fixture = load_fixture(args.fixture)
    fixture_hash = sha256_bytes(canonical_bytes(fixture))
    if args.self_check:
        print(
            json.dumps(
                {
                    "fixture_id": fixture["fixture_id"],
                    "fixture_sha256": fixture_hash,
                    "fault_boundary_count": len(fixture["fault_boundaries"]),
                    "status": "FIXTURE_PASS",
                },
                sort_keys=True,
            )
        )
        return 0
    required_args = {
        "adapter": args.adapter,
        "artifact_dir": args.artifact_dir,
        "sdk_wheel": args.sdk_wheel,
        "sdk_wheel_sha256": args.sdk_wheel_sha256,
        "sdk_version": args.sdk_version,
        "sdk_source_commit": args.sdk_source_commit,
    }
    missing = sorted(key for key, value in required_args.items() if not value)
    if missing:
        parser.error(f"formal run missing arguments: {missing}")
    artifact_dir = require_evidence_dir(args.artifact_dir)
    wheel_identity = inspect_wheel(
        args.sdk_wheel,
        expected_sha256=args.sdk_wheel_sha256,
        expected_version=args.sdk_version,
        expected_distribution=fixture["wheel_contract"]["distribution"],
    )
    adapter, adapter_identity = load_adapter(args.adapter, fixture, artifact_dir)
    result = run(
        fixture,
        adapter,
        adapter_identity,
        artifact_dir,
        wheel_identity,
        args.sdk_source_commit,
    )
    result_path = write_result(artifact_dir, "s4-execution-value-result.json", result)
    print(json.dumps({"result": str(result_path), "status": "PASS"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

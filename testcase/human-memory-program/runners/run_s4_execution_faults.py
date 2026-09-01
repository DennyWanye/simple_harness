#!/usr/bin/env python3
"""Black-box fault/restart and v44 retention oracle for S4 execution."""

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


def _prepare_scope(adapter: Any, fixture: dict[str, Any], boundary: str) -> str:
    expect_ok(
        invoke(
            adapter,
            "host.reset_fresh",
            {
                "data_format": fixture["data_format"],
                "provider": "deterministic-text-terminal",
                "session_db_reads": "forbidden",
                "scenario": boundary,
            },
        ),
        f"host.reset_fresh:{boundary}",
    )
    scope = payload(
        expect_ok(
            invoke(
                adapter,
                "task_scope.create",
                {"scope": fixture["task_scopes"]["a"]},
            ),
            f"task_scope.create:{boundary}",
        )
    )
    scope_ref = str(scope.get("scope_ref") or "")
    if not scope_ref:
        raise AssertionError(f"{boundary}: missing scope ref")
    expect_ok(
        invoke(
            adapter,
            "binding.seed_single_root",
            {"scope_ref": scope_ref, "root": fixture["workspace"]["manual_root"]},
        ),
        f"binding.seed_single_root:{boundary}",
    )
    return scope_ref


def _run_boundary(
    adapter: Any, fixture: dict[str, Any], boundary: str
) -> dict[str, Any]:
    scope_ref = _prepare_scope(adapter, fixture, boundary)
    armed = payload(
        expect_ok(
            invoke(adapter, "fault.arm", {"boundary": boundary}),
            f"fault.arm:{boundary}",
        )
    )
    require_fields(
        armed,
        {"fault_ref", "planned_host_run_id", "planned_sdk_run_id"},
        f"fault arm {boundary}",
    )
    turn = {
        **fixture["queue"]["turns"][0],
        "delivery_key": f"{fixture['queue']['turns'][0]['delivery_key']}:{boundary}",
    }
    outcome = invoke(adapter, "queue.enqueue", {"scope_ref": scope_ref, **turn})
    if outcome.get("ok") is False:
        expect_error(
            outcome,
            f"queue.enqueue:{boundary}",
            fixture["stable_errors"]["fault_injected"],
        )
    expect_ok(invoke(adapter, "host.cold_restart", {}), f"cold_restart:{boundary}")
    recovered = payload(
        expect_ok(
            invoke(adapter, "execution.await_current", {"state": "RUNNING"}),
            f"execution.await_current:{boundary}",
        )
    )
    require_fields(
        recovered,
        {"host_run_id", "sdk_run_id", "generation", "owner_id", "state"},
        f"recovered execution {boundary}",
    )
    if recovered["host_run_id"] != armed["planned_host_run_id"]:
        raise AssertionError(f"{boundary}: recovery changed Host Run identity")
    if recovered["sdk_run_id"] != armed["planned_sdk_run_id"]:
        raise AssertionError(f"{boundary}: recovery changed SDK Run identity")
    if recovered.get("sdk_start_count") != 1:
        raise AssertionError(f"{boundary}: recovery duplicated or omitted SDK start")
    if recovered.get("session_db_read_count") != 0:
        raise AssertionError(f"{boundary}: recovery read legacy SessionDB")
    audit = payload(
        expect_ok(
            invoke(
                adapter,
                "execution.audit",
                {"host_run_id": recovered["host_run_id"]},
            ),
            f"execution.audit:{boundary}",
        )
    )
    if audit.get("recovered_same_identity") is not True:
        raise AssertionError(f"{boundary}: audit did not prove same identity recovery")
    if audit.get("duplicate_start_observed") is not False:
        raise AssertionError(f"{boundary}: audit reported duplicate start")
    required_sets = set(fixture["required_v44_audit_sets"])
    if not required_sets.issubset(set(audit.get("v44_audit_sets", []))):
        raise AssertionError(f"{boundary}: v44 audit sets incomplete")
    assert_no_forbidden_keys(
        audit, set(fixture["forbidden_audit_keys"]), f"audit:{boundary}"
    )
    expect_ok(
        invoke(
            adapter,
            "runtime.finish_current",
            {"sdk_run_id": recovered["sdk_run_id"], "terminal": "COMPLETED"},
        ),
        f"runtime.finish_current:{boundary}",
    )
    return {
        "boundary": boundary,
        "fault_ref": armed["fault_ref"],
        "host_run_id": recovered["host_run_id"],
        "sdk_run_id": recovered["sdk_run_id"],
        "generation": recovered["generation"],
        "audit_sha256": sha256_bytes(canonical_bytes(audit)),
    }


def _assert_generation_fence(adapter: Any, fixture: dict[str, Any]) -> dict[str, Any]:
    scope_ref = _prepare_scope(adapter, fixture, "generation-fence")
    turn = fixture["queue"]["turns"][0]
    expect_ok(
        invoke(adapter, "queue.enqueue", {"scope_ref": scope_ref, **turn}),
        "queue.enqueue:generation-fence",
    )
    before = payload(
        expect_ok(
            invoke(adapter, "execution.await_current", {"state": "RUNNING"}),
            "execution.await_current:generation-before",
        )
    )
    reclaimed = payload(
        expect_ok(
            invoke(
                adapter,
                "fault.reclaim_current_lease",
                {"host_run_id": before["host_run_id"]},
            ),
            "fault.reclaim_current_lease",
        )
    )
    if reclaimed.get("generation") != before["generation"] + 1:
        raise AssertionError("lease reclaim did not advance generation exactly once")
    expect_error(
        invoke(
            adapter,
            "runtime.signal",
            {
                "host_run_id": before["host_run_id"],
                "sdk_run_id": before["sdk_run_id"],
                "owner_id": before["owner_id"],
                "generation": before["generation"],
                "signal": "cancel",
            },
        ),
        "stale generation signal",
        fixture["stable_errors"]["stale_generation"],
    )
    expect_error(
        invoke(
            adapter,
            "binding.append_auto_current",
            {
                "run_ref": before["host_run_id"],
                "generation": before["generation"],
                "root": fixture["workspace"]["auto_root"],
            },
        ),
        "stale generation Auto binding",
        fixture["stable_errors"]["stale_generation"],
    )
    return {
        "host_run_id": before["host_run_id"],
        "prior_generation": before["generation"],
        "current_generation": reclaimed["generation"],
    }


def _assert_recovery_export(adapter: Any, fixture: dict[str, Any]) -> dict[str, Any]:
    pre = payload(expect_ok(invoke(adapter, "recovery.manifest", {}), "manifest:pre"))
    expect_ok(invoke(adapter, "recovery.begin_close", {}), "recovery.begin_close")
    expect_error(
        invoke(
            adapter,
            "queue.enqueue",
            {
                "scope_ref": "fenced-write-must-not-resolve",
                "delivery_key": "fenced-write",
                "text": "must fail",
            },
        ),
        "fenced enqueue",
        fixture["stable_errors"]["ingress_fenced"],
    )
    closed = payload(
        expect_ok(
            invoke(adapter, "recovery.drain_checkpoint_seal", {}),
            "recovery.drain_checkpoint_seal",
        )
    )
    post = payload(expect_ok(invoke(adapter, "recovery.manifest", {}), "manifest:post"))
    exported = payload(
        expect_ok(invoke(adapter, "recovery.emergency_export", {}), "emergency export")
    )
    required_sets = set(fixture["required_v44_audit_sets"])
    for label, value in (("post manifest", post), ("export", exported)):
        if not required_sets.issubset(set(value.get("v44_audit_sets", []))):
            raise AssertionError(f"{label} omitted v44 execution lineage")
        assert_no_forbidden_keys(
            value, set(fixture["forbidden_audit_keys"]), label
        )
    protected_before = pre.get("protected_existing_row_roots", {})
    protected_after = post.get("protected_existing_row_roots", {})
    if protected_before != protected_after:
        raise AssertionError("recovery changed pre-existing protected execution rows")
    if exported.get("overall_root") != post.get("overall_root"):
        raise AssertionError("emergency export is not bound to sealed manifest root")
    if closed.get("wal_busy") not in (0, False) or not closed.get("drained_or_parked"):
        raise AssertionError("recovery did not prove drain-or-park and non-busy WAL")
    return {
        "manifest_ref": post.get("manifest_ref"),
        "overall_root": post.get("overall_root"),
        "export_ref": exported.get("export_ref"),
    }


def run(
    fixture: dict[str, Any],
    adapter: Any,
    adapter_identity: dict[str, str],
    wheel_identity: dict[str, str],
) -> dict[str, Any]:
    runtime_identity = payload(
        expect_ok(invoke(adapter, "runtime.identity", {}), "runtime.identity")
    )
    for key in ("filename", "sha256", "distribution", "version"):
        if runtime_identity.get(key) != wheel_identity[key]:
            raise AssertionError(f"runtime exact-wheel identity mismatch: {key}")
    require_fields(
        runtime_identity,
        {"source_commit", "module_origin", "public_contract_manifest_hash"},
        "runtime identity",
    )
    boundaries = [
        _run_boundary(adapter, fixture, boundary)
        for boundary in fixture["fault_boundaries"]
    ]
    generation = _assert_generation_fence(adapter, fixture)
    recovery = _assert_recovery_export(adapter, fixture)
    return {
        "schema_version": "1.0",
        "scenario": "HM-S4-TO-FIFO/HM-S4-TO-DATA",
        "fixture_id": fixture["fixture_id"],
        "fixture_sha256": sha256_bytes(canonical_bytes(fixture)),
        "adapter": adapter_identity,
        "wheel_identity": runtime_identity,
        "fault_boundaries": boundaries,
        "generation_fence": generation,
        "recovery": recovery,
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
                    "fault_boundaries": fixture["fault_boundaries"],
                    "required_v44_audit_sets": fixture["required_v44_audit_sets"],
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
    result = run(fixture, adapter, adapter_identity, wheel_identity)
    result_path = write_result(artifact_dir, "s4-execution-faults-result.json", result)
    print(json.dumps({"result": str(result_path), "status": "PASS"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

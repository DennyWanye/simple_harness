#!/usr/bin/env python3
# ruff: noqa: E501
"""Reproducible V1.4 H1-H stage matrix runner.

Test execution and specification coverage are separate. A passing nearby unit
test cannot close an unrelated H1-H requirement. The coverage audit is recorded
in the Host plan's H1H矩阵映射审计-2026-09-21.md. No provider is started here.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

#: I07 ("legacy cold reopen") was retired with the flat orchestration mode on 2026-10-02:
#: its test file was deleted and there is no legacy Mission left to reopen.
#: O10 ("retired step still has a running Attempt") was retired on 2026-10-03: no product
#: command reaches that state, and its refusal only existed on the unbound-TaskGraph path.
RETIRED_CASE_IDS = frozenset({"I07", "O10"})
CASE_IDS = tuple(
    case_id
    for prefix, count in (("A", 8), ("O", 10), ("P", 10), ("I", 8))
    for index in range(1, count + 1)
    if (case_id := f"{prefix}{index:02d}") not in RETIRED_CASE_IDS
)

IMPLEMENTED_NODEIDS = {
    "A01": "tests/orchestrator/full_target/test_h1h_authority_throughput.py",
    "A02": "tests/orchestrator/full_target/test_h1h_authority_matrix.py::test_a02_real_collector_records_authority_failures_as_closed_rejections",
    "A03": "tests/orchestrator/full_target/test_h1h_authority_matrix.py::test_a03_wrong_bound_principal_or_scope_is_refused_without_leak_or_writes",
    "A04": "tests/orchestrator/full_target/test_h1h_authority_matrix.py::test_a04_reply_bound_before_grant_change_is_stale_and_never_rebound",
    "A05": (
        "tests/orchestrator/full_target/test_h1h_authority_matrix.py::"
        "test_a04_reply_bound_before_grant_change_is_stale_and_never_rebound[expire]"
    ),
    "A06": None,
    "A07": "tests/orchestrator/full_target/test_h1h_planning_authorization.py::test_issue_command_replay_and_changed_input_conflict",
    "A08": (
        "tests/orchestrator/full_target/test_h1i_decision_replay.py::"
        "test_committed_refine_replays_after_grant_revocation_without_new_mutation"
    ),
    "O01": "tests/orchestrator/full_target/test_h1h_operation_matrix.py::test_o01_store_complete_empty_has_digest_and_read_error_is_not_empty",
    "O02": "tests/orchestrator/full_target/test_h1h_operation_admission.py::test_missing_bridge_is_source_unavailable_instead_of_latest_join",
    "O03": None,
    "O04": "tests/orchestrator/full_target/test_h1h_operation_matrix.py::test_o04_store_identity_or_link_fault_is_source_unavailable",
    "O05": "tests/orchestrator/full_target/test_h1h_operation_matrix.py::test_o05_store_handed_off_lease_never_becomes_not_applied",
    "O06": "tests/orchestrator/full_target/test_h1h_action_handoff.py::test_o06_executor_empty_lookup_rehandoff_keeps_new_protocol_hold",
    "O07": None,
    "O08": (
        "tests/orchestrator/full_target/test_h1h_commit_guard.py::"
        "test_o08_new_action_after_preview_is_detected_by_complete_set_reread"
    ),
    "O09": None,
    "P01": "tests/orchestrator/full_target/test_h1h_plan_preview.py::test_preview_commit_passes_the_same_compilation_without_recompiling",
    "P02": None,
    "P03": None,
    "P04": "tests/orchestrator/full_target/test_planning_decision_admission.py::test_an_unknown_premise_is_evidence_required",
    "P05": "tests/orchestrator/full_target/test_h1h_preview_purity.py",
    "P06": "tests/orchestrator/full_target/test_planning_decision_admission.py::test_running_work_on_the_retired_instance_is_refused",
    "P07": "tests/orchestrator/full_target/test_planning_decision_adapter.py::test_replace_method_is_retire_then_refine_and_requests_stop_then_reconcile",
    "P08": "tests/orchestrator/full_target/test_h1h_nonmutating_collect.py",
    "P09": "tests/orchestrator/full_target/test_h1i_wait_lifecycle.py::test_wait_target_terminal_before_registration_wakes",
    "P10": "tests/orchestrator/full_target/test_h1h_preview_problem_mapping.py",
    "I01": "tests/orchestrator/full_target/test_h1i_raw_artifact_collector.py",
    "I02": "tests/orchestrator/full_target/test_h1i_decision_replay.py",
    "I03": "tests/orchestrator/full_target/test_h1h_process_recovery.py::test_i03_process_exit_between_grant_and_side_binding_recovers_neither",
    "I04": "tests/orchestrator/full_target/test_h1i_preview_recovery.py",
    "I05": "tests/orchestrator/full_target/test_h1i_commit_recovery.py",
    "I06": (
        "tests/orchestrator/full_target/test_h1h_authority_matrix.py::"
        "test_a04_reply_bound_before_grant_change_is_stale_and_never_rebound[renew]"
    ),
    "I08": None,
}

# These are static coverage classifications, not signed acceptance results.
# P09 was subsequently replaced with its actual WAIT lifecycle regressions.
MATCHED = frozenset(
    {
        "A01",
        "A02",
        "A03",
        "A04",
        "A05",
        "A07",
        "A08",
        "O01",
        "O05",
        "O06",
        "P01",
        "P05",
        "P08",
        "P09",
        "P10",
        "I01",
        "I02",
        "I03",
        "I04",
        "I05",
    }
)
PARTIAL = frozenset({"O02", "O04", "O08", "P04", "P06", "P07", "I06"})
SUPPORTING_NODEIDS = {
    "I01": ("tests/orchestrator/full_target/test_h1i_raw_artifact_collector.py",),
    "O06": (
        "tests/orchestrator/full_target/test_h1h_action_handoff.py::test_o06_executor_weak_reconcile_rehandoff_cannot_call_connector",
    ),
    "A03": ("tests/orchestrator/full_target/test_h1h_authority_isolation.py",),
    "O04": ("tests/orchestrator/full_target/test_h1h_operation_tenant.py",),
    "A02": (
        "tests/orchestrator/full_target/test_h1h_authority_matrix.py::test_a02_malformed_authority_json_is_typed_source_unavailable_without_writes",
    ),
    "I03": (
        "tests/orchestrator/full_target/test_h1h_process_recovery.py::test_i03_process_exit_after_issue_replays_original_receipt_without_resigning",
    ),
    "P09": (
        "tests/orchestrator/full_target/test_h1i_wait_lifecycle.py::test_repeated_satisfied_wait_stops_instead_of_registering_a_busy_loop",
        "tests/orchestrator/full_target/test_h1i_wait_lifecycle.py::test_wait_wake_fault_rolls_back_woken_and_planner_intent_together",
    ),
}

COVERAGE_NOTES = {
    "A03": "principal/scope and issuer/tenant receipt isolation, cross-Mission issue/bind rejection are covered",
    "A08": "collector replay after revoke keeps the receipt; the other-principal denial at the commit entry is A03",
    "O04": "key/version/hash/dangling-link faults covered; tenant isolation remains",
    "O08": "new action is asserted; a newly started handoff remains uncovered",
    "O09": "handoff guard tests remain local to the handoff seam; atomic producer/link write is absent",
    "I02": "same raw committed replay after revoke and different raw conflict both preserve receipt and mutations",
}


# 2026-09-22 production-path additions. These mappings describe specification
# coverage; only run() may report an execution PASS. Earlier audit results remain
# evidence for their original source snapshot, not current gate status.
IMPLEMENTED_NODEIDS.update({
    "A06": "tests/orchestrator/full_target/test_h1h_authority_vs_action_approval.py::test_a06_zero_declared_approvals_and_applicable_method_do_not_replace_grant",
    "O03": "tests/orchestrator/full_target/test_h1h_commit_guard.py::test_o03b_a_plan_change_touching_an_unknown_publish_waits_for_reconciliation_then_is_handed_back",
    "O07": "tests/orchestrator/full_target/test_h1h_operation_live_boundaries.py::test_o07_real_t0_t1_success_receipt_is_applied_and_wrong_receipt_is_refused",
    "O09": "tests/orchestrator/full_target/operation_completion/test_publish_variants.py::test_a_materialization_link_write_failure_rolls_back_and_materializes_once_after_restart",
    "P02": "tests/orchestrator/full_target/test_h1h_p02_compiler_cycles.py",
    "P03": "tests/orchestrator/full_target/test_h1h_p03_compiler_data_coverage_resources.py",
    "P04": "tests/orchestrator/full_target/test_h1h_preview_compiler_refusal.py",
    "P06": "tests/orchestrator/product_world/test_repair_replace_method.py",
    "P07": "tests/orchestrator/product_world/test_repair_replace_method.py",
    "I06": "tests/orchestrator/full_target/test_h1h_commit_interleaving.py",
    "I08": "tests/orchestrator/full_target/test_h1h_no_nanojev_process.py",
})
SUPPORTING_NODEIDS.update({
    "A06": ("tests/orchestrator/full_target/test_h1h_authority_vs_action_approval.py::test_a06_unapproved_external_write_with_complete_origin_link_cannot_handoff",),
    "O02": ("tests/orchestrator/full_target/test_h1h_operation_alias.py", "tests/orchestrator/full_target/operation_completion/test_publish_variants.py::test_two_required_publishes_each_need_their_own_chain"),
    "O03": ("tests/orchestrator/full_target/operation_completion/test_publish_variants.py::test_a_lost_reply_is_reconciled_and_never_resent",
            "tests/orchestrator/full_target/test_h1h_commit_guard.py::test_o03_an_unknown_publish_outcome_holds_the_plan_change_back_without_a_revision"),
    "O07": ("tests/orchestrator/full_target/operation_completion/test_publish_variants.py::test_a_lost_reply_is_reconciled_and_never_resent",),
    "O08": ("tests/orchestrator/full_target/operation_completion/test_publish_variants.py::test_a_handoff_changes_the_operation_snapshot_a_plan_preview_read",),
    "P03": ("tests/orchestrator/full_target/test_h1h_preview_compiler_refusal.py::test_p02_p03_real_compiler_refusal_keeps_typed_reason",),
})
MATCHED = MATCHED | {"A06", "O03", "O07", "O08", "O09", "P02", "P03", "P04", "P06", "P07", "I06", "O02", "O04", "I08"}
PARTIAL = frozenset()
COVERAGE_NOTES.update({
    "A06": "observed method gate=True and exact required_approvals=0 cannot replace a grant; same-Mission planning/action authority separation covered",
    "O02": "two real T0 intents/reviews/materializations share a target with distinct occurrence/hash/action links; alias rollback covered",
    "O03": "2026-10-07: o03b — a plan change touching an UNKNOWN publish waits on TaskGraph convergence RECONCILE_OPERATION, is handed back after NOT_APPLIED_FINAL, and the resubmission commits; o03 (root already concluded under A48 refuses OBLIGATION_NOT_OPEN) supports",
    "O04": "key/version/hash/dangling-link and cross-tenant Mission corruption refuse SOURCE_UNAVAILABLE without latest fallback",
    "O07": "real T0/T1 success receipts plus registered scoped cancellation evidence and late-send fence",
    "O08": "both new action and actual new handoff after preview are checked at Commit",
    "O09": "real T0 materialization fault rolls back Action/link/events; independent SQLite reader sees no uncommitted Action; exact replay is single identity",
    "P06": "2026-10-03: a replacement with running work on the retired instance converges first — the TaskGraph cancels the running Attempt and its late result is not accepted before the new revision commits (product world)",
    "P07": "2026-10-03: replace = retire + refine in one TaskGraph commit after convergence; the repair request is addressed and the Mission completes on the new method. The deferred cold-resume path was removed: unreachable once every Mission is TaskGraph-bound",
    "I08": "isolated clean process blocks NanoJev imports, uses no Shadow provider/config/checkpoint/events, and runs all three producers and original Commit",
})


def manifest() -> list[dict[str, Any]]:
    return [
        {
            "case_id": case_id,
            "nodeid": IMPLEMENTED_NODEIDS.get(case_id),
            "status": "NOT_RUN" if IMPLEMENTED_NODEIDS.get(case_id) else "NOT_COVERED",
            "test_status": "NOT_RUN",
            "coverage": (
                "MATCH" if case_id in MATCHED else "PARTIAL" if case_id in PARTIAL else "MISMATCH"
            ),
            "supporting_nodeids": list(SUPPORTING_NODEIDS.get(case_id, ())),
            "coverage_note": COVERAGE_NOTES.get(case_id, ""),
        }
        for case_id in CASE_IDS
    ]


def run(mapped: list[dict[str, Any]], root: Path) -> None:
    for item in mapped:
        nodeid = item["nodeid"]
        if not nodeid:
            continue
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", nodeid, *item["supporting_nodeids"]],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        item["test_status"] = "PASS" if result.returncode == 0 else "FAIL"
        item["status"] = (
            "FAIL"
            if result.returncode != 0
            else "PASS"
            if item["coverage"] == "MATCH"
            else "PARTIAL"
            if item["coverage"] == "PARTIAL"
            else "NOT_COVERED"
        )
        item["returncode"] = result.returncode


def gate_exit_code(rows: list[dict[str, Any]]) -> int:
    if any(row["status"] == "FAIL" for row in rows):
        return 1
    return 0 if all(row["status"] == "PASS" for row in rows) else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-implemented", action="store_true")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    rows = manifest()
    if args.run_implemented:
        run(rows, root)
    payload = {"schema": "v1.4-h1h-stage-matrix-v1", "cases": rows}
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.manifest:
        args.manifest.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return gate_exit_code(rows) if args.run_implemented else 0


if __name__ == "__main__":
    raise SystemExit(main())

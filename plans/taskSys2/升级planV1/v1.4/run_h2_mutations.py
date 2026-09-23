#!/usr/bin/env python3
"""Ten H2 package contract mutations; no real-model claim."""
from pathlib import Path

import run_h1h_mutations as runner
from run_h1h_mutations import Mutation

SOURCE = "src/agent_orchestrator/planning/htn/planner_package_v1.py"
TEST = "tests/orchestrator/full_target/test_planner_package_v1.py::"
M = (
    Mutation(201, "facts exceed the 24-entry bound", SOURCE,
             'MAX_FACTS = 24', 'MAX_FACTS = 25', TEST + "test_limits_are_deterministic_and_report_omitted_counts"),
    Mutation(202, "accepted results exceed the 24-entry bound", SOURCE,
             'MAX_ACCEPTED_RESULTS = 24', 'MAX_ACCEPTED_RESULTS = 25',
             TEST + "test_accepted_and_failure_caps_preserve_exact_omission_counts"),
    Mutation(203, "failures exceed the 16-entry bound", SOURCE,
             'MAX_FAILURES = 16', 'MAX_FAILURES = 17',
             TEST + "test_accepted_and_failure_caps_preserve_exact_omission_counts"),
    Mutation(204, "methods exceed the per-signature bound", SOURCE,
             'MAX_METHODS_PER_SIGNATURE = 12', 'MAX_METHODS_PER_SIGNATURE = 13',
             TEST + "test_method_cap_is_per_signature_and_reports_all_omissions"),
    Mutation(205, "required visible references exceed 128", SOURCE,
             'MAX_VISIBLE_REFS = 128', 'MAX_VISIBLE_REFS = 129',
             TEST + "test_limits_are_deterministic_and_report_omitted_counts"),
    Mutation(206, "truncation silently loses omitted count", SOURCE,
             'omitted[name] = len(rows) - limit', 'omitted[name] = 0',
             TEST + "test_limits_are_deterministic_and_report_omitted_counts"),
    Mutation(207, "truncation reported as false", SOURCE,
             'truncated=bool(omitted)', 'truncated=False',
             TEST + "test_limits_are_deterministic_and_report_omitted_counts"),
    Mutation(208, "oversized mandatory decoded package accepted", SOURCE,
             'if len(result.canonical_bytes()) > MAX_PACKAGE_BYTES:',
             'if False and len(result.canonical_bytes()) > MAX_PACKAGE_BYTES:',
             TEST + "test_size_pressure_drops_optional_evidence_with_a_receipt"),
    Mutation(209, "unknown top-level field accepted", SOURCE,
             '        if unknown:\n            raise PlannerPackageCodecError(f"unknown planner package fields: {unknown}")',
             '        if False and unknown:\n            raise PlannerPackageCodecError(f"unknown planner package fields: {unknown}")',
             TEST + "test_codec_rejects_unknown_fields_and_oversized_mandatory_package"),
    Mutation(210, "noninteger omissions accepted by coercion", SOURCE,
             'type(v) is not int or v < 0 for v in omitted.values()',
             'int(v) < 0 for v in omitted.values()',
             TEST + "test_omitted_counts_are_integers_without_coercion"),
)

if __name__ == "__main__":
    runner.M = M
    runner.OUT = Path("/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-22/h2-mutations")
    raise SystemExit(runner.main())

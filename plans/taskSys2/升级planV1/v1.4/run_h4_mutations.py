#!/usr/bin/env python3
"""H4 contract, durable-trigger and runtime-wake mutation controls.

These checks do not replace the eleven repair actions' production acceptance
or paid-model scenarios. Each mutation executes in an isolated source copy.
"""
from pathlib import Path

import run_h1h_mutations as runner
from run_h1h_mutations import Mutation

SOURCE = "src/agent_orchestrator/planning/htn/repair_decision.py"
TEST = "tests/orchestrator/full_target/test_h4_repair_decision.py::"
RUNTIME = "tests/orchestrator/full_target/test_v14_runtime_closure.py::"
WAKE = "src/agent_orchestrator/orchestrator/planning_runtime_block.py"
WAKE_TEST = "tests/orchestrator/full_target/test_h4_retry_runtime_entry.py::test_runtime_wakes_fence_stale_planner_and_repeated_source_transitions"
REQUESTS = "src/agent_orchestrator/orchestrator/planning_repair_requests.py"
M = (
    Mutation(401, "accept altered request identity", SOURCE,
             "if self.request_id is not None and self.request_id != expected:",
             "if False and self.request_id is not None and self.request_id != expected:",
             TEST + "test_request_rejects_tampered_id_and_impact_overlap"),
    Mutation(402, "allow overlapping impact categories", SOURCE,
             "if any(groups[i] & groups[j] for i in range(3) for j in range(i + 1, 3)):",
             "if False and any(groups[i] & groups[j] for i in range(3) for j in range(i + 1, 3)):",
             TEST + "test_request_rejects_tampered_id_and_impact_overlap"),
    Mutation(403, "drop reverse DATA dependencies", SOURCE,
             "indexes = (data, support, methods, demands, accepts)",
             "indexes = (support, methods, demands, accepts)",
             TEST + "test_impact_analysis_uses_program_indexes_not_model_hints"),
    Mutation(404, "drop reverse support dependencies", SOURCE,
             "indexes = (data, support, methods, demands, accepts)",
             "indexes = (data, methods, demands, accepts)",
             TEST + "test_impact_analysis_uses_program_indexes_not_model_hints"),
    Mutation(405, "trust model affected hints", SOURCE,
             "seed = {_ref_id(item) for item in source_refs}",
             "seed = {_ref_id(item) for item in source_refs} | set(affected_hints)",
             TEST + "test_impact_analysis_uses_program_indexes_not_model_hints"),
    Mutation(406, "ignore unknown operation outcomes", SOURCE,
             'unresolved = {key for key, state in op_states.items() if state in {"UNKNOWN", "UNRESOLVED"}}',
             "unresolved = set()",
             TEST + "test_impact_analysis_uses_program_indexes_not_model_hints"),
    Mutation(407, "admit incomplete impact coverage", SOURCE,
             "if decision.status is RepairDecisionStatus.ADMITTED and decision.impact.unknown_coverage:",
             "if False and decision.status is RepairDecisionStatus.ADMITTED and decision.impact.unknown_coverage:",
             TEST + "test_unknown_coverage_without_unknown_operation_still_defers"),
    Mutation(408, "automatically approve human request", SOURCE,
             "if not human_authorized\n", "if False\n",
             TEST + "test_request_human_is_not_automatically_admitted"),
    Mutation(409, "lose ledger records on cold restore", SOURCE,
             "return cls(records)", "return cls({})",
             TEST + "test_restart_snapshot_restores_ledger"),
    Mutation(410, "overwrite same request decision", SOURCE,
             "if prior is not None:\n", "if False and prior is not None:\n",
             TEST + "test_same_request_cannot_replace_its_recorded_decision_after_restart"),
    Mutation(411, "collapse repeated runtime transitions", WAKE,
             '"previous_wake": None if wake is None else wake.payload["decision_id"],',
             '"previous_wake": None,', WAKE_TEST),
    Mutation(412, "admit stale runtime source", WAKE,
             'return bool(wake and content_hash_of(sources) == wake.payload["source_hash"])',
             "return bool(wake)", WAKE_TEST),
    Mutation(413, "nonblocking human request pauses dispatch", "src/agent_orchestrator/storage/planning_human_store.py",
             'r["state"] == "PENDING" and r["request"]["payload"]["blocking"]',
             'r["state"] == "PENDING"',
             RUNTIME + "test_h4_nonblocking_question_is_durable_without_pausing_dispatch"),
    Mutation(414, "rejected plan consumes repair request", REQUESTS,
             'if status != "COMMITTED" or decision_type not in',
             'if decision_type not in',
             RUNTIME + "test_h4_repair_request_is_consumed_only_by_committed_same_subject"),
    Mutation(415, "unaffected subject consumes repair request", REQUESTS,
             "if targets & affected:", "if True:",
             RUNTIME + "test_h4_repair_request_is_consumed_only_by_committed_same_subject"),
)

if __name__ == "__main__":
    runner.M = M
    runner.OUT = Path("/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-22/h4-mutations")
    raise SystemExit(runner.main())

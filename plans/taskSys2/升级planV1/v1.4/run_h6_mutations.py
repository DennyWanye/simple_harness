#!/usr/bin/env python3
"""Eight H6 negative controls; source checkout is never mutated.

Run only after main implementation. Each isolated copy receives one semantic
fault, and an ordinary assertion failure is required for a valid kill. Two
statistics controls use explicitly synthetic aggregation fixtures, not real
50-Mission promotion evidence.
"""
from pathlib import Path

import run_h1h_mutations as runner
from run_h1h_mutations import Mutation

LIFECYCLE = "src/agent_orchestrator/planning/htn/method_lifecycle.py"
STORE = "src/agent_orchestrator/storage/method_evaluation_store.py"
CONTRACT = "tests/orchestrator/full_target/test_h6_method_lifecycle_v1.py::"
REAL = "tests/orchestrator/full_target/test_h6_oracle_bound_evaluation.py::"
AGGREGATION = REAL + "test_aggregation_only_oracle_thresholds_use_all_25_candidate_receipts[4-REJECTED-0.84]"

M = (
    Mutation(601, "evaluation identity depends on input ordering", LIFECYCLE,
             'trials = tuple(sorted(set(str(item) for item in trial_ids)))',
             'trials = tuple(dict.fromkeys(str(item) for item in trial_ids))',
             CONTRACT + "test_evaluation_set_is_frozen_and_order_independent"),
    Mutation(602, "default trial threshold reduced below contract", LIFECYCLE,
             'min_trials: int = 20', 'min_trials: int = 1',
             CONTRACT + "test_policy_requires_trials_and_acceptance_thresholds"),
    Mutation(603, "critical side effects do not block evaluation", LIFECYCLE,
             'and self.critical_side_effect_failures <= policy.max_critical_side_effect_failures',
             'and True', CONTRACT + "test_evaluation_contract_accepts_complete_run_evidence_and_rejects_failed_gate"),
    Mutation(604, "formal success overrides failed independent oracle", STORE,
             '"accepted": run["accepted"] and outcome["passed"]',
             '"accepted": run["accepted"]', AGGREGATION),
    Mutation(605, "nonadopting Mission counts as candidate success", STORE,
             'if accepted and reference is not None:',
             'if False and accepted and reference is not None:',
             REAL + "test_real_terminal_nonadopting_mission_oracle_persists_and_legacy_run_stays_strict"),
    Mutation(606, "unknown usage enters evaluation", STORE,
             'if not usage or any(row["unknown"] for row in usage):',
             'if not usage:', REAL + "test_real_oracle_rejects_unknown_usage_and_post_receipt_usage_change"),
    Mutation(607, "failed candidate samples leave denominator", STORE,
             'sum(run["accepted"] for run in runs) / len(runs)',
             'sum(run["accepted"] for run in runs) / max(1, sum(run["accepted"] for run in runs))',
             AGGREGATION),
    Mutation(608, "baseline may be selected after running", STORE,
             '                *baseline_mission_ids,\n', '',
             REAL + "test_baseline_must_be_frozen_before_work_and_exact_replay_stays_valid"),
)

if __name__ == "__main__":
    runner.M = M
    runner.OUT = Path("/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-22/h6-mutations")
    raise SystemExit(runner.main())

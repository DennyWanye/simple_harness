#!/usr/bin/env python3
"""Five H8 core negative controls; these do not execute the 576-cell experiment."""
from pathlib import Path

import run_h1h_mutations as runner
from run_h1h_mutations import Mutation

CLOSURE = "tests/orchestrator/full_target/test_v14_runtime_closure.py::"
FORMAL = "tests/orchestrator/full_target/test_h8_formal_root_completion.py::"
METER = "src/agent_orchestrator/evaluation/htn_meter.py"
MATRIX = "src/agent_orchestrator/evaluation/htn_matrix.py"
M = (
    Mutation(801, "dirty root proof remains usable",
             "src/agent_orchestrator/evaluation/htn_hierarchical.py",
             'if any(item.subject_id in {str(root.task_id), str(resolution.resolution_id)}',
             'if False and any(item.subject_id in {str(root.task_id), str(resolution.resolution_id)}',
             FORMAL + "test_completed_root_identity_survives_cold_read_but_dirty_proof_is_refused"),
    Mutation(802, "recovery accepts another run identity", METER,
             'if content_hash_of(state) != envelope["sha256"] or state["binding"] != self._binding:',
             'if content_hash_of(state) != envelope["sha256"]:',
             CLOSURE + "test_h8_durable_meter_requires_explicit_recovery_and_matching_identity"),
    Mutation(803, "started physical call loses unknown usage at recovery", METER,
             'self.unknown_usage_calls = max(self._tokens(state["unknown_usage_calls"], "unknown usage"),\n'
             '                                       len(rows) - len(known))',
             'self.unknown_usage_calls = self._tokens(state["unknown_usage_calls"], "unknown usage")',
             CLOSURE + "test_h8_started_call_recovery_closes_meter_instead_of_resetting_budget"),
    Mutation(804, "repair passes without actual fault injection", MATRIX,
             'and (run.scenario.kind == ScenarioKind.NORMAL or self.intervention_triggered)',
             'and True', CLOSURE + "test_h8_non_normal_episode_cannot_pass_without_triggered_intervention[repair]"),
    Mutation(805, "hierarchical completed flag bypasses formal root proof", MATRIX,
             'and (run.arm == FourArm.STRONG_SINGLE_AGENT or bool(self.formal_completion_id))',
             'and True', FORMAL + "test_hierarchical_receipt_requires_formal_root_oracle_and_settled_usage"),
)

if __name__ == "__main__":
    runner.M = M
    runner.OUT = Path("/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-22/h8-core-mutations")
    raise SystemExit(runner.main())

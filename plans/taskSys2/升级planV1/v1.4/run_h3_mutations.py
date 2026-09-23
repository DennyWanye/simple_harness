#!/usr/bin/env python3
"""Eight isolated H3 routing, selection identity and evidence authority controls."""
from pathlib import Path

import run_h1h_mutations as runner
from run_h1h_mutations import Mutation

SOURCE = "src/agent_orchestrator/planning/htn/method_selection.py"
TEST = "tests/orchestrator/full_target/test_h3_method_selection.py::"
RUNTIME = "tests/orchestrator/full_target/test_v14_runtime_closure.py::"
M = (
    Mutation(301, "legacy adopts new model-selection policy", SOURCE,
             '    if legacy:\n', '    if False and legacy:\n',
             TEST + "test_h3_legacy_entry_keeps_cardinality_behavior_unchanged"),
    Mutation(302, "multiple applicable methods bypass planner", SOURCE,
             'len(applicable) == 1 and resolved.mode is not SelectionPolicyMode.ALWAYS_MODEL',
             'len(applicable) <= 2 and resolved.mode is not SelectionPolicyMode.ALWAYS_MODEL',
             TEST + "test_h3_policy_matrix_and_new_protocol_default"),
    Mutation(303, "same selection identity is claimed twice", SOURCE,
             'if key in self._claims:', 'if False and key in self._claims:',
             TEST + "test_h3_selection_identity_is_idempotent_across_restart"),
    Mutation(304, "selection identity drops the occurrence", SOURCE,
             'sha256_hex({"subject_id": subject_id, "candidates": candidate_set_digest(candidates)})',
             'candidate_set_digest(candidates)',
             RUNTIME + "test_h3_selection_identity_changes_for_a_different_occurrence"),
    Mutation(305, "selection identity omits candidates beyond prompt cap", SOURCE,
             '"candidates": [item.to_json() for item in normalized]',
             '"candidates": [item.to_json() for item in normalized[:12]]',
             RUNTIME + "test_h3_thirteenth_candidate_is_part_of_identity_even_when_prompt_view_caps_at_twelve"),
    Mutation(306, "evidence request bypasses observer availability", SOURCE,
             'if not _observer_available(key, observers):',
             'if False and not _observer_available(key, observers):',
             TEST + "test_h3_evidence_requires_registered_observer_and_authority"),
    Mutation(307, "evidence request bypasses caller authority", SOURCE,
             'if key not in predicate_grants and name not in predicate_grants and (',
             'if False and key not in predicate_grants and name not in predicate_grants and (',
             TEST + "test_h3_evidence_requires_registered_observer_and_authority"),
    Mutation(308, "duplicate evidence question accepted", SOURCE,
             'if question_identity in seen:', 'if False and question_identity in seen:',
             TEST + "test_h3_evidence_contract_caps_questions_and_rejects_duplicates"),
)

if __name__ == "__main__":
    runner.M = M
    runner.OUT = Path("/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-22/h3-mutations")
    raise SystemExit(runner.main())

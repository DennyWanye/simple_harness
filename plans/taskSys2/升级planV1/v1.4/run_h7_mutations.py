#!/usr/bin/env python3
"""Ten H7 port mutations; fake toolchain responses, no real solver quality claim."""
from pathlib import Path

import run_h1h_mutations as runner
from run_h1h_mutations import Mutation

SOURCE = "src/agent_orchestrator/planning/htn/backend_port.py"
TEST = "tests/orchestrator/full_target/test_h7_planning_backend_port_v1.py::"
M = tuple(Mutation(701 + index, "collapse solver status " + external, SOURCE,
    f'"{external}": BackendStatus.{expected}',
    f'"{external}": BackendStatus.' + ("TOOL_ERROR" if expected == "TIMEOUT" else "TIMEOUT"),
    TEST + f"test_panda_non_solved_statuses_keep_their_meaning[{external}-{expected}]")
    for index, (external, expected) in enumerate((
        ("UNSOLVABLE_PROVEN", "UNSOLVABLE_PROVEN"), ("SEARCH_LIMIT_REACHED", "SEARCH_LIMIT_REACHED"),
        ("TIMEOUT", "TIMEOUT"), ("UNSUPPORTED_FEATURE", "UNSUPPORTED_FEATURE"),
        ("SOLVER_UNAVAILABLE", "BACKEND_UNAVAILABLE"), ("TOOL_ERROR", "TOOL_ERROR"),
    ))) + (
    Mutation(707, "mutated snapshot reaches commit", SOURCE,
             'if rebuilt.digest != snapshot.digest:', 'if False and rebuilt.digest != snapshot.digest:',
             TEST + "test_snapshot_mutation_cannot_reach_the_commit_port"),
    Mutation(708, "witness hash is not verified", SOURCE,
             'and result.witness.valid_hash()', 'and True',
             TEST + "test_tampered_witness_hash_cannot_validate"),
    Mutation(709, "timeout witness may commit", SOURCE,
             'result.status is BackendStatus.SOLVED\n            and result.witness is not None',
             'True\n            and result.witness is not None',
             TEST + "test_non_solved_with_valid_witness_cannot_validate"),
    Mutation(710, "PANDA silently ignores requested expansion bound", SOURCE,
             'if limits.max_expansions is not None:', 'if False and limits.max_expansions is not None:',
             TEST + "test_panda_rejects_unsupported_expansion_limit_before_tool_call"),
)

if __name__ == "__main__":
    runner.M = M
    runner.OUT = Path("/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-22/h7-mutations")
    raise SystemExit(runner.main())

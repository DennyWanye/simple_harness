from __future__ import annotations

from dataclasses import replace

from deskpet.execution.failure_reports import FailureReportIssuer
from deskpet.harness.attempts import AttemptLoopGuard


def _report(*strategies: str):
    return FailureReportIssuer.issue(
        root_run_id="root-1",
        run_id="run-1",
        task_scope_id="task-1",
        attempt_id="attempt-1",
        plan_version=1,
        call_record_id="call-record-1",
        source_kind="tool_executor",
        source_identity="builtin:process_start",
        error_code="executable_not_found",
        action_fingerprint="action-1",
        failed_step="launch helper",
        prior_strategy_fingerprints=strategies,
    )


def test_unchanged_strategy_is_rejected_without_dispatch() -> None:
    guard = AttemptLoopGuard(max_model_replans=3)
    report = _report("strategy-a")
    assert (
        guard.evaluate(
            action_fingerprint=report.action_fingerprint,
            error_fingerprint=report.error_fingerprint,
            strategy_fingerprint="strategy-a",
            prior_reports=(report,),
        )
        == "reject_same_strategy"
    )


def test_model_may_change_strategy_until_budget_is_exhausted() -> None:
    guard = AttemptLoopGuard(max_model_replans=3)
    reports = (
        _report("strategy-a"),
        _report("strategy-b"),
        _report("strategy-c"),
    )
    assert (
        guard.evaluate(
            action_fingerprint=reports[0].action_fingerprint,
            error_fingerprint=reports[0].error_fingerprint,
            strategy_fingerprint="strategy-d",
            prior_reports=reports[:2],
        )
        == "allow"
    )
    assert (
        guard.evaluate(
            action_fingerprint=reports[0].action_fingerprint,
            error_fingerprint=reports[0].error_fingerprint,
            strategy_fingerprint="strategy-d",
            prior_reports=reports,
        )
        == "block_budget"
    )


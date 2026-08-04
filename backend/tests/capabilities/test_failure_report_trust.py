from __future__ import annotations

from deskpet.execution.failure_reports import (
    FailureReportIssuer,
    TaskFailureReport,
)


def _report(**overrides):
    values = {
        "root_run_id": "root-1",
        "run_id": "run-1",
        "task_scope_id": "task-1",
        "attempt_id": "attempt-1",
        "plan_version": 1,
        "call_record_id": "call-record-1",
        "source_kind": "tool_executor",
        "source_identity": "builtin:move_file",
        "error_code": "destination_exists",
        "action_fingerprint": "action-1",
        "failed_step": "rename photo",
        "provider_call_id": "provider-call-1",
    }
    values.update(overrides)
    return FailureReportIssuer.issue(**values)


def test_failure_report_is_deterministic_and_round_trips() -> None:
    first = _report()
    second = _report()
    assert first == second
    assert TaskFailureReport.from_dict(first.to_dict()) == first


def test_tool_text_cannot_override_host_failure_class_or_completed_steps() -> None:
    report = _report(
        evidence_refs=("tool-text:error_class=capability",),
        completed_step_refs=("trusted-receipt-1",),
    )
    assert report.error_class == "task_project"
    assert report.completed_step_refs == ("trusted-receipt-1",)
    assert report.evidence_refs == ("tool-text:error_class=capability",)


def test_child_and_tool_failure_fingerprints_keep_host_identity() -> None:
    tool = _report()
    child = _report(
        source_kind="child_terminal",
        source_identity="workflow.durable_task",
        child_run_id="child-1",
    )
    assert tool.error_fingerprint != child.error_fingerprint
    assert tool.report_ref != child.report_ref


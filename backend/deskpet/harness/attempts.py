"""Attempt and plan-version contracts for failure-aware model replanning."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from deskpet.execution.contracts import JsonValue
from deskpet.execution.failure_reports import TaskFailureReport


AttemptStatus = Literal[
    "running",
    "failed",
    "rejected",
    "succeeded",
    "waiting_external",
    "blocked",
    "cancelled",
]
LoopDecision = Literal["allow", "reject_same_strategy", "block_budget"]


def _hash(value: Mapping[str, JsonValue]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TaskGoalRecord:
    goal_id: str
    root_run_id: str
    task_scope_id: str
    objective_ref: str
    status: Literal[
        "active", "waiting_external", "completed", "blocked", "cancelled"
    ] = "active"


@dataclass(frozen=True, slots=True)
class PlanVersionRecord:
    root_run_id: str
    plan_version: int
    trigger_failure_set_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderActionCall:
    call_record_id: str
    root_run_id: str
    provider_batch_id: str
    call_order: int
    provider_call_id: str
    raw_tool_name: str
    raw_arguments_ref: str
    raw_arguments_hash: str
    parsed_arguments_hash: str | None
    admission_state: Literal[
        "admitted", "prepared", "rejected", "waiting_external", "settled"
    ]
    prepared_call_ref: str | None = None
    command_boundary_ref: str | None = None
    terminal_outcome_ref: str | None = None


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    attempt_id: str
    root_run_id: str
    run_id: str
    provider_turn_id: str
    provider_batch_id: str
    plan_version: int
    trigger_failure_set_id: str | None
    supersedes_attempt_id: str | None
    strategy_fingerprint: str
    planned_call_refs: tuple[str, ...]
    checkpoint_ref: str | None
    status: AttemptStatus
    budget_eligible: bool


@dataclass(frozen=True, slots=True)
class AttemptFailureSet:
    failure_set_id: str
    root_run_id: str
    failed_attempt_id: str
    report_refs: tuple[str, ...]
    primary_report_ref: str
    backfill_state: Literal["pending", "ready", "committed"] = "pending"
    provider_resume_state: Literal[
        "pending", "requested", "accepted"
    ] = "pending"

    @classmethod
    def from_reports(
        cls, attempt: AttemptRecord, reports: Sequence[TaskFailureReport]
    ) -> "AttemptFailureSet":
        ordered = tuple(
            sorted(
                reports,
                key=lambda report: (
                    report.provider_call_id or "",
                    report.report_ref,
                ),
            )
        )
        if not ordered:
            raise ValueError("failure set requires at least one report")
        refs = tuple(report.report_ref for report in ordered)
        value: dict[str, JsonValue] = {
            "root_run_id": attempt.root_run_id,
            "failed_attempt_id": attempt.attempt_id,
            "report_refs": list(refs),
        }
        return cls(
            failure_set_id="failure-set:" + _hash(value),
            root_run_id=attempt.root_run_id,
            failed_attempt_id=attempt.attempt_id,
            report_refs=refs,
            primary_report_ref=refs[0],
        )


@dataclass(frozen=True, slots=True)
class TaskExternalWait:
    wait_ref: str
    root_run_id: str
    attempt_id: str
    call_record_id: str
    provider_call_id: str
    command_boundary_ref: str | None
    effect_id: str | None
    wait_kind: Literal["credential", "user_content", "uac", "third_party"]
    required_action_ref: str
    checkpoint_ref: str | None
    resume_admission_state: Literal["admitted", "prepared"]
    evidence_refs: tuple[str, ...]
    state: Literal["open", "satisfied", "cancelled"] = "open"


class AttemptLoopGuard:
    """Bound unchanged strategies without choosing the next strategy."""

    def __init__(self, *, max_model_replans: int = 3, max_transient_retries: int = 2) -> None:
        self.max_model_replans = max(1, int(max_model_replans))
        self.max_transient_retries = max(0, int(max_transient_retries))

    def evaluate(
        self,
        *,
        action_fingerprint: str,
        error_fingerprint: str,
        strategy_fingerprint: str,
        prior_reports: Sequence[TaskFailureReport],
    ) -> LoopDecision:
        same_failure = tuple(
            report
            for report in prior_reports
            if report.action_fingerprint == action_fingerprint
            and report.error_fingerprint == error_fingerprint
        )
        if any(
            strategy_fingerprint in report.prior_strategy_fingerprints
            or (
                report.prior_strategy_fingerprints
                and report.prior_strategy_fingerprints[-1] == strategy_fingerprint
            )
            for report in same_failure
        ):
            return "reject_same_strategy"
        strategies = {
            strategy
            for report in same_failure
            for strategy in report.prior_strategy_fingerprints
            if strategy
        }
        if len(strategies) >= self.max_model_replans:
            return "block_budget"
        return "allow"


__all__ = [
    "AttemptFailureSet",
    "AttemptLoopGuard",
    "AttemptRecord",
    "PlanVersionRecord",
    "ProviderActionCall",
    "TaskExternalWait",
    "TaskGoalRecord",
]

"""Trusted, structured failure facts used for model replanning.

Only host execution code should call :class:`FailureReportIssuer`.  Tool or
plugin payloads are treated as evidence text and cannot choose the failure
class, identity, completed steps, or retry budget.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from deskpet.execution.contracts import JsonValue


FailureSource = Literal[
    "tool_parse",
    "tool_unknown",
    "tool_preflight",
    "tool_prepare",
    "tool_authorization",
    "tool_executor",
    "child_launch",
    "child_terminal",
    "strategy_rejected",
]
FailureClass = Literal["transient", "task_project", "capability", "environment"]


def _canonical(value: Mapping[str, JsonValue]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _ref(prefix: str, value: Mapping[str, JsonValue]) -> str:
    return f"{prefix}:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TaskFailureReport:
    report_ref: str
    root_run_id: str
    run_id: str
    task_scope_id: str
    attempt_id: str
    plan_version: int
    call_record_id: str
    source_kind: FailureSource
    source_identity: str
    provider_call_id: str | None
    child_run_id: str | None
    inner_failure_ref: str | None
    failed_call_id: str | None
    failed_effect_id: str | None
    failed_step: str
    error_class: FailureClass
    error_code: str
    error_fingerprint: str
    action_fingerprint: str
    exit_code: int | None
    evidence_refs: tuple[str, ...]
    completed_step_refs: tuple[str, ...]
    artifact_refs: tuple[str, ...]
    checkpoint_ref: str | None
    prior_strategy_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        if not all(
            (
                self.report_ref,
                self.root_run_id,
                self.run_id,
                self.task_scope_id,
                self.attempt_id,
                self.call_record_id,
                self.source_identity,
                self.failed_step,
                self.error_code,
                self.error_fingerprint,
                self.action_fingerprint,
            )
        ):
            raise ValueError("failure report identity is incomplete")
        if self.plan_version < 1:
            raise ValueError("failure report plan version must be positive")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "report_ref": self.report_ref,
            "root_run_id": self.root_run_id,
            "run_id": self.run_id,
            "task_scope_id": self.task_scope_id,
            "attempt_id": self.attempt_id,
            "plan_version": self.plan_version,
            "call_record_id": self.call_record_id,
            "source_kind": self.source_kind,
            "source_identity": self.source_identity,
            "provider_call_id": self.provider_call_id,
            "child_run_id": self.child_run_id,
            "inner_failure_ref": self.inner_failure_ref,
            "failed_call_id": self.failed_call_id,
            "failed_effect_id": self.failed_effect_id,
            "failed_step": self.failed_step,
            "error_class": self.error_class,
            "error_code": self.error_code,
            "error_fingerprint": self.error_fingerprint,
            "action_fingerprint": self.action_fingerprint,
            "exit_code": self.exit_code,
            "evidence_refs": list(self.evidence_refs),
            "completed_step_refs": list(self.completed_step_refs),
            "artifact_refs": list(self.artifact_refs),
            "checkpoint_ref": self.checkpoint_ref,
            "prior_strategy_fingerprints": list(
                self.prior_strategy_fingerprints
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TaskFailureReport":
        payload = dict(value)
        for name in (
            "evidence_refs",
            "completed_step_refs",
            "artifact_refs",
            "prior_strategy_fingerprints",
        ):
            payload[name] = tuple(str(item) for item in payload.get(name, ()) or ())
        return cls(**payload)  # type: ignore[arg-type]


class FailureReportIssuer:
    """Host-only deterministic report signer."""

    _SOURCE_DEFAULT_CLASS: Mapping[FailureSource, FailureClass] = {
        "tool_parse": "task_project",
        "tool_unknown": "environment",
        "tool_preflight": "task_project",
        "tool_prepare": "task_project",
        "tool_authorization": "environment",
        "tool_executor": "task_project",
        "child_launch": "environment",
        "child_terminal": "task_project",
        "strategy_rejected": "task_project",
    }

    @classmethod
    def issue(
        cls,
        *,
        root_run_id: str,
        run_id: str,
        task_scope_id: str,
        attempt_id: str,
        plan_version: int,
        call_record_id: str,
        source_kind: FailureSource,
        source_identity: str,
        error_code: str,
        action_fingerprint: str,
        failed_step: str,
        provider_call_id: str | None = None,
        child_run_id: str | None = None,
        inner_failure_ref: str | None = None,
        failed_call_id: str | None = None,
        failed_effect_id: str | None = None,
        error_class: FailureClass | None = None,
        exit_code: int | None = None,
        evidence_refs: Sequence[str] = (),
        completed_step_refs: Sequence[str] = (),
        artifact_refs: Sequence[str] = (),
        checkpoint_ref: str | None = None,
        prior_strategy_fingerprints: Sequence[str] = (),
    ) -> TaskFailureReport:
        selected_class = error_class or cls._SOURCE_DEFAULT_CLASS[source_kind]
        fingerprint_payload: dict[str, JsonValue] = {
            "source_kind": source_kind,
            "source_identity": source_identity,
            "error_code": error_code,
            "failed_step": failed_step,
            "exit_code": exit_code,
        }
        error_fingerprint = hashlib.sha256(
            _canonical(fingerprint_payload).encode("utf-8")
        ).hexdigest()
        unsigned: dict[str, JsonValue] = {
            "root_run_id": root_run_id,
            "run_id": run_id,
            "task_scope_id": task_scope_id,
            "attempt_id": attempt_id,
            "plan_version": plan_version,
            "call_record_id": call_record_id,
            "source_kind": source_kind,
            "source_identity": source_identity,
            "provider_call_id": provider_call_id,
            "child_run_id": child_run_id,
            "inner_failure_ref": inner_failure_ref,
            "failed_call_id": failed_call_id,
            "failed_effect_id": failed_effect_id,
            "failed_step": failed_step,
            "error_class": selected_class,
            "error_code": error_code,
            "error_fingerprint": error_fingerprint,
            "action_fingerprint": action_fingerprint,
            "exit_code": exit_code,
            "evidence_refs": list(evidence_refs),
            "completed_step_refs": list(completed_step_refs),
            "artifact_refs": list(artifact_refs),
            "checkpoint_ref": checkpoint_ref,
            "prior_strategy_fingerprints": list(
                prior_strategy_fingerprints
            ),
        }
        return TaskFailureReport(
            report_ref=_ref("failure", unsigned),
            root_run_id=root_run_id,
            run_id=run_id,
            task_scope_id=task_scope_id,
            attempt_id=attempt_id,
            plan_version=plan_version,
            call_record_id=call_record_id,
            source_kind=source_kind,
            source_identity=source_identity,
            provider_call_id=provider_call_id,
            child_run_id=child_run_id,
            inner_failure_ref=inner_failure_ref,
            failed_call_id=failed_call_id,
            failed_effect_id=failed_effect_id,
            failed_step=failed_step,
            error_class=selected_class,
            error_code=error_code,
            error_fingerprint=error_fingerprint,
            action_fingerprint=action_fingerprint,
            exit_code=exit_code,
            evidence_refs=tuple(str(item) for item in evidence_refs),
            completed_step_refs=tuple(
                str(item) for item in completed_step_refs
            ),
            artifact_refs=tuple(str(item) for item in artifact_refs),
            checkpoint_ref=checkpoint_ref,
            prior_strategy_fingerprints=tuple(
                str(item) for item in prior_strategy_fingerprints
            )[-3:],
        )


__all__ = ["FailureReportIssuer", "TaskFailureReport"]

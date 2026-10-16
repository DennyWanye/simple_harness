# SPDX-License-Identifier: BUSL-1.1

"""Shared helpers for the split class."""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "STOPPED", "CANCELLED"})


class TurnState(StrEnum):
    QUEUED = "QUEUED"
    CLAIMED = "CLAIMED"
    SETTLED = "SETTLED"


class RunState(StrEnum):
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    PAUSED = "PAUSED"
    STOP_REQUESTED = "STOP_REQUESTED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"
    CANCELLED = "CANCELLED"


class ControlKind(StrEnum):
    PAUSE = "pause"
    STOP = "stop"
    CANCEL = "cancel"


class ForegroundQueueError(RuntimeError):
    code = "foreground_queue_rejected"

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ContextLineage:
    context_snapshot_id: str
    context_snapshot_revision: int
    context_snapshot_hash: str


@dataclass(frozen=True, slots=True)
class EnqueueReceipt:
    turn_id: str
    subject: str
    primary_conversation_id: str
    task_scope_id: str | None
    evidence_id: str
    evidence_hash: str
    enqueue_sequence: int
    turn_hash: str


@dataclass(frozen=True, slots=True)
class AdmissionReceipt:
    admission_receipt_id: str
    admission_receipt_hash: str
    host_run_id: str
    turn_id: str
    subject: str
    enqueue_sequence: int
    state: RunState
    owner_id: str
    generation: int
    lease_expires_at: float
    lineage_hash: str
    recovered: bool


@dataclass(frozen=True, slots=True)
class PreparationCandidate:
    subject: str
    turn_id: str
    primary_conversation_id: str
    task_scope_id: str | None
    evidence_id: str
    evidence_hash: str
    enqueue_sequence: int
    turn_hash: str
    binding_set_revision: int
    binding_set_receipt_id: str | None
    binding_set_receipt_hash: str | None
    candidate_hash: str
    candidate_json: str


@dataclass(frozen=True, slots=True)
class PreparationDraftReceipt:
    draft_id: str
    subject: str
    turn_id: str
    candidate_hash: str
    context_snapshot_id: str
    context_snapshot_revision: int
    context_snapshot_hash: str
    draft_hash: str
    candidate_json: str


@dataclass(frozen=True, slots=True)
class ExecutionAuditReceipt:
    receipt_id: str
    host_run_id: str
    generation: int
    phase: str
    receipt_hash: str


@dataclass(frozen=True, slots=True)
class SdkRunBindingReceipt:
    binding_id: str
    host_run_id: str
    sdk_run_id: str
    binding_hash: str


@dataclass(frozen=True, slots=True)
class LeaseReceipt:
    lease_receipt_id: str
    host_run_id: str
    owner_id: str
    generation: int
    prior_generation: int | None
    action: str
    expires_at: float | None
    lease_hash: str


@dataclass(frozen=True, slots=True)
class ControlReceipt:
    control_id: str
    host_run_id: str
    control_kind: ControlKind
    outcome: str
    reduced_state: RunState
    generation: int
    control_hash: str
    signal_id: str | None


@dataclass(frozen=True, slots=True)
class SignalEnvelope:
    signal_id: str
    control_id: str
    host_run_id: str
    sdk_run_id: str | None
    generation: int
    control_kind: ControlKind
    signal_hash: str


@dataclass(frozen=True, slots=True)
class SignalAckReceipt:
    ack_id: str
    signal_id: str
    host_run_id: str
    sdk_run_id: str
    generation: int
    sdk_signal_id: str
    ack_hash: str


@dataclass(frozen=True, slots=True)
class TerminalReceipt:
    terminal_receipt_id: str
    host_run_id: str
    sdk_run_id: str
    terminal_state: RunState
    generation: int
    sdk_event_id: str
    sdk_event_hash: str
    receipt_hash: str


@dataclass(frozen=True, slots=True)
class ForegroundRunSnapshot:
    host_run_id: str
    sdk_run_id: str | None
    subject: str
    primary_conversation_id: str
    turn_id: str
    task_scope_id: str | None
    binding_set_revision: int
    binding_set_receipt_id: str | None
    binding_set_receipt_hash: str | None
    context_snapshot_id: str
    context_snapshot_revision: int
    context_snapshot_hash: str
    state: RunState
    desired_control: ControlKind | None
    owner_id: str
    generation: int
    lease_expires_at: float
    lineage_hash: str
    snapshot_hash: str


def _uuid(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"simple-harness:{label}"))


def _clock_value(clock: Callable[[], float]) -> float:
    value = float(clock())
    if not math.isfinite(value) or value < 0:
        raise ForegroundQueueError("foreground_clock_invalid")
    return value


def _duration(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ForegroundQueueError("foreground_lease_duration_invalid")
    duration = float(value)
    if not math.isfinite(duration) or duration <= 0 or duration > 86400:
        raise ForegroundQueueError("foreground_lease_duration_invalid")
    return duration

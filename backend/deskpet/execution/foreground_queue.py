# SPDX-License-Identifier: BUSL-1.1

"""Durable Host authority for one foreground Run and ordinary-turn FIFO.

This module intentionally does not execute an Agent loop.  It owns durable
admission, FIFO ordering, controls and lease generations, while the published
Harness SDK Runtime remains the sole owner of actual Agent execution.  A Host
Run can bind exactly one SDK Run and every lifecycle write is fenced by the
current lease generation.
"""

from __future__ import annotations

from .foreground_queue_domains.claims import FOREGROUND_SCHEDULER_KIND, ClaimedExecution, ClaimMixin
from .foreground_queue_domains.driving import DrivingMixin
from .foreground_queue_domains.enqueue import EnqueueMixin
from .foreground_queue_domains.observation import EffectAdmissionReceipt, ObservationMixin
from .foreground_queue_domains.support import (
    AdmissionReceipt,
    ContextLineage,
    ControlKind,
    ControlReceipt,
    EnqueueReceipt,
    ExecutionAuditReceipt,
    ForegroundQueueError,
    ForegroundRunSnapshot,
    LeaseReceipt,
    PreparationCandidate,
    PreparationDraftReceipt,
    RunState,
    SdkRunBindingReceipt,
    SignalAckReceipt,
    SignalEnvelope,
    TerminalReceipt,
    TurnState,
)


class ForegroundQueueStore(EnqueueMixin, ClaimMixin, DrivingMixin, ObservationMixin):
    """SQLite-backed reducer for the frozen S4 foreground coordination AC."""


__all__ = [
    "FOREGROUND_SCHEDULER_KIND",
    "AdmissionReceipt",
    "ClaimedExecution",
    "ContextLineage",
    "ControlKind",
    "ControlReceipt",
    "EffectAdmissionReceipt",
    "EnqueueReceipt",
    "ExecutionAuditReceipt",
    "ForegroundQueueError",
    "ForegroundQueueStore",
    "ForegroundRunSnapshot",
    "LeaseReceipt",
    "PreparationCandidate",
    "PreparationDraftReceipt",
    "RunState",
    "SdkRunBindingReceipt",
    "SignalAckReceipt",
    "SignalEnvelope",
    "TerminalReceipt",
    "TurnState",
]

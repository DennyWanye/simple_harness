"""Ordered retention and startup reconciliation for durable workflows."""

from __future__ import annotations

import time

from .retention_domains.cleanup import (
    BLOB_REF_STAGE,
    CHECKPOINT_STAGE,
    CONTROL_STAGE,
    DELIVERY_STAGE,
    LINEAGE_STAGE,
    ORPHAN_STAGE,
    RESERVATION_STAGE,
    SNAPSHOT_STAGE,
    TOMBSTONE_STAGE,
    CleanupStageMixin,
)
from .retention_domains.core import REACHABILITY_STAGE, ClockPort, RetentionPolicy
from .retention_domains.planning import CleanupPlanMixin
from .retention_domains.support import RUN_STAGE, RetentionReport, RetentionStageResult
from .retention_domains.v6_components import V6ComponentCleanupMixin

CLEANUP_STAGE_ORDER = (
    DELIVERY_STAGE,
    TOMBSTONE_STAGE,
    CHECKPOINT_STAGE,
    BLOB_REF_STAGE,
    ORPHAN_STAGE,
)


class SystemClock:
    def now(self) -> float:
        return time.time()


class WorkflowRetentionManager(V6ComponentCleanupMixin, CleanupPlanMixin, CleanupStageMixin):
    """Apply the fixed delivery -> tombstone -> reachability -> ref -> grace order."""


__all__ = [
    "BLOB_REF_STAGE",
    "CHECKPOINT_STAGE",
    "CLEANUP_STAGE_ORDER",
    "CONTROL_STAGE",
    "ClockPort",
    "DELIVERY_STAGE",
    "ORPHAN_STAGE",
    "LINEAGE_STAGE",
    "REACHABILITY_STAGE",
    "RESERVATION_STAGE",
    "RetentionPolicy",
    "RetentionReport",
    "RetentionStageResult",
    "RUN_STAGE",
    "SNAPSHOT_STAGE",
    "SystemClock",
    "TOMBSTONE_STAGE",
    "WorkflowRetentionManager",
]

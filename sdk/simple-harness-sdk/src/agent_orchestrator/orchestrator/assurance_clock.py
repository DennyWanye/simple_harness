# SPDX-License-Identifier: Apache-2.0
"""Persist clock observations through the original Commit and event writers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from ..assurance.clock import CLOCK_PERSIST_STEP_MS, ClockState
from ..assurance.codec import AssuranceError, fingerprint, integer
from ..assurance.refs import AssuranceRef, Pin
from ..storage.assurance_store import AssuranceStore
from ..storage.assurance_work import atomic

if TYPE_CHECKING:
    from .commit_service import CommitService


def observe_assurance_clock(commit: CommitService, *, now_ms: int) -> ClockState:
    """One receipt per *written* observation; one discontinuity per generation.

    The high-water mark is exact in this process (kept on the CommitService); the row and
    its receipt are written only on a rollback, on a recovery, or when the mark has moved
    ``CLOCK_PERSIST_STEP_MS`` past the stored one.  Writing it on every millisecond of
    ordinary advance was pure churn (真机两小时 8.6 万条回执).

    Events are diagnostic wakeups. They never assert source validity or cancel
    physical work. A rollback does not move any expiry or work deadline.
    """
    integer(now_ms)
    store = commit.store
    with atomic(store) as connection:
        row = connection.execute(
            "SELECT * FROM assurance_environment_state WHERE singleton=1"
        ).fetchone()
        if row is None:
            raise AssuranceError("ASSURANCE_ENVIRONMENT_UNINITIALIZED")
        stored = ClockState(row["clock_generation"], row["wall_high_ms"], row["clock_state"])
        seen = getattr(commit, "_assurance_clock_seen", None)
        seen_high = seen.wall_high_ms if seen is not None and seen.generation == stored.generation else 0
        previous = ClockState(stored.generation, max(stored.wall_high_ms, seen_high), stored.state)
        current = previous.observe(now_ms)
        commit._assurance_clock_seen = current
        if (current.generation, current.state) == (stored.generation, stored.state) and (
                current.wall_high_ms - stored.wall_high_ms < CLOCK_PERSIST_STEP_MS):
            return current
        receipt_id = "assurance-clock:" + uuid4().hex
        body = {
            "schema_version": 1,
            "receipt_role": "CLOCK_OBSERVATION_ONLY",
            "observed_at_ms": now_ms,
            "previous_row_version": row["row_version"],
            "clock_generation": current.generation,
            "wall_high_ms": current.wall_high_ms,
            "clock_state": current.state,
        }
        receipt_hash = fingerprint(body)
        store.insert_receipt(
            commit_id=receipt_id,
            kind="AssuranceClockObserved",
            subject_id="assurance-environment",
            base_version=row["row_version"],
            proposal_hash=receipt_hash,
            receipt=body,
        )
        AssuranceStore(store).observe_clock(
            now_ms, receipt=AssuranceRef("commit_receipt", Pin(receipt_id, 0, receipt_hash)),
            seen_high_ms=previous.wall_high_ms,
        )
        if current.state != previous.state:
            event_type = (
                "TimeDiscontinuity" if current.state == "ROLLBACK" else "AssuranceClockStable"
            )
            for binding in connection.execute(
                "SELECT mission_id FROM assurance_mission_bindings ORDER BY mission_id"
            ).fetchall():
                commit._emit(
                    event_type,
                    binding["mission_id"],
                    key=f"assurance:{binding['mission_id']}:{current.generation}:{current.state}",
                    payload={**body, "clock_receipt_id": receipt_id},
                )
        return current

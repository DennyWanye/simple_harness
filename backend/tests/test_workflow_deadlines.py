from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from deskpet.workflows.deadlines import (
    DeadlineValidationError,
    DurableDeadlineV1,
    checkpoint_deadline,
    create_child_deadline,
    deadline_id_for,
    remaining_timeout_seconds,
    resume_deadline,
)


NOW = datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone.utc)
POLICY_HASH = "a" * 64


def _deadline(*, budget_ms: int = 120_000) -> DurableDeadlineV1:
    return DurableDeadlineV1.create(
        owner_id="run-1",
        logical_scope="research_retrieval",
        policy_hash=POLICY_HASH,
        budget_ms=budget_ms,
        now_wall=NOW,
    )


def test_deadline_roundtrip_is_strict_and_never_serializes_monotonic_epoch() -> None:
    deadline = _deadline()
    payload = deadline.to_json()

    assert DurableDeadlineV1.from_json(copy.deepcopy(payload)) == deadline
    assert all("monotonic" not in key for key in payload)
    assert deadline.deadline_id == deadline_id_for(
        owner_id="run-1",
        logical_scope="research_retrieval",
        policy_hash=POLICY_HASH,
    )

    payload["future_field"] = True
    with pytest.raises(DeadlineValidationError, match="deadline_keys_differ"):
        DurableDeadlineV1.from_json(payload)


def test_resume_counts_offline_time_and_anchors_a_new_local_lease() -> None:
    resumed = resume_deadline(
        _deadline(),
        now_wall=NOW + timedelta(seconds=5),
        now_monotonic_ns=10_000_000_000,
    )

    assert resumed.transition == "resumed"
    assert resumed.charged_ms == 5_000
    assert resumed.state.remaining_ms == 115_000
    assert resumed.state.revision == 1
    assert resumed.lease is not None
    assert resumed.lease.revision == 1
    assert resumed.lease.deadline_monotonic_ns == 125_000_000_000
    assert resumed.allows_upstream


def test_checkpoint_charges_monotonic_time_without_double_charging_wall() -> None:
    resumed = resume_deadline(
        _deadline(),
        now_wall=NOW,
        now_monotonic_ns=1_000_000_000,
    )
    assert resumed.lease is not None

    checkpointed = checkpoint_deadline(
        resumed.state,
        resumed.lease,
        now_wall=NOW + timedelta(seconds=10),
        now_monotonic_ns=11_000_000_000,
    )
    assert checkpointed.charged_ms == 10_000
    assert checkpointed.state.remaining_ms == 110_000
    assert checkpointed.lease is not None

    recovered = resume_deadline(
        checkpointed.state,
        now_wall=NOW + timedelta(seconds=15),
        now_monotonic_ns=50_000_000_000,
    )
    assert recovered.charged_ms == 5_000
    assert recovered.state.remaining_ms == 105_000


def test_submillisecond_active_intervals_are_carried_in_local_lease() -> None:
    resumed = resume_deadline(
        _deadline(), now_wall=NOW, now_monotonic_ns=1_000_000
    )
    assert resumed.lease is not None
    first = checkpoint_deadline(
        resumed.state,
        resumed.lease,
        now_wall=NOW + timedelta(microseconds=500),
        now_monotonic_ns=1_500_000,
    )
    assert first.charged_ms == 0
    assert first.lease is not None and first.lease.unaccounted_ns == 500_000

    second = checkpoint_deadline(
        first.state,
        first.lease,
        now_wall=NOW + timedelta(milliseconds=1),
        now_monotonic_ns=2_000_000,
    )
    assert second.charged_ms == 1
    assert second.state.remaining_ms == 119_999
    assert second.lease is not None and second.lease.unaccounted_ns == 0


def test_small_wall_rollback_is_clamped_without_extending_budget() -> None:
    first = resume_deadline(
        _deadline(),
        now_wall=NOW + timedelta(seconds=5),
        now_monotonic_ns=1_000_000_000,
    )
    replay = resume_deadline(
        first.state,
        now_wall=NOW + timedelta(seconds=4),
        now_monotonic_ns=2_000_000_000,
    )

    assert replay.state.status == "open"
    assert replay.state.remaining_ms == first.state.remaining_ms
    assert replay.state.last_observed_at == first.state.last_observed_at
    assert replay.charged_ms == 0


def test_large_wall_rollback_fails_closed() -> None:
    first = resume_deadline(
        _deadline(),
        now_wall=NOW + timedelta(seconds=5),
        now_monotonic_ns=1_000_000_000,
    )
    rolled_back = resume_deadline(
        first.state,
        now_wall=NOW + timedelta(seconds=2),
        now_monotonic_ns=2_000_000_000,
    )

    assert rolled_back.state.status == "expired"
    assert rolled_back.state.terminal_reason == "clock_rollback"
    assert rolled_back.lease is None
    assert not rolled_back.allows_upstream


def test_wall_guard_expires_after_long_offline_window() -> None:
    expired = resume_deadline(
        _deadline(),
        now_wall=NOW + timedelta(seconds=120),
        now_monotonic_ns=1_000_000_000,
    )

    assert expired.state.status == "expired"
    assert expired.state.terminal_reason == "wall_guard"
    assert expired.state.remaining_ms == 0
    assert expired.charged_ms == 120_000
    assert expired.lease is None


def test_checkpoint_rejects_stale_or_backwards_monotonic_lease() -> None:
    resumed = resume_deadline(
        _deadline(), now_wall=NOW, now_monotonic_ns=10_000_000
    )
    assert resumed.lease is not None
    checkpointed = checkpoint_deadline(
        resumed.state,
        resumed.lease,
        now_wall=NOW + timedelta(milliseconds=1),
        now_monotonic_ns=11_000_000,
    )

    with pytest.raises(DeadlineValidationError, match="deadline_lease_stale"):
        checkpoint_deadline(
            checkpointed.state,
            resumed.lease,
            now_wall=NOW + timedelta(milliseconds=2),
            now_monotonic_ns=12_000_000,
        )

    assert checkpointed.lease is not None
    with pytest.raises(DeadlineValidationError, match="deadline_monotonic_rollback"):
        checkpoint_deadline(
            checkpointed.state,
            checkpointed.lease,
            now_wall=NOW + timedelta(milliseconds=2),
            now_monotonic_ns=10_000_000,
        )


def test_child_deadline_is_bounded_by_parent_budget_and_wall_guard() -> None:
    parent = resume_deadline(
        _deadline(),
        now_wall=NOW + timedelta(seconds=110),
        now_monotonic_ns=1_000_000_000,
    ).state
    child = create_child_deadline(
        parent,
        logical_key="page:https://example.invalid",
        logical_scope="page_fetch",
        budget_ms=20_000,
        now_wall=NOW + timedelta(seconds=110),
    )

    assert child.parent_deadline_id == parent.deadline_id
    assert child.budget_ms == 10_000
    assert child.remaining_ms == 10_000
    assert child.wall_not_after == parent.wall_not_after


def test_remaining_timeout_uses_shared_monotonic_deadline_and_stage_cap() -> None:
    resumed = resume_deadline(
        _deadline(), now_wall=NOW, now_monotonic_ns=1_000_000_000
    )
    assert resumed.lease is not None

    assert remaining_timeout_seconds(
        resumed.lease,
        now_monotonic_ns=2_000_000_000,
    ) == 119.0
    assert remaining_timeout_seconds(
        resumed.lease,
        now_monotonic_ns=2_000_000_000,
        stage_cap_ms=3_000,
    ) == 3.0


def test_v1_rejects_pause_policy_and_naive_wall_clock() -> None:
    payload = _deadline().to_json()
    payload["offline_policy"] = "pause"
    with pytest.raises(DeadlineValidationError, match="offline_policy=count"):
        DurableDeadlineV1.from_json(payload)

    with pytest.raises(DeadlineValidationError, match="timezone"):
        DurableDeadlineV1.create(
            owner_id="run",
            logical_scope="scope",
            policy_hash=POLICY_HASH,
            budget_ms=1_000,
            now_wall=datetime(2026, 7, 18, 10, 0, 0),
        )

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from deskpet.workflows.definitions.deep_research_v5_contracts import (
    ContractValidationError,
    DimensionCoverage,
)
from deskpet.workflows.definitions.deep_research_v5_loop_policy import (
    DEFAULT_AUTOMATIC_CAP_SECONDS,
    DEFAULT_LEASE_SECONDS,
    DEFAULT_PLATEAU_ROUNDS,
    DEFAULT_SETTLE_TARGET_SECONDS,
    DEFAULT_SOFT_CHECKPOINT_SECONDS,
    GapWorkCandidate,
    LoopPolicyState,
    ProgressSnapshot,
    checkpoint_active_duration,
    claim_continue_child,
    claim_query_strategy,
    evaluate_time_gate,
    initial_loop_state,
    measurable_progress_gain,
    progress_from_coverages,
    projected_active_seconds,
    record_progress_round,
    request_cancel,
    request_generate_now,
    select_gap_work,
    settle_target_reached,
)


FIXTURE = Path(__file__).parent / "fixtures" / "deep_research_v5_loop_policy.json"


class VirtualClock:
    def __init__(self) -> None:
        self.monotonic = 100.0
        self.wall = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)

    @property
    def wall_iso(self) -> str:
        return self.wall.isoformat()

    def advance(self, seconds: float) -> None:
        self.monotonic += seconds
        self.wall += timedelta(seconds=seconds)


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _initial(clock: VirtualClock | None = None) -> LoopPolicyState:
    clock = clock or VirtualClock()
    return initial_loop_state("operation-1", wall_clock_anchor=clock.wall_iso)


def _coverage(
    dimension_id: str,
    status: str,
    *,
    first_party: bool = False,
    family: str = "",
) -> DimensionCoverage:
    winning = (f"evidence-{dimension_id}",) if status == "covered" else ()
    passages = winning if winning else (() if status == "not_applicable" else (f"passage-{dimension_id}",))
    return DimensionCoverage(
        dimension_id,
        status,  # type: ignore[arg-type]
        passages,
        winning,
        (family,) if family else (),
        first_party,
        0.8,
    )


def test_fixture_pins_loop_policy_defaults() -> None:
    defaults = _fixture()["defaults"]
    assert defaults == {
        "soft_checkpoint_seconds": DEFAULT_SOFT_CHECKPOINT_SECONDS,
        "lease_seconds": DEFAULT_LEASE_SECONDS,
        "automatic_cap_seconds": DEFAULT_AUTOMATIC_CAP_SECONDS,
        "plateau_rounds": DEFAULT_PLATEAU_ROUNDS,
        "settle_target_seconds": DEFAULT_SETTLE_TARGET_SECONDS,
    }


def test_fixture_pins_named_acceptance_scenarios() -> None:
    scenarios = _fixture()["scenarios"]
    assert {scenario["id"] for scenario in scenarios} == {
        "SC-LEASE-GAIN",
        "SC-LEASE-NO-GAIN",
        "SC-PLATEAU",
        "SC-CAP",
        "SC-NOW",
    }


def test_active_duration_projects_monotonic_segment_and_survives_restart() -> None:
    clock = VirtualClock()
    state = _initial(clock)
    segment_start = clock.monotonic
    clock.advance(125.0)

    assert projected_active_seconds(
        state,
        segment_started_monotonic=segment_start,
        now_monotonic=clock.monotonic,
    ) == 125.0
    checkpointed = checkpoint_active_duration(
        state,
        segment_started_monotonic=segment_start,
        now_monotonic=clock.monotonic,
        wall_clock_now=clock.wall_iso,
    )
    payload = checkpointed.to_json()
    assert payload["accumulated_active_seconds"] == 125.0
    assert payload["wall_clock_anchor"] == clock.wall_iso
    assert not any("monotonic" in key for key in payload)

    restored = LoopPolicyState.from_json(payload)
    assert projected_active_seconds(
        restored,
        segment_started_monotonic=5.0,
        now_monotonic=15.0,
    ) == 135.0


def test_active_duration_rejects_monotonic_clock_rollback() -> None:
    with pytest.raises(ContractValidationError, match="cannot run backwards"):
        projected_active_seconds(
            _initial(), segment_started_monotonic=10.0, now_monotonic=9.0
        )


@pytest.mark.parametrize(
    "progress",
    [
        ProgressSnapshot(0.5, 0, 0.0),
        ProgressSnapshot(0.0, 1, 0.0),
        ProgressSnapshot(0.0, 0, 0.1),
    ],
    ids=["coverage", "first-party", "quality"],
)
def test_sc_lease_renews_only_for_quantifiable_gain(progress: ProgressSnapshot) -> None:
    state = replace(_initial(), current_progress=progress)
    state, decision = evaluate_time_gate(state, active_seconds=300.0)

    assert decision.reason == "lease_renewed_measurable_gain"
    assert decision.renewed is True
    assert decision.start_new_upstream is True
    assert decision.checkpoint_required is True
    assert state.lease_count == 1
    assert state.lease_baseline == progress
    assert state.next_review_active_seconds == 420.0


def test_sc_lease_boundary_and_no_gain_settles() -> None:
    state, before = evaluate_time_gate(_initial(), active_seconds=299.999)
    assert before.start_new_upstream is True
    assert before.renewed is False
    assert state.control_mode == "running"

    state, at_checkpoint = evaluate_time_gate(state, active_seconds=300.0)
    assert at_checkpoint.reason == "lease_not_renewed_no_gain"
    assert at_checkpoint.start_new_upstream is False
    assert at_checkpoint.allow_inflight_completion is True
    assert at_checkpoint.checkpoint_required is True
    assert state.control_mode == "lease_no_gain_settling"
    assert state.settle_deadline_active_seconds == 330.0


def test_lease_uses_fresh_baseline_for_each_extension() -> None:
    first = ProgressSnapshot(0.5, 0, 0.0)
    state, _ = evaluate_time_gate(
        replace(_initial(), current_progress=first), active_seconds=300.0
    )
    second = ProgressSnapshot(0.5, 1, 0.0)
    state, decision = evaluate_time_gate(
        replace(state, current_progress=second), active_seconds=420.0
    )
    assert decision.renewed is True
    assert state.next_review_active_seconds == 540.0

    state, decision = evaluate_time_gate(state, active_seconds=540.0)
    assert decision.renewed is False
    assert state.control_mode == "lease_no_gain_settling"


def test_progress_regression_is_not_a_quantifiable_gain() -> None:
    before = ProgressSnapshot(2.0, 2, 0.9)
    after = ProgressSnapshot(1.0, 1, 0.4)
    gain = measurable_progress_gain(before, after)
    assert gain.measurable is False
    state = replace(_initial(), lease_baseline=before, current_progress=after)
    state, decision = evaluate_time_gate(state, active_seconds=300.0)
    assert decision.renewed is False
    assert state.control_mode == "lease_no_gain_settling"


def test_sc_plateau_stops_after_two_consecutive_no_gain_rounds() -> None:
    state = _initial()
    state = record_progress_round(state, state.current_progress, active_seconds=40.0)
    assert state.control_mode == "running"
    assert state.no_gain_rounds == 1

    state = record_progress_round(state, state.current_progress, active_seconds=55.0)
    assert state.control_mode == "plateau_settling"
    assert state.no_gain_rounds == 2
    assert state.prohibit_new_upstream is True
    assert state.settle_deadline_active_seconds == 85.0
    state, decision = evaluate_time_gate(state, active_seconds=56.0)
    assert decision.start_new_upstream is False
    assert decision.allow_inflight_completion is True
    assert decision.checkpoint_required is True


def test_measurable_round_resets_plateau_counter() -> None:
    state = record_progress_round(
        _initial(), ProgressSnapshot(0.0, 0, 0.0), active_seconds=10.0
    )
    state = record_progress_round(
        state, ProgressSnapshot(0.5, 0, 0.0), active_seconds=20.0
    )
    assert state.control_mode == "running"
    assert state.no_gain_rounds == 0


def test_sc_cap_prohibits_new_upstream_but_allows_bounded_atomic_completion() -> None:
    state = replace(_initial(), next_review_active_seconds=900.0)
    state, before = evaluate_time_gate(state, active_seconds=899.999)
    assert before.start_new_upstream is True

    state, at_cap = evaluate_time_gate(state, active_seconds=900.0)
    assert at_cap.reason == "automatic_cap"
    assert at_cap.start_new_upstream is False
    assert at_cap.allow_inflight_completion is True
    assert at_cap.checkpoint_required is True
    assert state.control_mode == "automatic_cap_settling"
    assert state.settle_deadline_active_seconds == 930.0
    assert settle_target_reached(state, active_seconds=929.999) is False
    assert settle_target_reached(state, active_seconds=930.0) is True


def test_cap_settle_deadline_is_anchored_to_cap_even_when_observed_late() -> None:
    state = replace(_initial(), next_review_active_seconds=900.0)
    state, decision = evaluate_time_gate(state, active_seconds=920.0)
    assert decision.reason == "automatic_cap"
    assert state.settle_deadline_active_seconds == 930.0


def test_sc_now_enters_idempotent_30_second_convergence() -> None:
    state = request_generate_now(
        _initial(), action_id="action-now", idempotency_key="key-now", active_seconds=125.0
    )
    assert state.control_mode == "generate_now_settling"
    assert state.settle_deadline_active_seconds == 155.0
    state, decision = evaluate_time_gate(state, active_seconds=126.0)
    assert decision.reason == "generate_now_settling"
    assert decision.start_new_upstream is False
    assert decision.allow_inflight_completion is True
    assert request_generate_now(
        state, action_id="action-now", idempotency_key="key-now", active_seconds=130.0
    ) is state
    with pytest.raises(ContractValidationError, match="already exists"):
        request_generate_now(
            state, action_id="action-other", idempotency_key="key-other", active_seconds=130.0
        )


def test_generate_now_fence_has_priority_over_lease_gain() -> None:
    gained = replace(_initial(), current_progress=ProgressSnapshot(1.0, 1, 0.5))
    state = request_generate_now(
        gained, action_id="action-now", idempotency_key="key-now", active_seconds=300.0
    )
    state, decision = evaluate_time_gate(state, active_seconds=300.0)
    assert decision.reason == "generate_now_settling"
    assert decision.renewed is False
    assert state.lease_count == 0


def test_cancel_disables_even_inflight_completion_and_is_idempotent() -> None:
    state = request_generate_now(
        _initial(), action_id="action-now", idempotency_key="key-now", active_seconds=125.0
    )
    state = request_cancel(
        state, action_id="action-cancel", idempotency_key="key-cancel", active_seconds=126.0
    )
    state, decision = evaluate_time_gate(state, active_seconds=126.0)
    assert state.control_mode == "cancelled"
    assert decision.start_new_upstream is False
    assert decision.allow_inflight_completion is False
    assert decision.checkpoint_required is True
    assert request_cancel(
        state, action_id="action-cancel", idempotency_key="key-cancel", active_seconds=200.0
    ) is state


def test_gap_work_only_targets_partial_or_uncovered_and_deduplicates() -> None:
    coverages = (
        _coverage("covered", "covered", family="family-covered"),
        _coverage("partial", "partially_covered"),
        _coverage("missing", "uncovered"),
        _coverage("na", "not_applicable"),
    )
    candidates = (
        GapWorkCandidate("covered-work", "covered", "fp-covered"),
        GapWorkCandidate("partial-1", "partial", "FP-1", "family-1"),
        GapWorkCandidate("partial-duplicate-fp", "partial", "fp-1", "family-2"),
        GapWorkCandidate("missing-duplicate-family", "missing", "fp-2", "FAMILY-1"),
        GapWorkCandidate("missing-executed", "missing", "fp-old"),
        GapWorkCandidate("missing-new", "missing", "fp-3", "family-3"),
        GapWorkCandidate("na-work", "na", "fp-na"),
    )
    selected = select_gap_work(
        coverages, candidates, executed_fingerprints=("FP-OLD",)
    )
    assert [item.item_id for item in selected] == ["partial-1", "missing-new"]


def test_progress_snapshot_counts_only_core_coverage_and_first_party() -> None:
    snapshot = progress_from_coverages(
        (
            _coverage("core-covered", "covered", first_party=True),
            _coverage("core-partial", "partially_covered"),
            _coverage("non-core", "covered", first_party=True),
        ),
        core_dimension_ids=("core-covered", "core-partial"),
        quality_score=0.7,
    )
    assert snapshot == ProgressSnapshot(1.5, 1, 0.7)


def test_query_strategy_is_claimed_once_per_durable_operation() -> None:
    state, accepted = claim_query_strategy(_initial(), operation_id="operation-1")
    assert accepted is True
    restored = LoopPolicyState.from_json(state.to_json())
    restored, accepted = claim_query_strategy(restored, operation_id="operation-1")
    assert accepted is False
    with pytest.raises(ContractValidationError, match="state operation_id"):
        claim_query_strategy(restored, operation_id="operation-2")

    child, accepted = claim_query_strategy(
        initial_loop_state(
            "operation-2", wall_clock_anchor=VirtualClock().wall_iso
        ),
        operation_id="operation-2",
    )
    assert accepted is True
    assert child.query_strategy_operations == ("operation-2",)


def test_continue_creates_at_most_one_distinct_child_operation() -> None:
    claim = claim_continue_child(
        _initial(), parent_operation_id="operation-1", child_operation_id="child-1"
    )
    assert claim.accepted is True
    restored = LoopPolicyState.from_json(claim.state.to_json())
    duplicate = claim_continue_child(
        restored, parent_operation_id="operation-1", child_operation_id="child-2"
    )
    assert duplicate.accepted is False
    assert duplicate.child_operation_id == "child-1"
    with pytest.raises(ContractValidationError, match="state operation_id"):
        claim_continue_child(
            restored, parent_operation_id="parent-2", child_operation_id="child-1"
        )
    with pytest.raises(ContractValidationError, match="state operation_id"):
        claim_continue_child(
            restored, parent_operation_id="same", child_operation_id="same"
        )


def test_restored_state_rejects_foreign_operation_claims() -> None:
    payload = _initial().to_json()
    payload["query_strategy_operations"] = ["foreign-operation"]
    with pytest.raises(ContractValidationError, match="belong to this operation"):
        LoopPolicyState.from_json(payload)

    payload = _initial().to_json()
    payload["continued_children"] = [["foreign-operation", "child"]]
    with pytest.raises(ContractValidationError, match="belong to this operation"):
        LoopPolicyState.from_json(payload)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update(extra="field"), "invalid loop policy state fields"),
        (lambda value: value.update(prohibit_new_upstream="false"), "must be boolean"),
        (lambda value: value.update(lease_count="0"), "must be an integer"),
        (
            lambda value: value.update(settle_target_seconds=0),
            "settle_target_seconds must be positive",
        ),
        (
            lambda value: value["current_progress"].update(first_party_satisfied=True),
            "must be an integer",
        ),
    ],
)
def test_loop_state_json_is_strict(mutate: object, message: str) -> None:
    payload = _initial().to_json()
    mutate(payload)  # type: ignore[operator]
    with pytest.raises(ContractValidationError, match=message):
        LoopPolicyState.from_json(payload)

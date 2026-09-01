"""Pure duration, lease, plateau, control, and gap-loop policy for v5."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal

from .deep_research_v5_contracts import ContractValidationError, DimensionCoverage

DEFAULT_SOFT_CHECKPOINT_SECONDS = 300.0
DEFAULT_LEASE_SECONDS = 120.0
DEFAULT_AUTOMATIC_CAP_SECONDS = 900.0
DEFAULT_PLATEAU_ROUNDS = 2
DEFAULT_SETTLE_TARGET_SECONDS = 30.0

ControlMode = Literal[
    "running",
    "generate_now_settling",
    "cancelled",
    "plateau_settling",
    "lease_no_gain_settling",
    "automatic_cap_settling",
]


def _number(value: object, name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractValidationError(f"{name} must be numeric")
    result = float(value)
    if result < minimum:
        raise ContractValidationError(f"{name} must be >= {minimum}")
    return result


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractValidationError(f"{name} must be an integer")
    if value < minimum:
        raise ContractValidationError(f"{name} must be >= {minimum}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ContractValidationError(f"{name} must be boolean")
    return value


def _text(value: object, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{name} is required")
    return value


def _wall_clock(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError("wall_clock_anchor is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractValidationError("wall_clock_anchor must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ContractValidationError("wall_clock_anchor must include timezone")
    return value


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    coverage_points: float
    first_party_satisfied: int
    quality_score: float

    def __post_init__(self) -> None:
        _number(self.coverage_points, "coverage_points")
        if (
            isinstance(self.first_party_satisfied, bool)
            or not isinstance(self.first_party_satisfied, int)
            or self.first_party_satisfied < 0
        ):
            raise ContractValidationError(
                "first_party_satisfied must be a non-negative integer"
            )
        _number(self.quality_score, "quality_score")

    def to_json(self) -> dict[str, float | int]:
        return {
            "coverage_points": self.coverage_points,
            "first_party_satisfied": self.first_party_satisfied,
            "quality_score": self.quality_score,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ProgressSnapshot:
        expected = {"coverage_points", "first_party_satisfied", "quality_score"}
        if set(value) != expected:
            raise ContractValidationError("invalid progress snapshot fields")
        return cls(
            coverage_points=_number(value["coverage_points"], "coverage_points"),
            first_party_satisfied=_integer(
                value["first_party_satisfied"], "first_party_satisfied"
            ),
            quality_score=_number(value["quality_score"], "quality_score"),
        )


@dataclass(frozen=True, slots=True)
class ProgressGain:
    coverage_gain: float
    first_party_gain: int
    quality_gain: float

    @property
    def measurable(self) -> bool:
        return (
            self.coverage_gain > 0
            or self.first_party_gain > 0
            or self.quality_gain > 0
        )


@dataclass(frozen=True, slots=True)
class LoopPolicyState:
    operation_id: str
    wall_clock_anchor: str
    accumulated_active_seconds: float
    soft_checkpoint_seconds: float
    lease_seconds: float
    automatic_cap_seconds: float
    plateau_round_limit: int
    settle_target_seconds: float
    next_review_active_seconds: float
    lease_count: int
    no_gain_rounds: int
    lease_baseline: ProgressSnapshot
    current_progress: ProgressSnapshot
    control_mode: ControlMode = "running"
    prohibit_new_upstream: bool = False
    settle_deadline_active_seconds: float | None = None
    control_action_id: str | None = None
    control_idempotency_key: str | None = None
    query_strategy_operations: tuple[str, ...] = ()
    continued_children: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _text(self.operation_id, "operation_id")
        _wall_clock(self.wall_clock_anchor)
        for name in (
            "accumulated_active_seconds",
            "soft_checkpoint_seconds",
            "lease_seconds",
            "automatic_cap_seconds",
            "settle_target_seconds",
            "next_review_active_seconds",
        ):
            _number(getattr(self, name), name)
        if not 0 < self.soft_checkpoint_seconds <= self.automatic_cap_seconds:
            raise ContractValidationError("invalid soft checkpoint")
        if not 0 < self.lease_seconds <= self.automatic_cap_seconds:
            raise ContractValidationError("invalid lease")
        if self.settle_target_seconds <= 0:
            raise ContractValidationError("settle_target_seconds must be positive")
        if self.next_review_active_seconds > self.automatic_cap_seconds:
            raise ContractValidationError("next review exceeds automatic cap")
        for name in ("plateau_round_limit", "lease_count", "no_gain_rounds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ContractValidationError(f"{name} must be a non-negative integer")
        if self.plateau_round_limit < 1:
            raise ContractValidationError("plateau_round_limit must be positive")
        allowed_modes = {
            "running", "generate_now_settling", "cancelled", "plateau_settling",
            "lease_no_gain_settling", "automatic_cap_settling",
        }
        if self.control_mode not in allowed_modes:
            raise ContractValidationError("invalid control mode")
        if self.control_mode == "running" and self.prohibit_new_upstream:
            raise ContractValidationError("running state cannot prohibit upstream")
        if self.control_mode != "running" and not self.prohibit_new_upstream:
            raise ContractValidationError("settling state must prohibit upstream")
        if self.settle_deadline_active_seconds is not None:
            _number(self.settle_deadline_active_seconds, "settle_deadline_active_seconds")
        if self.control_mode == "running" and self.settle_deadline_active_seconds is not None:
            raise ContractValidationError("running state cannot have a settle deadline")
        if self.control_mode != "running" and self.settle_deadline_active_seconds is None:
            raise ContractValidationError("settling state requires a settle deadline")
        if (self.control_action_id is None) != (self.control_idempotency_key is None):
            raise ContractValidationError("control identity must be complete")
        _text(self.control_action_id, "control_action_id", optional=True)
        _text(self.control_idempotency_key, "control_idempotency_key", optional=True)
        if self.control_mode in {"generate_now_settling", "cancelled"} and self.control_action_id is None:
            raise ContractValidationError("explicit control mode requires control identity")
        if self.control_mode not in {"generate_now_settling", "cancelled"} and self.control_action_id is not None:
            raise ContractValidationError("implicit control mode cannot own control identity")
        if not all(isinstance(item, str) and item for item in self.query_strategy_operations):
            raise ContractValidationError("invalid query strategy operation")
        if len(set(self.query_strategy_operations)) != len(
            self.query_strategy_operations
        ):
            raise ContractValidationError("query strategy operations must be unique")
        if any(
            operation_id != self.operation_id
            for operation_id in self.query_strategy_operations
        ) or len(self.query_strategy_operations) > 1:
            raise ContractValidationError(
                "query strategy claim must belong to this operation"
            )
        if not all(
            isinstance(claim, tuple) and len(claim) == 2
            and all(isinstance(part, str) and part for part in claim)
            for claim in self.continued_children
        ):
            raise ContractValidationError("invalid continue child claim")
        parents = [parent for parent, _child in self.continued_children]
        children = [child for _parent, child in self.continued_children]
        if any(parent == child for parent, child in self.continued_children):
            raise ContractValidationError("continue child must be a new operation")
        if len(set(parents)) != len(parents) or len(set(children)) != len(children):
            raise ContractValidationError("continue child claims must be one-to-one")
        if any(parent != self.operation_id for parent in parents) or len(parents) > 1:
            raise ContractValidationError(
                "continue child claim must belong to this operation"
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "operation_id": self.operation_id,
            "wall_clock_anchor": self.wall_clock_anchor,
            "accumulated_active_seconds": self.accumulated_active_seconds,
            "soft_checkpoint_seconds": self.soft_checkpoint_seconds,
            "lease_seconds": self.lease_seconds,
            "automatic_cap_seconds": self.automatic_cap_seconds,
            "plateau_round_limit": self.plateau_round_limit,
            "settle_target_seconds": self.settle_target_seconds,
            "next_review_active_seconds": self.next_review_active_seconds,
            "lease_count": self.lease_count,
            "no_gain_rounds": self.no_gain_rounds,
            "lease_baseline": self.lease_baseline.to_json(),
            "current_progress": self.current_progress.to_json(),
            "control_mode": self.control_mode,
            "prohibit_new_upstream": self.prohibit_new_upstream,
            "settle_deadline_active_seconds": self.settle_deadline_active_seconds,
            "control_action_id": self.control_action_id,
            "control_idempotency_key": self.control_idempotency_key,
            "query_strategy_operations": list(self.query_strategy_operations),
            "continued_children": [list(value) for value in self.continued_children],
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> LoopPolicyState:
        expected = {
            "schema_version", "operation_id", "wall_clock_anchor",
            "accumulated_active_seconds", "soft_checkpoint_seconds", "lease_seconds",
            "automatic_cap_seconds", "plateau_round_limit", "settle_target_seconds",
            "next_review_active_seconds", "lease_count", "no_gain_rounds",
            "lease_baseline", "current_progress", "control_mode",
            "prohibit_new_upstream", "settle_deadline_active_seconds",
            "control_action_id", "control_idempotency_key",
            "query_strategy_operations", "continued_children",
        }
        if set(value) != expected or value.get("schema_version") != 1:
            raise ContractValidationError("invalid loop policy state fields")
        raw_children = value["continued_children"]
        if not isinstance(raw_children, list) or not all(
            isinstance(item, list)
            and len(item) == 2
            and all(isinstance(part, str) and part for part in item)
            for item in raw_children
        ):
            raise ContractValidationError("invalid continued_children")
        raw_query_ops = value["query_strategy_operations"]
        if not isinstance(raw_query_ops, list) or not all(
            isinstance(item, str) and item for item in raw_query_ops
        ):
            raise ContractValidationError("invalid query_strategy_operations")
        return cls(
            operation_id=str(_text(value["operation_id"], "operation_id")),
            wall_clock_anchor=str(_text(value["wall_clock_anchor"], "wall_clock_anchor")),
            accumulated_active_seconds=_number(
                value["accumulated_active_seconds"], "accumulated_active_seconds"
            ),
            soft_checkpoint_seconds=_number(
                value["soft_checkpoint_seconds"], "soft_checkpoint_seconds"
            ),
            lease_seconds=_number(value["lease_seconds"], "lease_seconds"),
            automatic_cap_seconds=_number(
                value["automatic_cap_seconds"], "automatic_cap_seconds"
            ),
            plateau_round_limit=_integer(
                value["plateau_round_limit"], "plateau_round_limit", minimum=1
            ),
            settle_target_seconds=_number(
                value["settle_target_seconds"], "settle_target_seconds"
            ),
            next_review_active_seconds=_number(
                value["next_review_active_seconds"], "next_review_active_seconds"
            ),
            lease_count=_integer(value["lease_count"], "lease_count"),
            no_gain_rounds=_integer(value["no_gain_rounds"], "no_gain_rounds"),
            lease_baseline=ProgressSnapshot.from_json(value["lease_baseline"]),
            current_progress=ProgressSnapshot.from_json(value["current_progress"]),
            control_mode=str(_text(value["control_mode"], "control_mode")),  # type: ignore[arg-type]
            prohibit_new_upstream=_boolean(
                value["prohibit_new_upstream"], "prohibit_new_upstream"
            ),
            settle_deadline_active_seconds=(
                None
                if value["settle_deadline_active_seconds"] is None
                else _number(
                    value["settle_deadline_active_seconds"],
                    "settle_deadline_active_seconds",
                )
            ),
            control_action_id=(
                str(_text(value["control_action_id"], "control_action_id"))
                if value["control_action_id"] is not None
                else None
            ),
            control_idempotency_key=(
                str(_text(value["control_idempotency_key"], "control_idempotency_key"))
                if value["control_idempotency_key"] is not None
                else None
            ),
            query_strategy_operations=tuple(raw_query_ops),
            continued_children=tuple((item[0], item[1]) for item in raw_children),
        )


@dataclass(frozen=True, slots=True)
class LoopDecision:
    reason: str
    start_new_upstream: bool
    allow_inflight_completion: bool
    checkpoint_required: bool
    renewed: bool
    next_review_active_seconds: float | None
    settle_deadline_active_seconds: float | None


@dataclass(frozen=True, slots=True)
class GapWorkCandidate:
    item_id: str
    dimension_id: str
    query_fingerprint: str
    source_family_id: str | None = None

    def __post_init__(self) -> None:
        if not self.item_id or not self.dimension_id or not self.query_fingerprint:
            raise ContractValidationError("gap work identity is required")


@dataclass(frozen=True, slots=True)
class ContinueClaim:
    state: LoopPolicyState
    accepted: bool
    child_operation_id: str


def initial_loop_state(
    operation_id: str,
    *,
    wall_clock_anchor: str,
    initial_progress: ProgressSnapshot | None = None,
    soft_checkpoint_seconds: float = DEFAULT_SOFT_CHECKPOINT_SECONDS,
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
    automatic_cap_seconds: float = DEFAULT_AUTOMATIC_CAP_SECONDS,
    plateau_round_limit: int = DEFAULT_PLATEAU_ROUNDS,
) -> LoopPolicyState:
    progress = initial_progress or ProgressSnapshot(0.0, 0, 0.0)
    return LoopPolicyState(
        operation_id=operation_id,
        wall_clock_anchor=wall_clock_anchor,
        accumulated_active_seconds=0.0,
        soft_checkpoint_seconds=soft_checkpoint_seconds,
        lease_seconds=lease_seconds,
        automatic_cap_seconds=automatic_cap_seconds,
        plateau_round_limit=plateau_round_limit,
        settle_target_seconds=DEFAULT_SETTLE_TARGET_SECONDS,
        next_review_active_seconds=soft_checkpoint_seconds,
        lease_count=0,
        no_gain_rounds=0,
        lease_baseline=progress,
        current_progress=progress,
    )


def projected_active_seconds(
    state: LoopPolicyState,
    *,
    segment_started_monotonic: float,
    now_monotonic: float,
) -> float:
    start = _number(segment_started_monotonic, "segment_started_monotonic")
    now = _number(now_monotonic, "now_monotonic")
    if now < start:
        raise ContractValidationError("monotonic time cannot run backwards")
    return state.accumulated_active_seconds + now - start


def checkpoint_active_duration(
    state: LoopPolicyState,
    *,
    segment_started_monotonic: float,
    now_monotonic: float,
    wall_clock_now: str,
) -> LoopPolicyState:
    active = projected_active_seconds(
        state,
        segment_started_monotonic=segment_started_monotonic,
        now_monotonic=now_monotonic,
    )
    return replace(
        state,
        accumulated_active_seconds=active,
        wall_clock_anchor=_wall_clock(wall_clock_now),
    )


def measurable_progress_gain(
    before: ProgressSnapshot,
    after: ProgressSnapshot,
) -> ProgressGain:
    return ProgressGain(
        coverage_gain=max(0.0, after.coverage_points - before.coverage_points),
        first_party_gain=max(
            0, after.first_party_satisfied - before.first_party_satisfied
        ),
        quality_gain=max(0.0, after.quality_score - before.quality_score),
    )


def progress_from_coverages(
    coverages: Sequence[DimensionCoverage],
    *,
    core_dimension_ids: Sequence[str],
    quality_score: float = 0.0,
) -> ProgressSnapshot:
    core = set(core_dimension_ids)
    values = [item for item in coverages if item.dimension_id in core]
    weights = {
        "covered": 1.0,
        "partially_covered": 0.5,
        "uncovered": 0.0,
        "not_applicable": 0.0,
    }
    return ProgressSnapshot(
        coverage_points=sum(weights[item.status] for item in values),
        first_party_satisfied=sum(1 for item in values if item.first_party_satisfied),
        quality_score=_number(quality_score, "quality_score"),
    )


def record_progress_round(
    state: LoopPolicyState,
    progress: ProgressSnapshot,
    *,
    active_seconds: float,
) -> LoopPolicyState:
    active = _number(active_seconds, "active_seconds")
    if state.prohibit_new_upstream:
        return state
    gain = measurable_progress_gain(state.current_progress, progress)
    no_gain = 0 if gain.measurable else state.no_gain_rounds + 1
    updated = replace(state, current_progress=progress, no_gain_rounds=no_gain)
    if no_gain < state.plateau_round_limit:
        return updated
    return replace(
        updated,
        control_mode="plateau_settling",
        prohibit_new_upstream=True,
        settle_deadline_active_seconds=active + state.settle_target_seconds,
    )


def _settling_decision(state: LoopPolicyState, reason: str) -> LoopDecision:
    return LoopDecision(
        reason=reason,
        start_new_upstream=False,
        allow_inflight_completion=state.control_mode != "cancelled",
        checkpoint_required=True,
        renewed=False,
        next_review_active_seconds=None,
        settle_deadline_active_seconds=state.settle_deadline_active_seconds,
    )


def evaluate_time_gate(
    state: LoopPolicyState,
    *,
    active_seconds: float,
) -> tuple[LoopPolicyState, LoopDecision]:
    active = _number(active_seconds, "active_seconds")
    if state.prohibit_new_upstream:
        return state, _settling_decision(state, state.control_mode)
    if active >= state.automatic_cap_seconds:
        updated = replace(
            state,
            control_mode="automatic_cap_settling",
            prohibit_new_upstream=True,
            settle_deadline_active_seconds=(
                state.automatic_cap_seconds + state.settle_target_seconds
            ),
        )
        return updated, _settling_decision(updated, "automatic_cap")
    if active < state.next_review_active_seconds:
        return state, LoopDecision(
            reason="before_soft_or_lease_checkpoint",
            start_new_upstream=True,
            allow_inflight_completion=True,
            checkpoint_required=False,
            renewed=False,
            next_review_active_seconds=state.next_review_active_seconds,
            settle_deadline_active_seconds=None,
        )
    gain = measurable_progress_gain(state.lease_baseline, state.current_progress)
    if not gain.measurable:
        updated = replace(
            state,
            control_mode="lease_no_gain_settling",
            prohibit_new_upstream=True,
            settle_deadline_active_seconds=active + state.settle_target_seconds,
        )
        return updated, _settling_decision(updated, "lease_not_renewed_no_gain")
    next_review = min(active + state.lease_seconds, state.automatic_cap_seconds)
    updated = replace(
        state,
        lease_count=state.lease_count + 1,
        lease_baseline=state.current_progress,
        next_review_active_seconds=next_review,
    )
    return updated, LoopDecision(
        reason="lease_renewed_measurable_gain",
        start_new_upstream=next_review > active,
        allow_inflight_completion=True,
        checkpoint_required=True,
        renewed=True,
        next_review_active_seconds=next_review,
        settle_deadline_active_seconds=None,
    )


def request_generate_now(
    state: LoopPolicyState,
    *,
    action_id: str,
    idempotency_key: str,
    active_seconds: float,
) -> LoopPolicyState:
    if not action_id or not idempotency_key:
        raise ContractValidationError("generate-now identity is required")
    active = _number(active_seconds, "active_seconds")
    if state.control_mode == "generate_now_settling":
        if (
            state.control_action_id == action_id
            and state.control_idempotency_key == idempotency_key
        ):
            return state
        raise ContractValidationError("generate-now decision already exists")
    if state.control_mode != "running":
        raise ContractValidationError("run is already settling")
    return replace(
        state,
        control_mode="generate_now_settling",
        prohibit_new_upstream=True,
        settle_deadline_active_seconds=active + state.settle_target_seconds,
        control_action_id=action_id,
        control_idempotency_key=idempotency_key,
    )


def request_cancel(
    state: LoopPolicyState,
    *,
    action_id: str,
    idempotency_key: str,
    active_seconds: float,
) -> LoopPolicyState:
    if not action_id or not idempotency_key:
        raise ContractValidationError("cancel identity is required")
    active = _number(active_seconds, "active_seconds")
    if state.control_mode == "cancelled":
        if (
            state.control_action_id == action_id
            and state.control_idempotency_key == idempotency_key
        ):
            return state
        raise ContractValidationError("cancel decision already exists")
    return replace(
        state,
        control_mode="cancelled",
        prohibit_new_upstream=True,
        settle_deadline_active_seconds=active,
        control_action_id=action_id,
        control_idempotency_key=idempotency_key,
    )


def settle_target_reached(state: LoopPolicyState, *, active_seconds: float) -> bool:
    active = _number(active_seconds, "active_seconds")
    return (
        state.settle_deadline_active_seconds is not None
        and active >= state.settle_deadline_active_seconds
    )


def select_gap_work(
    coverages: Sequence[DimensionCoverage],
    candidates: Sequence[GapWorkCandidate],
    *,
    executed_fingerprints: Sequence[str] = (),
    observed_source_family_ids: Sequence[str] = (),
) -> tuple[GapWorkCandidate, ...]:
    coverage_by_id: dict[str, DimensionCoverage] = {}
    for coverage in coverages:
        if coverage.dimension_id in coverage_by_id:
            raise ContractValidationError("duplicate dimension coverage")
        coverage_by_id[coverage.dimension_id] = coverage
    fingerprints = {value.casefold() for value in executed_fingerprints}
    source_families = {value.casefold() for value in observed_source_family_ids}
    selected: list[GapWorkCandidate] = []
    seen_items: set[str] = set()
    for candidate in candidates:
        coverage = coverage_by_id.get(candidate.dimension_id)
        if coverage is None or coverage.status not in {
            "uncovered",
            "partially_covered",
        }:
            continue
        fingerprint = candidate.query_fingerprint.casefold()
        family = (candidate.source_family_id or "").casefold()
        if fingerprint in fingerprints or fingerprint in seen_items:
            continue
        if family and family in source_families:
            continue
        fingerprints.add(fingerprint)
        seen_items.add(fingerprint)
        if family:
            source_families.add(family)
        selected.append(candidate)
    return tuple(selected)


def claim_query_strategy(
    state: LoopPolicyState,
    *,
    operation_id: str,
) -> tuple[LoopPolicyState, bool]:
    if not operation_id:
        raise ContractValidationError("operation_id is required")
    if operation_id != state.operation_id:
        raise ContractValidationError(
            "query strategy claim must match state operation_id"
        )
    if operation_id in state.query_strategy_operations:
        return state, False
    return replace(
        state,
        query_strategy_operations=(*state.query_strategy_operations, operation_id),
    ), True


def claim_continue_child(
    state: LoopPolicyState,
    *,
    parent_operation_id: str,
    child_operation_id: str,
) -> ContinueClaim:
    if not parent_operation_id or not child_operation_id:
        raise ContractValidationError("continue operation identities are required")
    if parent_operation_id != state.operation_id:
        raise ContractValidationError(
            "continue parent must match state operation_id"
        )
    if parent_operation_id == child_operation_id:
        raise ContractValidationError("continue child must be a new operation")
    existing = dict(state.continued_children)
    if parent_operation_id in existing:
        return ContinueClaim(state, False, existing[parent_operation_id])
    if child_operation_id in existing.values():
        raise ContractValidationError("child operation is already claimed")
    updated = replace(
        state,
        continued_children=(
            *state.continued_children,
            (parent_operation_id, child_operation_id),
        ),
    )
    return ContinueClaim(updated, True, child_operation_id)


__all__ = [
    "DEFAULT_AUTOMATIC_CAP_SECONDS",
    "DEFAULT_LEASE_SECONDS",
    "DEFAULT_PLATEAU_ROUNDS",
    "DEFAULT_SETTLE_TARGET_SECONDS",
    "DEFAULT_SOFT_CHECKPOINT_SECONDS",
    "ContinueClaim",
    "ControlMode",
    "GapWorkCandidate",
    "LoopDecision",
    "LoopPolicyState",
    "ProgressGain",
    "ProgressSnapshot",
    "checkpoint_active_duration",
    "claim_continue_child",
    "claim_query_strategy",
    "evaluate_time_gate",
    "initial_loop_state",
    "measurable_progress_gain",
    "progress_from_coverages",
    "projected_active_seconds",
    "record_progress_round",
    "request_cancel",
    "request_generate_now",
    "select_gap_work",
    "settle_target_reached",
]

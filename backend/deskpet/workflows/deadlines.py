"""Pure durable deadline state transitions for restart-safe workflows.

Only wall-clock anchors and integer budget values are serialized. Monotonic
epochs remain process-local in :class:`DeadlineLeaseV1` and are never emitted by
``DurableDeadlineV1.to_json``. V1 deliberately counts process downtime against
the deadline and fails closed when the wall clock rolls back beyond tolerance.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Literal, Mapping

from .contracts import JsonValue, canonical_json, validate_json_value


DeadlineStatus = Literal["open", "expired", "cancelled", "completed"]
DeadlineTerminalReason = Literal[
    "budget_exhausted",
    "wall_guard",
    "clock_rollback",
    "parent_expired",
    "cancelled",
    "completed",
]


class DeadlineValidationError(ValueError):
    """A durable deadline contract or transition failed closed."""

    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


def _integer(value: Any, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise DeadlineValidationError(
            "deadline_integer_invalid",
            f"must be an integer >= {minimum}",
            path=path,
        )
    return value


def _text(value: Any, path: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise DeadlineValidationError(
            "deadline_text_invalid",
            "must be a non-empty string",
            path=path,
        )
    return value.strip()


def _hex64(value: Any, path: str) -> str:
    text = _text(value, path)
    assert text is not None
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise DeadlineValidationError(
            "deadline_hash_invalid",
            "must be 64 lowercase hex characters",
            path=path,
        )
    return text


def _wall(value: datetime | str, path: str) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DeadlineValidationError(
                "deadline_timestamp_invalid",
                "must be ISO-8601",
                path=path,
            ) from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise DeadlineValidationError(
            "deadline_timestamp_invalid",
            "must be a datetime or ISO-8601 string",
            path=path,
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DeadlineValidationError(
            "deadline_timestamp_naive",
            "must include a timezone",
            path=path,
        )
    return parsed.astimezone(timezone.utc)


def _wall_text(value: datetime | str, path: str) -> str:
    return _wall(value, path).isoformat()


def _floor_milliseconds(delta: timedelta) -> int:
    return max(0, delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000)


def _ceil_milliseconds(delta: timedelta) -> int:
    microseconds = (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )
    return max(0, math.ceil(microseconds / 1_000))


def _exact_object(value: Mapping[str, Any], keys: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DeadlineValidationError(
            "deadline_object_invalid", f"{name} must be an object"
        )
    raw = dict(value)
    missing = sorted(keys - set(raw))
    unknown = sorted(set(raw) - keys)
    if missing or unknown:
        raise DeadlineValidationError(
            "deadline_keys_differ",
            f"{name} keys differ (missing={missing}, unknown={unknown})",
        )
    if raw.get("schema_version") != 1:
        raise DeadlineValidationError(
            "deadline_schema_unsupported", f"{name}.schema_version must be 1"
        )
    validate_json_value(raw)
    return raw


def deadline_id_for(
    *,
    owner_id: str,
    logical_scope: str,
    policy_hash: str,
    parent_deadline_id: str | None = None,
) -> str:
    """Derive a stable deadline identity without using wall or monotonic time."""

    owner = _text(owner_id, "$.owner_id")
    scope = _text(logical_scope, "$.logical_scope")
    policy = _hex64(policy_hash, "$.policy_hash")
    parent = _text(
        parent_deadline_id,
        "$.parent_deadline_id",
        optional=True,
    )
    identity: JsonValue = {
        "schema_version": 1,
        "owner_id": owner,
        "logical_scope": scope,
        "policy_hash": policy,
        "parent_deadline_id": parent,
    }
    return "ddl_" + hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DurableDeadlineV1:
    deadline_id: str
    parent_deadline_id: str | None
    logical_scope: str
    policy_hash: str
    budget_ms: int
    remaining_ms: int
    created_at: str
    last_observed_at: str
    wall_not_after: str
    offline_policy: str
    rollback_tolerance_ms: int
    revision: int
    status: DeadlineStatus
    terminal_reason: DeadlineTerminalReason | None
    terminal_at: str | None

    schema_version: ClassVar[int] = 1

    def __post_init__(self) -> None:
        for name in ("deadline_id", "logical_scope"):
            value = _text(getattr(self, name), f"$.{name}")
            assert value is not None
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "parent_deadline_id",
            _text(
                self.parent_deadline_id,
                "$.parent_deadline_id",
                optional=True,
            ),
        )
        object.__setattr__(self, "policy_hash", _hex64(self.policy_hash, "$.policy_hash"))
        budget = _integer(self.budget_ms, "$.budget_ms", minimum=1)
        remaining = _integer(self.remaining_ms, "$.remaining_ms")
        if remaining > budget:
            raise DeadlineValidationError(
                "deadline_budget_invalid",
                "remaining_ms must not exceed budget_ms",
                path="$.remaining_ms",
            )
        object.__setattr__(self, "budget_ms", budget)
        object.__setattr__(self, "remaining_ms", remaining)
        created_at = _wall_text(self.created_at, "$.created_at")
        last_observed_at = _wall_text(
            self.last_observed_at, "$.last_observed_at"
        )
        wall_not_after = _wall_text(self.wall_not_after, "$.wall_not_after")
        created = _wall(created_at, "$.created_at")
        observed = _wall(last_observed_at, "$.last_observed_at")
        wall_end = _wall(wall_not_after, "$.wall_not_after")
        if observed < created:
            raise DeadlineValidationError(
                "deadline_wall_order_invalid",
                "last_observed_at must not precede created_at",
                path="$.last_observed_at",
            )
        if wall_end < created:
            raise DeadlineValidationError(
                "deadline_wall_order_invalid",
                "wall_not_after must not precede created_at",
                path="$.wall_not_after",
            )
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "last_observed_at", last_observed_at)
        object.__setattr__(self, "wall_not_after", wall_not_after)
        if self.offline_policy != "count":
            raise DeadlineValidationError(
                "deadline_offline_policy_unsupported",
                "DurableDeadlineV1 only supports offline_policy=count",
                path="$.offline_policy",
            )
        object.__setattr__(
            self,
            "rollback_tolerance_ms",
            _integer(self.rollback_tolerance_ms, "$.rollback_tolerance_ms"),
        )
        object.__setattr__(self, "revision", _integer(self.revision, "$.revision"))
        if self.status not in {"open", "expired", "cancelled", "completed"}:
            raise DeadlineValidationError(
                "deadline_status_invalid",
                "status must be open, expired, cancelled, or completed",
                path="$.status",
            )
        allowed_reasons: dict[str, set[str | None]] = {
            "open": {None},
            "expired": {
                "budget_exhausted",
                "wall_guard",
                "clock_rollback",
                "parent_expired",
            },
            "cancelled": {"cancelled"},
            "completed": {"completed"},
        }
        if self.terminal_reason not in allowed_reasons[self.status]:
            raise DeadlineValidationError(
                "deadline_terminal_reason_invalid",
                "terminal_reason does not match status",
                path="$.terminal_reason",
            )
        if self.status == "open":
            if self.terminal_at is not None:
                raise DeadlineValidationError(
                    "deadline_terminal_at_invalid",
                    "open deadline must not have terminal_at",
                    path="$.terminal_at",
                )
        else:
            terminal_at = _wall_text(self.terminal_at, "$.terminal_at")
            if _wall(terminal_at, "$.terminal_at") < created:
                raise DeadlineValidationError(
                    "deadline_terminal_at_invalid",
                    "terminal_at must not precede created_at",
                    path="$.terminal_at",
                )
            object.__setattr__(self, "terminal_at", terminal_at)

    @classmethod
    def create(
        cls,
        *,
        owner_id: str,
        logical_scope: str,
        policy_hash: str,
        budget_ms: int,
        now_wall: datetime | str,
        parent_deadline_id: str | None = None,
        rollback_tolerance_ms: int = 2_000,
        wall_not_after: datetime | str | None = None,
    ) -> "DurableDeadlineV1":
        budget = _integer(budget_ms, "$.budget_ms", minimum=1)
        now = _wall(now_wall, "$.now_wall")
        natural_end = now + timedelta(milliseconds=budget)
        hard_end = (
            min(natural_end, _wall(wall_not_after, "$.wall_not_after"))
            if wall_not_after is not None
            else natural_end
        )
        if hard_end <= now:
            raise DeadlineValidationError(
                "deadline_wall_guard_exhausted",
                "wall_not_after must be later than now_wall",
                path="$.wall_not_after",
            )
        return cls(
            deadline_id=deadline_id_for(
                owner_id=owner_id,
                logical_scope=logical_scope,
                policy_hash=policy_hash,
                parent_deadline_id=parent_deadline_id,
            ),
            parent_deadline_id=parent_deadline_id,
            logical_scope=logical_scope,
            policy_hash=policy_hash,
            budget_ms=budget,
            remaining_ms=budget,
            created_at=now.isoformat(),
            last_observed_at=now.isoformat(),
            wall_not_after=hard_end.isoformat(),
            offline_policy="count",
            rollback_tolerance_ms=rollback_tolerance_ms,
            revision=0,
            status="open",
            terminal_reason=None,
            terminal_at=None,
        )

    def to_json(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "schema_version": 1,
            "deadline_id": self.deadline_id,
            "parent_deadline_id": self.parent_deadline_id,
            "logical_scope": self.logical_scope,
            "policy_hash": self.policy_hash,
            "budget_ms": self.budget_ms,
            "remaining_ms": self.remaining_ms,
            "created_at": self.created_at,
            "last_observed_at": self.last_observed_at,
            "wall_not_after": self.wall_not_after,
            "offline_policy": self.offline_policy,
            "rollback_tolerance_ms": self.rollback_tolerance_ms,
            "revision": self.revision,
            "status": self.status,
            "terminal_reason": self.terminal_reason,
            "terminal_at": self.terminal_at,
        }
        validate_json_value(payload)
        return payload

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "DurableDeadlineV1":
        keys = {
            "schema_version",
            "deadline_id",
            "parent_deadline_id",
            "logical_scope",
            "policy_hash",
            "budget_ms",
            "remaining_ms",
            "created_at",
            "last_observed_at",
            "wall_not_after",
            "offline_policy",
            "rollback_tolerance_ms",
            "revision",
            "status",
            "terminal_reason",
            "terminal_at",
        }
        raw = _exact_object(value, keys, cls.__name__)
        return cls(
            deadline_id=str(_text(raw["deadline_id"], "$.deadline_id")),
            parent_deadline_id=_text(
                raw["parent_deadline_id"], "$.parent_deadline_id", optional=True
            ),
            logical_scope=str(_text(raw["logical_scope"], "$.logical_scope")),
            policy_hash=_hex64(raw["policy_hash"], "$.policy_hash"),
            budget_ms=_integer(raw["budget_ms"], "$.budget_ms", minimum=1),
            remaining_ms=_integer(raw["remaining_ms"], "$.remaining_ms"),
            created_at=_wall_text(raw["created_at"], "$.created_at"),
            last_observed_at=_wall_text(
                raw["last_observed_at"], "$.last_observed_at"
            ),
            wall_not_after=_wall_text(raw["wall_not_after"], "$.wall_not_after"),
            offline_policy=str(_text(raw["offline_policy"], "$.offline_policy")),
            rollback_tolerance_ms=_integer(
                raw["rollback_tolerance_ms"], "$.rollback_tolerance_ms"
            ),
            revision=_integer(raw["revision"], "$.revision"),
            status=str(_text(raw["status"], "$.status")),  # type: ignore[arg-type]
            terminal_reason=_text(
                raw["terminal_reason"], "$.terminal_reason", optional=True
            ),  # type: ignore[arg-type]
            terminal_at=_text(raw["terminal_at"], "$.terminal_at", optional=True),
        )


@dataclass(frozen=True, slots=True)
class DeadlineLeaseV1:
    """Process-local monotonic lease; never serialize this object."""

    deadline_id: str
    revision: int
    started_monotonic_ns: int
    last_monotonic_ns: int
    deadline_monotonic_ns: int
    unaccounted_ns: int = 0

    def __post_init__(self) -> None:
        _text(self.deadline_id, "$.deadline_id")
        object.__setattr__(self, "revision", _integer(self.revision, "$.revision"))
        for name in (
            "started_monotonic_ns",
            "last_monotonic_ns",
            "deadline_monotonic_ns",
            "unaccounted_ns",
        ):
            object.__setattr__(self, name, _integer(getattr(self, name), f"$.{name}"))
        if not (
            self.started_monotonic_ns
            <= self.last_monotonic_ns
            <= self.deadline_monotonic_ns
        ):
            raise DeadlineValidationError(
                "deadline_monotonic_order_invalid",
                "monotonic lease values are not ordered",
            )
        if self.unaccounted_ns >= 1_000_000:
            raise DeadlineValidationError(
                "deadline_monotonic_remainder_invalid",
                "unaccounted_ns must be less than one millisecond",
                path="$.unaccounted_ns",
            )


@dataclass(frozen=True, slots=True)
class DeadlineTransitionV1:
    state: DurableDeadlineV1
    lease: DeadlineLeaseV1 | None
    charged_ms: int
    transition: str

    @property
    def allows_upstream(self) -> bool:
        return self.state.status == "open" and self.lease is not None


def _terminal(
    state: DurableDeadlineV1,
    *,
    reason: DeadlineTerminalReason,
    terminal_at: datetime,
    remaining_ms: int | None = None,
    last_observed_at: datetime | None = None,
) -> DurableDeadlineV1:
    status: DeadlineStatus
    if reason == "completed":
        status = "completed"
    elif reason == "cancelled":
        status = "cancelled"
    else:
        status = "expired"
    return replace(
        state,
        remaining_ms=(state.remaining_ms if remaining_ms is None else remaining_ms),
        last_observed_at=(
            state.last_observed_at
            if last_observed_at is None
            else last_observed_at.isoformat()
        ),
        revision=state.revision + 1,
        status=status,
        terminal_reason=reason,
        terminal_at=terminal_at.isoformat(),
    )


def _wall_transition(
    state: DurableDeadlineV1,
    now_wall: datetime | str,
) -> tuple[datetime, int, DurableDeadlineV1 | None]:
    """Return effective wall, offline charge, or a rollback terminal state."""

    now = _wall(now_wall, "$.now_wall")
    observed = _wall(state.last_observed_at, "$.last_observed_at")
    if now < observed:
        rollback_ms = _ceil_milliseconds(observed - now)
        if rollback_ms > state.rollback_tolerance_ms:
            return (
                observed,
                0,
                _terminal(
                    state,
                    reason="clock_rollback",
                    terminal_at=observed,
                    last_observed_at=observed,
                ),
            )
        return observed, 0, None
    return now, min(state.remaining_ms, _floor_milliseconds(now - observed)), None


def _new_lease(
    state: DurableDeadlineV1,
    *,
    now_wall: datetime,
    now_monotonic_ns: int,
) -> DeadlineLeaseV1:
    monotonic_now = _integer(now_monotonic_ns, "$.now_monotonic_ns")
    wall_remaining_ms = _floor_milliseconds(
        _wall(state.wall_not_after, "$.wall_not_after") - now_wall
    )
    allowed_ms = min(state.remaining_ms, wall_remaining_ms)
    return DeadlineLeaseV1(
        deadline_id=state.deadline_id,
        revision=state.revision,
        started_monotonic_ns=monotonic_now,
        last_monotonic_ns=monotonic_now,
        deadline_monotonic_ns=monotonic_now + allowed_ms * 1_000_000,
    )


def resume_deadline(
    state: DurableDeadlineV1,
    *,
    now_wall: datetime | str,
    now_monotonic_ns: int,
) -> DeadlineTransitionV1:
    """Recover a deadline, charging all time since the last durable observation."""

    if state.status != "open":
        return DeadlineTransitionV1(state, None, 0, "already_terminal")
    effective_wall, offline_ms, rollback_terminal = _wall_transition(state, now_wall)
    if rollback_terminal is not None:
        return DeadlineTransitionV1(
            rollback_terminal, None, 0, "clock_rollback"
        )
    remaining = max(0, state.remaining_ms - offline_ms)
    wall_end = _wall(state.wall_not_after, "$.wall_not_after")
    if effective_wall >= wall_end:
        terminal = _terminal(
            state,
            reason="wall_guard",
            terminal_at=effective_wall,
            remaining_ms=remaining,
            last_observed_at=effective_wall,
        )
        return DeadlineTransitionV1(terminal, None, offline_ms, "wall_guard")
    if remaining == 0:
        terminal = _terminal(
            state,
            reason="budget_exhausted",
            terminal_at=effective_wall,
            remaining_ms=0,
            last_observed_at=effective_wall,
        )
        return DeadlineTransitionV1(
            terminal, None, offline_ms, "budget_exhausted"
        )
    resumed = replace(
        state,
        remaining_ms=remaining,
        last_observed_at=effective_wall.isoformat(),
        revision=state.revision + 1,
    )
    return DeadlineTransitionV1(
        resumed,
        _new_lease(
            resumed,
            now_wall=effective_wall,
            now_monotonic_ns=now_monotonic_ns,
        ),
        offline_ms,
        "resumed",
    )


def checkpoint_deadline(
    state: DurableDeadlineV1,
    lease: DeadlineLeaseV1,
    *,
    now_wall: datetime | str,
    now_monotonic_ns: int,
) -> DeadlineTransitionV1:
    """Persist elapsed active time without double-counting wall time."""

    if state.status != "open":
        return DeadlineTransitionV1(state, None, 0, "already_terminal")
    if lease.deadline_id != state.deadline_id or lease.revision != state.revision:
        raise DeadlineValidationError(
            "deadline_lease_stale",
            "lease identity/revision does not match durable state",
        )
    monotonic_now = _integer(now_monotonic_ns, "$.now_monotonic_ns")
    if monotonic_now < lease.last_monotonic_ns:
        raise DeadlineValidationError(
            "deadline_monotonic_rollback",
            "process-local monotonic clock moved backwards",
            path="$.now_monotonic_ns",
        )
    effective_wall, _, rollback_terminal = _wall_transition(state, now_wall)
    if rollback_terminal is not None:
        return DeadlineTransitionV1(
            rollback_terminal, None, 0, "clock_rollback"
        )
    elapsed_ns = (
        monotonic_now - lease.last_monotonic_ns + lease.unaccounted_ns
    )
    active_ms, remainder_ns = divmod(elapsed_ns, 1_000_000)
    charged_ms = min(state.remaining_ms, active_ms)
    remaining = max(0, state.remaining_ms - charged_ms)
    wall_end = _wall(state.wall_not_after, "$.wall_not_after")
    if effective_wall >= wall_end:
        terminal = _terminal(
            state,
            reason="wall_guard",
            terminal_at=effective_wall,
            remaining_ms=remaining,
            last_observed_at=effective_wall,
        )
        return DeadlineTransitionV1(terminal, None, charged_ms, "wall_guard")
    if remaining == 0 or monotonic_now >= lease.deadline_monotonic_ns:
        terminal = _terminal(
            state,
            reason="budget_exhausted",
            terminal_at=effective_wall,
            remaining_ms=0,
            last_observed_at=effective_wall,
        )
        return DeadlineTransitionV1(
            terminal, None, charged_ms, "budget_exhausted"
        )
    checkpointed = replace(
        state,
        remaining_ms=remaining,
        last_observed_at=effective_wall.isoformat(),
        revision=state.revision + 1,
    )
    next_lease = DeadlineLeaseV1(
        deadline_id=lease.deadline_id,
        revision=checkpointed.revision,
        started_monotonic_ns=lease.started_monotonic_ns,
        last_monotonic_ns=monotonic_now,
        deadline_monotonic_ns=lease.deadline_monotonic_ns,
        unaccounted_ns=remainder_ns,
    )
    return DeadlineTransitionV1(
        checkpointed, next_lease, charged_ms, "checkpointed"
    )


def create_child_deadline(
    parent: DurableDeadlineV1,
    *,
    logical_key: str,
    logical_scope: str,
    budget_ms: int,
    now_wall: datetime | str,
    policy_hash: str | None = None,
) -> DurableDeadlineV1:
    """Create a child deadline bounded by the parent's remaining wall/budget."""

    if parent.status != "open":
        raise DeadlineValidationError(
            "deadline_parent_terminal",
            "cannot create a child from a terminal parent",
        )
    now = _wall(now_wall, "$.now_wall")
    observed = _wall(parent.last_observed_at, "$.last_observed_at")
    if now < observed and _ceil_milliseconds(observed - now) > parent.rollback_tolerance_ms:
        raise DeadlineValidationError(
            "deadline_clock_rollback",
            "cannot create child after wall-clock rollback",
            path="$.now_wall",
        )
    effective_now = max(now, observed)
    wall_remaining = _floor_milliseconds(
        _wall(parent.wall_not_after, "$.wall_not_after") - effective_now
    )
    child_budget = min(
        _integer(budget_ms, "$.budget_ms", minimum=1),
        parent.remaining_ms,
        wall_remaining,
    )
    if child_budget <= 0:
        raise DeadlineValidationError(
            "deadline_parent_exhausted",
            "parent has no remaining budget for a child",
        )
    child_policy = parent.policy_hash if policy_hash is None else policy_hash
    return DurableDeadlineV1.create(
        owner_id=f"{parent.deadline_id}:{_text(logical_key, '$.logical_key')}",
        logical_scope=logical_scope,
        policy_hash=child_policy,
        budget_ms=child_budget,
        now_wall=effective_now,
        parent_deadline_id=parent.deadline_id,
        rollback_tolerance_ms=parent.rollback_tolerance_ms,
        wall_not_after=parent.wall_not_after,
    )


def lease_remaining_ms(lease: DeadlineLeaseV1, *, now_monotonic_ns: int) -> int:
    now = _integer(now_monotonic_ns, "$.now_monotonic_ns")
    if now >= lease.deadline_monotonic_ns:
        return 0
    return (lease.deadline_monotonic_ns - now) // 1_000_000


def remaining_timeout_seconds(
    lease: DeadlineLeaseV1,
    *,
    now_monotonic_ns: int,
    stage_cap_ms: int | None = None,
) -> float:
    remaining = lease_remaining_ms(lease, now_monotonic_ns=now_monotonic_ns)
    if stage_cap_ms is not None:
        remaining = min(
            remaining,
            _integer(stage_cap_ms, "$.stage_cap_ms", minimum=1),
        )
    return remaining / 1_000.0


__all__ = [
    "DeadlineLeaseV1",
    "DeadlineStatus",
    "DeadlineTerminalReason",
    "DeadlineTransitionV1",
    "DeadlineValidationError",
    "DurableDeadlineV1",
    "checkpoint_deadline",
    "create_child_deadline",
    "deadline_id_for",
    "lease_remaining_ms",
    "remaining_timeout_seconds",
    "resume_deadline",
]

# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Backpressure (§18.5, theory 11-9/11-11, ORCH-BUILD §8.2, plan D6-2).

"Budget = how much the whole run may spend; Backpressure = how fast it may go right
now" (theory 11-10).  This module is the single registry of the six caps §18.5 asks
for — running Agents, waiting work, results waiting for verification, Task DAG depth,
Attempts per Task, sub-task proposals per Agent — and the state machine that turns an
observation into a *raised* / *normal* signal.

The watermarks are this build's convention (plan §6.1, no verbatim source): a
dimension is raised when its observation reaches the cap (``high``) and is only
cleared again once the observation has fallen to ``low = floor(high × ratio)`` —
between the two the previous level is kept, which is the hysteresis ORCH-BUILD asks
for ("恢复有滞回").
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

BACKPRESSURE_VERSION = "backpressure-v1"
NORMAL = "NORMAL"
RAISED = "RAISED"
STATE_KEY = "backpressure"
# the three dimensions this module observes itself; the other three §18.5 caps are
# enforced at the graph / attempt level (step 5 knobs) and only *registered* here
OBSERVED_DIMENSIONS = ("running_attempts", "pending_dispatch", "pending_verifications")


@dataclass(frozen=True, slots=True)
class BackpressureLimits:
    """§18.5's six caps in one place (plan D6-2)."""

    max_running_attempts: int = 8
    max_pending_dispatch: int = 8
    max_pending_verifications: int = 4
    max_attempts_per_task: int | None = (
        None  # per Task budget.max_attempts governs; None = no global cap
    )
    low_watermark_ratio: float = 0.5

    def __post_init__(self) -> None:
        for name in ("max_running_attempts", "max_pending_dispatch", "max_pending_verifications"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")
        if not 0.0 <= self.low_watermark_ratio < 1.0:
            raise ValueError("low_watermark_ratio must be in [0, 1)")

    def high(self, dimension: str) -> int:
        return int(
            {
                "running_attempts": self.max_running_attempts,
                "pending_dispatch": self.max_pending_dispatch,
                "pending_verifications": self.max_pending_verifications,
            }[dimension]
        )

    def low(self, dimension: str) -> int:
        return int(self.high(dimension) * self.low_watermark_ratio)

    def to_json(self) -> dict[str, Any]:
        return {
            "max_running_attempts": self.max_running_attempts,
            "max_pending_dispatch": self.max_pending_dispatch,
            "max_pending_verifications": self.max_pending_verifications,
            "max_attempts_per_task": self.max_attempts_per_task,
            "low_watermark_ratio": self.low_watermark_ratio,
            "version": BACKPRESSURE_VERSION,
        }


@dataclass(frozen=True, slots=True)
class Observation:
    """What the queues look like right now (theory 12-1: running / waiting / to verify)."""

    running_attempts: int
    pending_dispatch: int
    pending_verifications: int
    observed_at: float

    def value(self, dimension: str) -> int:
        return int(getattr(self, dimension))

    def to_json(self) -> dict[str, Any]:
        return {
            "running_attempts": self.running_attempts,
            "pending_dispatch": self.pending_dispatch,
            "pending_verifications": self.pending_verifications,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True, slots=True)
class Transition:
    dimension: str
    to_level: str  # RAISED | NORMAL
    observed: int
    high: int
    low: int

    @property
    def event_type(self) -> str:
        return "BackpressureRaised" if self.to_level == RAISED else "BackpressureCleared"

    def to_json(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "level": self.to_level,
            "observed": self.observed,
            "high": self.high,
            "low": self.low,
            "version": BACKPRESSURE_VERSION,
        }


@dataclass(frozen=True, slots=True)
class BackpressureState:
    """Durable signal: which dimensions are raised, since when, and the last observation."""

    level: str = NORMAL
    raised: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    since: float | None = None
    observation: Mapping[str, Any] | None = None
    changes: int = 0
    version: str = BACKPRESSURE_VERSION

    @property
    def is_raised(self) -> bool:
        return self.level == RAISED

    def to_json(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "raised": {k: dict(v) for k, v in self.raised.items()},
            "since": self.since,
            "observation": None if self.observation is None else dict(self.observation),
            "changes": self.changes,
            "version": self.version,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any] | None) -> BackpressureState:
        if not value:
            return cls()
        return cls(
            level=str(value.get("level", NORMAL)),
            raised={str(k): dict(v) for k, v in dict(value.get("raised") or {}).items()},
            since=value.get("since"),
            observation=value.get("observation"),
            changes=int(value.get("changes", 0)),
            version=str(value.get("version", BACKPRESSURE_VERSION)),
        )


#: 推后第 3 批 H12：两种积压应对只看这一维（待审结果数）。
BACKLOG_DIMENSION = "pending_verifications"
#: 调度器状态里存当前应对的键。
BACKLOG_KEY = "backlog_response"


@dataclass(frozen=True, slots=True)
class BacklogResponse:
    """§18.5"提高 Verifier 资源""禁止新任务继续分裂"在这一轮的取值（推后第 3 批 H12）。

    ``verifier_workers``：这一轮最多同时跑几个审阅；``decomposition_paused``：已有计划的任务
    先不开新的规划轮。``reason``：``normal``（没积压）/ ``raised``（积压中）/ ``pause_lapsed``
    （积压仍在，但暂停已到时限，规划轮照开）。``since``：这次积压从何时起。"""

    base_verifier_workers: int
    verifier_workers: int
    verifier_ceiling: int
    decomposition_paused: bool
    reason: str
    since: float | None

    def to_json(self) -> dict[str, Any]:
        return {
            "base_verifier_workers": self.base_verifier_workers,
            "verifier_workers": self.verifier_workers,
            "verifier_ceiling": self.verifier_ceiling,
            "decomposition_paused": self.decomposition_paused,
            "reason": self.reason,
            "since": self.since,
            "dimension": BACKLOG_DIMENSION,
            "version": BACKPRESSURE_VERSION,
        }


def backlog_response(
    state: BackpressureState,
    *,
    verifier_workers: int,
    verifier_ceiling: int,
    now: float,
    pause_seconds: float,
) -> BacklogResponse:
    """纯函数：待审结果维升起 → 审阅并发取上限、暂停新拆分（到时限自动解除）；回落 → 都恢复。

    只看计数的水位与时刻，不看任何内容。上限由配置给出，不会因积压更大而再升。"""

    raised = state.raised.get(BACKLOG_DIMENSION)
    if raised is None:
        return BacklogResponse(verifier_workers, verifier_workers, verifier_ceiling, False, "normal", None)
    since = raised.get("since")
    since = None if since is None else float(since)
    paused = since is None or now - since < pause_seconds
    return BacklogResponse(
        verifier_workers, verifier_ceiling, verifier_ceiling, paused,
        "raised" if paused else "pause_lapsed", since,
    )


def evaluate(
    previous: BackpressureState, observation: Observation, limits: BackpressureLimits
) -> tuple[BackpressureState, list[Transition]]:
    """One step of the watermark state machine; pure, so it can be unit-tested."""

    raised: dict[str, dict[str, Any]] = {k: dict(v) for k, v in previous.raised.items()}
    transitions: list[Transition] = []
    for dimension in OBSERVED_DIMENSIONS:
        observed = observation.value(dimension)
        high, low = limits.high(dimension), limits.low(dimension)
        was_raised = dimension in raised
        if not was_raised and observed >= high:
            raised[dimension] = {
                "observed": observed,
                "high": high,
                "low": low,
                "since": observation.observed_at,
            }
            transitions.append(Transition(dimension, RAISED, observed, high, low))
        elif was_raised and observed <= low:
            del raised[dimension]
            transitions.append(Transition(dimension, NORMAL, observed, high, low))
        elif was_raised:
            raised[dimension]["observed"] = observed
    level = RAISED if raised else NORMAL
    since = previous.since
    if level != previous.level:
        since = observation.observed_at
    return (
        BackpressureState(
            level=level,
            raised=raised,
            since=since,
            observation=observation.to_json(),
            changes=previous.changes + len(transitions),
        ),
        transitions,
    )


__all__ = (
    "BACKLOG_DIMENSION",
    "BACKLOG_KEY",
    "BACKPRESSURE_VERSION",
    "BacklogResponse",
    "backlog_response",
    "NORMAL",
    "OBSERVED_DIMENSIONS",
    "RAISED",
    "STATE_KEY",
    "BackpressureLimits",
    "BackpressureState",
    "Observation",
    "Transition",
    "evaluate",
)

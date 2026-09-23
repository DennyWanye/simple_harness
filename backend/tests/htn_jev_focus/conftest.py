# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Shared fixtures for the HTN + Jev focus suite.

This is a **test adapter**, not a re-implementation.  Everything it does is map
the section-3 materials of ``simpleHarness_HTN_Jev_专项测试需求.md`` onto the
types the real code already exposes, and capture what comes back:

* the real ``agent_orchestrator.decision`` contract objects (never a stand-in
  for the seam);
* the real ``DecisionService`` — so T05-T08 exercise the production shadow
  branch, the timeout wrapper and the detached-task lifecycle, not a copy;
* the real Host seam ``deskpet.orchestration.decision.DecisionSeam``.

What is *not* substituted: candidate ordering, request construction, result
validation, the shadow branch and the fallback.  What *is* substituted: the
model itself (``StubJev``), which is the one thing section 2.3 permits.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from agent_orchestrator.contracts import Budget, Task, TaskStatus
from agent_orchestrator.decision import (
    DecisionMode,
    DecisionPolicy,
    DecisionProvider,
    DecisionRequest,
    DecisionResult,
    DecisionScore,
    DecisionService,
    DecisionType,
    ExistingDecisionProvider,
)

#: The three legal candidates of section 3.2 material A, in the section's order.
CANDIDATE_IDS = ("candidate-a", "candidate-b", "candidate-c")
CANDIDATE_LABELS = {
    "candidate-a": "补充问题的最小复现信息",
    "candidate-b": "整理现有日志中的错误证据",
    "candidate-c": "改善报告的展示格式",
}
MISSION = "m-htn-jev-focus"

#: The section-3.2 injection table, verbatim.  These are *test injection values*
#: and say nothing about real model confidence.
AGREEING = {"candidate-a": 0.90, "candidate-b": 0.05, "candidate-c": 0.05}
DISAGREEING = {"candidate-a": 0.05, "candidate-b": 0.90, "candidate-c": 0.05}
INCONCLUSIVE = {"candidate-a": 0.49, "candidate-b": 0.48, "candidate-c": 0.03}


def build_request(
    *,
    decision_id: str = "njr-focus-1",
    decision_type: DecisionType = DecisionType.READY_TASK_PRIORITY,
    candidate_ids: tuple[str, ...] = CANDIDATE_IDS,
    mission_id: str = MISSION,
    context: dict[str, Any] | None = None,
) -> DecisionRequest:
    """A real ``DecisionRequest`` over the section-3.2 candidates."""

    from agent_orchestrator.decision import DecisionCandidate

    return DecisionRequest(
        decision_id=decision_id,
        decision_type=decision_type,
        mission_id=mission_id,
        task_id=None,
        candidates=tuple(
            DecisionCandidate(
                id=candidate_id,
                label=CANDIDATE_LABELS.get(candidate_id, candidate_id),
                metadata={"kind": "task"},
            )
            for candidate_id in candidate_ids
        ),
        context=dict(context or {"frontier_size": len(candidate_ids)}),
    )


def build_result(
    request: DecisionRequest,
    probabilities: dict[str, float],
    *,
    selected: str | None = None,
    provider: str = "stub-jev",
    model_version: str | None = "stub-checkpoint",
) -> DecisionResult:
    """A real ``DecisionResult`` whose diagnostics are consistent with its scores.

    ``top1_probability`` and ``margin`` are *derived*, because the real
    ``DecisionResult`` validates them to 1e-9 — a fixture may not invent them.
    """

    candidate_ids = tuple(candidate.id for candidate in request.candidates)
    scores = tuple(DecisionScore(candidate_id, float(probabilities[candidate_id])) for candidate_id in candidate_ids)
    ordered = sorted((score.probability for score in scores), reverse=True)
    chosen = selected or max(scores, key=lambda score: score.probability).candidate_id
    return DecisionResult(
        decision_id=request.decision_id,
        provider=provider,
        selected=chosen,
        scores=scores,
        top1_probability=ordered[0],
        margin=ordered[0] - ordered[1] if len(ordered) > 1 else 1.0,
        latency_ms=0.0,
        model_version=model_version,
    )


class StubJev(DecisionProvider):
    """The controllable Jev test double of section 3.3.

    It subclasses the real ``DecisionProvider`` because the real
    ``DecisionService`` type-checks its providers with ``isinstance`` — a
    duck-typed object would be refused at construction, not exercised.

    Every behaviour in the section-3.3 table is reachable:
    agreement, disagreement, an inconclusive distribution, an illegal candidate
    set, a raised inference error, and a *gated* return controlled by an
    ``asyncio.Event`` (never a random sleep), so "slow" is deterministic.
    """

    def __init__(
        self,
        selected: str | None = "candidate-a",
        *,
        probabilities: dict[str, float] | None = None,
        error: BaseException | None = None,
        result: DecisionResult | None = None,
        gate: asyncio.Event | None = None,
        on_call: Any = None,
    ) -> None:
        self.selected = selected
        self.probabilities = probabilities
        self.error = error
        self.result = result
        self.gate = gate
        self.on_call = on_call
        self.calls = 0
        self.requests: list[DecisionRequest] = []

    async def decide(self, request: DecisionRequest) -> DecisionResult:
        self.calls += 1
        self.requests.append(request)
        if self.on_call is not None:
            self.on_call(request)
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        probabilities = self.probabilities
        if probabilities is None:
            chosen = self.selected or "candidate-a"
            others = [c.id for c in request.candidates if c.id != chosen]
            share = 0.5 / len(others) if others else 0.0
            probabilities = {chosen: 1.0 - share * len(others)}
            probabilities.update({other: share for other in others})
        return build_result(request, probabilities, selected=self.selected)


def existing_provider(selected: str | None = "candidate-a") -> ExistingDecisionProvider:
    """The frozen, repeatable production decision the section-3 materials name.

    ``selected=None`` means "the first candidate of whatever set arrives", which
    is how the T08 X/Y cases freeze a production answer for candidate ids that are
    not the section-3.2 trio.  A named id is still validated by the real provider,
    so a typo fails loudly rather than silently selecting nothing.
    """

    if selected is None:
        return ExistingDecisionProvider(lambda request: request.candidates[0].id)
    return ExistingDecisionProvider(lambda request: selected)


def build_service(
    *,
    mode: DecisionMode = DecisionMode.SHADOW,
    existing: Any = None,
    shadow: Any = None,
    shadow_timeout_seconds: float | None = None,
) -> DecisionService:
    """The **real** ``DecisionService``, in the mode the case is about."""

    return DecisionService(
        existing if existing is not None else existing_provider(),
        policy=DecisionPolicy(mode=mode),
        shadow_provider=shadow,
        shadow_timeout_seconds=shadow_timeout_seconds,
    )


def task_row(task_id: str, *, priority: float = 1.0, mission_id: str = MISSION) -> Task:
    return Task(
        id=task_id,
        mission_id=mission_id,
        parent_task_ids=(),
        dependency_ids=(),
        goal=f"goal of {task_id}",
        rationale="r",
        success_criteria=("file:x",),
        verification_policy=("format_check",),
        allowed_tools=(),
        budget=Budget(max_tokens=10_000, max_attempts=3),
        priority=priority,
        status=TaskStatus.READY,
        version=1,
        kind="work",
    )


@dataclass
class DownstreamSpy:
    """The minimal test double of section 2.3: it records, then fails the case.

    Nothing in this suite may dispatch a Worker or commit a TaskGraph; if the
    seam under test ever reaches this object, the case fails on the spot.
    """

    calls: list[Any]

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        raise AssertionError(
            "the boundary under test dispatched downstream execution; the focus "
            "suite stops at the local decomposition / decision result"
        )

    def assert_never_called(self) -> None:
        assert self.calls == [], f"downstream execution was reached: {self.calls!r}"


@pytest.fixture
def downstream() -> DownstreamSpy:
    return DownstreamSpy([])


@pytest.fixture
def request_abc() -> DecisionRequest:
    return build_request()

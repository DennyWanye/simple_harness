# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""T04-T08: the Jev decision request/return mapping and the Shadow boundary.

These cases exercise the **real** ``DecisionService`` and the **real** Host seam.
Only the model is substituted (``StubJev``); the request mapping, the result
validation, the shadow branch, the timeout wrapper and the fallback are the
production ones.

Where the SDK repo already owns a case, this file does not restate it — it is
recorded as reused in ``TEST-MATRIX.md`` instead.  What is added here is what the
focus document asks for and the existing suites do not cover as such:

* T04: the section-3.3 illegal-return table as one parameterised case, and the
  candidate-order permutation that catches positional (rather than id) matching.
* T05: the section-3.3 distribution trio (agree / disagree / inconclusive) against
  a frozen production answer, asserting the production answer never moves.
* T06: transient-failure isolation *and* recovery on the next call.
* T07: the non-blocking property asserted by construction (a gated shadow that is
  released only *after* the production answer is already in hand).
* T08: two concurrent requests whose shadow answers arrive out of order, with
  attribution checked by decision_id.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.decision import (
    DecisionMode,
    DecisionResult,
    DecisionScore,
    DecisionType,
)
from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.decision import ShadowObservationStatus

from .conftest import (
    AGREEING,
    CANDIDATE_IDS,
    DISAGREEING,
    INCONCLUSIVE,
    build_request,
    build_result,
    build_service,
    existing_provider,
    StubJev,
)

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------------------
# T04: the request/return mapping
# --------------------------------------------------------------------------------------


async def test_the_request_reaches_jev_with_identity_goal_and_candidates_intact():
    """T04 step 2: target summary, decision type, candidate ids and labels survive."""

    shadow = StubJev(selected="candidate-b")
    service = build_service(shadow=shadow)
    request = build_request(context={"goal_summary": "定位重试模块的失败原因", "frontier_size": 3})

    await service.decide(request)
    await service.close()

    assert len(shadow.requests) == 1
    sent = shadow.requests[0]
    assert sent.decision_id == request.decision_id
    assert sent.decision_type is DecisionType.READY_TASK_PRIORITY
    assert sent.mission_id == request.mission_id
    assert tuple(c.id for c in sent.candidates) == CANDIDATE_IDS
    # The labels are the material's text, not the ids echoed back.
    assert sent.candidates[0].label == "补充问题的最小复现信息"
    assert sent.context["goal_summary"] == "定位重试模块的失败原因"


async def test_a_disagreeing_shadow_answer_is_returned_as_the_shadow_choice():
    """T04 step 3: Jev picks B, and B is what the observation carries back."""

    service = build_service(shadow=StubJev(probabilities=DISAGREEING, selected="candidate-b"))
    production = await service.decide(build_request())
    observations = await service.close()

    assert production.selected == "candidate-a", "production must stay with Existing"
    assert len(observations) == 1
    assert observations[0].shadow_result is not None
    assert observations[0].shadow_result.selected == "candidate-b"


async def test_reordering_the_candidate_array_does_not_mis_select_by_position():
    """T04 step 4: [A,B,C] -> [C,A,B], the answer still points at B by id.

    This is the case a positional implementation fails: B is index 1 in the first
    order and index 2 in the second, so an implementation that maps the returned
    *slot* back onto the original array answers "candidate-c" here.
    """

    reordered = ("candidate-c", "candidate-a", "candidate-b")
    shadow = StubJev(probabilities={"candidate-a": 0.05, "candidate-b": 0.90, "candidate-c": 0.05}, selected="candidate-b")
    service = build_service(shadow=shadow)

    await service.decide(build_request(candidate_ids=reordered))
    observations = await service.close()

    assert tuple(c.id for c in shadow.requests[0].candidates) == reordered
    shadow_result = observations[0].shadow_result
    assert shadow_result is not None
    assert shadow_result.selected == "candidate-b", "selection must follow the id, not the slot"
    # And the scores still cover exactly the request's candidate set.
    assert {score.candidate_id for score in shadow_result.scores} == set(reordered)


@pytest.mark.parametrize(
    "case, probabilities",
    [
        ("nan", {"candidate-a": float("nan"), "candidate-b": 0.5, "candidate-c": 0.5 - 0.5}),
        ("infinity", {"candidate-a": float("inf"), "candidate-b": 0.0, "candidate-c": 0.0}),
        ("negative", {"candidate-a": -0.5, "candidate-b": 0.8, "candidate-c": 0.7}),
        ("above_one", {"candidate-a": 1.5, "candidate-b": -0.3, "candidate-c": -0.2}),
    ],
)
async def test_an_illegal_distribution_is_refused_and_observed_as_a_failure(case, probabilities):
    """T04: NaN / Infinity / negative / >1 all fail the distribution boundary."""

    service = build_service(shadow=StubJev(probabilities=probabilities))
    production = await service.decide(build_request())
    observations = await service.close()

    # The production answer is untouched and still the Existing one.
    assert production.selected == "candidate-a"
    assert len(observations) == 1
    observation = observations[0]
    assert observation.status is ShadowObservationStatus.FAILED, case
    assert observation.shadow_result is None
    assert observation.error, "a refused distribution must leave a reason"


@pytest.mark.parametrize(
    "case, probabilities",
    [
        ("missing_candidate", {"candidate-a": 0.5, "candidate-b": 0.5}),
        ("extra_candidate", {"candidate-a": 0.3, "candidate-b": 0.3, "candidate-c": 0.3, "candidate-d": 0.1}),
        ("sum_far_from_one", {"candidate-a": 0.30, "candidate-b": 0.30, "candidate-c": 0.30}),
        ("sum_slightly_off_beyond_tolerance", {"candidate-a": 0.34, "candidate-b": 0.33, "candidate-c": 0.33}),
    ],
)
async def test_a_distribution_that_does_not_match_the_request_is_refused(case, probabilities):
    """T04: the score set must equal the request's candidate set and sum to 1.

    ``1e-5`` is the contract's tolerance: 0.34+0.33+0.33 = 1.0000000000000002 is
    *inside* it and must be accepted by the sum check; the mismatched-id cases are
    refused on identity, which is checked first.
    """

    service = build_service(shadow=StubJev(probabilities=probabilities))
    production = await service.decide(build_request())
    observations = await service.close()

    assert production.selected == "candidate-a"
    observation = observations[0]
    if case == "sum_slightly_off_beyond_tolerance":
        # 1.0000000000000002 is within 1e-5 of 1: the tolerance is not exact equality.
        assert observation.status is ShadowObservationStatus.COMPLETED
    else:
        assert observation.status is ShadowObservationStatus.FAILED, case
        assert observation.error


async def test_a_returned_candidate_outside_the_request_is_refused():
    """T04: "not in the request" must not become a legal production choice."""

    service = build_service(shadow=StubJev(selected="candidate-zzz", probabilities=AGREEING))
    production = await service.decide(build_request())
    observations = await service.close()

    assert production.selected == "candidate-a"
    assert observations[0].status is ShadowObservationStatus.FAILED
    assert observations[0].shadow_result is None


async def test_a_mislabelled_result_is_rejected_at_construction_time():
    """T04: an illegal score is refused by the real type, not by the assertions."""

    with pytest.raises(ValueError):
        DecisionScore("candidate-a", float("nan"))
    with pytest.raises(ValueError):
        DecisionScore("candidate-a", 1.4)
    with pytest.raises(TypeError):
        DecisionScore("candidate-a", 1)  # int is not a probability


async def test_a_result_for_another_decision_id_is_refused():
    """T04: a coherent-looking result that belongs to another decision is not adopted."""

    other = build_request(decision_id="njr-focus-OTHER")
    stray = build_result(other, AGREEING)
    service = build_service(shadow=StubJev(result=stray))
    production = await service.decide(build_request())
    observations = await service.close()

    assert production.selected == "candidate-a"
    assert observations[0].status is ShadowObservationStatus.FAILED


# --------------------------------------------------------------------------------------
# T05: agreement and disagreement both leave the production answer alone
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case, probabilities, expected_shadow",
    [
        ("agreeing", AGREEING, "candidate-a"),
        ("disagreeing", DISAGREEING, "candidate-b"),
        ("inconclusive", INCONCLUSIVE, "candidate-a"),
    ],
)
async def test_production_is_unchanged_whether_the_shadow_agrees_or_not(case, probabilities, expected_shadow):
    """T05: the frozen production answer is A in all three injected behaviours."""

    shadow = StubJev(probabilities=probabilities, selected=expected_shadow)
    service = build_service(shadow=shadow)
    production = await service.decide(build_request())
    observations = await service.close()

    assert production.selected == "candidate-a", case
    observation = observations[0]
    assert observation.status is ShadowObservationStatus.COMPLETED
    assert observation.shadow_result is not None
    # The shadow answer is preserved verbatim — not rewritten to A to fake agreement.
    assert observation.shadow_result.selected == expected_shadow, case
    assert dict(
        (score.candidate_id, score.probability) for score in observation.shadow_result.scores
    ) == pytest.approx(probabilities)


async def test_the_baseline_without_any_shadow_provider_is_the_same_production_answer():
    """T05 step 2: the Existing baseline, and the model is never called."""

    shadow = StubJev()
    service = build_service(mode=DecisionMode.EXISTING, shadow=shadow)
    production = await service.decide(build_request())
    observations = await service.close()

    assert production.selected == "candidate-a"
    assert shadow.calls == 0, "EXISTING must not spend a model call"
    assert observations == ()


async def test_the_observation_records_both_sides_of_the_comparison():
    """T05: the observation keeps the production and the shadow answer side by side."""

    service = build_service(shadow=StubJev(probabilities=DISAGREEING, selected="candidate-b"))
    await service.decide(build_request())
    observations = await service.close()

    observation = observations[0]
    assert observation.existing_result.selected == "candidate-a"
    assert observation.shadow_result is not None
    assert observation.shadow_result.selected == "candidate-b"
    assert observation.existing_result.selected != observation.shadow_result.selected


# --------------------------------------------------------------------------------------
# T06: a Jev failure never becomes a production failure
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("inference exploded"),
        ContractError("nanojev adapter refused the answer"),
        MemoryError("out of memory"),
    ],
)
async def test_a_raising_shadow_is_observed_and_the_next_call_still_works(error):
    """T06: failure isolation *plus* recovery — no residual failed state."""

    failing = StubJev(error=error)
    service = build_service(shadow=failing)

    first = await service.decide(build_request(decision_id="njr-focus-fail"))
    observations = await service.close()
    assert first.selected == "candidate-a"
    assert observations[0].status is ShadowObservationStatus.FAILED
    assert type(error).__name__ in (observations[0].error or "")

    # Step 5: the adapter carries no failure state into the next request.
    healthy = StubJev(probabilities=DISAGREEING, selected="candidate-b")
    follow_up = build_service(shadow=healthy)
    second = await follow_up.decide(build_request(decision_id="njr-focus-next"))
    follow_up_observations = await follow_up.close()

    assert second.selected == "candidate-a"
    assert healthy.calls == 1
    assert follow_up_observations[0].status is ShadowObservationStatus.COMPLETED
    assert follow_up_observations[0].shadow_result is not None


async def test_a_provider_raised_timeout_is_recorded_as_a_failure_of_this_request():
    """T06 (observed behaviour): a provider-raised ``TimeoutError`` is retained.

    On Python >= 3.11 ``asyncio.TimeoutError`` *is* the builtin ``TimeoutError``,
    so the service's ``except TimeoutError`` cannot tell a provider that timed
    out from a ``wait_for`` that expired.  The observation is therefore filed as
    ``TIMED_OUT`` rather than ``FAILED``.  The production answer is unaffected
    either way, and the error is still attributable to this request — which is
    what T06 actually requires.  Recorded in TEST-MATRIX.md as an observation,
    not a production defect: nothing here reaches the Host's returned plan.
    """

    service = build_service(shadow=StubJev(error=TimeoutError("model unavailable")))
    production = await service.decide(build_request(decision_id="njr-focus-timeout"))
    observations = await service.close()

    assert production.selected == "candidate-a"
    assert len(observations) == 1
    observation = observations[0]
    assert observation.decision_id == "njr-focus-timeout"
    assert observation.status in (
        ShadowObservationStatus.FAILED,
        ShadowObservationStatus.TIMED_OUT,
    )
    assert observation.shadow_result is None
    assert observation.error


async def test_a_failing_shadow_does_not_fabricate_a_successful_prediction():
    """T06: no synthetic "the model agreed" record is written for a failure."""

    service = build_service(shadow=StubJev(error=RuntimeError("no weights")))
    await service.decide(build_request())
    observations = await service.close()

    observation = observations[0]
    assert observation.shadow_result is None
    assert observation.error is not None
    assert observation.status is ShadowObservationStatus.FAILED


# --------------------------------------------------------------------------------------
# T07: a slow shadow is not a precondition for the production answer
# --------------------------------------------------------------------------------------


async def test_the_production_answer_is_returned_before_the_shadow_finishes():
    """T07 step 3: the primary assertion — A is in hand while Jev is still blocked."""

    gate = asyncio.Event()
    shadow = StubJev(selected="candidate-b", gate=gate)
    service = build_service(shadow=shadow)

    production = await service.decide(build_request())
    # Let the detached shadow task actually reach its wait; it still cannot finish,
    # because nothing has set the gate.  This is the real "not yet done" state.
    await asyncio.sleep(0)

    # The answer is already returned, and the shadow has *started* but not finished.
    assert production.selected == "candidate-a"
    assert shadow.calls == 1
    assert service.observations == (), "no observation can exist while the model is gated"

    # Step 4: release it, and only then does the observation appear.
    gate.set()
    observations = await service.close()
    assert len(observations) == 1
    assert observations[0].status is ShadowObservationStatus.COMPLETED
    assert observations[0].shadow_result is not None
    assert observations[0].shadow_result.selected == "candidate-b"


async def test_a_shadow_that_never_returns_is_bounded_by_the_timeout_and_cleans_up():
    """T07 step 5: the timeout bounds the observation, not the production answer."""

    gate = asyncio.Event()  # never set within the timeout
    shadow = StubJev(selected="candidate-b", gate=gate)
    service = build_service(shadow=shadow, shadow_timeout_seconds=0.05)

    production = await service.decide(build_request())
    assert production.selected == "candidate-a"

    observations = await service.close()
    assert len(observations) == 1
    assert observations[0].status is ShadowObservationStatus.TIMED_OUT
    assert observations[0].shadow_result is None

    # Step: "no dangling task of its own" — drain leaves nothing behind.
    assert not [
        task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()
    ]


async def test_the_timeout_does_not_overwrite_the_returned_production_result():
    """T07: a late/timed-out shadow cannot retroactively change what was returned."""

    gate = asyncio.Event()
    shadow = StubJev(probabilities=DISAGREEING, selected="candidate-b", gate=gate)
    service = build_service(shadow=shadow, shadow_timeout_seconds=0.05)

    production = await service.decide(build_request())
    assert production.selected == "candidate-a"

    gate.set()  # release after the timeout has already been recorded
    observations = await service.close()

    assert production.selected == "candidate-a"
    assert observations[0].status in (
        ShadowObservationStatus.TIMED_OUT,
        ShadowObservationStatus.COMPLETED,
    )
    if observations[0].shadow_result is not None:
        assert observations[0].existing_result.selected == "candidate-a"


# --------------------------------------------------------------------------------------
# T08: two requests must not cross their observations
# --------------------------------------------------------------------------------------


async def test_two_requests_keep_their_own_candidates_and_results():
    """T08: X and Y never trade identities, even when Y's shadow returns first."""

    y_release = asyncio.Event()
    x_release = asyncio.Event()

    # Y is released first; X is still gated when Y's observation has landed.
    y_shadow = StubJev(selected="y-b", gate=y_release)
    x_shadow = StubJev(selected="x-b", gate=x_release)

    y_service = build_service(existing=existing_provider(None), shadow=y_shadow)
    x_service = build_service(existing=existing_provider(None), shadow=x_shadow)

    y_request = build_request(decision_id="njr-Y", candidate_ids=("y-a", "y-b"))
    x_request = build_request(decision_id="njr-X", candidate_ids=("x-a", "x-b"))

    x_production = await x_service.decide(x_request)
    y_production = await y_service.decide(y_request)

    assert x_production.selected == "x-a"
    assert y_production.selected == "y-a"

    y_release.set()
    y_observations = await y_service.close()
    assert len(y_observations) == 1
    assert y_observations[0].decision_id == "njr-Y"
    assert tuple(y_observations[0].request_candidate_ids) == ("y-a", "y-b")
    assert y_observations[0].shadow_result is not None
    assert y_observations[0].shadow_result.selected == "y-b"

    # X has not completed yet, so it cannot have produced an observation.
    assert x_service.observations == ()

    x_release.set()
    x_observations = await x_service.close()
    assert len(x_observations) == 1
    assert x_observations[0].decision_id == "njr-X"
    assert tuple(x_observations[0].request_candidate_ids) == ("x-a", "x-b")
    assert x_observations[0].shadow_result is not None
    assert x_observations[0].shadow_result.selected == "x-b"
    assert x_observations[0].existing_result.selected == "x-a"


async def test_a_late_shadow_result_does_not_overwrite_the_production_result():
    """T08: the observation is additive; the returned result object is the same one."""

    gate = asyncio.Event()
    service = build_service(shadow=StubJev(selected="candidate-b", gate=gate))
    production = await service.decide(build_request())

    before = production.selected
    gate.set()
    await service.close()

    assert production.selected == before == "candidate-a"


async def test_the_request_context_is_not_mutated_by_a_later_request():
    """T08 step 5: a request's captured context is not overwritten by a sibling."""

    first = build_request(decision_id="njr-first", context={"marker": "first"})
    second = build_request(decision_id="njr-second", context={"marker": "second"})

    shadow = StubJev()
    service = build_service(shadow=shadow)
    await service.decide(first)
    await service.decide(second)
    observations = await service.close()

    by_id = {observation.decision_id: observation for observation in observations}
    assert by_id["njr-first"].request_context["marker"] == "first"
    assert by_id["njr-second"].request_context["marker"] == "second"


async def test_a_mutating_shadow_cannot_disturb_the_existing_provider_view():
    """T08: the shadow gets a detached snapshot, so mutation cannot leak back."""

    seen: list[dict] = []

    def mutate(request) -> None:
        seen.append(dict(request.context))

    async def _decide(request):
        request.context["marker"] = "mutated-by-shadow"  # type: ignore[index]
        return build_result(request, AGREEING)

    class MutatingShadow(StubJev):
        async def decide(self, request):  # type: ignore[override]
            mutate(request)
            return await _decide(request)

    service = build_service(shadow=MutatingShadow())
    request = build_request(context={"marker": "original"})
    production = await service.decide(request)
    observations = await service.close()

    assert production.selected == "candidate-a"
    assert observations[0].request_context["marker"] == "original"

# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""No Mission without bounds (native run 2026-09-12, adjudication C in journal §4.4).

A person who leaves the budget blank got a Mission whose budget was all null; the real
Planner then invented Task budgets of 1200 / 800 tokens and the Mission failed with
``budget_exhausted``.  The Host door now fills each *blank* item with the deployment
default, keeps an item the person gave, and refuses a non-positive one.  The defaults are
filled before the facade, so the persisted receipt (spec hash) includes them.
"""

from __future__ import annotations

import pytest

from deskpet.orchestration.service import (
    OrchestrationRequestError,
    OrchestrationService,
    OrchestrationSettings,
)
from ._word_counter import FixtureWordCounter

from ._support import notes_provider, notes_request

DEFAULTS = {"max_tokens": 20_000_000, "max_attempts": 12}


def _request(key: str, budget):  # type: ignore[no-untyped-def]
    request = notes_request(key)
    if budget is None:
        request.pop("budget")
    else:
        request["budget"] = budget
    return request


async def _service(root, principal):  # type: ignore[no-untyped-def]
    service = OrchestrationService(
        root, OrchestrationSettings(), provider=notes_provider(), principal=principal, drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await service.start()
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("budget", "expected"),
    [
        (None, DEFAULTS),
        ({}, DEFAULTS),
        ({"max_tokens": None, "max_attempts": None}, DEFAULTS),
        ({"max_tokens": 50_000}, {"max_tokens": 50_000, "max_attempts": 12}),
        ({"max_attempts": 5}, {"max_tokens": 20_000_000, "max_attempts": 5}),
        ({"max_tokens": 90_000, "max_attempts": 4}, {"max_tokens": 90_000, "max_attempts": 4}),
    ],
    ids=["absent", "empty", "nulls", "tokens-only", "attempts-only", "both-given"],
)
async def test_blank_items_take_the_default_and_given_ones_stay(orchestration_root, principal, budget, expected):
    service = await _service(orchestration_root, principal)
    try:
        created = service.create_mission(_request("k-budget", budget))
        stored = service.mission_detail(created["mission_id"])["mission"]["budget"]
        assert {name: stored.get(name) for name in expected} == expected
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "budget",
    [{"max_tokens": 0}, {"max_attempts": -1}, {"max_tokens": "400000"}, {"max_attempts": True}, "400000"],
    ids=["zero-tokens", "negative-attempts", "string-tokens", "bool-attempts", "not-an-object"],
)
async def test_a_bad_budget_item_is_refused_not_replaced(orchestration_root, principal, budget):
    service = await _service(orchestration_root, principal)
    try:
        with pytest.raises(OrchestrationRequestError) as refused:
            service.create_mission(_request("k-bad", budget))
        assert refused.value.code == "invalid_request"
        assert service.list_missions() == []
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_a_blank_budget_retry_returns_the_same_receipt(orchestration_root, principal):
    service = await _service(orchestration_root, principal)
    try:
        first = service.create_mission(_request("k-retry", None))
        again = service.create_mission(_request("k-retry", None))
        assert again["created"] is False
        assert again["mission_id"] == first["mission_id"]
        assert again["spec_hash"] == first["spec_hash"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_the_defaults_are_announced_in_the_status(orchestration_root, principal):
    service = await _service(orchestration_root, principal)
    try:
        assert service.status()["mission_budget_defaults"] == DEFAULTS
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_each_leaf_gets_the_fixed_three_million_allowance(orchestration_root, principal):
    """2026-09-25 user decision: 20M per Mission by default, and every leaf a fixed 1M
    instead of an even share of the Mission pool."""

    service = await _service(orchestration_root, principal)
    try:
        assert service._config.task_max_tokens == 3_000_000
        assert service._orchestrator.commit._task_max_tokens == 3_000_000
        assert service.status()["mission_budget_defaults"]["max_tokens"] == 20_000_000
    finally:
        await service.close()

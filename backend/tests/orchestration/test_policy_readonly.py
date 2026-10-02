# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-7: the Host shows policy state and never changes it.

Draft written before the implementation (plan 2026-09-11 H2/H3).
"""

from __future__ import annotations

import pytest

from agent_orchestrator.api.policies import PolicyApi
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings
from agent_orchestrator.testing.word_counter import FixtureWordCounter

from ._support import notes_provider, notes_request


def test_the_policy_api_only_reads():
    """Structure: the SDK's policy API has no verb that writes the policy library — what
    the Host shows is all there is."""

    public = {name for name in vars(PolicyApi) if not name.startswith("_")}
    assert public == {"list", "show", "status"}


@pytest.mark.asyncio
async def test_policy_status_and_mission_binding_are_visible(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await service.start()
    try:
        status = service.policy_status()
        active = status["active_version_id"]  # PolicyApi.status() shape (SDK api/policies.py)
        assert active
        created = service.create_mission(notes_request("k-policy"))
        detail = service.mission_detail(created["mission_id"])
        assert detail["mission_policy"]["version_id"] == active
        assert status["drift"] == []  # the first start seeds the library from these settings
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_a_later_settings_change_is_shown_as_drift_not_applied(orchestration_root, principal):
    """Plan review P1-5 / HA-7 ④: ``max_concurrency`` reaches the ACTIVE policy only at the
    first seed; a later edit is displayed as drift and the ACTIVE version still governs."""

    first = OrchestrationService(
        orchestration_root, OrchestrationSettings(max_concurrency=1), provider=notes_provider(), principal=principal, drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await first.start()
    active = first.policy_status()["active_version_id"]
    await first.close()

    second = OrchestrationService(
        orchestration_root, OrchestrationSettings(max_concurrency=2), provider=notes_provider(), principal=principal, drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await second.start()
    try:
        status = second.policy_status()
        assert status["active_version_id"] == active  # the edit did not replace it
        assert status["drift"] == [{"name": "mission_concurrency", "config": 2, "active": 1}]
    finally:
        await second.close()

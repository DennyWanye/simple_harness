# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-3 / HA-5: create, run, idempotency, door checks and cancel through the Host service.

Draft written before the implementation (plan 2026-09-11 H2); the service is driven with
``drive=False`` and ``await service.drain()`` so each test is deterministic.
"""

from __future__ import annotations

import pytest

from deskpet.orchestration.service import (
    OrchestrationRequestError,
    OrchestrationService,
    OrchestrationSettings,
)

from ._support import mission_count as _mission_count
from ._support import notes_provider, notes_request


@pytest.mark.asyncio
async def test_create_runs_to_completed_and_is_idempotent(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
    )
    await service.start()
    try:
        first = service.create_mission(notes_request("k-1"))
        assert first["created"] is True
        await service.drain()
        detail = service.mission_detail(first["mission_id"])
        assert detail["mission"]["status"] == "COMPLETED"

        again = service.create_mission(notes_request("k-1"))
        assert again == {"mission_id": first["mission_id"], "created": False}
        assert _mission_count(orchestration_root) == 1
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"goal": "   "}, "invalid_request"),
        ({"success_criteria": []}, "invalid_request"),
        ({"success_criteria": ["pytest:tests/test_x.py"]}, "local_tests_disabled"),
        ({"goal": "用这个密钥 sk-" + "a" * 32 + " 调接口"}, "secret_rejected"),
        ({"success_criteria": ["结果里写上 sk-" + "b" * 32]}, "secret_rejected"),
    ],
)
async def test_door_refuses_and_writes_nothing(orchestration_root, principal, overrides, code):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
    )
    await service.start()
    try:
        with pytest.raises(OrchestrationRequestError) as refused:
            service.create_mission(notes_request("k-door", **overrides))
        assert refused.value.code == code
        assert "sk-" not in str(refused.value)  # the refusal never echoes the secret
        assert _mission_count(orchestration_root) == 0
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_local_tests_setting_lets_pytest_criteria_through(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(allow_local_tests=True),
        provider=notes_provider(),
        principal=principal,
        drive=False,
    )
    await service.start()
    try:
        created = service.create_mission(
            notes_request("k-tests", success_criteria=["pytest:tests/test_x.py"])
        )
        assert created["created"] is True
        assert "run_tests" in service.status()["allowed_tools"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cancel_stops_dispatch_and_is_idempotent(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
    )
    await service.start()
    try:
        created = service.create_mission(notes_request("k-cancel"))
        first = service.cancel_mission(created["mission_id"])
        assert first["status"] == "CANCELLED"
        await service.drain()
        detail = service.mission_detail(created["mission_id"])
        assert detail["mission"]["status"] == "CANCELLED"
        assert detail["attempts"] == []  # nothing was dispatched after the cancel
        assert service.cancel_mission(created["mission_id"])["status"] == "CANCELLED"
    finally:
        await service.close()

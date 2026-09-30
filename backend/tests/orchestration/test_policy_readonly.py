# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-7: the Host shows policy state and never changes it.

Draft written before the implementation (plan 2026-09-11 H2/H3).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import deskpet.orchestration as orchestration_package
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings
from deskpet.orchestration.native_fixture import FixtureWordCounter

from ._support import notes_provider, notes_request

WRITING_VERBS = {"propose", "approve", "reject", "promote", "rollback", "record_evaluation"}


def test_no_host_module_calls_a_policy_writing_verb():
    """Structure: nothing under deskpet/orchestration builds a PolicyApi and calls a verb that
    writes the policy library (propose / approve / reject / promote / rollback)."""

    package_dir = Path(orchestration_package.__file__).parent
    offenders: list[str] = []
    for path in sorted(package_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        policy_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                func = node.value.func
                if isinstance(func, ast.Name) and func.id == "PolicyApi":
                    policy_names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in WRITING_VERBS
            ):
                owner = node.func.value
                if (isinstance(owner, ast.Name) and owner.id in policy_names) or (
                    isinstance(owner, ast.Call)
                    and isinstance(owner.func, ast.Name)
                    and owner.func.id == "PolicyApi"
                ):
                    offenders.append(f"{path.name}:{node.lineno} {node.func.attr}")
    assert offenders == []


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
        assert status["active_version_id"] == active  # nothing was promoted silently
        assert status["drift"] == [{"name": "mission_concurrency", "config": 2, "active": 1}]
    finally:
        await second.close()

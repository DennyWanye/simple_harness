# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-20: the test-only approval-action scenario (plan §3.8, plan review P1-2) never
touches the production library and needs both gates: the environment variable *and* a
user-data directory under ``.local-test-evidence/``.

Draft written before the implementation (plan 2026-09-11 H2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.orchestration import paths as orchestration_paths  # a test_* name would be collected

orchestration_root = orchestration_paths.orchestration_root
scenario_root = orchestration_paths.test_scenario_root
from deskpet.orchestration.service import (
    OrchestrationRequestError,
    OrchestrationService,
    OrchestrationSettings,
)
from deskpet.orchestration.settings import resolve_test_scenario

ENV = "DESKPET_ORCHESTRATION_TEST_SCENARIO"


def test_both_gates_are_required(tmp_path):
    evidence_userdata = tmp_path / ".local-test-evidence" / "2026-09-11" / "userdata"
    plain_userdata = tmp_path / "userdata"
    assert resolve_test_scenario({ENV: "approval-action"}, evidence_userdata) == "approval-action"
    assert resolve_test_scenario({ENV: "approval-action"}, plain_userdata) is None
    assert resolve_test_scenario({}, evidence_userdata) is None
    assert resolve_test_scenario({ENV: "something-else"}, evidence_userdata) is None
    assert resolve_test_scenario({ENV: "approval-action", "DESKPET_DEV_MODE": "1"}, plain_userdata) is None


def test_the_scenario_has_its_own_directory(tmp_path):
    assert scenario_root(tmp_path) == tmp_path / "data" / "agent-orchestrator-test"
    assert scenario_root(tmp_path) != orchestration_root(tmp_path)


@pytest.mark.asyncio
async def test_one_mission_only_and_the_production_library_is_untouched(tmp_path, principal):
    from agent_orchestrator.testing.fixtures import APPROVAL_SPEC

    service = OrchestrationService(
        scenario_root(tmp_path),
        OrchestrationSettings(),
        principal=principal,
        test_scenario="approval-action",
        drive=False,
    )
    await service.start()
    try:
        status = service.status()
        assert status["test_scenario"] == "approval-action"
        request = {
            "goal": APPROVAL_SPEC["goal"],
            "success_criteria": list(APPROVAL_SPEC["success_criteria"]),
            "idempotency_key": "k-scenario",
            "budget": {"max_tokens": 200_000, "max_attempts": 8},
        }
        assert service.create_mission(request)["created"] is True
        with pytest.raises(OrchestrationRequestError) as refused:
            service.create_mission({**request, "idempotency_key": "k-scenario-2"})
        assert refused.value.code == "test_scenario_single_mission"
    finally:
        await service.close()
    assert not Path(orchestration_root(tmp_path) / "orchestrator.db").exists()

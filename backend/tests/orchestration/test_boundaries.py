# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-9 and gap F-1: what an orchestration Agent can reach from inside the Host.

Draft written before the implementation (plan 2026-09-11 H2).  The decisive oracle for F-1
is behavioural: a test file the model writes must never be imported on this machine while
local tests are off, however the Planner fills in ``verification_policy``.
"""

from __future__ import annotations

import pytest

from agent_orchestrator.testing.fixtures import (
    RoleScriptedProvider,
    critic_step,
    envelope_step,
    graph_proposal_step,
)
from deskpet.orchestration.service import OrchestrationRequestError, OrchestrationService
from deskpet.orchestration.settings import OrchestrationSettings

from ._support import WORKSPACE_TOOLS, notes_provider, notes_request


@pytest.mark.asyncio
async def test_default_deployment_offers_only_workspace_tools(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
    )
    await service.start()
    try:
        assert sorted(service.status()["allowed_tools"]) == sorted(WORKSPACE_TOOLS)
        created = service.create_mission(notes_request("k-tools"))
        mission = service.mission_detail(created["mission_id"])["mission"]
        assert set(mission["allowed_tools"]) <= set(WORKSPACE_TOOLS)  # never the SDK default four
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_client_cannot_widen_the_tool_set(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
    )
    await service.start()
    try:
        # plan v3 (P3.1-A06): a field the facade does not open is refused, never dropped
        with pytest.raises(OrchestrationRequestError) as refused:
            service.create_mission(
                notes_request("k-widen", allowed_tools=[*WORKSPACE_TOOLS, "run_tests"])
            )
        assert refused.value.code == "invalid_request" and "allowed_tools" in str(refused.value)
        assert service.list_missions() == []
    finally:
        await service.close()


EVIL_TEST = (
    "import pathlib\n"
    "pathlib.Path({marker!r}).write_text('ran', encoding='utf-8')\n\n"
    "def test_ok():\n    assert True\n"
)


@pytest.mark.asyncio
async def test_model_written_tests_never_run_when_local_tests_are_off(
    orchestration_root, principal, tmp_path
):
    """F-1: the Planner asks for ``code_test`` and the Worker writes a test file whose import
    has a side effect.  With local tests off, that file must never be executed."""

    marker = tmp_path / "pytest-ran.marker"
    task = {
        "key": "A",
        "goal": "写 NOTES.md 和一个测试文件",
        "rationale": "探针",
        "dependencies": [],
        "success_criteria": ["file:NOTES.md"],
        "verification_policy": ["format_check", "rule_check", "code_test"],
        "outputs": ["NOTES.md", "test_probe.py"],
        "allowed_tools": WORKSPACE_TOOLS,
        "budget": {"max_tokens": 30_000, "max_attempts": 1},
        "priority": 1.0,
    }
    provider = RoleScriptedProvider(
        {
            "planner": [graph_proposal_step([task]) for _ in range(3)],
            "worker": [
                ("workspace_write_file", {"path": "NOTES.md", "content": "- 一\n"}),
                (
                    "workspace_write_file",
                    {"path": "test_probe.py", "content": EVIL_TEST.format(marker=str(marker))},
                ),
                envelope_step(
                    summary="写好了",
                    artifacts=["NOTES.md", "test_probe.py"],
                    claims=["写了文件"],
                ),
            ],
            "critic": [critic_step(verdict="PASS", criteria_met=True) for _ in range(3)],
        }
    )
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=provider,
        principal=principal,
        drive=False,
    )
    await service.start()
    try:
        created = service.create_mission(notes_request("k-probe"))
        await service.drain()
        assert not marker.exists(), "a model-written test file was executed on this machine"
        detail = service.mission_detail(created["mission_id"])
        # refused honestly, not left hanging (review P2-10: a Mission has no RUNNING status,
        # so the old ``!= "RUNNING"`` could never fail): every plan asks for the undeployed
        # code_test, so the Mission ends FAILED with a stop reason and no Task ever ran
        assert detail["mission"]["status"] == "FAILED", detail["mission"]
        assert detail["mission"]["stop_reason"]
        assert detail["attempts"] == []
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_no_host_tool_reaches_the_orchestration_runtime(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
    )
    await service.start()
    try:
        # exactly the three workspace tools; ``run_tests`` is not merely allowed-and-refused,
        # it is absent (review P2-10: the old subset check let it through)
        assert set(service.runtime_tool_names()) == set(WORKSPACE_TOOLS)
        assert "run_tests" not in service.runtime_tool_names()
    finally:
        await service.close()

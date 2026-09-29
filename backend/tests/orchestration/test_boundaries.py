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

KNOWLEDGE_TOOLS = ("knowledge_list", "knowledge_read")


@pytest.mark.asyncio
async def test_default_deployment_offers_workspace_and_scoped_knowledge_tools(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
    )
    await service.start()
    try:
        # Workspace and Mission-scoped knowledge reads are always there; ``run_tests`` is there when
        # — and only when — the sandbox probe passed on this machine, because that is the
        # only way model-written code may run at all
        status = service.status()
        sandboxed = bool(status["sandbox"].get("ok"))
        # NEXT-TG-1.0 §11: the native plane (default on) also serves the three Skill tools
        skills = ["skill_discover", "skill_load", "skill_execute"] if status["native_plane"]["enabled"] else []
        expected = sorted([*WORKSPACE_TOOLS, *KNOWLEDGE_TOOLS, *(["run_tests"] if sandboxed else []), *skills])
        assert sorted(status["allowed_tools"]) == expected
        assert status["code_execution"] == ("sandboxed" if sandboxed else "off")
        created = service.create_mission(notes_request("k-tools"))
        mission = service.mission_detail(created["mission_id"])["mission"]
        assert set(mission["allowed_tools"]) <= set(expected)
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
        # the oracle is the same on both paths and is never weakened: a model-written test
        # that tries to write outside its workspace leaves nothing on this machine
        assert not marker.exists(), "a model-written test file reached this machine"
        detail = service.mission_detail(created["mission_id"])
        if bool(service.status()["sandbox"].get("ok")):
            # P3.2: the file *does* run now — inside the sandbox — so the Attempt is real;
            # what stops the side effect is the isolation, not a refusal to execute
            assert detail["attempts"], detail["mission"]
            # Attempt failures are UI-truncated; inspect the durable verification receipt.
            with service._orchestrator.store.read_view():
                snapshot = service._orchestrator.store.snapshot(created["mission_id"])
            runs = [run
                    for result in snapshot["results"]
                    for layer in result["verifications"]
                    if layer["layer"] == "code_test"
                    for run in layer["detail"].get("runs", [])]
            assert any(run.get("receipt", {}).get("status") == "ok"
                       and run["receipt"].get("execution_id")
                       for run in runs), "code_test needs a durable execution receipt"
        else:
            # refused honestly, not left hanging (review P2-10: a Mission has no RUNNING
            # status, so the old ``!= "RUNNING"`` could never fail): every plan asks for the
            # undeployed code_test, so the Mission ends FAILED and no Task ever ran
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
        # Nothing of the Host's own chat surface reaches the orchestration runtime: the set
        # is exactly workspace tools and Mission-scoped knowledge reads, plus ``run_tests``
        # when the sandbox proved itself
        # here (P3.2 plan D9).  Anything else would be a leak (review P2-10).  The three
        # Skill tools are the SDK runtime's own catalogue tools on native pools (NEXT-TG-1.0
        # §11), not Host chat tools.
        offered = set(service.runtime_tool_names())
        sandboxed = bool(service.status()["sandbox"].get("ok"))
        assert offered - set(WORKSPACE_TOOLS) <= {*KNOWLEDGE_TOOLS, "run_tests", "skill_discover", "skill_load", "skill_execute"}
        assert ("run_tests" in offered) is sandboxed
        assert set(WORKSPACE_TOOLS) <= offered
        assert set(KNOWLEDGE_TOOLS) <= offered
    finally:
        await service.close()

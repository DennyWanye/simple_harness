# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-9 and gap F-1: what an orchestration Agent can reach from inside the Host.

Draft written before the implementation (plan 2026-09-11 H2).  The decisive oracle for F-1
is behavioural: a test file the model writes must never be imported on this machine while
local tests are off, however the Planner fills in ``verification_policy``.
"""

from __future__ import annotations

import pytest

from deskpet.orchestration.service import OrchestrationRequestError, OrchestrationService
from ._word_counter import FixtureWordCounter
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
        native_test_counter=FixtureWordCounter(),
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
        native_test_counter=FixtureWordCounter(),
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
async def test_no_host_tool_reaches_the_orchestration_runtime(orchestration_root, principal):
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

# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""NEXT-TG-1.0 §11 / E8: a Mission's Worker can use the shared Skill catalogue.

With the native plane on, the deployment declares the three model-side Skill tools, the
pools' authorization port allows them, a new Mission's charter carries them, and the
current hierarchical Worker's frozen tool list (the original four-way intersection) has
them — on a native pool, which serves them.  With the native plane off nothing changes.
"""

from __future__ import annotations

import pytest

from agent_orchestrator.governance.policies import SKILL_TOOL_NAMES, effective_tools
from agent_orchestrator.runtime.role_templates import WORKER_HIERARCHICAL
from simple_harness.agents import AgentConfig

from ._support import notes_request
from .test_native_plane_host import _service


@pytest.mark.asyncio
async def test_native_plane_deployment_offers_skill_tools_to_the_mission_worker(orchestration_root, principal):
    service = _service(orchestration_root, principal)
    await service.start()
    try:
        deployment = service._deployment
        assert deployment.skill_tools == SKILL_TOOL_NAMES
        assert set(SKILL_TOOL_NAMES) <= set(service.status()["allowed_tools"])
        from types import SimpleNamespace

        from simple_harness.runtime.ports import AuthorizationRequest
        port = service._native.authorization
        for name in SKILL_TOOL_NAMES:
            decision = await port.request_authorization(AuthorizationRequest(SimpleNamespace(name=name, arguments={}), run_id="r"))
            assert decision.decision == "allow", name

        created = service.create_mission(notes_request("skills-1"))
        mission = service._orchestrator.store.get_mission(created["mission_id"])
        assert set(SKILL_TOOL_NAMES) <= set(mission.allowed_tools)
        worker = effective_tools(mission_tools=mission.allowed_tools, task_tools=mission.allowed_tools,
                                 role_tools=WORKER_HIERARCHICAL.tool_names, deployment=deployment)
        assert set(SKILL_TOOL_NAMES) <= set(worker)
        # the native pool the Mission runs on serves every one of them
        pool = service._orchestrator.assembled.pool(service.status()["default_context_profile_id"])
        assert pool.bridge.native_plane is True
        pool.bridge.check_tools(AgentConfig(name="w", instructions="-", model_profile_ref="p", tool_names=worker))
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_without_the_native_plane_no_skill_tool_is_offered(orchestration_root, principal):
    service = _service(orchestration_root, principal, native_plane="off")
    await service.start()
    try:
        assert service._deployment.skill_tools == ()
        assert not set(SKILL_TOOL_NAMES) & set(service.status()["allowed_tools"])
    finally:
        await service.close()

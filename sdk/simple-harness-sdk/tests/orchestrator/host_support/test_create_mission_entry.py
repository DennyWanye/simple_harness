# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Host support 0.9.8 · SA-5: ``Orchestrator.create_mission`` is the one door that knows
the deployment (Host plan §3.1 S1-b, plan review P1-1): parse → ``validate_spec`` against
the deployment's tools → action criteria → local-code-execution check → Commit with the
provider kind and the policy binding the authenticated facade uses.  ``MissionApi`` bound
to an orchestrator uses it too.

HTN 补齐阶段 A′：编排服务是产品同形部署（:func:`product_world`，部署策略照 Host 传入）；
建任务一律在提交层同一事务里初始化根、绑定执行图。删去 ``mission create`` 命令行那条（用户 10-03
定命令行建任务删除）；原来对照的 ``submit_mission`` 已删，改与认证门面 ``create`` 对照。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_orchestrator.api.missions import MissionApi, MissionRequestError
from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.testing.product_world import TENANT, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

TOOLS3 = ("workspace_read_file", "workspace_write_file", "workspace_list")
OFF = DeploymentPolicy(allowed_tools=TOOLS3, local_code_execution=False)


def _request(key, **overrides):
    request = {
        "goal": "写一份 NOTES.md，列出三个要点",
        "success_criteria": ["file:NOTES.md"],
        "idempotency_key": key,
        "budget": {"max_tokens": 200_000, "max_attempts": 4},
    }
    request.update(overrides)
    return request


def _with_orchestrator(tmp_path, body, deployment=OFF):
    async def run():
        async with product_world(Path(tmp_path) / "evidence", LayeredScriptedProvider(), max_concurrency=1,
                                 deployment_policy=deployment) as world:
            result = body(world.loop, world)
            if asyncio.iscoroutine(result):
                result = await result
            return result

    return asyncio.run(run())


def test_create_is_idempotent_and_binds_like_the_facade(tmp_path):
    def body(orchestrator, world):
        first, created = orchestrator.create_mission(tenant_id=TENANT, request=_request("a"))
        again, created_again = orchestrator.create_mission(
            tenant_id=TENANT, request=_request("a"))
        via_facade = orchestrator.store.get_mission(world.control.create(_request("b"))["mission_id"])
        store = orchestrator.store
        return (
            first,
            created,
            again,
            created_again,
            store.get_mission_policy(first.id),
            store.get_mission_policy(via_facade.id),
            len(store.list_missions()),
        )

    first, created, again, created_again, bound, bound_facade, count = _with_orchestrator(
        tmp_path, body
    )
    assert created is True and created_again is False and again.id == first.id
    assert count == 2
    assert bound["provider_kind"] == "fixtures"  # not "unknown"
    assert bound["version_id"] == bound_facade["version_id"]
    # an omitted tool set means the deployment's, not the SDK's four
    assert set(first.allowed_tools) == set(TOOLS3)


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"success_criteria": ["pytest:tests"]}, "local code execution"),
        ({"success_criteria": ["file:NOTES.md", "action:test_config.set:x"]}, "action criterion"),
        ({"allowed_tools": [*TOOLS3, "run_tests"]}, "not offered"),
        ({"goal": "  "}, "goal"),
        ({"success_criteria": []}, "success_criteria"),
    ],
)
def test_the_door_refuses_and_writes_nothing(tmp_path, overrides, fragment):
    def body(orchestrator, world):
        with pytest.raises(MissionRequestError, match=fragment):
            orchestrator.create_mission(tenant_id=TENANT, request=_request("refused", **overrides))
        return len(orchestrator.store.list_missions())

    assert _with_orchestrator(tmp_path, body) == 0


def test_pytest_criteria_are_accepted_when_local_code_execution_is_on(tmp_path):
    def body(orchestrator, world):
        mission, created = orchestrator.create_mission(
            tenant_id=TENANT, request=_request("on", success_criteria=["pytest:tests"])
        )
        return created

    assert _with_orchestrator(tmp_path, body, deployment=DeploymentPolicy()) is True


def test_mission_api_bound_to_the_orchestrator_uses_the_same_door(tmp_path):
    def body(orchestrator, world):
        api = MissionApi(orchestrator.commit, orchestrator=orchestrator)
        with pytest.raises(MissionRequestError, match="local code execution"):
            api.create(tenant_id=TENANT, request=_request("x", success_criteria=["pytest:tests"]))
        mission, created = api.create(tenant_id=TENANT, request=_request("y"))
        return created, orchestrator.store.get_mission_policy(mission.id)

    created, bound = _with_orchestrator(tmp_path, body)
    assert created is True and bound["provider_kind"] == "fixtures"

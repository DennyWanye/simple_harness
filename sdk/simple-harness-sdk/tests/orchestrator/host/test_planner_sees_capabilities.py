# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""规划器能看到执行者在这台机器上能用什么：应用注册的只读工具（带说明）与能提议的外部动作。

真实模型实测（2026-10-21，Sol）：只给工具名不给说明，规划器以为"没有搜照片 / 归档的步骤类型"，
把同一个问题问了四轮。这里用脚本模型钉住：规划请求包里有 executor_capabilities。
"""

from __future__ import annotations

import asyncio
from typing import Any

from agent_orchestrator.host import DomainTool, HostSettings, OrchestrationHost, Principal
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider
from agent_orchestrator.runtime.connectors import OperationSpec, Receipt, params_hash
from agent_orchestrator.testing.scripted_replies import package_of, planner_reply


class _Photos:
    name = "photos"
    supports_idempotency = True
    supports_reconciliation = True
    lookup_authority = "authoritative"
    lookup_fenced = True
    operations = {"move": OperationSpec("move", "L2", ("to",), description="把照片移到 to")}

    def normalize_target(self, target: str) -> str:
        return target.strip()

    def execute(self, operation, target, params, *, idempotency_key):
        return Receipt(idempotency_key, self.name, operation, target, params_hash(params), True)

    def lookup(self, idempotency_key):
        return None


def test_planner_package_lists_registered_tools_and_proposable_actions(tmp_path):
    seen: list[dict[str, Any]] = []

    def planner(request):
        seen.append(package_of(request))
        return planner_reply(request)

    tool = DomainTool(
        name="photo_search",
        schema={
            "type": "object",
            "description": "按关键词搜相册，返回匹配的照片路径",
            "properties": {"q": {"type": "string", "description": "关键词"}},
            "required": ["q"],
            "additionalProperties": False,
        },
        invoke=lambda arguments, context: {"matches": []},
    )

    async def case():
        host = await OrchestrationHost.open(
            tmp_path / "host",
            principal=Principal("u", "用户"),
            provider=ScriptedProvider(planner=planner),
            counter=FixtureWordCounter(),
            settings=HostSettings(max_action_level="L2"),
            connectors=[_Photos()],
            tools=[tool],
            auto_mode=True,
            require_acceptance=False,
        )
        try:
            mission_id = host.create_mission(
                {
                    "goal": "找出长颈鹿照片并归档，然后写 NOTES.md。",
                    "success_criteria": ["file:NOTES.md"],
                    "allowed_actions": ["photos.move"],
                    "idempotency_key": "cap-1",
                }
            )["mission_id"]
            await host.run_until_settled(mission_id, timeout=240)
        finally:
            await host.close()

    asyncio.run(asyncio.wait_for(case(), timeout=300))
    assert seen, "规划器没有被问到"
    capabilities = seen[0]["executor_capabilities"]
    listed = {item["name"]: item for item in capabilities["tools"]}
    assert listed["photo_search"] == {
        "name": "photo_search",
        "description": "按关键词搜相册，返回匹配的照片路径",
    }
    assert {"tool_search", "tool_schema"} <= set(listed)
    assert set(listed) <= {"photo_search", "tool_search", "tool_schema", "sandbox_run"}
    assert [item["name"] for item in capabilities["tools"]] == sorted(listed)
    [action] = capabilities["actions"]
    assert action["connector"] == "photos" and action["operation"] == "move"
    assert action["in_mission_scope"] is True and action["level"] == "L2"
    assert "执行者" in capabilities["note"]

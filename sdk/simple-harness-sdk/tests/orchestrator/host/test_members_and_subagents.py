# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""子 Agent、多成员、完整结果（AgentOS 迁移第 7 块）：把现有机制用测试钉住。

- 权限只缩不放：步骤的工具 = 任务 ∩ 步骤 ∩ 角色 ∩ 部署；步骤写了任务没有的工具也拿不到。
- 多成员：一人一个宿主（各自数据目录、各自当事人）；对方的任务号在自己这里是 not_found。
- 工具集更新：换工具集重开宿主，在途的待批审批卡还在、批准后动作照常执行。
- 超过 32 KB 的工具结果：模型拿到预览 + 回读指引，用 session_history_read 能读到全文。
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.governance.policies import DeploymentPolicy, effective_tools
from agent_orchestrator.host import (
    DomainTool,
    HostRequestError,
    HostSettings,
    OrchestrationHost,
    Principal,
)
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider, worker_reply

sys.path.insert(0, str(Path(__file__).parent))
from test_step_actions import Photos, _worker  # noqa: E402  复用假相册与按剧本提议动作的执行者


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=300))


def test_step_tools_only_narrow_never_widen():
    deployment = DeploymentPolicy(
        allowed_tools=("workspace_read_file", "workspace_write_file", "device_status"),
        domain_tools=("device_status",),
        domain_read_only_tools=("device_status",),
    )
    tools = effective_tools(
        mission_tools=("workspace_read_file", "workspace_write_file"),  # 任务没给 device_status
        task_tools=("workspace_read_file", "workspace_write_file", "device_status", "run_tests"),
        role_tools=("workspace_read_file", "workspace_write_file", "device_status", "run_tests"),
        deployment=deployment,
    )
    assert "device_status" not in tools and "run_tests" not in tools
    assert set(tools) == {"workspace_read_file", "workspace_write_file"}


def test_two_members_each_run_their_own_host_and_cannot_see_each_other(tmp_path):
    async def case():
        a = await OrchestrationHost.open(
            tmp_path / "alice",
            principal=Principal("alice", "甲"),
            provider=ScriptedProvider(),
            counter=FixtureWordCounter(),
            settings=HostSettings(),
            require_acceptance=False,
        )
        b = await OrchestrationHost.open(
            tmp_path / "bob",
            principal=Principal("bob", "乙"),
            provider=ScriptedProvider(),
            counter=FixtureWordCounter(),
            settings=HostSettings(),
            require_acceptance=False,
        )
        try:
            mission_id = a.create_mission(
                {
                    "goal": "写一份 NOTES.md。",
                    "success_criteria": ["file:NOTES.md"],
                    "idempotency_key": "alice-1",
                }
            )["mission_id"]
            assert [m["mission_id"] for m in a.missions()] == [mission_id]
            assert b.missions() == []
            for call in (lambda: b.snapshot(mission_id), lambda: b.cancel(mission_id)):
                with pytest.raises(HostRequestError) as refused:
                    call()
                assert refused.value.code == "not_found"
            # 同一个数据目录第二个宿主起不来：一人一个目录
            with pytest.raises(Exception):
                await OrchestrationHost.open(
                    tmp_path / "alice",
                    principal=Principal("bob", "乙"),
                    provider=ScriptedProvider(),
                    counter=FixtureWordCounter(),
                    settings=HostSettings(),
                    require_acceptance=False,
                )
        finally:
            await a.close()
            await b.close()

    _run(case())


def test_pending_approval_survives_a_tool_set_change_and_restart(tmp_path):
    async def case():
        photos = Photos()
        plan = [
            {
                "action_request": {
                    "connector": "photos",
                    "operation": "share",
                    "target": "/photos/giraffe-1.jpg",
                    "params": {"with": "family"},
                    "reason": "分享",
                }
            }
        ]
        extra = DomainTool(
            name="device_status",
            schema={"type": "object", "description": "查设备", "additionalProperties": False},
            invoke=lambda a, c: {"ok": 1},
        )

        async def open_host(tools):
            return await OrchestrationHost.open(
                tmp_path / "host",
                principal=Principal("u", "用户"),
                provider=ScriptedProvider(worker=_worker(plan)),
                counter=FixtureWordCounter(),
                settings=HostSettings(max_action_level="L2"),
                connectors=[photos],
                tools=tools,
                auto_mode=True,
                require_acceptance=False,
            )

        host = await open_host([extra])
        mission_id = host.create_mission(
            {
                "goal": "分享一张长颈鹿照片，然后写 NOTES.md。",
                "success_criteria": ["file:NOTES.md"],
                "allowed_actions": ["photos.share"],
                "idempotency_key": "share-1",
            }
        )["mission_id"]
        try:
            pending: list[dict[str, Any]] = []
            for _ in range(60):
                await host.drain(timeout=10)
                pending = [a for a in host.approvals(mission_id) if a["state"] == "PENDING"]
                if pending:
                    break
                await asyncio.sleep(0.05)
            assert len(pending) == 1
        finally:
            await host.close()
        # 换工具集（去掉 device_status）重开：待批的审批卡还在，批准后动作照常执行
        host = await open_host([])
        try:
            again = [a for a in host.approvals(mission_id) if a["state"] == "PENDING"]
            assert [a["request_id"] for a in again] == [pending[0]["request_id"]]
            host.decide(again[0]["request_id"], "approve", reason="重开后批准")
            final = await host.run_until_settled(mission_id, timeout=240)
            assert final["snapshot"]["mission"]["status"] == "COMPLETED", final["snapshot"][
                "mission"
            ]
            [action] = host.step_actions(mission_id)
            assert action["state"] == "SUCCEEDED"
            assert photos.calls == [("share", "/photos/giraffe-1.jpg", {"with": "family"})]
        finally:
            await host.close()

    _run(case())


def test_a_large_tool_result_is_fully_readable_through_history_read_back(tmp_path):
    big = "x " * 50_000  # 100 KB；带空格，测试用的按词计数器才算得出"超限"
    seen: list[dict[str, Any]] = []

    def dump(arguments, context):
        return {"blob": big}

    tool = DomainTool(
        name="big_dump",
        schema={
            "type": "object",
            "description": "回一个 100 KB 的结果",
            "additionalProperties": False,
        },
        invoke=dump,
    )

    pages: list[dict[str, Any]] = []

    def worker(request):
        tool_turns = [m for m in request.messages if "tool" in str(m.role).lower()]
        if not tool_turns:
            return ("big_dump", {})
        if len(tool_turns) == 1:
            preview = json.loads(str(tool_turns[0].content))
            seen.append(preview)
            seq = preview["journal_read_back"]["seq"]
            return ("session_history_read", {"seq_from": seq, "seq_to": seq, "max_bytes": 65536})
        # 之后每个工具回合都是一页历史（工具结果信封的 value）：has_more 就带 cursor 继续读，
        # 读完再交给默认执行者
        reading = not pages or bool(pages[-1].get("has_more"))
        if reading and len(tool_turns) == len(pages) + 2:  # 第 1 个工具回合是预览
            pages.append(json.loads(str(tool_turns[-1].content))["value"])
        last = pages[-1]
        if last.get("has_more"):
            seq = seen[0]["journal_read_back"]["seq"]
            return (
                "session_history_read",
                {"seq_from": seq, "seq_to": seq, "max_bytes": 65536, "cursor": last["next_cursor"]},
            )
        # 默认执行者按"工具回合数"数已写文件数；把我们的调用去掉再交给它
        ours = len(pages) + 1
        return worker_reply(
            replace(request, messages=[m for m in request.messages if m not in tool_turns[:ours]])
        )

    async def case():
        host = await OrchestrationHost.open(
            tmp_path / "host",
            principal=Principal("u", "用户"),
            provider=ScriptedProvider(worker=worker),
            counter=FixtureWordCounter(),
            settings=HostSettings(),
            tools=[tool],
            auto_mode=True,
            require_acceptance=False,
        )
        try:
            mission_id = host.create_mission(
                {
                    "goal": "调 big_dump 看完整结果，然后写 NOTES.md。",
                    "success_criteria": ["file:NOTES.md"],
                    "idempotency_key": "big-1",
                }
            )["mission_id"]
            final = await host.run_until_settled(mission_id, timeout=240)
            assert final["snapshot"]["mission"]["status"] == "COMPLETED", final["snapshot"][
                "mission"
            ]
        finally:
            await host.close()
        preview = seen[0]
        assert preview["truncated"] is True
        assert preview["journal_read_back"]["tool"] == "session_history_read"
        assert len(preview["value_preview"]) < len(big)
        # 100 KB 要分两页以上；每页都没有被隐藏的记录；拼起来是完整原文
        assert len(pages) >= 2 and all(p["hidden_seq_ranges"] == [] for p in pages), pages[0]
        assert pages[-1]["has_more"] is False
        text = "".join(item["text"] for page in pages for item in page["items"])
        assert big.strip() in text.replace("\\n", "\n"), text[:300]

    _run(case())

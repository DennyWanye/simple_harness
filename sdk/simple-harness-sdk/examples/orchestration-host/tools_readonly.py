# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""注册一个应用自己的只读工具，让编排里的模型调用它（不联网，脚本模型）。

    python tools_readonly.py

一个假的"设备状态"工具（普通函数）。脚本执行者先调它一次、再写 NOTES.md、交结果。跑完打印：
工具被调了几次、它收到的可信上下文、任务事件里的工具卡（开始 / 成功，带结构化结果）。
把 ``device_status`` 换成你的真实实现、把脚本模型换成 ``model=ModelConfig(...)`` 就是正式用法。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import replace
from pathlib import Path

from agent_orchestrator.host import (
    DomainTool,
    HostSettings,
    OrchestrationHost,
    Principal,
    ToolCallContext,
)
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider, worker_reply

CALLS: list[ToolCallContext] = []


def device_status(arguments, context: ToolCallContext):
    """假设备：真实实现里用 context.principal_id / mission_id 做你自己的授权，再去问设备。"""

    CALLS.append(context)
    return {"device": arguments["device"], "battery": 87, "online": True}


TOOL = DomainTool(
    name="device_status",
    schema={
        "type": "object",
        "description": "读一台设备的当前状态（电量、是否在线）",
        "properties": {"device": {"type": "string", "description": "设备名"}},
        "required": ["device"],
        "additionalProperties": False,
    },
    invoke=device_status,
    timeout_seconds=10,
)


def scripted_worker(request):
    """脚本执行者：没调过工具就先调一次，之后照 SDK 自带脚本写文件、交结果。"""

    tool_replies = [m for m in request.messages if "tool" in str(m.role).lower()]
    if not tool_replies:
        return ("device_status", {"device": "phone-1"})
    rest = [m for m in request.messages if m is not tool_replies[0]]
    return worker_reply(replace(request, messages=rest))


async def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="tools-readonly-"))
    host = await OrchestrationHost.open(
        root,
        principal=Principal("demo-user", "演示用户"),
        provider=ScriptedProvider(worker=scripted_worker),
        counter=FixtureWordCounter(),
        settings=HostSettings(),
        require_acceptance=False,  # 脚本模型只证明接线；正式用法不要关
        tools=[TOOL],
    )
    try:
        receipt = host.create_mission(
            {
                "goal": "查一下 phone-1 的状态，写进 NOTES.md",
                "success_criteria": ["file:NOTES.md"],
                "idempotency_key": "tools-readonly-1",
            }
        )
        final = await host.run_until_settled(receipt["mission_id"], timeout=120)
        print("任务：", final["snapshot"]["mission"]["status"])
        print("工具被调用次数：", len(CALLS))
        if CALLS:
            c = CALLS[0]
            print(
                "可信上下文：",
                {
                    "mission_id": c.mission_id,
                    "call_id": c.call_id,
                    "principal_id": c.principal_id,
                    "tenant_id": c.tenant_id,
                },
            )
        for event in host.events(receipt["mission_id"], limit=200)["events"]:
            if event["type"].startswith("ExternalToolCall"):
                print(event["type"], json.dumps(event["payload"], ensure_ascii=False))
        ok = str(final["snapshot"]["mission"]["status"]) == "COMPLETED" and len(CALLS) == 1
        return 0 if ok else 1
    finally:
        await host.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

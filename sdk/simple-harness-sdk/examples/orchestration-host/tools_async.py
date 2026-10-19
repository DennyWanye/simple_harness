# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""注册一个异步工具（``async def``），并看它失败一次、超时一次、再成功时的事件（不联网，脚本模型）。

    python tools_async.py

假的"应用列表"工具：第一次抛异常（事件里只有异常类型名，文字不外泄）；第二次超时（记为失败
``tool_timeout``，SDK 不自动重试，模型自己决定再调）；第三次成功。脚本执行者连调三次再写 NOTES.md。
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

CALLS: list[str] = []


async def app_list(arguments, context: ToolCallContext):
    """异步工具跑在宿主事件循环上：只用 await，不做阻塞操作。"""

    CALLS.append(context.call_id)
    if len(CALLS) == 1:
        raise RuntimeError("relay token=demo-secret refused")  # 文字不会进事件
    if len(CALLS) == 2:
        await asyncio.sleep(5)  # 超过 timeout_seconds：这个协程被取消，记为失败 tool_timeout
    return {"apps": ["camera", "notes", "music"]}


TOOL = DomainTool(
    name="app_list",
    schema={
        "type": "object",
        "description": "列出设备上允许使用的应用",
        "properties": {},
        "additionalProperties": False,
    },
    invoke=app_list,
    timeout_seconds=0.5,
)


def scripted_worker(request):
    tool_replies = [m for m in request.messages if "tool" in str(m.role).lower()]
    if len(tool_replies) < 3:
        return ("app_list", {})
    rest = [m for m in request.messages if m not in tool_replies[:3]]
    return worker_reply(replace(request, messages=rest))


async def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="tools-async-"))
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
                "goal": "列出 phone-1 上的应用，写进 NOTES.md",
                "success_criteria": ["file:NOTES.md"],
                "idempotency_key": "tools-async-1",
            }
        )
        final = await host.run_until_settled(receipt["mission_id"], timeout=120)
        print("任务：", final["snapshot"]["mission"]["status"])
        print("工具被调用次数：", len(CALLS))
        kinds = []
        for event in host.events(receipt["mission_id"], limit=200)["events"]:
            if event["type"].startswith("ExternalToolCall"):
                kinds.append(event["type"])
                print(event["type"], json.dumps(event["payload"], ensure_ascii=False))
        leaked = any(
            "demo-secret" in json.dumps(e, ensure_ascii=False)
            for e in host.events(receipt["mission_id"], limit=200)["events"]
        )
        ok = (
            str(final["snapshot"]["mission"]["status"]) == "COMPLETED"
            and kinds.count("ExternalToolCallFailed") == 2
            and "ExternalToolCallSucceeded" in kinds
            and not leaked
        )
        return 0 if ok else 1
    finally:
        await host.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

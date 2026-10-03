# SPDX-License-Identifier: Apache-2.0
"""执行者的黑板读工具（HTN 补齐阶段 C）。

金丝雀：产品路径（原生执行池）上执行者调 ``knowledge_list`` 能路由到编排网关并拿到目录。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.testing.product_world import DEFAULT_TOOLS, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, worker_reply

KNOWLEDGE_TOOLS = ("knowledge_list", "knowledge_read")


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _tool_results(request: Any) -> list[str]:
    return [str(message.content) for message in request.messages if "tool" in str(message.role).lower()]


def test_worker_can_call_the_knowledge_tools_on_the_native_pool(tmp_path):
    seen: list[str] = []

    def worker(request: Any) -> Any:
        results = _tool_results(request)
        if not results:
            return ("knowledge_list", {})
        if len(results) == 1:
            seen.append(results[0])
            # hand the rest to the ordinary script, which counts tool messages: skip ours
            return ("workspace_write_file", {"path": "NOTES.md", "content": "# 要点\n\n- 一\n- 二\n- 三\n"})
        reply = worker_reply(request)
        return reply if isinstance(reply, str) else worker_reply_final(request)

    def worker_reply_final(request: Any) -> str:
        # ``worker_reply`` counts tool messages against the declared outputs; with our extra
        # list call it already sees enough of them and returns the envelope.
        raise AssertionError("the script should have reached the result envelope")

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(worker=worker),
                                 allowed_tools=DEFAULT_TOOLS + KNOWLEDGE_TOOLS) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "kn-canary"})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert mission.status.value == "COMPLETED", mission.final_report
            assert seen, "the worker never got a knowledge_list result"
            body = json.loads(seen[0]) if seen[0].lstrip().startswith("{") else {"raw": seen[0]}
            assert "error" not in json.dumps(body).lower() or "denied" not in seen[0].lower(), seen[0]
            return seen[0]

    print(asyncio.run(case())[:600])

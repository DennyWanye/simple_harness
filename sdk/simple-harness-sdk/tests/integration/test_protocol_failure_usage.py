# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""协议错误时只认有效用量（HTN 补齐阶段 A′ 重写，2026-10-03，并入供方记账族）。

产品同形部署上，执行者第一次尝试的模型调用走真实的 OpenAI 兼容适配器（httpx 模拟传输，不连网），
回一个参数是半截 JSON 的工具调用：

* 解析照样失败，工具一次也不执行；
* 回复里带着合法用量的，这几次调用照实结账（SETTLED，实际数），同一请求按"一时失手"再采样
  （``empty_response_retries`` 次，每次各自记账）；
* 用量缺失或不合法（总数对不上）的，不信它：授权留在 UNKNOWN、按上限占着额度，也不再采样；
* 这次尝试失败不扣次数，同一做法再试一次，任务完成。

原文件（旧做法：``leaf_world`` 手工建尝试、自己 new 守卫、计价价格表）的计价断言按分诊裁决②删除；
两条"截断工具调用再采样 / 第二次准入超出任务金额上限"是计价用例，随删。"布尔型 token 数""推理
token 为负"两种不合法用量与"总数对不上"走同一条解析路径，留一种代表。
"""

import asyncio
import json
import sqlite3

import httpx
import pytest

from agent_orchestrator.testing.fixtures import package_of, role_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    planner_reply,
    retry_same_method,
)
from simple_harness.providers import OpenAICompatibleProvider, Secret

VALID_USAGE = {
    "prompt_tokens": 100,
    "completion_tokens": 50,
    "total_tokens": 150,
    "prompt_tokens_details": {"cached_tokens": 20},
    "completion_tokens_details": {"reasoning_tokens": 30},
}


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


class HalfJsonFirstAttempt(LayeredScriptedProvider):
    """第一次尝试的执行者调用走真实 HTTP 适配器，拿到半截 JSON 的工具参数；其余照脚本。"""

    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(planner=lambda request: retry_same_method(request) or planner_reply(request))
        self.http = OpenAICompatibleProvider(client, "https://protocol.invalid/v1", "agent-model",
                                             Secret("fixture-only"))

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        attempt = str(package_of(request).get("attempt", {}).get("attempt_id", ""))
        if role_of(request) == "worker" and attempt.endswith(":attempt-1"):
            self.asked.append("worker")
            return await self.http.invoke(request, cancel=cancel)
        return await super().invoke(request, cancel=cancel)


@pytest.mark.parametrize(
    "usage,known",
    [
        pytest.param(VALID_USAGE, True, id="valid-usage-bad-tool-json"),
        pytest.param(None, False, id="missing-usage"),
        pytest.param({**VALID_USAGE, "total_tokens": 149}, False, id="inconsistent-total"),
    ],
)
def test_protocol_failure_keeps_only_valid_usage_without_resampling_or_tools(tmp_path, usage, known):
    async def case():
        posted = []

        def transport(request):
            # 不带凭据头出去、不连网
            posted.append(json.loads(request.content))
            payload = {
                "id": f"bad-tool-response-{len(posted)}",
                "model": "agent-model",
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"role": "assistant", "content": "", "tool_calls": [{
                        "id": "unparseable-call", "type": "function",
                        "function": {"name": "workspace_write_file", "arguments": '{"path": "never'},
                    }]},
                }],
            }
            if usage is not None:
                payload["usage"] = usage
            return httpx.Response(200, json=payload)

        root = tmp_path / "root"
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            provider = HalfJsonFirstAttempt(client)
            async with product_world(root, provider, max_concurrent_model_calls=1) as world:
                mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                           "idempotency_key": "protocol-" + str(known)})["mission_id"]
                store = world.store

                async def drive():
                    while str(store.get_mission(mission_id).status.value) not in {"COMPLETED", "FAILED"}:
                        await world.drain(timeout=10)
                await asyncio.wait_for(drive(), 60)
                assert str(store.get_mission(mission_id).status.value) == "COMPLETED", world.loop.progress_log[-8:]

                first = next(a for a in (store.get_attempt(row[0]) for row in store.connection.execute(
                    "SELECT attempt_id FROM attempts WHERE mission_id=?", (mission_id,)))
                    if a.id.endswith(":attempt-1"))
                rows = [dict(row) for row in store.connection.execute(
                    "SELECT * FROM provider_token_grants WHERE subject_id=? ORDER BY created_at", (first.id,))]
                samples = 1 + (world.loop._config.empty_response_retries if known else 0)
                # 真实 HTTP 适配器被调到了；有效用量时按"失手"再采样，否则不再采样
                assert len(posted) == len(rows) == samples, (posted, rows)
                # 半截 JSON 的工具调用一次也没执行：尝试 1 的执行者在执行池库里没有任何工具效果
                agent_id = store.get_intent_for_subject(first.id).agent_id
                effects = 0
                for database in root.glob("execution*.db"):
                    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
                        effects += db.execute("SELECT COUNT(*) FROM execution_effects WHERE run_id=?",
                                              (agent_id,)).fetchone()[0]
                assert effects == 0
                assert first.failure["error"]["error_code"] == "provider_protocol_error"
                if known:
                    assert [row["state"] for row in rows] == ["SETTLED"] * samples
                    assert all(row["actual_tokens"] == 150 for row in rows)
                    assert not world.loop.commit.ledger.has_unknown_usage(first.id)
                else:
                    [row] = rows
                    assert row["state"] == "UNKNOWN" and row["actual_tokens"] is None
                    assert world.loop.commit.ledger.has_unknown_usage(first.id)
                # 失败不扣次数，同一做法再试一次做完
                released = [event.payload for event in store.list_events(mission_id)
                            if event.type == "AttemptChargeReleased" and event.attempt_id == first.id]
                assert released and released[0]["failure_class"] in {"INFRA", "INTERRUPTED"}

    asyncio.run(case())

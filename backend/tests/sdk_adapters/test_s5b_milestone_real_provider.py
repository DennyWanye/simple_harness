# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b 价值验证里程碑 — REAL provider（acceptance A1 / S5B-S1-REAL-EFFECT-CLOSURE-MEMORY）。

fresh state.db + fresh Memory 0.6.1 DB（生产 builder）；绑定一个含 README.md（1.1.3）的 workspace 根；
用户消息"把 README 里的版本号改成 1.2.0"；真实 gpt-5.6-luna 走完整链：route → ``write_file`` 经
EffectGate → host.file 事件 → 终答前 ``task_scope_update``（漏调则兜底一次，同一主模型）→
``record_sdk_terminal`` 同事务 outbox → worker ingest → job runner → analysis（真实 provider，
``memory_analysis_proposal``）→ APPLIED 且物化（cognitive head / memory.cognitive.committed）→ 下一轮
``typed_recall`` 读到含 "1.2.0" 的记忆。

Run locally: pytest -m real_provider tests/sdk_adapters/test_s5b_milestone_real_provider.py
Transcripts go to .local-test-evidence/s5b-real-provider/ (ignored); the API key never leaves the process.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from simple_harness.contracts import CallId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall

from tests.sdk_adapters import s5b_memory_harness as mh
from tests.sdk_adapters import s5b_milestone_harness as ms
from tests.sdk_adapters.test_s5a_milestone_real_provider import (
    RealRelayProvider,
    _runtime,
)

pytestmark = pytest.mark.real_provider

_MAIN_SYSTEM = (
    "你是桌面工作台的主模型。当前会话已绑定任务档案（task_scope_id 见下），工作区根目录里有 README.md。"
    "流程：① 先调用 context_route(route=resume_existing, task_scope_id=<下面给出的 exact id>) 提交路由；"
    "② 用 write_file(path='README.md', content=<完整新内容>) 把 README 里的版本号改成用户要求的版本"
    "（只改版本号，其余内容原样保留；README 当前内容也在下面给出）；③ 在给出最终回答之前必须调用"
    " task_scope_update 提交 outcome=mutate（base_revision、evidence_refs 使用系统消息里给出的 current_revision"
    " 与 allowed_evidence_refs；operations 用 kind=plan.step.add 描述已完成的修改）；④ 最后用一句简短中文告诉用户改好了。"
    "路由提交成功后不要再调用 context_route。"
)


class RelayAdapter:
    """``ProductProviderAdapter``-shaped adapter over the same relay for the post-turn calls."""

    def __init__(self, runtime: dict, transcript: list, *, lane: str) -> None:
        self._runtime = runtime
        self.transcript = transcript
        self.lane = lane
        self.calls: list = []
        self.target = SimpleNamespace(provider_id="relay", model=runtime["model"], endpoint_identity="r" * 64)

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        del cancel
        self.calls.append(request)
        messages = []
        for message in request.messages:
            role = getattr(message.role, "value", str(message.role))
            content = message.content if isinstance(message.content, str) else json.dumps(
                [getattr(block, "to_dict", lambda b=block: str(b))() for block in message.content], ensure_ascii=False
            )
            messages.append({"role": role if role in {"system", "user", "assistant"} else "user", "content": content})
        tools = [
            {"type": "function", "function": {"name": spec.name, "description": spec.description,
                                              "parameters": json.loads(json.dumps(spec.parameters, default=dict))}}
            for spec in request.tools
        ]
        payload = {"model": self._runtime["model"], "messages": messages, "tools": tools, "max_tokens": 1500}
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                self._runtime["base_url"] + "/chat/completions",
                headers={"Authorization": f"Bearer {self._runtime['api_key']}"},
                json=payload,
            )
        response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]
        raw = choice["message"]
        self.transcript.append({"lane": self.lane, "request_messages": messages, "response_message": raw,
                                "usage": data.get("usage"), "finish_reason": choice.get("finish_reason")})
        tool_calls = tuple(
            ProviderToolCall(CallId(item["id"]), item["function"]["name"], json.loads(item["function"]["arguments"] or "{}"))
            for item in (raw.get("tool_calls") or [])
        )
        return ProviderResponse(
            request_id=request.request_id,
            message=Message(role=MessageRole.ASSISTANT, content=raw.get("content") or ""),
            tool_calls=tool_calls,
            model=data.get("model"),
            finish_reason=choice.get("finish_reason"),
            provider_request_id=str(data.get("id") or "") or None,
        )


@pytest.mark.asyncio
async def test_real_provider_effect_closure_memory_milestone(tmp_path: Path) -> None:
    runtime = _runtime()
    m = await ms.build(tmp_path)
    started = time.time()
    transcript: list = []
    system = _MAIN_SYSTEM + f"\n当前 task_scope_id: {m.scope_id}\nREADME.md 当前内容:\n{ms.README_BEFORE}"
    import tests.sdk_adapters.test_s5a_milestone_real_provider as s5a

    saved = s5a._SYSTEM
    s5a._SYSTEM = system
    provider = RealRelayProvider(runtime, transcript)
    closure_adapter = RelayAdapter(runtime, transcript, lane="closure")
    analysis_adapter = RelayAdapter(runtime, transcript, lane="analysis")
    menv = None
    report: dict = {"model": runtime["model"], "scope_id": m.scope_id}
    try:
        out = await ms.run_main(m, provider)
        s5a._SYSTEM = saved
        report["main_run_exception"] = None if out["exception"] is None else repr(out["exception"])
        assert out["exception"] is None, out["exception"]
        report["main_calls"] = len(provider.calls)
        report["tool_calls"] = list(m.gate.effects.calls)
        report["readme_after"] = ms.readme(m)
        report["final_answer"] = ms.last_answer(m)

        _observed, settlement, receipt = await ms.settle_terminal(m, closure_adapter=closure_adapter)
        report["closure"] = {"status": settlement.status, "reason": settlement.reason_code, "provider_calls": settlement.provider_calls}
        assert receipt.terminal_state.value == "COMPLETED"

        menv = mh.memory_env(
            m, analysis_adapter, memory_db=tmp_path / "memory" / "human_memory_v7.db",
            subject=ms.SUBJECT, production_builder=True, deadline_ms=120_000,
        )
        report["outbox"] = str(await menv.worker.run_once())
        report["job"] = str(await mh.run_job(menv))
        report["job_idle"] = str(await mh.run_job(menv))
        report["memory"] = await mh.memory_snapshot(menv)
        report["attempts"] = ms.analysis_attempts(m)
        payloads = await mh.recall_payloads(menv, "README", run_id="next-turn")
        report["recall_payloads"] = payloads
        report["duration_s"] = round(time.time() - started, 2)
    finally:
        s5a._SYSTEM = saved
        report["turns"] = transcript
        ms.dump_transcript(ms.transcript_path("s5b-real-provider", started), report)
        if menv is not None:
            await mh.close(menv)

    # --- A1 assertions -------------------------------------------------------------
    assert ms.rows(m.db_path, "SELECT COUNT(*) FROM foreground_runs") == [(1,)]
    assert "1.2.0" in ms.readme(m) and "1.1.3" not in ms.readme(m)
    assert ms.last_answer(m).strip()
    receipts = ms.closure_receipts(m)
    assert [r[1] for r in receipts] == ["mutate"], receipts
    assert settlement.provider_calls <= 1 and len(closure_adapter.calls) <= 1
    assert report["outbox"] == "delivered" and report["job"] == "applied" and report["job_idle"] == "idle"
    assert [(o, s) for o, s, *_ in ms.analysis_attempts(m)] == [(1, "succeeded")]
    assert len(analysis_adapter.calls) == 1
    memory = report["memory"]
    assert memory["jobs"] == [("applied", 1)] and memory["heads"] >= 1
    assert ("memory.cognitive.committed", "pending") in memory["outbox"]
    assert memory["analysis_head"] == memory["cognitive_head"] == [(2,)]
    # Provider 调用计数 = 主 Run N + closure ≤1 + analysis 1。
    assert len(transcript) == len(provider.calls) + len(closure_adapter.calls) + 1
    assert any("1.2.0" in json.dumps(p, ensure_ascii=False) for p in report["recall_payloads"]), report["recall_payloads"]

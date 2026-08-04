# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-6 集成单测 — agent_loop IN-LOOP 三闸接入（plans/2026-06-24-...）。

覆盖：
  - 三闸全 None（pipeline off）→ 回退原链路（BC），不发 PipelineEvent
  - Step2 EvidenceGate：needs_investigation 且首轮无取证就 end_turn → 拦截 nudge
  - 工具请求到达 ToolBatchEvent 后暂停；执行与恢复由外部 harness 负责
  - PipelineEvent 仅 observability=True 时 yield
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from agent.agent_loop import AgentLoop, FinalEvent, PipelineEvent, ToolBatchEvent
from agent.termination import GateConfig, TerminationGate
from deskpet.agent.evidence_gate import EvidenceGate
from llm.types import ChatResponse, ChatUsage, ToolCall


def _usage() -> ChatUsage:
    return ChatUsage(input_tokens=10, output_tokens=5, cache_read_tokens=0, cache_write_tokens=0)


def _final(content: str) -> ChatResponse:
    return ChatResponse(content=content, tool_calls=[], stop_reason="end_turn", usage=_usage())


def _tool(name: str, args: dict) -> ChatResponse:
    return ChatResponse(content="", tool_calls=[ToolCall(id="tc-1", name=name, arguments=args)],
                        stop_reason="tool_use", usage=_usage())


class FakeLLM:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def chat_with_fallback(self, messages, tools=None, model=None, **kwargs) -> ChatResponse:
        self.calls.append({"messages": messages})
        if not self._responses:
            raise RuntimeError("FakeLLM exhausted")
        resp = self._responses.pop(0)
        return resp(messages) if callable(resp) else resp


class FakeTools:
    def __init__(self, results: Optional[dict] = None) -> None:
        self._results = results or {}
        self.calls: list[dict] = []

    def schemas(self, enabled_toolsets=None) -> list[dict]:
        return []

    def dispatch(self, name: str, args: dict, task_id: str = "") -> Any:
        self.calls.append({"name": name, "args": args})
        return self._results.get(name, json.dumps({"ok": True}))


@pytest.mark.asyncio
async def test_pipeline_off_is_bc_no_pipeline_events():
    """三闸全 None → 不发 PipelineEvent，正常 FinalEvent（BC）。"""
    llm = FakeLLM([_final("你好")])
    agent = AgentLoop(llm_registry=llm, tool_registry=FakeTools(), max_iterations=5)
    events = [ev async for ev in agent.run(
        task_id="t1", session_id="s1",
        messages=[{"role": "user", "content": "你好"}],
    )]
    assert not any(isinstance(e, PipelineEvent) for e in events)
    assert any(isinstance(e, FinalEvent) for e in events)


@pytest.mark.asyncio
async def test_host_validated_zero_tool_terminal_skips_foreground_gates():
    """Trusted structured background output terminates before conversation gates."""
    llm = FakeLLM([_final('{"schema_version":1,"ok":true}')])
    agent = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeTools(),
        max_iterations=5,
        evidence_gate=EvidenceGate(max_nudges=2),
        pipeline_needs_investigation=True,
        pipeline_problem_type="debug",
        pipeline_observability=True,
    )

    events = [event async for event in agent.run(
        task_id="background-1",
        session_id="companion:profile-a:1:job-1",
        messages=[{"role": "user", "content": "return strict JSON"}],
        host_validated_zero_tool_terminal=True,
    )]

    assert len(llm.calls) == 1
    assert isinstance(events[-1], FinalEvent)
    assert events[-1].content == '{"schema_version":1,"ok":true}'
    assert not any(
        isinstance(event, PipelineEvent)
        and event.type == "chat_v2_evidence_gate"
        for event in events
    )


@pytest.mark.asyncio
async def test_evidence_gate_blocks_then_pauses_at_external_tool_batch():
    """Evidence nudge runs before an external read batch is exposed."""
    llm = FakeLLM([
        _final("我觉得是 token 过期了"),        # iter1: 无取证直接下结论 → 被拦
        _tool("read", {"path": "auth.py"}),       # iter2: 转而取证
        _final("查证后确认是 token 配置错误"),   # iter3: 取证后收尾 → 放行
    ])
    tools = FakeTools({"read": json.dumps({"ok": True})})
    agent = AgentLoop(
        llm_registry=llm, tool_registry=tools,
        max_iterations=10,
        evidence_gate=EvidenceGate(max_nudges=2),
        pipeline_needs_investigation=True,
        pipeline_problem_type="debug",
        pipeline_observability=True,
    )
    events = [ev async for ev in agent.run(
        task_id="t2", session_id="s2",
        messages=[{"role": "user", "content": "登录报错帮我看看"}],
    )]
    # 发了 evidence_gate 观测事件，且至少一次 blocked=True
    eg_events = [e for e in events if isinstance(e, PipelineEvent) and e.type == "chat_v2_evidence_gate"]
    assert any(e.payload.get("blocked") for e in eg_events)
    batch = next(e for e in events if isinstance(e, ToolBatchEvent))
    assert [call.name for call in batch.tool_calls] == ["read"]
    assert tools.calls == []
    assert len(llm.calls) == 2
    assert not any(isinstance(e, FinalEvent) for e in events)


@pytest.mark.asyncio
async def test_evidence_gate_off_when_no_investigation():
    """needs_investigation=False → EvidenceGate 不拦（首轮 end_turn 直接收尾）。"""
    llm = FakeLLM([_final("答案是 42")])
    agent = AgentLoop(
        llm_registry=llm, tool_registry=FakeTools(), max_iterations=5,
        evidence_gate=EvidenceGate(max_nudges=2),
        pipeline_needs_investigation=False,   # 不需取证
        pipeline_problem_type="factual_qa",
        pipeline_observability=True,
    )
    events = [ev async for ev in agent.run(
        task_id="t3", session_id="s3",
        messages=[{"role": "user", "content": "1+1=?"}],
    )]
    assert any(isinstance(e, FinalEvent) for e in events)
    # 没有 blocked 事件
    eg_blocked = [e for e in events if isinstance(e, PipelineEvent)
                  and e.type == "chat_v2_evidence_gate" and e.payload.get("blocked")]
    assert eg_blocked == []


@pytest.mark.asyncio
async def test_convergence_turn_cap_is_not_consumed_past_tool_batch():
    """The external pause occurs before a later turn could hit the cap."""
    # 模型每轮都调工具（不同参数，避免 hallucination 提前在 allows_tool 拦），永不 end_turn。
    # 注入小 max_turns gate（2）+ 大 max_iterations（10）→ 第 3 轮顶部 allows_call 触顶 → 2d-① 止损。
    llm = FakeLLM([_tool("read", {"path": f"f{i}.py"}) for i in range(20)])
    agent = AgentLoop(
        llm_registry=llm, tool_registry=FakeTools({"read": json.dumps({"ok": True})}),
        max_iterations=10,
        termination_gate=TerminationGate(GateConfig(max_turns=2)),
        convergence_report_on_stop=True,
        pipeline_observability=True,
    )
    events = [ev async for ev in agent.run(
        task_id="t4", session_id="s4",
        messages=[{"role": "user", "content": "无限循环问题"}],
    )]
    assert isinstance(events[-1], ToolBatchEvent)
    assert len(llm.calls) == 1
    assert not any(
        isinstance(e, FinalEvent) and e.stop_reason == "stop_loss" for e in events
    )


@pytest.mark.asyncio
async def test_convergence_off_also_pauses_at_tool_batch():
    """Disabling convergence does not restore internal tool execution."""
    llm = FakeLLM([_tool("read", {"path": f"f{i}.py"}) for i in range(20)])
    agent = AgentLoop(
        llm_registry=llm, tool_registry=FakeTools({"read": json.dumps({"ok": True})}),
        max_iterations=10,
        termination_gate=TerminationGate(GateConfig(max_turns=2)),
        # convergence_report_on_stop 默认 False
    )
    events = [ev async for ev in agent.run(
        task_id="t5", session_id="s5",
        messages=[{"role": "user", "content": "无限循环"}],
    )]
    assert isinstance(events[-1], ToolBatchEvent)
    assert len(llm.calls) == 1
    assert not any(isinstance(e, FinalEvent) for e in events)


@pytest.mark.asyncio
async def test_tool_batch_precedes_agent_loop_exhaustion():
    """AgentLoop cannot exhaust iterations before the harness resumes it."""
    llm = FakeLLM([_tool("read", {"path": f"f{i}.py"}) for i in range(20)])
    agent = AgentLoop(
        llm_registry=llm, tool_registry=FakeTools({"read": json.dumps({"ok": True})}),
        max_iterations=3,
        convergence_report_on_stop=True,
        pipeline_observability=True,
    )
    events = [ev async for ev in agent.run(
        task_id="t6", session_id="s6",
        messages=[{"role": "user", "content": "无限循环不收尾"}],
    )]
    assert isinstance(events[-1], ToolBatchEvent)
    assert len(llm.calls) == 1
    assert not any(
        isinstance(e, FinalEvent) and e.stop_reason == "stop_loss" for e in events
    )


@pytest.mark.asyncio
async def test_pipeline_off_tool_batch_does_not_emit_exhaustion_error():
    """Pipeline-off mode still returns at the first external batch."""
    llm = FakeLLM([_tool("read", {"path": f"f{i}.py"}) for i in range(20)])
    agent = AgentLoop(
        llm_registry=llm, tool_registry=FakeTools({"read": json.dumps({"ok": True})}),
        max_iterations=3,
        # convergence_report_on_stop 默认 False → controller=None
    )
    events = [ev async for ev in agent.run(
        task_id="t7", session_id="s7",
        messages=[{"role": "user", "content": "无限循环不收尾"}],
    )]
    assert isinstance(events[-1], ToolBatchEvent)
    assert len(llm.calls) == 1
    assert not any(isinstance(e, FinalEvent) for e in events)

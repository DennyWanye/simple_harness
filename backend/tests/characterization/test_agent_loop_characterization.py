"""Agent 循环主体（AgentLoop._run_impl）的特性化测试：拆分前后行为逐字一致（代码质量整治阶段 6B）。

每个场景驱动 Agent 循环跑一遍，记下：产出的全部事件、每次提供方调用看到的消息（角色与内容）、
工具派发、替身记录的旁路调用。结果与 agent_loop_golden.json 逐字比对。

基准只在拆分前生成一次：
``CHAR_UPDATE=1 pytest tests/characterization/test_agent_loop_characterization.py``。
拆分后不许重新生成——快照变了就是行为变了。
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agent import agent_loop as agent_loop_module
from agent.agent_loop import AgentLoop
from deskpet.agent.evidence_gate import EvidenceGate
from llm.errors import LLMProviderError
from llm.types import ChatResponse, ChatUsage, ToolCall

GOLDEN = Path(__file__).with_name("agent_loop_golden.json")
UPDATE = os.environ.get("CHAR_UPDATE") == "1"


# ── 替身 ────────────────────────────────────────────────────────────────────


def _usage() -> ChatUsage:
    return ChatUsage(input_tokens=10, output_tokens=5, cache_read_tokens=0, cache_write_tokens=0)


def _final(content: str, reasoning: str = "") -> ChatResponse:
    return ChatResponse(
        content=content,
        tool_calls=[],
        stop_reason="end_turn",
        usage=_usage(),
        reasoning_content=reasoning,
    )


def _tool(name: str, args: dict, call_id: str = "tc-1") -> ChatResponse:
    return ChatResponse(
        content="",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        stop_reason="tool_use",
        usage=_usage(),
    )


class FakeLLM:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    async def chat_with_fallback(self, messages, tools=None, model=None, **kwargs) -> ChatResponse:
        self.calls.append([dict(m) for m in messages])
        if not self._responses:
            raise RuntimeError("FakeLLM exhausted")
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp(messages) if callable(resp) else resp


class FakeTools:
    def __init__(self, schemas: list[dict] | None = None) -> None:
        self._schemas = schemas or []
        self.calls: list[dict] = []

    def schemas(self, enabled_toolsets=None) -> list[dict]:
        return list(self._schemas)

    def dispatch(self, name: str, args: dict, task_id: str = "") -> Any:
        self.calls.append({"name": name, "args": args})
        return json.dumps({"ok": True})


def _raw_final(content: str = "done", provider_id: str = "?") -> dict:
    return {
        "content": content,
        "reasoning_content": "",
        "tool_calls": [],
        "stop_reason": "end_turn",
        "model": f"model-from-{provider_id}",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


class FakeProvider:
    """链模式下的提供方（仿 OpenAICompatibleProvider.chat_with_tools）。"""

    def __init__(
        self,
        provider_id: str,
        responses: list[Any] | None = None,
        *,
        model: str = "stub-model",
        context_window: int = 128_000,
    ) -> None:
        self.id = provider_id
        self.model = model
        self.context_window = context_window
        self.responses = list(responses or [])
        self.calls: list[list[dict[str, Any]]] = []

    async def chat_with_tools(self, messages, *, tools=None, max_tokens=2048, **kwargs):
        self.calls.append([dict(m) for m in messages])
        if not self.responses:
            raise AssertionError(f"FakeProvider({self.id}) exhausted")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


# ── 快照 ────────────────────────────────────────────────────────────────────


def _plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"__class__": type(value).__name__, **_plain(dataclasses.asdict(value))}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


def _messages(calls: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    return [
        [
            {
                "role": m.get("role"),
                "content": m.get("content"),
                "tool_calls": _plain(m.get("tool_calls")),
                "tool_call_id": m.get("tool_call_id"),
            }
            for m in call
        ]
        for call in calls
    ]


def _check(name: str, snapshot: dict[str, Any]) -> None:
    data = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
    plain = json.loads(json.dumps(_plain(snapshot), ensure_ascii=False, sort_keys=True))
    if UPDATE:
        data[name] = plain
        GOLDEN.write_text(
            json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8"
        )
        return
    assert name in data, f"基准里没有场景 {name}（拆分前用 CHAR_UPDATE=1 生成）"
    assert plain == data[name]


async def _run(agent: AgentLoop, **kwargs: Any) -> list[Any]:
    kwargs.setdefault("task_id", "t-char")
    kwargs.setdefault("session_id", "s-char")
    return [event async for event in agent.run(**kwargs)]


# ── 场景：基础链路 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plain_final_answer():
    llm = FakeLLM([_final("你好")])
    agent = AgentLoop(llm_registry=llm, tool_registry=FakeTools(), max_iterations=5)
    events = await _run(agent, messages=[{"role": "user", "content": "你好"}])
    _check("plain_final_answer", {"events": events, "llm": _messages(llm.calls)})


@pytest.mark.asyncio
async def test_tool_request_pauses_at_external_batch():
    tools = FakeTools(
        [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]
    )
    llm = FakeLLM([_tool("read_file", {"path": "a.txt"})])
    agent = AgentLoop(llm_registry=llm, tool_registry=tools, max_iterations=5)
    events = await _run(agent, messages=[{"role": "user", "content": "读 a.txt"}])
    _check(
        "tool_request_pauses_at_external_batch",
        {"events": events, "llm": _messages(llm.calls), "tools": tools.calls},
    )


@pytest.mark.asyncio
async def test_evidence_gate_nudges_then_final():
    llm = FakeLLM([_final("猜的答案"), _final("还是猜"), _final("最后")])
    agent = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeTools(),
        max_iterations=5,
        evidence_gate=EvidenceGate(max_nudges=1),
        pipeline_needs_investigation=True,
        pipeline_problem_type="debug",
        pipeline_observability=True,
    )
    events = await _run(agent, messages=[{"role": "user", "content": "为什么报错"}])
    _check("evidence_gate_nudges_then_final", {"events": events, "llm": _messages(llm.calls)})


@pytest.mark.asyncio
async def test_reasoning_only_turn_is_repaired_once():
    llm = FakeLLM(
        [_final("", reasoning="只想不说"), _final("", reasoning="还是只想"), _final("答")]
    )
    agent = AgentLoop(llm_registry=llm, tool_registry=FakeTools(), max_iterations=5)
    events = await _run(agent, messages=[{"role": "user", "content": "问题"}])
    _check("reasoning_only_turn_is_repaired_once", {"events": events, "llm": _messages(llm.calls)})


@pytest.mark.asyncio
async def test_provider_error_becomes_error_event():
    llm = FakeLLM([LLMProviderError("relay down", status_code=500)])
    agent = AgentLoop(llm_registry=llm, tool_registry=FakeTools(), max_iterations=5)
    events = await _run(agent, messages=[{"role": "user", "content": "问题"}])
    _check("provider_error_becomes_error_event", {"events": events, "llm": _messages(llm.calls)})


@pytest.mark.asyncio
async def test_unexpected_exception_propagates():
    llm = FakeLLM([RuntimeError("bug")])
    agent = AgentLoop(llm_registry=llm, tool_registry=FakeTools(), max_iterations=5)
    seen: list[Any] = []
    with pytest.raises(RuntimeError) as raised:
        async for event in agent.run(
            messages=[{"role": "user", "content": "问题"}], task_id="t-char", session_id="s-char"
        ):
            seen.append(event)
    _check("unexpected_exception_propagates", {"events": seen, "raised": repr(raised.value)})


# ── 场景：提供方链 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_provider_chain_is_reported_once():
    agent = AgentLoop(llm_registry=FakeLLM([]), tool_registry=FakeTools(), max_iterations=5)
    events = await _run(agent, messages=[{"role": "user", "content": "问题"}], provider_chain=[])
    _check("empty_provider_chain_is_reported_once", {"events": events})


@pytest.mark.asyncio
async def test_provider_chain_falls_back_on_transient_error():
    first = FakeProvider("a", [LLMProviderError("boom", status_code=503)])
    second = FakeProvider("b", [_raw_final("来自 b", "b")])
    agent = AgentLoop(llm_registry=FakeLLM([]), tool_registry=FakeTools(), max_iterations=5)
    events = await _run(
        agent, messages=[{"role": "user", "content": "问题"}], provider_chain=[first, second]
    )
    _check(
        "provider_chain_falls_back_on_transient_error",
        {"events": events, "a": _messages(first.calls), "b": _messages(second.calls)},
    )


# ── 场景：上下文规划（prepared_context）────────────────────────────────────────


def _budget(*, fits: bool, window: int = 64_000) -> SimpleNamespace:
    return SimpleNamespace(
        effective_input_budget=window - 4000,
        context_window=window,
        effective_pct=0.9,
        generation_reserve=4000,
        fits=fits,
        planned_input_tokens=1000,
        messages_tokens=900,
        tool_tokens=100,
    )


class FakePreparedContext:
    def __init__(
        self,
        messages: list[dict[str, Any]],
        *,
        jobs: tuple = (),
        replans: list[Any] | None = None,
        coverage: Any = None,
        fits: bool = True,
    ) -> None:
        self.messages = messages
        self.tool_set = None
        self.coverage_compaction_jobs = jobs
        self.attachment_tokens = 0
        self.request_budget = SimpleNamespace(generation_reserve=4000, fits=fits)
        self.coverage_report = coverage
        self._replans = list(replans or [])
        self.replan_calls: list[dict[str, Any]] = []

    async def replan_for_budget(self, **kwargs: Any) -> Any:
        self.replan_calls.append(dict(kwargs))
        return self._replans.pop(0) if self._replans else self

    async def replan_after_compaction(self) -> Any:
        self.replan_calls.append({"after_compaction": True})
        return self._replans.pop(0) if self._replans else self


class _QuietCompressor:
    """压缩器替身：只回答"不需要压缩"；覆盖压缩任务本身由测试替换掉的方法记录。"""

    def should_compress(self, tokens: int) -> bool:
        return False


def _context_agent(monkeypatch, *, fits: bool, compressor: Any = None) -> tuple[AgentLoop, list]:
    seen: list[dict[str, Any]] = []

    def fake_estimate(**kwargs: Any) -> SimpleNamespace:
        seen.append(
            {"model_id": kwargs.get("model_id"), "reserve": kwargs.get("generation_reserve")}
        )
        return _budget(fits=fits)

    monkeypatch.setattr(agent_loop_module, "estimate_selected_attempt_budget", fake_estimate)
    agent = AgentLoop(
        llm_registry=FakeLLM([]), tool_registry=FakeTools(), max_iterations=3, compressor=compressor
    )
    return agent, seen


@pytest.mark.asyncio
async def test_context_chain_budget_fits(monkeypatch):
    agent, seen = _context_agent(monkeypatch, fits=True)
    provider = FakeProvider("p", [_raw_final("规划后回答", "p")])
    ctx = FakePreparedContext([{"role": "user", "content": "规划的问题"}])
    events = await _run(
        agent,
        messages=[{"role": "user", "content": "原问题"}],
        provider_chain=[provider],
        prepared_context=ctx,
        context_request_id="req-1",
    )
    _check(
        "context_chain_budget_fits",
        {
            "events": events,
            "provider": _messages(provider.calls),
            "estimates": seen,
            "replans": ctx.replan_calls,
        },
    )


@pytest.mark.asyncio
async def test_context_chain_budget_blocked_without_compressor(monkeypatch):
    agent, seen = _context_agent(monkeypatch, fits=False)
    provider = FakeProvider("p", [])
    ctx = FakePreparedContext([{"role": "user", "content": "太长的问题"}])
    events = await _run(
        agent,
        messages=[{"role": "user", "content": "原问题"}],
        provider_chain=[provider],
        prepared_context=ctx,
        context_request_id="req-2",
    )
    _check(
        "context_chain_budget_blocked_without_compressor",
        {"events": events, "estimates": seen, "replans": ctx.replan_calls},
    )


@pytest.mark.asyncio
async def test_coverage_jobs_block_without_compressor(monkeypatch):
    agent, _ = _context_agent(monkeypatch, fits=True)
    ctx = FakePreparedContext([{"role": "user", "content": "历史很长"}], jobs=("job-1",))
    events = await _run(
        agent,
        messages=[{"role": "user", "content": "原问题"}],
        prepared_context=ctx,
        context_request_id="req-3",
    )
    _check(
        "coverage_jobs_block_without_compressor", {"events": events, "replans": ctx.replan_calls}
    )


@pytest.mark.asyncio
async def test_coverage_jobs_compacted_then_replanned(monkeypatch):
    calls: list[str] = []
    agent, _ = _context_agent(monkeypatch, fits=True, compressor=_QuietCompressor())

    async def fake_flush(**kwargs: Any) -> None:
        calls.append(f"flush:{kwargs['cycle_id']}")

    async def fake_jobs(**kwargs: Any) -> None:
        calls.append(f"jobs:{kwargs['cycle_id']}")

    monkeypatch.setattr(agent, "_flush_context_snapshot", fake_flush)
    monkeypatch.setattr(agent, "_execute_coverage_compaction_jobs", fake_jobs)
    monkeypatch.setattr(agent, "_resolve_compression_model", lambda *a, **k: "resolution")
    good = SimpleNamespace(
        valid=True, gaps=(), overlaps=(), stale_segment_ids=(), broken_causal_groups=()
    )
    replanned = FakePreparedContext([{"role": "user", "content": "压缩后的历史"}], coverage=good)
    ctx = FakePreparedContext(
        [{"role": "user", "content": "历史很长"}], jobs=("job-1",), replans=[replanned]
    )
    agent.llm = FakeLLM([_final("压缩后回答")])
    events = await _run(
        agent,
        messages=[{"role": "user", "content": "原问题"}],
        prepared_context=ctx,
        context_request_id="req-4",
    )
    _check(
        "coverage_jobs_compacted_then_replanned",
        {"events": events, "llm": _messages(agent.llm.calls), "side": calls},
    )


@pytest.mark.asyncio
async def test_coverage_replan_invalid_blocks(monkeypatch):
    agent, _ = _context_agent(monkeypatch, fits=True, compressor=_QuietCompressor())

    async def noop(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr(agent, "_flush_context_snapshot", noop)
    monkeypatch.setattr(agent, "_execute_coverage_compaction_jobs", noop)
    monkeypatch.setattr(agent, "_resolve_compression_model", lambda *a, **k: "resolution")
    bad = SimpleNamespace(
        valid=True, gaps=("g1",), overlaps=(), stale_segment_ids=(), broken_causal_groups=()
    )
    replanned = FakePreparedContext([{"role": "user", "content": "x"}], coverage=bad)
    ctx = FakePreparedContext(
        [{"role": "user", "content": "历史"}], jobs=("job-1",), replans=[replanned]
    )
    events = await _run(
        agent,
        messages=[{"role": "user", "content": "原问题"}],
        prepared_context=ctx,
        context_request_id="req-5",
    )
    _check("coverage_replan_invalid_blocks", {"events": events})


# ── 场景：轮次上限与止损 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_iterations_exhausted_by_repeated_nudges():
    llm = FakeLLM([_final("猜1"), _final("猜2"), _final("猜3"), _final("猜4")])
    agent = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeTools(),
        max_iterations=2,
        evidence_gate=EvidenceGate(max_nudges=5),
        pipeline_needs_investigation=True,
        pipeline_problem_type="debug",
        pipeline_observability=True,
    )
    events = await _run(agent, messages=[{"role": "user", "content": "为什么"}])
    _check(
        "max_iterations_exhausted_by_repeated_nudges",
        {"events": events, "llm": _messages(llm.calls)},
    )


@pytest.mark.asyncio
async def test_max_iterations_with_convergence_report():
    llm = FakeLLM([_final("猜1"), _final("猜2"), _final("猜3"), _final("猜4")])
    agent = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeTools(),
        max_iterations=2,
        evidence_gate=EvidenceGate(max_nudges=5),
        pipeline_needs_investigation=True,
        pipeline_problem_type="debug",
        pipeline_observability=True,
        convergence_report_on_stop=True,
    )
    events = await _run(agent, messages=[{"role": "user", "content": "为什么"}])
    _check(
        "max_iterations_with_convergence_report", {"events": events, "llm": _messages(llm.calls)}
    )


# ── 场景：收尾闸门 ───────────────────────────────────────────────────────────


class _Tracer:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record(self, item: dict[str, Any]) -> None:
        # 耗时等浮点数每次不同，换成占位
        def scrub(value: Any) -> Any:
            if isinstance(value, float):
                return "<float>"
            if isinstance(value, dict):
                return {k: scrub(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [scrub(v) for v in value]
            return value

        self.records.append(scrub(dict(item)))


@pytest.mark.asyncio
async def test_completion_probe_nudges_until_todos_done():
    pending = [[{"content": "写测试", "activeForm": "写测试中", "status": "pending"}], []]
    tracer = _Tracer()

    async def probe(session_id: str) -> list[str]:
        return pending.pop(0) if pending else []

    llm = FakeLLM([_final("做完了"), _final("真做完了")])
    agent = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeTools(),
        max_iterations=5,
        completion_probe=probe,
        max_completion_nudges=2,
        tracer=tracer,
    )
    events = await _run(agent, messages=[{"role": "user", "content": "完成任务"}])
    _check(
        "completion_probe_nudges_until_todos_done",
        {"events": events, "llm": _messages(llm.calls), "trace": tracer.records},
    )


class _SelfCheck:
    def __init__(self, verdicts: list[bool]) -> None:
        self.verdicts = list(verdicts)
        self.calls: list[dict[str, Any]] = []

    async def check(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({k: _plain(v) for k, v in kwargs.items() if k != "workload_context"})
        passed = self.verdicts.pop(0) if self.verdicts else True
        return SimpleNamespace(
            passed=passed,
            mode="claims",
            claims_unverified=[] if passed else ["claim-1"],
            heterogeneous=False,
            reflection_instruction="" if passed else "请核对你的结论",
        )


@pytest.mark.asyncio
async def test_self_check_gate_nudges_then_passes():
    gate = _SelfCheck([False, True])
    tracer = _Tracer()
    llm = FakeLLM([_final("结论 A"), _final("核对后的结论")])
    agent = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeTools(),
        max_iterations=5,
        self_check_gate=gate,
        pipeline_problem_type="debug",
        pipeline_observability=True,
        tracer=tracer,
    )
    events = await _run(agent, messages=[{"role": "user", "content": "排查问题"}])
    _check(
        "self_check_gate_nudges_then_passes",
        {
            "events": events,
            "llm": _messages(llm.calls),
            "gate": gate.calls,
            "trace": tracer.records,
        },
    )


class _Evaluator:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def evaluate(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({k: _plain(v) for k, v in kwargs.items() if k != "workload_context"})
        return dict(self.result)


@pytest.mark.parametrize(
    ("name", "result"),
    [
        (
            "external_evaluator_requests_revision",
            {"verdict": "revise", "quality_score": 3, "issues": ["缺证据"]},
        ),
        ("external_evaluator_approves", {"verdict": "approve", "quality_score": 9, "issues": []}),
    ],
)
@pytest.mark.asyncio
async def test_external_evaluator_gate(monkeypatch, name, result):
    import deskpet.agent.external_evaluator as external_evaluator_module
    import observability.metrics_sink as metrics_sink

    metrics: list[Any] = []
    monkeypatch.setattr(external_evaluator_module, "is_high_consequence_goal", lambda *a: True)
    monkeypatch.setattr(metrics_sink, "record", lambda *a, **k: metrics.append(_plain(a)))
    evaluator = _Evaluator(result)
    llm = FakeLLM([_final("已部署到生产")])
    agent = AgentLoop(
        llm_registry=llm, tool_registry=FakeTools(), max_iterations=5, external_evaluator=evaluator
    )
    events = await _run(agent, messages=[{"role": "user", "content": "部署到生产"}])
    _check(name, {"events": events, "evaluator": evaluator.calls, "metrics": metrics})


@pytest.mark.asyncio
async def test_self_check_gate_gives_up_after_max_nudges():
    gate = _SelfCheck([False, False, False])
    llm = FakeLLM([_final("结论 A"), _final("结论 B"), _final("结论 C")])
    agent = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeTools(),
        max_iterations=5,
        self_check_gate=gate,
        max_verify_nudges=1,
        pipeline_problem_type="debug",
        pipeline_observability=True,
        tracer=_Tracer(),
    )
    events = await _run(agent, messages=[{"role": "user", "content": "排查问题"}])
    _check(
        "self_check_gate_gives_up_after_max_nudges",
        {"events": events, "llm": _messages(llm.calls), "gate": gate.calls},
    )


@pytest.mark.parametrize("report", [False, True])
@pytest.mark.asyncio
async def test_termination_gate_cap(report):
    from agent.termination import GateConfig, TerminationGate

    llm = FakeLLM([_final("猜1"), _final("猜2"), _final("猜3")])
    agent = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeTools(),
        max_iterations=5,
        termination_gate=TerminationGate(GateConfig(max_turns=1)),
        evidence_gate=EvidenceGate(max_nudges=5),
        pipeline_needs_investigation=True,
        pipeline_problem_type="debug",
        pipeline_observability=True,
        convergence_report_on_stop=report,
    )
    events = await _run(agent, messages=[{"role": "user", "content": "为什么"}])
    _check(f"termination_gate_cap_report_{report}", {"events": events, "llm": _messages(llm.calls)})


@pytest.mark.asyncio
async def test_context_chain_safe_plan_runs_coverage_jobs(monkeypatch):
    calls: list[str] = []
    agent, seen = _context_agent(monkeypatch, fits=True, compressor=_QuietCompressor())

    async def fake_flush(**kwargs: Any) -> None:
        calls.append(f"flush:{kwargs['cycle_id']}:{len(kwargs['working_messages'])}")

    async def fake_jobs(**kwargs: Any) -> None:
        calls.append(f"jobs:{kwargs['cycle_id']}")

    monkeypatch.setattr(agent, "_flush_context_snapshot", fake_flush)
    monkeypatch.setattr(agent, "_execute_coverage_compaction_jobs", fake_jobs)
    monkeypatch.setattr(agent, "_resolve_compression_model", lambda *a, **k: "resolution")
    settled = FakePreparedContext([{"role": "user", "content": "压缩后规划"}])
    with_jobs = FakePreparedContext(
        [{"role": "user", "content": "要先压缩"}], jobs=("job-1",), replans=[settled]
    )
    ctx = FakePreparedContext([{"role": "user", "content": "原规划"}], replans=[with_jobs])
    provider = FakeProvider("p", [_raw_final("规划后回答", "p")])
    events = await _run(
        agent,
        messages=[{"role": "user", "content": "原问题"}],
        provider_chain=[provider],
        prepared_context=ctx,
        context_request_id="req-6",
    )
    _check(
        "context_chain_safe_plan_runs_coverage_jobs",
        {
            "events": events,
            "provider": _messages(provider.calls),
            "side": calls,
            "replans": ctx.replan_calls + with_jobs.replan_calls,
            "estimates": seen,
        },
    )


@pytest.mark.asyncio
async def test_periodic_self_check_reports_tools_used():
    """第 10 轮的第一级自检提示带着本轮累计的工具调用次数（来自外部反馈）。"""
    llm = FakeLLM([_final("继续回答")])
    agent = AgentLoop(llm_registry=llm, tool_registry=FakeTools(), max_iterations=12)
    events = await _run(
        agent,
        messages=[{"role": "user", "content": "长任务"}],
        _external_tool_feedback={"iteration": 9, "tools_used_count": 3},
    )
    _check(
        "periodic_self_check_reports_tools_used", {"events": events, "llm": _messages(llm.calls)}
    )

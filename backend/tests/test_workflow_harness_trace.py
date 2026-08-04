from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from agent.agent_loop import AgentLoop, ErrorEvent, FinalEvent, ToolBatchEvent
from deskpet.workflows.trace import SpanKind, SpanStatus, TraceStore
from llm.types import ChatResponse, ChatUsage, ToolCall


def _usage() -> ChatUsage:
    return ChatUsage(input_tokens=8, output_tokens=5)


def _tool_response() -> ChatResponse:
    return ChatResponse(
        content="Checking.",
        stop_reason="tool_use",
        tool_calls=[
            ToolCall(id="call-1", name="read_file", arguments={"path": "notes.md"})
        ],
        usage=_usage(),
    )


def _final_response() -> ChatResponse:
    return ChatResponse(
        content="Checked the file.",
        stop_reason="end_turn",
        tool_calls=[],
        usage=_usage(),
    )


class ScriptedLLM:
    def __init__(self, responses: list[ChatResponse]) -> None:
        self.responses = list(responses)

    async def chat_with_fallback(self, messages, **kwargs):  # noqa: ANN001, ANN003
        return self.responses.pop(0)


class WaitingLLM:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def chat_with_fallback(self, messages, **kwargs):  # noqa: ANN001, ANN003
        self.started.set()
        await asyncio.Event().wait()


class SyncTools:
    def schemas(self, enabled_toolsets=None):  # noqa: ANN001
        return []

    def get(self, name: str):  # noqa: ANN201
        return SimpleNamespace(name=name, completion_semantics="sync")

    async def execute_tool(self, name, args, session_id, task_id):  # noqa: ANN001
        return {
            "ok": True,
            "result": json.dumps({"path": args["path"], "content": "hello"}),
            "error": None,
        }


def _trace_rows(path) -> tuple[sqlite3.Row, list[sqlite3.Row]]:  # noqa: ANN001
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        run = db.execute("SELECT * FROM trace_runs").fetchone()
        spans = db.execute(
            "SELECT * FROM trace_spans ORDER BY started_at, span_id"
        ).fetchall()
        assert run is not None
        return run, spans
    finally:
        db.close()


@pytest.mark.asyncio
async def test_agent_loop_persists_closed_root_llm_and_gate_spans_at_tool_boundary(tmp_path) -> None:
    trace_path = tmp_path / "workflow.db"
    loop = AgentLoop(
        llm_registry=ScriptedLLM([_tool_response()]),
        tool_registry=SyncTools(),
        trace_store=TraceStore(trace_path),
        max_iterations=3,
    )

    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "check notes"}],
            session_id="session-trace",
            task_id="task-trace",
            trace_request_id="request-trace",
            trace_turn_id="turn-trace",
        )
    ]

    assert isinstance(events[-1], ToolBatchEvent)
    run, spans = _trace_rows(trace_path)
    assert run["run_id"] == "task-trace"
    assert run["request_id"] == "request-trace"
    assert run["turn_id"] == "turn-trace"
    assert run["status"] == SpanStatus.OK
    assert run["ended_at"] is not None
    assert run["duration_ms"] >= 0

    kinds = {row["kind"] for row in spans}
    assert {SpanKind.CHAT, SpanKind.LLM, SpanKind.GATE} <= kinds
    assert SpanKind.TOOL not in kinds
    root = next(row for row in spans if row["name"] == "agent_loop.run")
    assert root["parent_span_id"] is None
    assert all(row["status"] != SpanStatus.RUNNING for row in spans)
    assert all(row["ended_at"] is not None for row in spans)
    assert all(row["duration_ms"] >= 0 for row in spans)
    core_children = [
        row for row in spans if row["kind"] in {SpanKind.LLM, SpanKind.GATE}
    ]
    assert all(row["output_ref"] for row in core_children)


@pytest.mark.asyncio
async def test_agent_loop_uses_supplied_ingress_context_without_closing_its_run(tmp_path) -> None:
    trace_path = tmp_path / "workflow.db"
    store = TraceStore(trace_path)
    ingress_run = await store.start_run(
        session_id="session-parent", kind=SpanKind.CHAT, run_id="ingress-run"
    )
    ingress_span = await store.start_span(
        trace_id=ingress_run.trace_id,
        name="chat.ingress",
        kind=SpanKind.CHAT,
        run_id="ingress-run",
        lifecycle_stage="ingress",
    )
    loop = AgentLoop(
        llm_registry=ScriptedLLM([_final_response()]),
        tool_registry=SyncTools(),
        trace_store=store,
    )

    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "hello"}],
            session_id="session-parent",
            trace_context=store.context(ingress_span),
        )
    ]

    assert isinstance(events[-1], FinalEvent)
    tree = await store.tree(ingress_run.trace_id)
    assert tree is not None
    run = tree["run"]
    spans = tree["spans"]
    agent_root = next(span for span in spans if span["name"] == "agent_loop.run")
    assert run["status"] == SpanStatus.RUNNING
    assert agent_root["parent_span_id"] == ingress_span.span_id
    assert agent_root["status"] == SpanStatus.OK


@pytest.mark.asyncio
async def test_max_iteration_terminal_closes_every_span_as_error(tmp_path) -> None:
    trace_path = tmp_path / "workflow.db"
    loop = AgentLoop(
        llm_registry=ScriptedLLM([_tool_response()]),
        tool_registry=SyncTools(),
        trace_store=TraceStore(trace_path),
        max_iterations=1,
    )

    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "keep checking"}],
            session_id="session-max",
            _external_tool_feedback={"iteration": 1},
        )
    ]

    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].reason == "max_iterations"
    run, spans = _trace_rows(trace_path)
    assert run["status"] == SpanStatus.ERROR
    assert all(row["status"] != SpanStatus.RUNNING for row in spans)
    assert next(row for row in spans if row["name"] == "agent_loop.run")["status"] == SpanStatus.ERROR


@pytest.mark.asyncio
async def test_cancelled_llm_call_closes_child_root_and_run(tmp_path) -> None:
    trace_path = tmp_path / "workflow.db"
    llm = WaitingLLM()
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=SyncTools(),
        trace_store=TraceStore(trace_path),
        max_iterations=2,
    )

    async def consume() -> None:
        async for _ in loop.run(
            [{"role": "user", "content": "wait"}],
            session_id="session-cancel",
        ):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(llm.started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    run, spans = _trace_rows(trace_path)
    assert run["status"] == SpanStatus.CANCELLED
    assert all(row["status"] != SpanStatus.RUNNING for row in spans)
    assert next(row for row in spans if row["kind"] == SpanKind.LLM)["status"] == SpanStatus.CANCELLED
    assert next(row for row in spans if row["name"] == "agent_loop.run")["status"] == SpanStatus.CANCELLED

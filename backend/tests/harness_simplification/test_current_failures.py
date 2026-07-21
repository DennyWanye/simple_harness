# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Executable counterexamples frozen before the harness simplification.

Every test in this module is an intentional, strict expected failure.  The
ordinary regression suite therefore stays green, while ``pytest --runxfail``
proves that the legacy implementation still violates each contract.  Remove
the matching ``xfail`` only when the owning work item fixes the production
path; an accidental early fix is reported as XPASS because ``strict=True``.
"""

from __future__ import annotations

import ast
import asyncio
import copy
from pathlib import Path
from typing import Any

import pytest

from agent.agent_loop import AgentLoop, SubagentCompletionEvent
from deskpet.agent.subagent_registry import SubagentRegistry, SubagentRun
from deskpet.tools.registry import ToolRegistry
from deskpet.workflows.routing import WorkflowRoute, route_task
from llm.types import ChatResponse, ChatUsage, ToolCall


ROOT = Path(__file__).resolve().parents[3]


class _ScriptedLLM:
    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        del tools, model, kwargs
        self.calls.append(copy.deepcopy(messages))
        if not self._responses:
            raise AssertionError("scripted LLM exhausted")
        return self._responses.pop(0)


def _tool_response(names: list[str]) -> ChatResponse:
    return ChatResponse(
        content="",
        tool_calls=[
            ToolCall(id=f"call-{index}", name=name, arguments={})
            for index, name in enumerate(names)
        ],
        stop_reason="tool_use",
        usage=ChatUsage(input_tokens=1, output_tokens=1),
        model="wi0-stub",
    )


def _final_response() -> ChatResponse:
    return ChatResponse(
        content="done",
        tool_calls=[],
        stop_reason="end_turn",
        usage=ChatUsage(input_tokens=1, output_tokens=1),
        model="wi0-stub",
    )


def _schema(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"WI-0 probe {name}",
        "parameters": {"type": "object", "properties": {}},
    }


async def _run_tool_batch(
    calls: list[tuple[str, bool]],
) -> tuple[dict[str, frozenset[str]], list[str]]:
    """Run a real AgentLoop turn and capture completed tools at each start."""

    registry = ToolRegistry()
    completed: set[str] = set()
    completed_at_start: dict[str, frozenset[str]] = {}
    completion_order: list[str] = []

    for name, concurrency_safe in calls:

        async def handler(
            _args: dict[str, Any],
            _task_id: str,
            *,
            _name: str = name,
        ) -> dict[str, str]:
            completed_at_start[_name] = frozenset(completed)
            # All current gather tasks enter before any one of them completes.
            # A barrier implementation instead completes the preceding segment.
            await asyncio.sleep(0.01)
            completed.add(_name)
            completion_order.append(_name)
            return {"tool": _name}

        registry.register(
            name,
            "wi0",
            _schema(name),
            handler,
            concurrency_safe=concurrency_safe,
        )

    llm = _ScriptedLLM(
        [_tool_response([name for name, _ in calls]), _final_response()]
    )
    loop = AgentLoop(llm_registry=llm, tool_registry=registry, max_iterations=3)
    _ = [event async for event in loop.run([{"role": "user", "content": "go"}])]
    return completed_at_start, completion_order


@pytest.mark.xfail(
    strict=True,
    reason="EXPECTED-RED WI-0/AC-7: mixed batches require contiguous-safe segments and unsafe barriers",
)
@pytest.mark.asyncio
async def test_expected_red_mixed_safe_unsafe_batch_preserves_source_barriers() -> None:
    snapshots, _ = await _run_tool_batch(
        [
            ("safe-a", True),
            ("safe-b", True),
            ("unsafe-a", False),
            ("unsafe-b", False),
            ("safe-c", True),
        ]
    )

    assert {"safe-a", "safe-b"}.issubset(snapshots["unsafe-a"])
    assert "unsafe-a" in snapshots["unsafe-b"]
    assert "unsafe-b" in snapshots["safe-c"]


@pytest.mark.xfail(
    strict=True,
    reason="EXPECTED-RED WI-0/AC-7: two concurrency-unsafe calls must never overlap",
)
@pytest.mark.asyncio
async def test_expected_red_two_unsafe_tools_do_not_run_concurrently() -> None:
    snapshots, _ = await _run_tool_batch(
        [("unsafe-a", False), ("unsafe-b", False)]
    )
    assert "unsafe-a" in snapshots["unsafe-b"]


@pytest.mark.xfail(
    strict=True,
    reason="EXPECTED-RED WI-0/AC-7: model arguments cannot override host-reserved context",
)
@pytest.mark.asyncio
async def test_expected_red_model_cannot_override_reserved_host_context() -> None:
    registry = ToolRegistry()
    seen: dict[str, Any] = {}

    def handler(args: dict[str, Any], _task_id: str) -> dict[str, bool]:
        seen.update(args)
        return {"captured": True}

    registry.register("reserved_probe", "wi0", _schema("reserved_probe"), handler)
    registry.set_session_context(
        "session-a",
        {
            "_session_id": "session-a",
            "_write_scope_root": "C:/trusted/workspace",
        },
    )
    envelope = await registry.execute_tool(
        "reserved_probe",
        {
            "_session_id": "session-b",
            "_write_scope_root": "C:/attacker/chosen",
        },
        session_id="session-a",
        task_id="reserved-call",
    )

    assert envelope["ok"] is True
    assert seen["_session_id"] == "session-a"
    assert seen["_write_scope_root"] == "C:/trusted/workspace"


def _dict_items(node: ast.Dict) -> dict[str, ast.expr]:
    result: dict[str, ast.expr] = {}
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            result[key.value] = value
    return result


def _inline_tool_result_ok_nodes(path: Path) -> list[ast.expr]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    ok_nodes: list[ast.expr] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        outer = _dict_items(node)
        event_type = outer.get("type")
        payload = outer.get("payload")
        if not (
            isinstance(event_type, ast.Constant)
            and event_type.value == "tool_result"
            and isinstance(payload, ast.Dict)
        ):
            continue
        ok_node = _dict_items(payload).get("ok")
        if ok_node is not None:
            ok_nodes.append(ok_node)
    return ok_nodes


@pytest.mark.parametrize(
    "relative_path",
    [
        Path("backend/deskpet/agent/run_presenter.py"),
        pytest.param(
            Path("backend/pipeline/voice_pipeline.py"),
            marks=pytest.mark.xfail(
                strict=True,
                reason="EXPECTED-RED WI-0/AC-9: Voice ToolResult ok must derive from the typed outcome envelope",
            ),
        ),
    ],
    ids=["main-text-projection", "voice-projection"],
)
def test_expected_red_failed_tool_outcome_is_not_projected_as_success(
    relative_path: Path,
) -> None:
    ok_nodes = _inline_tool_result_ok_nodes(ROOT / relative_path)
    # The post-migration projector may remove these legacy inline envelopes.
    if not ok_nodes:
        return
    hardcoded_success = [
        node
        for node in ok_nodes
        if isinstance(node, ast.Constant) and node.value is True
    ]
    assert not hardcoded_success, (
        f"{relative_path} still hard-codes ok=True for a tool_result projection"
    )


@pytest.mark.xfail(
    strict=True,
    reason="EXPECTED-RED WI-0/AC-5: child completions must be scoped to their parent session",
)
@pytest.mark.asyncio
async def test_expected_red_subagent_completion_cannot_cross_sessions() -> None:
    registry = SubagentRegistry()
    registry.register(
        SubagentRun(run_id="child-of-session-a", kind="research", task_id="task-a")
    )
    registry.complete("child-of-session-a", summary="private result for session A")

    llm = _ScriptedLLM([_final_response()])
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=ToolRegistry(),
        subagent_registry=registry,
        max_iterations=2,
    )
    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "session B says hello"}],
            session_id="session-b",
        )
    ]

    assert not any(isinstance(event, SubagentCompletionEvent) for event in events)
    assert "private result for session A" not in str(llm.calls)


def _is_code_only_route_gate(node: ast.AST) -> bool:
    if not isinstance(node, ast.Compare) or len(node.comparators) != 1:
        return False
    left = node.left
    right = node.comparators[0]
    return (
        isinstance(left, ast.Attribute)
        and isinstance(left.value, ast.Name)
        and left.value.id == "_route_decision"
        and left.attr == "route"
        and isinstance(right, ast.Attribute)
        and isinstance(right.value, ast.Name)
        and right.value.id == "_WorkflowRoute"
        and right.attr == "CODE_COMPLEX"
    )


def _main_has_code_only_workflow_sink() -> bool:
    path = ROOT / "backend/main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(_is_code_only_route_gate(node) for node in ast.walk(tree))


@pytest.mark.xfail(
    strict=True,
    reason="EXPECTED-RED WI-0/AC-11: Code venue must consume Research/PPT route decisions instead of sinking to ReAct",
)
@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("请对这个代码仓库做一次深度调研并给出研究报告", WorkflowRoute.DEEP_RESEARCH),
        ("请为这个项目创建一份 PPT 汇报材料", WorkflowRoute.PPT_PRO),
    ],
    ids=["code-venue-research", "code-venue-ppt"],
)
def test_expected_red_code_venue_has_no_research_or_ppt_route_sink(
    prompt: str,
    expected: WorkflowRoute,
) -> None:
    decision = route_task(prompt, mode="code", workspace_context=True)
    assert decision.route is expected
    assert not _main_has_code_only_workflow_sink()


def _is_chat_interrupt_branch(node: ast.AST) -> bool:
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    test = node.test
    return (
        isinstance(test.left, ast.Name)
        and test.left.id == "msg_type"
        and any(
            isinstance(item, ast.Constant) and item.value == "chat_v2_interrupt"
            for item in test.comparators
        )
    )


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


@pytest.mark.xfail(
    strict=True,
    reason="EXPECTED-RED WI-0/AC-6: /stop must cancel the session's durable workflow run",
)
def test_expected_red_stop_cancels_the_session_workflow_run() -> None:
    path = ROOT / "backend/main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    branches = [node for node in ast.walk(tree) if _is_chat_interrupt_branch(node)]
    assert branches, "chat_v2_interrupt handler is missing"

    call_names = {
        _call_name(call.func)
        for branch in branches
        for statement in branch.body
        for call in ast.walk(statement)
        if isinstance(call, ast.Call)
    }
    durable_cancel = {
        name
        for name in call_names
        if "cancel" in name.casefold()
        and any(
            owner in name.casefold()
            for owner in ("workflow", "run_kernel", "kernel")
        )
        and "ppt" not in name.casefold()
    }
    assert durable_cancel, (
        "chat_v2_interrupt cancels chat/subagent/PPT owners but not the durable workflow owner"
    )

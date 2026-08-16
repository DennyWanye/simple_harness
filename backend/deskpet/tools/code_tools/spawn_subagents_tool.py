"""Stable public schemas and pure translation for durable ChildRun delegates."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from typing import Any, Protocol

from ..capabilities import ToolExecutionContext

_MAX = 8
_KIND_ENUM = ["general", "research", "code", "fileops", "doc", "web"]
_FORBIDDEN = {"agent", "agent_parallel", "spawn_team", "spawn_subagents", "await_subagents"}

_SPAWN_SCHEMA: dict[str, Any] = {
    "name": "spawn_subagents",
    "description": (
        "Start one durable detached child for a batch of delegated tasks. "
        "Returns after the child is accepted; its terminal result is delivered durably."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subagents": {
                "type": "array",
                "minItems": 1,
                "maxItems": _MAX,
                "items": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "prompt": {"type": "string"},
                        "kind": {"type": "string", "enum": _KIND_ENUM},
                        "tools": {"type": "array", "items": {"type": "string"}},
                        "input_files": {"type": "array", "items": {"type": "string"}},
                        "output_files": {"type": "array", "items": {"type": "string"}},
                        "forbidden_files": {"type": "array", "items": {"type": "string"}},
                        "success_criteria": {"type": "string"},
                    },
                    "required": ["prompt"],
                },
            },
        },
        "required": ["subagents"],
    },
}

_AWAIT_SCHEMA: dict[str, Any] = {
    "name": "await_subagents",
    "description": "Wait for durable child runs owned by the current parent and collect results.",
    "parameters": {
        "type": "object",
        "properties": {
            "run_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": [],
    },
}


def product_delegation_tool_catalog() -> dict[str, tuple[Any, dict[str, Any]]]:
    """Keep public schemas in the tool layer while Harness owns execution."""
    from .agent_parallel_tool import _SCHEMA as parallel
    from .agent_tool import _SCHEMA as agent
    from .spawn_team_tool import _SCHEMA as team

    async def boundary_only(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("delegation tool escaped the ReAct ChildRun boundary")

    return {name: (boundary_only, dict(schema)) for name, schema in (
        ("agent", agent), ("agent_parallel", parallel),
        ("spawn_team", team), ("spawn_subagents", _SPAWN_SCHEMA),
    )}


def build_await_subagents_tool(workflow_service_provider):
    """Build the durable child join handler from a stable source module."""

    async def _handler(
        args,
        _task_id="",
        *,
        execution_context=None,
    ):
        if execution_context is None:
            raise RuntimeError("await_subagents requires Harness context")
        workflow_service = workflow_service_provider()
        if workflow_service is None:
            raise RuntimeError("workflow service is unavailable")
        uow = workflow_service.execution_uow
        from deskpet.execution.contracts import (
            RunRef,
            TERMINAL_RUN_STATUSES,
        )
        from deskpet.harness.drivers.react_boundary import ReactCommandBoundary

        continuation = await uow.load_continuation(execution_context.run_id)
        if continuation is None:
            raise RuntimeError("parent continuation is unavailable")
        boundary = ReactCommandBoundary.from_record(continuation)
        context = boundary.run_context
        if context is None or (
            context.session_id,
            context.root_run_id,
            context.capability_hash,
        ) != (
            execution_context.session_id,
            execution_context.root_run_id,
            execution_context.capability_hash,
        ):
            raise RuntimeError("subagent scope differs from durable parent")
        actor = context.actor()
        links = await uow.list_child_links(
            RunRef(boundary.run_id, boundary.session_id),
            actor,
        )
        requested = {
            str(item)
            for item in (args.get("run_ids") or ())
            if str(item)
        }
        child_ids = [
            link.child_run_id
            for link in links
            if not requested or link.child_run_id in requested
        ]
        if requested - set(child_ids):
            raise RuntimeError(
                "requested child does not belong to this parent"
            )
        results = []
        for child_id in child_ids:
            while True:
                child = await uow.query(
                    RunRef(child_id, context.session_id),
                    actor,
                )
                if child.status in TERMINAL_RUN_STATUSES:
                    break
                await asyncio.sleep(0.05)
            event = await uow.get_event(child.terminal_event_id)
            results.append(
                {
                    "run_id": child_id,
                    "status": child.status.value,
                    "value": event.candidate.payload.get("text"),
                    "error": event.candidate.error,
                }
            )
        return json.dumps(
            {"ok": True, "results": results},
            ensure_ascii=False,
        )

    return _handler, dict(_AWAIT_SCHEMA)


class SdkSubagentJoinPort(Protocol):
    """Host boundary used by the SDK Tool catalog to join durable children."""

    def await_subagents(
        self,
        *,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
        operation_key: str,
    ) -> Any: ...


def build_sdk_await_subagents_tool(
    join_port_provider,
):
    """Build the new-stack join handler without importing legacy authority.

    The product host owns the durable child lookup and authorization details.
    The Tool layer only supplies the frozen SDK call identity and execution
    context to that typed boundary.
    """

    async def _handler(
        args,
        operation_key="",
        *,
        execution_context=None,
    ):
        if execution_context is None:
            raise RuntimeError("await_subagents requires SDK execution context")
        port = join_port_provider()
        await_join = getattr(port, "await_subagents", None)
        if port is None or not callable(await_join):
            raise RuntimeError("SDK subagent join port is unavailable")
        result = await_join(
            arguments=dict(args),
            context=execution_context,
            operation_key=str(operation_key),
        )
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, str) else json.dumps(
            result,
            ensure_ascii=False,
        )

    return _handler, dict(_AWAIT_SCHEMA)


def normalize_delegation(name: str, args: dict[str, Any]) -> tuple[str, Any, bool]:
    """Validate legacy argument shapes and return prompt/tools/detachment."""
    requested: Any = None
    if name == "agent":
        prompts, requested = [str(args.get("prompt") or "").strip()], args.get("tools")
    elif name == "spawn_team":
        prompts = args.get("task_descriptions")
    else:
        tasks = args.get("subagents")
        if not isinstance(tasks, list) or not all(isinstance(item, dict) for item in tasks):
            raise ValueError(f"{name} requires subagents")
        prompts = [str(item.get("prompt") or "").strip() for item in tasks]
        requested = [tool for item in tasks for tool in (item.get("tools") or ())] or None
    if not isinstance(prompts, list) or not prompts or not all(prompts):
        raise ValueError(f"{name} requires non-empty delegated tasks")
    text = prompts[0] if name == "agent" else (
        "Complete every delegated task and return results keyed by task index.\n"
        + json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return text, requested, name == "spawn_subagents"


def build_subagent_batch_delegate(
    request: Any,
    args: dict[str, Any],
    execution_context: ToolExecutionContext,
    allowed_capabilities: set[str] | frozenset[str],
):
    """Translate a public spawn batch into one deterministic detached ChildRun."""

    from deskpet.execution.contracts import AuthorizationError
    from deskpet.harness.ports import AttachmentPolicy, DelegateRun, JoinPolicy

    context = request.run_context
    supplied = (
        execution_context.run_id,
        execution_context.session_id,
        execution_context.root_run_id,
        execution_context.capability_hash,
    )
    expected = (
        request.run_id,
        context.session_id,
        context.root_run_id,
        context.capability_hash,
    ) if context is not None else ()
    if supplied != expected:
        raise AuthorizationError("actor_not_authorized", "subagent scope differs from parent")
    raw = args.get("subagents")
    if not isinstance(raw, list) or not 1 <= len(raw) <= _MAX:
        raise ValueError("subagents must contain one to eight tasks")
    tasks = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or not str(item.get("prompt") or "").strip():
            raise ValueError("each subagent requires a prompt")
        task = dict(item)
        requested = [
            str(tool) for tool in (task.get("tools") or sorted(allowed_capabilities))
            if str(tool) not in _FORBIDDEN
        ]
        if not set(requested) <= allowed_capabilities:
            raise AuthorizationError("actor_not_authorized", "subagent requested unavailable tools")
        task.update(task_id=str(task.get("task_id") or f"sub_{index}"), tools=requested)
        identity = json.dumps(
            {"parent": request.run_id, "index": index, "task": task}, sort_keys=True
        )
        task["run_id"] = "sub-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        tasks.append(task)
    frozen = json.dumps(tasks, sort_keys=True, separators=(",", ":"))
    capabilities = tuple(sorted({tool for task in tasks for tool in task["tools"]}))
    return DelegateRun(
        request.run_id,
        "spawn-subagents:" + hashlib.sha256(frozen.encode()).hexdigest()[:24],
        {
            "driver_kind": "react",
            "text": "Complete all delegated tasks and return results keyed by run_id.\n" + frozen,
            "subagent_runs": tasks,
        },
        "react.spawn_subagents",
        capabilities,
        AttachmentPolicy.DETACHED,
        JoinPolicy.DETACHED,
    )


__all__ = [
    "SdkSubagentJoinPort", "build_sdk_await_subagents_tool",
    "build_subagent_batch_delegate", "normalize_delegation",
    "product_delegation_tool_catalog", "_SPAWN_SCHEMA", "_AWAIT_SCHEMA",
]

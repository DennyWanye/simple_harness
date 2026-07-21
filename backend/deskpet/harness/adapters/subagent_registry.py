"""Dormant Subagent tools for an already-durable Harness parent."""

from __future__ import annotations
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from deskpet.execution.contracts import AuthorizationError, PersistenceLevel
from deskpet.harness.ports import DriverEvent, DriverStart
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.code_tools.spawn_subagents_tool import (
    _AWAIT_SCHEMA,
    _SPAWN_SCHEMA,
    build_subagent_batch_delegate,
)
ParentResolver = Callable[[ToolExecutionContext], Awaitable[DriverStart]]
SubmitDelegate = Callable[[DriverStart, DriverEvent, ToolExecutionContext], Awaitable[str]]
AwaitDelegate = Callable[[DriverStart, dict[str, Any], ToolExecutionContext], Awaitable[str]]

@dataclass(frozen=True, slots=True)
class HarnessSubagentRegistry:
    spawn: tuple[Any, dict[str, Any]]
    await_runs: tuple[Any, dict[str, Any]]

    @property
    def tools(self):
        return self.spawn, self.await_runs

def _trusted_parent(request: DriverStart, context: ToolExecutionContext) -> None:
    run, spec = request.run_context, request.run_spec
    if (
        run is None
        or spec is None
        or spec.persistence_level is not PersistenceLevel.DURABLE
        or (context.run_id, context.session_id, context.root_run_id)
        != (request.run_id, run.session_id, run.root_run_id)
    ):
        raise AuthorizationError(
            "actor_not_authorized", "subagent scope differs from durable parent"
        )

async def build_harness_subagent_registry(
    *, parent_request: ParentResolver,
    submit_delegate: SubmitDelegate,
    await_delegate: AwaitDelegate,
) -> HarnessSubagentRegistry:
    """Bind existing batch translation to typed durable submit/await delegates."""
    async def resolve(context):
        if context is None:
            raise AuthorizationError(
                "actor_not_authorized", "subagent tools require Harness context"
            )
        request = await parent_request(context)
        _trusted_parent(request, context)
        return request, context

    async def spawn(args, _task_id="", *, execution_context=None):
        request, context = await resolve(execution_context)
        allowed = frozenset(
            str(item) for item in request.capability_snapshot.get("tools", ())
        )
        command = build_subagent_batch_delegate(request, args, context, allowed)
        return await submit_delegate(request, command, context)

    async def await_runs(args, _task_id="", *, execution_context=None):
        request, context = await resolve(execution_context)
        return await await_delegate(request, args, context)

    return HarnessSubagentRegistry(
        (spawn, dict(_SPAWN_SCHEMA)), (await_runs, dict(_AWAIT_SCHEMA))
    )

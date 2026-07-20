"""Temporary handler bridge for the WI-2 add-only migration.

New execution always supplies ``ToolExecutionContext`` as a distinct handler
argument.  Only the production compatibility path may reconstruct the small
subset of host values historically merged into the argument dictionary.  The
bridge is deleted with the legacy merge in WI-12.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from functools import wraps
from typing import Any, Callable, Mapping, Optional

from .capabilities import ToolExecutionContext, current_tool_execution_context


def legacy_execution_context(
    args: Mapping[str, Any],
    task_id: str,
    explicit: Optional[ToolExecutionContext] = None,
) -> ToolExecutionContext:
    if explicit is not None:
        return explicit
    active = current_tool_execution_context()
    if active is not None:
        # Context OS v1 did not carry workspace/write scope.  During the
        # add-only window, enrich only those missing fields from the legacy
        # host merge; never replace its trusted session/request identity.
        if active.run_id or (active.workspace and active.write_scope_root):
            return active
        project_root = args.get("_project_root")
        write_root = args.get("_write_scope_root")
        return replace(
            active,
            workspace=(
                active.workspace
                or (str(project_root or write_root) if project_root or write_root else None)
            ),
            write_scope_root=(
                active.write_scope_root or (str(write_root) if write_root else None)
            ),
        )
    # This is the sole compatibility reader for P4-S22's historical merge.
    # Values created here are never accepted by registry.execute_call.
    session_id = str(args.get("_session_id") or args.get("session_id") or "default")
    project_root = args.get("_project_root")
    write_root = args.get("_write_scope_root")
    return ToolExecutionContext(
        scope_id="legacy",
        session_id=session_id,
        request_id=str(args.get("_request_id") or task_id or "legacy"),
        origin="legacy_adapter",
        turn_id=str(args.get("_turn_id") or task_id or "legacy"),
        workspace=str(project_root or write_root) if project_root or write_root else None,
        write_scope_root=str(write_root) if write_root else None,
        run_id=str(args.get("_run_id") or session_id),
        root_run_id=str(args.get("_root_run_id") or session_id),
        call_id=str(args.get("_call_id") or task_id),
        effect_id=str(args.get("_effect_id") or task_id),
    )


def legacy_host_service(args: Mapping[str, Any], name: str) -> Any:
    """Compatibility-only access to a live service formerly merged in args."""

    return args.get(name)


def bind_context_handler(handler: Callable[..., Any]) -> Callable[..., Any]:
    """Adapt a migrated ``execution_context=`` handler for ToolSpec."""

    if inspect.iscoroutinefunction(handler):
        @wraps(handler)
        async def async_adapter(args: dict[str, Any], context: ToolExecutionContext) -> Any:
            return await handler(args, context.call_id, execution_context=context)

        return async_adapter

    @wraps(handler)
    def sync_adapter(args: dict[str, Any], context: ToolExecutionContext) -> Any:
        return handler(args, context.call_id, execution_context=context)

    return sync_adapter


__all__ = [
    "bind_context_handler",
    "legacy_execution_context",
    "legacy_host_service",
]

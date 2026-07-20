"""Trusted host construction for the single generic ``RunContext``."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from deskpet.execution.contracts import RunContext
from deskpet.tools.capabilities import ToolExecutionContext, canonical_hash


class HostContextFactory:
    """Create immutable execution/tool context only from authenticated host data."""

    _VENUES = frozenset({"text", "code", "voice", "background", "workflow"})

    def create_run_context(
        self,
        *,
        session_id: str,
        root_run_id: str,
        request_id: str,
        turn_id: str,
        venue: str,
        capability_hash: str,
        provider_plan: Iterable[str],
        trace_id: str,
        principal_id: str,
        auth_epoch: int = 0,
        parent_run_id: str | None = None,
        workspace: str | Path | None = None,
        write_scope_root: str | Path | None = None,
        scope_hash: str = "",
    ) -> RunContext:
        if venue not in self._VENUES:
            raise ValueError(f"unsupported execution venue: {venue}")
        resolved_workspace = str(Path(workspace).resolve()) if workspace else None
        resolved_write_root = str(Path(write_scope_root).resolve()) if write_scope_root else None
        resolved_scope_hash = scope_hash or canonical_hash(
            {
                "session_id": session_id,
                "workspace": resolved_workspace,
                "write_scope_root": resolved_write_root,
                "venue": venue,
            }
        )
        return RunContext(
            session_id=session_id,
            root_run_id=root_run_id,
            parent_run_id=parent_run_id,
            request_id=request_id,
            turn_id=turn_id,
            venue=venue,
            workspace={
                "root": resolved_workspace,
                "write_scope_root": resolved_write_root,
                "scope_hash": resolved_scope_hash,
            },
            capability_hash=capability_hash,
            provider_plan={"providers": list(provider_plan)},
            trace_id=trace_id,
            principal_id=principal_id,
            auth_epoch=auth_epoch,
        )

    def create_tool_context(
        self,
        run_context: RunContext,
        *,
        run_id: str,
        call_id: str,
        effect_id: str,
        scope_id: str = "",
        origin: str = "agent",
    ) -> ToolExecutionContext:
        if not run_id or not call_id or not effect_id:
            raise ValueError("run_id, call_id, and effect_id are required")
        workspace = run_context.workspace
        root = workspace.get("root")
        write_root = workspace.get("write_scope_root")
        scope_hash = str(workspace.get("scope_hash") or "")
        providers = run_context.provider_plan.get("providers", ())
        return ToolExecutionContext(
            scope_id=scope_id or scope_hash,
            session_id=run_context.session_id,
            request_id=run_context.request_id,
            origin=origin,
            root_run_id=run_context.root_run_id,
            parent_run_id=run_context.parent_run_id,
            turn_id=run_context.turn_id,
            venue=run_context.venue,
            workspace=str(root) if root is not None else None,
            write_scope_root=str(write_root) if write_root is not None else None,
            capability_hash=run_context.capability_hash,
            scope_hash=scope_hash,
            provider_plan=tuple(str(item) for item in providers),
            run_id=run_id,
            call_id=call_id,
            effect_id=effect_id,
            trace_id=run_context.trace_id,
        )


__all__ = ["HostContextFactory", "RunContext"]

"""Trusted run-context construction.

Only venue adapters may hold a :class:`HostContextFactory`.  A model-facing
request is intentionally not accepted by this module, which makes it
impossible to smuggle host control fields through tool arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from deskpet.tools.capabilities import ToolExecutionContext, canonical_hash


@dataclass(frozen=True)
class RunContext:
    session_id: str
    run_id: str
    root_run_id: str
    parent_run_id: Optional[str]
    request_id: str
    turn_id: str
    venue: str
    workspace: Optional[str]
    write_scope_root: Optional[str]
    capability_hash: str
    scope_hash: str
    provider_plan: tuple[str, ...]
    trace_id: str

    def for_tool_call(
        self,
        *,
        call_id: str,
        effect_id: str,
        scope_id: str = "",
        origin: str = "agent",
    ) -> ToolExecutionContext:
        if not call_id:
            raise ValueError("call_id is required")
        return ToolExecutionContext(
            scope_id=scope_id or self.scope_hash,
            session_id=self.session_id,
            request_id=self.request_id,
            origin=origin,
            root_run_id=self.root_run_id,
            parent_run_id=self.parent_run_id,
            turn_id=self.turn_id,
            venue=self.venue,
            workspace=self.workspace,
            write_scope_root=self.write_scope_root,
            capability_hash=self.capability_hash,
            scope_hash=self.scope_hash,
            provider_plan=self.provider_plan,
            run_id=self.run_id,
            call_id=call_id,
            effect_id=effect_id,
            trace_id=self.trace_id,
        )


class HostContextFactory:
    """Create immutable context only from authenticated host arguments."""

    _VENUES = frozenset({"text", "code", "voice", "background", "workflow"})

    def create_run_context(
        self,
        *,
        session_id: str,
        run_id: str,
        root_run_id: str,
        request_id: str,
        turn_id: str,
        venue: str,
        capability_hash: str,
        provider_plan: Iterable[str],
        trace_id: str,
        parent_run_id: Optional[str] = None,
        workspace: str | Path | None = None,
        write_scope_root: str | Path | None = None,
        scope_hash: str = "",
    ) -> RunContext:
        required = {
            "session_id": session_id,
            "run_id": run_id,
            "root_run_id": root_run_id,
            "request_id": request_id,
            "turn_id": turn_id,
            "venue": venue,
            "capability_hash": capability_hash,
            "trace_id": trace_id,
        }
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            raise ValueError(f"trusted host context missing: {', '.join(missing)}")
        if venue not in self._VENUES:
            raise ValueError(f"unsupported execution venue: {venue}")
        resolved_workspace = str(Path(workspace).resolve()) if workspace else None
        resolved_write_root = (
            str(Path(write_scope_root).resolve()) if write_scope_root else None
        )
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
            run_id=run_id,
            root_run_id=root_run_id,
            parent_run_id=parent_run_id,
            request_id=request_id,
            turn_id=turn_id,
            venue=venue,
            workspace=resolved_workspace,
            write_scope_root=resolved_write_root,
            capability_hash=capability_hash,
            scope_hash=resolved_scope_hash,
            provider_plan=tuple(provider_plan),
            trace_id=trace_id,
        )


__all__ = ["HostContextFactory", "RunContext"]

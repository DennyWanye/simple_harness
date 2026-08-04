"""Trusted host construction for the single generic ``RunContext``."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Iterable

from deskpet.execution.contracts import (
    ActorContext,
    AuthorizationError,
    RunContext,
    RunRecord,
    RunRef,
)
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
        provider_bindings: Iterable[tuple[object, ...]] = (),
        auth_epoch: int = 0,
        parent_run_id: str | None = None,
        workspace: str | Path | None = None,
        write_scope_root: str | Path | None = None,
        scope_hash: str = "",
        owner_key: str | None = None,
        profile_generation: int = 0,
        binding_epoch: int = 0,
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
        provider_ids = tuple(str(item).strip() for item in provider_plan)
        exact_bindings = tuple(tuple(item) for item in provider_bindings)
        if any(
            len(item) not in (2, 5)
            or not str(item[0]).strip()
            or not str(item[1]).strip()
            for item in exact_bindings
        ):
            raise ValueError("provider bindings require provider_id and model_id")
        if exact_bindings and tuple(str(item[0]).strip() for item in exact_bindings) != provider_ids:
            raise ValueError("provider bindings must match the provider plan order")
        frozen_provider_plan: dict[str, object] = {
            "providers": list(provider_ids),
        }
        if exact_bindings:
            frozen_provider_plan["bindings"] = []
            for item in exact_bindings:
                binding = {
                    "provider_id": str(item[0]).strip(),
                    "model_id": str(item[1]).strip(),
                }
                if len(item) == 5:
                    binding.update(
                        {
                            "incarnation_id": str(item[2]),
                            "config_revision": int(item[3]),
                            "binding_epoch": int(item[4]),
                        }
                    )
                frozen_provider_plan["bindings"].append(binding)
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
            provider_plan=frozen_provider_plan,
            trace_id=trace_id,
            principal_id=principal_id,
            auth_epoch=auth_epoch,
            owner_key=owner_key,
            profile_generation=profile_generation,
            binding_epoch=binding_epoch,
        )

    def create_tool_context(
        self,
        run_context: RunContext,
        *,
        run_id: str,
        command_id: str = "",
        call_id: str,
        effect_id: str,
        scope_id: str = "",
        origin: str = "agent",
        capability_snapshot_ref: str = "",
        active_skill_scope_ids: tuple[str, ...] = (),
        effective_skill_tool_ref_hashes: tuple[str, ...] = (),
        effective_skill_tool_refs_hash: str = "",
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
            command_id=command_id,
            call_id=call_id,
            effect_id=effect_id,
            trace_id=run_context.trace_id,
            owner_key=run_context.owner_key or "",
            profile_generation=run_context.profile_generation,
            binding_epoch=run_context.binding_epoch,
            capability_snapshot_ref=capability_snapshot_ref,
            active_skill_scope_ids=tuple(active_skill_scope_ids),
            effective_skill_tool_ref_hashes=tuple(
                effective_skill_tool_ref_hashes
            ),
            effective_skill_tool_refs_hash=effective_skill_tool_refs_hash,
        )


def authorize_live(ref: RunRef, actor: ActorContext, record: RunRecord) -> None:
    """Apply the same ownership checks before serving an in-memory Run."""
    context = record.context
    if (
        ref.expected_session_id != context.session_id
        or actor.session_id != context.session_id
    ):
        raise AuthorizationError(
            "actor_not_authorized", "actor does not own the run session"
        )
    if actor.internal:
        if (
            actor.root_run_id != context.root_run_id
            or actor.capability_hash != context.capability_hash
            or actor.expires_at is None
            or actor.expires_at <= time.time()
        ):
            raise AuthorizationError(
                "actor_not_authorized", "internal authority is stale"
            )
        return
    if (
        actor.principal_id != context.principal_id
        or actor.auth_epoch != context.auth_epoch
        or (
            actor.root_run_id is not None
            and actor.root_run_id != context.root_run_id
        )
    ):
        raise AuthorizationError(
            "actor_not_authorized",
            "actor principal, authentication epoch, or run root differs",
        )


__all__ = ["HostContextFactory", "RunContext", "authorize_live"]

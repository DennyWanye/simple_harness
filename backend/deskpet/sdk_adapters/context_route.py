# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5a five-route ``context_route`` tool and read-only ``task_scope_search``.

Host adjudication over the model's route proposal:

- ``direct_standalone`` — standalone receipt, no TaskScope, no Memory query.
- ``memory_standalone`` — stable failure until the Task 5 recall lane lands
  (never a Noop/fake receipt).
- ``continue_active``   — exact current active scope (latest durable
  ROUTED_TASK decision) revalidated against the live binding head.
- ``resume_existing``   — requires an exact ``task_scope_id`` (search hits
  never authorize); exact open returns the bounded ResumePackage inside the
  tool result so the same Run can continue with it.
- ``create_new``        — idempotent scope creation + workspace binding.

A successful result is ``{"context_route_receipt": receipt.to_json(), ...}``;
every non-commit outcome is a stable ``{"ok": false, "error": {...}}`` failure
(the SDK unconditionally decodes successful CONTEXT_CONTROL results).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from simple_harness.execution.context_authority import ContextRouteReceipt
from simple_harness.runtime.task_scope_protocol import TaskScopeRoute

from deskpet.sdk_adapters.context_authority import (
    ContextRouteLedgerStore,
    canonical_sha256,
)

ROUTES = (
    "direct_standalone",
    "memory_standalone",
    "continue_active",
    "resume_existing",
    "create_new",
)

# The SDK bounds tool arguments but not tool results; the Host bounds its own
# inputs again so an oversized model payload can never reach the S4 stores.
_MAX_TEXT = 2048

CONTEXT_ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": list(ROUTES)},
        "query": {"type": "string", "maxLength": _MAX_TEXT},
        "task_scope_id": {"type": "string", "maxLength": 128},
        "title": {"type": "string", "maxLength": 256},
        "goal": {"type": "string", "maxLength": _MAX_TEXT},
        "expected_source_hash": {"type": "string", "minLength": 64, "maxLength": 64},
    },
    "required": ["route"],
    "additionalProperties": False,
}

TASK_SCOPE_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": _MAX_TEXT},
        "max_candidates": {"type": "integer", "minimum": 1, "maximum": 20},
        "cursor": {"type": "string", "maxLength": _MAX_TEXT},
    },
    "required": ["query"],
    "additionalProperties": False,
}


def _error(code: str, **detail: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": {"code": code, **detail}}
    return payload


def local_owner_auth() -> Any:
    """The single authenticated local owner (main.py control-channel template)."""

    from deskpet.memory.human_memory_service import AuthenticatedHostSnapshot

    return AuthenticatedHostSnapshot(
        subject="deskpet-local-owner-v1",
        principal_id="local-control-channel",
        authority_ref="host:validated-control-channel:v1",
    )


class ContextRouteToolService:
    """Adjudicate route proposals against the S4 authority surfaces."""

    def __init__(
        self,
        *,
        service_factory_getter: Any,
        binding_store_factory: Any,
        binding_append_getter: Any,
        ledger: ContextRouteLedgerStore,
        tool_context_getter: Any,
        auth_factory: Any = local_owner_auth,
        recall_executor: Any = None,
    ) -> None:
        self._service_factory_getter = service_factory_getter
        self._binding_store_factory = binding_store_factory
        self._binding_append_getter = binding_append_getter
        self._ledger = ledger
        self._tool_context_getter = tool_context_getter
        self._auth_factory = auth_factory
        self._recall_executor = recall_executor

    # -- shared -----------------------------------------------------------

    def _bind_service(self) -> Any:
        factory = self._service_factory_getter()
        if factory is None:
            raise _CompositionUnavailable()
        return factory.bind(self._auth_factory())

    def _identity(self) -> tuple[str, str, str, int]:
        context = self._tool_context_getter()
        envelope = context.task_execution_envelope
        if envelope is None:
            raise _CompositionUnavailable("context_route_envelope_missing")
        return (
            context.run_id.value,
            envelope.raw_call_id,
            context.effect_id.value,
            max(1, int(envelope.turn_ordinal)),
        )

    async def _record(
        self,
        *,
        run_id: str,
        raw_call_id: str,
        effect_id: str,
        proposal: Mapping[str, Any],
        verdict: str,
        decision_id: str | None,
        detail: Mapping[str, Any],
    ) -> None:
        await self._ledger.record_tool_invocation(
            sdk_run_id=run_id,
            raw_call_id=raw_call_id,
            effect_id=effect_id,
            proposal=proposal,
            verdict=verdict,
            decision_id=decision_id,
            detail=detail,
        )

    async def _commit_receipt(
        self,
        *,
        run_id: str,
        raw_call_id: str,
        effect_id: str,
        turn_ordinal: int,
        route: TaskScopeRoute,
        proposal: Mapping[str, Any],
        task_scope_id: str | None = None,
        binding: Mapping[str, Any] | None = None,
        extras: Mapping[str, Any] | None = None,
        recall_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        receipt = ContextRouteReceipt(
            receipt_id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"simple-harness:context-route:{run_id}:{effect_id}",
                )
            ),
            run_id=run_id,
            raw_call_id=raw_call_id,
            effect_id=effect_id,
            route=route,
            task_scope_id=task_scope_id,
            recall_refs=recall_refs,
            binding_set_revision=(
                None if binding is None else int(binding["binding_set_revision"])
            ),
            binding_set_receipt_id=(
                None if binding is None else str(binding["binding_set_receipt_id"])
            ),
            binding_set_receipt_hash=(
                None if binding is None else str(binding["binding_set_receipt_hash"])
            ),
        )
        await self._ledger.record_route_decision(
            receipt=receipt,
            provider_turn_ordinal=turn_ordinal,
            origin="context_tool",
            idempotency_key=effect_id,
        )
        await self._record(
            run_id=run_id,
            raw_call_id=raw_call_id,
            effect_id=effect_id,
            proposal=proposal,
            verdict="accepted",
            decision_id=f"route-decision:{run_id}:{effect_id}",
            detail={"route": route.value, "task_scope_id": task_scope_id},
        )
        result: dict[str, Any] = {"context_route_receipt": receipt.to_json()}
        if extras:
            result.update(dict(extras))
        return result

    async def _binding_head(self, task_scope_id: str) -> dict[str, Any]:
        store = self._binding_store_factory()
        receipt = await store.current_receipt(task_scope_id)
        return {
            "binding_set_revision": int(receipt.binding_set_revision),
            "binding_set_receipt_id": str(receipt.receipt_id),
            "binding_set_receipt_hash": str(receipt.receipt_hash),
        }

    # -- context_route ----------------------------------------------------

    async def handle_context_route(self, arguments: Mapping[str, Any]) -> Any:
        try:
            run_id, raw_call_id, effect_id, turn_ordinal = self._identity()
        except _CompositionUnavailable as exc:
            return _error(exc.code)
        proposal = {
            key: value
            for key, value in dict(arguments).items()
            if key != "deskpet_public_progress"
        }
        route_value = str(proposal.get("route") or "")
        if route_value not in ROUTES:
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_route_invalid",
            )
        try:
            if route_value == "direct_standalone":
                return await self._commit_receipt(
                    run_id=run_id,
                    raw_call_id=raw_call_id,
                    effect_id=effect_id,
                    turn_ordinal=turn_ordinal,
                    route=TaskScopeRoute.DIRECT_STANDALONE,
                    proposal=proposal,
                )
            if route_value == "memory_standalone":
                return await self._memory_standalone(
                    run_id, raw_call_id, effect_id, turn_ordinal, proposal
                )
            if route_value == "continue_active":
                return await self._continue_active(
                    run_id, raw_call_id, effect_id, turn_ordinal, proposal
                )
            if route_value == "resume_existing":
                return await self._resume_existing(
                    run_id, raw_call_id, effect_id, turn_ordinal, proposal
                )
            return await self._create_new(
                run_id, raw_call_id, effect_id, turn_ordinal, proposal
            )
        except _CompositionUnavailable as exc:
            return _error(exc.code)
        except Exception as exc:  # noqa: BLE001 - stable fail-closed surface
            code = str(getattr(exc, "code", "") or "context_route_adjudication_failed")
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal, code,
                message=str(exc)[:512],
            )

    async def _reject(
        self,
        run_id: str,
        raw_call_id: str,
        effect_id: str,
        proposal: Mapping[str, Any],
        code: str,
        **detail: Any,
    ) -> dict[str, Any]:
        await self._record(
            run_id=run_id,
            raw_call_id=raw_call_id,
            effect_id=effect_id,
            proposal=proposal,
            verdict="rejected",
            decision_id=None,
            detail={"code": code, **detail},
        )
        return _error(code, **detail)


    async def _memory_standalone(
        self,
        run_id: str,
        raw_call_id: str,
        effect_id: str,
        turn_ordinal: int,
        proposal: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self._recall_executor is None:
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_memory_standalone_unavailable",
            )
        query = str(proposal.get("query") or "").strip()
        if not query:
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_recall_query_required",
            )
        from deskpet.memory.human_memory_v7 import project_recall_fragments

        execution = await self._recall_executor(
            query=query, run_id=run_id, turn_ordinal=turn_ordinal
        )
        fragments = project_recall_fragments(execution)
        refs = tuple(dict.fromkeys(str(f["ref"]) for f in fragments))
        return await self._commit_receipt(
            run_id=run_id,
            raw_call_id=raw_call_id,
            effect_id=effect_id,
            turn_ordinal=turn_ordinal,
            route=TaskScopeRoute.MEMORY_STANDALONE,
            proposal=proposal,
            recall_refs=refs,
            extras={
                "fragments": list(fragments),
                "degradation_codes": list(execution.degradation_codes),
                "truncated": bool(execution.result.truncated),
            },
        )

    async def _continue_active(
        self,
        run_id: str,
        raw_call_id: str,
        effect_id: str,
        turn_ordinal: int,
        proposal: Mapping[str, Any],
    ) -> dict[str, Any]:
        active = await self._ledger.latest_task_route_decision()
        if active is None or not active.get("task_scope_id"):
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_no_active_task_scope",
            )
        active_scope = str(active["task_scope_id"])
        proposed = proposal.get("task_scope_id")
        if proposed is not None and str(proposed) != active_scope:
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_active_scope_mismatch",
                active_task_scope_id=active_scope,
            )
        binding = await self._binding_head(active_scope)
        return await self._commit_receipt(
            run_id=run_id,
            raw_call_id=raw_call_id,
            effect_id=effect_id,
            turn_ordinal=turn_ordinal,
            route=TaskScopeRoute.CONTINUE_ACTIVE,
            proposal=proposal,
            task_scope_id=active_scope,
            binding=binding,
        )

    async def _resume_existing(
        self,
        run_id: str,
        raw_call_id: str,
        effect_id: str,
        turn_ordinal: int,
        proposal: Mapping[str, Any],
    ) -> dict[str, Any]:
        scope = proposal.get("task_scope_id")
        if not scope:
            # Search hits never authorize; an exact ID is mandatory here.
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_exact_task_scope_required",
                guidance="Call task_scope_search first, confirm one candidate, "
                "then pass its exact task_scope_id.",
            )
        pinned = proposal.get("expected_source_hash")
        if pinned is not None and (
            len(str(pinned)) != 64
            or any(ch not in "0123456789abcdef" for ch in str(pinned))
        ):
            # A miscopied pin is the most common LLM payload slip: reject with
            # explicit guidance instead of surfacing a misleading stale error.
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_expected_source_hash_malformed",
                guidance="expected_source_hash is optional; omit it, or copy "
                "the candidate's source_hash exactly (64 lowercase hex).",
            )
        service = self._bind_service()
        from deskpet.memory.human_memory_service import OpenTaskScopeRequest

        opened = await service.open_task_scope(
            OpenTaskScopeRequest(
                scope_ref=str(scope),
                expected_source_hash=(
                    str(proposal["expected_source_hash"])
                    if proposal.get("expected_source_hash")
                    else None
                ),
            )
        )
        binding = await self._binding_head(str(scope))
        resume_package = opened["resume_package"]
        package_revision = resume_package.get("binding_set_revision")
        package_hash = resume_package.get("binding_receipt_hash")
        if package_revision is not None and int(package_revision) >= 1:
            if int(package_revision) != binding["binding_set_revision"] or (
                package_hash and str(package_hash) != binding["binding_set_receipt_hash"]
            ):
                return await self._reject(
                    run_id, raw_call_id, effect_id, proposal,
                    "context_route_binding_lineage_stale",
                )
        return await self._commit_receipt(
            run_id=run_id,
            raw_call_id=raw_call_id,
            effect_id=effect_id,
            turn_ordinal=turn_ordinal,
            route=TaskScopeRoute.RESUME_EXISTING,
            proposal=proposal,
            task_scope_id=str(scope),
            binding=binding,
            extras={
                "resume_package": resume_package,
                "resume_sha256": opened["resume_sha256"],
                "drift_report": opened.get("drift_report"),
            },
        )

    async def _create_new(
        self,
        run_id: str,
        raw_call_id: str,
        effect_id: str,
        turn_ordinal: int,
        proposal: Mapping[str, Any],
    ) -> dict[str, Any]:
        title = str(proposal.get("title") or "").strip()
        if not title:
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_title_required",
            )
        service = self._bind_service()
        from deskpet.memory.human_memory_service import CreateTaskScopeRequest

        created = await service.create_task_scope(
            CreateTaskScopeRequest(
                fixture_key=f"chat:{canonical_sha256({'title': title})[:16]}",
                title=title,
                goal=str(proposal.get("goal") or "").strip() or title,
                idempotency_key=f"context-route:{run_id}:{effect_id}",
            )
        )
        scope = str(created["scope_ref"])
        binding_append = self._binding_append_getter()
        if binding_append is None:
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_binding_authority_unavailable",
                task_scope_id=scope,
            )
        store = self._binding_store_factory()
        root = store.configured_root()
        if not root:
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_workspace_root_not_configured",
                task_scope_id=scope,
            )
        outcome = await binding_append.append_binding(
            subject=self._auth_factory().subject,
            task_scope_id=scope,
            root=str(root),
            idempotency_key=f"context-route:{run_id}:{effect_id}",
            interaction_evidence_id=f"context-route:{run_id}:{effect_id}",
            interaction_evidence_hash=canonical_sha256(dict(proposal)),
        )
        if str(outcome.get("status", "")) == "authorization_required":
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_binding_authorization_required",
                task_scope_id=scope,
            )
        binding = await self._binding_head(scope)
        return await self._commit_receipt(
            run_id=run_id,
            raw_call_id=raw_call_id,
            effect_id=effect_id,
            turn_ordinal=turn_ordinal,
            route=TaskScopeRoute.CREATE_NEW,
            proposal=proposal,
            task_scope_id=scope,
            binding=binding,
            extras={"created": dict(created)},
        )

    # -- task_scope_search ------------------------------------------------

    async def handle_task_scope_search(self, arguments: Mapping[str, Any]) -> Any:
        query = str(arguments.get("query") or "").strip()
        if not query or len(query) > _MAX_TEXT:
            return _error("task_scope_search_query_invalid")
        try:
            service = self._bind_service()
        except _CompositionUnavailable as exc:
            return _error(exc.code)
        from deskpet.memory.human_memory_service import SearchTaskScopesRequest

        raw_max = arguments.get("max_candidates")
        try:
            result = await service.search_task_scopes(
                SearchTaskScopesRequest(
                    query=query,
                    max_candidates=int(raw_max) if raw_max is not None else 8,
                    cursor=(
                        str(arguments["cursor"])
                        if arguments.get("cursor")
                        else None
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - stable fail-closed surface
            code = str(getattr(exc, "code", "") or "task_scope_search_failed")
            return _error(code)
        return {
            "candidates": list(result["candidates"]),
            "next_cursor": result.get("next_cursor"),
            "receipt_hash": result.get("receipt_hash"),
            "note": "Candidates are permission-first hits only; they grant no "
            "authority. Confirm one and pass its exact task_scope_id to "
            "context_route(route=resume_existing).",
        }


class _CompositionUnavailable(RuntimeError):
    def __init__(self, code: str = "context_route_composition_unavailable") -> None:
        super().__init__(code)
        self.code = code


__all__ = [
    "CONTEXT_ROUTE_SCHEMA",
    "TASK_SCOPE_SEARCH_SCHEMA",
    "ContextRouteToolService",
    "local_owner_auth",
]

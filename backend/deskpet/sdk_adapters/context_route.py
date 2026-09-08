# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5a five-route ``context_route`` tool and read-only ``task_scope_search``.

Host adjudication over the model's route proposal:

- ``direct_standalone`` — standalone receipt, no TaskScope, no Memory query.
- ``memory_standalone`` — explicit long-term and/or short-horizon selection,
  with one typed RecallPlan budget and current source checks.
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

import asyncio
import logging
import uuid
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from simple_harness.execution.context_authority import ContextRouteReceipt
from simple_harness.runtime.task_scope_protocol import TaskScopeRoute

from deskpet.sdk_adapters.context_authority import (
    ContextRouteLedgerStore,
    canonical_sha256,
)
from deskpet.memory.recall_selection import (
    MEMORY_TYPE_SELECTION_POLICY, REQUESTABLE_MEMORY_TYPES, parse_recall_selection,
    selection_policy_departures,
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
_AUDIT_CANCEL_SECONDS = 2.0
_LOG = logging.getLogger(__name__)
_WORKSPACE_REUSE_OMIT = (
    "reuse_workspace_of is only valid for create_new with an exact verified task ID "
    "and source hash. For memory_standalone, omit both reuse_workspace_of and "
    "expected_source_hash entirely, or set them to JSON null. Do not supply "
    "placeholder strings, whitespace, or a fabricated hash."
)
_WORKSPACE_REUSE_NEW_RUN = (
    "This Run is already bound to a task. End this turn without switching scopes. "
    "In the next Run, use task_scope_search to obtain the completed task's current source_hash, "
    "then create_new with reuse_workspace_of before binding any other task. "
    "This request did not reopen the completed task or edit its files."
)

_PROCEDURE_HINT = {
    "reason": "typed_recall_returns_only_applicable_procedures",
    "next": "procedure_discover",
    "message": "Typed recall only returns a Procedure that is already bound/applicable here; "
               "a saved-but-unbound workflow never appears in these fragments. Their absence does not "
               "mean no such workflow was saved. Call procedure_discover with the workflow name to list "
               "the actual saved candidates before concluding anything or drafting from scratch.",
}

CONTEXT_ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": list(ROUTES)},
        "query": {"type": "string", "maxLength": _MAX_TEXT},
        "memory_types": {
            "type": "array", "minItems": 0, "maxItems": 4,
            "items": {"type": "string", "enum": list(REQUESTABLE_MEMORY_TYPES)},
            "description": ("Required for memory_standalone. " + MEMORY_TYPE_SELECTION_POLICY
                            + " An empty list is valid only with include_short_horizon=true. "
                              "Selection grants no permission to disclose or execute."),
        },
        "include_short_horizon": {
            "type": "boolean",
            "description": "For memory_standalone, request relevant prior conversation groups outside the current recent context. Defaults to false. Short and long-term results share one Host budget and current source checks.",
        },
        "task_scope_id": {"type": "string", "maxLength": 128},
        "reuse_workspace_of": {
            "type": ["string", "null"], "minLength": 1, "maxLength": 128,
            "description": "Only create_new: explicitly bind the new active task to the completed task's existing workspace. Copy its exact task_scope_id and expected_source_hash from public search/resume. Requires one verified root and a new binding grant; never reopens the old task. For a new separate workspace or memory_standalone, omit this field and expected_source_hash or set them to JSON null; never use placeholder strings or whitespace.",
        },
        "title": {"type": "string", "maxLength": 256},
        "goal": {"type": "string", "maxLength": _MAX_TEXT},
        "expected_source_hash": {
            "type": ["string", "null"], "minLength": 64, "maxLength": 64,
            "description": "Exact source_hash from public task search/resume for resume_existing or create_new workspace reuse. Otherwise omit this field or set it to JSON null; never fabricate a hash or use placeholders.",
        },
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
        scope_disclosure_reader: Any = None,
        producer_dependencies_reader: Any = None,
        typed_use_authority: Any = None,
    ) -> None:
        self._service_factory_getter = service_factory_getter
        self._binding_store_factory = binding_store_factory
        self._binding_append_getter = binding_append_getter
        self._ledger = ledger
        self._tool_context_getter = tool_context_getter
        self._auth_factory = auth_factory
        self._recall_executor = recall_executor
        self._scope_disclosure_reader = scope_disclosure_reader
        self._producer_dependencies_reader = producer_dependencies_reader
        self._typed_use_authority = typed_use_authority

    # -- shared -----------------------------------------------------------

    def _bind_service(self, *, binding_append: Any = None) -> Any:
        factory = self._service_factory_getter()
        if factory is None:
            raise _CompositionUnavailable()
        if binding_append is not None:
            return factory.bind(self._auth_factory(), binding_append=binding_append)
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
        wait_for_lock: bool = True,
    ) -> None:
        if proposal.get("route") == "memory_standalone":
            # Keep only the bounded enum/boolean projection. Raw query, invalid
            # model values and provider exception text are not audit metadata.
            try:
                selected, short = parse_recall_selection(
                    proposal.get("memory_types"), proposal.get("include_short_horizon", False),
                )
                selection = {"origin": "model_proposal",
                             "requested_memory_types": list(selected),
                             "include_short_horizon": short,
                             # Advisory only: recorded so the extra-type rate has
                             # a Host-side trace. Never gates or rewrites recall.
                             "selection_policy_departures":
                                 list(selection_policy_departures(selected))}
            except ValueError as exc:
                selection = {"origin": "model_proposal", "selection_status": "invalid",
                             "selection_error": str(exc)}
            detail = {**detail, "recall_selection": selection}
        await self._ledger.record_tool_invocation(
            sdk_run_id=run_id,
            raw_call_id=raw_call_id,
            effect_id=effect_id,
            proposal=proposal,
            verdict=verdict,
            decision_id=decision_id,
            detail=detail,
            wait_for_lock=wait_for_lock,
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
        recall_types: tuple[str, ...] = (),
        recall_short_horizon: bool | None = None,
        typed_carrier: Mapping[str, Any] | None = None,
        recall_conflict: Mapping[str, Any] | None = None,
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
            **({"require_unbound_run": True} if route is TaskScopeRoute.CREATE_NEW
               and proposal.get("reuse_workspace_of") is not None else {}),
        )
        result: dict[str, Any] = {"context_route_receipt": receipt.to_json()}
        if extras:
            result.update(dict(extras))
        await self._record(
            run_id=run_id,
            raw_call_id=raw_call_id,
            effect_id=effect_id,
            proposal=proposal,
            verdict="accepted",
            decision_id=f"route-decision:{run_id}:{effect_id}",
            detail={
                "route": route.value, "task_scope_id": task_scope_id,
                **({"typed_carrier": dict(typed_carrier),
                    "public_result_hash": canonical_sha256(result)} if typed_carrier is not None else {}),
                **({"recall_selection": {
                    "origin": "model_proposal",
                    "requested_memory_types": list(recall_types),
                    "include_short_horizon": bool(recall_short_horizon),
                    "selection_policy_departures":
                        list(selection_policy_departures(recall_types or ())),
                }} if recall_types or recall_short_horizon is not None else {}),
                # Stable Host reason code for the receipt/audit. Record only the
                # bounded conflict identity (group id + exact revisions), never
                # the candidate payloads: the audit needs attribution, not the
                # user's contested values a second time.
                **({"recall_conflict": {
                    "reason": recall_conflict["reason"],
                    "conflict_status": recall_conflict["conflict_status"],
                    "groups": [{
                        "conflict_group_id": group["conflict_group_id"],
                        "memory_type": group["memory_type"],
                        "revisions": [candidate["revision"] for candidate in group["candidates"]],
                    } for group in recall_conflict["groups"]],
                }} if recall_conflict is not None else {}),
            },
        )
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
        if proposal.get("reuse_workspace_of") is not None and route_value != "create_new":
            return await self._reject(run_id, raw_call_id, effect_id, proposal,
                                      "context_route_workspace_reuse_requires_create_new",
                                      message=_WORKSPACE_REUSE_OMIT)
        if proposal.get("expected_source_hash") is not None and not (
            route_value == "resume_existing" or (
                route_value == "create_new" and proposal.get("reuse_workspace_of") is not None
            )
        ):
            return await self._reject(run_id, raw_call_id, effect_id, proposal,
                "context_route_source_hash_not_applicable",
                message="expected_source_hash requires resume_existing or exact create_new workspace reuse. "
                        "Otherwise omit expected_source_hash or set it to JSON null; never fabricate a hash.")
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
            return await self._reject(run_id, raw_call_id, effect_id, proposal, exc.code)
        except Exception as exc:  # noqa: BLE001 - stable fail-closed surface
            if route_value == "memory_standalone":
                # Exception messages/codes can contain provider or source text.
                code = ("context_route_recall_timeout" if isinstance(exc, TimeoutError)
                        else "context_route_adjudication_failed")
                return await self._reject(run_id, raw_call_id, effect_id, proposal, code)
            code = str(getattr(exc, "code", "") or "context_route_adjudication_failed")
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal, code,
                message=(_WORKSPACE_REUSE_NEW_RUN if code == "context_route_workspace_reuse_requires_new_run"
                         else str(exc)[:512]),
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
        try:
            memory_types, include_short_horizon = parse_recall_selection(
                proposal.get("memory_types"), proposal.get("include_short_horizon", False),
            )
        except ValueError as exc:
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal, str(exc),
            )
        from deskpet.memory.human_memory_v7 import (
            project_contested_confirmation, project_recall_fragments,
        )

        admitted = None
        if self._typed_use_authority is not None:
            admitted = await self._typed_use_authority.admitted_tool_context(self._tool_context_getter())
        try:
            execution = await self._recall_executor(
                query=query, run_id=run_id, turn_ordinal=turn_ordinal,
                memory_types=memory_types,
                include_short_horizon=include_short_horizon,
                **({"admitted_context": admitted} if admitted is not None else {}),
            )
        except asyncio.CancelledError:
            # This boundary precedes route commit; cancellation is not a
            # successful route or a normal tool return. Do not wait for a DB
            # writer lock. The deadline requests cancellation; wait_for still
            # awaits owned rollback/close, so it is not a wall-clock hard cap.
            # Always propagate cancellation, including if storage fails.
            try:
                await asyncio.wait_for(self._record(
                    run_id=run_id, raw_call_id=raw_call_id, effect_id=effect_id,
                    proposal=proposal, verdict="rejected", decision_id=None,
                    detail={"code": "context_route_recall_cancelled"},
                    wait_for_lock=False,
                ), timeout=_AUDIT_CANCEL_SECONDS)
            except (Exception, asyncio.CancelledError):
                _LOG.warning("context_route_cancel_audit_unavailable run_id=%s effect_id=%s",
                             run_id, effect_id)
            raise
        fragments = project_recall_fragments(execution)
        # HM-S3: a contested head never reaches ``result.items``; without this the
        # whole recall reads to the model as "nothing was ever saved" and it
        # answers a dependent execution question from the last value it saw.
        conflict = project_contested_confirmation(execution)
        carrier = None
        if self._typed_use_authority is not None:
            carrier = await self._typed_use_authority.build_carrier(
                execution=execution, projected=fragments, admitted=admitted, effect_id=effect_id,
            )
        refs = tuple(dict.fromkeys(str(f["ref"]) for f in fragments))
        return await self._commit_receipt(
            run_id=run_id,
            raw_call_id=raw_call_id,
            effect_id=effect_id,
            turn_ordinal=turn_ordinal,
            route=TaskScopeRoute.MEMORY_STANDALONE,
            proposal=proposal,
            recall_refs=refs,
            recall_types=memory_types,
            recall_short_horizon=include_short_horizon,
            typed_carrier=carrier,
            recall_conflict=conflict,
            extras={
                "fragments": list(fragments),
                "degradation_codes": list(execution.degradation_codes),
                "truncated": bool(execution.result.truncated),
                **({"conflict_notice": conflict} if conflict is not None else {}),
                # By design typed recall withholds unbound Procedures. Silence
                # reads to the model as "nothing was ever saved", so point at
                # the discovery surface instead. This rides in the same extras
                # the receipt hash and typed-use public_result_hash cover; the
                # ContextRouteReceipt itself is untouched.
                **({"procedure_hint": dict(_PROCEDURE_HINT)}
                   if "procedure" in memory_types and not any(
                       fragment["memory_type"] == "procedure" for fragment in fragments)
                   else {}),
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
        if (
            package_revision is not None
            and int(package_revision) >= 1
            and (
                int(package_revision) != binding["binding_set_revision"]
                or (package_hash and str(package_hash) != binding["binding_set_receipt_hash"])
            )
        ):
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_binding_lineage_stale",
            )
        if self._scope_disclosure_reader is None:
            return await self._reject(run_id, raw_call_id, effect_id, proposal, "scope_disclosure_reader_missing")
        resume_package = await self._scope_disclosure_reader(run_id, resume_package, effect_id)
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
                "resume_sha256": canonical_sha256(resume_package),
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
        service = self._bind_service(binding_append=self._binding_append_getter())
        from deskpet.memory.human_memory_service import AppendBindingRequest, CreateTaskScopeRequest

        continuation = None
        if proposal.get("reuse_workspace_of") is not None:
            bound = await self._ledger.latest_route_decision_for_run(run_id, task_only=True)
            envelope = self._tool_context_getter().task_execution_envelope
            if bound is not None or envelope.task_scope_id is not None:
                return await self._reject(run_id, raw_call_id, effect_id, proposal,
                    "context_route_workspace_reuse_requires_new_run",
                    message=_WORKSPACE_REUSE_NEW_RUN)
            from deskpet.sdk_adapters.workspace_continuation import resolve_workspace_continuation
            continuation = await resolve_workspace_continuation(
                service=service, binding_store=self._binding_store_factory(),
                disclosure_reader=self._scope_disclosure_reader, run_id=run_id,
                effect_id=effect_id, proposal=proposal)
        producer_dependencies = (None if self._producer_dependencies_reader is None
            else await self._producer_dependencies_reader(run_id))
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
        # Normal creation gets its own stable direct child. Explicit reuse
        # selects the exact verified old root; model text never supplies a path.
        task_root = (Path(root.canonical_path) / f"task-{scope}" if continuation is None
                     else Path(continuation.root.canonical_path))
        outcome = await service.append_binding(AppendBindingRequest(
            scope_ref=scope,
            root=str(task_root),
            idempotency_key=f"context-route:{run_id}:{effect_id}",
            expected_filesystem_identity_hash=(None if continuation is None else
                continuation.root.filesystem_identity.identity_hash),
        ))
        if str(outcome.get("status", "")) == "authorization_required":
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_binding_authorization_required",
                task_scope_id=scope,
                binding_challenge=dict(outcome),
                required_action="binding.manual.decide",
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
            extras={"created": dict(created), "producer_dependencies": producer_dependencies,
                    **({} if continuation is None else {"workspace_source": dict(continuation.source)})},
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
        from deskpet.memory.human_memory_service import OpenTaskScopeRequest
        if self._scope_disclosure_reader is None:
            return _error("scope_disclosure_reader_missing")
        run_id, _, effect_id, _ = self._identity()
        candidates = []
        for item in result["candidates"]:
            scope_id = item["scope_ref"]
            opened = await service.open_task_scope(OpenTaskScopeRequest(scope_ref=scope_id,
                expected_source_hash=item["source_hash"]))
            package = await self._scope_disclosure_reader(run_id, opened["resume_package"], effect_id)
            candidates.append({"task_scope_id": scope_id, "source_id": package["source_id"],
                "source_hash": package["source_hash"], "scope_disclosure": package})
        payload: dict[str, Any] = {
            "candidates": candidates,
            "next_cursor": result.get("next_cursor"),
            "receipt_hash": result.get("receipt_hash"),
            "note": "Candidates grant no authority. resume_existing reads history/status and does not reopen a completed task. "
            "To edit a completed task's files, copy its task_scope_id to reuse_workspace_of and source_hash to expected_source_hash "
            "in create_new with a new title/goal and explicit original-workspace "
            "binding. It must be the first task route in a new Run; do not resume the old task first. "
            "Active tasks may use resume_existing with their exact task_scope_id.",
        }
        if not candidates:
            # HM-TO-A6 incident B: a real model re-issued the same zero-hit
            # query ten times because an empty candidate list said nothing
            # about what to do next.  The Host already knows whether a current
            # active task route exists — say so, and name the exact next call.
            payload["next_action"] = await self._empty_search_next_action()
        return payload

    async def _empty_search_next_action(self) -> str:
        """The one next call to make when the search returned no candidates.

        The active task's id is a fact the Host already returns to the model on
        ``context_route_active_scope_mismatch``; its *title* only exists behind
        the scope-disclosure authority, so it is deliberately not echoed here.
        """

        try:
            active = await self._ledger.latest_task_route_decision()
        except Exception:  # noqa: BLE001 - guidance must never fail the search
            active = None
        base = (
            "No task matched this query. Do not repeat the same search; a "
            "different wording of a zero-hit query returns the same empty list."
        )
        if active is not None and active.get("task_scope_id"):
            return (
                f"{base} There is a current active task "
                f"(task_scope_id={active['task_scope_id']}). If this request "
                "continues it, call context_route with route=continue_active — "
                "no search is needed for the active task. Otherwise call "
                "context_route with route=create_new plus title and goal."
            )
        return (
            f"{base} There is no current active task, so continue_active and "
            "resume_existing have nothing to select. Call context_route with "
            "route=create_new plus title and goal for multi-step work, or "
            "route=direct_standalone / memory_standalone if no task is needed."
        )


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

# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5a five-route ``context_route`` tool and read-only ``task_scope_search``.

Host adjudication over the model's route proposal:

- ``direct_standalone`` — standalone receipt, no TaskScope, no recalled content
  in the result. Since event V it is not "no Memory query": every executing
  route first runs the confirmation-only contested probe below, whose one and
  only output is a ``conflict_notice``.

What event V guarantees is that **the Host always asks Memory** before any
route commits — not that a contested value is always reported. Whether the ask
returns a group is the SDK's own slot-level admission, and on 0.6.34 a
Chinese-only turn over a slot whose ``contested_slot_text`` holds no CJK and
whose memory has no vector generation admits nothing (SDK followup F-V-2, a
blocker for the AC). The probe therefore sends both surfaces the Host has and
records which one admitted.
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
import inspect
import logging
import uuid
from dataclasses import dataclass
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
    MEMORY_TYPE_SELECTION_POLICY, REQUESTABLE_MEMORY_TYPES, indicates_workflow_request,
    parse_recall_selection, selection_policy_departures,
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

# Event V: the two Host lanes that ask Memory outside the model's own
# ``memory_standalone`` recall. They name their own durable SDK requests so a
# probe can never spend the idempotency key the model's recall needs.
CONTESTED_PROBE_PURPOSE = "contested-probe"
CONTESTED_PROBE_ATTRIBUTION_PURPOSE = "contested-probe-attribution"
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
            "description": "For memory_standalone, also request relevant prior conversation groups outside the recent context; default false. Short and long-term results share one Host budget and source checks.",
        },
        "task_scope_id": {"type": "string", "maxLength": 128},
        "reuse_workspace_of": {
            "type": ["string", "null"], "minLength": 1, "maxLength": 128,
            "description": "Only create_new: bind the new active task to the completed task's existing workspace under a new binding grant to its one verified root, never reopening it. Copy its exact task_scope_id from public search/resume, and its source_hash into expected_source_hash. Otherwise omit both fields or set both to JSON null; never a placeholder, whitespace or an invented hash.",
        },
        "title": {"type": "string", "maxLength": 256},
        "goal": {"type": "string", "maxLength": _MAX_TEXT},
        "expected_source_hash": {
            "type": ["string", "null"], "minLength": 64, "maxLength": 64,
            "description": "The exact source_hash published beside that task_scope_id by public task search/resume, for resume_existing or create_new workspace reuse.",
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


# MM-D3 (manual journey run4 T7). The user named 「二号任务」 while 一号 was the
# active task; the search hits carried no "which of these is the active one"
# fact, so the model routed ``continue_active``, bound the Run to 一号, and every
# later ``resume_existing`` for 二号 died on the frozen one-scope-per-Run rule.
# These two strings are the whole disclosure fix: which hit is active, and the
# route that follows from the *name the user used* rather than from activity.
RUN_SCOPE_BOUND_ELSEWHERE = "context_route_run_scope_bound_elsewhere"

_NAMED_TASK_ROUTE_RULE = (
    "is_active marks this Run's current active task; it is not the task the user named, and it is "
    "not scope_disclosure.status (which is every open task's own lifecycle). Route by the name the "
    "user used: when the task the user named is a candidate with is_active=false, call context_route "
    "with route=resume_existing and that exact task_scope_id. Never continue_active for it — that "
    "binds this Run to the active task instead, and a Run binds one scope and can never rebind."
)


def _named_task_route_hint(
    candidates: list[dict[str, Any]], active_scope: str | None
) -> str:
    """The one route rule a hit list has to carry, plus this Run's own facts."""

    if active_scope is None:
        return (
            f"{_NAMED_TASK_ROUTE_RULE} No task is active in this Run yet, so continue_active has "
            "nothing to select at all."
        )
    if all(candidate["is_active"] for candidate in candidates):
        return (
            f"{_NAMED_TASK_ROUTE_RULE} Every candidate here is the active task "
            f"(task_scope_id={active_scope}), so continue_active is the route for it."
        )
    return (
        f"{_NAMED_TASK_ROUTE_RULE} The active task here is task_scope_id={active_scope}; every other "
        "candidate needs resume_existing."
    )


def _error(code: str, **detail: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": {"code": code, **detail}}
    return payload


async def read_current_turn_text(db_path: Any, sdk_run_id: str) -> str:
    """This Run's own live user turn text, as the Host admitted it.

    Event V: the contested probe must not stand only on the model's paraphrase
    of the turn (T22 happened to quote the contested values; a plainly worded
    turn would not). This is a read of the Host's own durable claim — the live
    ``foreground_run_heads`` row bound to this SDK Run and its atomic
    ``foreground_turns`` body — never model text and never a memory-read grant.
    Returns "" when the Run has no live claim (a settled or foreign Run), which
    only costs the probe one lexical surface.
    """

    import json

    import aiosqlite

    from deskpet.memory.current_input_visibility import (
        LIVE_CLAIM_PLACEHOLDERS, LIVE_CLAIM_STATES,
    )

    async with aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT t.turn_json FROM foreground_run_heads h "
            "JOIN foreground_turns t ON t.turn_id=h.turn_id AND t.subject=h.subject "
            f"WHERE h.sdk_run_id=? AND h.current_state IN ({LIVE_CLAIM_PLACEHOLDERS}) LIMIT 2",
            (sdk_run_id, *LIVE_CLAIM_STATES),
        )).fetchall()
    if len(rows) != 1:
        return ""
    text = json.loads(rows[0]["turn_json"]).get("payload", {}).get("text")
    return text if isinstance(text, str) else ""


@dataclass(frozen=True)
class _ContestedProbe:
    """One verdict of the event-V contested guard, plus what the audit needs.

    ``status is None`` means the probe never ran for this call (the
    ``memory_standalone`` lane asks for itself, and a proposal rejected before
    dispatch never reached it): the recorded detail is then byte-identical to
    the pre-event-V one.
    """

    notice: dict[str, Any] | None = None
    status: str | None = None
    query_sources: tuple[str, ...] = ()
    degradation_codes: tuple[str, ...] = ()
    admitted: str | None = None

    def audit(self) -> dict[str, Any]:
        """The bounded probe record. Never the query text, never a payload."""

        if self.status is None:
            return {}
        return {
            "contested_probe": self.status,
            **({"contested_probe_query_sources": list(self.query_sources)}
               if self.query_sources else {}),
            # The SDK's own lane degradations for the probe read. The model
            # never sees them on a non-memory route; the audit must, or a
            # silently degraded probe is indistinguishable from a clear one.
            **({"contested_probe_degradation_codes": list(self.degradation_codes)}
               if self.degradation_codes else {}),
            **({"contested_probe_admitted": self.admitted}
               if self.admitted is not None else {}),
        }


_NO_PROBE = _ContestedProbe()


def _conflict_digest(notice: Mapping[str, Any]) -> dict[str, Any]:
    """The audit projection of a conflict notice.

    Stable Host reason code plus the bounded conflict identity (group id +
    exact revisions), never the candidate payloads: the audit needs
    attribution, not the user's contested values a second time.
    """

    return {
        "reason": notice["reason"],
        "conflict_status": notice["conflict_status"],
        "groups": [{
            "conflict_group_id": group["conflict_group_id"],
            "memory_type": group["memory_type"],
            "revisions": [candidate["revision"] for candidate in group["candidates"]],
        } for group in notice["groups"]],
    }


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
        current_turn_text_reader: Any = None,
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
        self._current_turn_text_reader = current_turn_text_reader

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
                             # The query text is read for rule R5 and is not
                             # itself recorded - the projection stays bounded.
                             "selection_policy_departures":
                                 list(selection_policy_departures(
                                     selected, proposal.get("query")))}
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
        contested: _ContestedProbe = _NO_PROBE,
    ) -> dict[str, Any]:
        # Event V: the guard's verdict, taken by ``handle_context_route`` before
        # this route touched anything. ``memory_standalone`` passes its own
        # ``recall_conflict`` and never carries one here, so the value lands in
        # the same extras field and the same audit detail either way.
        if recall_conflict is None and contested.notice is not None:
            recall_conflict = contested.notice
            extras = {**dict(extras or {}), "conflict_notice": dict(recall_conflict)}
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
                **({"typed_carrier": dict(typed_carrier)} if typed_carrier is not None else {}),
                # What the model actually received. Event V made this necessary
                # on non-memory routes too: those build no typed carrier, so
                # before this the contested values delivered in ``extras`` were
                # covered by no hash at all.
                **({"public_result_hash": canonical_sha256(result)}
                   if typed_carrier is not None or contested.notice is not None else {}),
                **({"recall_selection": {
                    "origin": "model_proposal",
                    "requested_memory_types": list(recall_types),
                    "include_short_horizon": bool(recall_short_horizon),
                    "selection_policy_departures":
                        list(selection_policy_departures(
                            recall_types or (), proposal.get("query"))),
                }} if recall_types or recall_short_horizon is not None else {}),
                **({"recall_conflict": _conflict_digest(recall_conflict)}
                   if recall_conflict is not None else {}),
                # Every route that ran the guard says so, clear included: "no
                # notice" and "never asked" are different facts and the AC is
                # about the asking.
                **contested.audit(),
            },
        )
        return result

    async def _current_turn_text(self, run_id: str) -> str:
        """This turn's user text as the Host itself admitted it, if readable.

        Advisory: an unavailable reader only costs the probe one of its two
        lexical surfaces, never the route.
        """

        if self._current_turn_text_reader is None:
            return ""
        try:
            text = self._current_turn_text_reader(run_id)
            if inspect.isawaitable(text):
                text = await text
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - advisory guard, never a route verdict
            _LOG.warning("context_route_current_turn_text_unavailable run_id=%s", run_id)
            return ""
        return str(text or "").strip()[:_MAX_TEXT]

    async def _probe_recall(
        self, *, query: str, run_id: str, turn_ordinal: int, effect_id: str, purpose: str,
    ) -> Any:
        """One confirmation-only recall in the probe's own idempotency lane.

        The SDK keys one durable request per ``idempotency_key`` and rejects a
        second, different request under the same key. ``turn_ordinal`` is per
        provider *response*, shared by every tool call in one batch, so the
        probe must never spend the model's own ``context-route:{run}:{turn}``
        key: with two ``context_route`` calls in a batch the second would come
        back ``context_route_adjudication_failed``. Purpose + ``effect_id``
        give every read of a turn its own key.
        """

        return await self._recall_executor(
            query=query, run_id=run_id, turn_ordinal=turn_ordinal,
            memory_types=REQUESTABLE_MEMORY_TYPES,
            include_short_horizon=False,
            idempotency_purpose=purpose,
            idempotency_scope=effect_id,
        )

    async def _contested_notice(
        self,
        *,
        run_id: str,
        turn_ordinal: int,
        effect_id: str,
        proposal: Mapping[str, Any],
    ) -> _ContestedProbe:
        """Ask Memory whether this turn's own words touch a contested value.

        Confirmation-only: the probe's ordinary items and refs are discarded
        (its degradation codes go to the audit, not to the model), so a
        non-memory route still delivers no recalled content, keeps
        ``recall_refs`` empty and leaves ``ContextRouteReceipt`` untouched.
        Relevance is the SDK's own slot-level admission (0.6.31
        ``contested_slot_text`` + vector), not a Host heuristic, so an unrelated
        turn discloses nothing (minimum necessary) while the turn that names the
        contested slot gets the identical notice ``memory_standalone`` returns.

        The probe query is BOTH surfaces the Host has, model paraphrase first
        then the admitted user turn, because neither alone is sound: 0.6.34
        admits a group lexically only when a query term hits
        ``contested_slot_text`` (the differing fields + ``predicate``), and this
        incident's memory has that text in English/values only, with no vector
        generation to fall back on (SDK followup F-V-2). The real T22 user
        sentence 「那你现在按哪个版本执行这套校对流程？」 admits **nothing** on
        0.6.34 — the replay measured ``confirmation_groups 0`` — and only the
        model's paraphrase, which quoted 「3.13，不是 3.12」, hit the slot; a
        differently-worded turn is the mirror case. Sending both is what makes
        this a guarantee that the Host *asks* on every executing route. It is
        not a guarantee that Memory can always answer: until F-V-2 lands, a
        Chinese turn over a Chinese-less slot can still come back clear.

        Never a route verdict: a route that already validated must not fail on
        an unavailable memory read, so every failure degrades to ``unavailable``
        in the audit.
        """

        model_query = str(proposal.get("query") or "").strip()[:_MAX_TEXT]
        turn_text = await self._current_turn_text(run_id)
        sources: list[str] = []
        parts: list[str] = []
        for name, text in (("model_query", model_query), ("user_turn", turn_text)):
            if text and text not in parts:
                sources.append(name)
                parts.append(text)
        if not parts:
            # Neither surface exists, so there is no bounded basis to disclose
            # on. Recorded, never silent.
            return _ContestedProbe(status="no_query")
        if self._recall_executor is None:
            return _ContestedProbe(status="unavailable", query_sources=tuple(sources))
        from deskpet.memory.human_memory_v7 import project_contested_confirmation

        try:
            execution = await self._probe_recall(
                query="\n".join(parts), run_id=run_id, turn_ordinal=turn_ordinal,
                effect_id=effect_id, purpose=CONTESTED_PROBE_PURPOSE,
            )
            notice = project_contested_confirmation(execution)
            codes = tuple(str(getattr(code, "value", code))
                          for code in (getattr(execution, "degradation_codes", ()) or ()))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - advisory guard, never a route verdict
            # Exception text can carry provider or source content; log identity.
            _LOG.warning("context_route_contested_probe_unavailable run_id=%s", run_id)
            return _ContestedProbe(status="unavailable", query_sources=tuple(sources))
        if notice is None:
            return _ContestedProbe(
                status="clear", query_sources=tuple(sources), degradation_codes=codes)
        return _ContestedProbe(
            notice=notice, status="contested", query_sources=tuple(sources),
            degradation_codes=codes,
            admitted=await self._admitting_surface(
                turn_text=turn_text, sources=tuple(sources), run_id=run_id,
                turn_ordinal=turn_ordinal, effect_id=effect_id,
            ),
        )

    async def _admitting_surface(
        self, *, turn_text: str, sources: tuple[str, ...], run_id: str,
        turn_ordinal: int, effect_id: str,
    ) -> str | None:
        """Would the user's own sentence alone have reached this group?

        Asked only when a notice actually fired and both surfaces were sent, so
        it costs one extra recall on the rare contested turn and none otherwise.
        This is the production measurement of SDK followup F-V-2, and it asks
        the one question that matters: ``user_turn`` means the Host stands on
        the user's own words and the model's paraphrase was not needed;
        ``model_query`` means the user's sentence did NOT reach the group and
        this disclosure only happened because the model happened to quote the
        contested values — the T22 shape, and exactly the case F-V-2 must close.
        A field of ``model_query`` in production is therefore a defect report,
        not a statistic. Discarded result, own idempotency lane, never a verdict.
        """

        if sources != ("model_query", "user_turn"):
            return sources[0] if sources else None
        from deskpet.memory.human_memory_v7 import project_contested_confirmation

        try:
            execution = await self._probe_recall(
                query=turn_text, run_id=run_id, turn_ordinal=turn_ordinal,
                effect_id=effect_id,
                purpose=CONTESTED_PROBE_ATTRIBUTION_PURPOSE,
            )
            return "user_turn" if project_contested_confirmation(execution) else "model_query"
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - attribution is a measurement, not a gate
            _LOG.warning("context_route_contested_attribution_unavailable run_id=%s", run_id)
            return "unknown"

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
        # Event V: ``memory_standalone`` is not the only route that executes on
        # a stored value. A6 attempt 9 T22 asked 「那你现在按哪个版本执行这套校对
        # 流程？」 66 s after the contest landed, routed ``direct_standalone``
        # ("answer the turn as it stands") and answered "Python 3.13" from its
        # own transcript: the Host's contested gate lived only inside
        # ``_memory_standalone``, so no route but that one ever consulted the
        # open conflict group. Every other route now runs the same bounded probe
        # — deliberately here, BEFORE any route side effect, so a cancelled probe
        # can never leave a created TaskScope without its route decision.
        contested = _NO_PROBE
        if route_value != "memory_standalone":
            contested = await self._contested_notice(
                run_id=run_id, turn_ordinal=turn_ordinal, effect_id=effect_id,
                proposal=proposal,
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
                    contested=contested,
                )
            if route_value == "memory_standalone":
                return await self._memory_standalone(
                    run_id, raw_call_id, effect_id, turn_ordinal, proposal
                )
            if route_value == "continue_active":
                return await self._continue_active(
                    run_id, raw_call_id, effect_id, turn_ordinal, proposal,
                    contested=contested,
                )
            if route_value == "resume_existing":
                return await self._resume_existing(
                    run_id, raw_call_id, effect_id, turn_ordinal, proposal,
                    contested=contested,
                )
            return await self._create_new(
                run_id, raw_call_id, effect_id, turn_ordinal, proposal,
                contested=contested,
            )
        except _CompositionUnavailable as exc:
            return await self._reject(run_id, raw_call_id, effect_id, proposal, exc.code,
                                      contested=contested)
        except Exception as exc:  # noqa: BLE001 - stable fail-closed surface
            if route_value == "memory_standalone":
                # Exception messages/codes can contain provider or source text.
                code = ("context_route_recall_timeout" if isinstance(exc, TimeoutError)
                        else "context_route_adjudication_failed")
                return await self._reject(run_id, raw_call_id, effect_id, proposal, code)
            code = str(getattr(exc, "code", "") or "context_route_adjudication_failed")
            # MM-D3: ``task_scope_conflict`` used to carry only the raw
            # ``execution_run_scope_conflict`` string — neither scope id and no
            # next step, so the model spent the rest of the Run looking for a
            # rebind that does not exist. Same stable code, actionable detail.
            scope_detail: dict[str, Any] = {}
            if code == "task_scope_conflict":
                scope_detail = await self._run_scope_conflict_detail(run_id, proposal)
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal, code,
                contested=contested, **scope_detail,
                message=(_WORKSPACE_REUSE_NEW_RUN if code == "context_route_workspace_reuse_requires_new_run"
                         else str(exc)[:512]),
            )

    async def _run_scope_conflict_detail(
        self, run_id: str, proposal: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Name the bound scope, the requested scope, and the only honest step.

        The frozen S4/S5 rule is one TaskScope per Run: the route decision is
        ingested into the bound scope's canonical archive in the same
        transaction that records it, so there is nothing here to undo and no
        "handoff" route to offer. The executable step is therefore for *this*
        turn to end with an explanation, and for the next turn to route
        ``resume_existing`` directly.
        """

        try:
            bound = await self._ledger.latest_route_decision_for_run(run_id, task_only=True)
        except Exception:  # noqa: BLE001 - the rejection must never fail on its own detail
            _LOG.warning("context_route_conflict_detail_unavailable run_id=%s", run_id)
            bound = None
        bound_id = None if bound is None else bound.get("task_scope_id")
        requested = proposal.get("task_scope_id")
        requested_id = None if requested is None else str(requested)[:128]
        bound_text = "another task" if bound_id is None else f"task_scope_id={bound_id}"
        target_text = (
            "the requested task" if requested_id is None else f"task_scope_id={requested_id}"
        )
        return {
            "reason_code": RUN_SCOPE_BOUND_ELSEWHERE,
            "bound_task_scope_id": bound_id,
            "requested_task_scope_id": requested_id,
            "next_step": (
                f"This Run is already bound to {bound_text} and a Run binds one scope only; it can "
                f"never rebind, and no route switches it. End this turn by telling the user that the "
                f"request needs the other task, naming {target_text}. Their next message starts a new "
                f"Run, and there route {target_text} with resume_existing as its first task route. Do "
                f"not retry this call, and do not close or mutate the bound task over this."
            ),
            "next_step_zh": (
                f"本 Run 已绑定 {bound_text}；请回复用户说明并在下一轮直接 resume_existing "
                f"{target_text}。"
            ),
        }

    async def _reject(
        self,
        run_id: str,
        raw_call_id: str,
        effect_id: str,
        proposal: Mapping[str, Any],
        code: str,
        *,
        contested: _ContestedProbe = _NO_PROBE,
        **detail: Any,
    ) -> dict[str, Any]:
        # Event V: the guard runs before dispatch, so a contest can be found on
        # a proposal that then fails route validation (no active scope, missing
        # task_scope_id/title, a binding or authorization rejection). Dropping
        # it there would put the Host back where T22 was — it asked Memory, was
        # told the value is contested, and told the model nothing. The error
        # code stays exactly what it was; the notice rides the error extras.
        notice = contested.notice
        payload = _error(code, **detail,
                         **({"conflict_notice": dict(notice)} if notice is not None else {}))
        await self._record(
            run_id=run_id,
            raw_call_id=raw_call_id,
            effect_id=effect_id,
            proposal=proposal,
            verdict="rejected",
            decision_id=None,
            detail={
                "code": code, **detail, **contested.audit(),
                **({"recall_conflict": _conflict_digest(notice),
                    # The rejection body is what the model receives, contested
                    # values included; cover it like a committed result.
                    "public_result_hash": canonical_sha256(payload)}
                   if notice is not None else {}),
            },
        )
        return payload


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
                # One provider response can carry two context_route calls; the
                # turn ordinal alone is not a per-call identity, so the model's
                # second recall of a turn used to collide with its first inside
                # the SDK's idempotency store.
                idempotency_scope=effect_id,
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
                # Event AA: the exact Host-authored recall behind this binding,
                # so the context-use fence can re-collect it (same plan, its own
                # idempotency purpose) when the bound authority lease runs out
                # mid-turn instead of failing the whole Run.
                recall_plan=dict(query=query, memory_types=memory_types,
                                 include_short_horizon=include_short_horizon,
                                 turn_ordinal=turn_ordinal),
            )
        refs = tuple(dict.fromkeys(str(f["ref"]) for f in fragments))
        procedure_hint = await self._procedure_hint(
            run_id=run_id, query=query, memory_types=memory_types, fragments=fragments,
        )
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
                **({"procedure_hint": procedure_hint} if procedure_hint else {}),
            },
        )

    async def _run_is_task_scoped(self, run_id: str) -> bool:
        """Has this Run already bound a TaskScope (any earlier turn of the Run)?

        Never fail an already successful recall on this read; an unavailable
        ledger only means the Host cannot claim the Run is doing task work.
        """

        try:
            decision = await self._ledger.latest_route_decision_for_run(
                run_id, task_only=True,
            )
        except Exception:  # noqa: BLE001 - advisory hint only, never a route verdict
            _LOG.warning("context_route_procedure_hint_scope_unavailable run_id=%s", run_id)
            return False
        return decision is not None

    async def _procedure_hint(
        self, *, run_id: str, query: str, memory_types: tuple[str, ...], fragments,
    ) -> dict[str, Any] | None:
        """Point at ``procedure_discover`` whenever a workflow could be the answer.

        F-ETR-5: this used to fire only when the model had put ``procedure`` in
        ``memory_types``. Rule R4 of ``MEMORY_TYPE_SELECTION_POLICY`` then
        correctly told the model to stop requesting a type typed recall cannot
        serve, which silently switched the hint off for the very requests it
        exists for (C06 ``procedure_discover`` 18/19 → 14/19). The trigger is
        therefore the request, not the type selection: a workflow-shaped query,
        or a Run already inside a TaskScope, where an unbound saved workflow is
        exactly what typed recall withholds. The payload is unchanged.
        """

        if any(fragment["memory_type"] == "procedure" for fragment in fragments):
            return None
        if not ("procedure" in memory_types
                or indicates_workflow_request(query)
                or await self._run_is_task_scoped(run_id)):
            return None
        return dict(_PROCEDURE_HINT)

    async def _continue_active(
        self,
        run_id: str,
        raw_call_id: str,
        effect_id: str,
        turn_ordinal: int,
        proposal: Mapping[str, Any],
        *,
        contested: _ContestedProbe = _NO_PROBE,
    ) -> dict[str, Any]:
        active = await self._ledger.latest_task_route_decision()
        if active is None or not active.get("task_scope_id"):
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_no_active_task_scope",
                contested=contested,
            )
        active_scope = str(active["task_scope_id"])
        proposed = proposal.get("task_scope_id")
        if proposed is not None and str(proposed) != active_scope:
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_active_scope_mismatch",
                contested=contested,
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
            contested=contested,
        )

    async def _resume_existing(
        self,
        run_id: str,
        raw_call_id: str,
        effect_id: str,
        turn_ordinal: int,
        proposal: Mapping[str, Any],
        *,
        contested: _ContestedProbe = _NO_PROBE,
    ) -> dict[str, Any]:
        scope = proposal.get("task_scope_id")
        if not scope:
            # Search hits never authorize; an exact ID is mandatory here.
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_exact_task_scope_required",
                contested=contested,
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
                contested=contested,
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
                contested=contested,
            )
        if self._scope_disclosure_reader is None:
            return await self._reject(run_id, raw_call_id, effect_id, proposal,
                                      "scope_disclosure_reader_missing", contested=contested)
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
            contested=contested,
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
        *,
        contested: _ContestedProbe = _NO_PROBE,
    ) -> dict[str, Any]:
        title = str(proposal.get("title") or "").strip()
        if not title:
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_title_required",
                contested=contested,
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
                    contested=contested, message=_WORKSPACE_REUSE_NEW_RUN)
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
                contested=contested,
                task_scope_id=scope,
            )
        store = self._binding_store_factory()
        root = store.configured_root()
        if not root:
            return await self._reject(
                run_id, raw_call_id, effect_id, proposal,
                "context_route_workspace_root_not_configured",
                contested=contested,
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
                contested=contested,
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
            contested=contested,
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
            run_id, _, effect_id, _ = self._identity()
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
                    effect_id=effect_id,
                    sdk_run_id=run_id,
                )
            )
        except Exception as exc:  # noqa: BLE001 - stable fail-closed surface
            code = str(getattr(exc, "code", "") or "task_scope_search_failed")
            return _error(code)
        from deskpet.memory.human_memory_service import OpenTaskScopeRequest
        if self._scope_disclosure_reader is None:
            return _error("scope_disclosure_reader_missing")
        # MM-D3: which hit is *this Run's* active task is a Host fact the model
        # cannot derive. ``scope_disclosure.status`` is the scope's own
        # lifecycle ("active" = not completed) and is "active" for every open
        # task, so it never answered "is this the one continue_active picks?".
        active_scope = await self._current_active_task_scope_id()
        candidates = []
        for item in result["candidates"]:
            scope_id = item["scope_ref"]
            opened = await service.open_task_scope(OpenTaskScopeRequest(scope_ref=scope_id,
                expected_source_hash=item["source_hash"], effect_id=effect_id, sdk_run_id=run_id))
            package = await self._scope_disclosure_reader(run_id, opened["resume_package"], effect_id)
            candidates.append({"task_scope_id": scope_id, "source_id": package["source_id"],
                "source_hash": package["source_hash"],
                "is_active": active_scope is not None and str(scope_id) == active_scope,
                "scope_disclosure": package})
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
        if candidates:
            payload["route_hint"] = _named_task_route_hint(candidates, active_scope)
        if not candidates:
            # HM-TO-A6 incident B: a real model re-issued the same zero-hit
            # query ten times because an empty candidate list said nothing
            # about what to do next.  The Host already knows whether a current
            # active task route exists — say so, and name the exact next call.
            payload["next_action"] = await self._empty_search_next_action()
        return payload

    async def _current_active_task_scope_id(self) -> str | None:
        """This Run's would-be ``continue_active`` target, or ``None``.

        Advisory disclosure only: an unavailable ledger degrades to "unknown",
        never to a wrong flag and never to a failed search.
        """

        try:
            active = await self._ledger.latest_task_route_decision()
        except Exception:  # noqa: BLE001 - disclosure must never fail the search
            _LOG.warning("task_scope_search_active_scope_unavailable")
            return None
        if active is None or not active.get("task_scope_id"):
            return None
        return str(active["task_scope_id"])

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
    "RUN_SCOPE_BOUND_ELSEWHERE",
    "TASK_SCOPE_SEARCH_SCHEMA",
    "ContextRouteToolService",
    "local_owner_auth",
]

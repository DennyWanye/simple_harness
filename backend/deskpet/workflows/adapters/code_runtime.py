# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Production ports for the durable Complex Code v1 workflow."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import logging
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable

from llm.types import ChatResponse
from deskpet.execution.dispatch import dispatch_with_run_fence
from deskpet.execution.provider_invocations import coordinate_provider_call
from deskpet.tools.capabilities import (
    canonical_deferred_capability_id,
    canonical_hash,
)

from ..contracts import (
    EffectPolicy,
    JsonValue,
    NodeExecutionIdentity,
    canonical_json,
    validate_json_value,
)
from ..definitions.code_nodes import (
    FORBIDDEN_DURABLE_TOOLS,
    CapabilitySnapshotV1,
    WorkflowSessionRefV1,
    _consecutive_discovery_searches,
    _DISCOVERY_ONLY_TOOL_NAMES,
    _MAX_CONSECUTIVE_DISCOVERY_SEARCHES,
    _REPEATABLE_DISCOVERY_SEARCH_TOOL_NAMES,
)
from ..effects import (
    EffectAction,
    EffectExecutionContext,
    EffectJournal,
    EffectStateConflict,
    NormalizedToolOutcome,
    PreparedToolCall,
    ToolOutcomeState,
)
from ..proposal_state import (
    ProposalErrorV1,
    ProposalOutcomeV1,
    ProposalStateV1,
    derive_stable_call_id,
)
from ..output_contract import TaskOutputContractV1

logger = logging.getLogger(__name__)


def _model_dump(value: object) -> dict[str, JsonValue]:
    if hasattr(value, "model_dump"):
        raw = value.model_dump(mode="json")
    elif hasattr(value, "dict"):
        raw = value.dict()
    elif isinstance(value, Mapping):
        raw = dict(value)
    else:
        raise TypeError(f"{type(value).__name__} is not a serializable model")
    payload = copy.deepcopy(dict(raw))
    validate_json_value(payload)
    return payload


def _schema_tool_name(schema: Mapping[str, object]) -> str:
    function = schema.get("function")
    if isinstance(function, Mapping):
        return str(function.get("name") or "")
    return str(schema.get("name") or "")


def _consecutive_discovery_turns(
    messages: Sequence[Mapping[str, Any]],
) -> int:
    count = 0
    setup_tools = _DISCOVERY_ONLY_TOOL_NAMES | {"tool_activate"}
    for message in reversed(messages):
        if str(message.get("role") or "") != "assistant":
            continue
        raw_calls = message.get("tool_calls")
        if not isinstance(raw_calls, list) or not raw_calls:
            break
        names: set[str] = set()
        for raw_call in raw_calls:
            function = (
                raw_call.get("function")
                if isinstance(raw_call, Mapping)
                else None
            )
            names.add(
                str(function.get("name") or "")
                if isinstance(function, Mapping)
                else ""
            )
        if not names or not names.issubset(setup_tools):
            break
        count += 1
    return count


def _compact_repeatable_search_payloads(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve tool-call pairing while compacting superseded search output."""

    copied = [copy.deepcopy(dict(message)) for message in messages]
    search_indexes = [
        index
        for index, message in enumerate(copied)
        if (
            str(message.get("role") or "") == "tool"
            and str(message.get("name") or "")
            in _REPEATABLE_DISCOVERY_SEARCH_TOOL_NAMES
        )
    ]
    for index in search_indexes[:-1]:
        copied[index]["content"] = canonical_json(
            {
                "ok": True,
                "status": "superseded_search_result",
                "instruction": "Use the newest search result below.",
            }
        )
    return copied


def _declined_dynamic_tool_names(state: ProposalStateV1) -> frozenset[str]:
    """Return tools the user chose to exclude for the rest of this Run.

    ``continue_without`` is a durable decision, not a one-turn hint.  Keeping
    the exact tool hidden from later provider turns prevents the model from
    reopening the same unsupported-dynamic-tool decision indefinitely while
    leaving alternative built-in capabilities available.
    """

    return frozenset(
        str(result.get("tool_name") or "")
        for result in state.committed_tool_results.values()
        if isinstance(result, Mapping)
        and str(result.get("code") or "") == "unsupported_dynamic_tool"
        and result.get("retryable") is False
        and str(result.get("tool_name") or "")
    )


def _policy_payload(policy: object | None) -> dict[str, JsonValue] | None:
    if policy is None:
        return None
    kind = getattr(policy, "kind", "")
    kind_value = getattr(kind, "value", kind)
    payload: dict[str, JsonValue] = {
        "policy_id": str(getattr(policy, "policy_id", "")),
        "version": str(getattr(policy, "version", "")),
        "kind": str(kind_value),
        "max_attempts": int(getattr(policy, "max_attempts", 1)),
        "reusable_across_branches": bool(
            getattr(policy, "reusable_across_branches", False)
        ),
    }
    validate_json_value(payload)
    return payload


def _callable_identity(value: object) -> str:
    return ":".join(
        (
            str(getattr(value, "__module__", "")),
            str(getattr(value, "__qualname__", getattr(value, "__name__", ""))),
        )
    )


def _lifecycle_hash(spec: object) -> str | None:
    required = tuple(getattr(spec, name, None) for name in ("stage", "commit", "rollback", "reconcile"))
    if not all(callable(value) for value in required):
        return None
    payload: dict[str, JsonValue] = {
        "spec_version": str(getattr(spec, "spec_version", "")),
        "callbacks": [_callable_identity(value) for value in required],
    }
    return hashlib.sha256(canonical_json(payload).encode("ascii")).hexdigest()


def _source_kind(source: object) -> str:
    value = str(source or "builtin")
    if value.startswith("plugin"):
        return "plugin"
    if value.startswith("mcp"):
        return "mcp"
    return value


def capability_snapshot(
    tool_registry: object,
    *,
    allowed_tools: Sequence[str] | None = None,
) -> list[CapabilitySnapshotV1]:
    """Snapshot exactly the tools exposed to this proposal port."""

    schemas = tool_registry.schemas()
    visible = {
        _schema_tool_name(schema)
        for schema in schemas
        if isinstance(schema, Mapping)
    }
    visible.discard("")
    visible.difference_update(FORBIDDEN_DURABLE_TOOLS)
    if allowed_tools is not None:
        visible.intersection_update(str(name) for name in allowed_tools)
    snapshots: list[CapabilitySnapshotV1] = []
    for name in sorted(visible):
        spec = tool_registry.get(name)
        if spec is None:
            continue
        snapshots.append(
            CapabilitySnapshotV1(
                tool_name=name,
                source=_source_kind(getattr(spec, "source", "builtin")),
                schema_hash=str(getattr(spec, "schema_hash", "")),
                spec_version=str(getattr(spec, "spec_version", "")),
                effect_policy=_policy_payload(getattr(spec, "effect_policy", None)),
                lifecycle_hash=_lifecycle_hash(spec),
                outcome_parser_hash=(
                    str(getattr(spec, "outcome_parser_hash"))
                    if getattr(spec, "outcome_parser_hash", None)
                    else None
                ),
            )
        )
    return snapshots


def workflow_session_ref(
    *,
    base_session_id: str,
    code_session_id: str,
    delivery_session_id: str,
    project_root: str | Path,
    base_epoch: int = 0,
    code_epoch: int = 0,
) -> WorkflowSessionRefV1:
    normalized_root = str(Path(project_root).expanduser().resolve(strict=False))
    root_hash = hashlib.sha256(normalized_root.casefold().encode("utf-8")).hexdigest()
    return WorkflowSessionRefV1(
        base_session_id=base_session_id,
        code_session_id=code_session_id,
        delivery_session_id=delivery_session_id,
        project_root_hash=root_hash,
        base_epoch=base_epoch,
        code_epoch=code_epoch,
    )


@dataclass
class DurableToolRuntimeState:
    """Mutable, process-local view rebuilt from durable activation receipts.

    The workflow checkpoint remains the source of truth.  This holder only
    lets the proposal and dispatch ports share the effective immutable
    ``PreparedToolSet`` during one runtime attachment.
    """

    prepared_tool_set: object


class ProposalPort:
    """Translate the existing provider response into a durable proposal."""

    def __init__(
        self,
        llm_registry: object,
        tool_registry: object,
        *,
        session_id: str,
        provider_name: str | None = None,
        model_name: str | None = None,
        profile_key: str | None = None,
        context_os_v1: bool = False,
        prepared_tool_set: object | None = None,
        eligibility: object | None = None,
        allowed_tools: Sequence[str] | None = None,
        execution_context: object | None = None,
        capability_scope_store: object | None = None,
        runtime_tool_state: DurableToolRuntimeState | None = None,
        dispatch_fence_acquirer: Callable[[str], Awaitable[Any]] | None = None,
        provider_invocation_coordinator: Any | None = None,
        context_compressor: object | None = None,
        compaction_trigger_tokens: int | None = None,
        output_contract: TaskOutputContractV1 | None = None,
    ) -> None:
        self.llm_registry = llm_registry
        self.tool_registry = tool_registry
        self.session_id = session_id
        self.provider_name = provider_name
        self.model_name = model_name
        self.profile_key = str(profile_key or "").strip() or None
        self.context_os_v1 = bool(context_os_v1)
        self.prepared_tool_set = prepared_tool_set
        self.eligibility = eligibility
        self.execution_context = execution_context
        self.capability_scope_store = capability_scope_store
        self.runtime_tool_state = runtime_tool_state
        self.dispatch_fence_acquirer = dispatch_fence_acquirer
        self.provider_invocation_coordinator = provider_invocation_coordinator
        self.context_compressor = context_compressor
        self.compaction_trigger_tokens = (
            max(1, int(compaction_trigger_tokens))
            if compaction_trigger_tokens is not None
            else None
        )
        self.output_contract = output_contract
        self.allowed_tools = (
            frozenset(str(name) for name in allowed_tools)
            if allowed_tools is not None else None
        )
        if self.context_os_v1 and self.prepared_tool_set is None:
            raise ValueError("context_os_code_proposal_requires_prepared_tool_set")

    def _durable_compaction_trigger(self) -> int | None:
        if self.context_compressor is None:
            return None
        if self.compaction_trigger_tokens is not None:
            return self.compaction_trigger_tokens
        window = int(
            getattr(self.context_compressor, "context_window", 0) or 0
        )
        if window <= 0:
            return None
        # Durable workflows are intrinsically agentic.  Start rolling
        # compaction no later than 70% of the selected model window, even
        # when the model's conversational base threshold is higher.
        return max(1, int(window * 0.70))

    async def _compact_messages_for_proposal(
        self,
        state: ProposalStateV1,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]] | None,
        str | None,
        str | None,
        int,
    ]:
        messages = [copy.deepcopy(dict(message)) for message in state.messages]
        try:
            from deskpet.agent.tokens import count_messages_tokens

            token_estimate = int(count_messages_tokens(messages))
        except Exception:  # noqa: BLE001
            token_estimate = max(0, int(state.token_estimate))
        trigger = self._durable_compaction_trigger()
        if (
            self.context_compressor is None
            or trigger is None
            or token_estimate < trigger
        ):
            return messages, None, None, None, token_estimate

        try:
            resolved_provider = getattr(self.llm_registry, "provider", None)
            result = await self.context_compressor.compress(
                messages,
                goal_text=state.original_request,
                pending_tasks=list(state.active_todo_ids),
                resolved_provider=resolved_provider,
                resolved_model=(
                    self.model_name
                    or str(state.model_snapshot.get("model") or "")
                    or None
                ),
                compaction_cycle_id=(
                    f"{state.request_id}:{state.turn_id}:workflow:"
                    f"{state.iteration + 1}"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            # Context compaction is a continuity optimization.  A summary
            # failure must never turn a resumable workflow into a failed Run.
            logger.warning(
                "durable_workflow_compaction_failed request_id=%s iteration=%d error=%s",
                state.request_id,
                state.iteration,
                str(exc)[:300],
            )
            return messages, None, None, None, token_estimate

        if not bool(getattr(result, "compressed", False)):
            error = str(getattr(result, "error", "") or "")
            if error:
                logger.warning(
                    "durable_workflow_compaction_skipped request_id=%s iteration=%d error=%s",
                    state.request_id,
                    state.iteration,
                    error[:300],
                )
            return messages, None, None, None, token_estimate

        compacted = [
            copy.deepcopy(dict(message))
            for message in getattr(result, "messages", messages)
        ]
        try:
            from deskpet.agent.tokens import count_messages_tokens

            compacted_tokens = int(count_messages_tokens(compacted))
        except Exception:  # noqa: BLE001
            compacted_tokens = max(
                0,
                int(getattr(result, "output_tokens", 0) or 0),
            )
        summary = str(getattr(result, "summary_preview", "") or "") or None
        compaction_ref = (
            "workflow-compaction:sha256:"
            + hashlib.sha256(
                canonical_json(compacted).encode("ascii")
            ).hexdigest()
        )
        logger.info(
            "durable_workflow_context_compacted request_id=%s iteration=%d "
            "tokens_before=%d tokens_after=%d trigger=%d",
            state.request_id,
            state.iteration,
            token_estimate,
            compacted_tokens,
            trigger,
        )
        return (
            compacted,
            compacted,
            summary,
            compaction_ref,
            compacted_tokens,
        )

    @staticmethod
    def _activation_batches(
        messages: Sequence[Mapping[str, Any]],
    ) -> list[list[tuple[dict[str, Any], dict[str, Any]]]]:
        """Extract host-issued activation controls with their bound call args."""

        call_bindings: dict[str, tuple[int, dict[str, Any]]] = {}
        batches: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        batch_order: list[int] = []
        next_batch = 0
        for message in messages:
            if str(message.get("role") or "") == "assistant":
                next_batch += 1
                for raw_call in message.get("tool_calls") or ():
                    if not isinstance(raw_call, Mapping):
                        continue
                    function = raw_call.get("function")
                    if not isinstance(function, Mapping):
                        continue
                    if str(function.get("name") or "") != "tool_activate":
                        continue
                    raw_args = function.get("arguments")
                    if isinstance(raw_args, str):
                        try:
                            raw_args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            continue
                    if not isinstance(raw_args, Mapping):
                        continue
                    call_bindings[str(raw_call.get("id") or "")] = (
                        next_batch,
                        dict(raw_args),
                    )
                continue
            if (
                str(message.get("role") or "") != "tool"
                or str(message.get("name") or "") != "tool_activate"
            ):
                continue
            binding = call_bindings.get(str(message.get("tool_call_id") or ""))
            if binding is None:
                continue
            try:
                payload = json.loads(str(message.get("content") or ""))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if not isinstance(payload, Mapping) or payload.get("ok") is not True:
                continue
            outcome = payload.get("outcome")
            if not isinstance(outcome, Mapping) or outcome.get("state") != "success":
                continue
            value = outcome.get("value")
            control = value.get("__deskpet_control") if isinstance(value, Mapping) else None
            if not isinstance(control, Mapping) or control.get("kind") != "tool_activation":
                continue
            batch_id, args = binding
            if batch_id not in batches:
                batches[batch_id] = []
                batch_order.append(batch_id)
            batches[batch_id].append((dict(control), args))
        return [batches[batch_id] for batch_id in batch_order]

    def _effective_prepared_tool_set(self, state: ProposalStateV1) -> object:
        prepared = (
            self.runtime_tool_state.prepared_tool_set
            if self.runtime_tool_state is not None
            else self.prepared_tool_set
        )
        if not self.context_os_v1:
            return prepared
        from deskpet.tools.capabilities import PreparedToolCapability

        for batch in self._activation_batches(state.messages):
            base_revisions = {int(control.get("base_scope_revision", -1)) for control, _ in batch}
            if len(base_revisions) != 1:
                raise RuntimeError("tool_activation_batch_revision_mismatch")
            base_revision = next(iter(base_revisions))
            already_applied = True
            for control, args in batch:
                capability_id = str(control.get("capability_id") or "")
                schema_hash = str(control.get("schema_hash") or "")
                nonce = str(control.get("nonce") or "")
                requested_capability_id = str(args.get("capability_id") or "")
                try:
                    bound_capability_id = canonical_deferred_capability_id(
                        requested_capability_id,
                        self.prepared_tool_set.deferred,
                    )
                except RuntimeError as exc:
                    raise RuntimeError(
                        "tool_activation_call_binding_mismatch"
                    ) from exc
                if (
                    bound_capability_id != capability_id
                    or str(args.get("schema_hash") or "") != schema_hash
                    or str(args.get("describe_nonce") or "") != nonce
                ):
                    raise RuntimeError("tool_activation_call_binding_mismatch")
                active = next(
                    (
                        item
                        for item in (*prepared.direct, *prepared.activated)
                        if item.ref.capability_id == capability_id
                        and item.ref.schema_hash == schema_hash
                    ),
                    None,
                )
                already_applied = already_applied and active is not None
            if already_applied:
                continue
            if int(prepared.revision) != base_revision:
                raise RuntimeError("tool_activation_revision_conflict")
            for control, _ in batch:
                capability_id = str(control.get("capability_id") or "")
                schema_hash = str(control.get("schema_hash") or "")
                schema = control.get("schema")
                if not isinstance(schema, Mapping):
                    raise RuntimeError("tool_activation_schema_malformed")
                ref = next(
                    (
                        item
                        for item in prepared.deferred
                        if item.capability_id == capability_id
                        and item.schema_hash == schema_hash
                    ),
                    None,
                )
                if ref is None:
                    raise RuntimeError("tool_activation_capability_stale")
                prepared = prepared.activate(
                    PreparedToolCapability(ref=ref, canonical_schema=dict(schema))
                )

        self.tool_registry.validate_prepared_tool_set(
            prepared, eligibility=self.eligibility
        )
        if self.runtime_tool_state is not None:
            self.runtime_tool_state.prepared_tool_set = prepared
        scopes = self.capability_scope_store
        if scopes is not None:
            current = scopes.get(
                prepared.scope_id,
                session_id=self.eligibility.session_id,
                request_id=self.eligibility.request_id,
            )
            if current is None:
                scopes.open(prepared, self.eligibility)
            elif current.prepared.revision < prepared.revision:
                scopes.commit_prevalidated(prepared)
            elif (
                current.prepared.revision != prepared.revision
                or current.prepared.schema_fingerprint != prepared.schema_fingerprint
            ):
                raise RuntimeError("tool_activation_scope_conflict")
        return prepared

    def _tool_schemas(self, prepared_tool_set: object | None = None) -> list[dict[str, Any]]:
        if self.context_os_v1:
            prepared_tool_set = prepared_tool_set or self.prepared_tool_set
            schemas = [
                copy.deepcopy(dict(schema))
                for schema in prepared_tool_set.logical_schemas()
            ]
            forbidden = {
                _schema_tool_name(schema)
                for schema in schemas
                if _schema_tool_name(schema) in FORBIDDEN_DURABLE_TOOLS
            }
            if forbidden:
                raise ValueError(
                    f"prepared durable tool set contains forbidden tools: {sorted(forbidden)!r}"
                )
            actual = canonical_hash(schemas)
            expected = str(prepared_tool_set.schema_fingerprint)
            if actual != expected:
                raise ValueError(
                    f"prepared_schema_fingerprint_mismatch:{expected}:{actual}"
                )
            if self.allowed_tools is not None:
                effective_allowed = set(self.allowed_tools)
                effective_allowed.update(
                    capability.ref.name
                    for capability in prepared_tool_set.activated
                )
                schemas = [
                    schema for schema in schemas
                    if _schema_tool_name(schema) in effective_allowed
                ]
            return schemas
        return [
            copy.deepcopy(dict(schema))
            for schema in self.tool_registry.schemas()
            if isinstance(schema, Mapping)
            and _schema_tool_name(schema) not in FORBIDDEN_DURABLE_TOOLS
            and (
                self.allowed_tools is None
                or _schema_tool_name(schema) in self.allowed_tools
            )
        ]

    async def propose(self, state: ProposalStateV1) -> ProposalOutcomeV1:
        return await self.propose_for_execution(state, execution_identity=None)

    async def propose_for_execution(
        self,
        state: ProposalStateV1,
        *,
        execution_identity: NodeExecutionIdentity | None,
    ) -> ProposalOutcomeV1:
        (
            context_messages,
            compacted_messages,
            compaction_summary,
            compaction_ref,
            token_estimate,
        ) = await self._compact_messages_for_proposal(state)
        effective_prepared = None
        if self.context_os_v1:
            validator = getattr(self.tool_registry, "validate_prepared_tool_set", None)
            if not callable(validator) or self.eligibility is None:
                raise RuntimeError("context_os_code_proposal_validation_unavailable")
            effective_prepared = self._effective_prepared_tool_set(state)
        tool_schemas = self._tool_schemas(effective_prepared)
        declined_dynamic_tools = _declined_dynamic_tool_names(state)
        if declined_dynamic_tools:
            tool_schemas = [
                schema
                for schema in tool_schemas
                if _schema_tool_name(schema) not in declined_dynamic_tools
            ]
        discovery_saturated = (
            _consecutive_discovery_searches(state)
            >= _MAX_CONSECUTIVE_DISCOVERY_SEARCHES
            or _consecutive_discovery_turns(state.messages)
            >= _MAX_CONSECUTIVE_DISCOVERY_SEARCHES
        )
        if discovery_saturated:
            # The latest successful search result remains in the conversation,
            # together with its exact capability ids and next actions. Hiding
            # only the repeatable search entrypoints makes the provider choose
            # describe/activate or an already available action instead of
            # ignoring a fourth structured loop rejection.
            tool_schemas = [
                schema
                for schema in tool_schemas
                if _schema_tool_name(schema)
                not in _REPEATABLE_DISCOVERY_SEARCH_TOOL_NAMES
            ]
        effective_allowed = (
            set(self.allowed_tools) if self.allowed_tools is not None else None
        )
        dynamically_activated = set()
        if effective_allowed is not None and effective_prepared is not None:
            dynamically_activated.update(
                capability.ref.name for capability in effective_prepared.activated
            )
            effective_allowed.update(dynamically_activated)
        deferred_by_name = (
            {
                str(ref.name): ref
                for ref in getattr(effective_prepared, "deferred", ())
            }
            if effective_prepared is not None
            else {}
        )
        from agent.context_messages import provider_purpose_scope

        with provider_purpose_scope(
            "workflow",
            session_id=(
                getattr(self.eligibility, "session_id", None)
                if self.eligibility is not None
                else None
            ),
            request_id=state.request_id,
        ):
            proposal_messages = _compact_repeatable_search_payloads(
                context_messages
            )
            response = await coordinate_provider_call(
                coordinator=self.provider_invocation_coordinator,
                acquire_fence=self.dispatch_fence_acquirer,
                run_id=str(
                    getattr(self.execution_context, "run_id", "")
                    or state.request_id
                ),
                session_id=self.session_id,
                root_run_id=(
                    str(
                        getattr(self.execution_context, "root_run_id", "")
                        or ""
                    ).strip()
                    or None
                ),
                parent_run_id=(
                    str(
                        getattr(self.execution_context, "parent_run_id", "")
                        or ""
                    ).strip()
                    or None
                ),
                profile_key=self.profile_key,
                provider=self.llm_registry,
                attempt_id=(
                    f"{state.request_id}:{state.turn_id}:proposal:"
                    f"node-attempt:{execution_identity.attempt}"
                    if execution_identity is not None
                    else f"{state.request_id}:{state.turn_id}:proposal"
                ),
                # Durable child proposals are still the Run's main agent
                # response boundary.  Keep this canonical purpose aligned
                # with the root ReAct path so provider fencing, fault
                # injection, and workload audit share one identity contract.
                purpose="agent_response",
                messages=proposal_messages,
                tools=tool_schemas,
                fallback_model=str(self.model_name or ""),
                invoke=lambda: self.llm_registry.chat_with_fallback(
                    proposal_messages,
                    tools=tool_schemas,
                ),
            )
        if not isinstance(response, ChatResponse):
            raise TypeError("code proposal provider must return ChatResponse")

        checkpoint_key = f"{state.request_id}:{state.turn_id}"
        raw_proposals: list[dict[str, JsonValue]] = []
        prepared_calls: list[PreparedToolCall] = []
        truncated_without_tool_call = (
            str(response.stop_reason or "") == "length"
            and not response.tool_calls
        )
        proposal_error: ProposalErrorV1 | None = (
            ProposalErrorV1(
                code="proposal_truncated_without_tool_call",
                message_ref="proposal:truncated_without_tool_call",
                retryable=True,
                details={
                    "instruction": (
                        "The prior response reached its output limit without "
                        "executing any tool. Do not continue code or file "
                        "content as prose. Call file_write directly using a "
                        "compact payload or overwrite followed by append "
                        "chunks of at most 2500 characters."
                    )
                },
            )
            if truncated_without_tool_call
            else None
        )
        for index, tool_call in enumerate(response.tool_calls):
            raw_args = copy.deepcopy(dict(tool_call.arguments or {}))
            validate_json_value(raw_args)
            stable_call_id = derive_stable_call_id(
                checkpoint_key=checkpoint_key,
                iteration=state.iteration,
                index=index,
                tool_name=tool_call.name,
                raw_args=raw_args,
            )
            spec = self.tool_registry.get(tool_call.name)
            policy = getattr(spec, "effect_policy", None) if spec is not None else None
            policy_kind = getattr(getattr(policy, "kind", ""), "value", getattr(policy, "kind", ""))
            raw: dict[str, JsonValue] = {
                "stable_call_id": stable_call_id,
                "provider_call_id": str(tool_call.id or ""),
                "tool_name": str(tool_call.name),
                "raw_params": raw_args,
                "source": _source_kind(getattr(spec, "source", "unknown")),
                "access": (
                    "read"
                    if str(policy_kind) in {"idempotent_read", "deterministic_reusable"}
                    else "write"
                ),
            }
            if str(tool_call.name) in dynamically_activated:
                # The graph's frozen capability snapshot intentionally omits
                # deferred schemas.  Preserve the Context OS admission proof
                # so the graph does not reject a tool that was activated and
                # validated after the workflow started.
                raw["capability_admission"] = "context_os_activated"
            if tool_call.args_parse_error:
                raw["args_parse_error"] = str(tool_call.args_parse_error)
                proposal_error = proposal_error or ProposalErrorV1(
                    code="tool_call_args_malformed_json",
                    message_ref=f"proposal:{stable_call_id}:malformed_args",
                    retryable=True,
                )
            elif (
                effective_allowed is not None
                and str(tool_call.name) not in effective_allowed
            ):
                deferred_ref = deferred_by_name.get(str(tool_call.name))
                if deferred_ref is not None:
                    proposal_error = proposal_error or ProposalErrorV1(
                        code="tool_requires_activation",
                        message_ref=(
                            f"proposal:{stable_call_id}:activation_required"
                        ),
                        retryable=True,
                        details={
                            "tool_name": str(tool_call.name),
                            "capability_id": str(deferred_ref.capability_id),
                            "recovery": [
                                "tool_search",
                                "tool_describe",
                                "tool_activate",
                            ],
                            "instruction": (
                                "Do not repeat this tool call yet. Search the "
                                "request-scoped deferred tools with tool_search, "
                                "copy its exact capability_id into tool_describe, "
                                "then call tool_activate with the returned "
                                "schema_hash and describe_nonce."
                            ),
                        },
                    )
                else:
                    proposal_error = proposal_error or ProposalErrorV1(
                        code="tool_outside_capability_snapshot",
                        message_ref=f"proposal:{stable_call_id}:capability_mismatch",
                        retryable=False,
                        details={"tool_name": str(tool_call.name)},
                    )
            elif spec is None:
                proposal_error = proposal_error or ProposalErrorV1(
                    code="unknown_tool",
                    message_ref=f"proposal:{stable_call_id}:unknown_tool",
                    retryable=True,
                    details={"tool_name": str(tool_call.name)},
                )
            else:
                execution_context = self.execution_context
                if execution_context is not None and hasattr(
                    execution_context, '__dataclass_fields__'
                ):
                    execution_context = replace(
                        execution_context,
                        call_id=stable_call_id,
                        effect_id='',
                        capability_hash=(
                            str(effective_prepared.schema_fingerprint)
                            if effective_prepared is not None
                            else execution_context.capability_hash
                        ),
                    )
                prepare_call = self.tool_registry.prepare_call
                execution_kwargs = (
                    {'execution_context': execution_context}
                    if execution_context is not None
                    and 'execution_context' in inspect.signature(prepare_call).parameters
                    else {}
                )
                try:
                    prepared_call = prepare_call(
                        tool_call.name,
                        raw_args,
                        self.session_id,
                        stable_call_id,
                        **execution_kwargs,
                    )
                except ValueError as exc:
                    # Model-supplied arguments can be rejected while the
                    # registry resolves deterministic resource selectors
                    # (for example a desktop filename containing a path
                    # separator).  That is a recoverable proposal rejection,
                    # not a permanent workflow-node failure.
                    reason = str(exc)
                    raw["prepare_error"] = reason
                    proposal_error = proposal_error or ProposalErrorV1(
                        code="tool_prepare_rejected",
                        message_ref=f"proposal:{stable_call_id}:prepare_rejected",
                        retryable=True,
                        details={
                            "tool_name": str(tool_call.name),
                            "reason": reason,
                            "instruction": (
                                "Correct the tool arguments or choose a more "
                                "appropriate capability, then continue the "
                                "existing task."
                            ),
                        },
                    )
                else:
                    try:
                        if self.output_contract is not None:
                            self.output_contract.validate_prepared_call(
                                prepared_call
                            )
                    except ValueError as exc:
                        reason = str(exc)
                        raw["prepare_error"] = reason
                        proposal_error = proposal_error or ProposalErrorV1(
                            code="tool_output_contract_violation",
                            message_ref=(
                                f"proposal:{stable_call_id}:output_contract"
                            ),
                            retryable=True,
                            details={
                                "tool_name": str(tool_call.name),
                                "reason": reason,
                                "output_refs": list(
                                    self.output_contract.output_refs
                                ),
                                "scratch_refs": list(
                                    self.output_contract.scratch_refs
                                ),
                                "instruction": (
                                    "Use only a declared output_ref or "
                                    "scratch_ref. Do not create helper files "
                                    "outside the frozen task output contract."
                                ),
                            },
                        )
                    else:
                        prepared_calls.append(prepared_call)
            raw_proposals.append(raw)

        reasoning_ref = None
        if response.reasoning_content:
            digest = hashlib.sha256(response.reasoning_content.encode("utf-8")).hexdigest()
            reasoning_ref = f"reasoning:sha256:{digest}"
        provider = str(
            getattr(self.llm_registry, "name", "")
            or self.provider_name
            or state.provider_snapshot.get("provider")
            or "runtime"
        )
        model = str(
            response.model
            or getattr(self.llm_registry, "model", "")
            or self.model_name
            or state.model_snapshot.get("model")
            or ""
        )
        return ProposalOutcomeV1(
            # Unexecuted truncated prose is neither a receipt nor useful
            # continuation context.  Persist only the host's structured
            # recovery instruction below so the next provider turn converges
            # on a bounded tool call instead of extending the same code dump.
            assistant_content=(
                "" if truncated_without_tool_call else response.content
            ),
            reasoning_summary_ref=reasoning_ref,
            raw_tool_proposals=raw_proposals,
            prepared_calls=prepared_calls,
            stop_reason=response.stop_reason,
            usage=_model_dump(response.usage),
            provider=provider,
            model=model,
            compacted_messages=compacted_messages,
            compaction_summary=compaction_summary,
            compaction_ref=compaction_ref,
            token_estimate=token_estimate,
            error=proposal_error,
        )


def derive_effect_id(workflow_step_id: str, prepared: PreparedToolCall) -> str:
    payload: dict[str, JsonValue] = {
        "workflow_step_id": workflow_step_id,
        "stable_call_id": prepared.stable_call_id,
        "tool_name": prepared.tool_name,
        "args_hash": prepared.args_hash,
    }
    digest = hashlib.sha256(canonical_json(payload).encode("ascii")).hexdigest()
    return f"effect-{digest[:32]}"


async def _resolve_effect_context(
    source: object | None, operation: str
) -> EffectExecutionContext | None:
    if source is None:
        return None
    selected = source.get(operation) if isinstance(source, Mapping) else source
    if callable(selected):
        selected = selected(operation)
    if inspect.isawaitable(selected):
        selected = await selected
    if not isinstance(selected, EffectExecutionContext):
        raise TypeError(f"effect context for {operation} is unavailable")
    return selected


async def _production_effect_context(
    *, session_id: str, workflow_name: str = "code_complex"
) -> EffectExecutionContext | None:
    user_data_dir = os.environ.get("DESKPET_USER_DATA_DIR")
    if not user_data_dir:
        return None
    journal = EffectJournal(Path(user_data_dir) / "data" / "workflow.db")
    return await journal.resolve_active_context(
        workflow_name=workflow_name,
        workflow_version="v1",
        node_id="tool_execution",
        session_id=session_id,
    )


def _normalize_registry_outcome(raw: object) -> NormalizedToolOutcome:
    if isinstance(raw, NormalizedToolOutcome):
        return raw
    if not isinstance(raw, Mapping):
        return NormalizedToolOutcome.malformed(
            "tool execution outcome must be normalized or a JSON object"
        )
    value = copy.deepcopy(dict(raw))
    validate_json_value(value)
    status = str(value.get("status") or value.get("state") or "unknown")
    ok = bool(value.get("ok", status in {"success", "completed", "committed"}))
    if ok:
        return NormalizedToolOutcome.success(value)
    return NormalizedToolOutcome.failure(
        str(value.get("code") or "tool_execution_failed"),
        str(value.get("message") or value.get("error") or status),
        value=value,
    )


def _target_evidence(path: str) -> dict[str, JsonValue]:
    candidate = Path(path)
    if not candidate.is_file():
        raise EffectStateConflict(f"effect target was not committed: {path}")
    digest = hashlib.sha256()
    with candidate.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(candidate),
        "sha256": digest.hexdigest(),
        "size": candidate.stat().st_size,
    }


class ToolDispatchPort:
    """Execute frozen registry calls and return strict JSON outcomes."""

    def __init__(
        self,
        tool_registry: object,
        *,
        session_id: str,
        effect_context: object | None = None,
        execution_context: object | None = None,
        runtime_tool_state: DurableToolRuntimeState | None = None,
        workflow_name: str = "code_complex",
        dispatch_fence_acquirer: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.session_id = session_id
        self.effect_context = effect_context
        self.execution_context = execution_context
        self.runtime_tool_state = runtime_tool_state
        self.workflow_name = workflow_name
        self.dispatch_fence_acquirer = dispatch_fence_acquirer

    def _effective_execution_context(
        self, *, prepared: PreparedToolCall, effect_id: str
    ) -> object | None:
        context = self.execution_context
        if context is None or not hasattr(context, "__dataclass_fields__"):
            return context
        capability_hash = context.capability_hash
        if self.runtime_tool_state is not None:
            capability_hash = str(
                self.runtime_tool_state.prepared_tool_set.schema_fingerprint
            )
        return replace(
            context,
            call_id=prepared.stable_call_id,
            effect_id=effect_id,
            capability_hash=capability_hash,
        )

    @staticmethod
    def _authorization(
        raw: object,
        *,
        prepared: PreparedToolCall,
        effect_id: str,
        session_id: str,
        execution_context: object | None = None,
    ) -> object | None:
        if not isinstance(raw, Mapping):
            return raw
        grant = copy.deepcopy(dict(raw))
        if grant.get("action") == "allow_once_opaque":
            grant.update(
                {
                    "run_id": str(getattr(execution_context, "run_id", "") or ""),
                    "call_id": prepared.stable_call_id,
                    "effect_id": effect_id,
                    "tool_name": prepared.tool_name,
                    "args_hash": prepared.args_hash,
                    "capability_hash": str(
                        getattr(execution_context, "capability_hash", "") or ""
                    ),
                    "scope_hash": str(
                        getattr(execution_context, "scope_hash", "") or ""
                    ),
                    "permission_policy_version": prepared.permission_policy_version,
                    "session_id": session_id,
                    "expires_at": time.time() + 300.0,
                }
            )
        return grant

    async def _execute(
        self,
        prepared: PreparedToolCall,
        *,
        effect_id: str,
        authorization: object | None,
    ) -> object:
        execution_context = self.execution_context
        if execution_context is not None and hasattr(
            execution_context, '__dataclass_fields__'
        ):
            capability_hash = execution_context.capability_hash
            if self.runtime_tool_state is not None:
                capability_hash = str(
                    self.runtime_tool_state.prepared_tool_set.schema_fingerprint
                )
            execution_context = replace(
                execution_context,
                call_id=prepared.stable_call_id,
                effect_id=effect_id,
                capability_hash=capability_hash,
            )
        execute_prepared = getattr(self.tool_registry, "execute_prepared", None)
        execute_outcome = getattr(self.tool_registry, "execute_tool_outcome", None)
        if callable(execute_prepared):
            execution_kwargs = (
                {'execution_context': execution_context}
                if execution_context is not None
                and 'execution_context' in inspect.signature(execute_prepared).parameters
                else {}
            )
            outcome = await dispatch_with_run_fence(
                acquire_fence=self.dispatch_fence_acquirer,
                run_id=str(
                    getattr(execution_context, "run_id", "")
                    or getattr(self.execution_context, "run_id", "")
                    or self.session_id
                ),
                operation_kind="workflow.code.execute_prepared",
                operation_id=effect_id,
                invoke=lambda: execute_prepared(
                    prepared,
                    effect_id=effect_id,
                    authorization=authorization,
                    **execution_kwargs,
                ),
            )
            if (
                isinstance(outcome, NormalizedToolOutcome)
                and outcome.state is ToolOutcomeState.FAILURE
                and isinstance(outcome.error, Mapping)
                and outcome.error.get("code") == "authorization_required"
                and prepared.effect_type == "idempotent_read"
                and callable(execute_outcome)
            ):
                execution_kwargs = (
                    {"execution_context": execution_context}
                    if execution_context is not None
                    else {}
                )
                return await dispatch_with_run_fence(
                    acquire_fence=self.dispatch_fence_acquirer,
                    run_id=str(
                        getattr(execution_context, "run_id", "")
                        or getattr(self.execution_context, "run_id", "")
                        or self.session_id
                    ),
                    operation_kind="workflow.code.execute_tool_outcome",
                    operation_id=effect_id,
                    invoke=lambda: execute_outcome(
                        prepared.tool_name,
                        prepared.arguments_json(),
                        self.session_id,
                        effect_id,
                        **execution_kwargs,
                    ),
                )
            return outcome
        if callable(execute_outcome):
            execution_kwargs = (
                    {"execution_context": execution_context}
                    if execution_context is not None
                else {}
            )
            return await dispatch_with_run_fence(
                acquire_fence=self.dispatch_fence_acquirer,
                run_id=str(
                    getattr(execution_context, "run_id", "")
                    or getattr(self.execution_context, "run_id", "")
                    or self.session_id
                ),
                operation_kind="workflow.code.execute_tool_outcome",
                operation_id=effect_id,
                invoke=lambda: execute_outcome(
                    prepared.tool_name,
                    prepared.arguments_json(),
                    self.session_id,
                    effect_id,
                    **execution_kwargs,
                ),
            )
        raise TypeError("tool registry has no durable execution API")

    async def _execute_journaled(
        self,
        prepared: PreparedToolCall,
        *,
        workflow_step_id: str,
        authorization: object | None,
        context: EffectExecutionContext,
    ) -> tuple[str, NormalizedToolOutcome]:
        resolve_prepared_spec = getattr(
            self.tool_registry, "resolve_prepared_spec", None
        )
        spec = (
            resolve_prepared_spec(prepared)
            if callable(resolve_prepared_spec)
            else self.tool_registry.get(prepared.tool_name)
        )
        policy = getattr(spec, "effect_policy", None) if spec is not None else None
        if not isinstance(policy, EffectPolicy):
            raise EffectStateConflict(
                f"prepared tool {prepared.tool_name} has no durable effect policy"
            )
        begun = await context.journal.begin(
            context.fence,
            node_execution_id=context.node_execution_id,
            workflow_name=context.workflow_name,
            workflow_version=context.workflow_version,
            node_id=context.node_id,
            logical_effect_key=f"{workflow_step_id}:{prepared.stable_call_id}",
            prepared=prepared,
            policy=policy,
            reuse_checkpoint=context.reuse_checkpoint,
        )
        effect_id = begun.effect.effect_id
        if begun.action in {EffectAction.REUSE, EffectAction.FAILED}:
            if begun.effect.outcome is None:
                raise EffectStateConflict(
                    f"terminal effect {effect_id} has no durable outcome"
                )
            return effect_id, begun.effect.outcome
        if begun.action is not EffectAction.EXECUTE:
            raise EffectStateConflict(
                f"effect {effect_id} is {begun.effect.status.value}; execution is blocked"
            )
        authorization = self._authorization(
            authorization,
            prepared=prepared,
            effect_id=effect_id,
            session_id=self.session_id,
            execution_context=self._effective_execution_context(
                prepared=prepared, effect_id=effect_id
            ),
        )
        try:
            raw = await self._execute(
                prepared,
                effect_id=effect_id,
                authorization=authorization,
            )
            outcome = _normalize_registry_outcome(raw)
            metadata_reader = getattr(
                self.tool_registry, "take_prepared_execution_metadata", None
            )
            execution_metadata = (
                dict(metadata_reader(effect_id))
                if callable(metadata_reader)
                else {}
            )
            if outcome.state is ToolOutcomeState.SUCCESS:
                for target in prepared.prepared_targets:
                    evidence = _target_evidence(target.final_path)
                    await context.journal.record_target_state(
                        context.fence,
                        effect_id,
                        target.reservation_key,
                        expected=("prepared",),
                        status="staged",
                        evidence=evidence,
                    )
                    await context.journal.record_target_state(
                        context.fence,
                        effect_id,
                        target.reservation_key,
                        expected=("staged",),
                        status="committing",
                        evidence=evidence,
                    )
                    await context.journal.record_target_state(
                        context.fence,
                        effect_id,
                        target.reservation_key,
                        expected=("committing",),
                        status="committed",
                        evidence=evidence,
                    )
            committed = await context.journal.commit(
                context.fence,
                effect_id,
                outcome,
                receipt_ref=(
                    str(execution_metadata.get("receipt_ref") or "").strip()
                    or None
                ),
                artifact_refs=tuple(
                    str(item)
                    for item in execution_metadata.get("artifact_refs", ())
                    if str(item).strip()
                ),
            )
        except BaseException as exc:
            await context.journal.mark_uncertain(
                context.fence,
                effect_id,
                f"execute_prepared raised {type(exc).__name__}",
            )
            raise
        if committed.outcome is None:
            raise EffectStateConflict(f"effect {effect_id} committed without an outcome")
        acknowledge = getattr(self.tool_registry, "acknowledge_prepared_effect", None)
        if callable(acknowledge):
            acknowledge(effect_id)
        return effect_id, committed.outcome

    @staticmethod
    def _result_payload(
        raw: object,
        *,
        prepared: PreparedToolCall,
        effect_id: str,
        workflow_step_id: str,
    ) -> dict[str, JsonValue]:
        if isinstance(raw, NormalizedToolOutcome):
            outcome = raw.to_dict()
            status = raw.state.value
            ok = raw.state is ToolOutcomeState.SUCCESS
        elif isinstance(raw, Mapping):
            outcome = copy.deepcopy(dict(raw))
            validate_json_value(outcome)
            status = str(outcome.get("status") or outcome.get("state") or "unknown")
            ok = bool(outcome.get("ok", status in {"success", "completed", "committed"}))
        else:
            raise TypeError("tool execution outcome must be normalized or a JSON object")
        payload: dict[str, JsonValue] = {
            "stable_call_id": prepared.stable_call_id,
            "effect_id": effect_id,
            "workflow_step_id": workflow_step_id,
            "args_hash": prepared.args_hash,
            "ok": ok,
            "status": status,
            "outcome": outcome,
        }
        validate_json_value(payload)
        return payload

    async def dispatch(
        self,
        prepared_calls: Sequence[PreparedToolCall],
        *,
        workflow_step_id: str,
        prior_results: Mapping[str, JsonValue],
        authorizations: Mapping[str, JsonValue],
    ) -> Mapping[str, JsonValue]:
        results: dict[str, JsonValue] = {}
        for prepared in prepared_calls:
            previous = prior_results.get(prepared.stable_call_id)
            if isinstance(previous, Mapping):
                reused = copy.deepcopy(dict(previous))
                validate_json_value(reused)
                results[prepared.stable_call_id] = reused
                continue
            context = await _resolve_effect_context(
                self.effect_context, "execute_prepared"
            )
            if context is None:
                context = await _production_effect_context(
                    session_id=self.session_id,
                    workflow_name=self.workflow_name,
                )
            effect_id = derive_effect_id(workflow_step_id, prepared)
            authorization = self._authorization(
                authorizations.get(prepared.stable_call_id),
                prepared=prepared,
                effect_id=effect_id,
                session_id=self.session_id,
                execution_context=self._effective_execution_context(
                    prepared=prepared, effect_id=effect_id
                ),
            )
            if context is None:
                raw = await self._execute(
                    prepared,
                    effect_id=effect_id,
                    authorization=authorization,
                )
            else:
                effect_id, raw = await self._execute_journaled(
                    prepared,
                    workflow_step_id=workflow_step_id,
                    authorization=authorizations.get(prepared.stable_call_id),
                    context=context,
                )
            results[prepared.stable_call_id] = self._result_payload(
                raw,
                prepared=prepared,
                effect_id=effect_id,
                workflow_step_id=workflow_step_id,
            )
        validate_json_value(results)
        return results


__all__ = [
    "ProposalPort",
    "ToolDispatchPort",
    "DurableToolRuntimeState",
    "capability_snapshot",
    "derive_effect_id",
    "workflow_session_ref",
]

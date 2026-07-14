# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Production ports for the durable Complex Code v1 workflow."""

from __future__ import annotations

import copy
import hashlib
import inspect
import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from llm.types import ChatResponse
from deskpet.tools.capabilities import canonical_hash

from ..contracts import EffectPolicy, JsonValue, canonical_json, validate_json_value
from ..definitions.code_nodes import (
    FORBIDDEN_DURABLE_TOOLS,
    CapabilitySnapshotV1,
    WorkflowSessionRefV1,
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


def capability_snapshot(tool_registry: object) -> list[CapabilitySnapshotV1]:
    """Snapshot exactly the tools exposed to this proposal port."""

    schemas = tool_registry.schemas()
    visible = {
        _schema_tool_name(schema)
        for schema in schemas
        if isinstance(schema, Mapping)
    }
    visible.discard("")
    visible.difference_update(FORBIDDEN_DURABLE_TOOLS)
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
        context_os_v1: bool = False,
        prepared_tool_set: object | None = None,
        eligibility: object | None = None,
    ) -> None:
        self.llm_registry = llm_registry
        self.tool_registry = tool_registry
        self.session_id = session_id
        self.provider_name = provider_name
        self.model_name = model_name
        self.context_os_v1 = bool(context_os_v1)
        self.prepared_tool_set = prepared_tool_set
        self.eligibility = eligibility
        if self.context_os_v1 and self.prepared_tool_set is None:
            raise ValueError("context_os_code_proposal_requires_prepared_tool_set")

    def _tool_schemas(self) -> list[dict[str, Any]]:
        if self.context_os_v1:
            schemas = [
                copy.deepcopy(dict(schema))
                for schema in self.prepared_tool_set.logical_schemas()
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
            expected = str(self.prepared_tool_set.schema_fingerprint)
            if actual != expected:
                raise ValueError(
                    f"prepared_schema_fingerprint_mismatch:{expected}:{actual}"
                )
            return schemas
        return [
            copy.deepcopy(dict(schema))
            for schema in self.tool_registry.schemas()
            if isinstance(schema, Mapping)
            and _schema_tool_name(schema) not in FORBIDDEN_DURABLE_TOOLS
        ]

    async def propose(self, state: ProposalStateV1) -> ProposalOutcomeV1:
        if self.context_os_v1:
            validator = getattr(self.tool_registry, "validate_prepared_tool_set", None)
            if not callable(validator) or self.eligibility is None:
                raise RuntimeError("context_os_code_proposal_validation_unavailable")
            validator(self.prepared_tool_set, eligibility=self.eligibility)
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
            response = await self.llm_registry.chat_with_fallback(
                [copy.deepcopy(dict(message)) for message in state.messages],
                tools=self._tool_schemas(),
            )
        if not isinstance(response, ChatResponse):
            raise TypeError("code proposal provider must return ChatResponse")

        checkpoint_key = f"{state.request_id}:{state.turn_id}"
        raw_proposals: list[dict[str, JsonValue]] = []
        prepared_calls: list[PreparedToolCall] = []
        proposal_error: ProposalErrorV1 | None = None
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
            if tool_call.args_parse_error:
                raw["args_parse_error"] = str(tool_call.args_parse_error)
                proposal_error = proposal_error or ProposalErrorV1(
                    code="tool_call_args_malformed_json",
                    message_ref=f"proposal:{stable_call_id}:malformed_args",
                    retryable=True,
                )
            elif spec is None:
                proposal_error = proposal_error or ProposalErrorV1(
                    code="unknown_tool",
                    message_ref=f"proposal:{stable_call_id}:unknown_tool",
                    retryable=True,
                    details={"tool_name": str(tool_call.name)},
                )
            else:
                prepared_calls.append(
                    self.tool_registry.prepare_call(
                        tool_call.name,
                        raw_args,
                        self.session_id,
                        stable_call_id,
                    )
                )
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
            assistant_content=response.content,
            reasoning_summary_ref=reasoning_ref,
            raw_tool_proposals=raw_proposals,
            prepared_calls=prepared_calls,
            stop_reason=response.stop_reason,
            usage=_model_dump(response.usage),
            provider=provider,
            model=model,
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
    *, session_id: str
) -> EffectExecutionContext | None:
    user_data_dir = os.environ.get("DESKPET_USER_DATA_DIR")
    if not user_data_dir:
        return None
    journal = EffectJournal(Path(user_data_dir) / "data" / "workflow.db")
    return await journal.resolve_active_context(
        workflow_name="code_complex",
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
    ) -> None:
        self.tool_registry = tool_registry
        self.session_id = session_id
        self.effect_context = effect_context
        self.execution_context = execution_context

    @staticmethod
    def _authorization(
        raw: object,
        *,
        prepared: PreparedToolCall,
        effect_id: str,
        session_id: str,
    ) -> object | None:
        if not isinstance(raw, Mapping):
            return raw
        grant = copy.deepcopy(dict(raw))
        if grant.get("action") == "allow_once_opaque":
            grant.update(
                {
                    "effect_id": effect_id,
                    "tool_name": prepared.tool_name,
                    "args_hash": prepared.args_hash,
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
        execute_prepared = getattr(self.tool_registry, "execute_prepared", None)
        execute_outcome = getattr(self.tool_registry, "execute_tool_outcome", None)
        if callable(execute_prepared):
            outcome = await execute_prepared(
                prepared,
                effect_id=effect_id,
                authorization=authorization,
            )
            if (
                isinstance(outcome, NormalizedToolOutcome)
                and outcome.state is ToolOutcomeState.FAILURE
                and isinstance(outcome.error, Mapping)
                and outcome.error.get("code") == "authorization_required"
                and callable(execute_outcome)
            ):
                execution_kwargs = (
                    {"execution_context": self.execution_context}
                    if self.execution_context is not None
                    else {}
                )
                return await execute_outcome(
                    prepared.tool_name,
                    dict(prepared.final_params),
                    self.session_id,
                    effect_id,
                    **execution_kwargs,
                )
            return outcome
        if callable(execute_outcome):
            execution_kwargs = (
                {"execution_context": self.execution_context}
                if self.execution_context is not None
                else {}
            )
            return await execute_outcome(
                prepared.tool_name,
                dict(prepared.final_params),
                self.session_id,
                effect_id,
                **execution_kwargs,
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
        spec = self.tool_registry.get(prepared.tool_name)
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
        )
        try:
            raw = await self._execute(
                prepared,
                effect_id=effect_id,
                authorization=authorization,
            )
            outcome = _normalize_registry_outcome(raw)
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
                context = await _production_effect_context(session_id=self.session_id)
            effect_id = derive_effect_id(workflow_step_id, prepared)
            authorization = self._authorization(
                authorizations.get(prepared.stable_call_id),
                prepared=prepared,
                effect_id=effect_id,
                session_id=self.session_id,
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
    "capability_snapshot",
    "derive_effect_id",
    "workflow_session_ref",
]

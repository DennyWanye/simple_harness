"""AgentLoop adaptation contracts and collaborator used by ReActDriver."""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import logging
from dataclasses import dataclass, field, replace
from types import MappingProxyType, SimpleNamespace
from typing import Any, AsyncIterator, Mapping

from deskpet.execution.contracts import thaw_json
from deskpet.execution.evidence import UNKNOWN_EVIDENCE
from deskpet.harness.live_index import BoundedLiveIndex
from deskpet.harness.ports import DriverEvent, DriverStart
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.effects import PreparedToolCall

from .react_boundary import (
    ReactCommandBoundary,
    _skill_tool_intersection,
)

logger = logging.getLogger(__name__)

_CATALOG_MUTATING_TOOLS = frozenset(
    {
        "capability_install",
        "capability_update",
        "capability_repair",
        "capability_uninstall",
        "capability_rollback",
    }
)
_MAX_PROVIDER_TOOL_CALLS_PER_BATCH = 32
_MIN_TRUNCATED_CONTROL_REPLAY_PREFIX_CHARS = 32


@dataclass(frozen=True)
class ReactToken:
    content: str
    kind: str = 'content'
    invocation_id: str | None = None
    stream_epoch: str | None = None
    provisional: bool = False
    retract_provisional: bool = False
@dataclass(frozen=True)
class ReactFallback:
    from_provider: str
    to_provider: str
    reason: str
@dataclass(frozen=True)
class ReactToolBatch:
    command_id: str
    calls: tuple[PreparedToolCall, ...]
    contexts: tuple[ToolExecutionContext, ...]
    canonical_messages: tuple[Mapping[str, Any], ...] = ()
    iteration: int = 0
    feedback_state: Mapping[str, Any] = field(default_factory=dict)
    raw_failures: tuple[Mapping[str, Any], ...] = ()
    provider_call_order: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.command_id:
            raise ValueError('tool batch command_id is required')
        if len(self.calls) != len(self.contexts):
            raise ValueError('tool batch calls and contexts must align')
        if not self.calls and not self.raw_failures:
            raise ValueError('tool batch must contain a prepared call or raw failure')
        raw = tuple(
            MappingProxyType(dict(thaw_json(item)))
            for item in self.raw_failures
        )
        object.__setattr__(self, "raw_failures", raw)
        identities = (
            *(call.stable_call_id for call in self.calls),
            *(str(item.get("provider_call_id") or "") for item in raw),
        )
        if any(not item for item in identities) or len(set(identities)) != len(
            identities
        ):
            raise ValueError("provider call identities must be unique and non-empty")
        order = self.provider_call_order or identities
        if len(order) != len(identities) or set(order) != set(identities):
            raise ValueError("provider call order must cover each call exactly once")
        object.__setattr__(self, "provider_call_order", tuple(order))


@dataclass(frozen=True)
class ReactControlBatch:
    command_id: str
    call: PreparedToolCall
    context: ToolExecutionContext
    canonical_messages: tuple[Mapping[str, Any], ...]
    iteration: int = 0
    feedback_state: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.command_id:
            raise ValueError("control batch command_id is required")


@dataclass(frozen=True)
class ReactFinal:
    content: str
@dataclass(frozen=True)
class ReactFailure:
    error: str
    error_code: str = "react_failure"
    source_layer: str = "driver"
ReactEmission = ReactToken | ReactFallback | ReactToolBatch | ReactControlBatch | DriverEvent | ReactFinal | ReactFailure
class AgentLoopToolInterceptionError(RuntimeError):
    pass
class AgentLoopCollaborator:
    """Map provider turns to typed Driver emissions; never execute tools."""

    def __init__(
        self,
        loop_factory: Any,
        *,
        call_factory: Any | None = None,
        delegation_factory: Any | None = None,
        provider_invocation_coordinator: Any | None = None,
        provider_fence_acquirer: Any | None = None,
        capability_scope_store: Any | None = None,
    ) -> None:
        self._loop_factory = loop_factory
        self._call_factory = call_factory
        self._delegation_factory = delegation_factory
        self._provider_invocation_coordinator = provider_invocation_coordinator
        self._provider_fence_acquirer = provider_fence_acquirer
        self._capability_scope_store = capability_scope_store
        self._live: BoundedLiveIndex | None = None
    def bind_live_index(self, live: BoundedLiveIndex) -> None:
        if self._live is not None and self._live is not live:
            raise RuntimeError('AgentLoop collaborator is already bound to another live index')
        self._live = live

    def _bound_live(self) -> BoundedLiveIndex:
        if self._live is None:
            raise RuntimeError('AgentLoop collaborator requires the Kernel live index')
        return self._live

    async def _loop_for(self, request: DriverStart) -> Any:
        active = self._bound_live().get(request.run_id)
        if active is None:
            raise RuntimeError("AgentLoop collaborator run is absent")
        if active.driver_runtime is None:
            created = self._loop_factory(request)
            active.driver_runtime = await created if inspect.isawaitable(created) else created
            runtime = active.driver_runtime
            loop = runtime[0] if isinstance(runtime, tuple) else runtime
            if self._provider_invocation_coordinator is not None:
                setattr(
                    loop,
                    "_provider_invocation_coordinator",
                    self._provider_invocation_coordinator,
                )
                setattr(
                    loop,
                    "_provider_fence_acquirer",
                    self._provider_fence_acquirer,
                )
            setattr(loop, "_provider_run_id", request.run_id)
            setattr(loop, "_provider_session_id", request.session_id)
            setattr(
                loop,
                "_provider_root_run_id",
                (
                    request.run_id
                    if request.run_context is None
                    else request.run_context.root_run_id
                ),
            )
            setattr(
                loop,
                "_provider_parent_run_id",
                (
                    None
                    if request.run_context is None
                    else request.run_context.parent_run_id
                ),
            )
            setattr(loop, "_provider_profile_key", request.profile_key)
            setattr(
                loop,
                "_context_usage_basis_sample_id",
                str(
                    request.request_payload.get(
                        "context_usage_basis_sample_id"
                    )
                    or ""
                ),
            )
        return active.driver_runtime

    async def _close_iterator(self, active: Any) -> None:
        iterator = None if active is None else active.driver_iterator
        close = getattr(iterator, 'aclose', None)
        try:
            if callable(close):
                await close()
        finally:
            if iterator is not None and active.driver_iterator is iterator:
                active.driver_iterator = None

    async def reset_runtime(self, run_id: str) -> None:
        """Drop the old provider runtime before a refreshed continuation resumes."""

        active = self._bound_live().get(run_id)
        if active is None:
            raise RuntimeError("AgentLoop collaborator run is absent")
        await self._close_iterator(active)
        active.driver_runtime = None

    async def prepare_control(
        self, request: DriverStart, call: PreparedToolCall
    ) -> DriverEvent:
        if self._delegation_factory is None:
            raise AgentLoopToolInterceptionError(
                "delegate control requires a product delegate factory"
            )
        delegated = self._delegation_factory(request, call)
        if inspect.isawaitable(delegated):
            delegated = await delegated
        if not isinstance(delegated, DriverEvent) or delegated.kind not in {
            "delegate_run",
            "open_decision",
        }:
            raise AgentLoopToolInterceptionError(
                f"delegate control was not mapped: {call.tool_name}"
            )
        return delegated

    @staticmethod
    def _allowed_tool_names(request: DriverStart) -> tuple[str, ...] | None:
        snapshot = request.capability_snapshot
        if "tools" in snapshot:
            raw = snapshot["tools"]
        elif "capabilities" in snapshot:
            raw = snapshot["capabilities"]
        else:
            raw_context = request.request_payload.get("context_os")
            prepared_ref = snapshot.get("prepared_tool_set_ref")
            if raw_context is None or not prepared_ref:
                return None
            if not isinstance(raw_context, Mapping):
                raise ValueError("Context OS request snapshot must be an object")
            from deskpet.capabilities.refresh import context_os_snapshot_ref
            from deskpet.tools.prepared_snapshot import (
                load_context_os_snapshot,
            )

            prepared, eligibility = load_context_os_snapshot(raw_context)
            if (
                context_os_snapshot_ref(prepared, eligibility)
                != prepared_ref
            ):
                raise ValueError(
                    "Context OS snapshot differs from trusted catalog lease"
                )
            base_names = tuple(
                sorted(
                    capability.ref.name
                    for capability in (
                        *prepared.direct,
                        *prepared.activated,
                    )
                )
            )
            raw = base_names
        if not isinstance(raw, (list, tuple, set, frozenset)):
            raise ValueError("capability snapshot tools must be a sequence")
        base = {str(item) for item in raw if str(item).strip()}
        intersection = _skill_tool_intersection(
            snapshot,
            request.request_payload,
            active_scope_ids=(
                request.active_skill_scope_ids or None
            ),
            activated_scopes=request.activated_skill_scopes,
        )
        if intersection is None:
            return tuple(sorted(base))
        effective = set(intersection.effective_tool_names)
        if not effective.issubset(base):
            raise ValueError(
                "Skill ToolRef intersection widened the base ToolSet"
            )
        return tuple(sorted(effective))

    @staticmethod
    def _capability_scope_id(request: DriverStart) -> str:
        """Recover the host-prepared Context OS scope for every tool call."""

        raw = request.request_payload.get("context_os")
        if raw is None:
            return ""
        if not isinstance(raw, Mapping):
            raise ValueError("Context OS request snapshot must be an object")
        from deskpet.tools.prepared_snapshot import load_context_os_snapshot

        prepared, eligibility = load_context_os_snapshot(raw)
        prepared_ref = request.capability_snapshot.get(
            "prepared_tool_set_ref"
        )
        if prepared_ref:
            from deskpet.capabilities.refresh import context_os_snapshot_ref

            if (
                context_os_snapshot_ref(prepared, eligibility)
                != prepared_ref
            ):
                raise ValueError(
                    "Context OS snapshot differs from trusted catalog lease"
                )
        if eligibility.session_id != request.session_id:
            raise ValueError("Context OS session identity does not match the run")
        request_id = (
            request.run_context.request_id
            if request.run_context is not None
            else ""
        )
        if not request_id or eligibility.request_id != request_id:
            raise ValueError("Context OS request identity does not match the run")
        if not prepared.scope_id:
            raise ValueError("Context OS scope identity is missing")
        return prepared.scope_id

    @staticmethod
    def _capability_snapshot_ref(request: DriverStart) -> str:
        snapshot_ref = str(
            request.capability_snapshot.get("catalog_snapshot_ref") or ""
        )
        if (
            request.capability_snapshot.get("run_catalog_content_stamp")
            and not snapshot_ref
        ):
            raise ValueError("trusted capability snapshot ref is missing")
        return snapshot_ref

    def _pin_provider_capability_scope(
        self, request: DriverStart
    ) -> tuple[str, str, str, str, bool] | None:
        """Keep Context OS authority alive for one complete provider turn.

        The scope TTL is orphan cleanup, not a model-thinking deadline.  A
        slow provider can legitimately take longer than that TTL, so every
        provider turn owns a pin from dispatch through its final emission.
        When an idle/recovered Run has already lost the in-memory record, the
        exact durable Context OS snapshot is sufficient to rebuild it before
        pinning; AgentLoop still performs the normal registry validation.
        """

        store = self._capability_scope_store
        raw_context = request.request_payload.get("context_os")
        if store is None or raw_context is None:
            return None
        if not isinstance(raw_context, Mapping):
            raise ValueError("Context OS request snapshot must be an object")

        from deskpet.tools.prepared_snapshot import load_context_os_snapshot

        prepared, eligibility = load_context_os_snapshot(raw_context)
        scope_id = self._capability_scope_id(request)
        session_id = request.session_id
        request_id = (
            request.run_context.request_id
            if request.run_context is not None
            else ""
        )
        record = store.pin(
            scope_id,
            session_id=session_id,
            request_id=request_id,
        )
        rehydrated = False
        if record is None:
            try:
                store.open(prepared, eligibility)
                rehydrated = True
            except ValueError:
                # Another owner may have restored the same exact scope
                # between the failed pin and this open.
                pass
            record = store.pin(
                scope_id,
                session_id=session_id,
                request_id=request_id,
            )
        if record is None:
            raise RuntimeError(
                "Context OS capability scope could not be restored"
            )
        logger.info(
            "harness_provider_scope_pinned run_id=%s scope_id=%s "
            "rehydrated=%s",
            request.run_id,
            scope_id,
            rehydrated,
        )
        return request.run_id, scope_id, session_id, request_id, rehydrated

    def _unpin_provider_capability_scope(
        self, lease: tuple[str, str, str, str, bool] | None
    ) -> None:
        if lease is None or self._capability_scope_store is None:
            return
        run_id, scope_id, session_id, request_id, rehydrated = lease
        released = self._capability_scope_store.unpin(
            scope_id,
            session_id=session_id,
            request_id=request_id,
        )
        logger.info(
            "harness_provider_scope_unpinned run_id=%s scope_id=%s "
            "released=%s rehydrated=%s",
            run_id,
            scope_id,
            released,
            rehydrated,
        )

    @staticmethod
    def _raw_failure(
        *,
        run_id: str,
        tool_call: Any,
        call_order: int,
        source_kind: str,
        error_code: str,
        message: str,
        retriable: bool = False,
        replan: bool = False,
    ) -> Mapping[str, Any]:
        provider_call_id = str(getattr(tool_call, "id", "") or "")
        tool_name = (
            str(getattr(tool_call, "name", "") or "").strip()
            or "<missing-tool-name>"
        )
        parse_error = getattr(tool_call, "args_parse_error", None)
        raw_arguments: Any = (
            getattr(tool_call, "args_raw", None)
            if parse_error
            else getattr(tool_call, "arguments", {})
        )
        frozen = json.dumps(
            raw_arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        raw_hash = hashlib.sha256(frozen.encode("utf-8")).hexdigest()
        action_fingerprint = hashlib.sha256(
            json.dumps(
                {"tool_name": tool_name, "raw_arguments_hash": raw_hash},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        payload = {
                "call_record_id": "provider-call:"
                + hashlib.sha256(
                    f"{run_id}|{provider_call_id}".encode("utf-8")
                ).hexdigest(),
                "call_order": int(call_order),
                "provider_call_id": provider_call_id,
                "raw_tool_name": tool_name,
                "raw_arguments": thaw_json(raw_arguments),
                "raw_arguments_ref": f"inline:{raw_hash}",
                "raw_arguments_hash": raw_hash,
                "parsed_arguments_hash": None if parse_error else raw_hash,
                "admission_state": "rejected",
                "source_kind": source_kind,
                "source_identity": tool_name or "provider_tool_call",
                "error_code": error_code,
                "message": message,
                "failed_step": tool_name or "provider_tool_call",
                "action_fingerprint": action_fingerprint,
            }
        if retriable:
            payload["retriable"] = True
        if replan:
            payload["replan"] = True
        return MappingProxyType(payload)

    def _is_control_tool(self, tool_name: str) -> bool:
        dispatch = getattr(self._call_factory, "dispatch_kind", None)
        handles = getattr(self._delegation_factory, "handles", None)
        product_control = bool(callable(handles) and handles(tool_name))
        if callable(dispatch):
            return dispatch(tool_name) == "delegate_control" or product_control
        return product_control

    def _coalesce_replayed_exclusive_control(self, event: Any) -> Any:
        """Collapse only an exact replay of one exclusive control action.

        GLM-5.2 can emit the same long ``workflow_spawn`` call under many
        distinct call IDs until its output limit, followed by one truncated
        copy.  Orchestration controls are exclusive by contract, so that
        provider artefact is safely one action.  Ordinary tools are never
        coalesced because repeated identical calls can be intentional.
        """

        tool_calls = tuple(event.tool_calls)
        if len(tool_calls) <= 1:
            return event

        names = {
            str(getattr(item, "name", "") or "").strip()
            for item in tool_calls
        }
        if len(names) != 1 or not next(iter(names), ""):
            return event
        tool_name = next(iter(names))
        try:
            if not self._is_control_tool(tool_name):
                return event
        except KeyError:
            return event

        valid = tuple(
            item
            for item in tool_calls
            if not getattr(item, "args_parse_error", None)
        )
        malformed = tuple(
            item
            for item in tool_calls
            if getattr(item, "args_parse_error", None)
        )
        if not valid:
            return event
        if any(not str(getattr(item, "id", "") or "") for item in valid):
            return event

        signatures = {
            json.dumps(
                getattr(item, "arguments", {}),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            for item in valid
        }
        if len(signatures) != 1:
            return event

        valid_renderings = tuple(
            rendering
            for item in valid
            for rendering in (
                json.dumps(
                    getattr(item, "arguments", {}),
                    ensure_ascii=False,
                    default=str,
                ),
                json.dumps(
                    getattr(item, "arguments", {}),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ),
            )
        )
        for item in malformed:
            raw = str(getattr(item, "args_raw", "") or "")
            if (
                len(raw) < _MIN_TRUNCATED_CONTROL_REPLAY_PREFIX_CHARS
                or not any(rendering.startswith(raw) for rendering in valid_renderings)
            ):
                return event

        selected = valid[0]
        selected_id = str(selected.id)
        canonical_messages = [
            copy.deepcopy(dict(item))
            for item in event.canonical_messages
        ]
        canonical_bound = False
        for message in reversed(canonical_messages):
            if (
                str(message.get("role") or "") != "assistant"
                or not isinstance(message.get("tool_calls"), list)
            ):
                continue
            selected_calls = [
                call
                for call in message["tool_calls"]
                if str(call.get("id") or "") == selected_id
            ]
            if len(selected_calls) == 1:
                message["tool_calls"] = selected_calls
                canonical_bound = True
            break
        if not canonical_bound:
            return event

        logger.warning(
            "provider_replayed_exclusive_control_coalesced "
            "tool_name=%s original_call_count=%d malformed_tail_count=%d "
            "provider_call_id=%s",
            tool_name,
            len(tool_calls),
            len(malformed),
            selected_id,
        )
        return replace(
            event,
            tool_calls=(selected,),
            canonical_messages=tuple(canonical_messages),
        )

    async def _map(self, request: DriverStart, iterator: AsyncIterator[Any]) -> AsyncIterator[ReactEmission]:
        from agent.agent_loop import AssistantDeltaEvent, AsyncHandoffEvent, ContextCompactedEvent, ErrorEvent, FinalEvent, ProviderChainFallbackEvent, ToolCallEvent, ToolBatchEvent, ToolResultEvent
        from deskpet.harness.ports import ContextCompactedCandidate
        run_id = request.run_id
        allowed_tools = self._allowed_tool_names(request)
        skill_intersection = _skill_tool_intersection(
            request.capability_snapshot,
            request.request_payload,
            active_scope_ids=(
                request.active_skill_scope_ids or None
            ),
            activated_scopes=request.activated_skill_scopes,
        )
        live = self._bound_live()
        active = live.get(run_id)
        if active is None or active.driver_iterator is not None:
            raise RuntimeError('legacy collaborator run is absent or already active')
        active.driver_iterator = iterator
        try:
            async for event in iterator:
                if isinstance(event, AssistantDeltaEvent):
                    yield ReactToken(
                        event.content,
                        event.kind,
                        event.invocation_id,
                        event.stream_epoch,
                        event.provisional,
                        event.retract_provisional,
                    )
                elif isinstance(event, ProviderChainFallbackEvent):
                    yield ReactFallback(event.from_, event.to, event.reason)
                elif isinstance(event, ContextCompactedEvent):
                    yield ContextCompactedCandidate(
                        request.run_id,
                        reduction=event.reduction,
                        tokens_in=event.tokens_in,
                        tokens_out=event.tokens_out,
                        model=event.model,
                        source_event_id=event.source_event_id,
                        based_on_sample_id=event.based_on_sample_id,
                        iteration=event.iteration,
                        occurred_at=event.occurred_at,
                    )
                elif isinstance(event, FinalEvent):
                    await self._close_iterator(active)
                    yield ReactFinal(event.content)
                elif isinstance(event, ErrorEvent):
                    await self._close_iterator(active)
                    yield ReactFailure(
                        event.detail or event.reason,
                        error_code=event.reason or "agent_loop_error",
                        source_layer="agent_loop",
                    )
                elif isinstance(event, ToolBatchEvent):
                    if self._call_factory is None or request.run_context is None:
                        raise AgentLoopToolInterceptionError('external tool batch requires a prepared-call factory and run context')
                    event = self._coalesce_replayed_exclusive_control(event)
                    if len(event.tool_calls) > _MAX_PROVIDER_TOOL_CALLS_PER_BATCH:
                        # Some OpenAI-compatible relays append a long tail of
                        # speculative discovery calls after the model has
                        # already emitted one valid durable handoff.  Keep the
                        # exclusive orchestration intent and drop that tail;
                        # otherwise the recovery prompt asks for exactly the
                        # same workflow_spawn that we just discarded.
                        focused = tuple(
                            item
                            for item in event.tool_calls
                            if (
                                str(getattr(item, "name", "") or "")
                                == "workflow_spawn"
                                and not getattr(item, "args_parse_error", None)
                                and str(getattr(item, "id", "") or "")
                            )
                        )
                        if len(focused) == 1:
                            focused_id = str(focused[0].id)
                            canonical_messages = [
                                copy.deepcopy(dict(item))
                                for item in event.canonical_messages
                            ]
                            canonical_bound = False
                            for message in reversed(canonical_messages):
                                if (
                                    str(message.get("role") or "") != "assistant"
                                    or not isinstance(message.get("tool_calls"), list)
                                ):
                                    continue
                                selected = [
                                    call
                                    for call in message["tool_calls"]
                                    if str(call.get("id") or "") == focused_id
                                ]
                                if len(selected) == 1:
                                    message["tool_calls"] = selected
                                    canonical_bound = True
                                break
                            if canonical_bound:
                                logger.warning(
                                    "provider_oversized_batch_focused_workflow_spawn "
                                    "original_call_count=%d provider_call_id=%s",
                                    len(event.tool_calls),
                                    focused_id,
                                )
                                event = replace(
                                    event,
                                    tool_calls=focused,
                                    canonical_messages=tuple(canonical_messages),
                                )
                    if len(event.tool_calls) > _MAX_PROVIDER_TOOL_CALLS_PER_BATCH:
                        representative = next(
                            (
                                item
                                for item in event.tool_calls
                                if str(getattr(item, "id", "") or "")
                            ),
                            SimpleNamespace(
                                id=(
                                    "provider-oversized-batch:"
                                    f"{request.run_id}:{event.iteration}"
                                ),
                                name="provider_tool_batch",
                                arguments={
                                    "call_count": len(event.tool_calls)
                                },
                                args_parse_error=None,
                                args_raw=None,
                            ),
                        )
                        provider_call_id = str(
                            getattr(representative, "id", "") or ""
                        )
                        command_id = hashlib.sha256(
                            (
                                f"oversized-tool-batch|{run_id}|"
                                f"{event.iteration}|{provider_call_id}|"
                                f"{len(event.tool_calls)}"
                            ).encode("utf-8")
                        ).hexdigest()
                        failure = self._raw_failure(
                            run_id=run_id,
                            tool_call=representative,
                            call_order=0,
                            source_kind="tool_parse",
                            error_code="provider_tool_batch_too_large",
                            message=(
                                "provider emitted "
                                f"{len(event.tool_calls)} tool calls; maximum "
                                f"is {_MAX_PROVIDER_TOOL_CALLS_PER_BATCH}. "
                                "Do not enumerate or search every tool. "
                                "Replan with one focused action; for a "
                                "multi-file software or game project, call "
                                "workflow_spawn with profile_key "
                                "workflow.durable_task."
                            ),
                            replan=True,
                        )
                        # The Driver immediately persists this synthetic
                        # failure and starts a fresh provider turn.  Release
                        # the intercepted iterator before yielding, matching
                        # the normal ToolBatch path below; otherwise the
                        # recursive replan observes the old iterator as still
                        # active and terminalizes the Run with
                        # ``legacy collaborator run is absent or already
                        # active``.
                        await self._close_iterator(active)
                        yield ReactToolBatch(
                            command_id=command_id,
                            calls=(),
                            contexts=(),
                            canonical_messages=tuple(
                                event.canonical_messages
                            ),
                            iteration=int(event.iteration),
                            feedback_state=dict(event.feedback_state),
                            raw_failures=(failure,),
                            provider_call_order=(provider_call_id,),
                        )
                        return
                    provider_ids = tuple(
                        str(getattr(tool_call, "id", "") or "")
                        for tool_call in event.tool_calls
                    )
                    if (
                        not provider_ids
                        or any(not item for item in provider_ids)
                        or len(set(provider_ids)) != len(provider_ids)
                    ):
                        await self._close_iterator(active)
                        yield ReactFailure(
                            "provider_protocol_error: tool call ids must be "
                            "non-empty and unique"
                        )
                        return
                    command_id = hashlib.sha256(
                        (
                            f'tool-batch|{run_id}|{event.iteration}|'
                            + '|'.join(provider_ids)
                        ).encode('utf-8')
                    ).hexdigest()
                    from deskpet.harness.context import HostContextFactory
                    context_factory = HostContextFactory()
                    capability_scope_id = self._capability_scope_id(request)
                    calls: list[PreparedToolCall] = []
                    contexts: list[ToolExecutionContext] = []
                    raw_failures: list[Mapping[str, Any]] = []
                    candidates: list[tuple[int, Any, bool]] = []
                    for call_order, tool_call in enumerate(event.tool_calls):
                        if not str(getattr(tool_call, "name", "") or "").strip():
                            raw_failures.append(
                                self._raw_failure(
                                    run_id=run_id,
                                    tool_call=tool_call,
                                    call_order=call_order,
                                    source_kind="tool_parse",
                                    error_code="tool_call_name_missing",
                                    message=(
                                        "provider emitted a tool call without "
                                        "a non-empty function name"
                                    ),
                                    replan=True,
                                )
                            )
                            continue
                        if tool_call.args_parse_error:
                            raw_failures.append(
                                self._raw_failure(
                                    run_id=run_id,
                                    tool_call=tool_call,
                                    call_order=call_order,
                                    source_kind="tool_parse",
                                    error_code="tool_call_args_malformed_json",
                                    message=(
                                        f"malformed tool arguments for "
                                        f"{tool_call.name}: "
                                        f"{tool_call.args_parse_error}"
                                    ),
                                )
                            )
                            continue
                        if (
                            allowed_tools is not None
                            and tool_call.name not in allowed_tools
                        ):
                            raw_failures.append(
                                self._raw_failure(
                                    run_id=run_id,
                                    tool_call=tool_call,
                                    call_order=call_order,
                                    source_kind="tool_unknown",
                                    error_code="tool_not_exposed",
                                    message=(
                                        "tool is outside the prepared capability "
                                        f"snapshot: {tool_call.name}. Do not "
                                        "stop or report that the task is "
                                        "impossible. Use capability_search, "
                                        "then tool_describe with the returned "
                                        "full capability_id, then tool_activate "
                                        "before retrying the action."
                                    ),
                                    retriable=True,
                                    replan=True,
                                )
                            )
                            continue
                        try:
                            is_control = self._is_control_tool(tool_call.name)
                        except KeyError:
                            raw_failures.append(
                                self._raw_failure(
                                    run_id=run_id,
                                    tool_call=tool_call,
                                    call_order=call_order,
                                    source_kind="tool_unknown",
                                    error_code="unknown_tool",
                                    message=f"unknown tool: {tool_call.name}",
                                )
                            )
                            continue
                        candidates.append((call_order, tool_call, is_control))

                    control_candidates = tuple(
                        item for item in candidates if item[2]
                    )
                    if control_candidates and (
                        len(event.tool_calls) != 1
                        or len(control_candidates) != 1
                        or raw_failures
                    ):
                        for call_order, tool_call, _is_control in candidates:
                            raw_failures.append(
                                self._raw_failure(
                                    run_id=run_id,
                                    tool_call=tool_call,
                                    call_order=call_order,
                                    source_kind="strategy_rejected",
                                    error_code="control_batch_not_exclusive",
                                    message=(
                                        "orchestration control tools require an "
                                        "exclusive provider action batch"
                                    ),
                                )
                            )
                        candidates = []
                        control_candidates = ()

                    mutating_candidates = tuple(
                        item
                        for item in candidates
                        if str(getattr(item[1], "name", ""))
                        in _CATALOG_MUTATING_TOOLS
                    )
                    if mutating_candidates and (
                        len(event.tool_calls) != 1
                        or len(mutating_candidates) != 1
                        or raw_failures
                    ):
                        for call_order, tool_call, _is_control in candidates:
                            raw_failures.append(
                                self._raw_failure(
                                    run_id=run_id,
                                    tool_call=tool_call,
                                    call_order=call_order,
                                    source_kind="strategy_rejected",
                                    error_code="capability_mutation_not_exclusive",
                                    message=(
                                        "capability lifecycle tools require an "
                                        "exclusive provider action batch"
                                    ),
                                )
                            )
                        candidates = []

                    if candidates and not control_candidates:
                        from agent.harness_feedback import preflight_external_tool_batch
                        runtime = active.driver_runtime[0] if isinstance(active.driver_runtime, tuple) else active.driver_runtime
                        preflight_error = preflight_external_tool_batch(
                            runtime,
                            tuple(item[1] for item in candidates),
                        )
                        if preflight_error:
                            for call_order, tool_call, _is_control in candidates:
                                raw_failures.append(
                                    self._raw_failure(
                                        run_id=run_id,
                                        tool_call=tool_call,
                                        call_order=call_order,
                                        source_kind="tool_preflight",
                                        error_code=str(
                                            preflight_error.split(":", 1)[0]
                                            or "tool_preflight_failed"
                                        ),
                                        message=preflight_error,
                                    )
                                )
                            candidates = []

                    prepared_control = False
                    for call_order, tool_call, is_control in candidates:
                        effect_id = hashlib.sha256(f'effect|{run_id}|{tool_call.id}'.encode('utf-8')).hexdigest()
                        context = context_factory.create_tool_context(
                            request.run_context,
                            run_id=run_id,
                            command_id=command_id,
                            call_id=tool_call.id,
                            effect_id=effect_id,
                            scope_id=capability_scope_id,
                            capability_snapshot_ref=(
                                self._capability_snapshot_ref(request)
                            ),
                            active_skill_scope_ids=(
                                ()
                                if skill_intersection is None
                                else skill_intersection.active_scope_ids
                            ),
                            effective_skill_tool_ref_hashes=(
                                ()
                                if skill_intersection is None
                                else skill_intersection.effective_tool_ref_hashes
                            ),
                            effective_skill_tool_refs_hash=(
                                ""
                                if skill_intersection is None
                                else skill_intersection.effective_tool_refs_hash
                            ),
                        )
                        try:
                            prepared = self._call_factory.prepare_call(
                                tool_call.name, tool_call.arguments,
                                request.session_id, tool_call.id,
                                execution_context=context,
                            )
                        except KeyError as exc:
                            raw_failures.append(
                                self._raw_failure(
                                    run_id=run_id,
                                    tool_call=tool_call,
                                    call_order=call_order,
                                    source_kind="tool_unknown",
                                    error_code="unknown_tool",
                                    message=str(exc),
                                )
                            )
                            continue
                        except Exception as exc:  # noqa: BLE001 - normalized below
                            scope_error = (
                                type(exc).__name__ == "AuthorizationScopeMissing"
                            )
                            raw_failures.append(
                                self._raw_failure(
                                    run_id=run_id,
                                    tool_call=tool_call,
                                    call_order=call_order,
                                    source_kind=(
                                        "tool_authorization"
                                        if scope_error
                                        else "tool_prepare"
                                    ),
                                    error_code=(
                                        "authorization_scope_missing"
                                        if scope_error
                                        else "tool_prepare_failed"
                                    ),
                                    message=f"{type(exc).__name__}: {exc}",
                                    retriable=scope_error,
                                    replan=scope_error,
                                )
                            )
                            continue
                        policy_for = getattr(
                            self._call_factory,
                            "prepared_execution_policy",
                            None,
                        )
                        requires_authorization = (
                            bool(policy_for(prepared)[0])
                            if callable(policy_for)
                            else False
                        )
                        if (
                            requires_authorization
                            and not prepared.resource_selectors
                        ):
                            raw_failures.append(
                                self._raw_failure(
                                    run_id=run_id,
                                    tool_call=tool_call,
                                    call_order=call_order,
                                    source_kind="tool_authorization",
                                    error_code="authorization_scope_missing",
                                    message=(
                                        "prepared tool has no deterministic "
                                        "resource selectors"
                                    ),
                                    retriable=True,
                                    replan=True,
                                )
                            )
                            continue
                        if prepared.catalog_snapshot_ref not in {
                            "",
                            context.capability_snapshot_ref,
                        }:
                            raise ValueError(
                                "prepared call capability snapshot ref drifted"
                            )
                        prepared = replace(
                            prepared,
                            catalog_snapshot_ref=(
                                context.capability_snapshot_ref
                            ),
                        )
                        calls.append(prepared)
                        contexts.append(context)
                        prepared_control = is_control
                    await self._close_iterator(active)
                    if prepared_control and len(calls) == 1 and not raw_failures:
                        yield ReactControlBatch(
                            command_id,
                            calls[0],
                            contexts[0],
                            tuple(event.canonical_messages),
                            int(event.iteration),
                            dict(event.feedback_state),
                        )
                        return
                    yield ReactToolBatch(
                        command_id=command_id,
                        calls=tuple(calls),
                        contexts=tuple(contexts),
                        canonical_messages=tuple(event.canonical_messages),
                        iteration=int(event.iteration),
                        feedback_state=dict(event.feedback_state),
                        raw_failures=tuple(raw_failures),
                        provider_call_order=provider_ids,
                    )
                    return
                elif isinstance(event, (ToolCallEvent, ToolResultEvent, AsyncHandoffEvent)):
                    raise AgentLoopToolInterceptionError('AgentLoop tool dispatch must cross the Driver boundary')
        finally:
            if active.driver_iterator is iterator:
                await self._close_iterator(active)

    async def start(self, request: DriverStart) -> AsyncIterator[ReactEmission]:
        runtime = await self._loop_for(request)
        if isinstance(runtime, tuple):
            loop, options = runtime
            kwargs = dict(options)
        else:
            loop = runtime
            kwargs = {}
        kwargs.update({
            'task_id': request.run_id,
            'session_id': request.session_id,
            'stream': True,
        })
        allowed_tools = self._allowed_tool_names(request)
        if allowed_tools is not None:
            kwargs['tool_names_filter'] = list(allowed_tools)
        kwargs['_scoped_evidence'] = request.scoped_evidence
        feedback = dict(request.completion_state.get('agent_loop_feedback', {}))
        feedback['iteration'] = max(int(feedback.get('iteration', 0)), request.iteration)
        kwargs['_external_tool_feedback'] = feedback
        if request.launch_operation_id is not None:
            kwargs['launch_operation_id'] = request.launch_operation_id
            kwargs['provider_launch_snapshot'] = request.provider_launch_snapshot
        scope_lease = self._pin_provider_capability_scope(request)
        try:
            iterator = loop.run(
                [dict(thaw_json(item)) for item in request.canonical_messages],
                **kwargs,
            )
            async for emission in self._map(request, iterator):
                yield emission
        finally:
            self._unpin_provider_capability_scope(scope_lease)

    async def resume(self, boundary: ReactCommandBoundary, response: Mapping[str, Any]) -> AsyncIterator[ReactEmission]:
        async for emission in self.start(boundary.to_start(response.get('scoped_evidence', UNKNOWN_EVIDENCE))):
            yield emission

    async def prepare_tool_feedback(self, boundary: ReactCommandBoundary, state: dict[str, Any], messages: tuple[Mapping[str, Any], ...]) -> tuple[dict[str, Any], tuple[Mapping[str, Any], ...]]:
        """Persist legacy loop observations without returning execution ownership."""
        from agent.harness_feedback import prepare_external_tool_feedback
        runtime = await self._loop_for(boundary.to_start())
        loop = runtime[0] if isinstance(runtime, tuple) else runtime
        return prepare_external_tool_feedback(loop, boundary, state, messages)

    async def cancel(self, run_id: str, reason: str) -> None:
        await self._close_iterator(self._bound_live().get(run_id))

    async def prepare_recovery(self, boundary: ReactCommandBoundary) -> None:
        await self._loop_for(boundary.to_start())

    async def close(self) -> None:
        if self._live is None:
            return
        for active in self._live.values():
            if active.driver_iterator is not None:
                await self._close_iterator(active)


__all__ = [
    "AgentLoopCollaborator",
    "AgentLoopToolInterceptionError",
    "ReactCommandBoundary",
    "ReactControlBatch",
    "ReactEmission",
    "ReactFailure",
    "ReactFallback",
    "ReactFinal",
    "ReactToken",
    "ReactToolBatch",
]

"""Transport-neutral adapter for Text and Voice venue clients.

This module is deliberately not registered by the production bootstrap before
the atomic harness activation. It translates transport payloads into the six
RunKernel operations while trusted identity remains owned by the resolver.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from deskpet.execution.contracts import AdmissionSpec, ActorContext, DecisionSignal as DurableDecisionSignal, ProviderLaunchSnapshot, RunEvent, RunNotFound, RunRef, fingerprint_json
from deskpet.execution.run_block_signals import (
    PreflightBlocked,
    RootBlockReasonV1,
)
from deskpet.agent.product_domain_sink import ProductDomainSink
from deskpet.agent.run_presenter import (
    CanonicalRunEventPresentationAdapter,
    PresentationState,
    RunPresentationContext,
    RunPresenter,
)
from deskpet.agent.turn_preparer import (
    ProductTurnPreparer,
    TurnInput,
)
from deskpet.harness.contracts import HostExtensionRefV1, PreparedRunContextV1
from deskpet.harness.kernel import HostContext, RunKernel, RunRequest, root_run_identity
from deskpet.harness.ports import DecisionSignal, UserContinuationSignal
from deskpet.harness.adapters.product_turn_open import (
    ProductTurnIdentityResolver,
    ProductTurnPreparationService,
)


def _enrich_companion_selection_exact_tools(
    selection: Mapping[str, Any],
    exact_tools: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Narrow Skill names to Tool facts captured under the publish lock."""

    exact_by_name: dict[str, dict[str, Any]] = {}
    for raw in exact_tools:
        item = copy.deepcopy(dict(raw))
        name = str(item.get("name") or "")
        if not name or name in exact_by_name:
            raise ValueError("captured exact Tool identities are malformed")
        exact_by_name[name] = item
    enriched = copy.deepcopy(dict(selection))
    raw_scopes = enriched.get("skill_invocation_scopes", ())
    if isinstance(raw_scopes, (str, bytes)) or not isinstance(
        raw_scopes, (list, tuple)
    ):
        raise ValueError("companion Skill scopes must be a sequence")
    scopes: list[dict[str, Any]] = []
    for raw_scope in raw_scopes:
        if not isinstance(raw_scope, Mapping):
            raise ValueError("companion Skill scope must be an object")
        scope = copy.deepcopy(dict(raw_scope))
        allowed = scope.get("allowed_tools", ())
        if isinstance(allowed, (str, bytes)) or not isinstance(
            allowed, (list, tuple)
        ):
            raise ValueError("companion Skill allowed_tools must be a sequence")
        allowed_names = sorted({str(item) for item in allowed})
        frozen_allowed_names = [
            name for name in allowed_names if name in exact_by_name
        ]
        scope["allowed_tools"] = frozen_allowed_names
        scope["allowed_tool_refs"] = [
            copy.deepcopy(exact_by_name[name])
            for name in frozen_allowed_names
        ]
        scopes.append(scope)
    enriched["skill_invocation_scopes"] = scopes
    return enriched


def _companion_catalog_lease_entries(
    lease_entries: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Preserve the typed RunCatalogEntryIdentity envelope for finalization."""

    normalized: list[dict[str, Any]] = []
    for raw in lease_entries:
        item = copy.deepcopy(dict(raw))
        envelope = item.get("canonical_envelope")
        if isinstance(envelope, Mapping):
            normalized.append(item)
            continue
        entry_kind = str(item.pop("entry_kind", "") or "")
        descriptor = str(
            item.pop("descriptor_fingerprint", "") or ""
        )
        if not entry_kind or not descriptor:
            raise RuntimeError("run catalog lease entry identity is malformed")
        normalized.append(
            {
                "entry_kind": entry_kind,
                "descriptor_fingerprint": descriptor,
                "canonical_envelope": item,
            }
        )
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class VenueRunHandle:
    run_id: str
    events: AsyncIterator[RunEvent]
    _client: "KernelRunClient"
    _host: HostContext
    _session_id: str

    async def signal(self, signal: Mapping[str, object]) -> object:
        return await self._client.signal(self._ref(), self._host, signal)

    async def cancel(self, reason: str) -> object:
        return await self._client.cancel(self._ref(), self._host, reason)

    async def close(self) -> None:
        await self._client.close(self._ref(), self._host)

    def _ref(self) -> Mapping[str, object]:
        return {"run_id": self.run_id, "expected_session_id": self._session_id}


@dataclass(frozen=True, slots=True)
class ProductVenueRunResult:
    run_id: str | None
    status: str
    final_text: str = ""


class KernelRunClient:
    def __init__(self, kernel: RunKernel) -> None:
        self._kernel = kernel

    @staticmethod
    def _parse_request(request: Mapping[str, object]) -> RunRequest:
        text = str(request.get("text") or "").strip()
        if not text:
            raise ValueError("run request text is required")
        request_id = str(request.get("request_id") or uuid.uuid4().hex)
        turn_id = str(request.get("turn_id") or uuid.uuid4().hex)
        raw_payload = request.get("payload", {})
        if not isinstance(raw_payload, Mapping):
            raise ValueError("run request payload must be a mapping")
        raw_tools = request.get("proposed_tools", ())
        if isinstance(raw_tools, str) or not isinstance(raw_tools, (list, tuple)):
            raise ValueError("proposed_tools must be a sequence of names")
        raw_messages = request.get("canonical_messages", ())
        if isinstance(raw_messages, (str, bytes)) or not isinstance(
            raw_messages, (list, tuple)
        ) or any(not isinstance(message, Mapping) for message in raw_messages):
            raise ValueError("canonical_messages must be a sequence of mappings")
        admission = request.get("admission")
        provider_snapshot = request.get("provider_launch_snapshot")
        if admission is not None and not isinstance(admission, AdmissionSpec):
            raise ValueError("admission must be a typed AdmissionSpec")
        if provider_snapshot is not None and not isinstance(
            provider_snapshot, ProviderLaunchSnapshot
        ):
            raise ValueError("provider launch snapshot must be typed")
        if "prepared" in request or "prepared_run_context" in request:
            raise ValueError("run request payload cannot construct prepared host context")
        return RunRequest(
            text=text,
            request_id=request_id,
            turn_id=turn_id,
            venue=str(request.get("venue") or "text"),
            mode=str(request.get("mode") or "general"),
            workspace_context=bool(request.get("workspace_context", False)),
            proposed_tools=tuple(str(name) for name in raw_tools),
            canonical_messages=tuple(dict(message) for message in raw_messages),
            payload=dict(raw_payload),
            admission=admission,
            provider_launch_snapshot=provider_snapshot,
        )

    async def start(
        self,
        request: Mapping[str, object],
        host: HostContext,
        *,
        prepared: PreparedRunContextV1 | None = None,
    ) -> VenueRunHandle:
        if prepared is not None and not isinstance(prepared, PreparedRunContextV1):
            raise TypeError("prepared must be a host-issued PreparedRunContextV1")
        parsed = self._parse_request(request)
        handle = await self._kernel.start(parsed, host, prepared=prepared)
        actor = host.actor(root_run_id=handle.root_run_id)
        return VenueRunHandle(
            run_id=handle.ref.run_id,
            events=self._kernel.observe(handle.ref, actor),
            _client=self,
            _host=host,
            _session_id=host.session_id,
        )

    async def start_blocked(
        self,
        request: Mapping[str, object],
        host: HostContext,
        *,
        reason_code: str,
        evidence_refs: Sequence[str],
        prepared: PreparedRunContextV1 | None = None,
    ) -> VenueRunHandle:
        if prepared is not None and not isinstance(prepared, PreparedRunContextV1):
            raise TypeError("prepared must be a host-issued PreparedRunContextV1")
        parsed = self._parse_request(request)
        handle = await self._kernel._start_blocked(
            parsed,
            host,
            reason_code=reason_code,
            evidence_refs=evidence_refs,
            prepared=prepared,
        )
        actor = host.actor(root_run_id=handle.root_run_id)
        return VenueRunHandle(
            run_id=handle.ref.run_id,
            events=self._kernel.observe(handle.ref, actor),
            _client=self,
            _host=host,
            _session_id=host.session_id,
        )

    async def resume(self, request_id: str, turn_id: str, host: HostContext) -> VenueRunHandle | None:
        _, ref = root_run_identity(host.session_id, request_id, turn_id)
        actor = host.actor(root_run_id=ref.run_id)
        try:
            handle = await self._kernel.recover(ref, actor)
        except RunNotFound:
            return None
        return VenueRunHandle(
            handle.ref.run_id,
            self._kernel.observe(handle.ref, actor),
            self,
            host,
            host.session_id,
        )

    async def signal(
        self,
        ref: Mapping[str, object],
        actor: HostContext,
        signal: Mapping[str, object],
    ) -> object:
        run_ref = self._ref(ref)
        trusted_actor = await self._actor_for_ref(run_ref, actor)
        if str(signal.get("kind") or "") == "user_continuation":
            return await self._kernel.signal(
                run_ref,
                trusted_actor,
                UserContinuationSignal(
                    run_ref.run_id,
                    task_scope_id=str(signal.get("task_scope_id") or ""),
                    message_ref=str(signal.get("message_ref") or ""),
                    content=str(signal.get("content") or ""),
                    expected_boundary_version=int(
                        signal.get("expected_boundary_version") or 0
                    ),
                ),
            )
        decision_id = str(signal.get("decision_id") or "").strip()
        if not decision_id:
            raise ValueError("decision_id is required")
        response = signal.get("response")
        if not isinstance(response, Mapping):
            response = {"value": response}
        return await self._kernel.signal(
            run_ref,
            trusted_actor,
            DecisionSignal(
                run_ref.run_id,
                decision_id,
                response,
                nonce=(
                    None
                    if signal.get("nonce") is None
                    else str(signal["nonce"])
                ),
                version=(
                    None
                    if signal.get("version") is None
                    else int(signal["version"])
                ),
            ),
        )

    async def signal_decision(
        self,
        ref: RunRef,
        actor: ActorContext,
        signal: DurableDecisionSignal,
    ) -> object:
        """Submit a host-recovered decision without accepting product JSON fences."""

        if signal.run_id != ref.run_id:
            raise ValueError("decision run binding mismatch")
        if signal.expected_session_id != ref.expected_session_id:
            raise ValueError("decision session binding mismatch")
        if actor.session_id != ref.expected_session_id:
            raise ValueError("decision actor session mismatch")
        return await self._kernel.signal(
            ref,
            actor,
            DecisionSignal(
                signal.run_id,
                signal.decision_id,
                dict(signal.response),
                nonce=signal.nonce,
                version=signal.expected_version,
            ),
        )

    async def cancel(
        self,
        ref: Mapping[str, object],
        actor: HostContext,
        reason: str,
    ) -> object:
        run_ref = self._ref(ref)
        trusted_actor = await self._actor_for_ref(run_ref, actor)
        return await self._kernel.cancel(run_ref, trusted_actor, reason)

    async def close(
        self,
        ref: Mapping[str, object],
        actor: HostContext,
    ) -> None:
        run_ref = self._ref(ref)
        trusted_actor = await self._actor_for_ref(run_ref, actor)
        await self._kernel.close(run_ref, trusted_actor)

    async def _actor_for_ref(
        self,
        ref: RunRef,
        host: HostContext,
    ) -> ActorContext:
        """Resolve and bind the authoritative root through a controlled Kernel seam.

        The first rootless lookup still enforces principal, session, and auth epoch.
        Only the persisted root is then bound before any mutation. This deliberate
        private call keeps RunKernel's public surface limited to its six lifecycle
        operations; never substitute ``ref.run_id`` because a child ID is not a root.
        """

        record = await self._kernel._query(ref, host.actor())
        return host.actor(root_run_id=record.context.root_run_id)

    @staticmethod
    def _ref(value: Mapping[str, object]) -> RunRef:
        run_id = str(value.get("run_id") or "").strip()
        session_id = str(value.get("expected_session_id") or "").strip()
        if not run_id or not session_id:
            raise ValueError("run_id and expected_session_id are required")
        return RunRef(run_id, session_id)


class ProductVenueRunSession:
    def __init__(
        self,
        *,
        handle: VenueRunHandle,
        presenter: RunPresenter,
        event_adapter: CanonicalRunEventPresentationAdapter,
        presentation_context: RunPresentationContext,
    ) -> None:
        self.run_id = handle.run_id
        self._handle = handle
        self._source = handle.events
        self._presenter = presenter
        self._event_adapter = event_adapter
        self._context = presentation_context
        self._state = PresentationState()
        self._status = "unknown"
        self._consumed = False
        self._closed = False

    @property
    def events(self) -> AsyncIterator[RunEvent]:
        if self._consumed:
            raise RuntimeError("product venue run events are single-consumer")
        self._consumed = True
        return self._presenting_events()

    @property
    def result(self) -> ProductVenueRunResult:
        return ProductVenueRunResult(self.run_id, self._status, self._state.final_text)

    async def _presenting_events(self) -> AsyncIterator[RunEvent]:
        async for event in self._source:
            self._status = event.status.value
            await self._presenter.present_run_event(
                event, self._event_adapter, self._context, self._state
            )
            yield event

    async def signal(self, signal: Mapping[str, object]) -> object:
        return await self._handle.signal(signal)

    async def cancel(self, reason: str) -> object:
        return await self._handle.cancel(reason)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._presenter.finish_turn(self._context, self._state)
        finally:
            await self._handle.close()


class ProductVenueRunAdapter:
    """Dormant venue-neutral product chain kept outside the production owner."""

    def __init__(
        self,
        *,
        preparer: ProductTurnPreparer,
        run_client: KernelRunClient,
        presenter: RunPresenter,
        event_adapter: CanonicalRunEventPresentationAdapter | None = None,
        identity_resolver: ProductTurnIdentityResolver | None = None,
        preparation_service: ProductTurnPreparationService | None = None,
    ) -> None:
        self._preparer = preparer
        self._run_client = run_client
        self._presenter = presenter
        self._event_adapter = event_adapter or CanonicalRunEventPresentationAdapter()
        self._identity_resolver = (
            identity_resolver or ProductTurnIdentityResolver()
        )
        # Tests may replace ``_preparer`` after construction. Production
        # composition injects one stable preparation service explicitly.
        self._preparation_service = preparation_service

    async def open(
        self,
        turn: TurnInput,
        host: HostContext,
        *,
        services: Mapping[str, Any],
        config: Any,
        local_llm: Any,
        tool_registry: Any,
        provider: Any,
        current_message_id: int | None,
        summary_user_is_confused: Any,
        summary_latest_task_snapshot: Any,
        summary_build_reinject_msg: Any,
        presentation_context: RunPresentationContext,
        domain_sink: ProductDomainSink,
        companion_ingress_owner: Any = None,
        provider_launch_snapshot: ProviderLaunchSnapshot | None = None,
        proposed_tools: Sequence[str] = (),
    ) -> ProductVenueRunSession | ProductVenueRunResult:
        resolved = self._identity_resolver.resolve(
            turn,
            host,
            current_message_id=current_message_id,
        )
        root_ref = resolved.root_ref
        task_scope_id = resolved.task_scope_id
        work_context = resolved.work_context
        conversation = resolved.conversation
        projection = resolved.projection
        presentation_context.run_id = root_ref.run_id
        presentation_context.task_scope_id = task_scope_id
        presentation_context.conversation_boundary_ref = (
            conversation.boundary_ref
        )
        # Note: SDK Runtime v0.1.x doesn't support resume; always start new runs
        effective_host = resolved.host
        turn = resolved.turn
        preparation_service = (
            self._preparation_service
            or ProductTurnPreparationService(self._preparer)
        )
        preparation = await preparation_service.prepare(
            turn,
            services=services,
            config=config,
            local_llm=local_llm,
            tool_registry=tool_registry,
            current_message_id=current_message_id,
            companion_ingress_owner=companion_ingress_owner,
            summary_user_is_confused=summary_user_is_confused,
            summary_latest_task_snapshot=summary_latest_task_snapshot,
            summary_build_reinject_msg=summary_build_reinject_msg,
            provider=provider,
        )
        prepared = preparation.prepared
        presentation_context.messages = prepared.messages
        presentation_context.assembler = prepared.assembler
        presentation_context.bundle = prepared.bundle
        routed = preparation.routed
        admission = None

        request_payload = preparation.request_payload
        if turn.active_execution_budget_seconds is not None:
            request_payload["active_execution_budget_seconds"] = float(
                turn.active_execution_budget_seconds
            )
        request_payload.update(
            {
                "root_run_id": root_ref.run_id,
                "task_scope_id": task_scope_id,
                "task_work_context": {
                    "session_id": work_context.session_id,
                    "root_run_id": work_context.root_run_id,
                    "task_scope_id": work_context.task_scope_id,
                    "workspace_root": work_context.workspace_root,
                    "workspace_source": work_context.workspace_source,
                    "binding_version": work_context.binding_version,
                },
                "conversation_boundary": {
                    "boundary_ref": conversation.boundary_ref,
                    "session_id": conversation.session_id,
                    "root_run_id": conversation.root_run_id,
                    "task_scope_id": conversation.task_scope_id,
                    "seed_message_refs": list(
                        conversation.seed_message_refs
                    ),
                    "continuation_message_refs": list(
                        conversation.continuation_message_refs
                    ),
                    "version": conversation.version,
                },
                "task_run_projection": {
                    "projection_id": projection.projection_id,
                    "session_id": projection.session_id,
                    "root_run_id": projection.root_run_id,
                    "task_scope_id": projection.task_scope_id,
                    "ui_state": projection.ui_state,
                    "version": projection.version,
                },
            }
        )
        proposed_tool_names = tuple(str(name) for name in proposed_tools)
        prepared_catalog_lease: Any | None = None
        companion_state = prepared.companion_authority_state
        companion_finalizer = prepared.companion_turn_finalizer
        if (companion_state is None) != (companion_finalizer is None):
            raise RuntimeError(
                "companion turn authority state/finalizer must be paired"
            )
        if prepared.prepared_context is not None:
            from deskpet.capabilities.contracts import CapabilityScope
            from deskpet.capabilities.refresh import (
                context_os_snapshot_ref,
                exposure_intent_ref,
            )
            from deskpet.tools.capabilities import ToolExposureIntent
            from deskpet.tools.prepared_snapshot import dump_context_os_snapshot

            prepared_tool_set = prepared.prepared_context.tool_set
            context_os = dump_context_os_snapshot(
                prepared_tool_set, prepared.eligibility
            )
            context_ref = context_os_snapshot_ref(
                prepared_tool_set, prepared.eligibility
            )
            exposure = (
                getattr(prepared.bundle, "tool_exposure_intent", None)
                or ToolExposureIntent()
            )
            exposure_ref = exposure_intent_ref(exposure)
            snapshots = services.get("capability_refresh_snapshots")
            platform = services.get("capability_platform")
            if snapshots is None or platform is None:
                raise PreflightBlocked(
                    RootBlockReasonV1.CAPABILITY_UNAVAILABLE,
                    ("capability-runtime:refresh-unavailable",),
                )
            await snapshots.put_context_os(context_ref, context_os)
            await snapshots.put_exposure_intent(exposure_ref, exposure)
            if companion_state is not None:
                owner_key = str(companion_state.owner_key)
                if not owner_key:
                    raise RuntimeError(
                        "prepared Companion owner identity is unavailable"
                    )
                profile_generation = int(
                    companion_state.owner.profile_generation
                )
                binding_epoch = int(companion_state.binding_epoch)
            else:
                identity_gate = getattr(platform, "identity_gate", None)
                if identity_gate is None:
                    raise PreflightBlocked(
                        RootBlockReasonV1.CAPABILITY_UNAVAILABLE,
                        ("capability-runtime:catalog-identity-unavailable",),
                    )
                frozen_owner = identity_gate.freeze()
                owner_key = str(frozen_owner.owner_key)
                profile_generation = int(
                    frozen_owner.owner.profile_generation
                )
                binding_epoch = int(frozen_owner.binding_epoch)
            scope = CapabilityScope.for_run(
                root_ref.run_id,
                project_root=work_context.workspace_root,
                user_key=owner_key,
            )
            prepared_catalog_lease = await platform.prepare_run_catalog_lease(
                scope=scope,
                owner_key=owner_key,
                prepared_tool_set=prepared_tool_set,
                prepared_tool_set_fingerprint=context_ref,
                run_id=root_ref.run_id,
                root_run_id=root_ref.run_id,
                request_id=turn.request_id,
                turn_id=turn.turn_id,
                owner_operation_id=f"run-start:{root_ref.run_id}:1",
            )
            base_run_context = (
                prepared.run_prepared_context
                or PreparedRunContextV1(persistence_required=True)
            )
            capture_envelope = dict(
                prepared_catalog_lease.prepared_tool_set_envelope
            )
            if (
                fingerprint_json(capture_envelope)
                != prepared_catalog_lease.prepared_tool_set_capture_hash
                or capture_envelope.get("external_ref") != context_ref
            ):
                raise RuntimeError(
                    "prepared ToolSet capture envelope is inconsistent"
                )
            exact_tools = capture_envelope.get("exact_tools")
            if isinstance(exact_tools, (str, bytes)) or not isinstance(
                exact_tools, (list, tuple)
            ) or any(not isinstance(item, Mapping) for item in exact_tools):
                raise RuntimeError(
                    "prepared ToolSet exact capture is unavailable"
                )
            lease_descriptor = prepared_catalog_lease.descriptor
            if companion_state is not None:
                from deskpet.companion.turn_authority import (
                    CompanionCatalogLeaseCaptureV1,
                    FinalizedCompanionTurnV1,
                )

                capture = CompanionCatalogLeaseCaptureV1(
                    run_catalog_content_stamp=(
                        prepared_catalog_lease.run_catalog_content_stamp
                    ),
                    process_catalog_stamp=(
                        prepared_catalog_lease.process_catalog_stamp
                    ),
                    catalog_snapshot_ref=(
                        prepared_catalog_lease.snapshot_ref
                    ),
                    capability_lease_intent_ref=(
                        prepared_catalog_lease.lease_intent_id
                    ),
                    exact_tools=tuple(
                        copy.deepcopy(dict(item))
                        for item in exact_tools
                    ),
                    lease_entries=_companion_catalog_lease_entries(
                        prepared_catalog_lease.lease_entries
                    ),
                )
                finalized = await companion_finalizer.finalize_after_catalog(
                    companion_state,
                    capture,
                    run_id=root_ref.run_id,
                )
                if not isinstance(finalized, FinalizedCompanionTurnV1):
                    raise RuntimeError(
                        "companion turn authority finalization is invalid"
                    )
                if (
                    finalized.preparation_id
                    != companion_state.preparation_id
                    or finalized.owner_key != owner_key
                    or finalized.profile_generation != profile_generation
                    or finalized.binding_epoch != binding_epoch
                ):
                    raise RuntimeError(
                        "companion turn authority identity drifted"
                    )
                base_run_context = replace(
                    base_run_context,
                    product_snapshot_ref=finalized.product_snapshot_ref,
                    product_snapshot_hash=finalized.product_snapshot_hash,
                )
                prepared = replace(
                    prepared,
                    growth_dependencies=tuple(
                        finalized.growth_dependencies
                    ),
                )
                finalized_selection = _enrich_companion_selection_exact_tools(
                    finalized.selection_payload,
                    exact_tools,
                )
            else:
                finalized_selection = None
            host_extensions = dict(base_run_context.host_extensions)
            host_extensions[lease_descriptor.kind] = lease_descriptor
            host_extension_payloads = dict(
                base_run_context.host_extension_payloads
            )
            prepared_tool_names = tuple(
                capability.ref.name
                for capability in (
                    *prepared_tool_set.direct,
                    *prepared_tool_set.activated,
                )
            )
            from deskpet.security.tool_public_projection import (
                ToolPresentationSnapshotExtension,
                compile_tool_presentation_policy,
            )

            presentation_policies = tuple(
                compile_tool_presentation_policy(tool_spec)
                for tool_name in sorted(set(prepared_tool_names))
                if (tool_spec := tool_registry.get(tool_name)) is not None
            )
            presentation_extension = (
                ToolPresentationSnapshotExtension(presentation_policies)
                if presentation_policies
                else None
            )
            selection_kind = "deskpet.companion.selection.v1"
            selection_payload = (
                finalized_selection
                if finalized_selection is not None
                else base_run_context.host_extension_payloads.get(
                    selection_kind
                )
            )
            if selection_payload is not None:
                if finalized_selection is None:
                    selection_payload = (
                        _enrich_companion_selection_exact_tools(
                            selection_payload,
                            exact_tools,
                        )
                    )
                selection_hash = fingerprint_json(selection_payload)
                host_extensions[selection_kind] = HostExtensionRefV1(
                    kind=selection_kind,
                    ref=f"companion-selection:{selection_hash}",
                    content_hash=selection_hash,
                )
                host_extension_payloads[selection_kind] = selection_payload
            snapshot_extensions = (
                {}
                if selection_payload is None
                else {selection_kind: dict(selection_payload)}
            )
            capability_snapshot = {
                "run_catalog_content_stamp": (
                    prepared_catalog_lease.run_catalog_content_stamp
                ),
                "process_catalog_stamp": (
                    prepared_catalog_lease.process_catalog_stamp
                ),
                "catalog_snapshot_ref": prepared_catalog_lease.snapshot_ref,
                "capability_lease_intent_ref": (
                    prepared_catalog_lease.lease_intent_id
                ),
                "prepared_tool_set_ref": context_ref,
                "capability_hash": effective_host.capability_hash,
                "product_snapshot_ref": (
                    base_run_context.product_snapshot_ref
                ),
                "host_extensions": snapshot_extensions,
            }
            run_prepared_context = replace(
                base_run_context,
                persistence_required=True,
                prepared_tool_ref=context_ref,
                prepared_tool_hash=context_ref,
                capability_lease_intent_ref=(
                    prepared_catalog_lease.lease_intent_id
                ),
                capability_lease_intent_hash=(
                    prepared_catalog_lease.lease_intent_hash
                ),
                owner_key=owner_key,
                profile_generation=profile_generation,
                binding_epoch=binding_epoch,
                prepared_tool_names=prepared_tool_names,
                capability_snapshot=capability_snapshot,
                host_extensions=host_extensions,
                host_extension_payloads=host_extension_payloads,
                start_commit_extensions=(
                    *base_run_context.start_commit_extensions,
                    prepared_catalog_lease.start_commit_extension,
                    *(
                        (presentation_extension,)
                        if presentation_extension is not None
                        else ()
                    ),
                ),
                after_start_commit_handshakes=(
                    *base_run_context.after_start_commit_handshakes,
                    prepared_catalog_lease.after_start_handshake,
                ),
                terminal_commit_extensions=(
                    *base_run_context.terminal_commit_extensions,
                    prepared_catalog_lease.terminal_commit_extension,
                ),
                after_terminal_commit_cleanup=(
                    *base_run_context.after_terminal_commit_cleanup,
                    prepared_catalog_lease.after_terminal_cleanup,
                ),
            )
            prepared = replace(
                prepared,
                run_prepared_context=run_prepared_context,
            )
            request_payload["context_os"] = context_os
            request_payload["tool_set_snapshot_ref"] = context_ref
            request_payload["capability_refresh"] = {
                "catalog_snapshot_ref": prepared_catalog_lease.snapshot_ref,
                "run_catalog_content_stamp": (
                    prepared_catalog_lease.run_catalog_content_stamp
                ),
                "process_catalog_stamp": (
                    prepared_catalog_lease.process_catalog_stamp
                ),
                "lease_intent_ref": (
                    prepared_catalog_lease.lease_intent_id
                ),
                "lease_intent_hash": (
                    prepared_catalog_lease.lease_intent_hash
                ),
                "tool_set_snapshot_ref": context_ref,
                "exposure_intent_ref": exposure_ref,
                "scope": scope.to_dict(),
                "refresh_pending": None,
            }
            proposed_tool_names = tuple(
                run_prepared_context.prepared_tool_names
            )
        elif services.get("capability_platform") is not None:
            raise RuntimeError(
                "new durable run requires an exact prepared tool-set capture"
            )
        try:
            handle = await self._run_client.start(
                {
                    "text": turn.text,
                    "request_id": turn.request_id,
                    "turn_id": turn.turn_id,
                    "venue": turn.venue,
                    "workspace_context": turn.workspace_ref is not None,
                    "canonical_messages": prepared.messages,
                    "proposed_tools": list(proposed_tool_names),
                    "payload": request_payload,
                    "admission": admission,
                    "provider_launch_snapshot": (
                        provider_launch_snapshot if admission else None
                    ),
                },
                effective_host,
                prepared=prepared.run_prepared_context,
            )
        except BaseException:
            if prepared_catalog_lease is not None:
                try:
                    await prepared_catalog_lease.release_prepared()
                except RuntimeError as cleanup_error:
                    if str(cleanup_error) != (
                        "run_catalog_lease_commit_outcome_unknown"
                    ):
                        raise
            raise
        if prepared_catalog_lease is not None:
            await platform.require_run_catalog_ready(handle.run_id)
        if admission is not None:
            await domain_sink.store_plan({**dict(admission.presentation), "run_id": handle.run_id})
        return self._session(handle, presentation_context)
    def _session(self, handle: VenueRunHandle,
                 context: RunPresentationContext) -> ProductVenueRunSession:
        return ProductVenueRunSession(handle=handle, presenter=self._presenter,
            event_adapter=self._event_adapter, presentation_context=context)
    async def _present_commands(
        self,
        commands: Sequence[Any],
        sink: ProductDomainSink,
    ) -> bool:
        for command in commands:
            if not await self._presenter.present_domain(command, sink):
                return False
        return True


__all__ = [
    "KernelRunClient",
    "ProductVenueRunAdapter",
    "ProductVenueRunResult",
    "ProductVenueRunSession",
    "VenueRunHandle",
]

"""Transport-neutral adapter for Text and Voice venue clients.

This module is deliberately not registered by the production bootstrap before
the atomic harness activation. It translates transport payloads into the six
RunKernel operations while trusted identity remains owned by the resolver.
"""

from __future__ import annotations

import uuid
import time
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from deskpet.execution.contracts import AdmissionSpec, ActorContext, DecisionKind, ProviderLaunchSnapshot, RunEvent, RunNotFound, RunRef
from deskpet.agent.product_domain_sink import ProductDomainSink
from deskpet.agent.run_presenter import (
    CanonicalRunEventPresentationAdapter,
    PresentationState,
    RunPresentationContext,
    RunPresenter,
)
from deskpet.agent.turn_preparer import ProductTurnPreparer, TurnInput
from deskpet.harness.kernel import HostContext, RunKernel, RunRequest, root_run_identity
from deskpet.harness.ports import DecisionSignal


class ProviderLaunchPolicyRegistry:
    def __init__(self, policies: Iterable[ProviderLaunchSnapshot] = ()) -> None:
        self._policies = {
            (item.provider_id, item.adapter_id, item.adapter_version): item
            for item in policies
        }

    def resolve(self, provider: object) -> ProviderLaunchSnapshot:
        identity = tuple(str(getattr(provider, field, "") or "")
                         for field in ("provider_id", "adapter_id", "adapter_version"))
        if not all(identity):
            raise RuntimeError("provider launch identity is incomplete")
        policy = self._policies.get(identity)
        if policy is None:
            raise RuntimeError(f"provider launch policy is unavailable: {'/'.join(identity)}")
        return policy


class VenueContextResolver(Protocol):
    """Resolve authenticated host state; model payloads never implement this."""

    def resolve_host(self, transport: Mapping[str, object]) -> HostContext: ...

    def resolve_actor(
        self,
        transport: Mapping[str, object],
        *,
        root_run_id: str,
    ) -> ActorContext: ...


@dataclass(frozen=True, slots=True)
class VenueRunHandle:
    run_id: str
    events: AsyncIterator[RunEvent]


@dataclass(frozen=True, slots=True)
class ProductVenueRunResult:
    run_id: str | None
    status: str
    final_text: str = ""


class KernelRunClient:
    def __init__(self, kernel: RunKernel, resolver: VenueContextResolver) -> None:
        self._kernel = kernel
        self._resolver = resolver

    async def start(
        self,
        request: Mapping[str, object],
        host: Mapping[str, object],
    ) -> VenueRunHandle:
        trusted = self._resolver.resolve_host(host)
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
        canonical_messages = tuple(dict(message) for message in raw_messages)
        admission = request.get("admission")
        provider_snapshot = request.get("provider_launch_snapshot")
        if admission is not None and not isinstance(admission, AdmissionSpec):
            raise ValueError("admission must be a typed AdmissionSpec")
        if provider_snapshot is not None and not isinstance(provider_snapshot, ProviderLaunchSnapshot):
            raise ValueError("provider launch snapshot must be typed")
        handle = await self._kernel.start(
            RunRequest(
                text=text,
                request_id=request_id,
                turn_id=turn_id,
                venue=str(host.get("venue") or "text"),
                mode=str(request.get("mode") or host.get("mode") or "auto"),
                workspace_context=bool(
                    request.get(
                        "workspace_context",
                        host.get("workspace_context", False),
                    )
                ),
                proposed_tools=tuple(str(name) for name in raw_tools),
                canonical_messages=canonical_messages,
                payload=dict(raw_payload),
                admission=admission,
                provider_launch_snapshot=provider_snapshot,
            ),
            trusted,
        )
        actor = self._resolver.resolve_actor(host, root_run_id=handle.root_run_id)
        return VenueRunHandle(
            run_id=handle.ref.run_id,
            events=self._kernel.observe(handle.ref, actor),
        )

    async def resume(self, request_id: str, turn_id: str, host: Mapping[str, object]) -> VenueRunHandle | None:
        trusted = self._resolver.resolve_host(host)
        _, ref = root_run_identity(trusted.session_id, request_id, turn_id)
        actor = self._resolver.resolve_actor(host, root_run_id=ref.run_id)
        try:
            handle = await self._kernel.recover(ref, actor)
        except RunNotFound:
            return None
        return VenueRunHandle(handle.ref.run_id, self._kernel.observe(handle.ref, actor))

    async def signal(
        self,
        ref: Mapping[str, object],
        actor: Mapping[str, object],
        signal: Mapping[str, object],
    ) -> object:
        run_ref = self._ref(ref)
        trusted_actor = self._resolver.resolve_actor(actor, root_run_id=run_ref.run_id)
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

    async def cancel(
        self,
        ref: Mapping[str, object],
        actor: Mapping[str, object],
        reason: str,
    ) -> object:
        run_ref = self._ref(ref)
        trusted_actor = self._resolver.resolve_actor(actor, root_run_id=run_ref.run_id)
        return await self._kernel.cancel(run_ref, trusted_actor, reason)

    async def close(
        self,
        ref: Mapping[str, object],
        actor: Mapping[str, object],
    ) -> None:
        run_ref = self._ref(ref)
        trusted_actor = self._resolver.resolve_actor(actor, root_run_id=run_ref.run_id)
        await self._kernel.close(run_ref, trusted_actor)

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
        run_client: KernelRunClient,
        host: Mapping[str, object],
        presenter: RunPresenter,
        event_adapter: CanonicalRunEventPresentationAdapter,
        presentation_context: RunPresentationContext,
    ) -> None:
        self.run_id = handle.run_id
        self._source = handle.events
        self._run_client = run_client
        self._host = dict(host)
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
        return await self._run_client.signal(self._ref(), self._host, signal)

    async def cancel(self, reason: str) -> object:
        return await self._run_client.cancel(self._ref(), self._host, reason)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._presenter.finish_turn(self._context, self._state)
        finally:
            await self._run_client.close(self._ref(), self._host)

    def _ref(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "expected_session_id": self._context.session_id,
        }


class ProductVenueRunAdapter:
    """Dormant venue-neutral product chain kept outside the production owner."""

    def __init__(
        self,
        *,
        preparer: ProductTurnPreparer,
        run_client: KernelRunClient,
        presenter: RunPresenter,
        event_adapter: CanonicalRunEventPresentationAdapter | None = None,
        provider_launch_policies: ProviderLaunchPolicyRegistry | None = None,
    ) -> None:
        self._preparer = preparer
        self._run_client = run_client
        self._presenter = presenter
        self._event_adapter = event_adapter or CanonicalRunEventPresentationAdapter()
        self._provider_launch_policies = provider_launch_policies or ProviderLaunchPolicyRegistry()

    async def open(
        self,
        turn: TurnInput,
        host: Mapping[str, object],
        *,
        services: Mapping[str, Any],
        config: Any,
        local_llm: Any,
        tool_registry: Any,
        provider: Any,
        code_mode: Any,
        in_code_mode: bool,
        current_message_id: int | None,
        summary_user_is_confused: Any,
        summary_latest_task_snapshot: Any,
        summary_build_reinject_msg: Any,
        presentation_context: RunPresentationContext,
        domain_sink: ProductDomainSink,
        proposed_tools: Sequence[str] = (),
    ) -> ProductVenueRunSession | ProductVenueRunResult:
        session_id = str(host.get("session_id") or "").strip()
        venue = str(host.get("venue") or turn.venue).strip()
        if session_id != turn.session_id:
            raise ValueError("turn and trusted host session_id must match")
        if venue != turn.venue:
            raise ValueError("turn and trusted host venue must match")
        resumed = await self._run_client.resume(turn.request_id, turn.turn_id, host)
        if resumed is not None:
            return self._session(resumed, host, presentation_context)

        prepared = await self._preparer.prepare_context(
            turn,
            services=services,
            config=config,
            local_llm=local_llm,
            tool_registry=tool_registry,
            current_message_id=current_message_id,
            summary_user_is_confused=summary_user_is_confused,
            summary_latest_task_snapshot=summary_latest_task_snapshot,
            summary_build_reinject_msg=summary_build_reinject_msg,
        )
        presentation_context.messages = prepared.messages
        presentation_context.assembler = prepared.assembler
        presentation_context.bundle = prepared.bundle

        routed = await self._preparer.route_intent(prepared, services=services)
        if not await self._present_commands(routed.commands, domain_sink):
            return ProductVenueRunResult(None, "short_circuited")
        if not routed.continue_turn:
            return ProductVenueRunResult(None, "short_circuited")
        planned = await self._preparer.plan_decision(
            routed,
            services=services,
            config=config,
            provider=provider,
            code_mode=code_mode,
            in_code_mode=in_code_mode,
        )
        blocking = [item for item in planned.commands if item.kind == "plan_confirmation"]
        if blocking and (len(blocking) != 1 or len(planned.commands) != 2
                         or planned.commands[0].kind != "plan_proposed"):
            raise RuntimeError("blocking product admission is incomplete or unknown")
        admission = provider_snapshot = None
        if blocking:
            prompt = dict(blocking[0].payload)
            presentation = dict(planned.commands[0].payload)
            admission = AdmissionSpec(DecisionKind.PLAN, 1, 1, prompt, presentation,
                time.time() + float(prompt.get("timeout_seconds", 900.0)))
            provider_snapshot = self._provider_launch_policies.resolve(provider)
        else:
            if not await self._present_commands(planned.commands, domain_sink):
                return ProductVenueRunResult(None, "cancelled")

        handle = await self._run_client.start(
            {
                "text": turn.text,
                "request_id": turn.request_id,
                "turn_id": turn.turn_id,
                "mode": turn.mode,
                "canonical_messages": prepared.messages,
                "proposed_tools": list(proposed_tools),
                "payload": {
                    "provider_ref": turn.provider_ref,
                    "capability_ref": turn.capability_ref,
                    "workspace_ref": turn.workspace_ref,
                },
                "admission": admission,
                "provider_launch_snapshot": provider_snapshot,
            },
            host,
        )
        if admission is not None:
            await domain_sink.store_plan({**dict(admission.presentation), "run_id": handle.run_id})
        return self._session(handle, host, presentation_context)

    def _session(self, handle: VenueRunHandle, host: Mapping[str, object],
                 context: RunPresentationContext) -> ProductVenueRunSession:
        return ProductVenueRunSession(handle=handle, run_client=self._run_client,
            host=host, presenter=self._presenter, event_adapter=self._event_adapter,
            presentation_context=context)

    async def execute(
        self,
        turn: TurnInput,
        host: Mapping[str, object],
        **options: Any,
    ) -> ProductVenueRunResult:
        outcome = await self.open(turn, host, **options)
        if isinstance(outcome, ProductVenueRunResult):
            return outcome
        session = outcome
        try:
            async for _event in session.events:
                pass
        finally:
            await session.close()
        return session.result

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
    "ProviderLaunchPolicyRegistry",
    "ProductVenueRunAdapter",
    "ProductVenueRunResult",
    "ProductVenueRunSession",
    "VenueContextResolver",
    "VenueRunHandle",
]

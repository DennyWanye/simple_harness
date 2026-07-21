"""Transport-neutral adapter for Text and Voice venue clients.

This module is deliberately not registered by the production bootstrap before
the atomic harness activation. It translates transport payloads into the six
RunKernel operations while trusted identity remains owned by the resolver.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from deskpet.execution.contracts import ActorContext, RunEvent, RunRef
from deskpet.agent.product_domain_sink import ProductDomainSink
from deskpet.agent.run_presenter import (
    CanonicalRunEventPresentationAdapter,
    PresentationState,
    RunPresentationContext,
    RunPresenter,
)
from deskpet.agent.turn_preparer import ProductTurnPreparer, TurnInput
from deskpet.harness.kernel import HostContext, RunKernel, RunRequest
from deskpet.harness.ports import DecisionSignal


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
    """Observable result of the test-only product-to-kernel chain."""

    run_id: str | None
    status: str
    final_text: str = ""


class KernelRunClient:
    """Narrow structural RunClient shared by Text and Voice transports."""

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
        ):
            raise ValueError("canonical_messages must be a sequence of mappings")
        canonical_messages: list[dict[str, Any]] = []
        for message in raw_messages:
            if not isinstance(message, Mapping):
                raise ValueError("canonical_messages must contain mappings")
            canonical_messages.append(dict(message))
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
                canonical_messages=tuple(canonical_messages),
                payload=dict(raw_payload),
            ),
            trusted,
        )
        actor = self._resolver.resolve_actor(host, root_run_id=handle.root_run_id)
        return VenueRunHandle(
            run_id=handle.ref.run_id,
            events=self._kernel.observe(handle.ref, actor),
        )

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


class ProductVenueRunAdapter:
    """Test-only venue-neutral product chain kept outside the production owner.

    R5 uses this adapter to prove the final composition order while ``_run_chat``
    remains the sole production ingress.  It owns no durable state: preparation
    stays with ProductTurnPreparer, lifecycle with KernelRunClient/RunKernel and
    product projection with RunPresenter.
    """

    def __init__(
        self,
        *,
        preparer: ProductTurnPreparer,
        run_client: KernelRunClient,
        presenter: RunPresenter,
        event_adapter: CanonicalRunEventPresentationAdapter | None = None,
    ) -> None:
        self._preparer = preparer
        self._run_client = run_client
        self._presenter = presenter
        self._event_adapter = event_adapter or CanonicalRunEventPresentationAdapter()

    async def execute(
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
    ) -> ProductVenueRunResult:
        session_id = str(host.get("session_id") or "").strip()
        venue = str(host.get("venue") or turn.venue).strip()
        if session_id != turn.session_id:
            raise ValueError("turn and trusted host session_id must match")
        if venue != turn.venue:
            raise ValueError("turn and trusted host venue must match")

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
            },
            host,
        )
        state = PresentationState()
        status = "unknown"
        try:
            async for event in handle.events:
                status = event.status.value
                await self._presenter.present_run_event(
                    event,
                    self._event_adapter,
                    presentation_context,
                    state,
                )
            await self._presenter.finish_turn(presentation_context, state)
            return ProductVenueRunResult(handle.run_id, status, state.final_text)
        finally:
            await self._run_client.close(
                {"run_id": handle.run_id, "expected_session_id": session_id},
                host,
            )

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
    "VenueContextResolver",
    "VenueRunHandle",
]

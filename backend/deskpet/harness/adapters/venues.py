"""Transport-neutral adapter for Text and Voice venue clients.

This module is deliberately not registered by the production bootstrap before
the atomic harness activation. It translates transport payloads into the six
RunKernel operations while trusted identity remains owned by the resolver.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Protocol

from deskpet.execution.contracts import ActorContext, RunEvent, RunRef
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

    @staticmethod
    def _ref(value: Mapping[str, object]) -> RunRef:
        run_id = str(value.get("run_id") or "").strip()
        session_id = str(value.get("expected_session_id") or "").strip()
        if not run_id or not session_id:
            raise ValueError("run_id and expected_session_id are required")
        return RunRef(run_id, session_id)


__all__ = ["KernelRunClient", "VenueContextResolver", "VenueRunHandle"]

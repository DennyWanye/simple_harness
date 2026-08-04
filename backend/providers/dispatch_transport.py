from __future__ import annotations

import asyncio

import httpx

from deskpet.execution.dispatch import current_dispatch_handoff


class DispatchAwareAsyncTransport(httpx.AsyncBaseTransport):
    """Emit a start ack at the public httpx transport handoff boundary.

    The wrapped transport is frozen at construction.  A coordinated request
    is handed to one task exactly once; the ack is emitted after that task has
    been scheduled and before response headers or body are awaited.
    """

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport,
        *,
        adapter_identity: str,
    ) -> None:
        if not isinstance(transport, httpx.AsyncBaseTransport):
            raise TypeError("transport must implement httpx.AsyncBaseTransport")
        self._transport = transport
        self._adapter_identity = str(adapter_identity or "").strip()
        if not self._adapter_identity:
            raise ValueError("adapter_identity is required")

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        handoff = current_dispatch_handoff()
        if handoff is None:
            raise RuntimeError(
                "dispatch_transport_without_coordinator: coordinated transport "
                "requires an active dispatch handoff"
            )
        handoff.mark_transport_entered()
        request_task = asyncio.create_task(
            self._transport.handle_async_request(request),
            name=f"httpx-dispatch:{handoff.identity.invocation_id}",
        )
        # Give the frozen underlying task one scheduling turn.  Cancellation
        # before this method is entered leaves the physical transport count at
        # zero; cancellation after task creation is conservatively unknown.
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            handoff.handoff_attempted = True
            raise
        handoff.acknowledge(self._adapter_identity)
        return await request_task

    async def aclose(self) -> None:
        await self._transport.aclose()

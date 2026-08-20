"""Product ingress facade for the SDK Runtime.

IMPORTANT: This is the sole ingress for all product entry points (text/voice/
background). Do NOT import SDK Runtime internals directly from main.py or other
product modules — use this facade exclusively.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simple_harness import RunId
from simple_harness.contracts import ExecutionSessionId, RequestId
from simple_harness.runtime import RunStart

from .composition import ProductSdkRuntimeStack, SdkRuntimeNotReady, SdkRuntimeReady


@dataclass(frozen=True, slots=True)
class IngressStartReceipt:
    """Receipt proving a Run was started through the ingress."""

    run_id: str
    generation: int
    session_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class IngressSignalReceipt:
    """Receipt proving a signal was delivered through the ingress."""

    run_id: str
    delivery_id: str
    generation: int
    accepted: bool = True
    duplicate: bool = False
    reason: str | None = None


class SdkRuntimeIngress:
    """Sole ingress facade for the SDK Runtime — text/voice/background entry.

    All product entry points (WebSocket text/chat_v2, voice host, companion
    background) MUST use this facade. Direct access to SDK Runtime internals
    is forbidden in production code.
    """

    def __init__(self, stack: ProductSdkRuntimeStack) -> None:
        if not isinstance(stack, ProductSdkRuntimeStack):
            raise TypeError("stack must be ProductSdkRuntimeStack")
        self._stack = stack
        self._accepting = False

    @property
    def accepting(self) -> bool:
        """Whether the ingress is open for new Runs."""
        return self._accepting

    def require_ready(self) -> SdkRuntimeReady:
        """Require SDK Runtime is ready or raise SdkRuntimeNotReady."""
        return self._stack.require_ready()

    def open(self) -> None:
        """Open the ingress barrier to accept new Runs."""
        self._accepting = True

    def close(self) -> None:
        """Close the ingress barrier (no new Runs accepted)."""
        self._accepting = False

    async def start(
        self,
        *,
        session_id: str,
        request_id: str,
        turn_id: str,
        payload: dict[str, Any],
        session_generation: int,
        tool_catalog_fingerprint: str | None = None,
        provider_budget_fingerprint: str | None = None,
    ) -> IngressStartReceipt:
        """Start a new Run through the sole ingress.

        Deterministic root identity: same (session_id, request_id, turn_id)
        always produces the same RunId for replay idempotency.
        """
        if not self._accepting:
            raise SdkRuntimeNotReady("SDK Runtime ingress is not accepting new Runs")
        ready = self._stack.require_ready()

        # Deterministic root identity for replay idempotency
        run_id = self._compute_run_id(session_id, request_id, turn_id)

        start = RunStart(
            ExecutionSessionId(session_id),
            run_id,
            RequestId(request_id),
            turn_id,
            payload,
            session_generation,
            tool_catalog_fingerprint,
            provider_budget_fingerprint,
        )
        await ready.client.start(start)

        return IngressStartReceipt(
            run_id=run_id.value,
            generation=ready.generation,
            session_id=session_id,
            request_id=request_id,
        )

    async def signal(
        self,
        *,
        run_id: str,
        signal_id: str,
        payload: dict[str, Any],
    ) -> IngressSignalReceipt:
        """Deliver a signal to a running or completed Run."""
        if not self._accepting:
            raise SdkRuntimeNotReady("SDK Runtime ingress is not accepting signals")
        ready = self._stack.require_ready()

        # SDK 0.1.5 enqueues continuations synchronously. Awaiting this value
        # would fail after the durable signal had already been accepted.
        delivery = ready.client.signal(
            RunId(run_id),
            signal_id=signal_id,
            payload=payload,
        )

        return IngressSignalReceipt(
            run_id=run_id,
            delivery_id=delivery.continuation_id,
            generation=ready.generation,
            reason="continuation_queued",
        )

    async def decide_authorization(
        self,
        *,
        run_id: str,
        decision_id: str,
        nonce: str,
        expected_version: int,
        decision: str,
    ) -> IngressSignalReceipt:
        """Resolve one fenced Tool authorization through the SDK API."""

        if not self._accepting:
            raise SdkRuntimeNotReady("SDK Runtime ingress is not accepting signals")
        ready = self._stack.require_ready()
        from simple_harness.tools import AuthorizationDecision

        normalized = str(decision).strip().lower()
        if normalized == "allow_session":
            # SDK 0.1.5 exposes a per-decision ALLOW/DENY contract. The
            # product's broader session preference remains a UI preference;
            # this concrete waiting decision is still an ALLOW.
            normalized = "allow"
        if normalized not in {"allow", "deny"}:
            raise ValueError("authorization decision must be allow or deny")
        await ready.client.decide_authorization(
            RunId(run_id),
            decision_id=decision_id,
            nonce=nonce,
            expected_version=int(expected_version),
            decision=AuthorizationDecision(normalized),
        )
        return IngressSignalReceipt(
            run_id=run_id,
            delivery_id=decision_id,
            generation=ready.generation,
        )

    async def cancel(self, run_id: str) -> None:
        """Request cancellation of a Run."""
        if not self._accepting:
            raise SdkRuntimeNotReady("SDK Runtime ingress is not accepting cancellations")
        ready = self._stack.require_ready()
        await ready.client.cancel(RunId(run_id))

    def query(self, run_id: str):  # type: ignore[no-untyped-def]
        """Query Run state (does not require ingress open)."""
        ready = self._stack.require_ready()
        return ready.client.query(RunId(run_id))

    async def reconcile(self) -> None:
        """Trigger reconciliation of unknown effects/providers."""
        ready = self._stack.require_ready()
        await ready.runtime.reconcile()

    async def recover(self) -> None:
        """Recover incomplete Runs after restart."""
        ready = self._stack.require_ready()
        await ready.runtime.recover()

    async def wait_idle(self, run_id: str) -> None:
        """Wait until a Run reaches an idle state (completed/failed/cancelled)."""
        ready = self._stack.require_ready()
        await ready.runtime.wait_idle(RunId(run_id))

    @staticmethod
    def _compute_run_id(session_id: str, request_id: str, turn_id: str) -> RunId:
        """Compute deterministic RunId for replay idempotency."""
        import hashlib

        identity = hashlib.sha256(
            f"{session_id}\0{request_id}\0{turn_id}".encode("utf-8")
        ).hexdigest()
        return RunId(f"product-sdk-{identity}")


__all__ = (
    "IngressSignalReceipt",
    "IngressStartReceipt",
    "SdkRuntimeIngress",
)

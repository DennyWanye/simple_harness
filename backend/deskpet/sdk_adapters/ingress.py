"""Product ingress facade for the SDK Runtime.

IMPORTANT: This is the sole ingress for all product entry points (text/voice/
background). Do NOT import SDK Runtime internals directly from main.py or other
product modules — use this facade exclusively.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from simple_harness import RunId, thaw_json
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


@dataclass(frozen=True, slots=True)
class IngressAuthorizationRequest:
    """Public-safe desktop projection of one durable SDK Tool decision."""

    sdk_run_id: str
    run_id: str
    session_id: str
    request_id: str
    task_scope_id: str
    turn_id: str
    decision_id: str
    nonce: str
    version: int
    tool_name: str
    prompt: str
    params: dict[str, Any]
    category: str
    dangerous: bool
    expires_at: float | None


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

    def list_open_authorizations(
        self,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[IngressAuthorizationRequest, ...]:
        """Return public-safe open Tool decisions for live/reconnect UI replay."""

        rows = self._stack.list_open_authorization_decisions(
            run_id=run_id,
            session_id=session_id,
        )
        projected: list[IngressAuthorizationRequest] = []
        for decision, start in rows:
            request = thaw_json(getattr(decision, "request", {}))
            start_value = thaw_json(start)
            if not isinstance(request, dict) or not isinstance(start_value, dict):
                continue
            start_input = start_value.get("input")
            metadata = (
                start_input.get("context_metadata")
                if isinstance(start_input, dict)
                else None
            )
            if not isinstance(metadata, dict):
                continue
            tool_name = str(request.get("tool_name") or "").strip()
            decision_id = str(getattr(decision, "decision_id", "")).strip()
            nonce = str(request.get("nonce") or "").strip()
            sdk_run_id = str(getattr(decision, "run_id", "")).strip()
            root_run_id = str(metadata.get("root_run_id") or "").strip()
            bound_session_id = str(metadata.get("session_id") or "").strip()
            if not all(
                (tool_name, decision_id, nonce, sdk_run_id, root_run_id, bound_session_id)
            ):
                continue
            category = "tool"
            dangerous = False
            tool_authority = metadata.get("tool_authority")
            inventory = (
                tool_authority.get("inventory")
                if isinstance(tool_authority, dict)
                else None
            )
            if isinstance(inventory, list):
                item = next(
                    (
                        value
                        for value in inventory
                        if isinstance(value, dict)
                        and str(value.get("name") or "") == tool_name
                    ),
                    None,
                )
                if item is not None:
                    category = str(item.get("permission_category") or "tool")
                    dangerous = bool(item.get("dangerous", False))
            arguments = request.get("arguments")
            projected.append(
                IngressAuthorizationRequest(
                    sdk_run_id=sdk_run_id,
                    run_id=root_run_id,
                    session_id=bound_session_id,
                    request_id=decision_id,
                    task_scope_id=str(metadata.get("task_scope_id") or ""),
                    turn_id=str(start_value.get("turn_id") or ""),
                    decision_id=decision_id,
                    nonce=nonce,
                    version=int(getattr(decision, "version", 0)),
                    tool_name=tool_name,
                    prompt=str(request.get("prompt") or ""),
                    params=(dict(arguments) if isinstance(arguments, dict) else {}),
                    category=category,
                    dangerous=dangerous,
                    expires_at=(
                        None
                        if request.get("expires_at") is None
                        else float(request["expires_at"])
                    ),
                )
            )
        return tuple(projected)

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
    "IngressAuthorizationRequest",
    "IngressSignalReceipt",
    "IngressStartReceipt",
    "SdkRuntimeIngress",
)

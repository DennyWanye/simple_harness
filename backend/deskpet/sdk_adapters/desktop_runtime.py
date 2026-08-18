"""Minimal desktop bridge used to exercise the installed Simple Harness SDK.

This is intentionally a narrow local-test ingress: ordinary ReAct chat plus two
real DeskPet Tools (``process_list`` and ``ppt_create``).  The legacy product
runtime stays available when ``DESKPET_SDK_DESKTOP_TEST`` is not enabled.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import httpx
import structlog

from simple_harness.contracts import ExecutionSessionId, RequestId, RunId
from simple_harness.execution.budget import BudgetPolicy, FrozenPriceEstimator
from simple_harness.execution.delivery import DeliveryDispatcher
from simple_harness.execution.dispatch import ProviderInvocationCoordinator
from simple_harness.execution.uow import RunState
from simple_harness.providers import (
    ProviderReconciliationObservation,
    ProviderReconciliationState,
)
from simple_harness.runtime import (
    RunStart,
    RuntimePorts,
    RuntimeProfile,
    SqliteContextPort,
)
from simple_harness.runtime.drivers import build_react_driver
from simple_harness.runtime.termination import TerminationLimits
from simple_harness.tools import (
    AuthorizationDecision,
    AuthorizationReceipt,
    AuthorizationResult,
    EffectExecutor,
    FunctionTool,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from simple_harness.tools.reconciliation import (
    ReconciliationObservation,
    ReconciliationState,
)

from deskpet.tool_catalog import load_tool_manifest, migrate_tool_schemas

logger = structlog.get_logger(__name__)

# Global registry: run_id → ProductDeliveryAdapter
# Populated by _execute_sdk_run() before starting a Run
# Cleaned up after Run completes
_delivery_adapters: dict[str, "ProductDeliveryAdapter"] = {}  # type: ignore[name-defined]
from deskpet.tools.os_tools.process_tools import process_list
from deskpet.tools.ppt_tools import _handle_ppt_create

from .composition import OwnedResourceCloser, ProductSdkRuntimeStack, SdkRuntimeBuildInputs
from .provider import ProductProviderAdapter
from .runtime_paths import ProductRuntimePathsAdapter
from .sdk_candidate import build_candidate_identity


@dataclass(frozen=True, slots=True)
class DesktopSdkChatResult:
    run_id: str
    state: str
    text: str
    tool_names: tuple[str, ...]


class _AllowSelectedTools:
    """Immediate allow policy, safe because the registry contains only two Tools."""

    async def prepare(self, prepared):  # type: ignore[no-untyped-def]
        if prepared.call.name not in {"process_list", "ppt_create"}:
            return AuthorizationResult(
                AuthorizationDecision.DENY,
                reason_code="desktop_test_tool_not_allowed",
                public_message="This Tool is not enabled for the desktop SDK test.",
            )
        return AuthorizationResult(
            AuthorizationDecision.ALLOW,
            receipt_ref=f"desktop-sdk-test:allow:{prepared.effect_id.value}",
        )

    async def bind_decision(self, prepared, request, decision, sdk_receipt):  # type: ignore[no-untyped-def]
        del prepared, request, decision
        return _host_receipt("decision", sdk_receipt)

    async def bind_effect_handoff(self, prepared, authorization_receipt_ref, sdk_receipt):  # type: ignore[no-untyped-def]
        del authorization_receipt_ref
        return _host_receipt(f"handoff:{prepared.effect_id.value}", sdk_receipt)


def _host_receipt(kind: str, sdk_receipt: AuthorizationReceipt) -> AuthorizationReceipt:
    digest = hashlib.sha256(
        f"desktop-sdk-test:{kind}:{sdk_receipt.receipt_hash}".encode("utf-8")
    ).hexdigest()
    return AuthorizationReceipt(
        receipt_ref=f"desktop-sdk-test:{kind}:{digest}",
        receipt_hash=digest,
        bound_sdk_receipt_hash=sdk_receipt.receipt_hash,
    )


class _ToolReconciliation:
    async def observe(self, effect):  # type: ignore[no-untyped-def]
        return ReconciliationObservation(
            ReconciliationState.STILL_UNKNOWN,
            f"desktop-sdk-test:tool-unknown:{effect.effect_id.value}",
        )


class _ProviderReconciliation:
    async def observe(self, invocation):  # type: ignore[no-untyped-def]
        return ProviderReconciliationObservation(
            ProviderReconciliationState.STILL_UNKNOWN,
            f"desktop-sdk-test:provider-unknown:{invocation.invocation_id}",
        )


class _RuntimeReconciliation:
    async def reconcile(self) -> None:
        return None


class _ToolCatalog:
    def current_generation(self) -> int:
        return 1


class _DeliverySink:
    """Bridge between SDK DeliveryDispatcher and ProductDeliveryAdapter.

    Routes delivery events to the appropriate per-request adapter based on
    run_id extracted from the payload or idempotency_key.
    """

    async def deliver(self, payload: dict, *, idempotency_key: str) -> None:  # type: ignore[no-untyped-def]
        """Deliver one SDK event to the registered ProductDeliveryAdapter.

        Called by SDK DeliveryDispatcher background task. Routes the event
        to the adapter registered for this run_id.

        Args:
            payload: SDK delivery payload (should contain run_id or RunEvent data)
            idempotency_key: Unique delivery key from SDK
        """
        # Import here to avoid circular dependency
        from .delivery import ProductDeliveryAdapter

        # Extract run_id from payload or idempotency_key
        # Strategy 1: Check if payload contains run_id directly
        run_id = payload.get("run_id")

        # Strategy 2: Check nested event structure first (before parsing idempotency_key)
        if not run_id and "event" in payload:
            event_data = payload.get("event")
            if isinstance(event_data, dict):
                run_id = event_data.get("run_id")

        # Strategy 3: If not found, try parsing from idempotency_key
        # (Assuming format like "run-id:event-id" or similar)
        if not run_id and ":" in idempotency_key:
            run_id = idempotency_key.split(":")[0]

        if not run_id:
            logger.warning(
                "delivery_sink_no_run_id",
                payload_keys=list(payload.keys()),
                idempotency_key=idempotency_key,
            )
            return

        # Lookup adapter from global registry
        adapter = _delivery_adapters.get(run_id)
        if adapter is None:
            logger.warning(
                "delivery_adapter_not_found",
                run_id=run_id,
                idempotency_key=idempotency_key,
            )
            return

        # Forward to adapter
        await adapter.handle_event(payload, idempotency_key)


def _tool_result(call_id, raw: object) -> ToolResult:  # type: ignore[no-untyped-def]
    value: object = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
    if isinstance(value, Mapping) and value.get("ok") is False:
        error = value.get("error")
        if isinstance(error, Mapping):
            code = str(error.get("code") or "tool_failed")
            message = str(error.get("message") or "Tool execution failed.")
        else:
            code = "tool_failed"
            message = str(error or "Tool execution failed.")
        return ToolResult.failed(call_id, code, message)
    return ToolResult.succeeded(call_id, value)  # type: ignore[arg-type]


def _desktop_tools() -> ToolRegistry:
    manifest = load_tool_manifest()
    schemas, _ = migrate_tool_schemas(manifest)
    by_name = {str(item["name"]): item for item in manifest.tools}

    async def invoke_process(arguments, context):  # type: ignore[no-untyped-def]
        raw = await process_list(dict(arguments), context.call_id.value if context.call_id else "")
        return _tool_result(context.call_id, raw)

    async def invoke_ppt(arguments, context):  # type: ignore[no-untyped-def]
        raw = await asyncio.to_thread(
            _handle_ppt_create,
            dict(arguments),
            context.call_id.value if context.call_id else "",
        )
        return _tool_result(context.call_id, raw)

    tools = []
    for name, handler in (
        ("process_list", invoke_process),
        ("ppt_create", invoke_ppt),
    ):
        item = by_name[name]
        tools.append(
            FunctionTool(
                ToolSpec(
                    name,
                    str(item["schema"]["description"]),
                    schemas[name]["parameters"],
                ),
                handler,
            )
        )
    return ToolRegistry(tuple(tools))


class DesktopSdkRuntimeBridge:
    """Lazy SDK Runtime bound to the provider selected by the desktop app."""

    def __init__(
        self,
        *,
        user_data_root: Path,
        provider_registry: object,
        ready_publisher=None,
        http_client_factory=httpx.AsyncClient,
    ) -> None:
        self._user_data_root = Path(user_data_root).resolve()
        self._provider_registry = provider_registry
        self._ready_publisher = ready_publisher
        self._http_client_factory = http_client_factory
        self._stack: ProductSdkRuntimeStack | None = None
        self._context: SqliteContextPort | None = None
        self._start_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._stack is not None:
            self._stack.require_ready()
            return
        async with self._start_lock:
            if self._stack is not None:
                self._stack.require_ready()
                return
            chain = self._provider_registry.get_chain()
            provider_id = str(chain[0]["id"])
            client = self._http_client_factory()
            if not isinstance(client, httpx.AsyncClient):
                raise TypeError("http_client_factory must return httpx.AsyncClient")
            provider = ProductProviderAdapter(
                self._provider_registry,
                provider_id=provider_id,
                client=client,
                price_resolver=lambda _provider_id, _model: (0, 0, "desktop-sdk-test-v1"),
            )
            estimator = FrozenPriceEstimator(
                provider.price_snapshot.version,
                provider.target.pricing_key,
                provider.price_snapshot.input_micros_per_million,
                provider.price_snapshot.output_micros_per_million,
            )
            budget_policy = BudgetPolicy()
            driver = build_react_driver(
                limits=TerminationLimits(max_turns=12, max_tool_calls=12),
                budget_policy=budget_policy,
                estimator=estimator,
            )
            tools = _desktop_tools()
            authorization = _AllowSelectedTools()
            tool_reconciliation = _ToolReconciliation()

            def ports_factory(database, uow):  # type: ignore[no-untyped-def]
                context = SqliteContextPort(database)
                self._context = context
                effects = EffectExecutor(
                    uow=uow,
                    registry=tools,
                    authorization=authorization,
                    reconciliation=tool_reconciliation,
                )
                provider_port = ProviderInvocationCoordinator(
                    uow=uow,
                    provider=provider,
                    budget_policy=budget_policy,
                    estimator=estimator,
                )
                return RuntimePorts(
                    provider=provider_port,
                    tools=effects,
                    authorization=authorization,
                    context=context,
                    delivery=DeliveryDispatcher(uow, {"desktop": _DeliverySink()}),
                    tool_reconciliation=tool_reconciliation,
                    reconciliation=_RuntimeReconciliation(),
                    provider_reconciliation=_ProviderReconciliation(),
                    react_checkpoint=uow,
                    tool_catalog=_ToolCatalog(),
                    owner_id="deskpet-desktop-sdk-test",
                )

            stack = ProductSdkRuntimeStack(
                paths=ProductRuntimePathsAdapter(self._user_data_root),
                candidate_identity=build_candidate_identity(),
                dependency_loader=lambda: SdkRuntimeBuildInputs(
                    profiles={"agent.general": RuntimeProfile("agent.general", "react")},
                    drivers={"react": driver},
                    ports_factory=ports_factory,
                    workflow_catalog_digest="desktop-sdk-test-v1",
                    owned_resources=(OwnedResourceCloser("provider-http", client.aclose),),
                ),
                ready_publisher=self._ready_publisher,
            )
            try:
                await stack.start()
            except BaseException:
                self._context = None
                raise
            self._stack = stack

    async def run_chat(
        self,
        *,
        text: str,
        session_id: str,
        request_id: str,
        turn_id: str,
    ) -> DesktopSdkChatResult:
        await self.start()
        assert self._stack is not None and self._context is not None
        ready = self._stack.require_ready()
        run_id = RunId(self.run_id_for(session_id, request_id, turn_id))
        start = RunStart(
            ExecutionSessionId(session_id),
            run_id,
            RequestId(request_id),
            turn_id,
            {
                "messages": [{"role": "user", "content": text}],
                "capability_snapshot": {"tools": ["process_list", "ppt_create"]},
            },
            1,
        )
        await ready.client.start(start)
        await ready.runtime.wait_idle(run_id)
        record = ready.client.query(run_id)
        if record is None:
            raise RuntimeError("SDK Run disappeared")
        context = self._context.load(run_id)
        assistant = [
            message.content
            for message in context.messages
            if str(message.role.value) == "assistant"
        ]
        tool_names = tuple(
            str(message.name)
            for message in context.messages
            if str(message.role.value) == "tool" and message.name
        )
        if record.state is not RunState.COMPLETED:
            raise RuntimeError(f"SDK Run ended in {record.state.value}")
        if not assistant:
            raise RuntimeError("SDK Run completed without an assistant message")
        return DesktopSdkChatResult(
            run_id=run_id.value,
            state=record.state.value,
            text=assistant[-1],
            tool_names=tool_names,
        )

    @staticmethod
    def run_id_for(session_id: str, request_id: str, turn_id: str) -> str:
        identity = hashlib.sha256(
            f"{session_id}\0{request_id}\0{turn_id}".encode("utf-8")
        ).hexdigest()
        return f"desktop-sdk-{identity}"

    async def close(self) -> None:
        stack, self._stack = self._stack, None
        self._context = None
        if stack is not None:
            await stack.close()


__all__ = ("DesktopSdkChatResult", "DesktopSdkRuntimeBridge")

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass

import httpx
import pytest

from simple_harness import CallId, RequestId, RunId, fingerprint_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import (
    CancelToken,
    ProviderAuthenticationError,
    ProviderPaymentRequiredError,
    ProviderRateLimitError,
    ProviderRequest,
    ProviderServerError,
    ProviderTimeoutError,
    ProviderReconciliationState,
)
from simple_harness.tools import CancellationToken, ToolCall, ToolContext, ToolOutcome
from simple_harness.workflows.personal_v1 import PersonalWorkflowSelectionV1

from deskpet.product_state.database import ProductStateDatabase
from deskpet.sdk_adapters.capability_host import (
    ProductCapabilityHostAdapter,
    ProductCapabilityOperationReceipts,
)
from deskpet.sdk_adapters.context import ProductContextAdapter
from deskpet.sdk_adapters.personal_catalog import ProductPersonalCatalogAdapter
from deskpet.sdk_adapters.provider import ProductProviderAdapter
from deskpet.sdk_adapters.reconciliation import ProductProviderReconciliationAdapter
from deskpet.sdk_adapters.tools import (
    PRODUCT_TOOL_NAMES,
    ProductToolRegistration,
    build_product_tool_registry,
)


@dataclass
class Entry:
    id: str = "relay"
    base_url: str = "https://relay.invalid/v1"
    model: str = "model-a"
    models: tuple[str, ...] = ("model-a",)
    incarnation_id: str = "incarnation-1"
    config_revision: int = 3
    enabled: bool = True


class Registry:
    def __init__(self, secret: str) -> None:
        self.entry = Entry()
        self.secret = secret
        self.secret_reads = 0

    def get_entry(self, provider_id: str):
        return self.entry if provider_id == self.entry.id else None

    def resolve_api_key(self, provider_id: str):
        assert provider_id == self.entry.id
        self.secret_reads += 1
        return self.secret


def test_provider_freezes_target_price_and_redacts_secret() -> None:
    async def case() -> None:
        canary = "secret-provider-canary"
        registry = Registry(canary)

        async def respond(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == f"Bearer {canary}"
            return httpx.Response(
                200,
                json={
                    "id": "provider-request-1",
                    "model": "model-a",
                    "choices": [
                        {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = ProductProviderAdapter(
            registry,
            provider_id="relay",
            client=client,
            price_resolver=lambda provider, model: (2_000_000, 6_000_000, "price-v1"),
        )
        registry.entry.model = "mutated-after-freeze"
        assert adapter.target.model == "model-a"
        assert adapter.price_snapshot.input_micros_per_million == 2_000_000
        assert registry.secret_reads == 1
        response = await adapter.invoke(
            ProviderRequest(
                RequestId("request-1"),
                (Message(MessageRole.USER, "hello"),),
            ),
            cancel=CancelToken(),
        )
        assert response.message.content == "ok"
        public = json.dumps(adapter.public_snapshot(), sort_keys=True)
        assert canary not in public
        assert canary not in repr(adapter)
        await client.aclose()

    asyncio.run(case())


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, ProviderAuthenticationError),
        (402, ProviderPaymentRequiredError),
        (408, ProviderTimeoutError),
        (429, ProviderRateLimitError),
        (503, ProviderServerError),
    ],
)
def test_provider_error_does_not_leak_secret_or_body(status, error_type) -> None:
    async def case() -> None:
        canary = "secret-error-canary"

        async def respond(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(status, text=f"bad credential {canary}")

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = ProductProviderAdapter(
            Registry(canary),
            provider_id="relay",
            client=client,
            price_resolver=lambda provider, model: (1, 1, "price-v1"),
        )
        with pytest.raises(error_type) as caught:
            await adapter.invoke(
                ProviderRequest(
                    RequestId("request-1"),
                    (Message(MessageRole.USER, "hello"),),
                ),
                cancel=CancelToken(),
            )
        assert canary not in str(caught.value)
        assert canary not in repr(caught.value)
        await client.aclose()

    asyncio.run(case())


@pytest.mark.parametrize(
    "price",
    [(-1, 1, "price-v1"), (1, -1, "price-v1"), (1, 1, ""), None],
)
def test_provider_refuses_negative_or_unknown_pricing(price) -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: None))
    with pytest.raises((TypeError, ValueError), match="price"):
        ProductProviderAdapter(
            Registry("secret"),
            provider_id="relay",
            client=client,
            price_resolver=lambda _provider, _model: price,
        )
    asyncio.run(client.aclose())


def test_provider_reconciliation_defaults_to_unknown() -> None:
    class Invocation:
        invocation_id = "provider-invocation-1"

    observed = asyncio.run(ProductProviderReconciliationAdapter().observe(Invocation()))
    assert observed.state is ProviderReconciliationState.STILL_UNKNOWN


def test_tool_adapter_has_exact_explicit_inventory_and_six_dispatch_shapes() -> None:
    calls: list[str] = []

    def registration(name: str, dispatch_kind: str):
        async def handler(arguments, context):
            calls.append(dispatch_kind)
            return {"name": name, "run_id": context.run_id.value, "arguments": dict(arguments)}

        return ProductToolRegistration(
            name=name,
            description=f"{name} fixture",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=handler,
            dispatch_kind=dispatch_kind,
            permission_category="read_file",
            metadata={"source": "product", "version": "1"},
        )

    kinds = ("sync", "async", "context", "staged", "control", "provider")
    registrations = [
        registration(name, kinds[index % len(kinds)])
        for index, name in enumerate(PRODUCT_TOOL_NAMES)
    ]
    registry, inventory = build_product_tool_registry(registrations)
    assert tuple(item.name for item in inventory) == PRODUCT_TOOL_NAMES
    assert len(registry.specs) == 77
    context = ToolContext(
        RunId("run-1"), RequestId("request-1"), CancellationToken()
    )

    async def case() -> None:
        for index in range(6):
            name = PRODUCT_TOOL_NAMES[index]
            result = await registry.invoke(ToolCall(CallId(f"call-{index}"), name, {}), context)
            assert result.outcome is ToolOutcome.SUCCEEDED
        assert calls == list(kinds)

    asyncio.run(case())


def test_importing_sdk_tool_adapter_has_no_product_provider_side_effect() -> None:
    code = """
import json, sys
import deskpet.sdk_adapters.tools
blocked = sorted(name for name in sys.modules if name.startswith(
    ('llm.openai_adapter','deskpet.tools.research_tools','deskpet.workflows')
))
print(json.dumps(blocked))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(__file__.rsplit("/tests/", 1)[0]),
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == []


def test_context_personal_and_capability_ports_are_projection_only_and_idempotent(
    tmp_path,
) -> None:
    context = ProductContextAdapter()
    start = context.project_run_start(
        execution_session_id="session-1",
        run_id="run-1",
        request_id="request-1",
        turn_id="turn-1",
        messages=({"role": "user", "content": "hello"},),
        capability_snapshot={"tools": ["file_read"]},
        tool_catalog_generation=7,
    )
    assert start.input["messages"][0]["content"] == "hello"
    assert not hasattr(context, "append")

    personal = ProductPersonalCatalogAdapter()
    issued = PersonalWorkflowSelectionV1.issue(
        owner_key="owner-1",
        pack_id="pack-1",
        version="1",
        manifest_hash="a" * 64,
        binding_generation=1,
        graph={"nodes": [], "edges": []},
        graph_hash=fingerprint_json({"nodes": [], "edges": []}),
        query_hash="c" * 64,
        run_catalog_content_stamp="stamp-1",
        lease_entries=({"lease_id": "lease-1"},),
        effect_topology={},
        tool_bindings={},
    )
    selection = personal.bind_selection(issued.to_child_payload())
    assert selection.selection_id == issued.selection_id
    assert "matcher" not in vars(personal)
    forged_graph = dict(issued.to_child_payload())
    forged_graph["graph"] = {"nodes": [{"id": "forged"}], "edges": []}
    with pytest.raises(ValueError, match="graph hash"):
        personal.bind_selection(forged_graph)
    forged_selection = dict(issued.to_child_payload())
    forged_selection["selection_fingerprint"] = "d" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        personal.bind_selection(forged_selection)
    with pytest.raises(ValueError, match="override"):
        context.project_run_start(
            execution_session_id="session-1",
            run_id="run-2",
            request_id="request-2",
            turn_id="turn-2",
            messages=({"role": "user", "content": "hello"},),
            capability_snapshot={},
            tool_catalog_generation=7,
            trusted_input={"messages": []},
        )

    physical = 0

    async def search(**kwargs):
        nonlocal physical
        physical += 1
        return {"operation_key": kwargs["operation_key"], "matches": []}

    database = ProductStateDatabase(tmp_path / "capability-operations.db")
    database.initialize()
    capability = ProductCapabilityHostAdapter(
        search=search,
        receipts=ProductCapabilityOperationReceipts(database, clock=lambda: 10.0),
    )

    async def case() -> None:
        first = await capability.search(query="ocr", operation_key="op-1", admission={})
        second = await capability.search(query="ocr", operation_key="op-1", admission={})
        assert first == second
        assert physical == 1
        with pytest.raises(ValueError, match="operation_key"):
            await capability.search(query="different", operation_key="op-1", admission={})

    asyncio.run(case())
    database.close()
    reopened = ProductStateDatabase(tmp_path / "capability-operations.db")
    reopened.initialize()
    after_restart = ProductCapabilityHostAdapter(
        search=search,
        receipts=ProductCapabilityOperationReceipts(reopened, clock=lambda: 11.0),
    )
    asyncio.run(
        after_restart.search(query="ocr", operation_key="op-1", admission={})
    )
    assert physical == 1
    reopened.close()


def test_capability_handler_return_crash_reopens_without_second_physical_call(
    tmp_path,
) -> None:
    path = tmp_path / "capability-handler-crash.db"
    physical = 0

    async def search(**kwargs):
        nonlocal physical
        physical += 1
        return {"operation_key": kwargs["operation_key"], "matches": ["ocr"]}

    database = ProductStateDatabase(path)
    database.initialize()
    crashing = ProductCapabilityHostAdapter(
        search=search,
        receipts=ProductCapabilityOperationReceipts(database, clock=lambda: 10.0),
        fault=lambda point: (_ for _ in ()).throw(RuntimeError("crash"))
        if point == "handler_return"
        else None,
    )
    with pytest.raises(RuntimeError, match="crash"):
        asyncio.run(
            crashing.search(query="ocr", operation_key="op-crash", admission={})
        )
    assert physical == 1
    database.close()

    reopened = ProductStateDatabase(path)
    reopened.initialize()
    unresolved = ProductCapabilityHostAdapter(
        search=search,
        receipts=ProductCapabilityOperationReceipts(reopened, clock=lambda: 11.0),
    )
    with pytest.raises(RuntimeError, match="pending reconciliation"):
        asyncio.run(
            unresolved.search(query="ocr", operation_key="op-crash", admission={})
        )
    assert physical == 1

    reconciled = ProductCapabilityHostAdapter(
        search=search,
        receipts=ProductCapabilityOperationReceipts(reopened, clock=lambda: 12.0),
        reconcile=lambda **_kwargs: {
            "operation_key": "op-crash",
            "matches": ["ocr"],
        },
    )
    recovered = asyncio.run(
        reconciled.search(query="ocr", operation_key="op-crash", admission={})
    )
    assert recovered["matches"] == ["ocr"]
    assert physical == 1
    reopened.close()

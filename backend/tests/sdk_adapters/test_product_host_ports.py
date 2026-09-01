# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from simple_harness import CallId, RequestId, RunId, fingerprint_json
from simple_harness.contracts.messages import ContentBlock, Message, MessageRole
from simple_harness.execution.dispatch import (
    ProviderInvocationCoordinator,
    ProviderInvocationUnknownError,
)
from simple_harness.providers import (
    CancelToken,
    ProviderAuthenticationError,
    ProviderCancelledError,
    ProviderPaymentRequiredError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderReconciliationState,
    ProviderRequest,
    ProviderResponse,
    ProviderServerError,
    ProviderTimeoutError,
    ProviderToolCall,
    ProviderToolSpec,
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
from deskpet.sdk_adapters.provider import (
    ProductProviderAdapter,
    ProductProviderInvocationCoordinator,
    _ProductOpenAICompatibleProvider,
)
from deskpet.sdk_adapters.reconciliation import ProductProviderReconciliationAdapter
from deskpet.sdk_adapters.tools import (
    PRODUCT_TOOL_NAMES,
    ProductToolRegistration,
    active_product_tool_context,
    build_product_tool_registry,
)


def test_provider_restores_cooperative_task_cancellation() -> None:
    async def case() -> None:
        client = httpx.AsyncClient()
        adapter = ProductProviderAdapter(
            Registry("secret"),
            provider_id="relay",
            client=client,
            price_resolver=lambda provider, model: (1, 1, "price-v1"),
        )
        adapter._delegate.invoke = AsyncMock(
            side_effect=ProviderCancelledError()
        )
        try:
            with pytest.raises(asyncio.CancelledError):
                await adapter.invoke(
                    ProviderRequest(
                        RequestId("run-cancel:provider-turn:1"),
                        (Message(MessageRole.USER, "stop"),),
                    ),
                    cancel=CancelToken(),
                )
        finally:
            await client.aclose()

    asyncio.run(case())


def test_input_text_attachment_is_lowered_only_at_provider_wire_boundary() -> None:
    message = Message(
        MessageRole.USER,
        (
            ContentBlock.from_dict({"type": "text", "text": "read it"}),
            ContentBlock.from_dict({
                "type": "input_text",
                "name": "fixture.txt",
                "data": "FIRST LINE\nbody-canary",
            }),
        ),
    )

    payload = _ProductOpenAICompatibleProvider._message_payload(message)

    assert payload["content"] == [
        {"type": "text", "text": "read it"},
        {
            "type": "text",
            "text": 'Attached text file "fixture.txt":\nFIRST LINE\nbody-canary',
        },
    ]


@pytest.mark.asyncio
async def test_product_coordinator_terminalizes_user_cancelled_run(
    monkeypatch,
) -> None:
    base_invoke = AsyncMock(side_effect=ProviderInvocationUnknownError())
    monkeypatch.setattr(ProviderInvocationCoordinator, "invoke", base_invoke)
    coordinator = object.__new__(ProductProviderInvocationCoordinator)

    with pytest.raises(asyncio.CancelledError):
        await coordinator.invoke(
            SimpleNamespace(),
            SimpleNamespace(),
            cancel=SimpleNamespace(is_cancelled=True),
            execution_lease=SimpleNamespace(),
        )


@pytest.mark.asyncio
async def test_product_coordinator_preserves_non_cancel_unknown(monkeypatch) -> None:
    unknown = ProviderInvocationUnknownError()
    base_invoke = AsyncMock(side_effect=unknown)
    monkeypatch.setattr(ProviderInvocationCoordinator, "invoke", base_invoke)
    coordinator = object.__new__(ProductProviderInvocationCoordinator)

    with pytest.raises(ProviderInvocationUnknownError) as raised:
        await coordinator.invoke(
            SimpleNamespace(),
            SimpleNamespace(),
            cancel=SimpleNamespace(is_cancelled=False),
            execution_lease=SimpleNamespace(),
        )
    assert raised.value is unknown


@pytest.mark.asyncio
async def test_tool_turn_without_public_content_does_not_make_second_provider_call() -> None:
    client = httpx.AsyncClient()
    adapter = ProductProviderAdapter(
        Registry("secret"),
        provider_id="relay",
        client=client,
        price_resolver=lambda provider, model: (1, 1, "price-v1"),
    )
    response = ProviderResponse(
        RequestId("run-one:provider-turn:1"),
        Message(MessageRole.ASSISTANT, ""),
        tool_calls=(
            ProviderToolCall(CallId("call-one"), "memory_search", {"query": "x"}),
        ),
    )
    adapter._delegate.invoke = AsyncMock(return_value=response)
    try:
        actual = await adapter.invoke(
            ProviderRequest(RequestId("run-one:provider-turn:1"), (Message(MessageRole.USER, "x"),)),
            cancel=CancelToken(),
        )
    finally:
        await client.aclose()

    assert adapter._delegate.invoke.await_count == 1
    assert actual.message.content == ""


@pytest.mark.asyncio
async def test_nested_tool_arguments_survive_provider_history_normalization() -> None:
    client = httpx.AsyncClient()
    adapter = ProductProviderAdapter(
        Registry("secret"),
        provider_id="relay",
        client=client,
        price_resolver=lambda provider, model: (1, 1, "price-v1"),
    )
    response = ProviderResponse(
        RequestId("run-nested:provider-turn:1"),
        Message(MessageRole.ASSISTANT, ""),
        tool_calls=(
            ProviderToolCall(
                CallId("call-nested"),
                "skill_install",
                {
                    "source": {
                        "url": "https://github.com/example/skill",
                        "ref": None,
                    },
                    "skill_names": ["one", "two"],
                },
            ),
        ),
    )
    adapter._delegate.invoke = AsyncMock(return_value=response)
    try:
        actual = await adapter.invoke(
            ProviderRequest(
                RequestId("run-nested:provider-turn:1"),
                (Message(MessageRole.USER, "install"),),
            ),
            cancel=CancelToken(),
        )
    finally:
        await client.aclose()

    retained = actual.message.metadata["provider_tool_calls"][0]
    assert retained["arguments"]["source"]["ref"] is None
    assert tuple(retained["arguments"]["skill_names"]) == ("one", "two")
    assert actual.tool_calls[0].arguments["source"]["url"].endswith("/skill")


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_progress", [None, "", "   ", 7, {"text": "fake"}])
async def test_non_text_or_blank_public_progress_is_removed_without_narration(
    raw_progress,
) -> None:
    client = httpx.AsyncClient()
    adapter = ProductProviderAdapter(
        Registry("secret"),
        provider_id="relay",
        client=client,
        price_resolver=lambda provider, model: (1, 1, "price-v1"),
    )
    response = ProviderResponse(
        RequestId("run-progress:provider-turn:1"),
        Message(MessageRole.ASSISTANT, ""),
        tool_calls=(
            ProviderToolCall(
                CallId("call-progress"),
                "memory_search",
                {"query": "x", "deskpet_public_progress": raw_progress},
            ),
        ),
    )
    adapter._delegate.invoke = AsyncMock(return_value=response)
    try:
        actual = await adapter.invoke(
            ProviderRequest(
                RequestId("run-progress:provider-turn:1"),
                (Message(MessageRole.USER, "x"),),
            ),
            cancel=CancelToken(),
        )
    finally:
        await client.aclose()

    assert actual.message.content == ""
    assert actual.tool_calls[0].arguments == {"query": "x"}


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


def test_provider_round_trips_assistant_tool_calls_for_follow_up() -> None:
    async def case() -> None:
        requests: list[dict] = []

        async def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                return httpx.Response(
                    200,
                    json={
                        "id": "provider-request-tool",
                        "model": "model-a",
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "I will write the requested file.",
                                    "tool_calls": [
                                        {
                                            "id": "call-1",
                                            "type": "function",
                                            "function": {
                                                "name": "file_write",
                                                "arguments": '{"path":"ok.txt"}',
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": "provider-request-final",
                    "model": "model-a",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "done"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = ProductProviderAdapter(
            Registry("secret"),
            provider_id="relay",
            client=client,
            price_resolver=lambda provider, model: (1, 1, "price-v1"),
        )
        first = await adapter.invoke(
            ProviderRequest(RequestId("request-1"), (Message(MessageRole.USER, "write"),)),
            cancel=CancelToken(),
        )
        assert first.message.metadata["provider_tool_calls"][0]["id"] == "call-1"
        await adapter.invoke(
            ProviderRequest(
                RequestId("request-2"),
                (
                    Message(MessageRole.USER, "write"),
                    first.message,
                    Message(
                        MessageRole.TOOL,
                        '{"outcome":"succeeded"}',
                        name="file_write",
                        call_id=CallId("call-1"),
                    ),
                ),
            ),
            cancel=CancelToken(),
        )
        assistant = requests[1]["messages"][1]
        assert assistant["tool_calls"] == [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": '{"path":"ok.txt"}',
                },
            }
        ]
        assert requests[1]["messages"][2]["tool_call_id"] == "call-1"
        await client.aclose()

    asyncio.run(case())


def test_provider_captures_only_public_tool_turn_narration() -> None:
    from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

    async def case() -> None:
        async def respond(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                json={
                    "id": "provider-request-public-narration",
                    "model": "model-a",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "I will inspect the requested file.",
                                "reasoning_content": "private hidden chain of thought",
                                "tool_calls": [
                                    {
                                        "id": "call-public-narration",
                                        "type": "function",
                                        "function": {
                                            "name": "file_read",
                                            "arguments": '{"path":"README.md"}',
                                        },
                                    },
                                    {
                                        "id": "call-public-narration-2",
                                        "type": "function",
                                        "function": {
                                            "name": "file_read",
                                            "arguments": '{"path":"ARCHITECTURE/index.md"}',
                                        },
                                    },
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                },
            )

        delivery = type(
            "Delivery",
            (),
            {"capture_public_narration": AsyncMock()},
        )()
        _delivery_adapters["run-public-narration"] = delivery
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = ProductProviderAdapter(
            Registry("secret"),
            provider_id="relay",
            client=client,
            price_resolver=lambda provider, model: (1, 1, "price-v1"),
        )
        try:
            response = await adapter.invoke(
                ProviderRequest(
                    RequestId("run-public-narration:provider-turn:3"),
                    (Message(MessageRole.USER, "read"),),
                ),
                cancel=CancelToken(),
            )
            assert response.message.content == "I will inspect the requested file."
            assert "reasoning_content" not in response.message.metadata
            delivery.capture_public_narration.assert_awaited_once_with(
                "I will inspect the requested file.",
                iteration=2,
                call_ids=(
                    "call-public-narration",
                    "call-public-narration-2",
                ),
            )
        finally:
            _delivery_adapters.pop("run-public-narration", None)
            await client.aclose()

    asyncio.run(case())


def test_provider_extracts_model_authored_virtual_public_progress() -> None:
    from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

    async def case() -> None:
        payloads: list[dict] = []

        async def respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            payloads.append(payload)
            assert payload["thinking"] == {"type": "enabled"}
            assert payload["reasoning_effort"] == "high"
            assert [item["function"]["name"] for item in payload["tools"]] == [
                "file_read",
            ]
            schema = payload["tools"][0]["function"]["parameters"]
            assert "deskpet_public_progress" in schema["properties"]
            assert "deskpet_public_progress" not in schema.get("required", [])
            if len(payloads) == 2:
                assistant = payload["messages"][1]
                assert assistant["reasoning_content"] == (
                    "PRIVATE-COT-MUST-NOT-APPEAR"
                )
                assert [
                    item["function"]["name"] for item in assistant["tool_calls"]
                ] == ["file_read"]
                return httpx.Response(
                    200,
                    json={
                        "id": "provider-request-model-progress-final",
                        "model": "model-a",
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "检查完成。",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": "provider-request-model-progress",
                    "model": "model-a",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "reasoning_content": "PRIVATE-COT-MUST-NOT-APPEAR",
                                "tool_calls": [
                                    {
                                        "id": "call-read",
                                        "type": "function",
                                        "function": {
                                            "name": "file_read",
                                            "arguments": json.dumps(
                                                {
                                                    "path": "tests/test_config.py",
                                                    "deskpet_public_progress": (
                                                        "指定路径刚才未命中，我先定位实际文件，"
                                                        "再读取目标内容。"
                                                    ),
                                                },
                                                ensure_ascii=False,
                                            ),
                                        },
                                    },
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                },
            )

        delivery = type(
            "Delivery",
            (),
            {"capture_public_narration": AsyncMock()},
        )()
        _delivery_adapters["run-model-progress"] = delivery
        registry = Registry("secret")
        registry.entry.base_url = "https://api.deepseek.com"
        registry.entry.model = "deepseek-v4-pro"
        registry.entry.models = ("deepseek-v4-pro",)
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = ProductProviderAdapter(
            registry,
            provider_id="relay",
            client=client,
            price_resolver=lambda provider, model: (1, 1, "price-v1"),
            model_params={"reasoning_mode": "thinking"},
        )
        try:
            response = await adapter.invoke(
                ProviderRequest(
                    RequestId("run-model-progress:provider-turn:2"),
                    (Message(MessageRole.USER, "read"),),
                    tools=(
                        ProviderToolSpec(
                            "file_read",
                            "Read a file.",
                            {
                                "type": "object",
                                "properties": {
                                    "deskpet_public_progress": {"type": "string"}
                                },
                            },
                        ),
                    ),
                ),
                cancel=CancelToken(),
            )
            expected = "指定路径刚才未命中，我先定位实际文件，再读取目标内容。"
            assert response.message.content == expected
            assert [call.name for call in response.tool_calls] == ["file_read"]
            assert "deskpet_public_progress" not in repr(response.tool_calls)
            assert "PRIVATE-COT-MUST-NOT-APPEAR" not in response.message.content
            delivery.capture_public_narration.assert_awaited_once_with(
                expected,
                iteration=1,
                call_ids=("call-read",),
            )
            final = await adapter.invoke(
                ProviderRequest(
                    RequestId("run-model-progress:provider-turn:3"),
                    (
                        Message(MessageRole.USER, "read"),
                        response.message,
                        Message(
                            MessageRole.TOOL,
                            '{"ok":true}',
                            name="file_read",
                            call_id=CallId("call-read"),
                        ),
                    ),
                    tools=(
                        ProviderToolSpec(
                                "file_read",
                                "Read a file.",
                                {
                                    "type": "object",
                                    "properties": {
                                        "deskpet_public_progress": {"type": "string"}
                                    },
                                },
                        ),
                    ),
                ),
                cancel=CancelToken(),
            )
            assert final.message.content == "检查完成。"
            assert delivery.capture_public_narration.await_count == 1
        finally:
            _delivery_adapters.pop("run-model-progress", None)
            await client.aclose()

    asyncio.run(case())


def test_provider_does_not_invent_summary_when_tool_content_and_progress_are_empty() -> None:
    from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

    async def case() -> None:
        payloads: list[dict] = []

        async def respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            payloads.append(payload)
            if len(payloads) == 1:
                schema = payload["tools"][0]["function"]["parameters"]
                assert "deskpet_public_progress" in schema["properties"]
                assert "deskpet_public_progress" not in schema.get("required", [])
                return httpx.Response(
                    200,
                    json={
                        "id": "provider-main-empty-public",
                        "model": "model-a",
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "reasoning_content": "PRIVATE-REAL-REASONING",
                                    "tool_calls": [
                                        {
                                            "id": "call-read-summary",
                                            "type": "function",
                                            "function": {
                                                "name": "file_read",
                                                "arguments": '{"path":"tests/test_config.py"}',
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                    },
                )
            assistant = payload["messages"][1]
            assert assistant["reasoning_content"] == "PRIVATE-REAL-REASONING"
            return httpx.Response(
                200,
                json={
                    "id": "provider-main-final",
                    "model": "model-a",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "完成。",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

        delivery = type(
            "Delivery",
            (),
            {"capture_public_narration": AsyncMock()},
        )()
        _delivery_adapters["run-model-summary"] = delivery
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = ProductProviderAdapter(
            Registry("secret"),
            provider_id="relay",
            client=client,
            price_resolver=lambda provider, model: (1, 1, "price-v1"),
        )
        tool = ProviderToolSpec(
            "file_read",
            "Read a file.",
            {
                "type": "object",
                "properties": {
                    "deskpet_public_progress": {"type": "string"}
                },
            },
        )
        try:
            first = await adapter.invoke(
                ProviderRequest(
                    RequestId("run-model-summary:provider-turn:1"),
                    (Message(MessageRole.USER, "read"),),
                    tools=(tool,),
                ),
                cancel=CancelToken(),
            )
            assert first.message.content == ""
            assert "PRIVATE-REAL-REASONING" not in first.message.content
            delivery.capture_public_narration.assert_awaited_once_with(
                "",
                iteration=0,
                call_ids=("call-read-summary",),
            )
            final = await adapter.invoke(
                ProviderRequest(
                    RequestId("run-model-summary:provider-turn:2"),
                    (
                        Message(MessageRole.USER, "read"),
                        first.message,
                        Message(
                            MessageRole.TOOL,
                            '{"ok":true}',
                            name="file_read",
                            call_id=CallId("call-read-summary"),
                        ),
                    ),
                    tools=(tool,),
                ),
                cancel=CancelToken(),
            )
            assert final.message.content == "完成。"
            assert len(payloads) == 2
        finally:
            _delivery_adapters.pop("run-model-summary", None)
            await client.aclose()

    asyncio.run(case())


def test_provider_does_not_make_summary_call_without_private_reasoning() -> None:
    from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

    async def case() -> None:
        payloads: list[dict] = []

        async def respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            payloads.append(payload)
            if len(payloads) == 1:
                return httpx.Response(
                    200,
                    json={
                        "id": "provider-main-no-reasoning",
                        "model": "model-a",
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "call-runtime-refresh",
                                            "type": "function",
                                            "function": {
                                                "name": "file_read",
                                                "arguments": (
                                                    '{"path":"tests/'
                                                    'test_provider_runtime_refresh.py"}'
                                                ),
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                    },
                )
            raise AssertionError("unexpected second Provider request")

        delivery = type(
            "Delivery",
            (),
            {"capture_public_narration": AsyncMock()},
        )()
        _delivery_adapters["run-summary-no-reasoning"] = delivery
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = ProductProviderAdapter(
            Registry("secret"),
            provider_id="relay",
            client=client,
            price_resolver=lambda provider, model: (1, 1, "price-v1"),
        )
        try:
            response = await adapter.invoke(
                ProviderRequest(
                    RequestId("run-summary-no-reasoning:provider-turn:1"),
                    (
                        Message(
                            MessageRole.USER,
                            "读取 tests/test_provider_runtime_refresh.py 前 6 行。",
                        ),
                    ),
                    tools=(
                        ProviderToolSpec(
                            "file_read",
                            "Read a file.",
                            {"type": "object", "properties": {}},
                        ),
                    ),
                ),
                cancel=CancelToken(),
            )
            assert response.message.content == ""
            assert response.tool_calls[0].arguments == {
                "path": "tests/test_provider_runtime_refresh.py"
            }
            delivery.capture_public_narration.assert_awaited_once_with(
                "",
                iteration=0,
                call_ids=("call-runtime-refresh",),
            )
            assert len(payloads) == 1
        finally:
            _delivery_adapters.pop("run-summary-no-reasoning", None)
            await client.aclose()

    asyncio.run(case())


def test_provider_public_narration_projection_is_best_effort() -> None:
    from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

    async def case() -> None:
        async def respond(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                json={
                    "id": "provider-request-projection-failure",
                    "model": "model-a",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Public work update.",
                                "tool_calls": [
                                    {
                                        "id": "call-projection-failure",
                                        "type": "function",
                                        "function": {
                                            "name": "file_read",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                },
            )

        delivery = type(
            "Delivery",
            (),
            {
                "capture_public_narration": AsyncMock(
                    side_effect=RuntimeError("projection unavailable")
                )
            },
        )()
        _delivery_adapters["run-best-effort"] = delivery
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = ProductProviderAdapter(
            Registry("secret"),
            provider_id="relay",
            client=client,
            price_resolver=lambda provider, model: (1, 1, "price-v1"),
        )
        try:
            response = await adapter.invoke(
                ProviderRequest(
                    RequestId("run-best-effort:provider-turn:1"),
                    (Message(MessageRole.USER, "read"),),
                ),
                cancel=CancelToken(),
            )
            assert response.tool_calls[0].name == "file_read"
        finally:
            _delivery_adapters.pop("run-best-effort", None)
            await client.aclose()

    asyncio.run(case())


def test_provider_binds_all_empty_content_calls_for_delivery_fallback() -> None:
    from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

    async def case() -> None:
        async def respond(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                json={
                    "id": "provider-request-empty-narration",
                    "model": "model-a",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "reasoning_content": "PRIVATE-COT-MUST-NOT-APPEAR",
                                "tool_calls": [
                                    {
                                        "id": call_id,
                                        "type": "function",
                                        "function": {
                                            "name": "file_read",
                                            "arguments": "{}",
                                        },
                                    }
                                    for call_id in ("call-empty-a", "call-empty-b")
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                },
            )

        delivery = type(
            "Delivery",
            (),
            {"capture_public_narration": AsyncMock()},
        )()
        _delivery_adapters["run-empty-narration"] = delivery
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = ProductProviderAdapter(
            Registry("secret"),
            provider_id="relay",
            client=client,
            price_resolver=lambda provider, model: (1, 1, "price-v1"),
        )
        try:
            response = await adapter.invoke(
                ProviderRequest(
                    RequestId("provider-turn:4"),
                    (Message(MessageRole.USER, "read"),),
                ),
                cancel=CancelToken(),
            )
            assert len(response.tool_calls) == 2
            delivery.capture_public_narration.assert_awaited_once_with(
                "",
                iteration=3,
                call_ids=("call-empty-a", "call-empty-b"),
            )
        finally:
            _delivery_adapters.pop("run-empty-narration", None)
            await client.aclose()

    asyncio.run(case())


def test_provider_contract_violation_is_definite_protocol_failure() -> None:
    async def case() -> None:
        async def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "provider-request-invalid-call-id",
                    "model": "model-a",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "调用-1",
                                        "type": "function",
                                        "function": {
                                            "name": "file_read",
                                            "arguments": '{"path":"README.md"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = ProductProviderAdapter(
            Registry("secret"),
            provider_id="relay",
            client=client,
            price_resolver=lambda provider, model: (1, 1, "price-v1"),
        )
        try:
            with pytest.raises(ProviderProtocolError):
                await adapter.invoke(
                    ProviderRequest(
                        RequestId("request-invalid-call-id"),
                        (Message(MessageRole.USER, "read"),),
                    ),
                    cancel=CancelToken(),
                )
        finally:
            await client.aclose()

    asyncio.run(case())


@pytest.mark.asyncio
async def test_provider_diagnostics_correlate_success_without_payload_leak(
    caplog,
) -> None:
    secret = "secret-diagnostic-canary"
    prompt_canary = "prompt-diagnostic-canary"
    response_canary = "response-diagnostic-canary"
    argument_canary = "argument-diagnostic-canary"

    async def respond(request: httpx.Request) -> httpx.Response:
        assert prompt_canary in request.content.decode()
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json; charset=utf-8",
                "x-request-id": "upstream-diagnostic-canary",
            },
            json={
                "id": "provider-body-id-canary",
                "model": "model-a",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": response_canary,
                            "tool_calls": [
                                {
                                    "id": "call-diagnostic",
                                    "type": "function",
                                    "function": {
                                        "name": "file_read",
                                        "arguments": json.dumps({"path": argument_canary}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 4,
                    "total_tokens": 12,
                },
            },
        )

    caplog.set_level("INFO", logger="deskpet.sdk_adapters.provider")
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    adapter = ProductProviderAdapter(
        Registry(secret),
        provider_id="relay",
        client=client,
        price_resolver=lambda provider, model: (1, 1, "price-v1"),
    )
    try:
        response = await adapter.invoke(
            ProviderRequest(
                RequestId("run-diagnostic:provider-turn:3"),
                (Message(MessageRole.USER, prompt_canary),),
                tools=(
                    ProviderToolSpec(
                        "file_read",
                        "Read a file without exposing arguments.",
                        {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                        },
                    ),
                ),
            ),
            cancel=CancelToken(),
        )
    finally:
        await client.aclose()

    assert response.message.content == response_canary
    log_text = caplog.text
    assert "product_provider_attempt_started" in log_text
    assert "product_provider_http_response_received" in log_text
    assert "status_code=200" in log_text
    assert "choice_count=1" in log_text
    assert "tool_call_count=1" in log_text
    assert "product_provider_attempt_succeeded" in log_text
    assert "finish_reason=tool_calls" in log_text
    assert "usage_present=True" in log_text
    for private_value in (
        secret,
        prompt_canary,
        response_canary,
        argument_canary,
        "upstream-diagnostic-canary",
        "provider-body-id-canary",
    ):
        assert private_value not in log_text


@pytest.mark.asyncio
async def test_provider_diagnostics_classify_transport_timeout_without_leak(
    caplog,
) -> None:
    secret = "secret-timeout-canary"

    async def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("body-timeout-canary", request=request)

    caplog.set_level("INFO", logger="deskpet.sdk_adapters.provider")
    client = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
    adapter = ProductProviderAdapter(
        Registry(secret),
        provider_id="relay",
        client=client,
        price_resolver=lambda provider, model: (1, 1, "price-v1"),
    )
    try:
        with pytest.raises(ProviderTimeoutError):
            await adapter.invoke(
                ProviderRequest(
                    RequestId("run-timeout:provider-turn:4"),
                    (Message(MessageRole.USER, "prompt-timeout-canary"),),
                ),
                cancel=CancelToken(),
            )
    finally:
        await client.aclose()

    log_text = caplog.text
    assert "product_provider_attempt_failed" in log_text
    assert "stage=transport_timeout" in log_text
    assert "error_code=provider_timeout" in log_text
    assert "status_code=missing" in log_text
    assert secret not in log_text
    assert "body-timeout-canary" not in log_text
    assert "prompt-timeout-canary" not in log_text


@pytest.mark.asyncio
async def test_provider_diagnostics_classify_response_shape_failure(caplog) -> None:
    async def malformed(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"choices": [], "private": "protocol-body-canary"},
        )

    caplog.set_level("INFO", logger="deskpet.sdk_adapters.provider")
    client = httpx.AsyncClient(transport=httpx.MockTransport(malformed))
    adapter = ProductProviderAdapter(
        Registry("secret-protocol-canary"),
        provider_id="relay",
        client=client,
        price_resolver=lambda provider, model: (1, 1, "price-v1"),
    )
    try:
        with pytest.raises(ProviderProtocolError):
            await adapter.invoke(
                ProviderRequest(
                    RequestId("run-protocol:provider-turn:5"),
                    (Message(MessageRole.USER, "protocol-prompt-canary"),),
                ),
                cancel=CancelToken(),
            )
    finally:
        await client.aclose()

    log_text = caplog.text
    assert "product_provider_http_response_received" in log_text
    assert "status_code=200" in log_text
    assert "choice_count=0" in log_text
    assert "product_provider_response_parse_failed" in log_text
    assert "stage=response_protocol" in log_text
    assert "error_code=provider_protocol_error" in log_text
    assert "protocol-body-canary" not in log_text
    assert "protocol-prompt-canary" not in log_text
    assert "secret-protocol-canary" not in log_text


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
    assert len(registry.specs) == len(PRODUCT_TOOL_NAMES)
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


def test_tool_adapter_contextvar_is_concurrent_and_fail_closed() -> None:
    observed: dict[str, list[str]] = {}
    entered = 0
    both_entered = asyncio.Event()
    release = asyncio.Event()

    def registration(name: str):
        async def handler(_arguments, _context):
            nonlocal entered
            run_id = active_product_tool_context().run_id.value
            observed.setdefault(run_id, []).append(
                active_product_tool_context().request_id.value
            )
            entered += 1
            if entered == 2:
                both_entered.set()
            await release.wait()
            observed[run_id].append(active_product_tool_context().run_id.value)
            return {"ok": True}

        return ProductToolRegistration(
            name=name,
            description=f"{name} fixture",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
            dispatch_kind="async",
            permission_category="read_file",
            metadata={"source": "product", "version": "1"},
        )

    registry, _ = build_product_tool_registry(
        [registration(name) for name in PRODUCT_TOOL_NAMES]
    )

    async def case() -> None:
        tasks = [
            asyncio.create_task(
                registry.invoke(
                    ToolCall(CallId(f"call-{suffix}"), tool, {}),
                    ToolContext(
                        RunId(f"run-{suffix}"),
                        RequestId(f"request-{suffix}"),
                        CancellationToken(),
                    ),
                )
            )
            for suffix, tool in (("a", "file_read"), ("b", "file_write"))
        ]
        await asyncio.wait_for(both_entered.wait(), timeout=1)
        release.set()
        await asyncio.gather(*tasks)

    with pytest.raises(RuntimeError, match="outside SDK ToolRegistry"):
        active_product_tool_context()
    asyncio.run(case())
    assert observed == {
        "run-a": ["request-a", "run-a"],
        "run-b": ["request-b", "run-b"],
    }
    with pytest.raises(RuntimeError, match="outside SDK ToolRegistry"):
        active_product_tool_context()


def test_tool_adapter_projects_through_registered_run_delivery() -> None:
    from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

    def registration(name: str):
        async def handler(arguments, context):
            return {"name": name}

        return ProductToolRegistration(
            name=name,
            description=f"{name} fixture",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=handler,
            dispatch_kind="async",
            permission_category="read_file",
            metadata={"source": "product", "version": "1"},
        )

    registry, _ = build_product_tool_registry(
        [registration(name) for name in PRODUCT_TOOL_NAMES]
    )
    delivery = type(
        "Delivery",
        (),
        {
            "present_tool_call": AsyncMock(),
            "present_tool_result": AsyncMock(),
        },
    )()
    _delivery_adapters["run-visible"] = delivery

    async def case() -> None:
        result = await registry.invoke(
            ToolCall(CallId("call-visible"), "file_read", {}),
            ToolContext(
                RunId("run-visible"),
                RequestId("request-visible"),
                CancellationToken(),
            ),
        )
        assert result.outcome is ToolOutcome.SUCCEEDED

    try:
        asyncio.run(case())
        delivery.present_tool_call.assert_awaited_once()
        delivery.present_tool_result.assert_awaited_once()
    finally:
        _delivery_adapters.pop("run-visible", None)


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

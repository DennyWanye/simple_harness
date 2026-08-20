from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from unittest.mock import AsyncMock

import httpx
import pytest

from simple_harness import CallId, RequestId, RunId, fingerprint_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import (
    CancelToken,
    ProviderAuthenticationError,
    ProviderPaymentRequiredError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderRequest,
    ProviderServerError,
    ProviderTimeoutError,
    ProviderReconciliationState,
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
            assert "deskpet_public_progress" in schema["required"]
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
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = ProductProviderAdapter(
            registry,
            provider_id="relay",
            client=client,
            price_resolver=lambda provider, model: (1, 1, "price-v1"),
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
                            {"type": "object"},
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
                            {"type": "object"},
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


def test_provider_uses_model_summary_when_tool_content_and_progress_are_empty() -> None:
    from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

    async def case() -> None:
        payloads: list[dict] = []

        async def respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            payloads.append(payload)
            if len(payloads) == 1:
                schema = payload["tools"][0]["function"]["parameters"]
                assert "deskpet_public_progress" in schema["required"]
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
            if len(payloads) == 2:
                assert "tools" not in payload
                assert payload["max_tokens"] == 180
                material = json.loads(payload["messages"][1]["content"])
                assert material == {
                    "current_user_task": "read",
                    "private_reasoning": "PRIVATE-REAL-REASONING",
                    "selected_tools": ["file_read"],
                }
                return httpx.Response(
                    200,
                    json={
                        "id": "provider-public-summary",
                        "model": "model-a",
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": (
                                        "刚才的相对路径没有命中，我正在确认实际文件位置，"
                                        "随后会读取目标内容。"
                                    ),
                                },
                                "finish_reason": "stop",
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
            {"type": "object", "properties": {}},
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
            expected = (
                "刚才的相对路径没有命中，我正在确认实际文件位置，"
                "随后会读取目标内容。"
            )
            assert first.message.content == expected
            assert "PRIVATE-REAL-REASONING" not in first.message.content
            delivery.capture_public_narration.assert_awaited_once_with(
                expected,
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
            assert len(payloads) == 3
        finally:
            _delivery_adapters.pop("run-model-summary", None)
            await client.aclose()

    asyncio.run(case())


def test_provider_uses_model_summary_without_private_reasoning() -> None:
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
            assert "tools" not in payload
            material = json.loads(payload["messages"][1]["content"])
            assert material == {
                "current_user_task": (
                    "读取 tests/test_provider_runtime_refresh.py 前 6 行。"
                ),
                "selected_tools": ["file_read"],
            }
            assert "test_provider_runtime_refresh.py" not in json.dumps(
                payloads[0]["tools"], ensure_ascii=False
            )
            return httpx.Response(
                200,
                json={
                    "id": "provider-public-summary-no-reasoning",
                    "model": "model-a",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": (
                                    "我正在读取 provider runtime refresh 的测试文件，"
                                    "以核对它覆盖的运行时刷新行为。"
                                ),
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
            expected = (
                "我正在读取 provider runtime refresh 的测试文件，"
                "以核对它覆盖的运行时刷新行为。"
            )
            assert response.message.content == expected
            assert response.tool_calls[0].arguments == {
                "path": "tests/test_provider_runtime_refresh.py"
            }
            delivery.capture_public_narration.assert_awaited_once_with(
                expected,
                iteration=0,
                call_ids=("call-runtime-refresh",),
            )
            assert len(payloads) == 2
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

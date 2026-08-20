"""Public SDK Provider bridge over the product registry and keychain."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

import httpx

from simple_harness.contracts import ContractValidationError
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.contracts.json import thaw_json
from simple_harness.execution.dispatch import (
    ProviderInvocationCoordinator,
    ProviderInvocationUnknownError,
)
from simple_harness.providers import (
    CancelToken,
    OpenAICompatibleProvider,
    ProviderAuthenticationError,
    ProviderCancelledError,
    ProviderProtocolError,
    ProviderRequest,
    ProviderResponse,
    ProviderTarget,
    ProviderToolCall,
    Secret,
)


_PROVIDER_TOOL_CALLS_METADATA_KEY = "provider_tool_calls"
_PROVIDER_REASONING_CONTENT_METADATA_KEY = "provider_reasoning_content"
_PUBLIC_PROGRESS_ARGUMENT = "deskpet_public_progress"
logger = logging.getLogger(__name__)


class _ProductOpenAICompatibleProvider(OpenAICompatibleProvider):
    """Restore OpenAI tool-call history retained in SDK message metadata."""

    def __init__(
        self,
        *args: Any,
        reasoning_wire: Mapping[str, object] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._reasoning_wire = dict(reasoning_wire or {})

    def _request_payload(self, request: ProviderRequest) -> dict[str, Any]:
        payload = super()._request_payload(request)
        payload.update(self._reasoning_wire)
        return payload

    @staticmethod
    def _message_payload(message: Message) -> dict[str, Any]:
        payload = OpenAICompatibleProvider._message_payload(message)
        raw_reasoning = message.metadata.get(
            _PROVIDER_REASONING_CONTENT_METADATA_KEY
        )
        if (
            message.role is MessageRole.ASSISTANT
            and isinstance(raw_reasoning, str)
            and raw_reasoning
        ):
            # DeepSeek thinking-mode Tool calls require this private field to
            # be echoed verbatim on subsequent Provider requests. It remains
            # SDK-private metadata and is never used for public projection.
            payload["reasoning_content"] = raw_reasoning
        raw_calls = message.metadata.get(_PROVIDER_TOOL_CALLS_METADATA_KEY)
        if message.role is not MessageRole.ASSISTANT or not isinstance(
            raw_calls, Sequence
        ) or isinstance(raw_calls, (str, bytes, bytearray)):
            return payload

        calls: list[dict[str, Any]] = []
        for raw_call in raw_calls:
            if not isinstance(raw_call, Mapping):
                continue
            call_id = raw_call.get("id")
            name = raw_call.get("name")
            arguments = raw_call.get("arguments")
            if not isinstance(call_id, str) or not isinstance(name, str):
                continue
            calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(
                            thaw_json(arguments) if isinstance(arguments, Mapping) else {},
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                }
            )
        if calls:
            payload["tool_calls"] = calls
        return payload

    def _parse_response(
        self,
        request: ProviderRequest,
        payload: Any,
        response: httpx.Response,
    ) -> ProviderResponse:
        parsed = super()._parse_response(request, payload, response)
        if not isinstance(payload, Mapping):
            return parsed
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return parsed
        choice = choices[0]
        raw_message = choice.get("message") if isinstance(choice, Mapping) else None
        raw_reasoning = (
            raw_message.get("reasoning_content")
            if isinstance(raw_message, Mapping)
            else None
        )
        if not isinstance(raw_reasoning, str) or not raw_reasoning:
            return parsed
        metadata = dict(parsed.message.metadata)
        metadata[_PROVIDER_REASONING_CONTENT_METADATA_KEY] = raw_reasoning
        return replace(
            parsed,
            message=Message(
                parsed.message.role,
                parsed.message.content,
                name=parsed.message.name,
                metadata=metadata,
            ),
        )


def _retain_tool_calls_in_message(response: ProviderResponse) -> ProviderResponse:
    if not response.tool_calls:
        return response
    metadata = dict(response.message.metadata)
    metadata[_PROVIDER_TOOL_CALLS_METADATA_KEY] = [
        {
            "id": call.call_id.value,
            "name": call.name,
            "arguments": dict(call.arguments),
        }
        for call in response.tool_calls
    ]
    return replace(
        response,
        message=Message(
            response.message.role,
            response.message.content,
            name=response.message.name,
            metadata=metadata,
        ),
    )


def _public_progress_text(value: object, *, maximum: int = 600) -> str:
    text = re.sub(
        r"<think\b[^>]*>.*?</think\s*>",
        "",
        str(value or ""),
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"</?think\b[^>]*>", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()[:maximum].rstrip()


def _extract_public_progress(response: ProviderResponse) -> ProviderResponse:
    """Extract and remove model-authored narration from real Tool arguments."""

    if not response.tool_calls:
        return response
    narration = ""
    cleaned_calls: list[ProviderToolCall] = []
    for call in response.tool_calls:
        arguments = dict(call.arguments)
        raw_progress = arguments.pop(_PUBLIC_PROGRESS_ARGUMENT, "")
        candidate = (
            _public_progress_text(raw_progress)
            if isinstance(raw_progress, str)
            else ""
        )
        if candidate and not narration:
            narration = candidate
        cleaned_calls.append(
            ProviderToolCall(call.call_id, call.name, arguments)
        )
    if not narration:
        return replace(response, tool_calls=tuple(cleaned_calls))
    content = response.message.content.strip() or narration
    return replace(
        response,
        message=Message(
            response.message.role,
            content,
            name=response.message.name,
            metadata=dict(response.message.metadata),
        ),
        tool_calls=tuple(cleaned_calls),
    )


class ProductProviderRegistry(Protocol):
    def get_entry(self, provider_id: str) -> Any | None: ...

    def resolve_api_key(self, provider_id: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ProductPriceSnapshot:
    provider_id: str
    model: str
    input_micros_per_million: int
    output_micros_per_million: int
    version: str

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "input": self.input_micros_per_million,
                    "model": self.model,
                    "output": self.output_micros_per_million,
                    "provider_id": self.provider_id,
                    "version": self.version,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


class ProductProviderAdapter:
    """One immutable provider/model/config/price snapshot for an SDK Run."""

    def __init__(
        self,
        registry: ProductProviderRegistry,
        *,
        provider_id: str,
        client: httpx.AsyncClient,
        price_resolver,
        model: str | None = None,
        model_params: Mapping[str, object] | None = None,
        timeout: float = 60.0,
    ) -> None:
        entry = registry.get_entry(provider_id)
        if entry is None or not bool(getattr(entry, "enabled", True)):
            raise ValueError("selected provider is unavailable")
        frozen_model = str(model or getattr(entry, "model", "")).strip()
        models = tuple(str(item) for item in (getattr(entry, "models", ()) or ()))
        if not frozen_model or (models and frozen_model not in models):
            raise ValueError("selected model is unavailable")
        base_url = str(getattr(entry, "base_url", "")).strip().rstrip("/")
        if not base_url:
            raise ValueError("selected provider base_url is missing")
        secret_value = registry.resolve_api_key(provider_id)
        if not isinstance(secret_value, str) or not secret_value.strip():
            raise ProviderAuthenticationError(
                public_message="Provider login or API key is required."
            )
        price = price_resolver(provider_id, frozen_model)
        if not isinstance(price, tuple) or len(price) != 3:
            raise TypeError("price_resolver must return input, output, version")
        if (
            isinstance(price[0], bool)
            or isinstance(price[1], bool)
            or not isinstance(price[0], int)
            or not isinstance(price[1], int)
            or price[0] < 0
            or price[1] < 0
            or (price[0] == 0 and price[1] == 0)
            or not isinstance(price[2], str)
            or not price[2].strip()
        ):
            raise ValueError("provider price snapshot is unknown, zero, or invalid")
        self.price_snapshot = ProductPriceSnapshot(
            provider_id,
            frozen_model,
            int(price[0]),
            int(price[1]),
            str(price[2]),
        )
        endpoint_payload = {
            "base_url": base_url,
            "config_revision": int(getattr(entry, "config_revision", 0) or 0),
            "incarnation_id": str(getattr(entry, "incarnation_id", "")),
        }
        endpoint_identity = hashlib.sha256(
            json.dumps(endpoint_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        pricing_key = (
            f"{provider_id}:{frozen_model}:{self.price_snapshot.fingerprint}"
        )
        from llm.provider_capabilities import (
            declared_reasoning_capability,
            reasoning_wire_fields,
        )

        self.reasoning_capability = declared_reasoning_capability(frozen_model)
        self.reasoning_wire = reasoning_wire_fields(
            self.reasoning_capability, model_params
        )
        self._delegate = _ProductOpenAICompatibleProvider(
            client,
            base_url,
            frozen_model,
            Secret(secret_value),
            timeout,
            provider_id=provider_id,
            pricing_key=pricing_key,
            reasoning_wire=self.reasoning_wire,
        )
        self._target = ProviderTarget(
            provider_id,
            frozen_model,
            pricing_key,
            endpoint_identity,
            "openai-compatible:v1",
        )

    @property
    def target(self) -> ProviderTarget:
        return self._target

    async def invoke(
        self, request: ProviderRequest, *, cancel: CancelToken
    ) -> ProviderResponse:
        try:
            response = await self._delegate.invoke(
                request,
                cancel=cancel,
            )
        except ProviderCancelledError:
            # OpenAICompatibleProvider converts task cancellation into a
            # Provider error. At the product boundary restore cooperative
            # asyncio cancellation so the SDK driver cannot reconcile a user
            # stop as an unknown physical handoff and move the durable Run
            # back to waiting.
            raise asyncio.CancelledError() from None
        except ContractValidationError as exc:
            # OpenAI-compatible payload parsing constructs typed SDK values
            # (CallId, Message metadata, frozen JSON).  A provider-generated
            # value that violates one of those contracts is a definite invalid
            # response, not an unknown physical handoff.  Normalize it to the
            # provider protocol taxonomy so the coordinator settles the Run as
            # failed instead of leaving it in sdk_run_waiting forever.
            logger.warning(
                "product_provider_contract_violation error_code=%s",
                getattr(getattr(exc, "code", None), "value", None)
                or getattr(exc, "code", None)
                or "",
            )
            raise ProviderProtocolError(private_cause=exc) from None
        except Exception as exc:
            # The SDK coordinator deliberately converts unexpected exceptions
            # after a physical handoff into an unknown outcome.  Keep a safe,
            # payload-free breadcrumb so provider response contract drift can
            # be diagnosed without logging prompts, responses, or secrets.
            logger.exception(
                "product_provider_invoke_failed error_type=%s error_code=%s",
                type(exc).__name__,
                getattr(getattr(exc, "code", None), "value", None)
                or getattr(exc, "code", None)
                or "",
            )
            raise
        response = _extract_public_progress(response)
        response = _retain_tool_calls_in_message(response)
        await self._capture_public_tool_narration(request, response)
        return response

    async def _capture_public_tool_narration(
        self,
        request: ProviderRequest,
        response: ProviderResponse,
    ) -> None:
        """Best-effort bridge for deliberately public tool-turn narration.

        OpenAI-compatible providers may also return private
        ``reasoning_content`` fields.  The delegate intentionally does not
        retain those fields; this bridge only observes normalized assistant
        ``message.content`` and therefore cannot expose hidden chain of
        thought or feed it into a later provider turn.
        """

        if not response.tool_calls:
            return
        content = response.message.content.strip()

        request_id = request.request_id.value
        run_id = ""
        if request_id.startswith("provider-turn:"):
            raw_turn = request_id.removeprefix("provider-turn:")
        else:
            legacy_run_id, marker, raw_turn = request_id.rpartition(
                ":provider-turn:"
            )
            if not marker:
                return
            run_id = legacy_run_id
        try:
            turn = int(raw_turn)
        except ValueError:
            return
        if turn < 1:
            return

        try:
            from .desktop_runtime import _delivery_adapters

            # SDK v0.1.4 uses provider request identities like
            # ``provider-turn:1`` without a Run id. When exactly one product
            # Run is active, it is safe to retain the model's public content.
            # Concurrent ambiguity fails closed here; each per-Run Delivery
            # adapter still creates its own bounded tool narration fallback.
            if not run_id and len(_delivery_adapters) == 1:
                run_id = next(iter(_delivery_adapters))
            delivery = _delivery_adapters.get(run_id)
            if delivery is not None:
                await delivery.capture_public_narration(
                    content,
                    iteration=turn - 1,
                    call_ids=tuple(call.call_id.value for call in response.tool_calls),
                )
        except Exception as exc:  # noqa: BLE001 - presentation is best-effort
            # Never log narration/provider payloads. A projection failure may
            # reduce UI detail, but must not change Provider or Run settlement.
            logger.warning(
                "sdk_public_narration_capture_failed run_id=%s error_type=%s",
                run_id,
                type(exc).__name__,
            )

    def public_snapshot(self) -> dict[str, str | int]:
        return {
            "adapter_key": self.target.adapter_key,
            "endpoint_identity": self.target.endpoint_identity,
            "input_micros_per_million": self.price_snapshot.input_micros_per_million,
            "model": self.target.model,
            "output_micros_per_million": self.price_snapshot.output_micros_per_million,
            "price_fingerprint": self.price_snapshot.fingerprint,
            "price_version": self.price_snapshot.version,
            "provider_id": self.target.provider_id,
        }

    def __repr__(self) -> str:
        return (
            "ProductProviderAdapter("
            f"provider_id={self.target.provider_id!r},model={self.target.model!r},"
            f"endpoint_identity={self.target.endpoint_identity!r})"
        )


class ProductProviderInvocationCoordinator(ProviderInvocationCoordinator):
    """Keep Provider handoff uncertainty separate from user Run cancellation."""

    async def invoke(
        self,
        run_id,
        request,
        *,
        cancel,
        execution_lease,
        workflow_lease=None,
    ):
        try:
            return await super().invoke(
                run_id,
                request,
                cancel=cancel,
                execution_lease=execution_lease,
                workflow_lease=workflow_lease,
            )
        except ProviderInvocationUnknownError:
            if cancel.is_cancelled:
                # The physical Provider invocation remains unknown for
                # reconciliation/billing, but the user-owned Run cancellation
                # is authoritative and must reach Runtime._cancel_run.
                raise asyncio.CancelledError() from None
            raise


__all__ = (
    "ProductPriceSnapshot",
    "ProductProviderAdapter",
    "ProductProviderInvocationCoordinator",
    "ProductProviderRegistry",
)

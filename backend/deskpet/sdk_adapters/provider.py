"""Public SDK Provider bridge over the product registry and keychain."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

import httpx

from simple_harness.contracts import ContractValidationError
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.contracts.json import thaw_json
from simple_harness.providers import (
    CancelToken,
    OpenAICompatibleProvider,
    ProviderAuthenticationError,
    ProviderProtocolError,
    ProviderRequest,
    ProviderResponse,
    ProviderTarget,
    Secret,
)


_PROVIDER_TOOL_CALLS_METADATA_KEY = "provider_tool_calls"
logger = logging.getLogger(__name__)


class _ProductOpenAICompatibleProvider(OpenAICompatibleProvider):
    """Restore OpenAI tool-call history retained in SDK message metadata."""

    @staticmethod
    def _message_payload(message: Message) -> dict[str, Any]:
        payload = OpenAICompatibleProvider._message_payload(message)
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
            or not isinstance(price[2], str)
            or not price[2].strip()
        ):
            raise ValueError("provider price snapshot is unknown or invalid")
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
        self._delegate = _ProductOpenAICompatibleProvider(
            client,
            base_url,
            frozen_model,
            Secret(secret_value),
            timeout,
            provider_id=provider_id,
            pricing_key=pricing_key,
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
            response = await self._delegate.invoke(request, cancel=cancel)
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


__all__ = (
    "ProductPriceSnapshot",
    "ProductProviderAdapter",
    "ProductProviderRegistry",
)

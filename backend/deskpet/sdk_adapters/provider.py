"""Public SDK Provider bridge over the product registry and keychain."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from simple_harness.providers import (
    CancelToken,
    OpenAICompatibleProvider,
    ProviderAuthenticationError,
    ProviderRequest,
    ProviderResponse,
    ProviderTarget,
    Secret,
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
        self._delegate = OpenAICompatibleProvider(
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
        return await self._delegate.invoke(request, cancel=cancel)

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

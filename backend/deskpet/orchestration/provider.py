# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""The model the orchestration runtime uses (plan §3.3, decision D9, HA-17).

At service start the Host's provider chain is read once (the first enabled entry, the same
source the chat uses) into a :class:`ProviderSnapshot`; a later change of provider affects
new Attempts only after a restart.  A fresh install has no chain: the service is then
unavailable with "未配置模型" and the chat still starts.

DeepSeek's official endpoint names its flash model ``deepseek-flash``; a configuration that
says ``deepseek-v4-flash`` there is mapped and the status shows both ids.  No price table
is injected, so money is recorded as unpriced (never 0).  The API key lives only inside
the SDK ``Secret``; it is never in a repr, a log line, a payload or the manifest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

DEEPSEEK_OFFICIAL_HOSTS = frozenset({"api.deepseek.com"})
MODEL_ALIASES = {"deepseek-v4-flash": "deepseek-flash"}
NO_MODEL = "未配置模型"


class ProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderSnapshot:
    provider_id: str
    base_url: str
    configured_model: str
    requested_model: str
    api_key: str = field(repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "configured": self.configured_model,
            "requested": self.requested_model,
            "price": "unpriced",
        }


def requested_model(base_url: str, configured: str) -> str:
    host = (urlparse(base_url).hostname or "").lower()
    if host in DEEPSEEK_OFFICIAL_HOSTS:
        return MODEL_ALIASES.get(configured, configured)
    return configured


def snapshot_from_registry(registry: Any) -> ProviderSnapshot:
    if registry is None:
        raise ProviderUnavailable(NO_MODEL)
    try:
        chain = registry.get_chain()
    except Exception as error:  # NoProviderConfiguredError on a fresh install
        raise ProviderUnavailable(NO_MODEL) from error
    if not chain:
        raise ProviderUnavailable(NO_MODEL)
    entry = dict(chain[0])
    models = list(entry.get("models") or ())
    configured = str(entry.get("default_model") or entry.get("model") or (models[0] if models else ""))
    base_url = str(entry.get("base_url") or "")
    provider_id = str(entry.get("id") or "")
    if not (configured and base_url and provider_id):
        raise ProviderUnavailable(NO_MODEL)
    key = registry.resolve_api_key(provider_id)
    if not key:
        raise ProviderUnavailable(f"{NO_MODEL}：provider {provider_id} 没有可用的密钥")
    return ProviderSnapshot(
        provider_id=provider_id,
        base_url=base_url,
        configured_model=configured,
        requested_model=requested_model(base_url, configured),
        api_key=str(key),
    )


def build_provider(snapshot: ProviderSnapshot, *, timeout: float = 180.0,
                   allow_private_http: bool = False) -> tuple[Any, Any]:
    """``(provider, http_client)`` — the caller closes the client on shutdown."""

    import httpx
    from simple_harness.providers import OpenAICompatibleProvider, Secret

    from deskpet.provider_extra_headers import install_httpx_extra_headers_hook

    client = install_httpx_extra_headers_hook(httpx.AsyncClient())
    options = {"allow_private_http": True} if allow_private_http else {}
    # SDKs that implement complete SSE assembly can keep long model calls alive.
    # Older pinned wheels retain their existing transport until upgraded.
    import inspect
    if "stream" in inspect.signature(OpenAICompatibleProvider).parameters:
        options["stream"] = True
    provider = OpenAICompatibleProvider(
        client, snapshot.base_url, snapshot.requested_model, Secret(snapshot.api_key), timeout=timeout, **options
    )
    return provider, client


__all__ = (
    "NO_MODEL",
    "ProviderSnapshot",
    "ProviderUnavailable",
    "build_provider",
    "requested_model",
    "snapshot_from_registry",
)

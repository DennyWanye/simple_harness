"""Construction and lifecycle of the process-wide SearchGateway."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import AppConfig
    from .search_gateway import SearchGateway

_default_gateway: "SearchGateway | None" = None


def build_search_gateway(config: "AppConfig") -> "SearchGateway":
    from .providers import BaiduProvider, BingCDPProvider, BingHTTPProvider, DuckDuckGoProvider, GoogleCDPProvider, SearXNGProvider
    from .search_gateway import SearchGateway

    cfg = config.search_gateway
    providers = [BaiduProvider(), DuckDuckGoProvider(), GoogleCDPProvider(), BingCDPProvider()]
    if "bing" in cfg.providers:
        providers.append(BingHTTPProvider())
    if cfg.searxng_url:
        providers.append(SearXNGProvider(cfg.searxng_url))
    fetch_service = None
    try:
        from .fetch_extract import FetchExtractService
        fetch_service = FetchExtractService.from_config(config)
    except ImportError:
        pass
    return SearchGateway(config=cfg, providers=providers, fetch_service=fetch_service)


def set_default_gateway(gateway: "SearchGateway | None") -> None:
    global _default_gateway
    _default_gateway = gateway


def get_default_gateway() -> "SearchGateway":
    global _default_gateway
    if _default_gateway is None:
        from config import AppConfig
        _default_gateway = build_search_gateway(AppConfig())
    return _default_gateway


async def shutdown_default_gateway() -> None:
    global _default_gateway
    gateway, _default_gateway = _default_gateway, None
    if gateway is not None:
        await gateway.shutdown()


__all__ = ["build_search_gateway", "get_default_gateway", "set_default_gateway", "shutdown_default_gateway"]

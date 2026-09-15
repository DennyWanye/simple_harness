"""Per-endpoint extra HTTP headers declared in ``llm_runtime.json``.

2026-09-16 (Grok Build lane): xAI's CLI chat proxy
(``https://cli-chat-proxy.grok.com/v1``) is OpenAI-compatible but only
accepts a request when it also carries the CLI's identification headers
(``X-XAI-Token-Auth`` / ``x-grok-model-override`` / ``x-grok-client-version``
/ ``User-Agent``).  ``OpenAICompatibleProvider`` and the SDK adapter's httpx
client previously sent ``Authorization`` only, so there was no way to point
the Host at that proxy without patching code.

``llm_runtime.json`` may now declare::

    {
      "base_url": "https://cli-chat-proxy.grok.com/v1",
      "model": "grok-4.6",
      "api_key": "<token>",
      "extra_headers": {"X-XAI-Token-Auth": "xai-grok-cli", ...},
      "extra_headers_by_host": {"other.host": {"X-Foo": "bar"}}
    }

* ``extra_headers`` applies to requests whose host equals the file's own
  ``base_url`` host.
* ``extra_headers_by_host`` is an explicit ``{host: headers}`` map for any
  additional endpoints (e.g. a registry provider that is not the primary).

Headers are matched by **host only**, so a DeepSeek/relay endpoint never
receives Grok headers and vice versa.  Values are sent verbatim and are
never logged.  The file is re-read when its mtime changes, so a lane swap
(``scripts/grok_build_runtime.py apply/restore``) needs no restart of the
helper itself (the provider objects still cache ``base_url``/``api_key``).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "extra_headers_for",
    "install_httpx_extra_headers_hook",
    "load_extra_headers_map",
]

_lock = threading.Lock()
# (path, mtime_ns, {host: headers})
_cache: tuple[str, int, dict[str, dict[str, str]]] | None = None


def _runtime_path() -> Path:
    override = os.environ.get("DESKPET_LLM_RUNTIME_PATH")
    if override:
        return Path(override)
    import paths as _paths

    return _paths.user_data_dir() / "llm_runtime.json"


def _host_of(url: str) -> str:
    try:
        return (urlsplit(str(url or "")).hostname or "").lower()
    except Exception:  # noqa: BLE001 - malformed url → no match
        return ""


def _clean(headers: Any) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in headers.items():
        if isinstance(k, str) and k.strip() and isinstance(v, (str, int, float)):
            out[k.strip()] = str(v)
    return out


def load_extra_headers_map(path: Path | str | None = None) -> dict[str, dict[str, str]]:
    """Return ``{host: headers}`` parsed from ``llm_runtime.json`` (cached by mtime)."""
    global _cache
    p = Path(path) if path is not None else _runtime_path()
    key = str(p)
    try:
        mtime = p.stat().st_mtime_ns
    except OSError:
        return {}
    with _lock:
        if _cache is not None and _cache[0] == key and _cache[1] == mtime:
            return _cache[2]
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - malformed file → behave as "no extra headers"
        raw = {}
    result: dict[str, dict[str, str]] = {}
    if isinstance(raw, dict):
        own = _clean(raw.get("extra_headers"))
        own_host = _host_of(str(raw.get("base_url") or ""))
        if own and own_host:
            result[own_host] = own
        by_host = raw.get("extra_headers_by_host")
        if isinstance(by_host, dict):
            for host, headers in by_host.items():
                cleaned = _clean(headers)
                if isinstance(host, str) and host.strip() and cleaned:
                    result.setdefault(host.strip().lower(), {}).update(cleaned)
    with _lock:
        _cache = (key, mtime, result)
    return result


def extra_headers_for(base_url: str, *, path: Path | str | None = None) -> dict[str, str]:
    """Headers to add for a request/endpoint at ``base_url`` (``{}`` when none)."""
    host = _host_of(base_url)
    if not host:
        return {}
    return dict(load_extra_headers_map(path).get(host, {}))


def install_httpx_extra_headers_hook(client: Any) -> Any:
    """Attach a ``request`` event hook to an ``httpx.AsyncClient``.

    The SDK's ``OpenAICompatibleProvider`` builds its own request headers, so
    the Host injects endpoint-specific headers at the transport edge instead.
    Configured values override whatever httpx/SDK set for the same name
    (e.g. ``User-Agent``).  Returns the client for chaining.
    """

    async def _inject(request: Any) -> None:
        for k, v in extra_headers_for(str(request.url)).items():
            request.headers[k] = v

    try:
        hooks = client.event_hooks
        hooks.setdefault("request", []).append(_inject)
        client.event_hooks = hooks
    except Exception:  # noqa: BLE001 - never let a diagnostics hook break startup
        pass
    return client

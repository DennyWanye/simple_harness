"""Grok Build lane: per-endpoint extra headers from llm_runtime.json."""

from __future__ import annotations

import json

import httpx
import pytest

from deskpet import provider_extra_headers as peh
from providers.openai_compatible import OpenAICompatibleProvider

GROK = {
    "base_url": "https://cli-chat-proxy.grok.com/v1",
    "model": "grok-4.6",
    "api_key": "session-token",
    "extra_headers": {
        "X-XAI-Token-Auth": "xai-grok-cli",
        "x-grok-model-override": "grok-4.6",
        "User-Agent": "xai-grok-cli",
    },
    "extra_headers_by_host": {"Relay.Example.com": {"X-Relay": "1"}},
}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    p = tmp_path / "llm_runtime.json"
    p.write_text(json.dumps(GROK), encoding="utf-8")
    monkeypatch.setenv("DESKPET_LLM_RUNTIME_PATH", str(p))
    return p


def test_map_matches_by_host_only(runtime):
    m = peh.load_extra_headers_map(runtime)
    assert m["cli-chat-proxy.grok.com"]["X-XAI-Token-Auth"] == "xai-grok-cli"
    assert m["relay.example.com"] == {"X-Relay": "1"}
    assert peh.extra_headers_for("https://cli-chat-proxy.grok.com/v1/chat/completions")["User-Agent"] == "xai-grok-cli"
    assert peh.extra_headers_for("https://api.deepseek.com/v1") == {}
    assert peh.extra_headers_for("") == {}


def test_map_reloads_on_mtime_change(runtime):
    assert peh.extra_headers_for("https://cli-chat-proxy.grok.com/v1")
    runtime.write_text(json.dumps({"base_url": "https://api.deepseek.com/v1"}), encoding="utf-8")
    import os

    os.utime(runtime, ns=(runtime.stat().st_atime_ns + 1, runtime.stat().st_mtime_ns + 1_000_000))
    assert peh.extra_headers_for("https://cli-chat-proxy.grok.com/v1") == {}


def test_missing_or_malformed_file_is_empty(tmp_path):
    assert peh.load_extra_headers_map(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert peh.load_extra_headers_map(bad) == {}


def test_host_provider_client_sends_extra_headers(runtime):
    p = OpenAICompatibleProvider(base_url=GROK["base_url"], api_key="tok", model="grok-4.6")
    client = p._client(10.0)
    try:
        assert client.headers["Authorization"] == "Bearer tok"
        assert client.headers["X-XAI-Token-Auth"] == "xai-grok-cli"
        assert client.headers["x-grok-model-override"] == "grok-4.6"
        assert client.headers["User-Agent"] == "xai-grok-cli"
    finally:
        import asyncio

        asyncio.run(client.aclose())

    other = OpenAICompatibleProvider(base_url="https://api.deepseek.com/v1", api_key="tok", model="deepseek-flash")
    c2 = other._client(10.0)
    try:
        assert "X-XAI-Token-Auth" not in c2.headers
    finally:
        asyncio.run(c2.aclose())


def test_explicit_ctor_headers_win(runtime):
    p = OpenAICompatibleProvider(
        base_url=GROK["base_url"], api_key="tok", model="grok-4.6",
        extra_headers={"x-grok-model-override": "grok-4.5"},
    )
    client = p._client(10.0)
    try:
        assert client.headers["x-grok-model-override"] == "grok-4.5"
        assert client.headers["X-XAI-Token-Auth"] == "xai-grok-cli"
    finally:
        import asyncio

        asyncio.run(client.aclose())


@pytest.mark.asyncio
async def test_httpx_hook_injects_only_for_matching_host(runtime):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = peh.install_httpx_extra_headers_hook(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    async with client:
        await client.post("https://cli-chat-proxy.grok.com/v1/chat/completions", headers={"Authorization": "Bearer t"}, json={})
        await client.post("https://api.deepseek.com/v1/chat/completions", headers={"Authorization": "Bearer t"}, json={})
    grok_req, ds_req = seen
    assert grok_req.headers["X-XAI-Token-Auth"] == "xai-grok-cli"
    assert grok_req.headers["User-Agent"] == "xai-grok-cli"
    assert grok_req.headers["Authorization"] == "Bearer t"
    assert "X-XAI-Token-Auth" not in ds_req.headers
    assert ds_req.headers["User-Agent"].startswith("python-httpx")

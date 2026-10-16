# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Construction, launch request parts, relay errors, the HTTP client and health checks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
from llm.errors import LLMProviderError
from llm.relay_errors import classify_relay_error

from providers.context_os_e2e_hooks import trusted_provider_headers
from providers.dispatch_handoff import current_dispatch_handoff
from providers.dispatch_transport import DispatchAwareAsyncTransport
from providers.extra_headers import extra_headers_for

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from deskpet.execution.contracts import ProviderLaunchSnapshot


# 2026-06-28: 空/占位符 api_key 会被拼成非法头 "Authorization: Bearer "
# → httpx LocalProtocolError 把整轮对话刷崩（装机版 relay provider 路径丢失
# 后退到 legacy 空 key 时的次生崩溃，见 plans/2026-06-28-userdata-path-binding-fix）。
# 在唯一的 client 构造点 _client() 拦截：非本地 endpoint 且 key 不可用时抛
# 友好的 LLMProviderError(error_class=empty_api_key)，由 chat 层照常 surface 成
# "请重新登录/配置 provider"，而不是崩在传输层。本地 ollama（key 占位、server
# 忽略）放行。
# 注意含 "ollama"：chain 路径（main.py resolve_api_key(...) or "ollama"）在
# keychain key 缺失时会把 key 默认成 "ollama"。对**非本地** endpoint，"ollama"
# 等于"没有真 key"，必须拦截给友好错误（否则给云端发 Bearer ollama → 401，
# 对用户不可读）；本地 ollama/localhost endpoint 由 _is_local_base_url 放行。
_UNUSABLE_API_KEYS = {"", "ollama", "from-keychain", "from-env", "your-key-here"}


def _is_local_base_url(base_url: str) -> bool:
    b = (base_url or "").lower()
    return "localhost" in b or "127.0.0.1" in b or "0.0.0.0" in b


class OpenAICompatibleCore:
    """Construction, launch request parts, relay errors, the HTTP client and health checks."""

    if TYPE_CHECKING:

        async def _legacy_chat_with_tools_nonstream(
            self,
            messages: list[dict],
            *,
            tools: list[dict] | None = ...,
            max_tokens: int = ...,
            temperature: float | None = ...,
            response_format: dict | None = ...,
            tool_choice: str | None = ...,
        ) -> dict: ...
        async def chat_stream_with_tools(
            self,
            messages: list[dict],
            *,
            tools: list[dict] | None = ...,
            max_tokens: int = ...,
            temperature: float | None = ...,
            response_format: dict | None = ...,
            tool_choice: str | None = ...,
            retry_mode: str = ...,
            launch_operation_id: str | None = ...,
            provider_launch_snapshot: ProviderLaunchSnapshot | None = ...,
        ): ...

    adapter_id = "openai-compatible"
    adapter_version = "v1"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.7,
        timeout: float | None = None,
        sanitize_inline_cot_dsml: bool = True,
        code_params: dict | None = None,
        is_relay: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self._is_relay = is_relay
        # 2026-09-16 Grok Build lane: endpoint-specific headers. Explicit
        # ctor value wins over llm_runtime.json ``extra_headers`` (matched by
        # host in ``_client``). Never logged.
        self.extra_headers: dict[str, str] = dict(extra_headers or {})
        # code-session-model-params: per-code-session request fragment
        # (e.g. {"reasoning_effort":"high","extra_body":{...}}) produced
        # by llm.code_params; merged into the chat payload. None/{} = no
        # change (provider defaults / non-code sessions).
        self.code_params = code_params or {}
        # 2026-05-17 deepseek-inline-cot-dsml-sanitize Strangler-Fig flag.
        # Default on; caller (provider registry) passes the config value.
        # False = byte-for-byte legacy passthrough (demo rollback).
        self.sanitize_inline_cot_dsml = sanitize_inline_cot_dsml
        # P4-S25 / P5-S2: timeout is a tuple-style budget instead of a single
        # number:
        #   * connect = 10s — TCP + TLS handshake. Bumped 5→10s on 2026-05-11
        #                     after seeing real ConnectTimeout failures while
        #                     the relay itself reported no errors (their server
        #                     never saw the request — handshake failed our
        #                     side). Windows DNS cache invalidation + TLS
        #                     slow-start on cold connections legitimately
        #                     takes 6-8s. 10s gives headroom without making
        #                     real outages drag.
        #   * read    = 60s — between bytes / for a full single response.
        #                     Initial 20s based on user-reported 14.4s p-max
        #                     was too tight: 2026-05-09 logs showed frequent
        #                     `attempt=1 of=3 ReadTimeout` retries that ate
        #                     the whole budget on a string of slow turns.
        #                     60s covers thinking-mode bursts + the relay
        #                     idle-spikes; the proxy's own 100-120s idle cut
        #                     remains the outer bound.
        #   * write   = 5s  — POST body is small; anything past this is dead.
        #   * pool    = 5s  — connection-pool acquisition.
        # Caller can still pass a scalar/Timeout; we honour that verbatim.
        # P5-S2 F1 (2026-05-12) — bumped read 60→120 and write 5→10 per
        # the relay's integration guide (docs/中转站建议.md). the relay's PR #27
        # ships a 15s SSE keep-alive comment that resets read timers on
        # every hop, so a 120s read budget is generous + safe and covers
        # individual reasoning tokens that can be 5KB+ on slow chunks.
        # connect stays at 10s — Windows DNS slow-start legitimately
        # takes 6-8s, the relay's suggested 5s would false-fail occasionally.
        if timeout is None:
            # Local Ollama can spend several minutes evaluating a large
            # context before it emits the first SSE byte, especially when a
            # model is split between GPU and CPU memory.  The relay budget is
            # intentionally tighter because its keep-alive frames reset the
            # read timer; a local server has no such guarantee.
            read_timeout_s = 600.0 if _is_local_base_url(self.base_url) else 120.0
            self.timeout: float | httpx.Timeout = httpx.Timeout(
                connect=10.0,
                read=read_timeout_s,
                write=10.0,
                pool=5.0,
            )
            # Per-read timeouts do not stop a dead relay stream that emits
            # keep-alives forever. Bound the interval between meaningful
            # model events as well. Do not cap the total stream duration:
            # reasoning-heavy GLM-5.x calls can legitimately keep producing
            # reasoning deltas for longer than three minutes.
            self._stream_attempt_timeout_s = 900.0 if _is_local_base_url(self.base_url) else 180.0
        else:
            self.timeout = timeout
            self._stream_attempt_timeout_s = 900.0 if _is_local_base_url(self.base_url) else 180.0
        # P2-1-S8: last completed stream's usage block from the OpenAI SSE
        # protocol (populated only when the server emits one — OpenAI/DashScope
        # always do when stream_options.include_usage=True; Ollama today does
        # NOT emit usage in its SSE stream, so this stays None after Ollama
        # calls and billing records nothing. main.py handles that case.
        self.last_usage: dict | None = None
        # Test-only injection: unit tests assign an httpx.MockTransport here.
        # Production code MUST leave this None; otherwise every request goes
        # through the mock and never reaches the real endpoint.
        self._test_transport: httpx.AsyncBaseTransport | None = None

    def _launch_request_parts(
        self,
        launch_operation_id: str | None,
        snapshot: ProviderLaunchSnapshot | None,
    ) -> tuple[dict[str, str], str]:
        if (launch_operation_id is None) != (snapshot is None):
            raise ValueError("launch operation id and snapshot must be paired")
        if snapshot is None:
            return {}, "resilient"
        provider_id = str(
            getattr(self, "provider_id", "") or getattr(self, "id", "") or type(self).__name__
        )
        if (provider_id, self.adapter_id, self.adapter_version) != (
            snapshot.provider_id,
            snapshot.adapter_id,
            snapshot.adapter_version,
        ):
            raise ValueError("provider launch identity mismatch")
        if not snapshot.supports_idempotent_launch:
            return {}, "at_most_once"
        prefix, separator, field_name = (snapshot.token_field or "").partition(":")
        if (
            not separator
            or prefix != "header"
            or not field_name
            or field_name.lower() in {"authorization", "content-type"}
            or any(ch in field_name for ch in "\r\n\t ")
        ):
            raise ValueError("OpenAI-compatible launch token must use a safe header:<name>")
        return {field_name: launch_operation_id or ""}, "resilient"

    def _relay_error_class(
        self,
        status_code: int | None,
        body_text: str = "",
        response: httpx.Response | None = None,
    ) -> str | None:
        body: dict | None = None
        if response is not None:
            try:
                parsed = response.json()
                if isinstance(parsed, dict):
                    body = parsed
            except Exception:  # noqa: BLE001
                body = None
        error = body.get("error") if isinstance(body, dict) else None
        upstream_code = str(error.get("code") or "") if isinstance(error, dict) else ""
        if upstream_code in {"tool_catalog_stale", "tool_policy_unavailable"}:
            return upstream_code
        if not self._is_relay:
            return None
        return classify_relay_error(status_code, body_text, body=body)

    def _client(
        self,
        timeout: float | httpx.Timeout,
        *,
        transport_retries: int = 1,
    ) -> httpx.AsyncClient:
        # 空 key 护栏：唯一的出站 client 构造点。非本地 endpoint 且 key 不可用
        # （空/占位符）时不构造 client、不发请求，抛友好错误，避免拼出非法的
        # "Authorization: Bearer " 头崩在 httpx 传输层（LocalProtocolError）。
        if (self.api_key or "").strip().lower() in _UNUSABLE_API_KEYS and not _is_local_base_url(
            self.base_url
        ):
            raise LLMProviderError(
                "未检测到可用的 LLM API Key（可能未登录或登录已失效）。"
                "请重新登录，或在 设置 → LLM Providers 配置 API Key 后重试。",
                error_class="empty_api_key",
            )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # Grok Build lane (2026-09-16): cli-chat-proxy.grok.com rejects
        # requests without the CLI identification headers (426). They come
        # from llm_runtime.json ``extra_headers`` (host-matched) or the ctor.
        headers.update(extra_headers_for(self.base_url))
        headers.update(self.extra_headers)

        headers.update(trusted_provider_headers(self.base_url))
        # P5-S2 F1 (2026-05-12) — the relay integration guide root cause for
        # "succeeds then next request ConnectError" (docs/中转站建议.md
        # Failure Mode 2):
        #
        # httpx's default connection pool retains the TCP socket between
        # requests for reuse. Middleboxes (NAT, corporate proxy, ISP) and
        # the server's own keepAliveTimeout=75s silently kill that idle
        # socket. When we send the NEXT request to the stale FD, the
        # first write gets ECONNRESET and httpx surfaces it as
        # ConnectError — looking exactly like "can't connect to the relay"
        # even though the relay is healthy.
        #
        # Fix per the relay guide: disable pool reuse entirely. New TCP +
        # TLS handshake every request costs ~50ms; for an LLM workload
        # where each call is 5-30s, the overhead is irrelevant and the
        # fix eliminates an entire class of intermittent failures.
        #
        # transport retries=1 adds one implicit retry on raw socket
        # errors (httpx layer, BEFORE our 3-retry application backoff).
        # Cheap belt-and-suspenders for the (rare) case where a fresh
        # connection still hits a transient SYN drop.
        handoff = current_dispatch_handoff()
        if handoff is not None:
            transport_retries = 0
        limits = httpx.Limits(max_keepalive_connections=0)
        # Test path: when a MockTransport is injected we don't apply
        # AsyncHTTPTransport(retries=1) — mock transports don't honour
        # retries and the tests assert exact request counts.
        if self._test_transport is not None:
            transport: httpx.AsyncBaseTransport = self._test_transport
        else:
            transport = httpx.AsyncHTTPTransport(retries=transport_retries)
        if handoff is not None:
            transport = DispatchAwareAsyncTransport(
                transport,
                adapter_identity=(f"{self.adapter_id}:{self.adapter_version}:{self.base_url}"),
            )
        return httpx.AsyncClient(
            timeout=timeout,
            headers=headers,
            limits=limits,
            transport=transport,
        )

    async def health_check(self) -> bool:
        try:
            # 15s timeout: Sealos scale-to-zero cold start can exceed 5s,
            # causing health_check to return False and cloud_first to
            # silently fall back to local for the next 30s (cache TTL).
            async with self._client(timeout=15.0) as client:
                # Primary probe: GET /models (OpenAI standard, cheap, no token cost).
                resp = await client.get(f"{self.base_url}/models")
                if resp.status_code == 200:
                    return True

                # Fallback: many third-party OpenAI-compatible relays
                # (your-llm-relay.example.com, some sealos endpoints, certain proxies)
                # only implement /chat/completions and return 404/501 on
                # /models. Try a 1-token chat probe so users can still use
                # those services. Costs ~prompt_tokens charge but proves
                # the key + model are valid.
                if resp.status_code in (404, 405, 501):
                    chat_resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        json={
                            "model": self.model,
                            "messages": [{"role": "user", "content": "."}],
                            "max_tokens": 1,
                            "temperature": 0,
                        },
                    )
                    return chat_resp.status_code == 200
                return False
        except Exception:  # noqa: BLE001 - 健康检查出错按不健康
            logger.debug("provider_health_check_failed", exc_info=True)
            return False

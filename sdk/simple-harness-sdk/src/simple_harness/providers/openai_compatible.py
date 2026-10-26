# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Thin OpenAI-compatible HTTPS adapter with no retry or runtime state."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from ipaddress import ip_address, ip_network
from typing import Any, cast
from urllib.parse import urlsplit

import httpx

from simple_harness.contracts.identity import CallId
from simple_harness.contracts.json import JsonValue, validate_json_value
from simple_harness.contracts.messages import Message, MessageRole

from .base import (
    PROVIDER_REASONING_KEY,
    REASONING_EFFORTS,
    THINKING_MODES,
    CancelToken,
    ProviderContinuationCapability,
    ProviderContinuationMode,
    ProviderRequest,
    ProviderResponse,
    ProviderTarget,
    ProviderToolCall,
    ProviderUsage,
    Secret,
)
from .chat_stream import ChatStream
from .deepseek_strict import (
    DEEPSEEK_STRICT_ADAPTER_KEY,
    DEEPSEEK_STRICT_TOOL_SCHEMA_MODE,
    compile_deepseek_strict_schema,
)
from .errors import (
    ProviderAuthenticationError,
    ProviderCancelledError,
    ProviderPaymentRequiredError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderRequestRejectedError,
    ProviderServerError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from .redaction import SecretRedactor

LEGACY_TOOL_SCHEMA_MODE = "legacy"
_STREAM_IDLE_TIMEOUT_SECONDS = 180.0
_STREAM_FIRST_BYTE_TIMEOUT_SECONDS = 600.0


def _json_value(value: Any) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    validate_json_value(value)
    return value


def _plain_mapping(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return {key: _json_value(item) for key, item in value.items()}


def openai_chat_request_payload(
    request: ProviderRequest,
    *,
    model: str,
    tool_schema_mode: str = LEGACY_TOOL_SCHEMA_MODE,
    thinking: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """The exact credential-free body used by the chat adapter, also for admission.

    Callers must first restore durable assistant tool calls (and, in thinking mode, the
    replayed reasoning). This function neither consults a client nor sends a request;
    the HTTP adapter uses the same serializer, so a counter given the same ``thinking``
    renders exactly what goes on the wire.
    """
    payload = OpenAICompatibleProvider._payload_for_model(
        request, model, tool_schema_mode=tool_schema_mode
    )
    if thinking is not None:
        payload["thinking"] = {"type": thinking}
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort
    return payload


logger = logging.getLogger(__name__)

_THINKING_FIELDS = frozenset({"thinking", "reasoning_effort"})
_REQUEST_BODY_FIELDS = frozenset(
    {
        "model",
        "messages",
        "tools",
        "tool_choice",
        "max_tokens",
        "temperature",
        "stream",
        "stream_options",
    }
)

_DIAGNOSTIC_FINISH_REASONS = frozenset(
    {"content_filter", "function_call", "length", "stop", "tool_calls"}
)


class _ToolParseReason(StrEnum):
    SHAPE = "shape"
    TYPE = "type"
    ID = "id"
    FUNCTION = "function"
    NAME = "name"
    ARGUMENTS_JSON = "arguments_json"
    ARGUMENTS_NON_OBJECT = "arguments_non_object"
    NORMALIZATION = "normalization"


class _ToolCallParseError(ProviderProtocolError):
    """Private marker for a rejected tool-call shape without retaining its payload."""

    __slots__ = ("reason",)

    def __init__(self, reason: _ToolParseReason) -> None:
        super().__init__()
        self.reason = reason


class _ProtocolErrorWithUsage(ProviderProtocolError):
    """A rejected response can still contain independently valid billed usage."""

    __slots__ = ("detail",)

    def __init__(
        self,
        usage: ProviderUsage,
        cause: ProviderProtocolError,
        finish_reason: str | None,
    ) -> None:
        super().__init__(public_message=str(cause), private_cause=cause)
        self.detail: dict[str, JsonValue] = {
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                "cache_tokens": usage.cache_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
            }
        }
        if finish_reason is not None:
            self.detail["finish_reason"] = finish_reason
        if isinstance(cause, _ToolCallParseError):
            self.detail["parse_stage"] = "tool_parse"
            self.detail["tool_parse_reason"] = cause.reason.value


class _IdleTimeoutStream(httpx.AsyncByteStream):
    def __init__(
        self, stream: httpx.AsyncByteStream, timeout: float, *, first_timeout: float
    ) -> None:
        self.stream, self.timeout = stream, timeout
        self.first_timeout = first_timeout

    async def __aiter__(self):
        chunks = self.stream.__aiter__()
        timeout = self.first_timeout
        while True:
            try:
                async with asyncio.timeout(timeout):
                    data = await anext(chunks)
                    while not data:
                        data = await anext(chunks)
            except StopAsyncIteration:
                return
            except TimeoutError:
                raise httpx.ReadTimeout("chat stream byte idle timeout") from None
            timeout = self.timeout
            yield data

    async def aclose(self) -> None:
        await self.stream.aclose()


class OpenAICompatibleProvider:
    """Perform one OpenAI-compatible chat-completions request per invocation."""

    __slots__ = (
        "_extra_body",
        "_response_model_aliases",
        "_thinking",
        "_reasoning_effort",
        "_client",
        "_endpoint",
        "_redactor",
        "_secret",
        "_target",
        "_timeout",
        "_tool_schema_mode",
        "_stream",
        "delta_sink",
    )

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        model: str,
        secret: Secret,
        timeout: float | httpx.Timeout = 30.0,
        *,
        provider_id: str | None = None,
        pricing_key: str | None = None,
        tool_schema_mode: str = LEGACY_TOOL_SCHEMA_MODE,
        allow_private_http: bool = False,
        stream: bool = False,
        extra_body: Mapping[str, Any] | None = None,
        response_model_aliases: Sequence[str] = (),
        thinking: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        if not isinstance(client, httpx.AsyncClient):
            raise TypeError("client must be an httpx.AsyncClient")
        try:
            parsed = urlsplit(base_url)
            hostname = parsed.hostname
        except ValueError as exc:
            raise ValueError("base_url must be a valid absolute HTTP(S) URL") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a query or fragment")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("base_url must not contain credentials")
        if type(allow_private_http) is not bool:
            raise TypeError("allow_private_http must be a boolean")
        if type(stream) is not bool:
            raise TypeError("stream must be a boolean")
        if extra_body is not None:
            # Provider-specific request fields (e.g. DeepSeek ``thinking``); they ride along
            # with every request body but never replace the counted, fingerprinted request.
            if not isinstance(extra_body, Mapping) or any(
                type(key) is not str or not key for key in extra_body
            ):
                raise TypeError("extra_body must be a mapping with non-empty string keys")
            clash = sorted(set(extra_body) & (_REQUEST_BODY_FIELDS | _THINKING_FIELDS))
            if clash:
                raise ValueError(f"extra_body must not override request fields: {clash}")
            validate_json_value(_plain_mapping(extra_body))
        self._extra_body = None if not extra_body else _plain_mapping(extra_body)
        # Names under which a relay echoes *this* model (e.g. a vendor-prefixed spelling).
        # The kernel trusts usage only when the echo equals the bound model name, so a
        # declared alias is normalised to it here; any other echo stays untrusted.
        if (
            isinstance(response_model_aliases, str)
            or not isinstance(response_model_aliases, Sequence)
            or any(
                not isinstance(alias, str) or not alias.strip() for alias in response_model_aliases
            )
        ):
            raise TypeError("response_model_aliases must be a sequence of non-empty strings")
        self._response_model_aliases = frozenset(response_model_aliases)
        # Thinking mode (DeepSeek-style).  None keeps the legacy request (field not sent,
        # the endpoint's own default applies).  "enabled" replays each returned reasoning
        # on later requests of the run (REASONING_REPLAY continuation); "disabled" asks the
        # model not to think.  The mode is part of the target identity.
        if thinking is not None and thinking not in THINKING_MODES:
            raise ValueError(f"thinking must be one of {THINKING_MODES} or None")
        if reasoning_effort is not None and (
            thinking != "enabled" or reasoning_effort not in REASONING_EFFORTS
        ):
            raise ValueError(
                "reasoning_effort needs thinking='enabled' and one of " + str(REASONING_EFFORTS)
            )
        self._thinking = thinking
        self._reasoning_effort = reasoning_effort
        private_http = False
        if allow_private_http and hostname is not None:
            try:
                address = ip_address(hostname)
                private_http = any(
                    address in ip_network(network)
                    for network in (
                        "10.0.0.0/8",
                        "172.16.0.0/12",
                        "192.168.0.0/16",
                        "fc00::/7",
                    )
                )
            except ValueError:
                pass
        if (
            parsed.scheme == "http"
            and not private_http
            and hostname
            not in {
                "127.0.0.1",
                "localhost",
                "::1",
            }
        ):
            raise ValueError("non-loopback provider URLs must use HTTPS")
        if tool_schema_mode not in (LEGACY_TOOL_SCHEMA_MODE, DEEPSEEK_STRICT_TOOL_SCHEMA_MODE):
            raise ValueError("unsupported tool_schema_mode")
        if tool_schema_mode == DEEPSEEK_STRICT_TOOL_SCHEMA_MODE and base_url.rstrip("/") not in (
            "https://api.deepseek.com/beta",
            "https://api.deepseek.com/beta/chat/completions",
        ):
            raise ValueError(
                "deepseek-strict-v1 requires the official HTTPS DeepSeek beta endpoint"
            )
        if not model.strip():
            raise ValueError("model must not be blank")
        normalized = base_url.rstrip("/")
        self._endpoint = (
            normalized
            if normalized.endswith("/chat/completions")
            else normalized + "/chat/completions"
        )
        self._client = client
        self._secret = secret
        self._tool_schema_mode = tool_schema_mode
        self._stream = stream
        #: 逐字输出：流式回复里每段用户可见正文调一次 ``(request, text)``；None 不转发。
        #: 回调抛错只记日志，不影响这次调用；片段只是预览，正文以回执为准。
        self.delta_sink: Callable[[ProviderRequest, str], None] | None = None
        if isinstance(timeout, bool) or (isinstance(timeout, (int, float)) and timeout <= 0):
            raise ValueError("timeout must be positive")
        read_timeout = timeout.read if isinstance(timeout, httpx.Timeout) else float(timeout)
        self._timeout = (
            httpx.Timeout(
                connect=10,
                read=(_STREAM_FIRST_BYTE_TIMEOUT_SECONDS if read_timeout is None else read_timeout),
                write=30,
                pool=30,
            )
            if stream
            else timeout
        )
        self._redactor = SecretRedactor.from_secrets(secret)
        self._target = ProviderTarget(
            provider_id=provider_id or str(hostname),
            model=model,
            pricing_key=pricing_key or model,
            endpoint_identity=self._endpoint,
            adapter_key=(
                (
                    DEEPSEEK_STRICT_ADAPTER_KEY
                    if tool_schema_mode == DEEPSEEK_STRICT_TOOL_SCHEMA_MODE
                    else "openai-compatible.chat-completions.v1"
                )
                + (".sse-v1" if stream else "")
                + ("" if thinking is None else f".thinking-{thinking}-v1")
                + ("" if reasoning_effort is None else f".effort-{reasoning_effort}")
            ),
        )

    @property
    def target(self) -> ProviderTarget:
        return self._target

    @property
    def thinking(self) -> str | None:
        return self._thinking

    @property
    def reasoning_effort(self) -> str | None:
        return self._reasoning_effort

    @property
    def continuation_capability(self) -> ProviderContinuationCapability:
        """Thinking enabled replays private reasoning; every other mode keeps none."""
        if self._thinking == "enabled":
            return ProviderContinuationCapability(ProviderContinuationMode.REASONING_REPLAY)
        return ProviderContinuationCapability()

    async def invoke(self, request: ProviderRequest, *, cancel: CancelToken) -> ProviderResponse:
        if cancel.is_cancelled:
            raise ProviderCancelledError()

        request_task = asyncio.create_task(self._post_once(request))
        cancel_task = asyncio.create_task(cancel.wait())
        try:
            done, _ = await asyncio.wait(
                {request_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if request_task in done:
                return await request_task
            request_task.cancel()
            await asyncio.gather(request_task, return_exceptions=True)
            raise ProviderCancelledError()
        except asyncio.CancelledError:
            request_task.cancel()
            await asyncio.gather(request_task, return_exceptions=True)
            raise ProviderCancelledError() from None
        finally:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)

    async def _post_once(self, request: ProviderRequest) -> ProviderResponse:
        try:
            if self._stream:
                return await self._stream_once(request)
            response = await self._client.post(
                self._endpoint,
                headers={
                    "Authorization": f"Bearer {self._secret.reveal()}",
                    "Content-Type": "application/json",
                    **self._request_identity_header(request),
                },
                json=self._request_payload(request),
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(private_cause=self._redactor.exception(exc)) from None
        except httpx.RequestError as exc:
            category = next(
                (
                    kind.__name__
                    for kind in (
                        httpx.ConnectError,
                        httpx.ReadError,
                        httpx.WriteError,
                        httpx.CloseError,
                        httpx.LocalProtocolError,
                        httpx.RemoteProtocolError,
                        httpx.ProxyError,
                        httpx.UnsupportedProtocol,
                    )
                    if isinstance(exc, kind)
                ),
                "RequestError",
            )
            raise ProviderTransportError(
                private_cause=self._redactor.exception(exc), transport_error_type=category
            ) from None

        self._check_response_status(response, request_id=request.request_id.value)
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            raise ProviderProtocolError(
                private_cause=RuntimeError("response body was not valid JSON")
            ) from None
        return self._parse_response(request, payload, response)

    def _check_response_status(self, response: httpx.Response, *, request_id: str) -> None:
        if response.status_code == 403:
            # Some compatible relays use 403 for expired credentials. Recognize
            # only the explicit machine code; never disclose the response body
            # or reinterpret an arbitrary permission rejection as authentication.
            try:
                rejection = response.json()
            except (ValueError, UnicodeError):
                rejection = None
            if isinstance(rejection, dict):
                error = rejection.get("error", rejection)
                if isinstance(error, dict) and error.get("code") == "API_KEY_EXPIRED":
                    raise ProviderAuthenticationError(
                        status_code=403, public_message="Provider API key has expired."
                    )
        try:
            self._raise_for_status(response.status_code)
        except ProviderRequestRejectedError as error:
            error.detail = self._rejection_diagnostics(response, request_id=request_id)
            raise

    def _rejection_diagnostics(
        self, response: httpx.Response, *, request_id: str
    ) -> dict[str, object]:
        """Classify a rejection in memory; retain neither body nor arbitrary fields."""
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            payload = None
        error = payload.get("error", payload) if isinstance(payload, dict) else None
        error = error if isinstance(error, dict) else {}
        categories = {
            "invalid_request_error",
            "invalid_json_schema",
            "invalid_function_parameters",
            "unsupported_parameter",
            "unsupported_value",
            "model_not_found",
            "invalid_model",
            "permission_error",
            "permission_denied",
            "insufficient_quota",
            "context_length_exceeded",
            "authentication_error",
            "invalid_api_key",
            "not_found_error",
            "upstream_error",
        }
        detail: dict[str, object] = {
            "http_status": response.status_code,
            "failure_stage": "http_status",
            "provider_error_type": error.get("type")
            if isinstance(error.get("type"), str) and error.get("type") in categories
            else "unrecognized",
            "provider_error_code": error.get("code")
            if isinstance(error.get("code"), str) and error.get("code") in categories
            else "unrecognized",
            "message_class": "unclassified_rejection",
        }
        parameter = error.get("param")
        if isinstance(parameter, str):
            for prefix in (
                "tools",
                "messages",
                "thinking",
                "reasoning_effort",
                "temperature",
                "max_tokens",
                "max_completion_tokens",
                "model",
            ):
                if parameter == prefix or parameter.startswith((prefix + ".", prefix + "[")):
                    detail["parameter_class"] = prefix
                    break
        message = error.get("message")
        if not isinstance(message, str) and isinstance(payload, dict):
            message = payload.get("detail", payload.get("message"))
        message = (
            message.lower()
            if isinstance(message, str)
            else response.text[:8192].lower()
            if not isinstance(payload, dict)
            else ""
        )
        if any(word in message for word in ("quota", "credit", "balance", "额度", "余额")) and any(
            word in message
            for word in ("precharge", "pre-charge", "pre-consume", "reserve", "预扣", "预留")
        ):
            detail["message_class"] = "quota_precharge_rejected"
        elif "only https requests are allowed" in message or "https is required" in message:
            detail["message_class"] = "https_required"
        elif "ip" in message and ("not allowed" in message or "denied" in message):
            detail["message_class"] = "ip_policy_denied"
        elif "cloudflare" in message or "web application firewall" in message:
            detail["message_class"] = "waf_rejected"
        elif detail["provider_error_code"] in {
            "invalid_json_schema",
            "invalid_function_parameters",
        } or ("tool" in message and any(word in message for word in ("schema", "invalid", "must"))):
            detail["message_class"] = "invalid_tool_schema"
        elif "role" in message or "tool_call_id" in message:
            detail["message_class"] = "invalid_message_roles"
        elif "unsupported" in message or "not supported" in message:
            detail["message_class"] = "unsupported_parameter"
        elif "context length" in message or "context_length" in message:
            detail["message_class"] = "context_length_exceeded"
        elif response.status_code == 404:
            detail["message_class"] = "route_or_model_not_found"
        elif response.status_code == 403:
            detail["message_class"] = "access_denied"
        elif not isinstance(payload, dict):
            detail["message_class"] = "non_json_rejection"
        for name, value in (
            ("request_id", request_id),
            (
                "correlation_id",
                response.headers.get("x-request-id")
                or response.headers.get("x-correlation-id")
                or response.headers.get("request-id"),
            ),
        ):
            if (
                isinstance(value, str)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value)
                and self._redactor.text(value) == value
                and not value.lower().startswith(("sk-", "key_", "tsk_", "bearer"))
            ):
                detail[name] = value
        return detail

    def _request_identity_header(self, request: ProviderRequest) -> dict[str, str]:
        value = request.request_id.value
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value)
            and self._redactor.text(value) == value
            and not value.lower().startswith(("sk-", "key_", "tsk_", "bearer"))
        ):
            return {"X-Client-Request-Id": value}
        return {}

    async def _stream_once(self, request: ProviderRequest) -> ProviderResponse:
        accumulator = ChatStream(on_content=self._content_sink(request))
        timeout = cast(httpx.Timeout, self._timeout)
        first_timeout = _STREAM_FIRST_BYTE_TIMEOUT_SECONDS if timeout.read is None else timeout.read
        async with self._client.stream(
            "POST",
            self._endpoint,
            headers={
                "Authorization": f"Bearer {self._secret.reveal()}",
                "Content-Type": "application/json",
                **self._request_identity_header(request),
            },
            json={
                **self._request_payload(request),
                "stream": True,
                "stream_options": {"include_usage": True},
            },
            timeout=timeout,
        ) as response:
            if response.status_code >= 400:
                await response.aread()
            self._check_response_status(response, request_id=request.request_id.value)
            try:
                if (
                    response.headers.get("content-type", "").split(";", 1)[0].strip()
                    != "text/event-stream"
                ):
                    raise ProviderProtocolError(
                        public_message="invalid chat stream: not text/event-stream"
                    )
                # Preserve the caller's first-response budget at both HTTP and byte layers.
                # Once any raw byte arrives, enforce the shorter byte-idle budget.
                # Keep httpx's incremental text/line decoding and response close semantics.
                response.stream = _IdleTimeoutStream(
                    cast(httpx.AsyncByteStream, response.stream),
                    min(first_timeout, _STREAM_IDLE_TIMEOUT_SECONDS),
                    first_timeout=first_timeout,
                )
                async for line in response.aiter_lines():
                    accumulator.line(line)
                    if accumulator.done:
                        break
                payload = accumulator.payload()
            except httpx.TimeoutException:
                # A timeout keeps an UNKNOWN handed-off charge, conservatively bounded
                # by the existing admission cap; never misclassify it as a protocol error.
                raise
            except (ProviderProtocolError, httpx.RequestError) as error:
                # A valid final usage chunk remains billed even if framing fails.
                try:
                    usage = self._parse_usage(accumulator.usage)
                except ProviderProtocolError:
                    usage = None
                if usage is not None:
                    cause = (
                        error
                        if isinstance(error, ProviderProtocolError)
                        else ProviderProtocolError()
                    )
                    raise _ProtocolErrorWithUsage(usage, cause, None) from None
                raise
            return self._parse_response(request, payload, response)

    def _content_sink(self, request: ProviderRequest) -> Callable[[str], None] | None:
        sink = self.delta_sink
        if sink is None:
            return None

        def forward(text: str) -> None:
            try:
                sink(request, text)
            except Exception:  # noqa: BLE001 - 预览回调出错不碰这次调用
                logger.warning("delta sink failed", exc_info=True)

        return forward

    def _request_payload(self, request: ProviderRequest) -> dict[str, Any]:
        payload = openai_chat_request_payload(
            request,
            model=self._target.model,
            tool_schema_mode=self._tool_schema_mode,
            thinking=self._thinking,
            reasoning_effort=self._reasoning_effort,
        )
        if self._extra_body:
            payload.update(self._extra_body)
        return payload

    @staticmethod
    def _payload_for_model(
        request: ProviderRequest, model: str, *, tool_schema_mode: str = LEGACY_TOOL_SCHEMA_MODE
    ) -> dict[str, Any]:
        if tool_schema_mode not in (LEGACY_TOOL_SCHEMA_MODE, DEEPSEEK_STRICT_TOOL_SCHEMA_MODE):
            raise ValueError("unsupported tool_schema_mode")
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                OpenAICompatibleProvider._message_payload(message) for message in request.messages
            ],
        }
        if request.tools:
            if tool_schema_mode == LEGACY_TOOL_SCHEMA_MODE:
                payload["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": _plain_mapping(tool.parameters),
                        },
                    }
                    for tool in request.tools
                ]
            else:
                payload["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": compile_deepseek_strict_schema(
                                _plain_mapping(tool.parameters)
                            ),
                            "strict": True,
                        },
                    }
                    for tool in request.tools
                ]
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        return payload

    @staticmethod
    def _message_payload(message: Message) -> dict[str, Any]:
        role = message.role.value if isinstance(message.role, MessageRole) else str(message.role)
        payload: dict[str, Any] = {
            "role": role,
            "content": (
                message.content
                if isinstance(message.content, str)
                else [block.to_dict() for block in message.content]
            ),
        }
        if message.name is not None:
            payload["name"] = message.name
        if message.call_id is not None:
            payload["tool_call_id"] = message.call_id.value
        # An assistant message may carry its issued tool calls in metadata
        # (``provider_tool_calls``: [{id, name, arguments}]); OpenAI-compatible
        # endpoints reject a following ``tool`` message without them.
        restored = message.metadata.get("provider_tool_calls") if role == "assistant" else None
        if isinstance(restored, (list, tuple)) and restored:
            tool_calls = [
                {
                    "id": str(call["id"]),
                    "type": "function",
                    "function": {
                        "name": str(call["name"]),
                        "arguments": json.dumps(
                            _json_value(call.get("arguments", {})), ensure_ascii=False
                        ),
                    },
                }
                for call in restored
                if isinstance(call, Mapping) and "id" in call and "name" in call
            ]
            if tool_calls:  # never emit an empty array (some endpoints reject it)
                payload["tool_calls"] = tool_calls
        # Thinking mode: the wire restores the assistant's own earlier reasoning on a
        # request copy; it is replayed verbatim (the provider rejects a tool loop without it).
        reasoning = message.metadata.get(PROVIDER_REASONING_KEY) if role == "assistant" else None
        if isinstance(reasoning, str):
            payload["reasoning_content"] = reasoning
        return payload

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        if status_code == 401:
            raise ProviderAuthenticationError(status_code=status_code)
        if status_code == 402:
            raise ProviderPaymentRequiredError(status_code=status_code)
        if status_code == 408:
            raise ProviderTimeoutError(status_code=status_code)
        if status_code == 429:
            raise ProviderRateLimitError(status_code=status_code)
        if 500 <= status_code < 600:
            raise ProviderServerError(status_code=status_code)
        raise ProviderRequestRejectedError(status_code=status_code)

    def _parse_response(
        self,
        request: ProviderRequest,
        payload: Any,
        response: httpx.Response,
    ) -> ProviderResponse:
        try:
            return self._parse_response_payload(request, payload, response)
        except ProviderProtocolError as error:
            # Parsing a malformed tool call must not turn a known billed response
            # into unknown spend. Never infer usage from text, caps or estimates.
            try:
                usage = (
                    self._parse_usage(payload.get("usage"))
                    if isinstance(payload, Mapping)
                    else None
                )
            except ProviderProtocolError:
                usage = None
            if usage is not None:
                raise _ProtocolErrorWithUsage(
                    usage,
                    error,
                    self._diagnostic_finish_reason(payload),
                ) from None
            raise

    @staticmethod
    def _diagnostic_finish_reason(payload: Any) -> str | None:
        """Return only a recognized finish reason from the rejected response."""
        if not isinstance(payload, Mapping):
            return None
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        choice = choices[0]
        if not isinstance(choice, Mapping):
            return None
        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason in _DIAGNOSTIC_FINISH_REASONS:
            return finish_reason
        return None

    def _parse_response_payload(
        self,
        request: ProviderRequest,
        payload: Any,
        response: httpx.Response,
    ) -> ProviderResponse:
        if not isinstance(payload, Mapping):
            raise ProviderProtocolError(public_message="invalid chat response: not an object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderProtocolError(public_message="invalid chat response: no choices")
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise ProviderProtocolError(
                public_message="invalid chat response: choice is not an object"
            )
        raw_message = choice.get("message")
        if not isinstance(raw_message, Mapping):
            raise ProviderProtocolError(
                public_message="invalid chat response: message is not an object"
            )
        content = raw_message.get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise ProviderProtocolError(
                public_message="invalid chat response: content is not a string"
            )

        tool_calls = self._parse_tool_calls(raw_message.get("tool_calls", []))
        reasoning = raw_message.get("reasoning_content")
        if reasoning is not None and not isinstance(reasoning, str):
            raise ProviderProtocolError(
                public_message="invalid chat response: reasoning_content is not a string"
            )
        usage = self._parse_usage(payload.get("usage"))
        model = payload.get("model")
        if model is None or model in self._response_model_aliases:
            model = self._target.model
        finish_reason = choice.get("finish_reason")
        provider_request_id = response.headers.get("x-request-id") or payload.get("id")
        for field_value in (model, finish_reason, provider_request_id):
            if field_value is not None and not isinstance(field_value, str):
                raise ProviderProtocolError(
                    public_message="invalid chat response: model/finish_reason/id is not a string"
                )
        return ProviderResponse(
            request_id=request.request_id,
            message=Message(role=MessageRole.ASSISTANT, content=content),
            tool_calls=tool_calls,
            usage=usage,
            model=model,
            finish_reason=finish_reason,
            provider_request_id=provider_request_id,
            # Kept only when this provider replays reasoning; otherwise it is dropped here,
            # exactly as before (never public, never durable).
            reasoning_content=(reasoning if self._thinking == "enabled" else None),
        )

    @staticmethod
    def _parse_tool_calls(raw_calls: Any) -> tuple[ProviderToolCall, ...]:
        if raw_calls is None:
            return ()
        if not isinstance(raw_calls, list):
            raise _ToolCallParseError(_ToolParseReason.SHAPE)
        parsed: list[ProviderToolCall] = []
        for raw_call in raw_calls:
            if not isinstance(raw_call, Mapping):
                raise _ToolCallParseError(_ToolParseReason.SHAPE)
            if raw_call.get("type") != "function":
                raise _ToolCallParseError(_ToolParseReason.TYPE)
            raw_id = raw_call.get("id")
            function = raw_call.get("function")
            if not isinstance(raw_id, str) or not raw_id:
                raise _ToolCallParseError(_ToolParseReason.ID)
            if not isinstance(function, Mapping):
                raise _ToolCallParseError(_ToolParseReason.FUNCTION)
            name = function.get("name")
            arguments = function.get("arguments")
            if not isinstance(name, str) or not name:
                raise _ToolCallParseError(_ToolParseReason.NAME)
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (TypeError, ValueError, json.JSONDecodeError):
                    raise _ToolCallParseError(_ToolParseReason.ARGUMENTS_JSON) from None
            if not isinstance(arguments, Mapping):
                raise _ToolCallParseError(_ToolParseReason.ARGUMENTS_NON_OBJECT)
            try:
                normalized = _plain_mapping(arguments)
                validate_json_value(normalized)
            except (TypeError, ValueError):
                raise _ToolCallParseError(_ToolParseReason.NORMALIZATION) from None
            try:
                call_id = CallId(raw_id)
            except (TypeError, ValueError):
                raise _ToolCallParseError(_ToolParseReason.ID) from None
            try:
                parsed.append(ProviderToolCall(call_id=call_id, name=name, arguments=normalized))
            except (TypeError, ValueError):
                raise _ToolCallParseError(_ToolParseReason.NAME) from None
        return tuple(parsed)

    @staticmethod
    def _parse_usage(raw_usage: Any) -> ProviderUsage | None:
        if raw_usage is None:
            return None
        if not isinstance(raw_usage, Mapping):
            raise ProviderProtocolError(
                public_message="invalid chat response: usage is not an object"
            )
        prompt = raw_usage.get("prompt_tokens")
        completion = raw_usage.get("completion_tokens")
        total = raw_usage.get("total_tokens")
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (prompt, completion, total)
        ):
            raise ProviderProtocolError()
        try:
            prompt_details = raw_usage.get("prompt_tokens_details")
            completion_details = raw_usage.get("completion_tokens_details")
            cache_tokens = (
                prompt_details.get("cached_tokens") if isinstance(prompt_details, Mapping) else None
            )
            reasoning_tokens = (
                completion_details.get("reasoning_tokens")
                if isinstance(completion_details, Mapping)
                else None
            )
            return ProviderUsage(
                cast(int, prompt),
                cast(int, completion),
                cast(int, total),
                cache_tokens=cache_tokens,
                reasoning_tokens=reasoning_tokens,
            )
        except ValueError:
            raise ProviderProtocolError() from None

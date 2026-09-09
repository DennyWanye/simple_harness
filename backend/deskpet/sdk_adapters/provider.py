# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Public SDK Provider bridge over the product registry and keychain."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from contextvars import ContextVar
from typing import Any, Protocol

import httpx
from simple_harness.contracts import ContractValidationError
from simple_harness.contracts.json import thaw_json, validate_json_value
from simple_harness.contracts.messages import Message, MessageRole
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
    ProviderUsage,
    Secret,
)

from deskpet.sdk_adapters.tool_call_arguments import (
    EMPTY_TOOL_CALL_ARGUMENTS_JSON,
    ToolCallArgumentsMemo,
    canonical_tool_arguments_json,
    default_tool_call_arguments_memo,
)
from deskpet.sdk_adapters.wire_input_budget import (
    ObservedInputCarryLedger,
    WireInputBudgetExceeded,
    default_observed_input_carry_ledger,
    enforce_wire_input_budget,
    resolve_window_tokens,
)

_PROVIDER_TOOL_CALLS_METADATA_KEY = "provider_tool_calls"
_PROVIDER_REASONING_CONTENT_METADATA_KEY = "provider_reasoning_content"
_PUBLIC_PROGRESS_ARGUMENT = "deskpet_public_progress"
logger = logging.getLogger(__name__)
_diagnostic_request_ref: ContextVar[str] = ContextVar("provider_diagnostic_request_ref", default="missing")
# The one parse failure that a fresh sample of the *same* request can fix: the
# model serialized a tool call whose ``arguments`` string is not JSON and that
# ``_repaired_tool_arguments`` could not repair information-preservingly.
_RESAMPLEABLE_PARSE_FAILURE_CHECK = "tool_call_arguments_not_json"
# One retry, never two (2026-09-08 A6 事件 D).
_MAX_PROVIDER_PROTOCOL_ATTEMPTS = 2


class _ToolArgumentsProtocolError(ProviderProtocolError):
    """A 200 response rejected *only* because a tool call's ``arguments`` is not JSON.

    Same public taxonomy as its base (``error_code='provider_protocol_error'``,
    ``retryable=False``): if it escapes the adapter the SDK coordinator settles
    the Run exactly as before.  The subclass exists so that
    :meth:`ProductProviderAdapter.invoke` — and nothing further out — can tell
    this one transient sampling defect apart from every other invalid response
    and draw a second sample of the same request.
    """

    __slots__ = ()


class _DiagnosticPostClient:
    """Borrow the existing HTTP client; never own, retry or close it.

    SDK status rejection precedes response parsing. Observe that one returned
    response here, while leaving SDK cancellation, taxonomy and parsing intact.
    """
    def __init__(self, client: httpx.AsyncClient, redactor: Any) -> None:
        self._client = client
        self._redactor = redactor

    async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
        response = await self._client.post(*args, **kwargs)
        if response.status_code >= 400:
            try:
                raw = response.content
                diagnostic: dict[str, Any] = {
                    "body_sha256": hashlib.sha256(raw).hexdigest(),
                    "body_bytes": len(raw),
                    "body_format": "oversized" if len(raw) > 65536 else "unstructured",
                }
                # Do not parse arbitrary-size bodies or log arbitrary JSON.
                if len(raw) <= 65536:
                    try:
                        payload = response.json()
                    except (ValueError, UnicodeError):
                        payload = None
                    error = payload.get("error") if isinstance(payload, Mapping) else None
                    if isinstance(error, Mapping):
                        diagnostic["body_format"] = "structured_error"
                        for field, limit in (("code", 128), ("type", 128), ("param", 256), ("message", 1024)):
                            value = error.get(field)
                            if isinstance(value, str):
                                # Redact before truncation, including a secret
                                # crossing the clipping boundary. JSON escaping
                                # prevents line/control-character log injection.
                                diagnostic[field] = self._redactor.text(value)[:limit]
                logger.warning(
                    "product_provider_http_rejected request_ref=%s status_code=%s diagnostic=%s",
                    _diagnostic_request_ref.get(), response.status_code,
                    json.dumps(diagnostic, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
                )
            except Exception:
                # Diagnostics must never replace the original Provider error.
                try:
                    logger.warning("product_provider_http_rejection_diagnostic_unavailable request_ref=%s status_code=%s",
                                   _diagnostic_request_ref.get(), response.status_code)
                except Exception:
                    pass
        return response


def _opaque_ref(value: object) -> str:
    """Return a bounded correlation value without exposing provider IDs/body data."""

    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def _safe_media_type(value: object) -> str:
    media_type = str(value or "").partition(";")[0].strip().lower()
    if media_type == "application/json" or media_type.endswith("+json"):
        return "json"
    if media_type.startswith("text/"):
        return "text"
    return "other" if media_type else "missing"


def _safe_finish_reason(value: object) -> str:
    reason = str(value or "").strip().lower()
    if reason in {"stop", "tool_calls", "length", "content_filter", "function_call"}:
        return reason
    return "other" if reason else "missing"


_ARGUMENTS_TRAILING_DELIMITERS = frozenset("}] \t\r\n")
# ``CallId`` (simple_harness.contracts.identity): 1-255 printable ASCII.
_CALL_ID_IDENTIFIER = re.compile(r"[!-~]{1,255}\Z")


def _sdk_provider_usage(raw_usage: Mapping[str, object]) -> ProviderUsage:
    """Build ``ProviderUsage`` exactly as the SDK's ``_parse_usage`` does."""

    prompt_details = raw_usage.get("prompt_tokens_details")
    completion_details = raw_usage.get("completion_tokens_details")
    return ProviderUsage(
        raw_usage.get("prompt_tokens"),  # type: ignore[arg-type]
        raw_usage.get("completion_tokens"),  # type: ignore[arg-type]
        raw_usage.get("total_tokens"),  # type: ignore[arg-type]
        cache_tokens=(
            prompt_details.get("cached_tokens") if isinstance(prompt_details, Mapping) else None
        ),  # type: ignore[arg-type]
        reasoning_tokens=(
            completion_details.get("reasoning_tokens")
            if isinstance(completion_details, Mapping)
            else None
        ),  # type: ignore[arg-type]
    )


def _repaired_tool_arguments(raw: str) -> tuple[str, str] | None:
    """Undo a spurious ``}`` that made a tool-call ``arguments`` string unparseable.

    Returns ``(repaired_text, reason)`` or ``None``.  Two — and only two —
    deterministic candidates are tried, both derived from the JSON prefix the
    string actually decodes to, and both **information preserving**:

    ``trailing_delimiter``
        the string decodes to a complete JSON **object** and everything after
        it is nothing but repeated closing delimiters / whitespace — that tail
        cannot encode any value (every character that could start one is
        excluded), so dropping it loses nothing;
    ``early_object_close``
        the object was closed one brace too early and the ``arguments`` then
        continue — deleting exactly that brace must make the **whole** string
        parse as one JSON object **and** every key of the already-decoded
        prefix must survive with an identical value.  Without that second
        guard a duplicate key in the tail would silently override the prefix
        (JSON keeps the last one), turning a loud failure into a wrong
        proposal — strictly worse than failing closed.

    Everything else (truncated JSON, a second independent value, trailing
    prose, a non-object top level) returns ``None`` and keeps failing closed.

    2026-09-08 native HM-TO-A6 run (DeepSeek ``deepseek-v4-pro``, real API):
    the analysis lane's ``memory_analysis_proposal`` tool call intermittently
    carries one extra ``}`` (39 live replays of the identical durable request:
    12 malformed — 11 trailing, 1 early close; ``finish_reason=tool_calls`` and
    ``completion_tokens`` ≈ 540–1250 against a 6144 budget, so it is a
    serialization defect, not truncation).  The SDK parser rejects the whole
    200 response with ``ProviderProtocolError`` (non-retryable), the analysis
    attempt fails, and the turn's user facts never become memory heads.
    """

    try:
        value, end = json.JSONDecoder().raw_decode(raw)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    remainder = raw[end:]
    if not remainder.strip():
        # Nothing to repair — plain ``json.loads`` already accepts this string.
        return None
    if not (set(remainder) - _ARGUMENTS_TRAILING_DELIMITERS):
        return raw[:end], "trailing_delimiter"
    if end >= 1 and raw[end - 1] == "}":
        candidate = raw[: end - 1] + remainder
        try:
            spliced = json.loads(candidate)
        except ValueError:
            return None
        if isinstance(spliced, dict) and all(
            key in spliced and spliced[key] == item for key, item in value.items()
        ):
            return candidate, "early_object_close"
    return None


def _json_error_kind(exc: BaseException) -> str:
    """Bounded slug of a ``json`` decode failure.

    ``JSONDecodeError.msg`` is a library template ("Extra data", "Unterminated
    string starting at", …).  Every template reachable through CPython's C
    scanner is payload-free; the pure-Python fallback (``json/decoder.py``,
    used only when ``_json`` is unavailable) embeds one ``repr``-ed character
    in two of them.  The slug is therefore stripped of everything but
    ``a-z``/``_`` and clipped, which removes any such character and also makes
    log injection impossible.
    """

    return re.sub(r"[^a-z]+", "_", str(getattr(exc, "msg", "") or "").lower()).strip("_")[:48] or "unknown"


def _parse_failure_diagnostic(
    payload: object, *, provider_request_id_header: bool = False
) -> dict[str, object]:
    """Name the SDK parser check that rejected a 200 response — payload-free.

    Mirrors ``OpenAICompatibleProvider._parse_response`` / ``_parse_tool_calls``
    in their own order, including the checks that live inside the SDK's
    constructors (``CallId``, ``validate_json_value``, ``ProviderUsage``), and
    reports only shapes, bounded slugs and integer lengths/offsets/counters.
    No content, no arguments text, no identifiers.

    ``provider_request_id_header`` says whether the response carried an
    ``x-request-id`` header: the SDK only falls back to ``payload["id"]`` when
    it did not, so a non-string ``id`` is not a failure when it is set.
    """

    if not isinstance(payload, Mapping):
        return {"check": "payload_not_mapping"}
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return {"check": "choices_not_list_or_empty"}
    choice = choices[0]
    if not isinstance(choice, Mapping):
        return {"check": "choice_not_mapping"}
    detail: dict[str, object] = {
        "check": "unclassified",
        "finish_reason": _safe_finish_reason(choice.get("finish_reason")),
    }
    usage = payload.get("usage")
    if isinstance(usage, Mapping):
        completion = usage.get("completion_tokens")
        if isinstance(completion, int) and not isinstance(completion, bool):
            detail["completion_tokens"] = completion
    raw_message = choice.get("message")
    if not isinstance(raw_message, Mapping):
        detail["check"] = "message_not_mapping"
        return detail
    content = raw_message.get("content")
    if content is not None and not isinstance(content, str):
        detail["check"] = "content_not_string"
        return detail
    raw_calls = raw_message.get("tool_calls")
    if raw_calls is not None and not isinstance(raw_calls, list):
        detail["check"] = "tool_calls_not_list"
        return detail
    for index, raw_call in enumerate(raw_calls or ()):
        detail["call_index"] = index
        if not isinstance(raw_call, Mapping) or raw_call.get("type") != "function":
            detail["check"] = "tool_call_type_not_function"
            return detail
        raw_id = raw_call.get("id")
        if not isinstance(raw_id, str) or not raw_id:
            detail["check"] = "tool_call_id_not_string"
            return detail
        if not _CALL_ID_IDENTIFIER.fullmatch(raw_id):
            # ``CallId`` requires 1-255 printable ASCII (contracts/identity.py).
            detail["check"] = "tool_call_id_not_identifier"
            return detail
        function = raw_call.get("function")
        if not isinstance(function, Mapping):
            detail["check"] = "tool_call_function_not_mapping"
            return detail
        name = function.get("name")
        if not isinstance(name, str) or not name:
            detail["check"] = "tool_call_name_not_string"
            return detail
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            detail["arguments_length"] = len(arguments)
            try:
                arguments = json.loads(arguments)
            except ValueError as exc:
                detail["check"] = "tool_call_arguments_not_json"
                detail["json_error_kind"] = _json_error_kind(exc)
                position = getattr(exc, "pos", None)
                detail["json_error_position"] = (
                    position if isinstance(position, int) and not isinstance(position, bool) else -1
                )
                return detail
        if not isinstance(arguments, Mapping):
            detail["check"] = "tool_call_arguments_not_object"
            return detail
        try:
            # ``_plain_mapping`` + ``validate_json_value``: ``json.loads``
            # accepts NaN/Infinity, the SDK's JSON contract does not.
            validate_json_value(thaw_json(arguments))
        except Exception:
            detail["check"] = "tool_call_arguments_json_value_invalid"
            return detail
    detail.pop("call_index", None)
    if usage is not None and not isinstance(usage, Mapping):
        detail["check"] = "usage_not_mapping"
        return detail
    if isinstance(usage, Mapping):
        if any(
            isinstance(usage.get(field), bool) or not isinstance(usage.get(field), int)
            for field in ("prompt_tokens", "completion_tokens", "total_tokens")
        ):
            detail["check"] = "usage_token_counts_not_int"
            return detail
        try:
            # ``ProviderUsage.__post_init__`` also rejects negatives, a total
            # below prompt+completion, and non-int cache/reasoning details.
            _sdk_provider_usage(usage)
        except Exception:
            detail["check"] = "usage_values_rejected"
            return detail
    fields: list[tuple[str, object]] = [("model", payload.get("model"))]
    if not provider_request_id_header:
        fields.append(("id", payload.get("id")))
    for field, value in fields:
        if value is not None and not isinstance(value, str):
            detail["check"] = f"{field}_not_string"
            return detail
    if choice.get("finish_reason") is not None and not isinstance(choice.get("finish_reason"), str):
        detail["check"] = "finish_reason_not_string"
        return detail
    return detail


def _provider_error_code(exc: BaseException) -> str:
    return str(
        getattr(getattr(exc, "code", None), "value", None)
        or getattr(exc, "code", None)
        or "provider_unclassified_error"
    )


def _provider_failure_stage(error_code: str, status_code: object) -> str:
    if error_code == "provider_timeout":
        return "http_timeout" if status_code == 408 else "transport_timeout"
    if error_code == "provider_transport_error":
        return "transport"
    if error_code in {
        "provider_authentication_failed",
        "provider_payment_required",
        "provider_rate_limited",
        "provider_server_error",
        "provider_request_rejected",
    }:
        return "http_status"
    if error_code == "provider_protocol_error":
        return "response_protocol"
    return "adapter"


def _request_diagnostic_summary(request: ProviderRequest) -> dict[str, object]:
    roles = Counter(message.role.value for message in request.messages)
    # Wire-shape trace (roles only, no content): an OpenAI-compatible endpoint
    # rejects a tool message whose preceding assistant lost its tool_calls, and
    # that only shows up on a real provider round trip.
    logger.debug(
        "product_provider_wire_shape %s",
        " ".join(
            "{}{}{}".format(
                m.role.value,
                "+tc" if (m.metadata or {}).get(_PROVIDER_TOOL_CALLS_METADATA_KEY) else "",
                "+cid" if m.call_id is not None else "",
            )
            for m in request.messages
        ),
    )
    tool_names = sorted(tool.name for tool in request.tools)
    tool_schema_bytes = sum(
        len(
            json.dumps(
                thaw_json(tool.parameters),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        for tool in request.tools
    )
    return {
        "message_count": len(request.messages),
        "system_messages": roles.get("system", 0),
        "user_messages": roles.get("user", 0),
        "assistant_messages": roles.get("assistant", 0),
        "tool_messages": roles.get("tool", 0),
        "tool_count": len(tool_names),
        "tool_names_digest": _opaque_ref("\n".join(tool_names)),
        "tool_schema_bytes": tool_schema_bytes,
        "max_output_tokens": request.max_output_tokens,
        "temperature_set": request.temperature is not None,
    }


def _response_shape(payload: object) -> dict[str, object]:
    choices = payload.get("choices") if isinstance(payload, Mapping) else None
    choice = choices[0] if isinstance(choices, list) and choices else None
    message = choice.get("message") if isinstance(choice, Mapping) else None
    raw_calls = message.get("tool_calls") if isinstance(message, Mapping) else None
    return {
        "payload_mapping": isinstance(payload, Mapping),
        "choices_kind": type(choices).__name__,
        "choice_count": len(choices) if isinstance(choices, list) else -1,
        "message_mapping": isinstance(message, Mapping),
        "content_kind": (
            type(message.get("content")).__name__
            if isinstance(message, Mapping)
            else "missing"
        ),
        "tool_calls_kind": type(raw_calls).__name__,
        "tool_call_count": len(raw_calls) if isinstance(raw_calls, list) else -1,
        "usage_mapping": isinstance(
            payload.get("usage") if isinstance(payload, Mapping) else None,
            Mapping,
        ),
    }


class _ProductOpenAICompatibleProvider(OpenAICompatibleProvider):
    """Restore OpenAI tool-call history retained in SDK message metadata."""

    def __init__(
        self,
        *args: Any,
        reasoning_wire: Mapping[str, object] | None = None,
        reasoning_preserve: str = "tool_loop",
        tool_call_arguments_memo: ToolCallArgumentsMemo | None = None,
        wire_input_budget_window: int | None = None,
        observed_input_carry: ObservedInputCarryLedger | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._reasoning_wire = dict(reasoning_wire or {})
        # 事件 Y: 这个型号的 reasoning 回传契约(``provider_capabilities`` 的
        # ``preserve_reasoning``)决定哪些回传是无条件可丢的。未声明的型号按
        # 最保守的 ``tool_loop`` 走。
        self._reasoning_preserve = str(reasoning_preserve or "tool_loop")
        self._tool_call_arguments = (
            tool_call_arguments_memo
            if tool_call_arguments_memo is not None
            else default_tool_call_arguments_memo()
        )
        # 事件 W: 这条闸门量的是**这里装配出来的 payload**,不是 request.messages
        # —— 差别正是 _wire_messages 补回的 assistant tool_calls.arguments。
        self._wire_input_budget_window = wire_input_budget_window
        self._observed_input_carry = (
            observed_input_carry
            if observed_input_carry is not None
            else default_observed_input_carry_ledger()
        )
        self._client = _DiagnosticPostClient(self._client, self._redactor)

    async def _post_once(self, request: ProviderRequest) -> ProviderResponse:
        token = _diagnostic_request_ref.set(_opaque_ref(request.request_id.value))
        try:
            return await super()._post_once(request)
        finally:
            _diagnostic_request_ref.reset(token)

    def _request_payload(self, request: ProviderRequest) -> dict[str, Any]:
        payload = super()._request_payload(request)
        payload["messages"] = self._wire_messages(
            request.messages, arguments_memo=self._tool_call_arguments
        )
        payload.update(self._reasoning_wire)
        # Preserve Chat Completions' non-strict optional-field contract
        # explicitly on the physical wire; do not rewrite schemas or arguments.
        for tool in payload.get("tools", ()):
            if tool.get("type") == "function":
                tool["function"]["strict"] = False
        # 事件 W(2026-09-09): 物理请求成型后、任何字节离开 Host 之前的最后一道
        # 闸门。装配期的预算闸门按 request.messages 估算,看不到上面刚补回的
        # tool_calls.arguments,也看不到 _message_payload 回灌的 reasoning_content;
        # 这里用同一条 Run 上一次**真实** usage 推出的 carry 做实测下界,越界即
        # fail closed(sdk_provider_wire_input_budget_exceeded)。
        #
        # 事件 Y(2026-09-09): 判之前先按型号声明的回传契约把 reasoning_content
        # 收敛到必要范围(工具循环之外的无条件丢;循环内的只在越界时由老到新丢),
        # 并把这块质量**直接**计进 wire —— 它一直在 Host 手里,不该继续当隐藏
        # 质量去猜。payload["messages"] 在这里被就地改写,量的与发的是同一份。
        enforce_wire_input_budget(
            request_id=request.request_id,
            payload=payload,
            tool_specs=request.tools or (),
            window_tokens=self._wire_input_budget_window,
            # 请求自己带的窗口优先于适配器构造时解析的型号默认值 —— 判的必须
            # 是这条请求**被装配时**用的那个窗口。
            request_metadata=getattr(request, "metadata", None),
            model_id=payload.get("model"),
            ledger=self._observed_input_carry,
            reasoning_preserve=self._reasoning_preserve,
        )
        return payload

    @classmethod
    def _wire_messages(
        cls,
        messages: Sequence[Message],
        *,
        arguments_memo: ToolCallArgumentsMemo | None = None,
    ) -> list[dict[str, Any]]:
        """Assemble the wire ``messages`` array, restoring assistant tool_calls.

        S5A-UI-F2 (2026-09-02, real desktop UI): an OpenAI-compatible endpoint
        rejects a ``tool`` message whose preceding ``assistant`` carries no
        ``tool_calls`` ("function_call_output requires item_reference ids
        matching each call_id on HTTP requests").  The SDK 0.7.1 contract keeps
        durable Context free of private provider metadata — a provider
        assistant message is stored with empty metadata by construction — so
        ``metadata[provider_tool_calls]`` cannot survive a continuation and the
        second turn of every tool-using chat used to fail closed with HTTP 400.

        The durable messages themselves carry the missing facts: each tool
        result holds its ``call_id`` and tool ``name``.  Rebuild the assistant
        ``tool_calls`` from the tool results that follow it; the arguments come
        back from the Host's own ``ToolCallArgumentsMemo``, which retained them
        when this process parsed the response that issued the call.

        HM-TO-A6 incident K (2026-09-08): before the memo existed the rebuild
        used a literal ``"{}"``, and the measured evidence shows that was not a
        harmless shape degradation.  ``metadata[provider_tool_calls]`` survives
        in **zero** durable requests, so every assistant ``tool_calls`` on the
        wire was the ``{}`` rebuild, and the model imitated its own corrupted
        transcript: pooled over three native DeepSeek runs, requests with no
        rebuilt call emitted 0/16 empty-argument tool calls while requests with
        20+ rebuilt calls emitted 22/35, each rejected
        ``missing_required_argument`` until ``react_max_turns_exceeded``.

        A memo miss still degrades to ``"{}"`` — a valid wire shape beats a dead
        Run — but it is counted in a payload-free log line instead of passing
        silently.  Ambiguity fails closed the same way: a raw provider
        ``call_id`` is only unique within one ``(run, turn, ordinal)`` (see
        ``react_loop.py::_internal_effect_identity``), so an id repeated inside
        one request degrades for every occurrence.  Fabricating a plausible but
        wrong call/result pairing would feed the very imitation channel this
        repair closes.

        S5b upstream obligation (unchanged): the clean fix is upstream — treat an
        assistant's ``tool_calls`` as a first-class public transcript field (it is
        part of the conversation, not provider-private metadata) and re-attach it
        when the Context rebuilds a request.  The memo is a process-local Host
        repair, so a cold restart mid-conversation still degrades; remove both
        once the SDK carries the field.
        """

        memo = (
            arguments_memo
            if arguments_memo is not None
            else default_tool_call_arguments_memo()
        )
        # A raw provider call_id is only unique within one (run, turn, ordinal)
        # — the SDK hashes exactly those into its internal CallId
        # (``react_loop.py::_internal_effect_identity``), and index-style ids
        # (``call_0``, ``call_1``) restart every turn on several
        # OpenAI-compatible endpoints. An id repeated inside one request is
        # therefore unresolvable: fall back for every occurrence rather than
        # staple one turn's arguments above another turn's result.
        repeated = {
            call_id
            for call_id, count in Counter(
                message.call_id.value
                for message in messages
                if message.role is MessageRole.TOOL and message.call_id is not None
            ).items()
            if count > 1
        }
        payloads = [cls._message_payload(message) for message in messages]
        restored_total = 0
        fallback_total = 0
        ambiguous_total = 0
        for index, message in enumerate(messages):
            if message.role is not MessageRole.ASSISTANT:
                continue
            if payloads[index].get("tool_calls"):
                continue
            followers: list[dict[str, Any]] = []
            for follower in messages[index + 1 :]:
                if follower.role is not MessageRole.TOOL:
                    break
                # call_id 必然存在：Message 契约对 TOOL 角色强制要求它
                # （contracts/messages.py "tool message requires call_id"）。
                name = str(follower.name or "unknown")
                call_id = follower.call_id.value
                if call_id in repeated:
                    restored = None
                    ambiguous_total += 1
                    fallback_total += 1
                else:
                    restored = memo.read(call_id, name)
                    if restored is None:
                        fallback_total += 1
                    else:
                        restored_total += 1
                followers.append(
                    {
                        "id": follower.call_id.value,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": (
                                EMPTY_TOOL_CALL_ARGUMENTS_JSON
                                if restored is None
                                else restored
                            ),
                        },
                    }
                )
            if followers:
                payloads[index]["tool_calls"] = followers
        if fallback_total:
            # Counts only — never a call id, tool name or argument value.
            logger.warning(
                "product_provider_tool_call_arguments_unavailable "
                "request_ref=%s rebuilt=%s restored=%s fallback_empty=%s "
                "ambiguous_call_ids=%s",
                _diagnostic_request_ref.get(),
                restored_total + fallback_total,
                restored_total,
                fallback_total,
                ambiguous_total,
            )
        return payloads

    @staticmethod
    def _message_payload(message: Message) -> dict[str, Any]:
        payload = OpenAICompatibleProvider._message_payload(message)
        content = payload.get("content")
        if isinstance(content, list):
            provider_content: list[dict[str, Any]] = []
            for raw_block in content:
                if not isinstance(raw_block, Mapping):
                    continue
                block = dict(raw_block)
                if block.get("type") == "input_text" and isinstance(
                    block.get("data"), str
                ):
                    name = str(block.get("name") or "attachment.txt").strip()
                    provider_content.append({
                        "type": "text",
                        "text": (
                            f'Attached text file "{name}":\n'
                            + str(block["data"])
                        ),
                    })
                else:
                    provider_content.append(block)
            payload["content"] = provider_content
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
                        # One serialisation for both paths (live metadata here,
                        # memo restore in ``_wire_messages``) so a restored call
                        # is byte-identical to the same-process one.
                        "arguments": canonical_tool_arguments_json(
                            thaw_json(arguments) if isinstance(arguments, Mapping) else {}
                        ),
                    },
                }
            )
        if calls:
            payload["tool_calls"] = calls
        return payload

    @staticmethod
    def _normalized_tool_arguments(payload: Any, request_ref: str) -> Any:
        """Repair the DeepSeek spurious-``}`` ``arguments`` defect, nothing else.

        A well-formed response is returned unchanged (identity), because a
        repair is attempted only for an ``arguments`` string that ``json.loads``
        already rejects.  Only ``choices[0]`` is inspected — the SDK parser
        consumes no other choice.
        """

        if not isinstance(payload, Mapping):
            return payload
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return payload
        choice = choices[0]
        if not isinstance(choice, Mapping):
            return payload
        raw_message = choice.get("message")
        if not isinstance(raw_message, Mapping):
            return payload
        raw_calls = raw_message.get("tool_calls")
        if not isinstance(raw_calls, list):
            return payload

        repaired_calls = list(raw_calls)
        repaired = False
        for index, raw_call in enumerate(raw_calls):
            if not isinstance(raw_call, Mapping):
                continue
            function = raw_call.get("function")
            if not isinstance(function, Mapping):
                continue
            arguments = function.get("arguments")
            if not isinstance(arguments, str):
                continue
            try:
                json.loads(arguments)
                continue
            except ValueError:
                pass
            outcome = _repaired_tool_arguments(arguments)
            if outcome is None:
                continue
            candidate, reason = outcome
            repaired_function = dict(function)
            repaired_function["arguments"] = candidate
            repaired_call = dict(raw_call)
            repaired_call["function"] = repaired_function
            repaired_calls[index] = repaired_call
            repaired = True
            logger.warning(
                "product_provider_tool_arguments_repaired "
                "request_ref=%s call_index=%s arguments_length=%s repaired_length=%s "
                "reason=%s",
                request_ref,
                index,
                len(arguments),
                len(candidate),
                reason,
            )
        if not repaired:
            return payload
        repaired_message = dict(raw_message)
        repaired_message["tool_calls"] = repaired_calls
        repaired_choice = dict(choice)
        repaired_choice["message"] = repaired_message
        repaired_payload = dict(payload)
        repaired_payload["choices"] = [repaired_choice, *choices[1:]]
        return repaired_payload

    def _parse_response(
        self,
        request: ProviderRequest,
        payload: Any,
        response: httpx.Response,
    ) -> ProviderResponse:
        request_ref = _opaque_ref(request.request_id.value)
        shape = _response_shape(payload)
        logger.info(
            "product_provider_http_response_received "
            "request_ref=%s status_code=%s response_bytes=%s media_type=%s "
            "provider_request_ref=%s payload_mapping=%s choices_kind=%s "
            "choice_count=%s message_mapping=%s content_kind=%s "
            "tool_calls_kind=%s tool_call_count=%s usage_mapping=%s",
            request_ref,
            response.status_code,
            len(response.content),
            _safe_media_type(response.headers.get("content-type")),
            _opaque_ref(response.headers.get("x-request-id"))
            if response.headers.get("x-request-id")
            else "missing",
            shape["payload_mapping"],
            shape["choices_kind"],
            shape["choice_count"],
            shape["message_mapping"],
            shape["content_kind"],
            shape["tool_calls_kind"],
            shape["tool_call_count"],
            shape["usage_mapping"],
        )
        try:
            payload = self._normalized_tool_arguments(payload, request_ref)
        except Exception:
            # Normalization is best effort; it must never pre-empt the SDK's
            # own taxonomy (or escape unlogged, ahead of the try below).
            logger.warning(
                "product_provider_tool_arguments_normalization_unavailable request_ref=%s",
                request_ref,
            )
        try:
            parsed = super()._parse_response(request, payload, response)
        except Exception as exc:
            detail: dict[str, object] = {"check": "diagnostic_unavailable"}
            try:
                detail = _parse_failure_diagnostic(
                    payload,
                    provider_request_id_header=bool(response.headers.get("x-request-id")),
                )
                diagnostic = json.dumps(
                    detail, ensure_ascii=True, sort_keys=True, separators=(",", ":")
                )
            except Exception:
                # Diagnostics must never replace the original Provider error.
                detail = {"check": "diagnostic_unavailable"}
                diagnostic = '{"check":"diagnostic_unavailable"}'
            logger.warning(
                "product_provider_response_parse_failed "
                "request_ref=%s status_code=%s error_type=%s error_code=%s diagnostic=%s",
                request_ref,
                response.status_code,
                type(exc).__name__,
                _provider_error_code(exc),
                diagnostic,
            )
            if (
                isinstance(exc, ProviderProtocolError)
                and detail.get("check") == _RESAMPLEABLE_PARSE_FAILURE_CHECK
            ):
                # Narrow the taxonomy for the adapter's resample decision only;
                # the public error_code and retryable flag are unchanged.
                raise _ToolArgumentsProtocolError(private_cause=exc) from None
            raise
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


def _retain_tool_calls_in_message(
    response: ProviderResponse,
    *,
    arguments_memo: ToolCallArgumentsMemo | None = None,
) -> ProviderResponse:
    if not response.tool_calls:
        return response
    memo = (
        arguments_memo
        if arguments_memo is not None
        else default_tool_call_arguments_memo()
    )
    metadata = dict(response.message.metadata)
    retained: list[dict[str, Any]] = []
    for call in response.tool_calls:
        # ProviderToolCall freezes nested JSON objects/arrays as
        # mappingproxy/tuple. Message freezes metadata again, so a shallow
        # dict() copy leaves unsupported frozen containers below the first
        # level. Thaw the full JSON tree before crossing that contract.
        arguments = thaw_json(call.arguments)
        retained.append(
            {"id": call.call_id.value, "name": call.name, "arguments": arguments}
        )
        # HM-TO-A6 incident K: the same object must survive the durable Context
        # round trip, which strips this metadata by SDK contract. The memo is
        # the Host's own copy, read back by ``_wire_messages`` on every later
        # turn of this Run. It holds exactly what is retained here — the model's
        # arguments after ``_extract_public_progress`` removed the Host-internal
        # narration field, i.e. the same object the durable effect ledger stores
        # for this call (``tools/executor.py`` thaws the very same ToolCall
        # arguments into ``execution_effects.arguments_json``; only the JSON
        # escaping differs). No redacted record is re-exposed.
        memo.record(call.call_id.value, call.name, arguments)
    metadata[_PROVIDER_TOOL_CALLS_METADATA_KEY] = retained
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
        thawed_arguments = thaw_json(call.arguments)
        assert isinstance(thawed_arguments, dict)
        arguments = thawed_arguments
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


def provider_endpoint_identity(entry: Any, *, base_url: str | None = None) -> str:
    """Immutable endpoint identity of one registry entry (base_url + config_revision + incarnation).

    Shared by ``ProductProviderAdapter`` and the S5b Task 4 Memory-analysis
    lineage (``model_config_hash``), so the terminal outbox row and the attempt
    ledger derive the same value from the same durable facts.
    """

    resolved = str(base_url if base_url is not None else getattr(entry, "base_url", "")).strip().rstrip("/")
    endpoint_payload = {
        "base_url": resolved,
        "config_revision": int(getattr(entry, "config_revision", 0) or 0),
        "incarnation_id": str(getattr(entry, "incarnation_id", "")),
    }
    return hashlib.sha256(
        json.dumps(endpoint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _reasoning_params(
    model: str, model_params: Mapping[str, object] | None
) -> Mapping[str, object] | None:
    """会话的 ``model_params`` 补上该型号在 ``model_overrides.toml`` 里的默认。

    事件 Y(2026-09-09):旅程/测试需要一个「关掉 thinking」的开关,但那是
    **运行配置**,不该逼调用方改代码或改会话协议。这里只在会话**完全没有**
    表态时(既没 ``reasoning_mode``、也没 ``thinking``/``fast``)才填,所以
    用户在「模型与参数」面板选的东西永远赢。

    默认 ``reasoning_mode = "default"`` → 返回原对象,一个字段都不加,
    行为与事件 Y 之前逐 token 相同。
    """

    if isinstance(model_params, Mapping) and (
        model_params.get("reasoning_mode") is not None
        or model_params.get("thinking") is not None
        or model_params.get("fast") is not None
    ):
        return model_params
    try:
        from llm.model_info import resolve_reasoning_mode

        mode = resolve_reasoning_mode(model)
    except Exception:  # noqa: BLE001 — 元数据永远不该打断 provider 构造
        return model_params
    if mode == "default":
        return model_params
    merged = dict(model_params or {})
    merged["reasoning_mode"] = mode
    return merged


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
        # HTTP 传输超时是**防挂死的安全网**，不是语义闸门：语义切断由各车道自己的
        # deadline 负责（analysis 车道 = AnalysisBudget.deadline_ms，经
        # post_turn_invoker 的 asyncio.wait_for 生效；前台 = chat_turn_timeout_minutes）。
        # 因此它必须**大于任何车道的 deadline**——比车道 deadline 短就让后者永远够不着。
        # 实测教训：A16 把 analysis 预算提到 6144 token / deadline 180s，但这里仍是 60s，
        # 于是模型一写长就在 60003ms 撞 transport_timeout → sent_unknown → 记忆零物化
        # （.local-test-evidence/real-ui-channel/20260904T165832、T170608）。
        # 取 240s：高于当前最大车道 deadline（analysis 180s）并留余量。
        timeout: float = 240.0,
        pre_invoke_guard=None,
        tool_call_arguments_memo: ToolCallArgumentsMemo | None = None,
        observed_input_carry: ObservedInputCarryLedger | None = None,
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
        endpoint_identity = provider_endpoint_identity(entry, base_url=base_url)
        pricing_key = (
            f"{provider_id}:{frozen_model}:{self.price_snapshot.fingerprint}"
        )
        from llm.provider_capabilities import (
            declared_reasoning_capability,
            reasoning_wire_fields,
        )

        self.reasoning_capability = declared_reasoning_capability(frozen_model)
        self.reasoning_wire = reasoning_wire_fields(
            self.reasoning_capability,
            _reasoning_params(frozen_model, model_params),
        )
        # 事件 Y: 未声明的型号按最保守的 ``tool_loop`` 走 —— 只无条件丢掉
        # 「当前工具循环之外」的回传, 循环内的留到预算真的不够时才动。
        self._reasoning_preserve = (
            self.reasoning_capability.preserve_reasoning
            if self.reasoning_capability.behavior != "unknown"
            else "tool_loop"
        )
        # 事件 W: 闸门要的是**用户实际绑定的**窗口,所以走 llm.model_info.resolve
        # (含 model_overrides.toml 全局层) 而不是内置表——本次事故的 32000 正是
        # 全局 override 钉出来的。取不到窗口就整条闸门不判,见 wire_input_budget。
        self._wire_input_budget_window = resolve_window_tokens(frozen_model)
        self._observed_input_carry = (
            observed_input_carry
            if observed_input_carry is not None
            else default_observed_input_carry_ledger()
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
            reasoning_preserve=self._reasoning_preserve,
            tool_call_arguments_memo=tool_call_arguments_memo,
            wire_input_budget_window=self._wire_input_budget_window,
            observed_input_carry=self._observed_input_carry,
        )
        # The response parse writes this memo, the next turn's wire assembly
        # reads it (HM-TO-A6 事件 K). Production injects nothing, so this is the
        # module-level singleton shared by every adapter in the process — the
        # poison-on-conflict and repeated-id guards, not the topology, are what
        # keep a reused call_id from crossing Run or conversation boundaries.
        self._tool_call_arguments = self._delegate._tool_call_arguments
        self._pre_invoke_guard = pre_invoke_guard
        self._timeout_seconds = float(timeout)
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

    async def _invoke_with_protocol_resample(
        self,
        request: ProviderRequest,
        *,
        cancel: CancelToken,
        request_ref: str,
        started_at: float,
    ) -> ProviderResponse:
        """Draw at most one fresh sample of the *same* request after a malformed tool call.

        Scope (2026-09-08 A6 事件 D): the only retried failure is
        :class:`_ToolArgumentsProtocolError` — a 200 response whose sole defect
        is a tool-call ``arguments`` string that is not JSON and that the
        deterministic ``_repaired_tool_arguments`` repair could not fix.  With a
        sampling LLM that is a transient serialization defect, yet the SDK
        coordinator lists ``ProviderProtocolError`` among its *definite*
        failures, so one bad sample fails the whole Run.

        Why here and not deeper or further out: the SDK is frozen, and its
        ``reconcile_incomplete`` retry lane (F06,
        ``plans/2026-09-07-native-main-journey/DECISION-PROVIDER-TIMEOUT-STALL.md``)
        only ever sees UNKNOWN records — a definite protocol failure never
        reaches it.  This adapter is the last Host-owned frame before the
        coordinator, and it already owns the repair for the same defect.

        Bounds, mirroring F06's retry-once policy: at most
        ``_MAX_PROVIDER_PROTOCOL_ATTEMPTS`` attempts, no delay, the identical
        ``ProviderRequest`` (same ``request_id``), a second failure raised
        unchanged, and nothing retried once the turn is cancelled.  The retry
        lives *inside* one SDK hand-off, so the ledger keeps one invocation with
        one outcome; the discarded sample produced no parsed tool call and
        therefore no dispatched effect — its only cost is one billed completion
        (the same trade F06 accepted).
        """

        for attempt in range(1, _MAX_PROVIDER_PROTOCOL_ATTEMPTS + 1):
            try:
                return await self._delegate.invoke(request, cancel=cancel)
            except _ToolArgumentsProtocolError:
                if attempt >= _MAX_PROVIDER_PROTOCOL_ATTEMPTS or cancel.is_cancelled:
                    raise
                logger.warning(
                    "product_provider_protocol_resampled "
                    "request_ref=%s attempt=%s max_attempts=%s check=%s elapsed_ms=%s",
                    request_ref,
                    attempt,
                    _MAX_PROVIDER_PROTOCOL_ATTEMPTS,
                    _RESAMPLEABLE_PARSE_FAILURE_CHECK,
                    round((time.monotonic() - started_at) * 1000),
                )
        raise AssertionError("unreachable: the last attempt always returns or raises")

    async def invoke(
        self, request: ProviderRequest, *, cancel: CancelToken
    ) -> ProviderResponse:
        # Only this pre-delegate boundary may reject as a definite request
        # failure. Do not reclassify errors after a physical handoff.
        if self._pre_invoke_guard is not None:
            await self._pre_invoke_guard(request)
        started_at = time.monotonic()
        request_ref = _opaque_ref(request.request_id.value)
        summary = _request_diagnostic_summary(request)
        logger.info(
            "product_provider_attempt_started "
            "request_ref=%s provider_id=%s model=%s endpoint_ref=%s timeout_seconds=%s "
            "message_count=%s system_messages=%s user_messages=%s assistant_messages=%s "
            "tool_messages=%s tool_count=%s tool_names_digest=%s tool_schema_bytes=%s "
            "max_output_tokens=%s temperature_set=%s",
            request_ref,
            self.target.provider_id,
            self.target.model,
            self.target.endpoint_identity[:16],
            self._timeout_seconds,
            summary["message_count"],
            summary["system_messages"],
            summary["user_messages"],
            summary["assistant_messages"],
            summary["tool_messages"],
            summary["tool_count"],
            summary["tool_names_digest"],
            summary["tool_schema_bytes"],
            summary["max_output_tokens"],
            summary["temperature_set"],
        )
        try:
            response = await self._invoke_with_protocol_resample(
                request,
                cancel=cancel,
                request_ref=request_ref,
                started_at=started_at,
            )
        except ProviderCancelledError:
            # OpenAICompatibleProvider converts task cancellation into a
            # Provider error. At the product boundary restore cooperative
            # asyncio cancellation so the SDK driver cannot reconcile a user
            # stop as an unknown physical handoff and move the durable Run
            # back to waiting.
            logger.info(
                "product_provider_attempt_cancelled request_ref=%s elapsed_ms=%s stage=cancel",
                request_ref,
                round((time.monotonic() - started_at) * 1000),
            )
            raise asyncio.CancelledError() from None
        except WireInputBudgetExceeded as exc:
            # 事件 W: 请求在装配 payload 时就被实测闸门拦下,**一个字节都没发出**。
            # ProviderRequestRejectedError 是 SDK 的 _DEFINITE_PROVIDER_FAILURES
            # 之一,所以协调器会把它结算为确定失败而不是未知交接。
            logger.warning(
                "product_provider_attempt_failed "
                "request_ref=%s elapsed_ms=%s stage=wire_input_budget "
                "error_type=%s error_code=%s status_code=missing retryable=false "
                "floor=%s effective=%s wire=%s carry=%s ordinal=%s",
                request_ref,
                round((time.monotonic() - started_at) * 1000),
                type(exc).__name__,
                exc.error_code,
                exc.diagnostics.get("measured_input_floor"),
                exc.diagnostics.get("effective_input_budget"),
                exc.diagnostics.get("wire_input_tokens"),
                exc.diagnostics.get("observed_carry_tokens"),
                exc.diagnostics.get("provider_turn_ordinal"),
            )
            raise
        except ContractValidationError as exc:
            # OpenAI-compatible payload parsing constructs typed SDK values
            # (CallId, Message metadata, frozen JSON).  A provider-generated
            # value that violates one of those contracts is a definite invalid
            # response, not an unknown physical handoff.  Normalize it to the
            # provider protocol taxonomy so the coordinator settles the Run as
            # failed instead of leaving it in sdk_run_waiting forever.
            logger.warning(
                "product_provider_attempt_failed "
                "request_ref=%s elapsed_ms=%s stage=response_contract "
                "error_type=%s error_code=%s status_code=missing retryable=%s",
                request_ref,
                round((time.monotonic() - started_at) * 1000),
                type(exc).__name__,
                _provider_error_code(exc),
                bool(getattr(exc, "retryable", False)),
            )
            raise ProviderProtocolError(private_cause=exc) from None
        except Exception as exc:
            # The SDK coordinator deliberately converts unexpected exceptions
            # after a physical handoff into an unknown outcome.  Keep a safe,
            # payload-free breadcrumb so provider response contract drift can
            # be diagnosed without logging prompts, responses, or secrets.
            error_code = _provider_error_code(exc)
            status_code = getattr(exc, "status_code", None)
            logger.warning(
                "product_provider_attempt_failed "
                "request_ref=%s elapsed_ms=%s stage=%s error_type=%s "
                "error_code=%s status_code=%s retryable=%s",
                request_ref,
                round((time.monotonic() - started_at) * 1000),
                _provider_failure_stage(error_code, status_code),
                type(exc).__name__,
                error_code,
                status_code if status_code is not None else "missing",
                bool(getattr(exc, "retryable", False)),
            )
            raise
        try:
            response = _extract_public_progress(response)
            response = _retain_tool_calls_in_message(
                response, arguments_memo=self._tool_call_arguments
            )
        except ContractValidationError as exc:
            # Local normalization is still part of the Provider response
            # contract. A deterministic JSON-shape failure must settle as a
            # definite protocol error, never as an unknown physical handoff.
            logger.warning(
                "product_provider_attempt_failed "
                "request_ref=%s elapsed_ms=%s stage=response_contract "
                "error_type=%s error_code=%s status_code=missing retryable=false",
                request_ref,
                round((time.monotonic() - started_at) * 1000),
                type(exc).__name__,
                _provider_error_code(exc),
            )
            raise ProviderProtocolError(private_cause=exc) from None
        # 事件 W: 把这一次的真实 usage 与刚才量到的 wire 配对,成为下一轮闸门的
        # 实测底线。响应没有 usage 就不记——没有观测胜过错误的观测。
        if response.usage is not None:
            reasoning = getattr(response.usage, "reasoning_tokens", None)
            self._observed_input_carry.observe_usage(
                request.request_id,
                input_tokens=int(response.usage.input_tokens or 0),
                output_tokens=int(response.usage.output_tokens or 0),
                # 只有 reasoning 是下一轮 payload 量不到的那块; 正文与
                # tool_calls.arguments 下一轮会原样回到 wire 里(事件 K),
                # 再加一次就是重复计价。中转站不报时传 None → 退回 output。
                reasoning_tokens=None if reasoning is None else int(reasoning),
            )
        await self._capture_public_tool_narration(request, response)
        logger.info(
            "product_provider_attempt_succeeded "
            "request_ref=%s elapsed_ms=%s finish_reason=%s tool_call_count=%s "
            "content_empty=%s usage_present=%s response_model_matches=%s "
            "provider_request_ref=%s",
            request_ref,
            round((time.monotonic() - started_at) * 1000),
            _safe_finish_reason(response.finish_reason),
            len(response.tool_calls),
            not bool(response.message.content.strip()),
            response.usage is not None,
            response.model == self.target.model,
            _opaque_ref(response.provider_request_id)
            if response.provider_request_id
            else "missing",
        )
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
    """Keep Provider handoff uncertainty separate from user Run cancellation.

    S5b Task 2: for a foreground Run bound to an admission TaskScope the
    Provider invocation is a Harness fact — its ``source_sequence`` is reserved
    (``provider:{request_id}``) before the physical send and imported after
    the SDK settled the invocation.  A failed/unknown send leaves the row
    reserved; the terminal observer drains it from the SDK ledger (or writes
    a tombstone).
    """

    def __init__(self, *args: Any, evidence_ingress: Any | None = None, typed_terminal=None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._evidence_ingress = evidence_ingress
        self._typed_terminal = typed_terminal

    async def invoke(
        self,
        run_id,
        request,
        *,
        cancel,
        execution_lease,
        workflow_lease=None,
        context_use=None,
    ):
        binding = None
        ingress = getattr(self, "_evidence_ingress", None)
        if ingress is not None:
            binding = await ingress.resolve_run_scope(run_id.value)
            if binding is not None:
                await ingress.reserve(
                    run_id=run_id.value,
                    task_scope_id=binding.task_scope_id,
                    kind="provider_invocation",
                    source_event_id=f"provider:{request.request_id.value}",
                )
        try:
            response = await super().invoke(
                run_id,
                request,
                cancel=cancel,
                execution_lease=execution_lease,
                workflow_lease=workflow_lease,
                context_use=context_use,
            )
        except ProviderInvocationUnknownError:
            if cancel.is_cancelled:
                # The physical Provider invocation remains unknown for
                # reconciliation/billing, but the user-owned Run cancellation
                # is authoritative and must reach Runtime._cancel_run.
                raise asyncio.CancelledError() from None
            raise
        if binding is not None:
            await ingress.commit_fact(
                task_scope_id=binding.task_scope_id,
                subject=binding.subject,
                fact=provider_invocation_fact(run_id.value, request, response),
            )
        return response

    async def prepare_context_use_terminal(self, run_id, request, *, checkpoint, execution_lease):
        # SDK calls this after the actual successful response is checkpointed.
        await super().prepare_context_use_terminal(
            run_id, request, checkpoint=checkpoint, execution_lease=execution_lease,
        )
        if self._typed_terminal is not None:
            from simple_harness import ProviderContextUseAttemptV1
            attempt = ProviderContextUseAttemptV1.from_json(checkpoint["context_use_attempt"])
            await self._typed_terminal.record_terminal(run_id, request, attempt)

    def verify_context_use_terminal(self, run_id, request_id, *, checkpoint, execution_lease):
        view = super().verify_context_use_terminal(
            run_id, request_id, checkpoint=checkpoint, execution_lease=execution_lease,
        )
        if self._typed_terminal is not None:
            self._typed_terminal.verify_terminal(run_id, request_id, checkpoint, verified_use=view)
        return view


def provider_invocation_fact(run_id: str, request: ProviderRequest, response: ProviderResponse | None, *, error_code: str | None = None):
    """Public-only Harness fact for one settled Provider invocation."""

    from deskpet.execution.evidence_ingress import ProviderInvocationFact

    usage = None
    if response is not None and response.usage is not None:
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.total_tokens,
        }
    return ProviderInvocationFact(
        run_id=run_id,
        request_id=request.request_id.value,
        model=None if response is None else response.model,
        finish_reason=None if response is None else _safe_finish_reason(response.finish_reason),
        tool_call_count=0 if response is None else len(response.tool_calls),
        error_code=error_code,
        usage=usage,
    )


__all__ = (
    "ProductPriceSnapshot",
    "ProductProviderAdapter",
    "ProductProviderInvocationCoordinator",
    "ProductProviderRegistry",
    "provider_invocation_fact",
)

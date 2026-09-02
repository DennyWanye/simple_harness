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
from typing import Any, Protocol

import httpx
from simple_harness.contracts import ContractValidationError
from simple_harness.contracts.json import thaw_json
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
    Secret,
)

_PROVIDER_TOOL_CALLS_METADATA_KEY = "provider_tool_calls"
_PROVIDER_REASONING_CONTENT_METADATA_KEY = "provider_reasoning_content"
_PUBLIC_PROGRESS_ARGUMENT = "deskpet_public_progress"
logger = logging.getLogger(__name__)


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
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._reasoning_wire = dict(reasoning_wire or {})

    def _request_payload(self, request: ProviderRequest) -> dict[str, Any]:
        payload = super()._request_payload(request)
        payload["messages"] = self._wire_messages(request.messages)
        payload.update(self._reasoning_wire)
        return payload

    @classmethod
    def _wire_messages(cls, messages: Sequence[Message]) -> list[dict[str, Any]]:
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
        ``tool_calls`` from the tool results that follow it; original arguments
        are used when the live metadata is still present (same-process turn)
        and degrade to an empty JSON object otherwise, which keeps the wire
        shape valid instead of killing the Run.
        """

        payloads = [cls._message_payload(message) for message in messages]
        for index, message in enumerate(messages):
            if message.role is not MessageRole.ASSISTANT:
                continue
            if payloads[index].get("tool_calls"):
                continue
            followers: list[dict[str, Any]] = []
            for follower in messages[index + 1 :]:
                if follower.role is not MessageRole.TOOL:
                    break
                if follower.call_id is None:
                    continue
                followers.append(
                    {
                        "id": follower.call_id.value,
                        "type": "function",
                        "function": {
                            "name": str(follower.name or "unknown"),
                            "arguments": "{}",
                        },
                    }
                )
            if followers:
                payloads[index]["tool_calls"] = followers
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
            parsed = super()._parse_response(request, payload, response)
        except Exception as exc:
            logger.warning(
                "product_provider_response_parse_failed "
                "request_ref=%s status_code=%s error_type=%s error_code=%s",
                request_ref,
                response.status_code,
                type(exc).__name__,
                _provider_error_code(exc),
            )
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


def _retain_tool_calls_in_message(response: ProviderResponse) -> ProviderResponse:
    if not response.tool_calls:
        return response
    metadata = dict(response.message.metadata)
    metadata[_PROVIDER_TOOL_CALLS_METADATA_KEY] = [
        {
            "id": call.call_id.value,
            "name": call.name,
            # ProviderToolCall freezes nested JSON objects/arrays as
            # mappingproxy/tuple. Message freezes metadata again, so a shallow
            # dict() copy leaves unsupported frozen containers below the first
            # level. Thaw the full JSON tree before crossing that contract.
            "arguments": thaw_json(call.arguments),
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

    async def invoke(
        self, request: ProviderRequest, *, cancel: CancelToken
    ) -> ProviderResponse:
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
            logger.info(
                "product_provider_attempt_cancelled request_ref=%s elapsed_ms=%s stage=cancel",
                request_ref,
                round((time.monotonic() - started_at) * 1000),
            )
            raise asyncio.CancelledError() from None
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
            response = _retain_tool_calls_in_message(response)
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

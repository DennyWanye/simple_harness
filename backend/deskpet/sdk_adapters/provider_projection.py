# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Crash-safe projection boundary for SDK provider settlement envelopes.

The boundary intentionally accepts mappings or attribute-bearing v0.1.5 SDK
objects.  It does not infer absent usage fields: only a complete, successful
measurement can advance Context Usage authority.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping


def _read(value: object, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _required(value: object, key: str) -> str:
    text = str(_read(value, key, "") or "").strip()
    if not text:
        raise ValueError(f"{key} is required")
    return text


def _non_negative_int(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class ProviderProjectionEnvelopeV1:
    invocation_id: str
    settlement_version: int
    settled_at: float
    session_id: str
    root_run_id: str
    request_id: str
    snapshot_id: str
    state: str
    provider_id: str
    model_id: str
    binding_epoch: int
    context_window: int
    effective_ceiling: int
    usage: Mapping[str, int | None] | None
    provider_request_id: str | None = None
    error_code: str | None = None
    handoff_attempt: int | None = None

    @classmethod
    def from_value(cls, value: object) -> "ProviderProjectionEnvelopeV1":
        state = _required(value, "state").lower()
        if state not in {"succeeded", "failed", "cancelled", "unknown"}:
            raise ValueError("unsupported provider settlement state")
        usage_value = _read(value, "usage")
        usage: dict[str, int | None] | None = None
        if usage_value is not None:
            usage = {}
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                usage[key] = _non_negative_int(_read(usage_value, key), key)
            for key in ("cache_tokens", "reasoning_tokens"):
                raw = _read(usage_value, key)
                usage[key] = None if raw is None else _non_negative_int(raw, key)
        return cls(
            invocation_id=_required(value, "invocation_id"),
            settlement_version=_non_negative_int(
                _read(value, "settlement_version"), "settlement_version"
            ),
            settled_at=float(_read(value, "settled_at")),
            session_id=_required(value, "session_id"),
            root_run_id=_required(value, "root_run_id"),
            request_id=_required(value, "request_id"),
            snapshot_id=_required(value, "snapshot_id"),
            state=state,
            provider_id=_required(value, "provider_id"),
            model_id=_required(value, "model_id"),
            binding_epoch=_non_negative_int(_read(value, "binding_epoch"), "binding_epoch"),
            context_window=_non_negative_int(_read(value, "context_window"), "context_window"),
            effective_ceiling=_non_negative_int(
                _read(value, "effective_ceiling"), "effective_ceiling"
            ),
            usage=usage,
            provider_request_id=(
                str(_read(value, "provider_request_id") or "").strip() or None
            ),
            error_code=str(_read(value, "error_code") or "").strip() or None,
            handoff_attempt=(
                None
                if _read(value, "handoff_attempt") is None
                else _non_negative_int(_read(value, "handoff_attempt"), "handoff_attempt")
            ),
        )

    @property
    def has_trusted_usage(self) -> bool:
        return (
            self.state == "succeeded"
            and self.usage is not None
            and self.usage.get("cache_tokens") is not None
            and self.context_window > 0
            and self.effective_ceiling > 0
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "invocation_id": self.invocation_id,
            "settlement_version": self.settlement_version,
            "settled_at": self.settled_at,
            "session_id": self.session_id,
            "root_run_id": self.root_run_id,
            "request_id": self.request_id,
            "snapshot_id": self.snapshot_id,
            "state": self.state,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "binding_epoch": self.binding_epoch,
            "context_window": self.context_window,
            "effective_ceiling": self.effective_ceiling,
            "usage": None if self.usage is None else dict(self.usage),
            "provider_request_id": self.provider_request_id,
            "error_code": self.error_code,
            "handoff_attempt": self.handoff_attempt,
        }


class SdkProviderSettlementReconciler:
    def __init__(self, session_db: Any, *, consumer_id: str) -> None:
        self._db = session_db
        self._consumer_id = str(consumer_id or "").strip()
        if not self._consumer_id:
            raise ValueError("consumer_id is required")

    @staticmethod
    def _coerce(
        envelope: ProviderProjectionEnvelopeV1 | object,
    ) -> ProviderProjectionEnvelopeV1:
        return (
            envelope if isinstance(envelope, ProviderProjectionEnvelopeV1)
            else ProviderProjectionEnvelopeV1.from_value(envelope)
        )

    async def project_attempt(
        self, envelope: ProviderProjectionEnvelopeV1 | object
    ) -> str:
        """Durably project attempt/usage without moving the source cursor."""

        item = self._coerce(envelope)
        result = await self._db.record_sdk_provider_attempt(item.to_record())
        if item.has_trusted_usage:
            usage = item.usage
            assert usage is not None
            await self._db.record_context_usage_sample({
                "session_id": item.session_id,
                "source_event_id": f"sdk-provider-invocation:{item.invocation_id}",
                "run_id": item.root_run_id,
                "request_id": item.request_id,
                "attempt_id": item.invocation_id,
                "event_type": "provider_attempt",
                "tokens_after": usage["input_tokens"],
                "prompt_tokens": usage["input_tokens"],
                "completion_tokens": usage["output_tokens"],
                "cached_tokens": usage["cache_tokens"],
                "context_window": item.context_window,
                "effective_ceiling": item.effective_ceiling,
                "estimate_method": "provider_usage",
                "provider_id": item.provider_id,
                "model_id": item.model_id,
                "binding_epoch": item.binding_epoch,
                "metadata": {
                    "snapshot_id": item.snapshot_id,
                    "settlement_version": item.settlement_version,
                    "total_tokens": usage["total_tokens"],
                    "reasoning_tokens": usage.get("reasoning_tokens"),
                },
                "completed_at": item.settled_at,
                "created_at": time.time(),
            })
        return result

    async def advance_cursor(
        self,
        envelope: ProviderProjectionEnvelopeV1 | object,
        *,
        source_sequence: int | None = None,
    ) -> None:
        item = self._coerce(envelope)
        cursor = await self._db.get_sdk_provider_projection_cursor(self._consumer_id)
        if source_sequence is not None:
            current_sequence = int(cursor.get("source_sequence", 0)) if cursor else 0
            should_advance = source_sequence > current_sequence
        else:
            should_advance = False
        current_position = (
            (float(cursor["settled_at"]), str(cursor["invocation_id"]))
            if cursor else (-1.0, "")
        )
        if source_sequence is None:
            should_advance = (item.settled_at, item.invocation_id) > current_position
        if should_advance:
            await self._db.advance_sdk_provider_projection_cursor(
                self._consumer_id,
                settled_at=item.settled_at,
                invocation_id=item.invocation_id,
                expected_version=int(cursor["version"]) if cursor else 0,
                source_sequence=source_sequence,
            )

    async def project(
        self,
        envelope: ProviderProjectionEnvelopeV1 | object,
        *,
        source_sequence: int | None = None,
    ) -> str:
        result = await self.project_attempt(envelope)
        await self.advance_cursor(envelope, source_sequence=source_sequence)
        return result


__all__ = ["ProviderProjectionEnvelopeV1", "SdkProviderSettlementReconciler"]

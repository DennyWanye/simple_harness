# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Deterministic Context Usage history and materialized-state contract.

The execution ledger remains the source of provider/compaction facts.  This
module only normalizes those immutable facts and reduces them into the one
public state row a Session is allowed to expose.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping


CONTEXT_USAGE_SOURCES = frozenset({"measured", "compacted", "binding_only"})
CONTEXT_USAGE_AVAILABILITIES = frozenset(
    {"available", "unavailable", "unknown"}
)


class ContextUsageSampleConflict(RuntimeError):
    """The same durable source identity was observed with another payload."""

    code = "context_usage_sample_conflict"


class ContextUsageStateConflict(RuntimeError):
    """The materialized authority could not be advanced after bounded CAS."""

    code = "context_usage_state_conflict"


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def context_usage_sample_id(session_id: str, source_event_id: str) -> str:
    """Return the stable history identity for one durable producer fact."""

    sid = str(session_id or "").strip()
    source_id = str(source_event_id or "").strip()
    if not sid or not source_id:
        raise ValueError("session_id and source_event_id are required")
    return hashlib.sha256(f"{sid}\0{source_id}".encode("utf-8")).hexdigest()


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def normalize_context_usage_sample(sample: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable, canonical V2 history payload.

    ``source_event_id`` identifies the producer fact, while ``sample_id`` is
    the immutable history row identity.  They intentionally are not inferred
    from token counts or wall-clock time.
    """

    session_id = str(sample.get("session_id") or "").strip()
    event_type = str(sample.get("event_type") or "provider_attempt").strip()
    if not session_id or event_type not in {"provider_attempt", "compaction"}:
        raise ValueError("valid session_id and context usage event_type are required")
    source_event_id = str(sample.get("source_event_id") or "").strip()
    if not source_event_id:
        explicit_sample_id = str(sample.get("sample_id") or "").strip()
        request_id = str(sample.get("request_id") or "").strip()
        attempt_id = str(sample.get("attempt_id") or "").strip()
        if explicit_sample_id:
            source_event_id = f"legacy-explicit:{explicit_sample_id}"
        elif not request_id or not attempt_id:
            raise ValueError("context usage source_event_id is required")
        else:
            source_event_id = f"{event_type}:{request_id}:{attempt_id}"
    created_at = float(sample.get("created_at") or 0.0)
    completed_at = float(sample.get("completed_at") or created_at)
    tokens_after = _non_negative_int(
        sample.get("tokens_after")
        if sample.get("tokens_after") is not None
        else sample.get("prompt_tokens")
    )
    tokens_before = sample.get("tokens_before")
    normalized: dict[str, Any] = {
        "sample_id": str(sample.get("sample_id") or "").strip(),
        "session_id": session_id,
        "source_event_id": source_event_id,
        "run_id": str(sample.get("run_id") or "").strip() or None,
        "request_id": str(sample.get("request_id") or "").strip() or None,
        "attempt_id": str(sample.get("attempt_id") or "").strip() or None,
        "event_type": event_type,
        "tokens_before": (
            None if tokens_before is None else _non_negative_int(tokens_before)
        ),
        "tokens_after": tokens_after,
        "prompt_tokens": _non_negative_int(
            sample.get("prompt_tokens")
            if sample.get("prompt_tokens") is not None
            else tokens_after
        ),
        "completion_tokens": _non_negative_int(sample.get("completion_tokens")),
        "cached_tokens": _non_negative_int(sample.get("cached_tokens")),
        "context_window": _non_negative_int(sample.get("context_window")),
        "effective_ceiling": _non_negative_int(sample.get("effective_ceiling")),
        "estimate_method": str(sample.get("estimate_method") or "provider_usage"),
        "provider_id": str(sample.get("provider_id") or "").strip() or None,
        "model_id": str(sample.get("model_id") or sample.get("model") or "").strip()
        or None,
        "binding_epoch": _non_negative_int(sample.get("binding_epoch")),
        "based_on_sample_id": str(sample.get("based_on_sample_id") or "").strip()
        or None,
        "metadata": dict(sample.get("metadata") or {}),
        "completed_at": completed_at,
        "created_at": created_at,
    }
    hash_payload = dict(normalized)
    hash_payload.pop("sample_id", None)
    # Ingestion timestamps are an envelope concern.  A retry of the same
    # durable producer event may be observed later, but its semantic payload
    # must remain idempotent; ordering uses the timestamp stored by the first
    # successful insert.
    hash_payload.pop("created_at", None)
    hash_payload.pop("completed_at", None)
    payload_hash = canonical_hash(hash_payload)
    normalized["sample_id"] = normalized["sample_id"] or context_usage_sample_id(
        session_id, source_event_id
    )
    normalized["payload_hash"] = payload_hash
    return normalized


@dataclass(frozen=True)
class ContextUsageStateV2:
    session_id: str
    source: str
    sample_id: str | None
    state_version: int
    binding_epoch: int
    availability: str
    provider_id: str | None
    model_id: str | None
    context_window: int
    tokens: int
    effective_ceiling: int
    completion_tokens: int
    cached_tokens: int
    based_on_sample_id: str | None
    source_event_id: str | None
    source_completed_at: float
    has_measurement: bool
    legacy_incomplete: bool
    updated_at: float

    def __post_init__(self) -> None:
        if self.source not in CONTEXT_USAGE_SOURCES:
            raise ValueError(f"invalid Context Usage source: {self.source}")
        if self.availability not in CONTEXT_USAGE_AVAILABILITIES:
            raise ValueError(
                f"invalid Context Usage availability: {self.availability}"
            )

    def to_record(self) -> dict[str, Any]:
        value = asdict(self)
        value["has_measurement"] = int(self.has_measurement)
        value["legacy_incomplete"] = int(self.legacy_incomplete)
        return value

    def to_public_payload(self) -> dict[str, Any]:
        ceiling = max(0, int(self.effective_ceiling))
        window = max(0, int(self.context_window))
        return {
            "schema_version": 2,
            "session_id": self.session_id,
            "source": self.source,
            "sample_id": self.sample_id,
            "version": self.state_version,
            "binding_epoch": self.binding_epoch,
            "availability": self.availability,
            "provider_id": self.provider_id,
            "model": self.model_id or "",
            "model_id": self.model_id,
            "prompt_tokens": max(0, int(self.tokens)),
            "completion_tokens": max(0, int(self.completion_tokens)),
            "cached_tokens": max(0, int(self.cached_tokens)),
            "context_window": window,
            "effective_ceiling": ceiling,
            "compact_at": int(window * 0.70) if self.has_measurement else 0,
            "recall_sweet": min(16_000, ceiling) if self.has_measurement else 0,
            "based_on_sample_id": self.based_on_sample_id,
            "has_measurement": self.has_measurement,
            "legacy_incomplete": self.legacy_incomplete,
            "updated_at": self.updated_at,
        }


def binding_only_state(
    *,
    session_id: str,
    state_version: int,
    binding_epoch: int,
    provider_id: str | None,
    model_id: str | None,
    availability: str,
    updated_at: float,
) -> ContextUsageStateV2:
    return ContextUsageStateV2(
        session_id=session_id,
        source="binding_only",
        sample_id=None,
        state_version=state_version,
        binding_epoch=binding_epoch,
        availability=availability,
        provider_id=provider_id,
        model_id=model_id,
        context_window=0,
        tokens=0,
        effective_ceiling=0,
        completion_tokens=0,
        cached_tokens=0,
        based_on_sample_id=None,
        source_event_id=None,
        source_completed_at=0.0,
        has_measurement=False,
        legacy_incomplete=False,
        updated_at=updated_at,
    )


def reduce_context_usage_state(
    current: ContextUsageStateV2,
    sample: Mapping[str, Any],
) -> ContextUsageStateV2:
    """Monotonically reduce one immutable history fact.

    Non-current epochs and late provider facts stay in history but cannot
    change the public authority.  Compaction is accepted only for the exact
    sample it compacted.
    """

    if int(sample["binding_epoch"]) != current.binding_epoch:
        return current
    event_type = str(sample["event_type"])
    if event_type == "provider_attempt":
        incoming_order = (
            float(sample["completed_at"]),
            str(sample["source_event_id"]),
        )
        current_order = (current.source_completed_at, current.source_event_id or "")
        if current.has_measurement and incoming_order <= current_order:
            return current
        return ContextUsageStateV2(
            session_id=current.session_id,
            source="measured",
            sample_id=str(sample["sample_id"]),
            state_version=current.state_version + 1,
            binding_epoch=current.binding_epoch,
            availability="available",
            provider_id=sample.get("provider_id"),
            model_id=sample.get("model_id"),
            context_window=_non_negative_int(sample.get("context_window")),
            tokens=_non_negative_int(sample.get("tokens_after")),
            effective_ceiling=_non_negative_int(sample.get("effective_ceiling")),
            completion_tokens=_non_negative_int(sample.get("completion_tokens")),
            cached_tokens=_non_negative_int(sample.get("cached_tokens")),
            based_on_sample_id=None,
            source_event_id=str(sample["source_event_id"]),
            source_completed_at=float(sample["completed_at"]),
            has_measurement=True,
            legacy_incomplete=False,
            updated_at=float(sample["completed_at"]),
        )
    if event_type == "compaction":
        if not current.has_measurement or sample.get("based_on_sample_id") != current.sample_id:
            return current
        return ContextUsageStateV2(
            session_id=current.session_id,
            source="compacted",
            sample_id=str(sample["sample_id"]),
            state_version=current.state_version + 1,
            binding_epoch=current.binding_epoch,
            availability=current.availability,
            provider_id=sample.get("provider_id") or current.provider_id,
            model_id=sample.get("model_id") or current.model_id,
            context_window=_non_negative_int(sample.get("context_window"))
            or current.context_window,
            tokens=_non_negative_int(sample.get("tokens_after")),
            effective_ceiling=_non_negative_int(sample.get("effective_ceiling"))
            or current.effective_ceiling,
            completion_tokens=current.completion_tokens,
            cached_tokens=current.cached_tokens,
            based_on_sample_id=str(sample["based_on_sample_id"]),
            source_event_id=str(sample["source_event_id"]),
            source_completed_at=max(
                current.source_completed_at, float(sample["completed_at"])
            ),
            has_measurement=True,
            legacy_incomplete=False,
            updated_at=float(sample["completed_at"]),
        )
    return current

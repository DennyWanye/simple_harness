"""Typed, JSON-safe contracts shared by quick search and DeepResearch."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class AttemptStatus(str, Enum):
    HIT = "hit"
    EMPTY = "empty"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    CAPTCHA = "captcha"
    RATE_LIMIT = "rate_limit"
    ERROR = "error"
    COOLDOWN = "cooldown"
    HALF_OPEN_BUSY = "half_open_busy"
    CACHE_HIT = "cache_hit"
    UNAVAILABLE = "unavailable"
    BUDGET_EXHAUSTED = "budget_exhausted"
    QUEUE_TIMEOUT = "queue_timeout"


class PublicErrorCode(str, Enum):
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    CAPTCHA = "captcha"
    RATE_LIMIT = "rate_limit"
    HTTP_ERROR = "http_error"
    PARSE_ERROR = "parse_error"
    UNAVAILABLE = "unavailable"
    COOLDOWN = "cooldown"
    HALF_OPEN_BUSY = "half_open_busy"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INVALID_RESPONSE = "invalid_response"
    FETCH_FAILED = "fetch_failed"
    QUEUE_TIMEOUT = "queue_timeout"


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    max_results: int = 5
    region: str | None = None
    mode: Literal["quick", "research"] = "quick"
    hydrate_top: int = 0
    total_timeout_s: float | None = None
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str | None = None
    dimension_id: str | None = None
    query_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", self.query.strip())
        object.__setattr__(self, "max_results", max(1, min(int(self.max_results), 50)))
        object.__setattr__(self, "hydrate_top", max(0, min(int(self.hydrate_top), 3)))
        if self.dimension_id is not None:
            dimension_id = self.dimension_id.strip()
            if not dimension_id:
                raise ValueError("dimension_id must be non-empty when supplied")
            object.__setattr__(self, "dimension_id", dimension_id)
        if isinstance(self.query_terms, str):
            raise TypeError("query_terms must be a sequence, not a string")
        object.__setattr__(
            self,
            "query_terms",
            tuple(
                dict.fromkeys(
                    term.strip().casefold()
                    for term in self.query_terms
                    if isinstance(term, str) and term.strip()
                )
            ),
        )
        if self.mode not in {"quick", "research"}:
            raise ValueError("mode must be quick or research")


@dataclass(slots=True)
class SearchBudget:
    deadline: float
    provider_concurrency: int
    per_provider_timeout_s: float
    cdp_remaining: int
    hydrate_remaining: int
    parent_lease: Any = field(default=None, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @classmethod
    def create(
        cls,
        *,
        total_timeout_s: float,
        provider_concurrency: int,
        per_provider_timeout_s: float,
        cdp_budget: int,
        hydrate_budget: int,
    ) -> "SearchBudget":
        return cls(
            deadline=time.monotonic() + total_timeout_s,
            provider_concurrency=provider_concurrency,
            per_provider_timeout_s=per_provider_timeout_s,
            cdp_remaining=cdp_budget,
            hydrate_remaining=hydrate_budget,
        )

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    async def claim_cdp(self) -> bool:
        if self.parent_lease is not None:
            return bool(await self.parent_lease.claim_cdp())
        async with self._lock:
            if self.cdp_remaining <= 0:
                return False
            self.cdp_remaining -= 1
            return True

    async def claim_hydrate(self) -> bool:
        if self.parent_lease is not None:
            return bool(await self.parent_lease.claim_hydrate())
        async with self._lock:
            if self.hydrate_remaining <= 0:
                return False
            self.hydrate_remaining -= 1
            return True


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    provider: str
    status: AttemptStatus
    elapsed_ms: int
    result_count: int = 0
    public_error_code: PublicErrorCode | None = None
    permit: str = "closed"
    circuit_generation: int = 0
    probe_outcome: str | None = None
    upstream_called: bool = False
    is_rescue: bool = False
    budget_attempt_id: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    stable_id: str
    url: str
    canonical_url: str
    title: str
    snippet: str = ""
    published_at: str | None = None
    provider: str = ""
    providers: tuple[str, ...] = ()
    provider_rank: int = 0
    score: float = 0.0
    searched_at: str = ""
    source_kind: str = "serp"
    hydration_error: str | None = None
    text: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResponse:
    query: str
    results: tuple[RetrievalCandidate, ...]
    attempts: tuple[ProviderAttempt, ...] = ()
    elapsed_ms: int = 0
    cache_hit: bool = False
    degraded: bool = False
    request_id: str | None = None
    run_id: str | None = None
    rescue_status: Literal[
        "not_needed",
        "hit",
        "empty",
        "failed",
        "ineligible",
        "deadline_exhausted",
        "lost_race",
    ] = "not_needed"
    rescue_error_code: PublicErrorCode | None = None
    rescue_upstream_called: bool = False

    def __post_init__(self) -> None:
        if self.rescue_status not in {
            "not_needed",
            "hit",
            "empty",
            "failed",
            "ineligible",
            "deadline_exhausted",
            "lost_race",
        }:
            raise ValueError("invalid rescue_status")
        if (
            self.rescue_error_code is not None
            and not isinstance(self.rescue_error_code, PublicErrorCode)
        ):
            raise TypeError("rescue_error_code must be PublicErrorCode or None")

    @property
    def count(self) -> int:
        return len(self.results)

    @property
    def engines_tried(self) -> list[str]:
        return [a.provider for a in self.attempts]

    @property
    def engines_hit(self) -> list[str]:
        return [a.provider for a in self.attempts if a.status in {AttemptStatus.HIT, AttemptStatus.CACHE_HIT}]

    @property
    def errors(self) -> list[dict[str, str]]:
        return [
            {"provider": a.provider, "code": a.public_error_code.value}
            for a in self.attempts
            if a.public_error_code is not None
        ]

    def coverage(self) -> dict[str, int | str | bool | None]:
        """Return the single, mutually-exclusive Search Gateway accounting.

        Transport outcomes are counted only when ``upstream_called`` is true;
        routing decisions are kept in orthogonal skip buckets.  This makes the
        two frozen equations directly testable by every downstream consumer.
        """

        upstream = [attempt for attempt in self.attempts if attempt.upstream_called]
        hits = sum(attempt.status is AttemptStatus.HIT for attempt in upstream)
        empty = sum(attempt.status is AttemptStatus.EMPTY for attempt in upstream)
        timeouts = sum(attempt.status is AttemptStatus.TIMEOUT for attempt in upstream)
        blocked = sum(attempt.status is AttemptStatus.BLOCKED for attempt in upstream)
        captcha = sum(attempt.status is AttemptStatus.CAPTCHA for attempt in upstream)
        rate_limits = sum(
            attempt.status is AttemptStatus.RATE_LIMIT for attempt in upstream
        )
        errors = sum(attempt.status is AttemptStatus.ERROR for attempt in upstream)
        actual_requests = (
            hits + empty + timeouts + blocked + captcha + rate_limits + errors
        )

        error_attempts = [
            attempt for attempt in upstream if attempt.status is AttemptStatus.ERROR
        ]
        http_errors = sum(
            attempt.public_error_code is PublicErrorCode.HTTP_ERROR
            for attempt in error_attempts
        )
        invalid_response_errors = sum(
            attempt.public_error_code is PublicErrorCode.INVALID_RESPONSE
            for attempt in error_attempts
        )
        parse_errors = sum(
            attempt.public_error_code is PublicErrorCode.PARSE_ERROR
            for attempt in error_attempts
        )
        other_errors = errors - http_errors - invalid_response_errors - parse_errors

        cooldown_skips = sum(
            attempt.status is AttemptStatus.COOLDOWN and not attempt.upstream_called
            for attempt in self.attempts
        )
        busy_skips = sum(
            attempt.status is AttemptStatus.HALF_OPEN_BUSY
            and not attempt.upstream_called
            for attempt in self.attempts
        )
        queue_timeouts = sum(
            attempt.status is AttemptStatus.QUEUE_TIMEOUT
            and not attempt.upstream_called
            for attempt in self.attempts
        )
        unavailable_skips = sum(
            attempt.status is AttemptStatus.UNAVAILABLE
            and not attempt.upstream_called
            for attempt in self.attempts
        )
        budget_skips = sum(
            attempt.status is AttemptStatus.BUDGET_EXHAUSTED
            and not attempt.upstream_called
            for attempt in self.attempts
        )
        cache_hits = sum(
            attempt.status is AttemptStatus.CACHE_HIT
            and not attempt.upstream_called
            for attempt in self.attempts
        )
        routing_decisions = (
            actual_requests
            + cooldown_skips
            + busy_skips
            + queue_timeouts
            + unavailable_skips
            + budget_skips
            + cache_hits
        )
        probes = sum(
            attempt.permit == "half_open" and attempt.upstream_called
            for attempt in self.attempts
        )
        return {
            "actual_requests": actual_requests,
            "provider_attempt_count": actual_requests,
            "routing_decisions": routing_decisions,
            "hits": hits,
            "empty": empty,
            "timeouts": timeouts,
            "blocked": blocked,
            "captcha": captcha,
            "rate_limits": rate_limits,
            "errors": errors,
            "http_errors": http_errors,
            "invalid_response_errors": invalid_response_errors,
            "parse_errors": parse_errors,
            "other_errors": other_errors,
            "cooldown_skips": cooldown_skips,
            "busy_skips": busy_skips,
            "queue_timeouts": queue_timeouts,
            "unavailable_skips": unavailable_skips,
            "budget_skips": budget_skips,
            "cache_hits": cache_hits,
            "probes": probes,
            "rescue_status": self.rescue_status,
            "rescue_error_code": (
                self.rescue_error_code.value if self.rescue_error_code else None
            ),
            "rescue_considered": self.rescue_status != "not_needed",
            "rescue_executed": self.rescue_upstream_called,
            "candidates": self.count,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "count": self.count,
            "results": [_json_value(item) for item in self.results],
            "engines_tried": self.engines_tried,
            "engines_hit": self.engines_hit,
            "errors": self.errors,
            "attempts": [_json_value(item) for item in self.attempts],
            "elapsed_ms": self.elapsed_ms,
            "cache_hit": self.cache_hit,
            "degraded": self.degraded,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "rescue_status": self.rescue_status,
            "rescue_error_code": (
                self.rescue_error_code.value if self.rescue_error_code else None
            ),
            "rescue_upstream_called": self.rescue_upstream_called,
            "coverage": self.coverage(),
        }


@dataclass(frozen=True, slots=True)
class DimensionSearchRequest:
    dimension_id: str
    request: SearchRequest
    core: bool = True
    priority: int = 0
    query_terms: tuple[str, ...] = ()
    query_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not self.dimension_id.strip():
            raise ValueError("dimension_id is required")
        object.__setattr__(self, "dimension_id", self.dimension_id.strip())
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ValueError("priority must be an integer")
        if isinstance(self.query_terms, str):
            raise TypeError("query_terms must be a sequence, not a string")
        object.__setattr__(
            self,
            "query_terms",
            tuple(
                dict.fromkeys(
                    term.strip().casefold()
                    for term in self.query_terms
                    if isinstance(term, str) and term.strip()
                )
            ),
        )
        if self.query_fingerprint is not None and not self.query_fingerprint.strip():
            raise ValueError("query_fingerprint must be non-empty when supplied")

    @property
    def budget_query_key(self) -> str:
        if self.query_fingerprint is not None:
            return self.query_fingerprint.strip()
        payload = "|".join(
            (
                self.dimension_id,
                self.request.query.strip().casefold(),
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DimensionSearchResult:
    dimension_id: str
    response: SearchResponse


@dataclass(frozen=True, slots=True)
class DimensionBatchResponse:
    results: tuple[DimensionSearchResult, ...]
    budget: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [
                {
                    "dimension_id": item.dimension_id,
                    "response": item.response.to_dict(),
                }
                for item in self.results
            ],
            "budget": _json_value(self.budget),
        }


@dataclass(frozen=True, slots=True)
class FetchRequest:
    url: str
    timeout: float = 12.0
    extract: bool = True
    render_policy: Literal["auto", "never", "always"] = "auto"
    include_html: bool = False
    max_chars: int = 50_000
    request_budget: SearchBudget | None = None
    allow_jina: bool = False
    run_id: str = "default"
    deadline_monotonic: float | None = None
    cancel_event: Any = None
    render_budget: Any = None
    prefer_httpx: bool = False


@dataclass(frozen=True, slots=True)
class EvidenceDocument:
    stable_id: str
    url: str
    canonical_url: str
    title: str
    text: str
    published_at: str | None
    provider: str
    providers: tuple[str, ...]
    provider_rank: int
    score: float
    searched_at: str
    source_kind: str
    content_hash: str
    fetcher: str
    extractor: str
    fetched_at: str
    quality_flags: tuple[str, ...] = ()
    html: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return {f.name: _json_value(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, tuple):
        return [_json_value(v) for v in value]
    if isinstance(value, list):
        return [_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    return value


__all__ = [
    "AttemptStatus", "DimensionBatchResponse", "DimensionSearchRequest",
    "DimensionSearchResult", "EvidenceDocument", "FetchRequest", "ProviderAttempt",
    "PublicErrorCode", "RetrievalCandidate", "SearchBudget", "SearchRequest",
    "SearchResponse",
]

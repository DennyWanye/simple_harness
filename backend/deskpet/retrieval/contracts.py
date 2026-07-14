"""Typed, JSON-safe contracts shared by quick search and DeepResearch."""

from __future__ import annotations

import asyncio
import dataclasses
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
    ERROR = "error"
    COOLDOWN = "cooldown"
    CACHE_HIT = "cache_hit"
    UNAVAILABLE = "unavailable"
    BUDGET_EXHAUSTED = "budget_exhausted"


class PublicErrorCode(str, Enum):
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    CAPTCHA = "captcha"
    HTTP_ERROR = "http_error"
    PARSE_ERROR = "parse_error"
    UNAVAILABLE = "unavailable"
    COOLDOWN = "cooldown"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INVALID_RESPONSE = "invalid_response"
    FETCH_FAILED = "fetch_failed"


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", self.query.strip())
        object.__setattr__(self, "max_results", max(1, min(int(self.max_results), 50)))
        object.__setattr__(self, "hydrate_top", max(0, min(int(self.hydrate_top), 3)))
        if self.mode not in {"quick", "research"}:
            raise ValueError("mode must be quick or research")


@dataclass(slots=True)
class SearchBudget:
    deadline: float
    provider_concurrency: int
    per_provider_timeout_s: float
    cdp_remaining: int
    hydrate_remaining: int
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
        async with self._lock:
            if self.cdp_remaining <= 0:
                return False
            self.cdp_remaining -= 1
            return True

    async def claim_hydrate(self) -> bool:
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
    "AttemptStatus", "EvidenceDocument", "FetchRequest", "ProviderAttempt",
    "PublicErrorCode", "RetrievalCandidate", "SearchBudget", "SearchRequest",
    "SearchResponse",
]

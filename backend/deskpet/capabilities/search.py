# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Deterministic catalog search with optional semantic recall."""

from __future__ import annotations

import asyncio
import inspect
import math
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, Sequence

from .contracts import (
    CapabilityCatalogSnapshot,
    CapabilityDescriptor,
    CapabilitySearchHit,
    CapabilitySearchReceipt,
    CatalogStamp,
)
from .store import CapabilityStore


class CapabilitySearchError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SemanticEmbedder(Protocol):
    def embed(
        self, texts: Sequence[str]
    ) -> (
        Sequence[Sequence[float]]
        | Awaitable[Sequence[Sequence[float]]]
    ):
        ...


@dataclass(frozen=True, slots=True)
class _SearchDocument:
    descriptor: CapabilityDescriptor
    normalized_name: str
    normalized_aliases: tuple[str, ...]
    normalized_text: str
    tokens: frozenset[str]


@dataclass(frozen=True, slots=True)
class CapabilitySearchResult:
    snapshot_ref: str
    stamp: CatalogStamp
    hits: tuple[CapabilitySearchHit, ...]
    receipt: CapabilitySearchReceipt
    semantic_used: bool


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _tokens(value: str) -> frozenset[str]:
    return frozenset(
        token
        for token in re.findall(r"[\w-]+", _normalize(value), flags=re.UNICODE)
        if token
    )


def _document(descriptor: CapabilityDescriptor) -> _SearchDocument:
    version = descriptor.version
    aliases = tuple(_normalize(alias) for alias in version.aliases)
    parts = (
        version.capability_id,
        version.display_name,
        version.description,
        *version.aliases,
        *version.logical_tool_ids,
        *version.provider_tool_names,
        *version.permission_categories,
    )
    text = _normalize(" ".join(parts))
    return _SearchDocument(
        descriptor=descriptor,
        normalized_name=_normalize(version.display_name),
        normalized_aliases=aliases,
        normalized_text=text,
        tokens=_tokens(text),
    )


def _lexical_score(
    query: str, query_tokens: frozenset[str], document: _SearchDocument
) -> tuple[float, str] | None:
    version = document.descriptor.version
    normalized_id = _normalize(version.capability_id)
    provider_names = {_normalize(name) for name in version.provider_tool_names}
    logical_ids = {_normalize(name) for name in version.logical_tool_ids}
    if query in {
        normalized_id,
        document.normalized_name,
        *provider_names,
        *logical_ids,
    }:
        return 100.0, "exact"
    if query in document.normalized_aliases:
        return 95.0, "alias"
    intersection = query_tokens & document.tokens
    if intersection:
        coverage = len(intersection) / max(1, len(query_tokens))
        precision = len(intersection) / max(1, len(document.tokens))
        score = 45.0 + coverage * 35.0 + precision * 10.0
        return score, "token"
    if query and query in document.normalized_text:
        return 40.0, "token"
    return None


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class CapabilitySearch:
    def __init__(
        self,
        *,
        store: CapabilityStore | None = None,
        embedder: SemanticEmbedder | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self._clock = clock
        self._index_cache: dict[str, tuple[_SearchDocument, ...]] = {}
        self._embedding_cache: dict[str, tuple[tuple[float, ...], ...]] = {}
        self._cache_lock = asyncio.Lock()

    async def _documents(
        self, snapshot: CapabilityCatalogSnapshot
    ) -> tuple[_SearchDocument, ...]:
        key = snapshot.stamp.fingerprint
        cached = self._index_cache.get(key)
        if cached is not None:
            return cached
        documents = tuple(_document(item) for item in snapshot.descriptors)
        async with self._cache_lock:
            self._index_cache.setdefault(key, documents)
            if len(self._index_cache) > 32:
                oldest = next(iter(self._index_cache))
                if oldest != key:
                    self._index_cache.pop(oldest, None)
                    self._embedding_cache.pop(oldest, None)
            return self._index_cache[key]

    async def _semantic_scores(
        self,
        *,
        query: str,
        snapshot: CapabilityCatalogSnapshot,
        documents: tuple[_SearchDocument, ...],
    ) -> tuple[tuple[float, ...], bool]:
        if self.embedder is None or not documents:
            return tuple(0.0 for _ in documents), False
        key = snapshot.stamp.fingerprint
        try:
            document_vectors = self._embedding_cache.get(key)
            if document_vectors is None:
                values = await _maybe_await(
                    self.embedder.embed(
                        [document.normalized_text for document in documents]
                    )
                )
                document_vectors = tuple(
                    tuple(float(item) for item in vector) for vector in values
                )
                if len(document_vectors) != len(documents):
                    raise ValueError("embedder returned the wrong vector count")
                async with self._cache_lock:
                    self._embedding_cache[key] = document_vectors
            query_values = await _maybe_await(self.embedder.embed([query]))
            query_vectors = tuple(
                tuple(float(item) for item in vector) for vector in query_values
            )
            if len(query_vectors) != 1:
                raise ValueError("embedder returned the wrong query vector count")
            return (
                tuple(
                    _cosine(query_vectors[0], vector)
                    for vector in document_vectors
                ),
                True,
            )
        except Exception:
            # Semantic recall is additive.  An unavailable model must not block
            # ordinary capability discovery.
            return tuple(0.0 for _ in documents), False

    async def search(
        self,
        *,
        snapshot: CapabilityCatalogSnapshot,
        root_run_id: str,
        query: str,
        limit: int = 10,
    ) -> CapabilitySearchResult:
        normalized_query = _normalize(query)
        if not normalized_query:
            raise CapabilitySearchError("empty_query", "search query is required")
        if limit < 1 or limit > 100:
            raise CapabilitySearchError(
                "invalid_limit", "search limit must be between 1 and 100"
            )
        documents = await self._documents(snapshot)
        query_tokens = _tokens(normalized_query)
        semantic_scores, semantic_used = await self._semantic_scores(
            query=normalized_query,
            snapshot=snapshot,
            documents=documents,
        )
        candidates: list[CapabilitySearchHit] = []
        for index, document in enumerate(documents):
            lexical = _lexical_score(normalized_query, query_tokens, document)
            semantic = semantic_scores[index]
            if lexical is None and semantic < 0.25:
                continue
            if lexical is None:
                score = 25.0 + max(0.0, semantic) * 50.0
                match_kind = "semantic"
            else:
                lexical_score, match_kind = lexical
                score = lexical_score + max(0.0, semantic) * 5.0
            descriptor = document.descriptor
            candidates.append(
                CapabilitySearchHit(
                    capability_id=descriptor.version.capability_id,
                    version=descriptor.version.version,
                    score=score,
                    match_kind=match_kind,  # type: ignore[arg-type]
                    executable=descriptor.executable,
                    descriptor_fingerprint=descriptor.fingerprint,
                )
            )
        hits = tuple(
            sorted(
                candidates,
                key=lambda item: (
                    -item.score,
                    not item.executable,
                    item.capability_id,
                    item.version,
                ),
            )[:limit]
        )
        receipt = CapabilitySearchReceipt.create(
            root_run_id=root_run_id,
            stamp=snapshot.stamp,
            query=normalized_query,
            hits=hits,
            created_at=self._clock(),
        )
        if self.store is not None:
            await self.store.record_search_receipt(
                receipt_id=receipt.receipt_id,
                root_run_id=receipt.root_run_id,
                catalog_stamp_fingerprint=receipt.catalog_stamp_fingerprint,
                query_hash=receipt.query_hash,
                result={
                    "snapshot_ref": snapshot.snapshot_ref,
                    "hits": [
                        {
                            "capability_id": hit.capability_id,
                            "version": hit.version,
                            "score": hit.score,
                            "match_kind": hit.match_kind,
                            "executable": hit.executable,
                            "descriptor_fingerprint": hit.descriptor_fingerprint,
                        }
                        for hit in hits
                    ],
                    "semantic_used": semantic_used,
                },
                created_at=receipt.created_at,
            )
        return CapabilitySearchResult(
            snapshot_ref=snapshot.snapshot_ref,
            stamp=snapshot.stamp,
            hits=hits,
            receipt=receipt,
            semantic_used=semantic_used,
        )


@dataclass(frozen=True, slots=True)
class CapabilityClaimDecision:
    allowed: bool
    requires_search: bool
    reason: str


class CapabilityClaimGuard:
    """Require current-stamp search evidence before a negative action claim."""

    def __init__(self, store: CapabilityStore) -> None:
        self.store = store

    async def evaluate_negative_claim(
        self,
        *,
        root_run_id: str,
        stamp: CatalogStamp,
        action_context: bool,
    ) -> CapabilityClaimDecision:
        if not action_context:
            return CapabilityClaimDecision(
                allowed=True,
                requires_search=False,
                reason="ordinary conversation is outside capability-claim guard",
            )
        has_evidence = await self.store.has_search_evidence(
            root_run_id=root_run_id,
            catalog_stamp_fingerprint=stamp.fingerprint,
        )
        if not has_evidence:
            return CapabilityClaimDecision(
                allowed=False,
                requires_search=True,
                reason="current catalog stamp has no search receipt for this root run",
            )
        return CapabilityClaimDecision(
            allowed=True,
            requires_search=False,
            reason="current catalog stamp has grounded search evidence",
        )


__all__ = [
    "CapabilityClaimDecision",
    "CapabilityClaimGuard",
    "CapabilitySearch",
    "CapabilitySearchError",
    "CapabilitySearchResult",
    "SemanticEmbedder",
]

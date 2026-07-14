from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from ..contracts import PublicErrorCode, RetrievalCandidate, SearchBudget, SearchRequest


@dataclass(slots=True)
class ProviderFailure(Exception):
    code: PublicErrorCode
    status_code: int | None = None

    def __str__(self) -> str:
        return self.code.value


class SearchProvider(Protocol):
    name: str
    capabilities: frozenset[str]

    async def is_available(self) -> bool: ...
    async def search(self, request: SearchRequest, budget: SearchBudget, client: httpx.AsyncClient) -> list[RetrievalCandidate]: ...


def candidates_from_rows(provider: str, rows: list[dict[str, str]]) -> list[RetrievalCandidate]:
    from datetime import datetime, timezone
    from ..ranking import canonicalize_url, stable_candidate_id

    searched_at = datetime.now(timezone.utc).isoformat()
    out: list[RetrievalCandidate] = []
    for rank, row in enumerate(rows, start=1):
        url = str(row.get("url") or "").strip()
        title = str(row.get("title") or "").strip()
        if not url.startswith(("http://", "https://")) or not title:
            continue
        canonical = canonicalize_url(url)
        out.append(RetrievalCandidate(
            stable_id=stable_candidate_id(canonical), url=url, canonical_url=canonical,
            title=title, snippet=str(row.get("snippet") or "").strip(),
            published_at=row.get("published_at"), provider=provider,
            providers=(provider,), provider_rank=rank, searched_at=searched_at,
        ))
    return out

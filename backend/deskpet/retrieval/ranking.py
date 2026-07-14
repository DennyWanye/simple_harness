from __future__ import annotations
import hashlib
import re
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from .contracts import RetrievalCandidate

_TRACKING = {"fbclid", "gclid", "mc_cid", "mc_eid"}

def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if host.startswith("www."): host = host[4:]
    port = parsed.port
    netloc = host if port is None or (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443) else f"{host}:{port}"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in _TRACKING))
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/": path = path.rstrip("/")
    return urlunsplit((parsed.scheme.lower() or "https", netloc, path, query, ""))

def normalized_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host

def stable_candidate_id(canonical_url: str) -> str:
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:20]

def _fingerprint(candidate: RetrievalCandidate) -> str:
    text = re.sub(r"\W+", "", (candidate.title + candidate.snippet).lower())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]

def dedupe_candidates(candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
    by_url: dict[str, RetrievalCandidate] = {}
    seen_fp: set[str] = set()
    for item in sorted(candidates, key=lambda c: (c.provider_rank, c.canonical_url, c.provider)):
        canonical = canonicalize_url(item.canonical_url or item.url)
        if canonical in by_url:
            old = by_url[canonical]
            providers = tuple(dict.fromkeys((*old.providers, *item.providers, item.provider)))
            by_url[canonical] = replace(old, providers=providers, provider_rank=min(old.provider_rank, item.provider_rank))
            continue
        fp = _fingerprint(item)
        if fp in seen_fp: continue
        seen_fp.add(fp)
        by_url[canonical] = replace(item, canonical_url=canonical, stable_id=stable_candidate_id(canonical))
    return list(by_url.values())

def rank_candidates(query: str, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
    terms = {t for t in re.findall(r"[\w\u3400-\u9fff]+", query.lower()) if len(t) > 1}
    provider_counts: dict[str, int] = {}
    ranked: list[RetrievalCandidate] = []
    for item in candidates:
        haystack = f"{item.title} {item.snippet}".lower()
        relevance = sum(1 for term in terms if term in haystack) / max(1, len(terms))
        rank_score = 1.0 / max(1, item.provider_rank)
        freshness = 0.0
        if item.published_at:
            try:
                age = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))).days)
                freshness = 1.0 / (1.0 + age / 30.0)
            except ValueError: pass
        host = normalized_host(item.canonical_url)
        quality = 1.0 if host.endswith((".gov", ".edu", ".gov.cn", ".org")) else 0.5
        diversity = 1.0 / (1.0 + provider_counts.get(item.provider, 0))
        provider_counts[item.provider] = provider_counts.get(item.provider, 0) + 1
        score = 0.45 * relevance + 0.25 * rank_score + 0.1 * freshness + 0.1 * quality + 0.1 * diversity
        ranked.append(replace(item, score=round(score, 8)))
    return sorted(ranked, key=lambda c: (-c.score, c.canonical_url))

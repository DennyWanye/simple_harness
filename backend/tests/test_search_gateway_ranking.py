from __future__ import annotations

from deskpet.retrieval.contracts import RetrievalCandidate
from deskpet.retrieval.ranking import canonicalize_url, dedupe_candidates, normalized_host, rank_candidates


def _row(url, title, provider, rank):
    return RetrievalCandidate("id", url, url, title, "query evidence", provider=provider, providers=(provider,), provider_rank=rank)


def test_canonicalization_removes_tracking_normalizes_host_and_path():
    url = canonicalize_url("HTTPS://WWW.Example.COM:443/a//b/?utm_source=x&b=2&a=1#frag")
    assert url == "https://example.com/a/b?a=1&b=2"
    assert normalized_host("https://WWW.Example.COM.:8443/a") == "example.com"


def test_dedupe_merges_providers_and_content_fingerprints():
    rows = [
        _row("https://example.test/a?utm_source=x", "Same", "duckduckgo", 2),
        _row("https://example.test/a", "Same", "baidu", 1),
        _row("https://other.test/b", "Same", "bing-cdp", 3),
    ]
    out = dedupe_candidates(rows)
    assert len(out) == 1
    assert out[0].providers == ("baidu", "duckduckgo")
    assert out[0].provider_rank == 1


def test_ranking_is_stable_independent_of_completion_order():
    rows = [
        _row("https://b.test", "query B", "baidu", 1),
        _row("https://a.test", "query A", "duckduckgo", 1),
    ]
    forward = [(item.canonical_url, item.score) for item in rank_candidates("query", rows)]
    reverse = [(item.canonical_url, item.score) for item in rank_candidates("query", list(reversed(rows)))]
    assert forward == reverse

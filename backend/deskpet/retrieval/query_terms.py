"""Deterministic multilingual query terms and stable fingerprints."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable


_ENGLISH_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "how", "in", "is", "it", "of", "on", "or", "that", "the", "their",
        "this", "to", "was", "what", "when", "where", "which", "who", "with",
    }
)

_DOMAIN_TERMS = tuple(
    sorted(
        {
            "人工智能", "大模型", "生成式人工智能", "基准测试", "模型能力",
            "教育现状", "小学教育", "义务教育", "教育质量", "教育公平",
            "城乡均衡", "区域均衡", "教师队伍", "师资配置", "教育财政",
            "双减", "课后服务", "学生负担", "家庭负担", "国家计划",
            "下一步计划", "招生规模", "在校生", "学校规模", "发展趋势",
        },
        key=lambda value: (-len(value), value),
    )
)

_HAN_RUN = re.compile(r"[\u3400-\u9fff]+")
_ENGLISH_TOKEN = re.compile(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", re.I)
_NUMBER_ENTITY = re.compile(
    r"(?<![0-9A-Za-z])(?:19|20)\d{2}年(?:\d{1,2}月(?:\d{1,2}日)?)?"
    r"|(?<![0-9A-Za-z])(?:19|20)\d{2}(?:[-/.]\d{1,2}(?:[-/.]\d{1,2})?)?"
    r"|(?<![0-9A-Za-z])\d+(?:\.\d+)?%?"
)


def normalize_query_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def query_language(value: str) -> str:
    return "zh" if _HAN_RUN.search(value) else "en"


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = normalize_query_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def _han_ngrams(value: str) -> tuple[str, ...]:
    grams: list[str] = []
    for run in _HAN_RUN.findall(value):
        if len(run) == 1:
            grams.append(run)
            continue
        for size in (2, 3):
            if len(run) < size:
                continue
            grams.extend(run[index : index + size] for index in range(len(run) - size + 1))
    return _ordered_unique(grams)


def extract_query_terms(value: str) -> tuple[str, ...]:
    """Return domain words, numbers, Han n-grams, and English tokens.

    Han text must not depend on whitespace segmentation.  Domain terms are
    emitted first so callers can retain the most meaningful terms under a
    bounded term budget; character n-grams provide deterministic recall for
    unseen Chinese phrases.
    """

    normalized = normalize_query_text(value)
    domain = (term for term in _DOMAIN_TERMS if term in normalized)
    numbers = (match.group(0) for match in _NUMBER_ENTITY.finditer(normalized))
    english = (
        token
        for token in _ENGLISH_TOKEN.findall(normalized)
        if token not in _ENGLISH_STOPWORDS
    )
    return _ordered_unique((*domain, *numbers, *_han_ngrams(normalized), *english))


def query_term_relevance(query: str, candidate: str) -> float:
    query_terms = extract_query_terms(query)
    if not query_terms:
        return 0.0
    candidate_terms = set(extract_query_terms(candidate))
    normalized_candidate = normalize_query_text(candidate)
    matched = sum(
        1
        for term in query_terms
        if term in candidate_terms or term in normalized_candidate
    )
    return matched / len(query_terms)


def query_fingerprint(
    *,
    dimension_id: str,
    query_family: str,
    comparison_axis: str,
    source_target: str,
    query: str,
    freshness_window: str,
) -> str:
    payload = {
        "comparison_axis": normalize_query_text(comparison_axis),
        "dimension_id": normalize_query_text(dimension_id),
        "freshness_window": normalize_query_text(freshness_window),
        "query": normalize_query_text(query),
        "query_family": normalize_query_text(query_family),
        "source_target": normalize_query_text(source_target),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "extract_query_terms",
    "normalize_query_text",
    "query_fingerprint",
    "query_language",
    "query_term_relevance",
]

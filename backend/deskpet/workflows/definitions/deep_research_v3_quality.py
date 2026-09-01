"""Passage-first, deterministic support checks for DeepResearch v3."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from .deep_research_v3_contracts import (
    AtomicClaim,
    EvidencePassage,
    PublishDecision,
    SupportDecision,
)

_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9_.+-]*|\d+(?:\.\d+)?|[\u4e00-\u9fff]", re.UNICODE)
_EXACT = re.compile(
    r"(?:\b(?:v(?:ersion)?\s*)?\d+(?:\.\d+){1,3}\b|"
    r"\b\d{4}[-年/]\d{1,2}(?:[-月/]\d{1,2}日?)?\b|"
    r"\b\d+(?:\.\d+)?%\b|[$¥€£]\s*\d+(?:\.\d+)?|"
    r"\b\d+(?:\.\d+)?\b)",
    re.IGNORECASE,
)
_PARAGRAPH = re.compile(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", re.DOTALL)
_SENTENCE = re.compile(r"[^。！？.!?\n]+[。！？.!?]?", re.UNICODE)
_CLAUSE_SEPARATOR = re.compile(
    r"\s*(?:[;；]|,(?=\s+(?:and|but|while|whereas)\b)|，(?=(?:并且|但|而|同时|其中)))\s*",
    re.IGNORECASE,
)
_LOW_VALUE_EXTRACTIVE_RE = re.compile(
    r"^(?:"
    r"\[(?:submitted|revised|published)\b|"
    r"(?:computer science|economics|mathematics|physics)\s*>|"
    r"(?:title|authors?|keywords?|abstract)\s*:|"
    r"by\s+\S+.*(?:\d{4}|月)|"
    r"(?:a|an)\s+(?:ai\s+)?(?:chatbot|agent)\s+(?:answers?|takes?)\b|"
    r"making\s+.+\s+accessible\s+to\s+everyone"
    r")",
    re.IGNORECASE,
)
_PAGE_CHROME_RE = re.compile(
    r"(?:skip to (?:main )?content|navigation menu|appearance settings|"
    r"platform ai code creation|sign in|share this article|\d+ comments?|"
    r"github copilot (?:write better code with ai|app direct agents)|"
    r"write better code with ai github copilot|"
    r"direct agents from issue to merge|"
    r"guides,? concepts,? and product docs for|"
    r"migrate to .+ migrate to|"
    r"this page requires javascript|please turn on javascript)",
    re.IGNORECASE,
)
_PUBLICATION_METADATA_RE = re.compile(
    r"(?:\bsubmitted on\b|\blast revised\b|\bwas (?:submitted|published|authored)\b|"
    r"\bpublished on\b|\bauthored by\b|^the cited entry\b|"
    r"\bis associated with\b|\bcovers (?:january|february|march|april|may|june|"
    r"july|august|september|october|november|december)\b)",
    re.IGNORECASE,
)
_TECHNOLOGY_CHANGE_RE = re.compile(
    r"\b(?:introduc(?:e|es|ed)|launch(?:es|ed)?|releas(?:e|es|ed)|add(?:s|ed)?|"
    r"support(?:s|ed)?|enable(?:s|d)?|improv(?:e|es|ed)|reduc(?:e|es|ed)|"
    r"increase(?:s|d)?|outperform(?:s|ed)?|achiev(?:e|es|ed)|propos(?:e|es|ed)|"
    r"present(?:s|ed)?|open[- ]source(?:s|d)?)\b|"
    r"新增|发布|推出|开源|支持|实现|提升|降低|加速|提出|达到|优于",
    re.IGNORECASE,
)
_TECHNOLOGY_DETAIL_RE = re.compile(
    r"\b(?:inference|serving|latency|throughput|benchmark|context|kv[- ]cache|"
    r"quantization|mixture[- ]of[- ]experts|\bmoe\b|attention|tool calling|\bmcp\b|"
    r"multi[- ]agent|multimodal|vision|audio|video|training|runtime|framework|sdk|api)\b|"
    r"推理|服务|延迟|吞吐|基准|上下文|量化|混合专家|注意力|工具调用|多智能体|多模态|训练|运行时|框架",
    re.IGNORECASE,
)
_TECHNOLOGY_META_RE = re.compile(
    r"^(?:last updated|updated quarterly|architecture at a glance|best .+ 20\d{2}|"
    r"compare the best|\d+\.|table of contents)\b|"
    r"(?:living benchmark|research papers?: the 20\d{2} list)",
    re.IGNORECASE,
)


def _technology_information_score(text: str) -> int:
    """Prefer concrete changes over headings and broad scene-setting prose."""

    compact = " ".join(text.split())
    score = 0
    score += 4 if _TECHNOLOGY_CHANGE_RE.search(compact) else 0
    score += 3 if _TECHNOLOGY_DETAIL_RE.search(compact) else 0
    score += 2 if _EXACT.search(compact) else 0
    score += 1 if re.search(r"\b(?:[A-Z]{2,}|[A-Za-z]+[-_.]\w+|\w*\d\w*)\b", compact) else 0
    score -= 6 if _TECHNOLOGY_META_RE.search(compact) else 0
    return score


def _normalized_text(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value.casefold()).strip()


def _is_low_value_extractive(text: str, *, title: str = "") -> bool:
    """Reject page chrome and bibliographic metadata before it becomes evidence."""

    compact = " ".join(text.split()).strip()
    if len(compact) < 32:
        return True
    if (
        _LOW_VALUE_EXTRACTIVE_RE.search(compact)
        or _PAGE_CHROME_RE.search(compact)
        or _PUBLICATION_METADATA_RE.search(compact)
    ):
        return True
    normalized = _normalized_text(compact)
    normalized_title = _normalized_text(title)
    if normalized_title and (
        normalized == normalized_title
        or normalized == f"{normalized_title} {normalized_title}"
    ):
        return True
    return False


def _tokens(text: str) -> set[str]:
    return {value.casefold() for value in _WORD.findall(text) if value.strip()}


def _exact_tokens(text: str) -> tuple[str, ...]:
    return tuple(value.group(0).casefold().replace(" ", "") for value in _EXACT.finditer(text))


def _normalized_exact_haystack(text: str) -> str:
    return text.casefold().replace(" ", "")


def _relevance(text: str, query: str) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & _tokens(text)) / len(query_tokens)
    return round(min(1.0, max(0.0, overlap)), 6)


def select_evidence_passages(
    documents: Sequence[Mapping[str, Any]],
    *,
    topic: str,
    maximum: int = 16,
    per_source: int = 2,
    prefer_technology_changes: bool = False,
) -> list[EvidencePassage]:
    """Select stable sentence windows from full documents, including middle sections."""

    candidates: list[EvidencePassage] = []
    per_source = max(1, min(4, int(per_source)))
    for citation_id, document in enumerate(documents, 1):
        text = str(document.get("text") or "")
        if not text.strip():
            continue
        content_hash = str(document.get("content_hash") or hashlib.sha256(text.encode()).hexdigest())
        canonical = str(document.get("canonical_url") or document.get("url") or "").strip().lower().rstrip("/")
        title = str(document.get("title") or "")
        question = str(document.get("question") or topic)
        windows: list[EvidencePassage] = []
        for paragraph in _PARAGRAPH.finditer(text):
            sentence_matches = list(_SENTENCE.finditer(paragraph.group(0)))
            if not sentence_matches:
                sentence_matches = [paragraph]
            for index in range(len(sentence_matches)):
                first = sentence_matches[index]
                last = sentence_matches[min(index + 1, len(sentence_matches) - 1)]
                start = paragraph.start() + first.start()
                end = paragraph.start() + last.end()
                if end - start > 1800:
                    end = start + 1800
                snippet = text[start:end].strip()
                if len(snippet) < 24:
                    continue
                abstract = re.search(r"\babstract\s*:\s*", snippet, re.IGNORECASE)
                if abstract and len(snippet[abstract.end():].strip()) >= 32:
                    start += abstract.end()
                    snippet = snippet[abstract.end():].strip()
                if _is_low_value_extractive(snippet, title=title):
                    continue
                passage_id = hashlib.sha256(f"{content_hash}:{start}:{end}".encode()).hexdigest()[:24]
                score = max(_relevance(snippet, question), _relevance(snippet, topic) * 0.9)
                windows.append(
                    EvidencePassage(
                        passage_id=passage_id,
                        source_citation_id=citation_id,
                        source_content_hash=content_hash,
                        canonical_url=canonical,
                        question_id=question,
                        text=snippet,
                        start=start,
                        end=end,
                        relevance=score,
                    )
                )
        windows.sort(
            key=lambda value: (
                -value.relevance,
                -(_technology_information_score(value.text) if prefer_technology_changes else 0),
                value.start,
                value.passage_id,
            )
        )
        candidates.extend(windows[:per_source])
    candidates.sort(
        key=lambda value: (
            -value.relevance,
            -(_technology_information_score(value.text) if prefer_technology_changes else 0),
            value.source_citation_id,
            value.start,
            value.passage_id,
        )
    )
    # Give every source one evidence slot before a second passage from any
    # source.  Otherwise a few highly relevant documents can crowd out the
    # rest of the source set and make synthesis look artificially narrow.
    first_per_source: list[EvidencePassage] = []
    additional: list[EvidencePassage] = []
    seen_sources: set[int] = set()
    for candidate in candidates:
        if candidate.source_citation_id in seen_sources:
            additional.append(candidate)
            continue
        seen_sources.add(candidate.source_citation_id)
        first_per_source.append(candidate)
    return (first_per_source + additional)[: max(1, int(maximum))]


def _claim_id(index: int, text: str, kind: str, citations: tuple[int, ...]) -> str:
    canonical = json.dumps([index, text, kind, citations], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]


def parse_structured_claims(raw: str, *, valid_citation_ids: set[int]) -> list[AtomicClaim]:
    payload = raw.strip()
    if payload.startswith("```") and payload.endswith("```"):
        lines = payload.splitlines()
        payload = "\n".join(lines[1:-1]).strip()
    parsed = json.loads(payload)
    if not isinstance(parsed, Mapping) or set(parsed) != {"claims"}:
        raise ValueError("invalid_synthesis_schema")
    values = parsed["claims"]
    if not isinstance(values, list):
        raise ValueError("invalid_synthesis_schema")
    claims: list[AtomicClaim] = []
    seen: set[tuple[str, str, tuple[int, ...]]] = set()
    for index, value in enumerate(values):
        if not isinstance(value, Mapping) or set(value) != {"text", "kind", "citation_ids"}:
            raise ValueError("invalid_claim_schema")
        text = re.sub(r"\s+", " ", str(value["text"])).strip()[:1000]
        kind = str(value["kind"]).lower()
        raw_ids = value["citation_ids"]
        if kind not in {"factual", "inference", "opinion"} or not text or not isinstance(raw_ids, list):
            raise ValueError("invalid_claim_schema")
        if any(isinstance(item, bool) or not isinstance(item, int) for item in raw_ids):
            raise ValueError("invalid_claim_citation")
        citations = tuple(sorted({int(item) for item in raw_ids}))
        if not citations or any(item not in valid_citation_ids for item in citations):
            raise ValueError("invalid_claim_citation")
        atomic_texts = _repair_candidates(text) if kind == "factual" else []
        for clause_index, atomic_text in enumerate(atomic_texts or [text]):
            key = (atomic_text.casefold(), kind, citations)
            if key in seen:
                continue
            seen.add(key)
            claims.append(
                AtomicClaim(
                    claim_id=_claim_id(index * 1000 + clause_index, atomic_text, kind, citations),
                    text=atomic_text,
                    kind=kind,  # type: ignore[arg-type]
                    citation_ids=citations,
                )
            )
    if not claims:
        raise ValueError("empty_synthesis")
    return claims


def fallback_claims(passages: Sequence[EvidencePassage], *, maximum: int = 16) -> list[AtomicClaim]:
    """Build replayable extractive claims when structured synthesis is unavailable."""

    ordered = sorted(passages, key=lambda value: (-value.relevance, value.source_citation_id, value.start))
    # First take one passage per source to satisfy citation diversity, then
    # fill the remaining claim budget from additional independently supported
    # passages.  Stopping after the first source pass can create an otherwise
    # high-quality report that misses the minimum body-size gate.
    seen_sources: set[int] = set()
    first_per_source: list[EvidencePassage] = []
    additional: list[EvidencePassage] = []
    for passage in ordered:
        if passage.source_citation_id in seen_sources:
            additional.append(passage)
        else:
            seen_sources.add(passage.source_citation_id)
            first_per_source.append(passage)
    claim_rows: list[tuple[str, set[int]]] = []
    claim_indexes: dict[str, int] = {}
    for passage in (*first_per_source, *additional):
        sentence = next(
            (
                value.group(0).strip()
                for value in _SENTENCE.finditer(passage.text)
                if not _is_low_value_extractive(value.group(0).strip())
            ),
            "",
        )
        if not sentence:
            continue
        sentence = sentence[:700]
        key = " ".join(sentence.casefold().split())
        existing = claim_indexes.get(key)
        if existing is not None:
            claim_rows[existing][1].add(passage.source_citation_id)
            continue
        if len(claim_rows) >= maximum:
            continue
        claim_indexes[key] = len(claim_rows)
        claim_rows.append((sentence, {passage.source_citation_id}))
    return [
        AtomicClaim(
            claim_id=_claim_id(index, text, "factual", tuple(sorted(source_ids))),
            text=text,
            kind="factual",
            citation_ids=tuple(sorted(source_ids)),
        )
        for index, (text, source_ids) in enumerate(claim_rows)
    ]


def evaluate_support(
    claims: Iterable[AtomicClaim],
    passages: Sequence[EvidencePassage],
    *,
    lexical_threshold: float = 0.55,
) -> list[SupportDecision]:
    """Evaluate every cited passage independently; evidence is never concatenated."""

    decisions: list[SupportDecision] = []
    by_source: dict[int, list[EvidencePassage]] = {}
    for passage in passages:
        by_source.setdefault(passage.source_citation_id, []).append(passage)
    for claim in claims:
        claim_tokens = _tokens(claim.text)
        exact_tokens = _exact_tokens(claim.text)
        winners: list[tuple[float, EvidencePassage]] = []
        saw_passage = False
        exact_failed = False
        for source_id in claim.citation_ids:
            for passage in by_source.get(source_id, ()):
                saw_passage = True
                haystack = _normalized_exact_haystack(passage.text)
                exact_ok = all(token in haystack for token in exact_tokens)
                exact_failed = exact_failed or not exact_ok
                overlap = len(claim_tokens & _tokens(passage.text)) / len(claim_tokens) if claim_tokens else 0.0
                if exact_ok and overlap >= lexical_threshold:
                    winners.append((overlap, passage))
        winners.sort(key=lambda value: (-value[0], value[1].source_citation_id, value[1].passage_id))
        # Keep the strongest independently supporting passage from every
        # canonical URL.  Each passage has already passed the claim check on
        # its own; retaining corroborating sources does not concatenate weak
        # evidence and prevents mirrored official text from collapsing to one
        # arbitrary citation.
        best_by_url: list[tuple[float, EvidencePassage]] = []
        seen_urls: set[str] = set()
        for winner in winners:
            passage = winner[1]
            source_key = passage.canonical_url.strip().casefold().rstrip("/") or (
                f"source:{passage.source_citation_id}"
            )
            if source_key in seen_urls:
                continue
            seen_urls.add(source_key)
            best_by_url.append(winner)
        if best_by_url:
            score = best_by_url[0][0]
            decisions.append(
                SupportDecision(
                    claim_id=claim.claim_id,
                    supported=True,
                    winning_passage_ids=tuple(value[1].passage_id for value in best_by_url),
                    winning_source_citation_ids=tuple(
                        value[1].source_citation_id for value in best_by_url
                    ),
                    reason_codes=("supported_exact_lexical",),
                    lexical_score=round(score, 6),
                )
            )
        else:
            reason = "missing_cited_passage" if not saw_passage else "missing_exact_token" if exact_failed and exact_tokens else "insufficient_lexical_support"
            decisions.append(
                SupportDecision(
                    claim_id=claim.claim_id,
                    supported=False,
                    winning_passage_ids=(),
                    winning_source_citation_ids=(),
                    reason_codes=(reason,),
                    lexical_score=0.0,
                )
            )
    return decisions


def _repair_candidates(text: str) -> list[str]:
    values = [value.strip(" ,，") for value in _CLAUSE_SEPARATOR.split(text) if value.strip(" ,，")]
    return values if len(values) > 1 else []


def repair_and_prune(
    claims: Sequence[AtomicClaim],
    passages: Sequence[EvidencePassage],
) -> tuple[list[AtomicClaim], list[SupportDecision], int, int]:
    """Perform one pure extractive clause repair, then fail-closed pruning."""

    initial = evaluate_support(claims, passages)
    decision_by_id = {value.claim_id: value for value in initial}
    published: list[AtomicClaim] = []
    published_decisions: list[SupportDecision] = []
    repaired = 0
    discarded = 0
    for claim in claims:
        decision = decision_by_id[claim.claim_id]
        if decision.supported:
            published.append(claim)
            published_decisions.append(decision)
            continue
        repaired_claim: AtomicClaim | None = None
        repaired_decision: SupportDecision | None = None
        if claim.kind == "factual":
            for clause_index, clause in enumerate(_repair_candidates(claim.text)):
                candidate = AtomicClaim(
                    claim_id=hashlib.sha256(f"{claim.claim_id}:repair:{clause_index}:{clause}".encode()).hexdigest()[:24],
                    text=clause,
                    kind=claim.kind,
                    citation_ids=claim.citation_ids,
                    repaired_from=claim.claim_id,
                )
                candidate_decision = evaluate_support((candidate,), passages)[0]
                if candidate_decision.supported:
                    repaired_claim = candidate
                    repaired_decision = candidate_decision
                    break
        if repaired_claim is not None and repaired_decision is not None:
            published.append(repaired_claim)
            published_decisions.append(repaired_decision)
            repaired += 1
        else:
            discarded += 1
    return published, published_decisions, repaired, discarded


def make_publish_decision(
    *,
    original_claims: Sequence[AtomicClaim],
    published_claims: Sequence[AtomicClaim],
    published_decisions: Sequence[SupportDecision],
    citation_sources: Mapping[int, Mapping[str, Any]],
    body_md: str,
    minimum_citations: int = 8,
) -> PublishDecision:
    factual_pre = sum(value.kind == "factual" for value in original_claims)
    factual_ids: set[str] = set()
    seen_factual_text: set[str] = set()
    for claim in published_claims:
        if claim.kind != "factual":
            continue
        key = " ".join(claim.text.casefold().split())
        if key in seen_factual_text:
            continue
        seen_factual_text.add(key)
        factual_ids.add(claim.claim_id)
    supported_by_claim = {
        value.claim_id: value
        for value in published_decisions
        if value.claim_id in factual_ids and value.supported
    }
    supported_decisions = list(supported_by_claim.values())
    supported_count = len(supported_decisions)
    support_rate = supported_count / factual_pre if factual_pre else 0.0
    used_source_ids = {
        source_id
        for decision in supported_decisions
        for source_id in decision.winning_source_citation_ids
        if source_id in citation_sources
    }
    canonical_urls = {
        str(citation_sources[source_id].get("canonical_url") or citation_sources[source_id].get("url") or "")
        .strip().casefold().rstrip("/")
        for source_id in used_source_ids
    } - {""}
    domains = {
        urlparse(url).netloc.lower()
        for url in canonical_urls
    } - {""}
    body_bytes = len(body_md.encode("utf-8"))
    reasons: list[str] = []
    if support_rate < 0.60:
        reasons.append("support_rate_below_threshold")
    if supported_count < 8:
        reasons.append("published_factual_below_threshold")
    if len(canonical_urls) < max(1, int(minimum_citations)):
        reasons.append("citation_count_below_threshold")
    if len(domains) < 4:
        reasons.append("domain_count_below_threshold")
    if body_bytes < 1500:
        reasons.append("body_bytes_below_threshold")
    return PublishDecision(
        passed=not reasons,
        factual_claim_count_pre_repair=factual_pre,
        supported_factual_count=supported_count,
        published_factual_count=supported_count,
        support_rate=round(support_rate, 6),
        citation_count=len(canonical_urls),
        independent_domain_count=len(domains),
        body_bytes=body_bytes,
        reason_codes=tuple(reasons),
    )


def quality_payload(
    *,
    original_claims: Sequence[AtomicClaim],
    published_claims: Sequence[AtomicClaim],
    published_decisions: Sequence[SupportDecision],
    repaired: int,
    discarded: int,
    decision: PublishDecision,
) -> dict[str, Any]:
    return {
        "claims": [value.to_json() for value in original_claims],
        "published_claims": [value.to_json() for value in published_claims],
        "decisions": [value.to_json() for value in published_decisions],
        "factual_claim_count_pre_repair": decision.factual_claim_count_pre_repair,
        "supported_factual_count": decision.supported_factual_count,
        "published_factual_count": decision.published_factual_count,
        "unsupported_count": discarded,
        "repaired_count": repaired,
        "discarded_count": discarded,
        "support_rate": decision.support_rate,
        "citation_count": decision.citation_count,
        "independent_domain_count": decision.independent_domain_count,
        "body_bytes": decision.body_bytes,
        "publish_decision": decision.to_json(),
    }


__all__ = [
    "evaluate_support", "fallback_claims", "make_publish_decision",
    "parse_structured_claims", "quality_payload", "repair_and_prune",
    "select_evidence_passages",
]

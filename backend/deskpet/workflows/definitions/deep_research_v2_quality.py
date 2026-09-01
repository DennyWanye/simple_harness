"""Deterministic claim-to-evidence support checks for DeepResearch v2."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Literal

_CITATION = re.compile(r"\[(\d+)\]")
_SENTENCE = re.compile(r"(?<=[。！？.!?])\s*")
_TOKEN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_EXACT = re.compile(r"(?:\d{4}[-年/]\d{1,2}(?:[-月/]\d{1,2}日?)?|\d+(?:\.\d+)?%?|[$¥€£]\s*\d+(?:\.\d+)?)")
_ANALYSIS = re.compile(r"(?:可能|意味着|推测|或许|likely|may|might|suggests)", re.IGNORECASE)
_OPINION = re.compile(r"(?:应该|最好|建议|must|should|recommend)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ClaimRecord:
    claim_id: str
    text_hash: str
    paragraph_index: int
    citation_ids: tuple[int, ...]
    kind: Literal["factual", "analysis", "opinion"]
    text: str


@dataclass(frozen=True, slots=True)
class SupportDecision:
    claim_id: str
    status: Literal["supported", "unsupported", "inference"]
    evidence_ids: tuple[int, ...]
    lexical_score: float
    semantic_score: float | None
    reason_code: str


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN.findall(text) if len(token) > 1}


def _clean_claim_text(text: str) -> str:
    return re.sub(r"\s+([。！？.!?])", r"\1", _CITATION.sub("", text)).strip()


def _replace_claim_once(markdown: str, claim: ClaimRecord, replacement: str) -> str:
    for match in re.finditer(r"[^。！？.!?]+[。！？.!?]?", markdown):
        raw = match.group(0).strip()
        if _clean_claim_text(raw) != claim.text:
            continue
        punctuation = raw[-1] if raw and raw[-1] in "。！？.!?" else ""
        rendered = replacement.strip()
        if rendered and punctuation and not rendered.endswith(tuple("。！？.!?")):
            rendered += punctuation
        return markdown[: match.start()] + match.group(0).replace(raw, rendered, 1) + markdown[match.end():]
    raise ValueError("repair_claim_not_found")


def lexical_score(claim: str, evidence: str) -> float:
    left, right = _tokens(claim), _tokens(evidence)
    return len(left & right) / len(left | right) if left and right else 0.0


def parse_claims(markdown: str) -> list[ClaimRecord]:
    claims: list[ClaimRecord] = []
    paragraph_index = -1
    for raw in re.split(r"\n\s*\n", markdown):
        paragraph = raw.strip()
        if not paragraph or paragraph.startswith(("#", "```")) or paragraph.lower().startswith(("references", "引用")):
            continue
        paragraph_index += 1
        sentences = [value.strip() for value in _SENTENCE.split(paragraph) if value.strip()]
        trailing = tuple(int(value) for value in _CITATION.findall(sentences[-1])) if sentences else ()
        for sentence_index, sentence in enumerate(sentences):
            citations = tuple(int(value) for value in _CITATION.findall(sentence))
            text = _clean_claim_text(sentence)
            if not text:
                continue
            if not citations and trailing and sentence_index < len(sentences):
                citations = trailing
            kind: Literal["factual", "analysis", "opinion"]
            if _ANALYSIS.search(text):
                kind = "analysis"
            elif _OPINION.search(text):
                kind = "opinion"
            else:
                kind = "factual"
            digest = hashlib.sha256(f"{paragraph_index}:{sentence_index}:{text}".encode()).hexdigest()
            claims.append(ClaimRecord(digest[:20], hashlib.sha256(text.encode()).hexdigest(), paragraph_index, citations, kind, text))
    return claims


def evaluate_support(
    claims: Iterable[ClaimRecord],
    evidence: Mapping[int, str],
    *,
    semantic_scorer: Callable[[str, str], float] | None = None,
) -> list[SupportDecision]:
    decisions: list[SupportDecision] = []
    for claim in claims:
        valid = tuple(value for value in claim.citation_ids if evidence.get(value, "").strip())
        combined = "\n".join(evidence[value] for value in valid)
        lexical = lexical_score(claim.text, combined) if combined else 0.0
        semantic = semantic_scorer(claim.text, combined) if semantic_scorer and combined else None
        exact_ok = all(token in combined for token in _EXACT.findall(claim.text))
        supported = bool(valid and exact_ok and (lexical >= 0.15 or (semantic is not None and semantic >= 0.72)))
        if supported:
            status = "inference" if claim.kind in {"analysis", "opinion"} else "supported"
            reason = "supported_semantic" if semantic is not None and semantic >= 0.72 else "supported_lexical"
        else:
            status, reason = "unsupported", "missing_exact_token" if valid and not exact_ok else "insufficient_support"
        decisions.append(SupportDecision(claim.claim_id, status, valid, lexical, semantic, reason))
    return decisions


def apply_repair(
    markdown: str,
    claims: Iterable[ClaimRecord],
    repair: Mapping[str, object],
    *,
    valid_citation_ids: set[int],
    already_repaired: bool = False,
) -> str:
    """Apply one schema-constrained repair without allowing new facts or sources."""

    if already_repaired:
        raise ValueError("repair_already_attempted")
    if set(repair) - {"replacements", "removals"}:
        raise ValueError("invalid_repair_schema")
    claim_map = {value.claim_id: value for value in claims}
    removals = repair.get("removals", [])
    replacements = repair.get("replacements", [])
    if not isinstance(removals, list) or not isinstance(replacements, list):
        raise ValueError("invalid_repair_schema")
    output = markdown
    seen: set[str] = set()
    for claim_id in removals:
        key = str(claim_id)
        if key not in claim_map or key in seen:
            raise ValueError("invalid_repair_claim")
        seen.add(key)
        output = _replace_claim_once(output, claim_map[key], "")
    for raw in replacements:
        if not isinstance(raw, Mapping) or set(raw) != {"claim_id", "replacement", "citation_ids"}:
            raise ValueError("invalid_repair_schema")
        key = str(raw["claim_id"])
        replacement = str(raw["replacement"]).strip()
        citation_ids = raw["citation_ids"]
        if key not in claim_map or key in seen or not replacement or len(replacement) > 500:
            raise ValueError("invalid_repair_claim")
        if not isinstance(citation_ids, list) or any(
            isinstance(value, bool) or not isinstance(value, int) or value not in valid_citation_ids
            for value in citation_ids
        ):
            raise ValueError("invalid_repair_citation")
        seen.add(key)
        rendered = replacement + " " + " ".join(f"[{value}]" for value in citation_ids)
        output = _replace_claim_once(output, claim_map[key], rendered)
    return output


def quality_payload(claims: Iterable[ClaimRecord], decisions: Iterable[SupportDecision]) -> dict:
    claim_values, decision_values = list(claims), list(decisions)
    supported = sum(value.status != "unsupported" for value in decision_values)
    return {
        "claims": [
            {**asdict(value), "citation_ids": list(value.citation_ids)}
            for value in claim_values
        ],
        "decisions": [
            {**asdict(value), "evidence_ids": list(value.evidence_ids)}
            for value in decision_values
        ],
        "claim_count": len(claim_values),
        "supported_claim_count": supported,
        "unsupported_count": len(decision_values) - supported,
        "support_rate": supported / len(decision_values) if decision_values else 0.0,
    }


__all__ = ["ClaimRecord", "SupportDecision", "apply_repair", "evaluate_support", "lexical_score", "parse_claims", "quality_payload"]

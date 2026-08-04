"""Deterministic scalar extraction for DeepResearch v6 official facts.

The extractor is driven by the frozen requirement definition.  It deliberately
does not know an expected value, a source URL, or a complete user question.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final, Iterable

from ..contracts import canonical_json


_WIRE_REF_RE: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_NUMBER_RE: Final = r"(?P<value>[0-9][0-9,，]*(?:\.[0-9]+)?)"
_UNIT_RE: Final = r"(?P<unit>亿人|万人|人)"


@dataclass(frozen=True, slots=True)
class ScalarEvidenceRequest:
    requirement_id: str
    definition: str
    canonical_unit: str
    time_label: str

    def __post_init__(self) -> None:
        if not self.requirement_id:
            raise ValueError("requirement_id is required")
        if self.canonical_unit != "person":
            raise ValueError("v6 official population facts require canonical unit person")


@dataclass(frozen=True, slots=True)
class ExtractedScalarEvidence:
    binding_id: str
    span_id: str
    requirement_id: str
    page_id: str
    body_ref: str
    start_byte: int
    end_byte: int
    excerpt: str
    excerpt_hash: str
    parsed_value: str
    normalized_value: int
    source_unit: str
    canonical_unit: str
    definition: str
    time_label: str


_PATTERNS: Final[dict[str, tuple[re.Pattern[str], ...]]] = {
    "year_end_total_population": (
        re.compile(rf"(?:[12][0-9]{{3}}年)?年末全国人口[^。；;\n]{{0,20}}?{_NUMBER_RE}\s*{_UNIT_RE}"),
        re.compile(rf"(?:[12][0-9]{{3}}年)?年末总人口[^。；;\n]{{0,20}}?{_NUMBER_RE}\s*{_UNIT_RE}"),
    ),
    "births_during_period": (
        re.compile(rf"(?:[12][0-9]{{3}}年)?全年出生人口[^。；;\n]{{0,20}}?{_NUMBER_RE}\s*{_UNIT_RE}"),
        re.compile(rf"(?:[12][0-9]{{3}}年)?出生人口[^。；;\n]{{0,20}}?{_NUMBER_RE}\s*{_UNIT_RE}"),
    ),
}

_REJECTED_REGION_MARKERS: Final = ("AI摘要", "免责声明", "机器生成", "仅供参考")


def _to_person(value: str, unit: str) -> int:
    normalized = value.replace(",", "").replace("，", "")
    multiplier = {"人": 1, "万人": 10_000, "亿人": 100_000_000}[unit]
    scaled = float(normalized) * multiplier
    if not scaled.is_integer():
        raise ValueError("population value is not an integer number of persons")
    return int(scaled)


def _byte_offset(text: str, character_offset: int) -> int:
    return len(text[:character_offset].encode("utf-8"))


def _candidate_matches(
    body: str,
    request: ScalarEvidenceRequest,
) -> Iterable[re.Match[str]]:
    patterns = _PATTERNS.get(request.definition, ())
    year_match = re.search(r"(?P<year>[12][0-9]{3})年", request.time_label)
    required_year = year_match.group("year") if year_match is not None else None
    for pattern in patterns:
        for match in pattern.finditer(body):
            line_start = body.rfind("\n", 0, match.start()) + 1
            line_end = body.find("\n", match.end())
            if line_end < 0:
                line_end = len(body)
            line = body[line_start:line_end]
            if any(marker in line for marker in _REJECTED_REGION_MARKERS):
                continue
            if required_year is not None:
                nearby_start = max(line_start, match.start() - 16)
                nearby = body[nearby_start : match.end()]
                explicit_years = re.findall(r"(?<![0-9])([12][0-9]{3})年", nearby)
                if explicit_years and explicit_years[-1] != required_year:
                    continue
            yield match


def extract_scalar_evidence(
    *,
    body: str,
    body_ref: str,
    page_id: str,
    requests: Iterable[ScalarEvidenceRequest],
) -> tuple[ExtractedScalarEvidence, ...]:
    if not _WIRE_REF_RE.fullmatch(body_ref):
        raise ValueError("body_ref must be a canonical sha256 wire ref")
    if not page_id:
        raise ValueError("page_id is required")
    results: list[ExtractedScalarEvidence] = []
    for request in requests:
        match = next(iter(_candidate_matches(body, request)), None)
        if match is None:
            continue
        excerpt = match.group(0)
        excerpt_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        start_byte = _byte_offset(body, match.start())
        end_byte = _byte_offset(body, match.end())
        span_seed = canonical_json(
            {
                "body_ref": body_ref,
                "end_byte": end_byte,
                "excerpt_hash": excerpt_hash,
                "page_id": page_id,
                "start_byte": start_byte,
            }
        )
        span_id = "span_" + hashlib.sha256(span_seed.encode("utf-8")).hexdigest()[:24]
        normalized_value = _to_person(match.group("value"), match.group("unit"))
        binding_seed = canonical_json(
            {
                "canonical_unit": request.canonical_unit,
                "definition": request.definition,
                "normalized_value": normalized_value,
                "requirement_id": request.requirement_id,
                "span_id": span_id,
                "time_label": request.time_label,
            }
        )
        binding_id = "bind_" + hashlib.sha256(binding_seed.encode("utf-8")).hexdigest()[:24]
        results.append(
            ExtractedScalarEvidence(
                binding_id=binding_id,
                span_id=span_id,
                requirement_id=request.requirement_id,
                page_id=page_id,
                body_ref=body_ref,
                start_byte=start_byte,
                end_byte=end_byte,
                excerpt=excerpt,
                excerpt_hash=excerpt_hash,
                parsed_value=match.group("value"),
                normalized_value=normalized_value,
                source_unit=match.group("unit"),
                canonical_unit=request.canonical_unit,
                definition=request.definition,
                time_label=request.time_label,
            )
        )
    return tuple(results)


__all__ = [
    "ExtractedScalarEvidence",
    "ScalarEvidenceRequest",
    "extract_scalar_evidence",
]

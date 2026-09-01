"""Deterministic readiness gate and renderer for v6 scalar exact facts."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from html import escape

from ..contracts import canonical_json
from .deep_research_v6_exact_fact import ExtractedScalarEvidence, ScalarEvidenceRequest


@dataclass(frozen=True, slots=True)
class CitationView:
    title: str
    url: str

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.url.startswith(("https://", "http://")):
            raise ValueError("citation requires a title and an HTTP(S) URL")


@dataclass(frozen=True, slots=True)
class ExactFactClaim:
    claim_id: str
    requirement_id: str
    normalized_proposition: str
    binding_id: str
    support_status: str = "supported"


@dataclass(frozen=True, slots=True)
class ExactFactReportResult:
    answer_status: str
    final_assistant: str
    report_markdown: str | None
    claims: tuple[ExactFactClaim, ...]
    missing_requirement_ids: tuple[str, ...]


def _display_value(item: ExtractedScalarEvidence) -> str:
    return f"{item.parsed_value}{item.source_unit}"


def assess_and_render_exact_facts(
    *,
    requests: Iterable[ScalarEvidenceRequest],
    evidence: Iterable[ExtractedScalarEvidence],
    citations: Mapping[str, CitationView],
) -> ExactFactReportResult:
    ordered_requests = tuple(requests)
    evidence_by_requirement: dict[str, ExtractedScalarEvidence] = {}
    for item in evidence:
        if item.requirement_id in evidence_by_requirement:
            raise ValueError(f"multiple winning bindings for {item.requirement_id}")
        if item.page_id not in citations:
            raise ValueError(f"missing citation view for {item.page_id}")
        evidence_by_requirement[item.requirement_id] = item

    request_ids = {item.requirement_id for item in ordered_requests}
    unknown = set(evidence_by_requirement) - request_ids
    if unknown:
        raise ValueError(f"evidence references unknown requirements: {sorted(unknown)}")

    missing = tuple(
        item.requirement_id
        for item in ordered_requests
        if item.requirement_id not in evidence_by_requirement
    )
    if len(missing) == len(ordered_requests):
        return ExactFactReportResult(
            answer_status="insufficient_evidence",
            final_assistant=(
                "没有找到同时满足时间、口径和权威来源约束的证据，因此不能可靠给出这些数值。"
            ),
            report_markdown=None,
            claims=(),
            missing_requirement_ids=missing,
        )

    lines: list[str] = []
    claims: list[ExactFactClaim] = []
    for request in ordered_requests:
        item = evidence_by_requirement.get(request.requirement_id)
        if item is None:
            lines.append(f"- {request.time_label}（{request.definition}）：未找到满足约束的权威证据。")
            continue
        citation = citations[item.page_id]
        title = escape(citation.title.strip(), quote=False).replace("[", "\\[").replace("]", "\\]")
        proposition = f"{request.time_label}{'全国人口' if request.definition == 'year_end_total_population' else '出生人口'}：{_display_value(item)}"
        lines.append(f"- {proposition}（[{title}]({citation.url})）")
        claim_seed = canonical_json(
            {
                "binding_id": item.binding_id,
                "normalized_proposition": proposition,
                "requirement_id": request.requirement_id,
            }
        )
        claims.append(
            ExactFactClaim(
                claim_id="claim_" + hashlib.sha256(claim_seed.encode("utf-8")).hexdigest()[:24],
                requirement_id=request.requirement_id,
                normalized_proposition=proposition,
                binding_id=item.binding_id,
            )
        )

    status = "completed" if not missing else "partial"
    final_assistant = "\n".join(lines)
    return ExactFactReportResult(
        answer_status=status,
        final_assistant=final_assistant,
        report_markdown="# 调研结论\n\n" + final_assistant,
        claims=tuple(claims),
        missing_requirement_ids=missing,
    )


__all__ = [
    "CitationView",
    "ExactFactClaim",
    "ExactFactReportResult",
    "assess_and_render_exact_facts",
]

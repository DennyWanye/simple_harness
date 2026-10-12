"""Deterministic typed renderers for non-scalar DeepResearch v6 intents.

The renderer is deliberately a read-only projection.  ``ClaimRecordV1`` owns
every displayable proposition; ``GenericAdmittedFactV1`` only supplies the
assessment-owned item order and claim-set facets needed to choose a layout.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape

from .deep_research_v6_assessment import (
    GenericAdmittedFactV1,
    derive_matrix_cell_id,
    rank_collection_item_ids,
)
from .deep_research_v6_contracts import ResearchSpecV1
from .deep_research_v6_evidence import AnswerAssessmentV1
from .deep_research_v6_integrity import ClaimRecordV1

_NON_HTTP_LINK = re.compile(r"\[([^\]]+)\]\((?!https?://)[^)]+\)", re.IGNORECASE)
_STATUS = {
    "completed_candidate": "completed",
    "partial_candidate": "partial",
    "insufficient": "insufficient_evidence",
    "needs_evidence": "insufficient_evidence",
}


@dataclass(frozen=True, slots=True)
class CitationView:
    """Non-owning display metadata for a registered evidence locator."""

    title: str
    url: str

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("citation title must not be empty")
        if not self.url.startswith(("https://", "http://")):
            raise ValueError("citation URL must use HTTP(S)")


@dataclass(frozen=True, slots=True)
class TypedReportResult:
    answer_status: str
    final_assistant: str
    report_markdown: str | None
    claims: tuple[ClaimRecordV1, ...]
    missing_requirement_ids: tuple[str, ...]


def _labels(locale: str) -> dict[str, str]:
    if locale.casefold().startswith("zh"):
        return {
            "answer": "结论",
            "report": "调研报告",
            "facts": "事实",
            "inferences": "推断",
            "preferences": "偏好判断",
            "ranking": "排名结果",
            "policy_facts": "政策事实",
            "issuer": "发布方",
            "document": "文件",
            "date": "日期",
            "commitment": "原文承诺",
            "impact": "影响推断",
            "conclusion": "结论",
            "limitation": "局限",
            "counterevidence": "反证",
            "uncertainty": "不确定性",
            "none": "暂无已支持的内容。",
            "gaps": "证据缺口",
            "missing": "尚未满足：",
            "partial": "结果不完整：仅找到 {actual} 个满足约束的条目，目标为 {target} 个；未补空槽。",
            "insufficient": "没有足够的已准入证据来形成可靠答案。",
        }
    return {
        "answer": "Answer",
        "report": "Research report",
        "facts": "Facts",
        "inferences": "Inferences",
        "preferences": "Preference judgments",
        "ranking": "Ranked results",
        "policy_facts": "Policy facts",
        "issuer": "Issuer",
        "document": "Document",
        "date": "Date",
        "commitment": "Original commitment",
        "impact": "Impact inferences",
        "conclusion": "Conclusion",
        "limitation": "Limitations",
        "counterevidence": "Counterevidence",
        "uncertainty": "Uncertainty",
        "none": "No supported content is available.",
        "gaps": "Evidence gaps",
        "missing": "Not yet satisfied: ",
        "partial": "Partial result: {actual} eligible items were found for a target of {target}; no empty slots were added.",
        "insufficient": "There is not enough admitted evidence to provide a reliable answer.",
    }


def _safe_proposition(value: str) -> str:
    # Citations have their own typed HTTP(S)-only channel.  A proposition may
    # retain ordinary text but cannot smuggle a non-HTTP Markdown link into it.
    return _NON_HTTP_LINK.sub(r"\1", value.strip())


def _citation_suffix(claim_id: str, citations: Mapping[str, CitationView]) -> str:
    citation = citations.get(claim_id)
    if citation is None:
        return ""
    title = escape(citation.title.strip(), quote=False).replace("[", "\\[").replace("]", "\\]")
    return f" ([{title}]({citation.url}))"


def _result_index(assessment: AnswerAssessmentV1) -> dict[tuple[str, str], dict[str, object]]:
    return {
        (str(item["requirement_id"]), str(item["item_or_cell_id"])): dict(item)
        for item in assessment.requirement_results
    }


def _visible_claims(
    *,
    spec: ResearchSpecV1,
    assessment: AnswerAssessmentV1,
    claims: Sequence[ClaimRecordV1],
) -> tuple[ClaimRecordV1, ...]:
    if assessment.spec_hash != spec.spec_hash:
        raise ValueError("assessment/spec hash mismatch")
    requirement_ids = {str(item["requirement_id"]) for item in spec.requirements}
    results = _result_index(assessment)
    visible: list[ClaimRecordV1] = []
    for claim in claims:
        if claim.visibility != "user" or claim.support_status != "supported":
            continue
        if claim.requirement_id not in requirement_ids:
            raise ValueError("claim references an unknown requirement")
        identity = (claim.requirement_id, str(claim.item_or_cell_id))
        result = results.get(identity)
        if result is None or result["support_status"] != "supported":
            raise ValueError("visible claim is not supported by the assessment")
        if not set(claim.binding_ids).issubset(set(result["binding_ids"])):
            raise ValueError("visible claim bindings differ from the assessment")
        if claim.claim_kind == "inference" and claim.inference_ref is None:
            raise ValueError("visible inference must reference a registered inference")
        if claim.claim_kind != "inference" and not claim.binding_ids:
            raise ValueError("visible non-inference must reference admitted bindings")
        visible.append(claim)
    return tuple(sorted(visible, key=lambda item: item.claim_id))


def _claim_line(claim: ClaimRecordV1, citations: Mapping[str, CitationView]) -> str:
    return f"- {_safe_proposition(claim.normalized_proposition)}{_citation_suffix(claim.claim_id, citations)}"


def _missing_result_ids(assessment: AnswerAssessmentV1) -> tuple[str, ...]:
    return tuple(
        str(item["item_or_cell_id"])
        for item in assessment.requirement_results
        if item["support_status"] != "supported"
    )


def _append_gaps(lines: list[str], assessment: AnswerAssessmentV1, labels: Mapping[str, str]) -> None:
    missing = _missing_result_ids(assessment)
    if not missing:
        return
    lines.extend(("", f"## {labels['gaps']}", f"{labels['missing']}{', '.join(missing)}"))


def _render_comparison(
    requirement: Mapping[str, object],
    claims: Sequence[ClaimRecordV1],
    citations: Mapping[str, CitationView],
    labels: Mapping[str, str],
) -> list[str]:
    invalid = sorted({claim.claim_kind for claim in claims} - {"fact", "inference", "preference"})
    if invalid:
        raise ValueError(f"comparison contains unsupported claim kinds: {invalid}")
    lines = [f"## {labels['answer']}"]
    axes = requirement["axes"]
    cell_labels: dict[str, str] = {}
    for members in itertools.product(*(axis["members"] for axis in axes)):  # type: ignore[index]
        member_ids = tuple(str(member["member_id"]) for member in members)
        cell_labels[derive_matrix_cell_id(str(requirement["requirement_id"]), member_ids)] = " × ".join(
            str(member["label"]) for member in members
        )
    cells = sorted({str(claim.item_or_cell_id) for claim in claims}, key=lambda value: cell_labels.get(value, value))
    category = (("fact", "facts"), ("inference", "inferences"), ("preference", "preferences"))
    for cell_id in cells:
        lines.extend(("", f"### {cell_labels.get(cell_id, cell_id)}"))
        cell_claims = [claim for claim in claims if str(claim.item_or_cell_id) == cell_id]
        for kind, label_key in category:
            selected = [claim for claim in cell_claims if claim.claim_kind == kind]
            lines.extend(("", f"#### {labels[label_key]}"))
            lines.extend(_claim_line(claim, citations) for claim in selected)
            if not selected:
                lines.append(labels["none"])
    return lines


def _render_top_n(
    *,
    requirement: Mapping[str, object],
    facts: Sequence[GenericAdmittedFactV1],
    claims: Sequence[ClaimRecordV1],
    citations: Mapping[str, CitationView],
    labels: Mapping[str, str],
) -> tuple[list[str], int, int]:
    ranked_ids = rank_collection_item_ids(requirement, facts)  # single ranking owner
    unknown_items = {
        str(claim.item_or_cell_id) for claim in claims
    } - set(ranked_ids)
    if unknown_items:
        raise ValueError(f"ranked claims are absent from the assessment ranking: {sorted(unknown_items)}")
    by_item = {
        item_id: [claim for claim in claims if str(claim.item_or_cell_id) == item_id]
        for item_id in ranked_ids
    }
    # Assessment may support an item before synthesis produces a visible claim.
    # Such an item is not displayed as an empty row.
    actual_ids = tuple(item_id for item_id in ranked_ids if by_item[item_id])
    target = int(requirement["selection"]["minimum_items"])  # type: ignore[index]
    lines = [f"## {labels['answer']}", "", f"### {labels['ranking']}"]
    for rank, item_id in enumerate(actual_ids, start=1):
        lines.extend(("", f"{rank}."))
        lines.extend(_claim_line(claim, citations) for claim in by_item[item_id])
    if len(actual_ids) < target:
        lines.extend(("", labels["partial"].format(actual=len(actual_ids), target=target)))
    return lines, len(actual_ids), target


def _semantic_kind_by_item(facts: Sequence[GenericAdmittedFactV1]) -> dict[str, str]:
    result: dict[str, str] = {}
    for fact in facts:
        if fact.item_or_cell_id is None or fact.claim_kind is None:
            continue
        previous = result.setdefault(fact.item_or_cell_id, fact.claim_kind)
        if previous != fact.claim_kind:
            raise ValueError("one claim identity maps to multiple semantic kinds")
    return result


def _facet_by_item(facts: Sequence[GenericAdmittedFactV1]) -> dict[str, tuple[str, ...]]:
    result: dict[str, set[str]] = {}
    for fact in facts:
        if fact.item_or_cell_id is not None:
            result.setdefault(fact.item_or_cell_id, set()).update(fact.facet_ids)
    return {key: tuple(sorted(value)) for key, value in result.items()}


def _render_policy(
    *,
    facts: Sequence[GenericAdmittedFactV1],
    claims: Sequence[ClaimRecordV1],
    citations: Mapping[str, CitationView],
    labels: Mapping[str, str],
) -> list[str]:
    kinds = _semantic_kind_by_item(facts)
    facets = _facet_by_item(facts)
    allowed_facets = {"issuer", "document", "date", "commitment"}
    for claim in claims:
        item_id = str(claim.item_or_cell_id)
        semantic_kind = kinds.get(item_id)
        if semantic_kind == "impact_inference":
            if claim.claim_kind != "inference":
                raise ValueError("policy impact must be a registered inference claim")
        elif semantic_kind == "policy_fact":
            if claim.claim_kind != "fact" or not (set(facets.get(item_id, ())) & allowed_facets):
                raise ValueError("policy fact must map to an issuer/document/date/commitment facet")
        else:
            raise ValueError("policy claim is absent from admitted semantic facts")
    lines = [f"## {labels['answer']}", "", f"### {labels['policy_facts']}"]
    for facet in ("issuer", "document", "date", "commitment"):
        selected = [
            claim for claim in claims
            if kinds.get(str(claim.item_or_cell_id)) == "policy_fact"
            and facet in facets.get(str(claim.item_or_cell_id), ())
        ]
        lines.extend(("", f"#### {labels[facet]}"))
        lines.extend(_claim_line(claim, citations) for claim in selected)
        if not selected:
            lines.append(labels["none"])
    impact = [
        claim for claim in claims
        if kinds.get(str(claim.item_or_cell_id)) == "impact_inference"
    ]
    lines.extend(("", f"### {labels['impact']}"))
    lines.extend(_claim_line(claim, citations) for claim in impact)
    if not impact:
        lines.append(labels["none"])
    return lines


def _render_open_research(
    *,
    facts: Sequence[GenericAdmittedFactV1],
    claims: Sequence[ClaimRecordV1],
    citations: Mapping[str, CitationView],
    labels: Mapping[str, str],
) -> list[str]:
    kinds = _semantic_kind_by_item(facts)
    allowed = {"conclusion", "limitation", "counterevidence", "uncertainty"}
    unknown = {
        str(claim.item_or_cell_id)
        for claim in claims
        if kinds.get(str(claim.item_or_cell_id)) not in allowed
    }
    if unknown:
        raise ValueError(f"open-research claims lack a typed semantic kind: {sorted(unknown)}")
    lines = [f"## {labels['answer']}"]
    for semantic_kind in ("conclusion", "limitation", "counterevidence", "uncertainty"):
        selected = [
            claim for claim in claims
            if kinds.get(str(claim.item_or_cell_id)) == semantic_kind
        ]
        lines.extend(("", f"### {labels[semantic_kind]}"))
        lines.extend(_claim_line(claim, citations) for claim in selected)
        if not selected:
            lines.append(labels["none"])
    return lines


def render_typed_report(
    *,
    spec: ResearchSpecV1,
    assessment: AnswerAssessmentV1,
    admitted_facts: Sequence[GenericAdmittedFactV1],
    claims: Sequence[ClaimRecordV1],
    citations: Mapping[str, CitationView] | None = None,
) -> TypedReportResult:
    """Render comparison, Top-N, policy, or open-research output."""

    if spec.intent_type == "official_exact_fact":
        raise ValueError("official_exact_fact must keep using the Q1 exact renderer")
    citations = citations or {}
    labels = _labels(spec.answer_locale)
    visible = _visible_claims(spec=spec, assessment=assessment, claims=claims)
    status = _STATUS[assessment.status]
    if spec.intent_type == "comparison":
        lines = _render_comparison(spec.requirements[0], visible, citations, labels)
    elif spec.intent_type == "top_n":
        requirement = spec.requirements[0]
        lines, actual, target = _render_top_n(
            requirement=requirement,
            facts=admitted_facts,
            claims=visible,
            citations=citations,
            labels=labels,
        )
        status = "completed" if actual >= target else ("partial" if actual else "insufficient_evidence")
    elif spec.intent_type == "policy":
        lines = _render_policy(facts=admitted_facts, claims=visible, citations=citations, labels=labels)
    elif spec.intent_type == "open_research":
        lines = _render_open_research(facts=admitted_facts, claims=visible, citations=citations, labels=labels)
    else:  # defensive: ResearchSpecV1 already validates the enum
        raise ValueError(f"unsupported renderer intent: {spec.intent_type}")

    _append_gaps(lines, assessment, labels)
    if status == "insufficient_evidence":
        return TypedReportResult(
            answer_status=status,
            final_assistant=labels["insufficient"],
            report_markdown=None,
            claims=(),
            missing_requirement_ids=assessment.missing_requirement_ids,
        )
    final_assistant = "\n".join(lines)
    return TypedReportResult(
        answer_status=status,
        final_assistant=final_assistant,
        report_markdown=f"# {labels['report']}\n\n{final_assistant}",
        claims=visible,
        missing_requirement_ids=assessment.missing_requirement_ids,
    )


__all__ = ["CitationView", "TypedReportResult", "render_typed_report"]

"""Privacy-safe, implementation-independent DeepResearch report acceptance.

The evaluator reads a final Markdown artifact and emits only fixed rule codes,
booleans, and counts.  It never returns headings, entity names, citations,
URLs, report excerpts, or the input/output paths.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any


_MAX_REPORT_BYTES = 5 * 1024 * 1024
_REQUIRED_SECTIONS = {
    "executive_summary": re.compile(r"(?m)^##\s+一页式执行摘要\s*$"),
    "top_findings": re.compile(r"(?m)^##\s+分主题\s+Top\s+技术(?:[^\n]*)$", re.I),
    "methodology": re.compile(r"(?m)^##\s+方法与局限\s*$"),
    "citations": re.compile(r"(?m)^##\s+引用(?:附录|\s+Appendix)?\s*$", re.I),
}
_FINDING_HEADING = re.compile(
    r"(?m)^####\s+(?:\d{1,2}\s*[.、)]\s*)?(.+?)\s*$"
)
_FIELD_PATTERNS = {
    "core_change": re.compile(r"(?m)^-\s*核心变化[：:]\s*(\S.*)$"),
    "value": re.compile(r"(?m)^-\s*价值判断[：:]\s*(\S.*)$"),
    "maturity": re.compile(r"(?m)^-\s*成熟度与采用建议[：:]\s*(\S.*)$"),
    "risk": re.compile(r"(?m)^-\s*风险与不确定性[：:]\s*(\S.*)$"),
    "citations": re.compile(r"(?m)^-\s*引用[：:]\s*(\S.*)$"),
}
_CITATION = re.compile(r"\[(\d{1,6})\]")
_APPENDIX_CITATION = re.compile(r"(?m)^\[(\d{1,6})\]\s+\S")
_MAPPED_APPENDIX_CITATION = re.compile(
    r"(?m)^\[(\d{1,6})\]\s+支持条目[：:].+?来源类型[：:]"
)
_SCORE = re.compile(r"总分\s*([01](?:\.\d+)?)", re.I)
_RECENCY = re.compile(r"近期性\s*([01](?:\.\d+)?)", re.I)
_EVIDENCE_QUALITY = re.compile(r"证据质量\s*([0-3])\s*/\s*3", re.I)
_UNKNOWN_MATURITY = re.compile(r"成熟度(?:待核验|未知)|证据不足|\bunknown\b", re.I)
_DIRECT_ADOPTION = re.compile(r"适合开展限界\s*PoC|可进入受控采用|可直接进入限界\s*PoC", re.I)
_SUMMARY_SIGNAL = re.compile(
    r"总分|生产可用|正在成熟|实验阶段|成熟度|采用|PoC|技术验证|暂缓生产",
    re.I,
)
_PLACEHOLDER = re.compile(
    r"当前证据未形成|暂无可发布|待补充|占位|placeholder|第\s*\d+\s*项可核验技术更新",
    re.I,
)
_FORBIDDEN = {
    "coverage_heading": re.compile(r"(?im)^#{1,6}\s*coverage\b"),
    "provider_attempts": re.compile(r"\bprovider_attempts\b", re.I),
    "published_claims": re.compile(r"\bpublished_claims\b", re.I),
    "raw_query": re.compile(r"\braw[\s_-]*query\b", re.I),
    "debug_json": re.compile(r"\bdebug\s*json\b|```\s*json\b", re.I),
    "missing_citation": re.compile(r"缺少引用|missing\s+citation", re.I),
    "ehr_noise": re.compile(r"\bEHR\b|electronic\s+health\s+records?", re.I),
    "healthcare_noise": re.compile(r"\bhealthcare\b", re.I),
    "navigation_noise": re.compile(
        r"Example workflows and tasks teams can take on with ChatGPT or Codex",
        re.I,
    ),
    "duplicate_punctuation": re.compile(r"。。|；；|，，"),
}


def _section(markdown: str, heading: re.Pattern[str]) -> str:
    match = heading.search(markdown)
    if match is None:
        return ""
    following = re.search(r"(?m)^##\s+", markdown[match.end() :])
    end = match.end() + following.start() if following else len(markdown)
    return markdown[match.end() : end]


def _normalize_entity(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    text = re.sub(r"[`*_~]", "", text)
    return "".join(character for character in text if character.isalnum())


def _entity_title_is_valid(value: str) -> bool:
    plain = re.sub(r"[`*_~]", "", value).strip()
    if not plain or len(plain) > 80 or re.search(r"[。！？.!?]\s*$", plain):
        return False
    if _CITATION.search(plain):
        return False
    if re.fullmatch(r"[A-Z]{2}", plain):
        return False
    if _normalize_entity(plain) in {"plus", "vla", "contextwindow"}:
        return False
    latin_words = re.findall(r"[A-Za-z][A-Za-z0-9+._/-]*", plain)
    return len(latin_words) <= 10


def _cjk_latin_counts(value: str) -> tuple[int, int]:
    cjk = len(re.findall(r"[\u3400-\u9fff]", value))
    latin = len(re.findall(r"[A-Za-z]", value))
    return cjk, latin


def _core_is_substantive_chinese(value: str) -> bool:
    cleaned = _CITATION.sub("", value)
    cjk, latin = _cjk_latin_counts(cleaned)
    ratio = cjk / max(1, cjk + latin)
    return cjk >= 12 and ratio >= 0.25 and _PLACEHOLDER.search(cleaned) is None


def _core_template(value: str, entity: str) -> str:
    normalized = unicodedata.normalize("NFKC", _CITATION.sub("", value)).casefold()
    normalized = "".join(character for character in normalized if character.isalnum())
    entity_key = _normalize_entity(entity)
    if entity_key:
        normalized = normalized.replace(entity_key, "")
    return re.sub(r"\d+", "#", normalized)


def _finding_blocks(top_section: str) -> list[tuple[str, str]]:
    matches = list(_FINDING_HEADING.finditer(top_section))
    blocks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(top_section)
        blocks.append((match.group(1).strip(), top_section[match.end() : end]))
    return blocks


def _summary_bullets(summary_section: str) -> list[str]:
    marker = re.search(r"(?m)^###\s+关键结论与采用动作\s*$", summary_section)
    if marker is None:
        return []
    tail = summary_section[marker.end() :]
    next_heading = re.search(r"(?m)^###\s+", tail)
    if next_heading:
        tail = tail[: next_heading.start()]
    return [match.group(1).strip() for match in re.finditer(r"(?m)^-\s+(\S.*)$", tail)]


def evaluate_report(markdown: str) -> dict[str, Any]:
    """Evaluate final Markdown without returning any report-derived strings."""

    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    section_presence = {
        name: pattern.search(normalized) is not None
        for name, pattern in _REQUIRED_SECTIONS.items()
    }
    summary = _section(normalized, _REQUIRED_SECTIONS["executive_summary"])
    top = _section(normalized, _REQUIRED_SECTIONS["top_findings"])
    appendix = _section(normalized, _REQUIRED_SECTIONS["citations"])
    findings = _finding_blocks(top)

    entities = [_normalize_entity(entity) for entity, _block in findings]
    title_valid_count = sum(_entity_title_is_valid(entity) for entity, _block in findings)
    complete_fields = 0
    cited_findings = 0
    substantive_chinese_cores = 0
    scores: list[float] = []
    recencies: list[float] = []
    known_maturity = 0
    used_citations: set[int] = set()
    core_templates: list[str] = []
    inconsistent_adoption_count = 0

    for entity, block in findings:
        fields = {
            name: pattern.search(block) for name, pattern in _FIELD_PATTERNS.items()
        }
        if all(match is not None and bool(match.group(1).strip()) for match in fields.values()):
            complete_fields += 1
        citation_text = fields["citations"].group(1) if fields["citations"] else ""
        citation_ids = {int(value) for value in _CITATION.findall(citation_text)}
        if citation_ids:
            cited_findings += 1
            used_citations.update(citation_ids)
        core = fields["core_change"].group(1) if fields["core_change"] else ""
        if _core_is_substantive_chinese(core):
            substantive_chinese_cores += 1
        core_templates.append(_core_template(core, entity))
        score_match = _SCORE.search(block)
        if score_match:
            scores.append(float(score_match.group(1)))
        recency_match = _RECENCY.search(block)
        if recency_match:
            recencies.append(float(recency_match.group(1)))
        maturity_text = fields["maturity"].group(1) if fields["maturity"] else ""
        if maturity_text and _UNKNOWN_MATURITY.search(maturity_text) is None:
            known_maturity += 1
        evidence_match = _EVIDENCE_QUALITY.search(block)
        if (
            evidence_match
            and int(evidence_match.group(1)) < 3
            and _DIRECT_ADOPTION.search(maturity_text)
        ):
            inconsistent_adoption_count += 1

    summary_bullets = _summary_bullets(summary)
    concrete_summary_count = sum(
        len(_CITATION.findall(bullet)) > 0
        and _cjk_latin_counts(bullet)[0] >= 12
        and _SUMMARY_SIGNAL.search(bullet) is not None
        and len(re.sub(r"[`*_~\[\]\d]", "", bullet)) >= 30
        for bullet in summary_bullets
    )
    appendix_ids = {int(value) for value in _APPENDIX_CITATION.findall(appendix)}
    mapped_appendix_ids = {
        int(value) for value in _MAPPED_APPENDIX_CITATION.findall(appendix)
    }
    unresolved = used_citations - appendix_ids
    forbidden_match_count = sum(
        len(pattern.findall(normalized)) for pattern in _FORBIDDEN.values()
    )
    finding_count = len(findings)
    distinct_scores = len({round(score, 6) for score in scores})
    unique_templates = len({value for value in core_templates if value})

    checks = {
        "required_sections": all(section_presence.values()),
        "finding_count": 3 <= finding_count <= 8,
        "entity_uniqueness": (
            finding_count > 0
            and all(entities)
            and len(set(entities)) == finding_count
        ),
        "entity_title_format": title_valid_count == finding_count and finding_count > 0,
        "finding_fields": complete_fields == finding_count and finding_count > 0,
        "finding_citations": cited_findings == finding_count and finding_count > 0,
        "summary_judgments": (
            3 <= len(summary_bullets) <= 5
            and concrete_summary_count == len(summary_bullets)
        ),
        "score_diversity": len(scores) == finding_count and distinct_scores >= 2,
        "recency_signal": (
            len(recencies) == finding_count and any(value > 0 for value in recencies)
        ),
        "maturity_signal": known_maturity > 0 and len(findings) > 0,
        "adoption_consistency": inconsistent_adoption_count == 0 and finding_count > 0,
        "chinese_core_changes": substantive_chinese_cores == finding_count and finding_count > 0,
        "distinct_core_changes": unique_templates == finding_count and finding_count > 0,
        "forbidden_noise": forbidden_match_count == 0,
        "citation_appendix": section_presence["citations"] and len(appendix_ids) > 0,
        "citation_resolution": len(used_citations) > 0 and not unresolved,
        "citation_explainability": (
            len(used_citations) > 0 and used_citations.issubset(mapped_appendix_ids)
        ),
    }
    failure_codes = sorted(name for name, passed in checks.items() if not passed)
    counts = {
        "report_bytes": len(normalized.encode("utf-8")),
        "required_section_count": sum(section_presence.values()),
        "top_finding_count": finding_count,
        "normalized_unique_entity_count": len(set(value for value in entities if value)),
        "valid_entity_title_count": title_valid_count,
        "complete_field_finding_count": complete_fields,
        "cited_finding_count": cited_findings,
        "summary_judgment_count": len(summary_bullets),
        "concrete_summary_judgment_count": concrete_summary_count,
        "distinct_score_count": distinct_scores,
        "nonzero_recency_count": sum(value > 0 for value in recencies),
        "known_maturity_count": known_maturity,
        "inconsistent_adoption_count": inconsistent_adoption_count,
        "substantive_chinese_core_count": substantive_chinese_cores,
        "unique_core_template_count": unique_templates,
        "forbidden_rule_match_count": forbidden_match_count,
        "appendix_citation_count": len(appendix_ids),
        "unresolved_citation_count": len(unresolved),
        "mapped_appendix_citation_count": len(mapped_appendix_ids),
    }
    return {
        "schema_version": 1,
        "passed": not failure_codes,
        "checks": checks,
        "counts": counts,
        "failure_codes": failure_codes,
    }


def _input_failure(code: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "passed": False,
        "checks": {"input_readable": False},
        "counts": {"report_bytes": 0},
        "failure_codes": [code],
    }


def _write_output(output: str, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    if output == "-":
        print(encoded)
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a final Chinese AI technology report without exposing content."
    )
    parser.add_argument("--report-md", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    try:
        source = Path(args.report_md)
        if source.stat().st_size > _MAX_REPORT_BYTES:
            payload = _input_failure("input_too_large")
        else:
            payload = evaluate_report(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        payload = _input_failure("input_read_error")
    _write_output(args.output, payload)
    if args.output != "-":
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

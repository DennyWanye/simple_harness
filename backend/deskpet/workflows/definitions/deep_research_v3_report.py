"""Canonical body and immutable report rendering for DeepResearch v3."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping, Sequence

from ..contracts import JsonValue, canonical_json
from .deep_research_v3_contracts import AtomicClaim, PublishDecision


def _render_claim(claim: AtomicClaim) -> str:
    citations = " ".join(f"[{value}]" for value in claim.citation_ids)
    return f"- {claim.text} {citations}".rstrip()


def render_body(
    *,
    topic: str,
    published_claims: Sequence[AtomicClaim],
    limitations: Sequence[str],
) -> str:
    """Render the one canonical body used both for gating and final delivery."""

    deduplicated: list[AtomicClaim] = []
    seen: set[str] = set()
    for claim in published_claims:
        key = " ".join(claim.text.casefold().split())
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(claim)
    factual = [value for value in deduplicated if value.kind == "factual"]
    inferences = [value for value in deduplicated if value.kind in {"inference", "opinion"}]
    facts_md = "\n".join(_render_claim(value) for value in factual) or "- 暂无通过支持核验的事实性结论"
    inference_md = "\n".join(
        _render_claim(value).replace("- ", "- 推断：", 1) for value in inferences
    ) or "- 无"
    limitations_md = "\n".join(f"- {value}" for value in limitations) or "- 无额外限制"
    return (
        f"# {topic}\n\n"
        f"## 基于来源的结论\n\n{facts_md}\n\n"
        f"## 分析与推断\n\n{inference_md}\n\n"
        f"## 局限与反证条件\n\n{limitations_md}"
    )


def render_report(
    *,
    topic: str,
    body_md: str,
    decision: PublishDecision,
    citations: Sequence[Mapping[str, JsonValue]],
    coverage: Mapping[str, JsonValue],
    errors: Sequence[Mapping[str, JsonValue]],
) -> dict[str, JsonValue]:
    """Assemble the canonical body once, followed by non-gating appendices."""

    visible_errors = (
        "\n".join(
            f"- {item.get('stage', 'unknown')}: {item.get('error_code', 'degraded')}"
            for item in errors
        )
        or "- 无"
    )
    if decision.passed:
        appendix = "\n".join(
            f"[{int(item.get('citation_id') or index)}] {item.get('title') or item.get('url')} — {item.get('url')}"
            for index, item in enumerate(citations, 1)
        ) or "- 无"
        markdown = (
            f"{body_md}\n\n## 引用 Appendix\n\n{appendix}"
            f"\n\n## Coverage\n\n```json\n{canonical_json(dict(coverage))}\n```"
            f"\n\n## Degraded / Error Summary\n\n{visible_errors}"
        )
        status = "completed"
    else:
        reasons = "、".join(decision.reason_codes) or "insufficient_evidence"
        markdown = (
            f"# {topic}\n\n已有来源未通过发布门（{reasons}），因此本次调研不发布未经充分支持的事实性结论。"
            f"\n\n## 安全不足项\n\n"
            f"- 支持率：{decision.support_rate:.1%}\n"
            f"- 支持的事实：{decision.supported_factual_count}\n"
            f"- 实际引用来源：{decision.citation_count}\n"
            f"- 独立域名：{decision.independent_domain_count}\n"
            f"- 正文字节：{decision.body_bytes}"
            f"\n\n## Degraded / Error Summary\n\n{visible_errors}"
        )
        status = "no_results"
    payload: dict[str, JsonValue] = {
        "schema_version": 3,
        "status": status,
        "reason_code": "quality_gate_passed" if decision.passed else "insufficient_evidence",
        "topic": topic,
        "report_md": markdown,
        "body_md": body_md if decision.passed else "",
        "citations": [copy.deepcopy(dict(value)) for value in citations] if decision.passed else [],
        "coverage": copy.deepcopy(dict(coverage)),
        "errors": [copy.deepcopy(dict(value)) for value in errors],
        "publish_decision": decision.to_json(),
    }
    payload["report_hash"] = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    return payload


__all__ = ["render_body", "render_report"]

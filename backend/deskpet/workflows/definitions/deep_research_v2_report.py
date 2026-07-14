"""Immutable Markdown report rendering for DeepResearch v2."""

from __future__ import annotations

import copy
import hashlib
from typing import Mapping, Sequence

from ..contracts import JsonValue, canonical_json


def render_report(
    *, topic: str,
    supported_findings: Sequence[str],
    inferences: Sequence[str],
    limitations: Sequence[str],
    citations: Sequence[Mapping[str, JsonValue]],
    coverage: Mapping[str, JsonValue],
    errors: Sequence[Mapping[str, JsonValue]],
) -> dict[str, JsonValue]:
    if not citations or not supported_findings:
        reason = "未找到可核验的可用来源" if not citations else "已有来源不足以支持任何事实性结论"
        markdown = f"# {topic}\n\n{reason}，因此本次调研未生成基于模型预训练知识的结论。"
        status = "no_results"
    else:
        findings = "\n".join(f"- {value}" for value in supported_findings) or "- 暂无已支持发现"
        analysis = "\n".join(f"- 推断：{value}" for value in inferences) or "- 无"
        limits = "\n".join(f"- {value}" for value in limitations) or "- 未发现额外限制"
        appendix = "\n".join(
            f"[{index}] {item.get('title') or item.get('url')} — {item.get('url')}"
            for index, item in enumerate(citations, 1)
        )
        error_summary = (
            "\n".join(
                f"- {item.get('stage', 'unknown')}: {item.get('error_code', 'degraded')}"
                for item in errors
            )
            or "- 无"
        )
        markdown = (
            f"# {topic}\n\n## 直接回答\n\n{findings}\n\n## 来源支撑发现\n\n{findings}"
            f"\n\n## 分析与推断\n\n{analysis}\n\n## 局限与反证条件\n\n{limits}"
            f"\n\n## 引用 Appendix\n\n{appendix}\n\n## Coverage\n\n```json\n{canonical_json(dict(coverage))}\n```"
            f"\n\n## Degraded / Error Summary\n\n{error_summary}"
        )
        status = "completed"
    payload: dict[str, JsonValue] = {
        "schema_version": 2,
        "status": status,
        "topic": topic,
        "report_md": markdown,
        "citations": [copy.deepcopy(dict(value)) for value in citations],
        "coverage": copy.deepcopy(dict(coverage)),
        "errors": [copy.deepcopy(dict(value)) for value in errors],
    }
    payload["report_hash"] = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    return payload


__all__ = ["render_report"]

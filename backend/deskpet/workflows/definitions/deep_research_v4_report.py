"""Professional technology-intelligence rendering layered on the v3 publish gate."""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

from ..contracts import JsonValue, canonical_json
from .deep_research_v4_contracts import IntentProfile, TechnologyFinding
from .deep_research_v4_intelligence import finding_set_failure_codes


_MATURITY = {
    "adopted": ("生产可用", "可进入受控采用；先用代表性业务负载验证成本、稳定性和迁移影响。"),
    "emerging": ("正在成熟", "适合开展限界 PoC，并持续跟踪接口稳定性、生态兼容和正式版节奏。"),
    "experimental": ("实验阶段", "仅建议用于技术验证，不应直接承诺生产 SLA 或大规模迁移。"),
    "unknown": ("成熟度待核验", "暂缓生产决策；优先补充正式发布状态、独立实现和真实采用证据。"),
}


def _is_chinese(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text))


def _value_band(score: float) -> str:
    if score >= 0.75:
        return "高优先级"
    if score >= 0.55:
        return "中高优先级"
    if score >= 0.35:
        return "持续关注"
    return "观察项"


def _display_entity(finding: TechnologyFinding) -> str:
    entity = finding.entity
    searchable = f"{finding.statement}\n{finding.localized_statement or ''}".casefold()
    if entity.casefold() == "sleuth" and "working memory" in searchable:
        return "SLEUTH 认识论工作记忆智能体"
    if entity.casefold() == "unibrowse":
        return "UNIBROWSE 多模态浏览智能体"
    return entity


def _citation_refs(
    finding: TechnologyFinding,
    citation_numbers: Mapping[int, int],
) -> str:
    return (
        " ".join(
            f"[{citation_numbers[value]}]"
            for value in finding.winning_citation_ids
            if value in citation_numbers
        )
        or "[缺少引用]"
    )


def _source_type(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.casefold()
    if host == "arxiv.org" or host.endswith(".arxiv.org"):
        return "论文预印本"
    if host == "github.com" and "/releases" in path:
        return "官方代码仓库发布页"
    if host == "github.com":
        return "官方代码仓库"
    if host.endswith(("openai.com", "anthropic.com", "deepmind.google", "ai.google.dev")):
        return "官方产品或研究页面"
    return "网页来源"


_VALUE_REASON = {
    "model_inference": "它可能改变推理延迟、单位请求成本或部署密度，值得用真实负载复测。",
    "agent": "它会影响工具接入、跨系统协作和智能体运行时的工程边界。",
    "multimodal": "它扩展了模型可处理的输入输出形态，并可能改变交互与内容生产链路。",
    "training_inference_system": "它会影响训练或服务效率、硬件利用率和系统维护复杂度。",
    "open_infrastructure": "它会影响自托管、生态兼容、迁移成本和供应商锁定风险。",
}


def _core_change(finding: TechnologyFinding) -> str:
    if finding.localized_statement:
        return finding.localized_statement
    if finding.impact_score >= 2:
        return f"核验来源记录了 {finding.entity} 的量化性能、效率或成本变化。"
    if finding.impact_score == 1:
        return f"核验来源记录了 {finding.entity} 的能力、效率或互操作性更新。"
    return f"核验来源确认 {finding.entity} 在情报窗口内出现技术更新，但尚无可复核的量化影响。"


def _risk(finding: TechnologyFinding) -> str:
    risks: list[str] = []
    if finding.published_at is None:
        risks.append("来源未提供可核验发布日期")
    if finding.evidence_quality < 3:
        risks.append("缺少一手来源与独立来源的交叉支持")
    if finding.impact_score == 0:
        risks.append("技术影响尚未量化")
    if finding.maturity == "unknown":
        risks.append("发布状态和采用成熟度未得到支持")
    return "；".join(risks) if risks else "当前最终采用证据未暴露额外重大不确定性，仍需在实际负载中复核"


def _adoption_advice(finding: TechnologyFinding) -> tuple[str, str]:
    maturity, advice = _MATURITY[finding.maturity]
    if finding.maturity in {"adopted", "emerging"} and finding.evidence_quality < 3:
        advice = (
            "先补齐一手来源与独立来源，再决定是否进入限界 PoC；"
            "在此之前只保留为候选观察项。"
        )
    return maturity, advice


def _value_reason(finding: TechnologyFinding) -> str:
    searchable = f"{finding.statement}\n{finding.localized_statement or ''}".casefold()
    if re.search(r"logs?|dashboard|observab|telemetry|日志|仪表盘|可观测", searchable):
        reason = "分析判断：这类更新主要改善调用链可观测性和故障定位效率，不等同于模型能力提升。"
    elif re.search(r"working memory|epistemic|confirmed facts|工作记忆|事实", searchable):
        reason = "分析判断：它可能改善长链任务中的证据保持与事实状态管理，但收益需要独立复现。"
    elif re.search(
        r"latency|throughput|cost|memory (?:usage|footprint|bandwidth)|"
        r"效率|延迟|吞吐|成本|显存|内存占用",
        searchable,
    ):
        reason = "分析判断：若量化结果能在真实负载复现，它可能直接影响服务容量、延迟或单位成本。"
    elif re.search(r"behavior tree|contract|mcp|tool call|工具调用|合约|行为树", searchable):
        reason = "分析判断：它可能提升智能体工具执行的可约束性和执行前验证能力。"
    else:
        reason = "分析判断：" + _VALUE_REASON.get(
            finding.topic_kind,
            "它可能改变现有技术选型或工程边界，值得结合实际工作负载验证。",
        )
    if finding.impact_score == 0:
        return f"{reason} 但当前证据尚未量化实际收益。"
    return reason


def _validation_action(finding: TechnologyFinding) -> str:
    searchable = f"{finding.statement}\n{finding.localized_statement or ''}".casefold()
    if re.search(r"logs?|dashboard|observab|telemetry|日志|仪表盘|可观测", searchable):
        return "用故障注入验证日志完整性、检索延迟和定位耗时"
    if re.search(r"working memory|epistemic|confirmed facts|工作记忆|事实", searchable):
        return "在长链多跳任务上对比事实保持率、引用正确率和失败恢复"
    if re.search(
        r"latency|throughput|cost|memory (?:usage|footprint|bandwidth)|"
        r"效率|延迟|吞吐|成本|显存|内存占用",
        searchable,
    ):
        return "使用同一组业务负载复测吞吐、尾延迟、资源占用与单位成本"
    if re.search(r"behavior tree|contract|mcp|tool call|工具调用|合约|行为树", searchable):
        return "用越权、过期合约和工具失败用例验证执行前门禁与可恢复性"
    if finding.topic_kind == "multimodal":
        return "用领域图像、音频或视频集复测准确率、幻觉率和时序理解"
    return "用代表性业务样本复测质量、稳定性、集成复杂度和回退成本"


def _finding_block(
    index: int,
    finding: TechnologyFinding,
    citation_numbers: Mapping[int, int],
) -> str:
    citations = _citation_refs(finding, citation_numbers)
    maturity, advice = _adoption_advice(finding)
    return (
        f"#### {index}. {_display_entity(finding)}\n\n"
        f"- 核心变化：{_core_change(finding)} {citations}\n"
        f"- 价值判断：{_value_reason(finding)} 总分 {finding.total_score:.3f}；"
        f"近期性 {finding.recency_score:.1f}、技术影响 {finding.impact_score}/3、"
        f"证据质量 {finding.evidence_quality}/3。{citations}\n"
        f"- 成熟度与采用建议：{maturity}；{advice} 验证动作：{_validation_action(finding)}。{citations}\n"
        f"- 风险与不确定性：{_risk(finding)}。{citations}\n"
        f"- 引用：{citations}\n"
        f"- 来源日期：{finding.published_at or '未提供可核验日期'}"
    )


def _executive_judgments(
    findings: Sequence[TechnologyFinding],
    citation_numbers: Mapping[int, int],
) -> str:
    all_refs = " ".join(
        f"[{number}]" for number in sorted(set(citation_numbers.values()))
    )
    adopted = [finding for finding in findings if finding.maturity == "adopted"]
    emerging = [finding for finding in findings if finding.maturity == "emerging"]
    quantified = [finding for finding in findings if finding.impact_score >= 2]
    strong = [finding for finding in findings if finding.evidence_quality >= 3]
    poc = [
        finding for finding in findings
        if finding.maturity in {"adopted", "emerging"}
        and finding.total_score >= 0.55
        and finding.evidence_quality >= 3
    ]
    evidence_first = [
        finding for finding in findings
        if finding.maturity in {"adopted", "emerging"}
        and finding.total_score >= 0.55
        and finding.evidence_quality < 3
    ]
    if poc:
        poc_action = (
            f"{'、'.join(_display_entity(finding) for finding in poc[:3])} 可进入限界验证，"
            "但必须按正文验证动作复测"
        )
    elif evidence_first:
        poc_action = (
            "暂无条目可直接进入限界 PoC；"
            f"{'、'.join(_display_entity(finding) for finding in evidence_first[:3])} "
            "需先补齐独立来源，再按正文验证动作复测"
        )
    else:
        poc_action = "暂无条目达到限界 PoC 门槛，继续补充来源并跟踪"
    return "\n".join(
        (
            f"- **成熟度判断**：本轮 {len(findings)} 项中，生产可用 {len(adopted)} 项、"
            f"正在成熟 {len(emerging)} 项；整体不支持直接大规模迁移。{all_refs}",
            f"- **量化采用价值判断**：{len(quantified)} 项具备受支持的量化性能或效率信号；"
            f"其余条目只能作为方向性技术观察，不能据此承诺收益。{all_refs}",
            f"- **PoC 采用动作**：{poc_action}。{all_refs}",
            f"- **采用证据风险判断**：仅 {len(strong)} 项达到一手来源加独立来源的最高证据等级；"
            f"缺少交叉支持的条目已降权，不应被解读为行业共识。{all_refs}",
        )
    )


def _action_summary(findings: Sequence[TechnologyFinding]) -> str:
    validate = [
            _display_entity(finding)
        for finding in findings
        if finding.maturity in {"adopted", "emerging"}
        and finding.total_score >= 0.55
        and finding.evidence_quality >= 3
    ][:3]
    evidence_first = [
        _display_entity(finding)
        for finding in findings
        if _display_entity(finding) not in validate
        and finding.maturity in {"adopted", "emerging"}
        and finding.total_score >= 0.55
    ][:3]
    track = [
        _display_entity(finding)
        for finding in findings
        if _display_entity(finding) not in {*validate, *evidence_first}
    ][:3]
    rows: list[str] = []
    if validate:
        rows.append(
            "- **进入限界 PoC**："
            + "、".join(validate)
            + "。已有较强证据支撑，但仍需用同一组真实业务负载复测质量、延迟、成本和集成复杂度。"
        )
    if evidence_first:
        rows.append(
            "- **先补证据再 PoC**："
            + "、".join(evidence_first)
            + "。当前成熟度信号偏积极，但缺少独立交叉支持，不应直接进入迁移决策。"
        )
    if track:
        rows.append(
            "- **持续跟踪**："
            + "、".join(track)
            + "。等待更清晰的正式发布、独立复现或生产采用证据。"
        )
    rows.append("- **决策纪律**：没有量化或独立支持的条目只进入观察清单，不直接触发迁移。")
    return "\n".join(rows)


def _public_finding(finding: TechnologyFinding) -> dict[str, JsonValue]:
    return {
        "finding_id": finding.finding_id,
        "entity": finding.entity,
        "topic_kind": finding.topic_kind,
        "topic_label": finding.topic_label,
        "published_at": finding.published_at,
        "recency_score": finding.recency_score,
        "impact_score": finding.impact_score,
        "maturity": finding.maturity,
        "evidence_quality": finding.evidence_quality,
        "winning_citation_ids": list(finding.winning_citation_ids),
        "total_score": finding.total_score,
        "localized_statement": _core_change(finding),
    }


def render_technology_report(
    *,
    topic: str,
    profile: IntentProfile,
    findings: Sequence[TechnologyFinding],
    base_report: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    """Render a compact user report from supported, entity-deduplicated findings."""

    if base_report.get("status") != "completed":
        raise ValueError("technology report requires a passed base report")
    selected = list(findings[:8])
    gate_codes = finding_set_failure_codes(selected)
    if gate_codes:
        raise ValueError(f"technology report publish gate failed: {','.join(gate_codes)}")
    used_citation_ids = sorted(
        {value for finding in selected for value in finding.winning_citation_ids}
    )
    citation_numbers = {
        citation_id: index for index, citation_id in enumerate(used_citation_ids, 1)
    }
    grouped: OrderedDict[str, list[TechnologyFinding]] = OrderedDict()
    for finding in selected:
        grouped.setdefault(finding.topic_label, []).append(finding)

    adopted = sum(finding.maturity == "adopted" for finding in selected)
    emerging = sum(finding.maturity == "emerging" for finding in selected)
    concrete_bullets = _executive_judgments(selected, citation_numbers)
    coverage_note = (
        f"> 覆盖说明：本轮仅 {len(selected)} 项通过实体、时效与逐段引用门；"
        "未使用低质量候选补齐数量。\n\n"
        if len(selected) < 5
        else ""
    )
    summary = (
        "## 一页式执行摘要\n\n"
        f"> 情报窗口：{profile.window_start} 至 {profile.window_end}（UTC，截至 {profile.as_of_date}）。"
        f"本轮从最终通过逐段核验的证据中筛出 {len(selected)} 项；"
        f"生产可用 {adopted} 项、正在成熟 {emerging} 项。\n\n"
        f"{coverage_note}"
        f"### 关键结论与采用动作\n\n{concrete_bullets}\n\n"
        f"### 组合建议\n\n{_action_summary(selected)}"
    )
    group_sections = []
    display_index = 1
    for label, rows in grouped.items():
        group_sections.append(
            f"### {label}\n\n"
            + "\n\n".join(
                _finding_block(display_index + offset, finding, citation_numbers)
                for offset, finding in enumerate(rows)
            )
        )
        display_index += len(rows)
    body = "\n\n".join(group_sections) or "### 暂无可发布条目\n\n本轮没有通过 taxonomy 与引用支持双重核验的技术实体。"

    citation_to_entities: dict[int, list[str]] = {}
    for finding in selected:
        for citation_id in finding.winning_citation_ids:
            citation_to_entities.setdefault(citation_id, []).append(_display_entity(finding))
    appendix_rows: list[str] = []
    for index, item in enumerate(base_report.get("citations", []), 1):
        if not isinstance(item, Mapping):
            continue
        internal_citation_id = int(item.get("citation_id") or index)
        entities = citation_to_entities.get(internal_citation_id)
        if not entities:
            continue
        citation_id = citation_numbers[internal_citation_id]
        url = str(item.get("url") or "")
        title = str(item.get("title") or url)
        appendix_rows.append(
            f"[{citation_id}] 支持条目：{'、'.join(dict.fromkeys(entities))}｜"
            f"来源类型：{_source_type(url)}｜{title} — {url}"
        )
    appendix = "\n".join(appendix_rows)
    score_rows = "\n".join(
        "| {entity} | {band} | {score:.3f} | {recency:.1f} | {impact}/3 | {quality}/3 |".format(
            entity=_display_entity(finding).replace("|", "\\|"),
            band=_value_band(finding.total_score),
            score=finding.total_score,
            recency=finding.recency_score,
            impact=finding.impact_score,
            quality=finding.evidence_quality,
        )
        for finding in selected
    )
    methodology = (
        "## 方法与局限\n\n"
        "### 决策排序总览\n\n"
        "| 技术 | 优先级 | 总分 | 近期性 | 技术影响 | 证据质量 |\n"
        "|---|---:|---:|---:|---:|---:|\n"
        f"{score_rows}\n\n"
        "总分固定采用 35% 近期性、30% 技术影响、20% 成熟度/可采用性、15% 证据质量。"
        "仅发布通过逐段支持核验的条目；缺少日期、量化结果或独立来源时保守降级。"
    )
    if _is_chinese(topic):
        markdown = (
            f"# {topic}\n\n{summary}\n\n## 分主题 Top 技术（本次核验 {len(selected)} 项）\n\n"
            f"{body}\n\n{methodology}\n\n## 引用\n\n{appendix or '- 无'}"
        )
    else:
        # The workflow is primarily Chinese-facing today. Keep the evidence
        # structure intact for English prompts without exposing debug payloads.
        markdown = (
            f"# {topic}\n\n{summary}\n\n## Top technologies by theme\n\n"
            f"{body}\n\n{methodology}\n\n## References\n\n{appendix or '- None'}"
        )

    body_md = markdown.split("\n\n## 引用", 1)[0].split("\n\n## References", 1)[0]
    payload: dict[str, JsonValue] = {
        "schema_version": 4,
        "status": "completed",
        "reason_code": "technology_publish_gate_passed",
        "topic": topic,
        "report_md": markdown,
        "body_md": body_md,
        "intent_profile": profile.to_json(),
        "public_findings": [_public_finding(finding) for finding in selected],
        "citation_count": len({value for finding in selected for value in finding.winning_citation_ids}),
    }
    payload["report_hash"] = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    return payload


__all__ = ["render_technology_report"]

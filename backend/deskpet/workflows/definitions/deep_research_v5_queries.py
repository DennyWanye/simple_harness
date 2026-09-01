"""Deterministic, dimension-aware query plans for DeepResearch v5."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from ...retrieval.query_terms import normalize_query_text, query_fingerprint
from .deep_research_v5_contracts import (
    ContractValidationError,
    DimensionCoverage,
    ResearchBrief,
    ResearchDimension,
)

QueryFamily = Literal["general", "official", "temporal_statistics", "comparison"]
ComparisonAxis = Literal[
    "none", "urban_rural_region", "temporal_trend", "policy_phase", "peer_object"
]

QUERY_FAMILIES: tuple[QueryFamily, ...] = (
    "general",
    "official",
    "temporal_statistics",
    "comparison",
)

_MAX_QUERY_CHARS = 240

_COMPARISON_AXIS: dict[str, ComparisonAxis] = {
    "edu_current_state": "temporal_trend",
    "edu_scale_trend": "temporal_trend",
    "edu_equity_urban_rural_region": "urban_rural_region",
    "edu_teacher_finance": "urban_rural_region",
    "edu_double_reduction_after_school_burden": "policy_phase",
    "edu_national_next_plan": "policy_phase",
    "generic_who": "peer_object",
    "generic_what": "peer_object",
    "generic_current_state": "temporal_trend",
    "generic_drivers": "peer_object",
    "generic_outlook": "temporal_trend",
    "tech_current_state": "peer_object",
    "tech_recent_changes": "temporal_trend",
    "tech_capabilities_benchmarks": "peer_object",
    "tech_adoption_ecosystem": "peer_object",
    "tech_risks_limits": "peer_object",
    "tech_outlook": "temporal_trend",
}

_EDUCATION_ZH_BASE = {
    "edu_current_state": "教育现状 教育质量 入学机会 学习条件",
    "edu_scale_trend": "招生规模 在校生 学校规模 发展趋势",
    "edu_equity_urban_rural_region": "城乡均衡 区域教育公平 资源配置",
    "edu_teacher_finance": "教师队伍 师资配置 教育财政 经费投入",
    "edu_double_reduction_after_school_burden": "双减 课后服务 学生负担 家庭负担",
    "edu_national_next_plan": "国家下一步计划 已发布任务 时间节点",
}

_EDUCATION_EN_BASE = {
    "edu_current_state": "education current state quality access learning conditions",
    "edu_scale_trend": "enrollment student population school scale trend",
    "edu_equity_urban_rural_region": "urban rural regional education equity resource allocation",
    "edu_teacher_finance": "teacher workforce allocation education finance expenditure",
    "edu_double_reduction_after_school_burden": "Double Reduction after-school services student family burden",
    "edu_national_next_plan": "national next education plan published tasks milestones",
}

_AXIS_TEXT = {
    "zh": {
        "urban_rural_region": "城乡与区域比较",
        "temporal_trend": "历年时间趋势比较",
        "policy_phase": "政策实施前后阶段比较",
        "peer_object": "同类对象对照比较",
    },
    "en": {
        "urban_rural_region": "urban rural and regional comparison",
        "temporal_trend": "comparison over time",
        "policy_phase": "before and after policy phase comparison",
        "peer_object": "peer object comparison",
    },
}


@dataclass(frozen=True, slots=True)
class DimensionQuery:
    dimension_id: str
    query_family: QueryFamily
    comparison_axis: ComparisonAxis
    source_target: str
    query: str
    freshness_window: str

    def __post_init__(self) -> None:
        for name in ("dimension_id", "source_target", "query", "freshness_window"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ContractValidationError(f"{name} is required")
        if self.query_family not in QUERY_FAMILIES:
            raise ContractValidationError("invalid query_family")
        allowed_axes = {"none", "urban_rural_region", "temporal_trend", "policy_phase", "peer_object"}
        if self.comparison_axis not in allowed_axes:
            raise ContractValidationError("invalid comparison_axis")
        if self.query_family == "comparison" and self.comparison_axis == "none":
            raise ContractValidationError("comparison query requires an axis")
        if self.query_family != "comparison" and self.comparison_axis != "none":
            raise ContractValidationError("only comparison queries may carry an axis")
        if ".." not in self.freshness_window:
            raise ContractValidationError("freshness_window must be an inclusive date range")

    @property
    def fingerprint(self) -> str:
        return query_fingerprint(**self.to_json())

    def to_json(self) -> dict[str, str]:
        return {
            "dimension_id": self.dimension_id,
            "query_family": self.query_family,
            "comparison_axis": self.comparison_axis,
            "source_target": self.source_target,
            "query": self.query,
            "freshness_window": self.freshness_window,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> DimensionQuery:
        expected = {
            "dimension_id", "query_family", "comparison_axis", "source_target",
            "query", "freshness_window",
        }
        unknown = set(value) - expected
        missing = expected - set(value)
        if unknown or missing:
            raise ContractValidationError(
                f"query fields mismatch; unknown={sorted(unknown)} missing={sorted(missing)}"
            )
        return cls(
            dimension_id=str(value["dimension_id"]),
            query_family=str(value["query_family"]),  # type: ignore[arg-type]
            comparison_axis=str(value["comparison_axis"]),  # type: ignore[arg-type]
            source_target=str(value["source_target"]),
            query=str(value["query"]),
            freshness_window=str(value["freshness_window"]),
        )


def comparison_axis_for_dimension(dimension_id: str) -> ComparisonAxis:
    return _COMPARISON_AXIS.get(dimension_id, "peer_object")


def _language(brief: ResearchBrief) -> str:
    return "zh" if brief.locale.casefold().startswith("zh") else "en"


def _date_window(brief: ResearchBrief) -> str:
    try:
        end = date.fromisoformat(brief.as_of_date)
    except ValueError as exc:
        raise ContractValidationError("brief as_of_date must be ISO date") from exc
    lookback_days = 730 if brief.profile == "technology_intelligence" else 1825
    start = end - timedelta(days=lookback_days)
    return f"{start.isoformat()}..{end.isoformat()}"


def _context(brief: ResearchBrief) -> str:
    # Search engines perform materially worse when every modeled subject is
    # repeated in every query. Two subjects retain pairwise comparisons while
    # keeping broad modeled briefs bounded.
    values = [brief.geography or "", *brief.subjects[:2]]
    return " ".join(value for value in values if value).strip()


def _requested_year(brief: ResearchBrief) -> str:
    match = re.search(r"(?<!\d)((?:19|20)\d{2})(?:年)?", brief.user_question)
    return match.group(1) if match else brief.as_of_date[:4]


def _base_terms(brief: ResearchBrief, dimension: ResearchDimension) -> str:
    if brief.profile == "policy_education":
        mapping = _EDUCATION_ZH_BASE if _language(brief) == "zh" else _EDUCATION_EN_BASE
        if dimension.dimension_id in mapping:
            return mapping[dimension.dimension_id]
    # A target is already a search-ready phrase. Joining every target creates
    # multi-hundred-character queries after model enrichment and causes search
    # providers to return captchas/timeouts instead of results.
    return dimension.query_targets[0]


def _requires_official_statistics(
    brief: ResearchBrief,
    dimension: ResearchDimension,
) -> bool:
    corpus = " ".join(
        (
            brief.user_question,
            dimension.question,
            *dimension.expected_source_types,
            *dimension.query_targets,
        )
    ).casefold()
    return any(
        marker in corpus
        for marker in (
            "official_statistics",
            "stats.gov.cn",
            "国家统计局",
            "统计公报",
        )
    )


def _bounded_query(*parts: str, suffix: str) -> str:
    """Compose a concise query while preserving its source/family suffix."""

    normalized_parts: list[str] = []
    seen: set[str] = set()
    for raw in parts:
        value = " ".join(str(raw).split()).strip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        normalized_parts.append(value)

    normalized_suffix = " ".join(suffix.split()).strip()
    prefix_budget = max(0, _MAX_QUERY_CHARS - len(normalized_suffix) - 1)
    prefix = " ".join(normalized_parts)
    if len(prefix) > prefix_budget:
        prefix = prefix[:prefix_budget].rstrip()
    return " ".join(value for value in (prefix, normalized_suffix) if value)


def _source_targets(profile: str) -> dict[QueryFamily, str]:
    official = {
        "policy_education": "official_government_education",
        "technology_intelligence": "official_vendor_or_repository",
        "generic_research": "official_or_primary",
    }[profile]
    return {
        "general": "broad_web",
        "official": official,
        "temporal_statistics": "official_statistics_or_primary_data",
        "comparison": "cross_source_comparison",
    }


def _query_text(
    brief: ResearchBrief,
    dimension: ResearchDimension,
    family: QueryFamily,
    axis: ComparisonAxis,
) -> str:
    language = _language(brief)
    context = _context(brief)
    base = _base_terms(brief, dimension)
    year = _requested_year(brief)
    if family == "general":
        suffix = "综合现状" if language == "zh" else "current evidence overview"
    elif family == "official":
        if language == "zh" and brief.profile == "policy_education":
            suffix = "site:moe.gov.cn OR site:gov.cn OR site:stats.gov.cn 官方 政策 统计 原始资料"
        elif _requires_official_statistics(brief, dimension):
            suffix = "site:stats.gov.cn 官方 统计 公报 原始数据"
        elif any("iphone" in subject.casefold() for subject in brief.subjects):
            suffix = "site:apple.com OR site:mi.com 官方 规格 价格 支持"
        else:
            suffix = "official primary source documentation"
    elif family == "temporal_statistics":
        suffix = (
            f"site:stats.gov.cn OR site:moe.gov.cn {year} 统计 数据 趋势"
            if language == "zh" and brief.profile == "policy_education"
            else f"{year} statistics time series trend primary data"
        )
    else:
        suffix = _AXIS_TEXT[language][axis]
    return _bounded_query(context, base, suffix=suffix)


def build_dimension_queries(
    brief: ResearchBrief,
    dimension: ResearchDimension,
) -> tuple[DimensionQuery, ...]:
    if dimension.dimension_id not in {item.dimension_id for item in brief.dimensions}:
        raise ContractValidationError("dimension does not belong to brief")
    targets = _source_targets(brief.profile)
    window = _date_window(brief)
    comparison_axis = comparison_axis_for_dimension(dimension.dimension_id)
    result = tuple(
        DimensionQuery(
            dimension_id=dimension.dimension_id,
            query_family=family,
            comparison_axis=comparison_axis if family == "comparison" else "none",
            source_target=targets[family],
            query=_query_text(
                brief,
                dimension,
                family,
                comparison_axis if family == "comparison" else "none",
            ),
            freshness_window=window,
        )
        for family in QUERY_FAMILIES
    )
    if len({item.fingerprint for item in result}) != len(result):
        raise ContractValidationError("dimension query plan contains duplicate fingerprints")
    return result


def build_initial_query_plan(
    brief: ResearchBrief,
    *,
    include_supporting: bool = False,
) -> tuple[DimensionQuery, ...]:
    dimensions = tuple(
        item
        for item in brief.dimensions
        if item.importance == "core" or include_supporting
    )
    facet_priority = {
        "tech_ranking_method_candidates": 0,
        "tech_pricing_access_pros_cons": 1,
        "tech_capabilities_benchmarks": 2,
        "tech_current_state": 3,
        "tech_recent_changes": 4,
    }
    if any(item.dimension_id in facet_priority for item in dimensions):
        dimensions = tuple(sorted(
            dimensions,
            key=lambda item: (
                facet_priority.get(item.dimension_id, len(facet_priority)),
                brief.dimensions.index(item),
            ),
        ))
    probes: list[DimensionQuery] = []
    product_comparison_ids = {
        "camera_capability",
        "battery_and_charging",
        "price_and_value",
        "system_ecosystem",
        "major_drawbacks_and_risks",
        "performance_and_daily_experience",
        "user_fit_recommendation",
    }
    is_product_comparison = (
        len(brief.subjects) >= 2
        and any(item.dimension_id in product_comparison_ids for item in dimensions)
    )
    for dimension in dimensions:
        # Pairwise product discovery must first find pages about both exact
        # models. A combined ``site:vendor-a OR site:vendor-b`` probe tends to
        # return generic vendor pages or the wrong model. The bounded rescue
        # plan still includes official probes after broad discovery.
        family: QueryFamily = (
            "general"
            if is_product_comparison
            else "official" if dimension.first_party_required else "general"
        )
        probes.append(
            next(
                query
                for query in build_dimension_queries(brief, dimension)
                if query.query_family == family
            )
        )
        if len(probes) == 5:
            break
    if len(probes) < 3:
        for dimension in dimensions:
            for query in build_dimension_queries(brief, dimension):
                if query in probes:
                    continue
                probes.append(query)
                if len(probes) == 3:
                    break
            if len(probes) == 3:
                break
    return _deduplicate(tuple(probes))


def _deduplicate(queries: Sequence[DimensionQuery]) -> tuple[DimensionQuery, ...]:
    seen: set[str] = set()
    result: list[DimensionQuery] = []
    for query in queries:
        if query.fingerprint in seen:
            continue
        seen.add(query.fingerprint)
        result.append(query)
    return tuple(result)


def build_gap_query_plan(
    brief: ResearchBrief,
    coverages: Sequence[DimensionCoverage],
    *,
    executed_query_fingerprints: Sequence[str] = (),
    executed_source_families: Mapping[str, Sequence[str]] | None = None,
) -> tuple[DimensionQuery, ...]:
    """Return deterministic rescue work only for uncovered/partial core gaps.

    ``executed_source_families`` is keyed by dimension id (or ``*`` for a
    global exclusion) and contains the query policy's ``source_target`` family
    names.  It prevents a rescue round from reusing an already exhausted
    source family.
    """

    coverage_by_id: dict[str, DimensionCoverage] = {}
    for coverage in coverages:
        if coverage.dimension_id in coverage_by_id:
            raise ContractValidationError("duplicate dimension coverage")
        coverage_by_id[coverage.dimension_id] = coverage
    executed = {value.casefold() for value in executed_query_fingerprints}
    source_families = executed_source_families or {}
    global_excluded = {
        normalize_query_text(value) for value in source_families.get("*", ())
    }
    selected: list[DimensionQuery] = []
    for dimension in brief.dimensions:
        if dimension.importance != "core":
            continue
        coverage = coverage_by_id.get(dimension.dimension_id)
        if coverage is None or coverage.status not in {"uncovered", "partially_covered"}:
            continue
        dimension_excluded = global_excluded | {
            normalize_query_text(value)
            for value in source_families.get(dimension.dimension_id, ())
        }
        for query in build_dimension_queries(brief, dimension):
            if query.fingerprint.casefold() in executed:
                continue
            if normalize_query_text(query.source_target) in dimension_excluded:
                continue
            selected.append(query)
    return _deduplicate(selected)


__all__ = [
    "QUERY_FAMILIES",
    "ComparisonAxis",
    "DimensionQuery",
    "QueryFamily",
    "build_dimension_queries",
    "build_gap_query_plan",
    "build_initial_query_plan",
    "comparison_axis_for_dimension",
]

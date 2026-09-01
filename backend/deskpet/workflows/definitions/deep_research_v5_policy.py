"""Deterministic-first intent and profile policy for DeepResearch v5.

The policy owns research-question normalization and profile dimension
templates.  It deliberately performs no network or LLM calls: a caller may
optionally pass a validated modeling payload, but failure or absence always
leaves a complete deterministic brief.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Mapping

from .deep_research_v5_contracts import (
    ContractValidationError,
    DimensionCoverage,
    ResearchBrief,
    ResearchDimension,
)


EDUCATION_DIMENSION_IDS = (
    "edu_current_state",
    "edu_scale_trend",
    "edu_equity_urban_rural_region",
    "edu_teacher_finance",
    "edu_double_reduction_after_school_burden",
    "edu_national_next_plan",
)

GENERIC_DIMENSION_IDS = (
    "generic_who",
    "generic_what",
    "generic_current_state",
    "generic_drivers",
    "generic_outlook",
)

TECHNOLOGY_DIMENSION_IDS = (
    "tech_current_state",
    "tech_recent_changes",
    "tech_capabilities_benchmarks",
    "tech_adoption_ecosystem",
    "tech_risks_limits",
    "tech_outlook",
)

_PROFILES = frozenset(
    {"generic_research", "policy_education", "technology_intelligence"}
)


@dataclass(frozen=True, slots=True)
class ProfileDefinition:
    profile: str
    dimensions: tuple[ResearchDimension, ...]
    preferred_source_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DetectedResearchIntent:
    profile: str
    locale: str
    as_of_date: str
    geography: str | None
    subjects: tuple[str, ...]
    comparison_intents: tuple[str, ...]
    intent_facets: tuple[str, ...]
    explicit_exclusions: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ValidatedModelingOutput:
    subjects: tuple[str, ...] | None = None
    expected_decision: str | None = None
    dimensions: tuple[ResearchDimension, ...] | None = None
    rejected_dimensions: tuple[tuple[int, str], ...] = ()


def _dimension(
    dimension_id: str,
    question: str,
    expected_source_types: tuple[str, ...],
    query_targets: tuple[str, ...],
    *,
    first_party_required: bool,
    importance: str = "core",
) -> ResearchDimension:
    return ResearchDimension(
        dimension_id=dimension_id,
        question=question,
        importance=importance,  # type: ignore[arg-type]
        expected_source_types=expected_source_types,
        query_targets=query_targets,
        first_party_required=first_party_required,
        not_applicable_when=("user_explicitly_excludes_dimension",),
    )


_EDUCATION_DIMENSIONS = (
    _dimension(
        "edu_current_state",
        "What is the current education state, including access, quality, and learning conditions?",
        ("official_policy", "official_statistics", "independent_research"),
        ("current education state", "education quality access statistics"),
        first_party_required=True,
    ),
    _dimension(
        "edu_scale_trend",
        "How are enrollment, school scale, and demographic trends changing over time?",
        ("official_statistics", "official_report", "independent_research"),
        ("enrollment school scale trend", "student population time series"),
        first_party_required=True,
    ),
    _dimension(
        "edu_equity_urban_rural_region",
        "How do urban-rural and regional differences affect education equity?",
        ("official_statistics", "regional_government", "independent_research"),
        ("urban rural education equity", "regional education resource comparison"),
        first_party_required=True,
    ),
    _dimension(
        "edu_teacher_finance",
        "What are the teacher workforce, allocation, compensation, and public-finance conditions?",
        ("official_statistics", "finance_budget", "official_policy"),
        ("teacher workforce allocation", "education finance budget expenditure"),
        first_party_required=True,
    ),
    _dimension(
        "edu_double_reduction_after_school_burden",
        "What are the results and remaining issues for Double Reduction, after-school services, and student or family burden?",
        ("official_policy", "official_evaluation", "independent_research"),
        ("double reduction implementation", "after school services student family burden"),
        first_party_required=True,
    ),
    _dimension(
        "edu_national_next_plan",
        "Which national next-step plans, published tasks, and dated milestones are officially committed?",
        ("official_policy", "national_plan", "official_timeline"),
        ("national education next plan", "published tasks milestones timeline"),
        first_party_required=True,
    ),
)

_GENERIC_DIMENSIONS = (
    _dimension(
        "generic_who",
        "Who are the affected actors, decision makers, and relevant populations?",
        ("official", "primary_source", "independent_research"),
        ("stakeholders affected groups", "decision makers actors"),
        first_party_required=False,
    ),
    _dimension(
        "generic_what",
        "What exactly is the subject, scope, and decision-relevant definition?",
        ("official", "primary_source", "reference"),
        ("subject scope definition", "decision relevant facts"),
        first_party_required=False,
    ),
    _dimension(
        "generic_current_state",
        "What is the current state, including the strongest available measurements?",
        ("official_statistics", "primary_source", "independent_research"),
        ("current state latest statistics", "present conditions evidence"),
        first_party_required=False,
    ),
    _dimension(
        "generic_drivers",
        "What causal drivers, constraints, and countervailing explanations shape the current state?",
        ("primary_source", "scholarly", "independent_research"),
        ("causes drivers constraints", "counterevidence explanations"),
        first_party_required=False,
    ),
    _dimension(
        "generic_outlook",
        "What is the evidence-based outlook, including uncertainty and decision implications?",
        ("official_forecast", "scholarly", "independent_research"),
        ("outlook forecast uncertainty", "decision implications scenarios"),
        first_party_required=False,
    ),
)

_TECHNOLOGY_DIMENSIONS = (
    _dimension(
        "tech_current_state",
        "What is the current product, model, or technology state as of the research date?",
        ("official_release", "official_documentation", "repository"),
        ("current version capabilities", "official product status"),
        first_party_required=True,
    ),
    _dimension(
        "tech_recent_changes",
        "Which material releases, changes, and deprecations occurred in the relevant time window?",
        ("official_release", "changelog", "repository"),
        ("recent releases changelog", "breaking changes deprecations"),
        first_party_required=True,
    ),
    _dimension(
        "tech_capabilities_benchmarks",
        "How do capabilities and reproducible benchmarks compare under like-for-like conditions?",
        ("official_technical_report", "benchmark", "scholarly"),
        ("capability benchmark comparison", "evaluation methodology results"),
        first_party_required=False,
    ),
    _dimension(
        "tech_adoption_ecosystem",
        "What adoption, integrations, developer ecosystem, and operational evidence exist?",
        ("official_documentation", "repository", "independent_research"),
        ("adoption integrations ecosystem", "developer operational evidence"),
        first_party_required=False,
    ),
    _dimension(
        "tech_risks_limits",
        "What limitations, safety risks, costs, and contrary evidence constrain use?",
        ("official_safety_report", "scholarly", "independent_research"),
        ("limitations risks costs", "contrary evidence incidents"),
        first_party_required=False,
    ),
    _dimension(
        "tech_outlook",
        "What announced roadmap and evidence-based outlook follow from the current evidence?",
        ("official_roadmap", "official_release", "independent_research"),
        ("announced roadmap", "technology outlook uncertainty"),
        first_party_required=True,
    ),
)

_PROFILE_DEFINITIONS = {
    "policy_education": ProfileDefinition(
        "policy_education",
        _EDUCATION_DIMENSIONS,
        ("official_policy", "official_statistics", "finance_budget", "independent_research"),
    ),
    "generic_research": ProfileDefinition(
        "generic_research",
        _GENERIC_DIMENSIONS,
        ("official", "primary_source", "scholarly", "independent_research"),
    ),
    "technology_intelligence": ProfileDefinition(
        "technology_intelligence",
        _TECHNOLOGY_DIMENSIONS,
        ("official_release", "official_documentation", "repository", "scholarly"),
    ),
}

_DIMENSION_ALIASES = {
    "edu_current_state": ("教育现状", "current state", "current education"),
    "edu_scale_trend": ("规模", "趋势", "enrollment", "scale", "trend"),
    "edu_equity_urban_rural_region": ("城乡", "区域", "均衡", "urban-rural", "regional equity"),
    "edu_teacher_finance": ("教师", "师资", "财政", "teacher", "finance"),
    "edu_double_reduction_after_school_burden": ("双减", "课后服务", "负担", "double reduction", "after-school", "burden"),
    "edu_national_next_plan": ("下一步计划", "国家计划", "next plan", "roadmap"),
    "generic_who": ("相关方", "人群", "who", "stakeholder"),
    "generic_what": ("范围", "定义", "what", "scope"),
    "generic_current_state": ("现状", "current state", "status"),
    "generic_drivers": ("驱动", "原因", "drivers", "causes"),
    "generic_outlook": ("展望", "预测", "outlook", "forecast"),
    "tech_current_state": ("当前版本", "当前状态", "current version", "current state"),
    "tech_recent_changes": ("近期变化", "发布", "recent changes", "releases"),
    "tech_capabilities_benchmarks": ("能力", "基准", "benchmark", "capabilities"),
    "tech_adoption_ecosystem": ("采用", "生态", "adoption", "ecosystem"),
    "tech_risks_limits": ("风险", "限制", "risks", "limitations"),
    "tech_outlook": ("路线图", "展望", "roadmap", "outlook"),
}

_EXCLUSION_MARKERS = (
    "不考虑",
    "不讨论",
    "不需要",
    "无需",
    "排除",
    "忽略",
    "不涉及",
    "exclude",
    "excluding",
    "ignore",
    "without",
    "do not cover",
    "don't cover",
)


def profile_definition(profile: str) -> ProfileDefinition:
    try:
        return _PROFILE_DEFINITIONS[profile]
    except KeyError as exc:
        raise ValueError(f"unsupported research profile: {profile}") from exc


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _locale(question: str) -> str:
    return "zh-CN" if re.search(r"[\u3400-\u9fff]", question) else "en-US"


def _explicit_as_of(question: str, fallback: date) -> str:
    full = re.search(r"(?:截至|截止|as of|by)\s*(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})日?", question, re.I)
    if full:
        try:
            parsed = date(int(full.group(1)), int(full.group(2)), int(full.group(3)))
            return parsed.isoformat() if parsed <= fallback else fallback.isoformat()
        except ValueError:
            pass
    month = re.search(r"(?:截至|截止|as of|by)\s*(\d{4})[-年/](\d{1,2})月?", question, re.I)
    if month:
        try:
            parsed = date(int(month.group(1)), int(month.group(2)), 1)
            return parsed.isoformat() if parsed <= fallback else fallback.isoformat()
        except ValueError:
            pass
    return fallback.isoformat()


def _geography(question: str) -> str | None:
    choices = (
        (("中国", "我国", "china", "chinese"), "China"),
        (("美国", "美國", "united states", "u.s.", "usa"), "United States"),
        (("欧盟", "歐盟", "european union", " eu "), "European Union"),
        (("全球", "worldwide", "global"), "Global"),
    )
    padded = f" {question.casefold()} "
    for terms, canonical in choices:
        if any(term.casefold() in padded for term in terms):
            return canonical
    return None


def _profile(question: str) -> str:
    folded = question.casefold()
    education_terms = (
        "教育", "小学", "中学", "义务教育", "学校", "双减", "课后服务",
        "education", "primary school", "schooling", "teacher workforce",
    )
    if any(term in folded for term in education_terms):
        return "policy_education"
    technology_entities = (
        "人工智能", "大模型", "生成式ai", "llm", "gpt", "claude", "gemini",
        "openai", "anthropic", "deepseek", "model", "foundation model",
    )
    intelligence_terms = (
        "最新", "近期", "发布", "版本", "基准", "能力", "对比", "比较",
        "最强", "前10", "前十", "排名", "排行", "优缺点", "哪个好",
        "latest", "recent", "release", "version", "benchmark", "capability", "compare",
        "strongest", "top 10", "ranking", "rank", "pros and cons", "which is better",
    )
    has_ai_entity = any(term in folded for term in technology_entities) or bool(
        re.search(r"(?<![a-z0-9])ai(?![a-z0-9])", folded)
    )
    if has_ai_entity and any(
        term in folded for term in intelligence_terms
    ):
        return "technology_intelligence"
    return "generic_research"


def _comparison_intents(question: str) -> tuple[str, ...]:
    folded = question.casefold()
    intents: list[str] = []
    if any(term in folded for term in ("城乡", "urban-rural", "urban and rural")):
        intents.append("urban_rural")
    if any(term in folded for term in ("区域", "地区差异", "regional", "region")):
        intents.append("regional")
    if any(term in folded for term in ("趋势", "变化", "历年", "over time", "trend", "change")):
        intents.append("temporal")
    if re.search(
        r"(?:对比|比较|相比|之间选|选哪|哪个好|\bvs\.?\b|\bversus\b|\bcompare\b)",
        folded,
    ):
        intents.append("entity_comparison")
    return tuple(dict.fromkeys(intents))


def _intent_facets(question: str) -> tuple[str, ...]:
    folded = question.casefold()
    facets: list[str] = []
    markers = (
        ("ranking_top_n", ("最强", "前10", "前十", "top 10", "排名", "排行", "ranking")),
        ("pros_cons", ("优缺点", "优点", "缺点", "优势", "劣势", "pros and cons")),
        (
            "product_comparison",
            ("之间选", "选一台", "选一款", "对比", "比较", " vs ", " versus "),
        ),
        (
            "purchase_recommendation",
            ("选哪", "哪个好", "适合什么人", "适合谁", "购买建议", "推荐购买"),
        ),
        (
            "official_statistics",
            ("国家统计局", "官方统计", "统计公报", "official statistics"),
        ),
        (
            "exact_fact_lookup",
            ("分别是多少", "分别为多少", "具体数字", "精确数字", "占比", "城镇化率"),
        ),
        (
            "first_party_required",
            ("官方资料", "原始资料", "第一方", "国家统计局", "官网", "primary source"),
        ),
    )
    for facet, terms in markers:
        if any(term in folded for term in terms):
            facets.append(facet)
    return tuple(facets)


def _subjects(question: str, profile: str) -> tuple[str, ...]:
    folded = question.casefold()
    if profile == "policy_education":
        if any(term in folded for term in ("小学", "primary school", "primary education")):
            return ("primary education",)
        if any(term in folded for term in ("义务教育", "compulsory education")):
            return ("compulsory education",)
        return ("education policy",)
    if profile == "technology_intelligence":
        known = ("GPT", "Claude", "Gemini", "DeepSeek", "OpenAI", "Anthropic")
        found = tuple(item for item in known if item.casefold() in folded)
        return found or ("artificial intelligence technology",)
    product_patterns = (
        r"iphone\s*\d+(?:\s*pro(?:\s*max)?)?",
        r"小米\s*\d+(?:\s*ultra|\s*pro)?",
        r"xiaomi\s*\d+(?:\s*ultra|\s*pro)?",
    )
    products = tuple(
        dict.fromkeys(
            match.group(0).strip()
            for pattern in product_patterns
            for match in re.finditer(pattern, question, re.I)
        )
    )
    if products:
        return products
    quoted = tuple(
        value.strip()
        for pair in re.findall(r'["“]([^"”]{2,80})["”]|\'([^\']{2,80})\'', question)
        for value in pair
        if value.strip()
    )
    return quoted or (_normalized(question)[:160],)


def _facet_dimensions(
    question: str,
    intent: DetectedResearchIntent,
) -> tuple[ResearchDimension, ...]:
    facets = set(intent.intent_facets)
    if intent.profile == "technology_intelligence" and {
        "ranking_top_n",
        "pros_cons",
    } & facets:
        return (
            _dimension(
                "tech_ranking_method_candidates",
                "Which reproducible ranking method and candidate set answer the requested top-N question?",
                ("benchmark", "official_technical_report", "independent_research"),
                ("ranking methodology candidate frontier models", "top model evaluation criteria"),
                first_party_required=False,
            ),
            _dimension(
                "tech_pricing_access_pros_cons",
                "What are each candidate's strengths, weaknesses, pricing, access conditions, and recency limits?",
                ("official_pricing", "official_documentation", "independent_research"),
                ("model pricing access availability", "model strengths weaknesses limitations"),
                first_party_required=True,
            ),
            _TECHNOLOGY_DIMENSIONS[2],
            replace(_TECHNOLOGY_DIMENSIONS[0], importance="supporting"),
            replace(_TECHNOLOGY_DIMENSIONS[1], importance="supporting"),
        )
    if "product_comparison" in facets:
        return (
            _dimension("product_camera", "How do the products compare on camera hardware, image quality, and video?", ("official_specification", "independent_review"), ("camera specification image quality video comparison",), first_party_required=True),
            _dimension("product_battery", "How do battery capacity, endurance, charging, and thermal behavior compare?", ("official_specification", "independent_test"), ("battery endurance charging thermal comparison",), first_party_required=True),
            _dimension("product_price", "What are the current official prices, configurations, and availability?", ("official_pricing", "official_store"), ("official price configuration availability",), first_party_required=True),
            _dimension("product_ecosystem", "How do operating systems, update support, services, and device ecosystems compare?", ("official_documentation", "independent_review"), ("operating system updates ecosystem services",), first_party_required=True),
            _dimension("product_drawbacks", "What are the material drawbacks, limitations, and contrary evidence for each product?", ("independent_review", "official_support"), ("drawbacks limitations issues comparison",), first_party_required=False),
            _dimension("product_recommendation", "Which user profiles and priorities make each product the better choice?", ("independent_review", "official_specification"), ("suitable users purchase recommendation tradeoffs",), first_party_required=False),
        )
    if {"official_statistics", "exact_fact_lookup"} <= facets:
        return (
            _dimension("stat_total_population", "What was the total population for the requested year?", ("official_statistics",), ("total population requested year",), first_party_required=True),
            _dimension("stat_birth_population", "How many births were recorded for the requested year?", ("official_statistics",), ("birth population requested year",), first_party_required=True),
            _dimension("stat_age_65_share", "What share of the population was aged 65 or older?", ("official_statistics",), ("population age 65 and over share",), first_party_required=True),
            _dimension("stat_urbanization_rate", "What was the urbanization rate for the requested year?", ("official_statistics",), ("urbanization rate requested year",), first_party_required=True),
        )
    return profile_definition(intent.profile).dimensions


def _explicit_exclusions(
    question: str,
    dimensions: tuple[ResearchDimension, ...],
) -> tuple[tuple[str, str], ...]:
    folded = question.casefold()
    boundaries = ",，。;；!?！？\n"
    exclusions: list[tuple[str, str]] = []
    for dimension in dimensions:
        matched = False
        for alias in _DIMENSION_ALIASES.get(dimension.dimension_id, ()):
            for occurrence in re.finditer(re.escape(alias.casefold()), folded):
                start = occurrence.start()
                left = max(
                    (question.rfind(mark, 0, start) for mark in boundaries),
                    default=-1,
                ) + 1
                candidates = [
                    position
                    for mark in boundaries
                    if (position := question.find(mark, occurrence.end())) >= 0
                ]
                right = min(candidates) if candidates else len(question)
                clause = _normalized(question[left:right])
                if not any(
                    marker in clause.casefold() for marker in _EXCLUSION_MARKERS
                ):
                    continue
                exclusions.append((dimension.dimension_id, clause))
                matched = True
                break
            if matched:
                break
    return tuple(exclusions)


def detect_research_intent(
    question: str,
    *,
    as_of_date: date | None = None,
) -> DetectedResearchIntent:
    normalized = _normalized(question)
    if not normalized:
        raise ValueError("research question is required")
    today = as_of_date or date.today()
    profile = _profile(normalized)
    dimensions = profile_definition(profile).dimensions
    return DetectedResearchIntent(
        profile=profile,
        locale=_locale(normalized),
        as_of_date=_explicit_as_of(normalized, today),
        geography=_geography(normalized),
        subjects=_subjects(normalized, profile),
        comparison_intents=_comparison_intents(normalized),
        intent_facets=_intent_facets(normalized),
        explicit_exclusions=_explicit_exclusions(normalized, dimensions),
    )


def _expected_decision(intent: DetectedResearchIntent) -> str:
    base = {
        "policy_education": "assess current education conditions and distinguish published national commitments from uncertain outlook",
        "technology_intelligence": "compare current technology evidence, material changes, capabilities, adoption, risks, and outlook",
        "generic_research": "answer who, what, current state, drivers, and evidence-based outlook",
    }[intent.profile]
    if intent.comparison_intents:
        base += "; comparison axes=" + ",".join(intent.comparison_intents)
    if intent.intent_facets:
        base += "; intent facets=" + ",".join(intent.intent_facets)
    return base


def _brief_id(question: str, intent: DetectedResearchIntent) -> str:
    payload = json.dumps(
        {
            "question": question,
            "profile": intent.profile,
            "as_of_date": intent.as_of_date,
            "locale": intent.locale,
            "geography": intent.geography,
            "intent_facets": intent.intent_facets,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "brief-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _dimension_from_model(value: object) -> ResearchDimension:
    if not isinstance(value, Mapping):
        raise ContractValidationError("modeling dimension must be an object")
    allowed = {
        "dimension_id", "question", "importance", "expected_source_types",
        "query_targets", "first_party_required", "not_applicable_when",
    }
    unknown = set(value) - allowed
    missing = allowed - set(value)
    if unknown or missing:
        raise ContractValidationError(
            f"modeling dimension fields mismatch; unknown={sorted(unknown)} missing={sorted(missing)}"
        )
    normalized = dict(value)
    importance = normalized.get("importance")
    if importance == "high":
        normalized["importance"] = "core"
    elif importance == "medium":
        normalized["importance"] = "supporting"
    return ResearchDimension.from_json({"schema_version": 1, **normalized})


def validate_modeling_output(
    value: Mapping[str, Any],
    *,
    require_complete: bool = False,
) -> ValidatedModelingOutput:
    # profile/locale/geography are deterministic server-owned identity fields.
    # Older or drifted providers may echo them, but they can never override the
    # server policy and must not poison otherwise useful semantic dimensions.
    value = {
        key: item
        for key, item in value.items()
        if key not in {"profile", "locale", "geography"}
    }
    allowed = {
        "subjects", "expected_decision", "dimensions"
    }
    unknown = set(value) - allowed
    if unknown:
        raise ContractValidationError(f"unknown modeling fields: {sorted(unknown)}")
    if require_complete:
        missing = {"subjects", "expected_decision", "dimensions"} - set(value)
        if missing:
            raise ContractValidationError(
                f"missing required modeling fields: {sorted(missing)}"
            )
    expected_decision = value.get("expected_decision")
    if expected_decision is not None and (
        not isinstance(expected_decision, str) or not expected_decision.strip()
    ):
        raise ContractValidationError("invalid modeling expected_decision")
    subjects_raw = value.get("subjects")
    subjects: tuple[str, ...] | None = None
    if subjects_raw is not None:
        if not isinstance(subjects_raw, list) or not subjects_raw or not all(
            isinstance(item, str) and item.strip() for item in subjects_raw
        ):
            raise ContractValidationError("modeling subjects must be a non-empty string array")
        subjects = tuple(dict.fromkeys(item.strip() for item in subjects_raw))
    dimensions_raw = value.get("dimensions")
    dimensions: tuple[ResearchDimension, ...] | None = None
    rejected_dimensions: list[tuple[int, str]] = []
    if dimensions_raw is not None:
        if not isinstance(dimensions_raw, list):
            raise ContractValidationError("modeling dimensions must be an array")
        accepted: list[ResearchDimension] = []
        seen_ids: set[str] = set()
        seen_questions: set[str] = set()
        seen_targets: set[str] = set()
        for index, raw_dimension in enumerate(dimensions_raw):
            try:
                dimension = _dimension_from_model(raw_dimension)
                question_key = _normalized(dimension.question).casefold()
                target_keys = {
                    _normalized(target).casefold()
                    for target in dimension.query_targets
                }
                if dimension.dimension_id in seen_ids:
                    raise ContractValidationError("duplicate dimension_id")
                if question_key in seen_questions:
                    raise ContractValidationError("duplicate dimension question")
                if target_keys & seen_targets:
                    raise ContractValidationError("shared query target")
            except (ContractValidationError, TypeError, ValueError) as exc:
                rejected_dimensions.append((index, str(exc)))
                continue
            accepted.append(dimension)
            seen_ids.add(dimension.dimension_id)
            seen_questions.add(question_key)
            seen_targets.update(target_keys)
        if len(accepted) < 3 or len(accepted) > 8 or not any(
            item.importance == "core" for item in accepted
        ):
            raise ContractValidationError(
                "modeling dimensions have no usable 3-to-8 item set with a core item"
            )
        dimensions = tuple(accepted)
    return ValidatedModelingOutput(
        subjects=subjects,
        expected_decision=(
            expected_decision.strip() if isinstance(expected_decision, str) else None
        ),
        dimensions=dimensions,
        rejected_dimensions=tuple(rejected_dimensions),
    )


def merge_modeling_output(
    brief: ResearchBrief,
    modeling_output: Mapping[str, Any],
) -> ResearchBrief:
    modeled = validate_modeling_output(modeling_output)

    dimensions = brief.dimensions
    if modeled.dimensions is not None:
        if brief.profile == "generic_research":
            dimensions = modeled.dimensions
        elif any(
            item.dimension_id.startswith(("tech_ranking_", "stat_", "product_"))
            for item in brief.dimensions
        ):
            # Explicit facet templates are the server-owned interpretation of
            # the user's requested axes.  A model may improve subjects and the
            # expected decision, but it cannot re-expand a focused Gate-F brief
            # with generic background dimensions.
            dimensions = brief.dimensions
        else:
            fixed_ids = {item.dimension_id for item in brief.dimensions}
            additions = tuple(
                item
                for item in modeled.dimensions
                if item.dimension_id not in fixed_ids and item.importance == "supporting"
            )
            available = max(0, 8 - len(dimensions))
            dimensions = (*dimensions, *additions[:available])
    return replace(
        brief,
        subjects=modeled.subjects or brief.subjects,
        expected_decision=modeled.expected_decision or brief.expected_decision,
        dimensions=tuple(dimensions),
    )


def build_research_brief(
    question: str,
    *,
    as_of_date: date | None = None,
    modeling_output: Mapping[str, Any] | None = None,
) -> ResearchBrief:
    normalized = _normalized(question)
    intent = detect_research_intent(normalized, as_of_date=as_of_date)
    brief = ResearchBrief(
        brief_id=_brief_id(normalized, intent),
        user_question=normalized,
        profile=intent.profile,  # type: ignore[arg-type]
        as_of_date=intent.as_of_date,
        locale=intent.locale,
        geography=intent.geography,
        subjects=intent.subjects,
        expected_decision=_expected_decision(intent),
        dimensions=_facet_dimensions(normalized, intent),
        not_applicable_conditions=tuple(
            f"{dimension_id}|user_explicit:{reason}"
            for dimension_id, reason in intent.explicit_exclusions
        ),
    )
    return merge_modeling_output(brief, modeling_output) if modeling_output else brief


def initial_dimension_coverages(brief: ResearchBrief) -> tuple[DimensionCoverage, ...]:
    exclusions: dict[str, str] = {}
    normalized_question = _normalized(brief.user_question).casefold()
    for condition in brief.not_applicable_conditions:
        dimension_id, separator, reason = condition.partition("|user_explicit:")
        if not separator or not reason.strip():
            raise ContractValidationError(
                "not_applicable condition requires explicit user exclusion reason"
            )
        normalized_reason = _normalized(reason).casefold()
        if normalized_reason not in normalized_question or not any(
            marker in normalized_reason for marker in _EXCLUSION_MARKERS
        ):
            raise ContractValidationError(
                "not_applicable requires an explicit user exclusion from the question"
            )
        exclusions[dimension_id] = reason.strip()
    known_ids = {item.dimension_id for item in brief.dimensions}
    if not set(exclusions) <= known_ids:
        raise ContractValidationError("not_applicable condition references unknown dimension")
    return tuple(
        DimensionCoverage(
            dimension_id=item.dimension_id,
            status="not_applicable" if item.dimension_id in exclusions else "uncovered",
            evidence_passage_ids=(),
            winning_evidence_ids=(),
            source_family_ids=(),
            first_party_satisfied=False,
            relevance_score=0.0,
            gap_reasons=(
                f"user_explicit_exclusion:{exclusions[item.dimension_id]}"
                if item.dimension_id in exclusions
                else "missing_evidence"
            ,),
        )
        for item in brief.dimensions
    )


__all__ = [
    "DetectedResearchIntent",
    "EDUCATION_DIMENSION_IDS",
    "GENERIC_DIMENSION_IDS",
    "ProfileDefinition",
    "TECHNOLOGY_DIMENSION_IDS",
    "ValidatedModelingOutput",
    "build_research_brief",
    "detect_research_intent",
    "initial_dimension_coverages",
    "merge_modeling_output",
    "profile_definition",
    "validate_modeling_output",
]

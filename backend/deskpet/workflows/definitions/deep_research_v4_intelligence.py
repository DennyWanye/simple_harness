"""Deterministic technology-intelligence classification, planning and ranking."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

from .deep_research_v4_contracts import (
    IntentProfile,
    SourceSeed,
    TechnologyFinding,
    TechnologyTopic,
)


_AI_TERMS = ("ai", "人工智能", "大模型", "llm", "基础模型", "agent", "智能体", "多模态")
_RECENT_TERMS = (
    "最新", "近期", "前沿", "趋势", "突破", "价值", "值得关注",
    "latest", "recent", "frontier", "trend", "breakthrough", "valuable",
)
_TECH_TERMS = (
    "技术", "模型", "推理", "训练", "框架", "系统", "开源", "基础设施",
    "technology", "model", "inference", "training", "framework", "system",
    "open source", "infrastructure",
)
_OPTIONAL_DIMENSIONS = {
    "regulation": ("监管", "政策", "法律", "治理", "regulation", "policy", "law", "governance"),
    "business_case": ("商业案例", "business case"),
    "industry_application": ("行业应用", "industry application"),
}

_TAXONOMY = (
    ("model_inference", "基础模型与推理", "vLLM"),
    ("agent", "Agent 与工具调用", "Model Context Protocol"),
    ("multimodal", "原生多模态", "Gemini multimodal"),
    ("training_inference_system", "训练与推理系统", "PyTorch distributed training"),
    ("open_infrastructure", "开源基础设施", "Hugging Face Transformers"),
)

_GENERIC_TECH_ENTITIES = frozenset(
    {
        "ai",
        "artificial intelligence",
        "llm",
        "large language model",
        "technology",
        "tech",
        "llm inference",
        "foundation model inference",
        "ai agents",
        "ai agent tool use",
        "multimodal ai",
        "native multimodal model",
        "ai systems",
        "ai training inference system",
        "open ai infrastructure",
        "open source ai infrastructure",
    }
)
_GENERIC_TECH_ENTITY_MARKERS = (
    "latest technology",
    "technology research",
    "technology trends",
    "ai research",
    "最新技术",
    "技术调研",
    "技术趋势",
)


def _is_generic_technology_entity(value: str) -> bool:
    normalized = " ".join(value.casefold().replace("_", " ").replace("-", " ").split())
    return (
        len(normalized) < 3
        or normalized in _GENERIC_TECH_ENTITIES
        or any(marker in normalized for marker in _GENERIC_TECH_ENTITY_MARKERS)
    )


def _is_unrequested_version_anchor(entity: str, user_topic: str) -> bool:
    """Reject planner-invented version pins for broad technology discovery.

    A planning model can confidently return an older named release even when
    asked for the latest technology landscape.  Exact versions are useful when
    the user named them, but otherwise they prematurely narrow every downstream
    search query and source pack to a potentially stale product snapshot.
    """

    normalized_entity = " ".join(entity.casefold().split())
    normalized_topic = " ".join(user_topic.casefold().split())
    if normalized_entity and normalized_entity in normalized_topic:
        return False
    return bool(
        re.search(r"\d", entity)
        or re.search(r"\b(?:version|release|specification|alpha|beta|rc)\b", entity, re.I)
    )

_PRIMARY_DOMAINS = frozenset(
    {
        "arxiv.org", "openreview.net", "github.com", "openai.com", "anthropic.com",
        "deepmind.google", "ai.google.dev", "microsoft.com", "nvidia.com", "meta.com",
        "huggingface.co", "caict.ac.cn", "baidu.com", "deepseek.com", "x.ai",
        "mistral.ai", "cohere.com", "pytorch.org", "tensorflow.org",
    }
)
_QUANT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|x|ms|s|tokens?|k|m|b|gb|tb)\b", re.I)
_QUANT_CONTEXT_RE = re.compile(
    r"benchmark|accuracy|score|cost|latency|throughput|context|参数|准确率|得分|成本|延迟|吞吐|上下文",
    re.I,
)
_ADOPTED_RE = re.compile(r"\bga\b|generally available|stable|production|正式发布|稳定版|生产可用", re.I)
_EMERGING_RE = re.compile(r"\bbeta\b|preview|released|release|公测|预览|发布", re.I)
_EXPERIMENTAL_RE = re.compile(r"preprint|prototype|research preview|实验|原型|预印本", re.I)
_CAPABILITY_RE = re.compile(r"support(?:s|ed)?|enable(?:s|d)?|interop|新增|支持|互操作", re.I)
_CHANGE_RE = re.compile(
    r"introduc(?:e|es|ed)|improv(?:e|es|ed)|reduc(?:e|es|ed)|increase(?:s|d)?|"
    r"faster|integrat(?:e|es|ed)|compatib|launch(?:es|ed)?|release(?:s|d)?|"
    r"新增|提升|降低|加速|集成|兼容|发布|开源",
    re.I,
)
_VERTICAL_RE = re.compile(
    r"\behr\b|electronic health record|healthcare|clinical|medical|patient|hospital|"
    r"finance|banking|legal services|education|retail|insurance|pharma|drug discovery|"
    r"molecule|genomics|radiology|pathology|agriculture|crop|supply chain|marketing|"
    r"customer service|real estate|医疗|电子病历|临床|患者|医院|金融|银行|教育|零售|"
    r"保险|制药|药物发现|分子|基因组|放射科|病理|农业|作物|供应链|营销|客服|地产|政务",
    re.I,
)
_NAVIGATION_RE = re.compile(
    r"example workflows and tasks teams can take on with chatgpt or codex|"
    r"explore (?:our )?(?:products|solutions|resources)|browse all (?:products|solutions|resources)|"
    r"sign in(?: to)? chatgpt|contact sales|cookie preferences|skip to (?:main )?content",
    re.I,
)
_EVENT_ONLY_RE = re.compile(
    r"\b(?:conference|forum|workshop|summit)\b.*\b(?:was held|took place|convened)\b|"
    r"(?:论坛|会议|峰会|研讨会).{0,32}(?:举办|举行|召开|成功举办)",
    re.I,
)
_TOPIC_KEYWORDS = {
    "model_inference": re.compile(r"\bllm\b|\binference\b|foundation model|transformer|model inference|大模型|基础模型|模型推理", re.I),
    "agent": re.compile(r"\bagents?\b|tool use|tool calling|\bmcp\b|智能体|工具调用", re.I),
    "multimodal": re.compile(r"multimodal|vision|image|video|audio|speech|多模态|视觉|图像|视频|音频|语音", re.I),
    "training_inference_system": re.compile(r"training|inference system|serving|runtime|gpu|cpu|cuda|latency|throughput|activation[- ]offload|训练|推理系统|服务框架|运行时|延迟|吞吐|激活卸载", re.I),
    "open_infrastructure": re.compile(r"open[ -]?source|framework|runtime|library|repository|github|hugging ?face|开源|框架|运行时|代码库", re.I),
}
_ENTITY_PHRASE_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)*)(?:\s+[A-Z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)*){0,2}\b"
)
_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9-]{1,9}\b")
_GENERIC_ENTITY_WORDS = frozenset({
    "a", "an", "the", "this", "that", "ai", "llm", "agent", "agents", "model", "models",
    "system", "framework", "governance", "api", "gpu", "top", "new", "official", "research",
})
_VENDOR_PREFIX_RE = re.compile(
    r"^(?:openai|google|google deepmind|deepmind|microsoft|meta|anthropic|nvidia|"
    r"alibaba|aliyun|tencent|baidu|amazon|aws|hugging face|huggingface)\s+",
    re.I,
)
_VERSION_SUFFIX_RE = re.compile(
    r"(?:\s+|[-_.])(?:v(?:ersion)?\s*)?\d+(?:\.\d+)*(?:[-_.]?(?:alpha|beta|rc)\d*)?$",
    re.I,
)
_VARIANT_SUFFIX_RE = re.compile(
    r"\s+(?:sdk|framework|toolkit|library|platform|runtime|系统|框架|工具包|平台)$",
    re.I,
)
_ENTITY_ALIASES = {
    "hunyuan image": "hunyuanimage",
    "hunyuanimage": "hunyuanimage",
    "lang chain": "langchain",
    "langchain": "langchain",
    "llama index": "llamaindex",
    "llamaindex": "llamaindex",
    "semantic kernel": "semantickernel",
    "microsoft agent framework": "agentframework",
    "agent framework": "agentframework",
}
_GENERIC_ENTITY_CORES = frozenset({"agent", "agents", "model", "models", "platform", "runtime"})
_LOWER_PROJECT_RE = re.compile(r"\b[a-z][a-z0-9]{2,}(?:[-_.][a-z0-9]+)+\b")
_LEADING_SUBJECT_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)*"
    r"(?:\s+[A-Za-z0-9][A-Za-z0-9+_.-]*){0,3}?)\s+"
    r"(?:is|are|was|were|uses?|provides?|supports?|enables?|introduced|released|launched|"
    r"includes?|unifies?|maps?|executes?|preserves?|adds?|added|expands?|expanded|overlaps?|"
    r"degrades?|physically\s+separates?|can)\b",
    re.I,
)
_VENDOR_OBJECT_RE = re.compile(
    r"^\s*(Apple|Google|Microsoft|Meta|NVIDIA|OpenAI|Anthropic)\s+introduced\s+"
    r"(?:a|an)\s+new generation of\s+(.+?)(?:\s+at\b|\s+for\b|\s+with\b|[.;]|$)",
    re.I,
)
_VENDOR_PRODUCT_RE = re.compile(
    r"^\s*(OpenAI|Google|Microsoft|Meta|NVIDIA|Apple|Anthropic)\s+"
    r"(?:released|launched|introduced|unveiled)\s+(.+?)"
    r"(?:\s+(?:with|for|at|that|which|as|in)\b|[.;]|$)",
    re.I,
)
_ENTITY_ACTION_RE = re.compile(r"发布|新增|降低|提升|支持|能力|延迟|吞吐|正式", re.I)
_GENERIC_FINDING_ENTITY_KEYS = frozenset({
    "aitraining", "aiinference", "bestaiagents", "theopensourceengine", "ces",
    "apple", "google", "microsoft", "meta", "nvidia", "openai", "anthropic", "pdf",
    "uu", "generalpurposeassistants", "multimodalfoundation", "towards", "ondevice", "patch",
    "contextwindow", "plus", "vla",
})
_NON_CHANGE_STATEMENT_RE = re.compile(
    r"\b(?:are central to|often require|builds a model by|big money has flowed|"
    r"emphasis is shifting|cost difference|best ai agents|ranks?\s+#?\d+|leaderboard|"
    r"is an?\s+(?:(?:open[- ]source|high[- ]performance|native|multimodal|agentic)"
    r"(?:\s*[,/-]\s*|\s+))*"
    r"(?:model|library|framework|platform|repository|tool|serving engine)\b|"
    r"has\s+\d+\s+repositories\b|allows?\s+(?:users?\s+)?to\s+(?:interact|build)\b|"
    r"community building|monthly sdk downloads|runs in production|ecosystem dominance|"
    r"we trace this to|production checklist|recommends capturing|"
    r"^\s*#{1,6}\s*model support\b|"
    r"(?:nvidia-smi\s+.+?\s+captures|capture)\s+gpu\s+telemetry|"
    r"benchmark\s+breakdown\s+compared|"
    r"these\s+release\s+notes\s+describe|"
    r"uses\s+a\s+.+?\s+protocol\s+over|"
    r"moved\s+well\s+past\s+its\s+origins|"
    r"deliberate\s+decision\s+grounded\s+in\s+.+?\s+design\s+principles|"
    r"featured\s+.+?\s+as\s+of)\b",
    re.I,
)
_MALFORMED_PRODUCT_SUBJECT_RE = re.compile(
    r"^\s*\d+(?:\.\d+)?\s+Pro\b",
    re.I,
)
_RELEASE_METADATA_ONLY_RE = re.compile(
    r"\b(?:marked\s+the\s+release\s+candidate\s+revision|"
    r"locked\s+the\s+release\s+candidate|"
    r"released\s+as\s+a\s+release\s+candidate)\b",
    re.I,
)
_STATIC_NON_CHANGE_RE = re.compile(
    r"^\s*.+?\s+is\s+(?:"
    r"open[- ]source|"
    r"(?:(?:an?|the)\s+)?(?:[A-Za-z0-9-]+\s+){0,4}"
    r"(?:model|library|framework|platform|repository|tool|serving engine|"
    r"orchestration layer(?:\s+above\s+.+)?)"
    r")\s*\.?\s*$",
    re.I,
)
_RELEASE_DATE_ONLY_RE = re.compile(
    r"^\s*.+?\s+(?:(?:was\s+)?released\s+on|was\s+dated)\s+\d{4}-\d{2}-\d{2}\.?\s*$",
    re.I,
)
_POSSESSIVE_PROJECT_RE = re.compile(
    r"^\s*([A-Z][A-Za-z0-9+_.-]{2,40})[’']s\s+[A-Za-z0-9+_.-]+\s+"
    r"(?:added|introduced|released|enabled|improved|reduced)\b",
    re.I,
)
_METRIC_SUBJECT_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9+_.-]{2,40})\b[^.;]{0,100}?\b"
    r"(?:achiev(?:e|es|ed)|reduces?|improves?|outperforms?|deliver(?:s|ed)?)\b",
    re.I,
)
_SECURITY_SIDE_TOPIC_RE = re.compile(
    r"content protection|vulnerabilit|forensic|malware|\bcve[- ]|prompt injection|"
    r"security benchmark|agentic crawler",
    re.I,
)
_AGENT_TECH_DETAIL_RE = re.compile(
    r"tool (?:use|calling)|agent tools?|function calling|\bmcp\b|model context protocol|multi[- ]agent|"
    r"workflow runtime|agent runtime|agent framework|agent sdk|state graph|checkpoint|"
    r"human[- ]in[- ]the[- ]loop|semantic kernel|autogen|langgraph|crewai",
    re.I,
)
_PRICING_ONLY_RE = re.compile(
    r"(?:[$€£¥￥]|\bpricing\b|\bsubscription\b|\bprice plan\b|积分制|赠送|付费档|订阅档)",
    re.I,
)
_OPEN_SOURCE_NEWS_BOILERPLATE_RE = re.compile(
    r"latest news from .{0,40} on open source releases, major projects, events|"
    r"\bwe (?:are|are working to|aim to) .{0,80}(?:democrati[sz]|advanc).{0,40}"
    r"(?:artificial intelligence|\bai\b).{0,40}(?:open source|open science)",
    re.I,
)
_CHALLENGE_ONLY_RE = re.compile(
    r"\b(?:remain|remains|still) (?:difficult|hard|challenging) to (?:deploy|run|serve)|"
    r"\bdeployment remains (?:difficult|hard|challenging)\b",
    re.I,
)
_TECHNICAL_DETAIL_RE = re.compile(
    r"architecture|mixture[- ]of[- ]experts|\bmoe\b|attention|kv[ -]?cache|quantization|"
    r"架构|混合专家|注意力|量化",
    re.I,
)
_PRODUCT_PAIR_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9+_.-]*\s+\d+(?:\.\d+)?\s+[A-Za-z0-9+_.-]+)\s+and\s+"
    r"(?:[A-Za-z][A-Za-z0-9+_.-]*\s+\d+(?:\.\d+)?\s+)?([A-Za-z0-9+_.-]+)\s+"
    r"(?:are|use|support|enable|introduce|provide)\b",
    re.I,
)
_MIXED_LEADING_SUBJECT_RE = re.compile(
    r"^\s*([\u3400-\u9fffA-Za-z][\u3400-\u9fffA-Za-z0-9+_. -]{1,60}?)\s+"
    r"(?:is|are|was|were|uses?|provides?|supports?|enables?|introduced|released|launched|entered)\b",
    re.I,
)
_ENGLISH_MONTH_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(\d{1,2}),\s*(\d{4})\b",
    re.I,
)
_ENGLISH_MONTH_YEAR_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(\d{4})\b",
    re.I,
)
_MONTH_NUMBER = {
    name.casefold(): index
    for index, name in enumerate(
        (
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ),
        1,
    )
}
_CHINESE_ENTITY_RE = re.compile(r"[\u3400-\u9fff]{2,16}(?:模型|框架|平台|系统|引擎|工具链)?")


def normalize_entity_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = re.sub(r"[()\[\]{}]", " ", normalized)
    normalized = re.sub(r"[/_:]+", " ", normalized)
    normalized = " ".join(normalized.split())
    normalized = _VERSION_SUFFIX_RE.sub("", normalized).strip()
    if normalized in _ENTITY_ALIASES:
        normalized = _ENTITY_ALIASES[normalized]
        return re.sub(r"[^\w\u3400-\u9fff]+", "", normalized, flags=re.UNICODE)
    vendor_match = _VENDOR_PREFIX_RE.match(normalized)
    vendor = vendor_match.group(0).strip() if vendor_match else ""
    core = _VENDOR_PREFIX_RE.sub("", normalized).strip()
    reduced = _VARIANT_SUFFIX_RE.sub("", core).strip()
    if reduced in _GENERIC_ENTITY_CORES and vendor:
        # Generic product families are not globally unique. Retain both vendor
        # and variant so unrelated SDKs/frameworks cannot collapse together.
        normalized = f"{vendor} {core}"
    elif len(reduced) >= 3:
        normalized = reduced
    else:
        normalized = core
    normalized = _ENTITY_ALIASES.get(normalized, normalized)
    return re.sub(r"[^\w\u3400-\u9fff]+", "", normalized, flags=re.UNICODE)


def _leading_subject_entity(statement: str) -> str | None:
    meet_subject = re.search(r"^\s*Meet\s+(.+?):", statement, re.I)
    if meet_subject is not None:
        candidate = " ".join(meet_subject.group(1).split()).strip(" .,:;()[]")
        if normalize_entity_key(candidate) and not _is_generic_technology_entity(candidate):
            return candidate
    possessive_project = _POSSESSIVE_PROJECT_RE.search(statement)
    if possessive_project is not None:
        return possessive_project.group(1)
    vendor_object = _VENDOR_OBJECT_RE.search(statement)
    if vendor_object is not None:
        vendor = vendor_object.group(1)
        technology = " ".join(vendor_object.group(2).split()).strip(" .,:;()[]")
        return f"{vendor} {technology}"
    vendor_product = _VENDOR_PRODUCT_RE.search(statement)
    if vendor_product is not None:
        vendor = vendor_product.group(1)
        product = " ".join(vendor_product.group(2).split()).strip(" .,:;()[]")
        if product.casefold().startswith(("a ", "an ", "the ")):
            product = product.split(" ", 1)[1]
        return f"{vendor} {product}"
    product_pair = _PRODUCT_PAIR_RE.search(statement)
    if product_pair is not None:
        first = " ".join(product_pair.group(1).split())
        second = " ".join(product_pair.group(2).split())
        return f"{first}/{second}"
    metric_subject = _METRIC_SUBJECT_RE.search(statement)
    if metric_subject is not None:
        candidate = metric_subject.group(1).strip()
        if normalize_entity_key(candidate) and not _is_generic_technology_entity(candidate):
            return candidate
    mixed_subject = _MIXED_LEADING_SUBJECT_RE.search(statement)
    if mixed_subject is not None:
        candidate = " ".join(mixed_subject.group(1).split()).strip(" .,:;()[]")
        if (
            re.search(r"[\u3400-\u9fff]", candidate)
            and
            normalize_entity_key(candidate)
            and not _is_generic_technology_entity(candidate)
            and not _ENTITY_ACTION_RE.search(candidate)
        ):
            return candidate
    match = _LEADING_SUBJECT_RE.search(statement)
    if match is None:
        return None
    candidate = " ".join(match.group(1).split()).strip(" .,:;()[]")
    candidate = re.sub(r"^(?:a|an|the)\s+", "", candidate, flags=re.I).strip()
    candidate = re.sub(r"\s+(?:fast\s+)?models?$", "", candidate, flags=re.I).strip()
    candidate = re.sub(
        r"\s+(?:preprint\s+prototype|research\s+preview|beta\s+prototype)$",
        "",
        candidate,
        flags=re.I,
    ).strip()
    if not normalize_entity_key(candidate) or _is_generic_technology_entity(candidate):
        return None
    return candidate


def _is_publishable_finding_entity(entity: str) -> bool:
    key = normalize_entity_key(entity)
    return (
        bool(key)
        and key not in _GENERIC_FINDING_ENTITY_KEYS
        and not _ENTITY_ACTION_RE.search(entity)
    )


def _contains(text: str, terms: Iterable[str]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


def _parse_iso(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def _supported_statement_dates(value: str, *, as_of_date: date) -> tuple[date, ...]:
    """Extract only explicit dates contained in the already-supported claim."""

    parsed: set[date] = set()
    for token in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", value):
        candidate = _parse_iso(token)
        if candidate is not None and candidate <= as_of_date:
            parsed.add(candidate)
    for match in _ENGLISH_MONTH_DATE_RE.finditer(value):
        try:
            candidate = date(
                int(match.group(3)),
                _MONTH_NUMBER[match.group(1).casefold()],
                int(match.group(2)),
            )
        except (KeyError, ValueError):
            continue
        if candidate <= as_of_date:
            parsed.add(candidate)
    for match in _ENGLISH_MONTH_YEAR_RE.finditer(value):
        try:
            candidate = date(
                int(match.group(2)),
                _MONTH_NUMBER[match.group(1).casefold()],
                1,
            )
        except (KeyError, ValueError):
            continue
        if candidate <= as_of_date:
            parsed.add(candidate)
    return tuple(sorted(parsed, reverse=True))


def supported_statement_dates(value: str, *, as_of_date: date) -> tuple[date, ...]:
    """Return explicit dates already present in a supported statement."""

    return _supported_statement_dates(value, as_of_date=as_of_date)


def _time_window(topic: str, as_of: date) -> tuple[date, date]:
    fallback = (as_of - timedelta(days=180), as_of)
    text = topic.strip()
    range_match = re.search(r"(\d{4}-\d{2}-\d{2})\s*\.\.\s*(\d{4}-\d{2}-\d{2})", text)
    if range_match:
        start, end = _parse_iso(range_match.group(1)), _parse_iso(range_match.group(2))
        if start is None or end is None:
            return fallback
        end = min(end, as_of)
        return (start, end) if start <= end else fallback

    since_match = re.search(r"(?:since\s+|)(\d{4})\s*年以来|since\s+(\d{4})", text, re.I)
    if since_match:
        year = int(since_match.group(1) or since_match.group(2))
        if year > as_of.year:
            return fallback
        start = date(year, 1, 1)
        return (start, as_of) if 1 <= (as_of - start).days <= 3650 else fallback

    duration_match = re.search(
        r"(?:过去|最近)\s*(-?\d+)\s*(天|周|个月|年)|(?:last|past)\s+(-?\d+)\s*(days?|weeks?|months?|years?)",
        text,
        re.I,
    )
    if duration_match:
        amount = int(duration_match.group(1) or duration_match.group(3))
        unit = (duration_match.group(2) or duration_match.group(4)).casefold()
        factor = 1 if unit in {"天", "day", "days"} else 7 if unit in {"周", "week", "weeks"} else 30 if unit in {"个月", "month", "months"} else 365
        days = amount * factor
        return (as_of - timedelta(days=days), as_of) if 1 <= days <= 3650 else fallback
    return fallback


def classify_intent(topic: str, *, as_of_date: date | None = None) -> IntentProfile:
    as_of = as_of_date or datetime.now(timezone.utc).date()
    technology = all(
        (_contains(topic, _AI_TERMS), _contains(topic, _RECENT_TERMS), _contains(topic, _TECH_TERMS))
    )
    start, end = _time_window(topic, as_of)
    dimensions = tuple(
        key for key, terms in _OPTIONAL_DIMENSIONS.items() if _contains(topic, terms)
    )
    return IntentProfile(
        kind="technology_intelligence" if technology else "generic",
        as_of_date=as_of.isoformat(),
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        requested_dimensions=dimensions,
    )


def source_seeds(topic_kind: str) -> tuple[SourceSeed, ...]:
    seeds = (
        SourceSeed(
            f"{topic_kind}:official", topic_kind, "gateway", "official",
            "{entity} official release docs blog", (), None,
        ),
        SourceSeed(
            f"{topic_kind}:repository", topic_kind, "gateway", "repository",
            "{entity} site:github.com", ("github.com",), None,
        ),
        SourceSeed(
            f"{topic_kind}:scholarly", topic_kind, "direct", "scholarly",
            "{entity}", ("arxiv.org",), "arxiv",
        ),
    )
    return tuple(sorted(seeds, key=lambda value: value.seed_id))


def plan_technology_topics(
    topic: str,
    profile: IntentProfile,
    *,
    proposed: Sequence[Mapping[str, Any]] = (),
) -> tuple[TechnologyTopic, ...]:
    if profile.kind != "technology_intelligence":
        return ()
    allowed = {kind: (label, entity) for kind, label, entity in _TAXONOMY}
    selected: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    seen_entities: set[str] = set()
    for raw in proposed:
        kind = str(raw.get("topic_kind") or "")
        if kind not in allowed or kind in seen:
            continue
        default_label, default_entity = allowed[kind]
        # Topic labels are a public report contract.  Planner prose varies
        # across runs and previously leaked English or overly broad headings
        # into Chinese reports, so always retain the canonical taxonomy label.
        label = default_label
        entity = " ".join(str(raw.get("entity") or default_entity).split())[:120]
        normalized_entity = entity.casefold()
        entity_grounded_in_request = normalized_entity in " ".join(topic.casefold().split())
        if (
            _is_generic_technology_entity(entity)
            or _is_unrequested_version_anchor(entity, topic)
            or not entity_grounded_in_request
            or normalized_entity in seen_entities
        ):
            entity = default_entity
            normalized_entity = entity.casefold()
        if label and entity:
            selected.append((kind, label, entity))
            seen.add(kind)
            seen_entities.add(normalized_entity)
    for kind, label, entity in _TAXONOMY:
        if len(selected) >= 5:
            break
        if kind not in seen:
            selected.append((kind, label, entity))
            seen.add(kind)
            seen_entities.add(entity.casefold())
    selected = selected[:5]
    if len(selected) < 3:
        raise ValueError("technology planner requires three to five topics")
    result: list[TechnologyTopic] = []
    for index, (kind, label, entity) in enumerate(selected):
        result.append(
            TechnologyTopic(
                topic_id=f"t{index}:{kind}",
                label=label,
                entity=entity,
                topic_kind=kind,
                discovery_query=(
                    f"{entity} latest release benchmark {profile.window_end[:4]}"
                ),
                official_query=(
                    f"{entity} official release changelog {profile.window_end[:4]}"
                ),
                scholarly_query=entity,
                source_seeds=source_seeds(kind),
            )
        )
    return tuple(result)


def canonical_host(value: str) -> str:
    host = (urlsplit(value).hostname or "").casefold().strip(".")
    return host[4:] if host.startswith("www.") else host


def is_navigation_or_product_boilerplate(*values: str) -> bool:
    text = "\n".join(value for value in values if value)
    return bool(_NAVIGATION_RE.search(text))


def is_primary_source(value: str) -> bool:
    host = canonical_host(value)
    parsed = urlsplit(value)
    if host == "github.com" and re.search(r"/(?:issues|discussions)(?:/|$)", parsed.path, re.I):
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in _PRIMARY_DOMAINS)


def recency_score(published_at: str, *, as_of_date: date) -> float:
    published = _parse_iso(published_at)
    if published is None or published > as_of_date:
        return 0.0
    age = (as_of_date - published).days
    return 1.0 if age <= 30 else 0.8 if age <= 90 else 0.6 if age <= 180 else 0.3 if age <= 365 else 0.0


def _has_quantified_change(text: str) -> bool:
    return any(
        _QUANT_RE.search(sentence) and _QUANT_CONTEXT_RE.search(sentence)
        for sentence in re.split(r"[。！？.!?\n]+", text)
    )


def score_supported_finding(
    *,
    finding_id: str,
    claim_ids: Sequence[str],
    statement: str,
    winning_passages: Sequence[Mapping[str, Any]],
    winning_citation_ids: Sequence[int],
    as_of_date: date,
    entity: str | None = None,
    topic_kind: str = "technology",
    topic_label: str = "技术情报",
) -> TechnologyFinding:
    hosts = {
        canonical_host(str(item.get("canonical_url") or item.get("url") or ""))
        for item in winning_passages
        if canonical_host(str(item.get("canonical_url") or item.get("url") or ""))
    }
    text = "\n".join(str(item.get("text") or "") for item in winning_passages)
    passage_dates = [
        recency_score(str(item.get("published_at") or item.get("date") or ""), as_of_date=as_of_date)
        for item in winning_passages
    ]
    statement_dates = _supported_statement_dates(statement, as_of_date=as_of_date)
    dates = [*passage_dates, *(recency_score(value.isoformat(), as_of_date=as_of_date) for value in statement_dates)]
    recency = max(dates, default=0.0)
    quantified = _has_quantified_change(text)
    impact = (
        3 if quantified and len(hosts) >= 2
        else 2 if quantified
        else 1 if (_CAPABILITY_RE.search(text) or _CHANGE_RE.search(text))
        else 0
    )
    has_primary = any(
        is_primary_source(str(item.get("canonical_url") or item.get("url") or ""))
        for item in winning_passages
    )
    evidence_quality = 3 if has_primary and len(hosts) >= 2 else 2 if has_primary else 1 if hosts else 0
    primary_texts = "\n".join(
        str(item.get("text") or "")
        for item in winning_passages
        if is_primary_source(str(item.get("canonical_url") or item.get("url") or ""))
    )
    research_hosts = {
        host for host in hosts
        if host == "arxiv.org" or host.endswith(".arxiv.org")
        or host == "openreview.net" or host == "papers.cool"
    }
    repository_hosts = {
        host for host in hosts
        if host == "github.com" or host == "huggingface.co"
    }
    if _ADOPTED_RE.search(primary_texts):
        maturity, maturity_value = "adopted", 3
    elif _EMERGING_RE.search(primary_texts) or repository_hosts or (has_primary and not research_hosts) or len(hosts) >= 2:
        maturity, maturity_value = "emerging", 2
    elif _EXPERIMENTAL_RE.search(text) or research_hosts:
        maturity, maturity_value = "experimental", 1
    else:
        maturity, maturity_value = "unknown", 0
    total = round(
        0.35 * recency
        + 0.30 * (impact / 3)
        + 0.20 * (maturity_value / 3)
        + 0.15 * (evidence_quality / 3),
        6,
    )
    dated = sorted(
        {
            parsed.isoformat()
            for item in winning_passages
            if (parsed := _parse_iso(str(item.get("published_at") or item.get("date") or ""))) is not None
            and parsed <= as_of_date
        }
        | {value.isoformat() for value in statement_dates},
        reverse=True,
    )
    return TechnologyFinding(
        finding_id=finding_id,
        entity=entity or finding_id,
        topic_kind=topic_kind,
        topic_label=topic_label,
        claim_ids=tuple(claim_ids),
        published_at=dated[0] if dated else None,
        recency_score=recency,
        impact_score=impact,
        maturity=maturity,  # type: ignore[arg-type]
        evidence_quality=evidence_quality,
        winning_citation_ids=tuple(sorted({int(value) for value in winning_citation_ids if int(value) > 0})),
        total_score=total,
        statement=statement,
        localized_statement=None,
    )


def _topic_for_passages(
    passages: Sequence[Mapping[str, Any]],
    technology_topics: Sequence[TechnologyTopic],
) -> TechnologyTopic | None:
    by_label = {topic.label.casefold(): topic for topic in technology_topics}
    by_entity = {topic.entity.casefold(): topic for topic in technology_topics}
    for passage in passages:
        question = str(passage.get("question_id") or passage.get("question") or "").casefold()
        if question in by_label:
            return by_label[question]
        if question in by_entity:
            return by_entity[question]
    return None


def _entity_name(
    statement: str,
    passages: Sequence[Mapping[str, Any]],
    topic: TechnologyTopic | None,
) -> str:
    titles = [str(item.get("title") or "") for item in passages]
    searchable = "\n".join([statement, *titles])
    for title in titles:
        behavior_tree = re.search(
            r"\b((?:Contract[- ]Grounded\s+)?Behavior Tree Synthesis)\b",
            title,
            re.I,
        )
        if behavior_tree:
            return behavior_tree.group(1)
    source_urls = "\n".join(
        str(item.get("canonical_url") or item.get("url") or "")
        for item in passages
    ).casefold()
    if "torchcomms" in searchable.casefold() and "communications backend" in searchable.casefold():
        return "torchcomms"
    if "heteromosaic" in searchable.casefold():
        return "HeteroMosaic"
    if "model runner v2" in searchable.casefold() and "vllm-project/vllm" in source_urls:
        return "vLLM Model Runner V2"
    if "vllm-project/vllm" in source_urls and re.search(r"^\s*vLLM\b", statement, re.I):
        # Release notes often lead with the project name followed by a backend
        # or model feature.  Treat those changes as one vLLM finding instead
        # of emitting weak acronym entities such as ``FP8`` beside ``vLLM``.
        return "vLLM"
    for title in titles:
        jetson_pi = re.search(r"\bJetson-PI\b", title, re.I)
        if jetson_pi and re.search(
            r"\bJetson-PI\b|\bcited method\b|\bcontrol frequency\b",
            statement,
            re.I,
        ):
            return "Jetson-PI"
    leading = _leading_subject_entity(statement)
    if leading is not None:
        if leading.casefold().startswith("model runner") and "vllm-project/vllm" in source_urls:
            return f"vLLM {leading}"
        return leading
    llm_agent = re.search(r"\bLarge Language Models?\s*\(LLMs?\)\s+agents?\b", searchable, re.I)
    if llm_agent:
        return "LLM Agents"
    for acronym in _ACRONYM_RE.findall(searchable):
        if acronym.casefold() not in {"ai", "llm", "api", "gpu", "ide", "uw"}:
            return acronym
    candidates: list[str] = []
    for match in _ENTITY_PHRASE_RE.finditer(searchable):
        candidate = " ".join(match.group(0).split()).strip(" .,:;()[]")
        words = [word.casefold() for word in candidate.split()]
        if not words or all(word in _GENERIC_ENTITY_WORDS for word in words):
            continue
        candidates.append(candidate)
    candidates.extend(_CHINESE_ENTITY_RE.findall(statement))
    candidates = [
        candidate
        for candidate in candidates
        if normalize_entity_key(candidate)
        and not _is_generic_technology_entity(candidate)
        and normalize_entity_key(candidate) not in {"model", "system", "framework", "technology"}
    ]
    if candidates:
        # Prefer repeatable product/project identifiers; vendor/version variants
        # share the same normalized key and are merged later.
        return sorted(
            set(candidates),
            key=lambda value: (
                -int(bool(re.search(r"[a-z][A-Z]|[-_.]|\d", value))),
                -sum(ch.isupper() for ch in value),
                len(value),
                value.casefold(),
            ),
        )[0]
    fallback_candidates: list[str] = []
    for title in titles:
        fallback_candidates.extend(_LOWER_PROJECT_RE.findall(title.casefold()))
        fallback_candidates.extend(_CHINESE_ENTITY_RE.findall(title))
    for passage in passages:
        path = urlsplit(str(passage.get("canonical_url") or passage.get("url") or "")).path
        slug = path.strip("/").split("/")[-1]
        if slug and ("-" in slug or "_" in slug) and not slug.isdigit():
            fallback_candidates.append(slug)
    fallback_candidates = [
        candidate
        for candidate in fallback_candidates
        if normalize_entity_key(candidate) and not _is_generic_technology_entity(candidate)
    ]
    if fallback_candidates:
        return sorted(set(fallback_candidates), key=lambda value: (len(value), value.casefold()))[0]
    return topic.label if topic is not None else "technology"


def _is_taxonomy_relevant(
    text: str,
    topic: TechnologyTopic | None,
    *,
    user_topic: str,
) -> bool:
    if _VERTICAL_RE.search(text) and not _VERTICAL_RE.search(user_topic):
        return False
    if topic is not None:
        if (
            topic.topic_kind == "agent"
            and _SECURITY_SIDE_TOPIC_RE.search(text)
            and not _SECURITY_SIDE_TOPIC_RE.search(user_topic)
        ):
            return False
        entity = " ".join(topic.entity.casefold().split())
        if len(entity) >= 4 and entity in text.casefold():
            return True
        if topic.topic_kind == "agent":
            matcher = _TOPIC_KEYWORDS.get(topic.topic_kind)
            return bool(
                _AGENT_TECH_DETAIL_RE.search(text)
                or (matcher and matcher.search(text) and _CHANGE_RE.search(text))
            )
        matcher = _TOPIC_KEYWORDS.get(topic.topic_kind)
        return bool(
            matcher
            and (
                matcher.search(text)
                or _TECHNICAL_DETAIL_RE.search(text)
            )
        )
    return any(matcher.search(text) for matcher in _TOPIC_KEYWORDS.values())


def build_ranked_findings(
    *,
    claims: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    passages: Sequence[Mapping[str, Any]],
    citation_sources: Sequence[Mapping[str, Any]],
    as_of_date: date,
    technology_topics: Sequence[TechnologyTopic] = (),
    user_topic: str = "",
) -> tuple[TechnologyFinding, ...]:
    decision_by_claim = {str(item.get("claim_id") or ""): item for item in decisions}
    passage_by_id = {str(item.get("passage_id") or ""): item for item in passages}
    source_by_id = {int(item.get("citation_id") or 0): item for item in citation_sources}
    groups: dict[str, dict[str, Any]] = {}
    for index, claim in enumerate(claims):
        claim_id = str(claim.get("claim_id") or "")
        decision = decision_by_claim.get(claim_id)
        if not claim_id or not isinstance(decision, Mapping) or not decision.get("supported"):
            continue
        winning_ids = [int(value) for value in decision.get("winning_source_citation_ids", [])]
        winning = []
        for passage_id in decision.get("winning_passage_ids", []):
            passage = passage_by_id.get(str(passage_id))
            if passage is None:
                continue
            source = source_by_id.get(int(passage.get("source_citation_id") or 0), {})
            winning.append({**source, **passage})
        statement = str(claim.get("text") or "").strip()
        topic = _topic_for_passages(winning, technology_topics)
        combined = "\n".join([statement, *(str(item.get("text") or "") for item in winning)])
        official_release_context = any(
            is_primary_source(str(item.get("canonical_url") or item.get("url") or ""))
            and re.search(
                r"/releases(?:/|$)",
                urlsplit(str(item.get("canonical_url") or item.get("url") or "")).path,
                re.I,
            )
            for item in winning
        )
        if (
            not statement
            or is_navigation_or_product_boilerplate(combined)
            or (
                _EVENT_ONLY_RE.search(combined)
                and not (_CHANGE_RE.search(statement) or _CAPABILITY_RE.search(statement))
            )
            or (_NON_CHANGE_STATEMENT_RE.search(statement) and not official_release_context)
            or _MALFORMED_PRODUCT_SUBJECT_RE.search(statement)
            or (_RELEASE_METADATA_ONLY_RE.search(statement) and not official_release_context)
            or (_STATIC_NON_CHANGE_RE.search(statement) and not official_release_context)
            or (_RELEASE_DATE_ONLY_RE.search(statement) and not official_release_context)
            or (
                _PRICING_ONLY_RE.search(statement)
                and not (
                    _CHANGE_RE.search(statement)
                    and _QUANT_CONTEXT_RE.search(statement)
                )
            )
            or _OPEN_SOURCE_NEWS_BOILERPLATE_RE.search(statement)
            or _CHALLENGE_ONLY_RE.search(statement)
            or not _is_taxonomy_relevant(combined, topic, user_topic=user_topic)
        ):
            continue
        entity = _entity_name(statement, winning, topic)
        if not _is_publishable_finding_entity(entity):
            continue
        key = normalize_entity_key(entity)
        if not key:
            key = normalize_entity_key(topic.label if topic is not None else "technology")
        group = groups.setdefault(
            key,
            {
                "entity": entity,
                "topic_kind": topic.topic_kind if topic is not None else "technology",
                "topic_label": topic.label if topic is not None else "其他核心技术",
                "claims": [],
                "statements": [],
                "passages": {},
                "citations": set(),
                "first_index": index,
            },
        )
        group["claims"].append(claim_id)
        if statement not in group["statements"]:
            group["statements"].append(statement)
        group["citations"].update(winning_ids)
        for passage in winning:
            passage_key = str(passage.get("passage_id") or passage.get("canonical_url") or repr(sorted(passage.items())))
            group["passages"].setdefault(passage_key, passage)

    findings: list[TechnologyFinding] = []
    for key, group in sorted(groups.items(), key=lambda item: (item[1]["first_index"], item[0])):
        digest = hashlib.sha256(key.encode()).hexdigest()[:12]
        combined_statement = " | ".join(group["statements"][:3])
        finding = score_supported_finding(
            finding_id=f"finding:{digest}",
            claim_ids=tuple(dict.fromkeys(group["claims"])),
            statement=combined_statement,
            winning_passages=tuple(group["passages"].values()),
            winning_citation_ids=tuple(group["citations"]),
            as_of_date=as_of_date,
            entity=group["entity"],
            topic_kind=group["topic_kind"],
            topic_label=group["topic_label"],
        )
        findings.append(
            TechnologyFinding(
                finding_id=finding.finding_id,
                entity=finding.entity,
                topic_kind=finding.topic_kind,
                topic_label=finding.topic_label,
                claim_ids=finding.claim_ids,
                published_at=finding.published_at,
                recency_score=finding.recency_score,
                impact_score=finding.impact_score,
                maturity=finding.maturity,
                evidence_quality=finding.evidence_quality,
                winning_citation_ids=finding.winning_citation_ids,
                total_score=finding.total_score,
                statement=finding.statement,
                localized_statement=finding.localized_statement,
            )
        )
    findings.sort(
        key=lambda value: (
            -value.total_score,
            -value.evidence_quality,
            -value.recency_score,
            value.finding_id,
        )
    )
    return tuple(findings)


def finding_set_failure_codes(
    findings: Sequence[TechnologyFinding],
    *,
    minimum_count: int = 3,
    maximum_count: int = 8,
    minimum_topic_kinds: int = 3,
) -> tuple[str, ...]:
    """Return deterministic publish-gate failures for a technology finding set."""

    selected = tuple(findings)
    codes: list[str] = []
    if len(selected) < minimum_count:
        codes.append("technology_findings_below_minimum")
    if len(selected) > maximum_count:
        codes.append("technology_findings_above_maximum")
    if len({finding.finding_id for finding in selected}) != len(selected):
        codes.append("technology_findings_not_unique")
    entity_keys = [normalize_entity_key(finding.entity) for finding in selected]
    if any(not key for key in entity_keys) or len(set(entity_keys)) != len(entity_keys):
        codes.append("technology_entities_not_unique")
    if any(not finding.winning_citation_ids for finding in selected):
        codes.append("technology_finding_missing_citation")
    if selected and len({finding.total_score for finding in selected}) == 1:
        codes.append("technology_scores_indistinguishable")
    if selected and all(finding.recency_score == 0.0 for finding in selected):
        codes.append("technology_recency_unresolved")
    if selected and sum(finding.recency_score > 0.0 for finding in selected) * 5 < len(selected) * 3:
        codes.append("technology_recent_coverage_insufficient")
    if selected and all(finding.maturity == "unknown" for finding in selected):
        codes.append("technology_maturity_unresolved")
    if selected and len({finding.topic_kind for finding in selected}) < minimum_topic_kinds:
        codes.append("technology_taxonomy_coverage_insufficient")
    generic_markers = (
        "可由引用核验的进展",
        "出现了可由引用核验",
        "可由引用核验的能力与互操作性",
        "由引用支持的技术版本与能力",
        "可核验变化",
    )
    generic_localized = sum(
        any(
            marker in str(finding.localized_statement or "")
            for marker in generic_markers
        )
        for finding in selected
    )
    if selected and generic_localized:
        codes.append("technology_core_changes_generic")
    return tuple(codes)


__all__ = [
    "build_ranked_findings", "canonical_host", "classify_intent", "finding_set_failure_codes",
    "is_primary_source",
    "is_navigation_or_product_boilerplate", "plan_technology_topics", "recency_score",
    "normalize_entity_key", "score_supported_finding", "source_seeds",
    "supported_statement_dates",
]

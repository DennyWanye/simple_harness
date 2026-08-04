from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from deskpet.workflows.definitions.deep_research_v4_contracts import SourceSeed, TechnologyFinding
from deskpet.workflows.definitions.deep_research_v4_intelligence import (
    _entity_name,
    build_ranked_findings,
    classify_intent,
    finding_set_failure_codes,
    is_primary_source,
    is_navigation_or_product_boilerplate,
    plan_technology_topics,
    normalize_entity_key,
    recency_score,
    score_supported_finding,
    source_seeds,
    supported_statement_dates,
)
from deskpet.workflows.definitions.deep_research_v4_report import render_technology_report
from scripts.acceptance.deepresearch_report_quality import evaluate_report


AS_OF = date(2026, 7, 15)


def test_entity_extraction_merges_vllm_release_features_and_names_jetson_paper() -> None:
    vllm_passage = ({
        "title": "v0.25.1",
        "canonical_url": "https://github.com/vllm-project/vllm/releases",
    },)
    assert _entity_name(
        "vLLM Transformers modeling backend gained FP8 MoE support.",
        vllm_passage,
        None,
    ) == "vLLM"

    jetson_passage = ({
        "title": "Jetson-PI: Towards Onboard Real-Time Robot Control",
        "canonical_url": "https://arxiv.org/abs/2607.12659v1",
    },)
    assert _entity_name(
        "The cited method achieved 41x improvements in control frequency compared with naive PyTorch on NVIDIA Jetson Orin.",
        jetson_passage,
        None,
    ) == "Jetson-PI"


def test_frozen_intelligence_fixture_matches_classifier_and_taxonomy() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "deep_research_v4_intelligence.json").read_text(
            encoding="utf-8"
        )
    )
    as_of = date.fromisoformat(fixture["as_of_date"])
    case = fixture["technology_case"]
    profile = classify_intent(case["topic"], as_of_date=as_of)
    topics = plan_technology_topics(case["topic"], profile)
    assert (profile.window_start, profile.window_end) == (case["window_start"], case["window_end"])
    assert [topic.topic_kind for topic in topics] == case["topic_kinds"]
    assert all(classify_intent(topic, as_of_date=as_of).kind == "generic" for topic in fixture["generic_cases"])


@pytest.mark.parametrize(
    "topic",
    (
        "可以帮我调研一下，现在 AI 相关的最新最有价值的技术相关的信息吗？",
        "What are the latest valuable AI model and inference technology trends?",
        "近期值得关注的大模型开源基础设施技术趋势",
    ),
)
def test_wide_ai_technology_topics_are_classified_deterministically(topic: str) -> None:
    profile = classify_intent(topic, as_of_date=AS_OF)
    assert profile.kind == "technology_intelligence"
    assert profile.as_of_date == "2026-07-15"
    assert (profile.window_start, profile.window_end) == ("2026-01-16", "2026-07-15")


@pytest.mark.parametrize(
    "topic",
    (
        "AI 监管政策的最新变化",
        "人工智能行业应用商业案例",
        "History of transformer models",
        "最新手机产品推荐",
    ),
)
def test_non_technology_topics_remain_generic(topic: str) -> None:
    assert classify_intent(topic, as_of_date=AS_OF).kind == "generic"


def test_optional_dimensions_require_explicit_user_words() -> None:
    profile = classify_intent("AI 最新技术趋势与治理政策", as_of_date=AS_OF)
    assert profile.kind == "technology_intelligence"
    assert profile.requested_dimensions == ("regulation",)


@pytest.mark.parametrize(
    ("topic", "expected"),
    (
        ("AI 最新技术 过去 7 天", ("2026-07-08", "2026-07-15")),
        ("AI latest technology last 2 weeks", ("2026-07-01", "2026-07-15")),
        ("AI 最新技术 最近 3 个月", ("2026-04-16", "2026-07-15")),
        ("AI 最新技术 2025 年以来", ("2025-01-01", "2026-07-15")),
        ("AI 最新技术 2026-07-01..2026-08-01", ("2026-07-01", "2026-07-15")),
        ("AI 最新技术 过去 0 天", ("2026-01-16", "2026-07-15")),
        ("AI 最新技术 since 2030", ("2026-01-16", "2026-07-15")),
    ),
)
def test_time_grammar_is_bounded_and_future_clipped(topic: str, expected: tuple[str, str]) -> None:
    profile = classify_intent(topic, as_of_date=AS_OF)
    assert (profile.window_start, profile.window_end) == expected


def test_topics_and_source_seeds_are_short_stable_and_auditable() -> None:
    original = "可以帮我调研一下，现在 AI 相关的最新最有价值的技术相关的信息吗？"
    profile = classify_intent(original, as_of_date=AS_OF)
    topics = plan_technology_topics(original, profile)
    assert 3 <= len(topics) <= 5
    assert len({topic.topic_kind for topic in topics}) == len(topics)
    assert all(original not in topic.discovery_query for topic in topics)
    assert all(len(topic.source_seeds) == 3 for topic in topics)
    assert len({topic.scholarly_query for topic in topics}) == len(topics)
    assert all(topic.scholarly_query.isascii() for topic in topics)
    assert all(
        tuple(seed.seed_id for seed in topic.source_seeds)
        == tuple(sorted(seed.seed_id for seed in topic.source_seeds))
        for topic in topics
    )
    scholarly = [seed for seed in source_seeds("agent") if seed.query_kind == "scholarly"]
    assert [(seed.channel, seed.direct_source, seed.allowed_domains) for seed in scholarly] == [
        ("direct", "arxiv", ("arxiv.org",))
    ]


def test_generic_or_duplicate_llm_entities_fall_back_to_distinct_taxonomy_queries() -> None:
    original = "What are the latest valuable AI model and inference technology trends?"
    profile = classify_intent(original, as_of_date=AS_OF)
    proposed = [
        {"topic_kind": kind, "label": f"Bucket {index}", "entity": "AI"}
        for index, kind in enumerate(
            (
                "model_inference",
                "agent",
                "multimodal",
                "training_inference_system",
                "open_infrastructure",
            )
        )
    ]
    topics = plan_technology_topics(original, profile, proposed=proposed)
    assert len({topic.entity.casefold() for topic in topics}) == 5
    assert all(topic.entity.casefold() != "ai" for topic in topics)
    assert len({topic.discovery_query.casefold() for topic in topics}) == 5


def test_broad_topic_rejects_planner_invented_stale_version_anchors() -> None:
    original = "可以帮我调研一下，现在 AI 相关的最新最有价值的技术相关的信息吗？"
    profile = classify_intent(original, as_of_date=AS_OF)
    proposed = (
        {"topic_kind": "model_inference", "label": "Reasoning", "entity": "OpenAI o4-mini"},
        {"topic_kind": "agent", "label": "Agents", "entity": "LangGraph v1.0"},
        {"topic_kind": "multimodal", "label": "Multimodal", "entity": "Gemini 2.5 Pro"},
        {
            "topic_kind": "training_inference_system",
            "label": "Serving",
            "entity": "vLLM v0.10",
        },
        {
            "topic_kind": "open_infrastructure",
            "label": "Protocols",
            "entity": "Model Context Protocol 2026-03 specification",
        },
    )

    topics = plan_technology_topics(original, profile, proposed=proposed)

    assert [topic.entity for topic in topics] == [
        "vLLM",
        "Model Context Protocol",
        "Gemini multimodal",
        "PyTorch distributed training",
        "Hugging Face Transformers",
    ]
    assert [topic.label for topic in topics] == [
        "基础模型与推理",
        "Agent 与工具调用",
        "原生多模态",
        "训练与推理系统",
        "开源基础设施",
    ]


def test_broad_topic_rejects_ungrounded_non_version_planner_entities() -> None:
    original = "可以帮我调研一下，现在 AI 相关的最新最有价值的技术相关的信息吗？"
    profile = classify_intent(original, as_of_date=AS_OF)
    proposed = tuple(
        {
            "topic_kind": kind,
            "label": f"English bucket {index}",
            "entity": entity,
        }
        for index, (kind, entity) in enumerate((
            ("model_inference", "Edge LLM serving"),
            ("agent", "Autonomous coding agents"),
            ("multimodal", "Vision-language foundation models"),
            ("training_inference_system", "Distributed optimization frameworks"),
            ("open_infrastructure", "Open model collaboration hubs"),
        ))
    )

    topics = plan_technology_topics(original, profile, proposed=proposed)

    assert [topic.entity for topic in topics] == [
        "vLLM",
        "Model Context Protocol",
        "Gemini multimodal",
        "PyTorch distributed training",
        "Hugging Face Transformers",
    ]
    assert [topic.label for topic in topics] == [
        "基础模型与推理",
        "Agent 与工具调用",
        "原生多模态",
        "训练与推理系统",
        "开源基础设施",
    ]


def test_explicit_user_version_remains_a_valid_research_anchor() -> None:
    original = "请调研 LangGraph v1.0 最新的 agent 技术变化"
    profile = classify_intent(original, as_of_date=AS_OF)

    topics = plan_technology_topics(
        original,
        profile,
        proposed=(
            {"topic_kind": "agent", "label": "Agents", "entity": "LangGraph v1.0"},
        ),
    )

    assert topics[0].entity == "LangGraph v1.0"


@pytest.mark.parametrize("generic_entity", ("AI latest technology research", "AI最新技术调研"))
def test_generic_llm_entity_phrase_cannot_disguise_itself_as_a_specific_bucket(
    generic_entity: str,
) -> None:
    original = "What are the latest valuable AI model and inference technology trends?"
    profile = classify_intent(original, as_of_date=AS_OF)
    topics = plan_technology_topics(
        original,
        profile,
        proposed=(
            {
                "topic_kind": "model_inference",
                "label": "Model inference",
                "entity": generic_entity,
            },
        ),
    )
    assert topics[0].entity == "vLLM"


def test_direct_seed_without_explicit_backend_is_a_contract_error() -> None:
    with pytest.raises(ValueError, match="direct_source"):
        SourceSeed("bad", "agent", "direct", "scholarly", "{entity}", ("arxiv.org",))


def test_github_issue_is_not_treated_as_a_primary_technology_source() -> None:
    assert is_primary_source("https://github.com/owner/repository") is True
    assert is_primary_source("https://github.com/owner/repository/issues/123") is False


@pytest.mark.parametrize(
    ("published", "expected"),
    (
        ("2026-07-15", 1.0),
        ("2026-06-15", 1.0),
        ("2026-04-16", 0.8),
        ("2026-01-16", 0.6),
        ("2025-07-15", 0.3),
        ("2025-07-14", 0.0),
        ("", 0.0),
    ),
)
def test_recency_rubric_boundaries(published: str, expected: float) -> None:
    assert recency_score(published, as_of_date=AS_OF) == expected


def test_finding_scores_only_supported_passage_markers() -> None:
    finding = score_supported_finding(
        finding_id="f1",
        claim_ids=("c1",),
        statement="A runtime improvement was measured.",
        winning_passages=(
            {
                "text": "The stable GA release reduced benchmark latency by 2x in production.",
                "published_at": "2026-07-01",
                "url": "https://openai.com/research/a",
            },
            {
                "text": "An independent implementation reports support for the release.",
                "published_at": "2026-07-02",
                "url": "https://example.org/implementation",
            },
        ),
        winning_citation_ids=(1, 2),
        as_of_date=AS_OF,
    )
    assert finding.recency_score == 1.0
    assert finding.impact_score == 3
    assert finding.maturity == "adopted"
    assert finding.evidence_quality == 3
    assert finding.total_score == 1.0
    assert is_primary_source("https://sub.openai.com/post") is True
    assert is_primary_source("https://example.org/post") is False


def test_supported_claim_date_supplies_recency_when_page_metadata_is_missing() -> None:
    finding = score_supported_finding(
        finding_id="f-statement-date",
        claim_ids=("c1",),
        statement="gemini-omni-flash-preview entered public preview on June 30, 2026.",
        winning_passages=({
            "text": "gemini-omni-flash-preview entered public preview on June 30, 2026.",
            "url": "https://ai.google.dev/gemini-api/docs/changelog",
        },),
        winning_citation_ids=(1,),
        as_of_date=AS_OF,
    )
    assert finding.published_at == "2026-06-30"
    assert finding.recency_score == 1.0


@pytest.mark.parametrize(
    ("url", "text", "expected"),
    (
        ("https://arxiv.org/abs/2604.12213", "A new routing method is evaluated.", "experimental"),
        ("https://github.com/google-research/timesfm", "TimesFM supports forecasting.", "emerging"),
        ("https://www.anthropic.com/engineering/tool-use", "Tool use integrates IDE operations.", "emerging"),
    ),
)
def test_maturity_uses_source_lifecycle_when_claim_text_omits_release_wording(
    url: str, text: str, expected: str
) -> None:
    finding = score_supported_finding(
        finding_id="f-source-lifecycle",
        claim_ids=("c1",),
        statement=text,
        winning_passages=({"text": text, "published_at": "2026-07-01", "url": url},),
        winning_citation_ids=(1,),
        as_of_date=AS_OF,
    )
    assert finding.maturity == expected


def test_finding_sort_is_total_then_quality_then_recency_then_id() -> None:
    profile = classify_intent("AI 最新技术趋势", as_of_date=AS_OF)
    topic = plan_technology_topics("AI 最新技术趋势", profile)[0]
    claims = [
        {"claim_id": "c2", "text": "BetaRuntime supports model inference."},
        {"claim_id": "c1", "text": "AlphaRuntime supports model inference."},
    ]
    decisions = [
        {
            "claim_id": value["claim_id"],
            "supported": True,
            "winning_passage_ids": [f"p{index}"],
            "winning_source_citation_ids": [index],
        }
        for index, value in enumerate(claims, 1)
    ]
    passages = [
        {
            "passage_id": f"p{index}",
            "source_citation_id": index,
            "text": "The runtime supports model inference capability.",
            "question_id": topic.label,
        }
        for index in (1, 2)
    ]
    sources = [
        {
            "citation_id": index,
            "url": f"https://example{index}.org/item",
            "published_at": "2026-07-01",
        }
        for index in (1, 2)
    ]
    findings = build_ranked_findings(
        claims=claims,
        decisions=decisions,
        passages=passages,
        citation_sources=sources,
        as_of_date=AS_OF,
        technology_topics=(topic,),
        user_topic="AI 最新技术趋势",
    )
    assert len(findings) == 2
    assert [finding.finding_id for finding in findings] == sorted(
        (finding.finding_id for finding in findings)
    )
    assert {finding.published_at for finding in findings} == {"2026-07-01"}


def test_technology_report_leads_with_window_rubric_top_items_and_winning_citations() -> None:
    profile = classify_intent("AI 最新技术趋势", as_of_date=AS_OF)
    finding = TechnologyFinding(
        finding_id="f1",
        entity="TensorRT-Next",
        topic_kind="training_inference_system",
        topic_label="训练与推理系统",
        claim_ids=("c1",),
        published_at="2026-07-01",
        recency_score=1.0,
        impact_score=2,
        maturity="emerging",
        evidence_quality=3,
        winning_citation_ids=(1,),
        total_score=0.85,
        statement="A supported inference runtime release reduced benchmark latency by 2x.",
        localized_statement="TensorRT-Next 发布新推理运行时，基准延迟降低 2x。",
    )
    report = render_technology_report(
        topic="AI 最新技术趋势",
        profile=profile,
        findings=(finding, *(_gate_finding(index) for index in range(2, 6))),
        base_report={
            "schema_version": 3,
            "status": "completed",
            "citations": [
                {"citation_id": index, "title": f"Primary {index}", "url": f"https://openai.com/{index}"}
                for index in range(1, 6)
            ],
            "coverage": {"actual_requests": 2},
            "errors": [],
            "publish_decision": {"passed": True},
        },
    )
    markdown = report["report_md"]
    assert "情报窗口：2026-01-16 至 2026-07-15" in markdown
    assert "35% 近期性、30% 技术影响、20% 成熟度/可采用性、15% 证据质量" in markdown
    assert "## 一页式执行摘要" in markdown
    assert "### 关键结论与采用动作" in markdown
    assert "### 组合建议" in markdown
    assert "## 分主题 Top 技术" in markdown
    assert "## 方法与局限" in markdown
    assert "- 核心变化：" in markdown
    assert "TensorRT-Next 发布新推理运行时，基准延迟降低 2x。" in markdown
    assert "- 成熟度与采用建议：正在成熟" in markdown
    assert "[1]" in markdown
    assert "A supported inference runtime" not in markdown
    assert "## Coverage" not in markdown
    assert "provider_attempts" not in markdown
    assert report["schema_version"] == 4
    assert len(report["report_hash"]) == 64


def test_same_entity_is_merged_and_vertical_or_navigation_content_is_excluded() -> None:
    original = "AI 最新最有价值的技术趋势"
    profile = classify_intent(original, as_of_date=AS_OF)
    topic = plan_technology_topics(original, profile)[0]
    claims = [
        {"claim_id": "c1", "text": "HunyuanImage released a model inference update."},
        {"claim_id": "c2", "text": "HunyuanImage supports faster inference throughput."},
        {"claim_id": "c3", "text": "EHR healthcare model inference assists hospitals."},
        {"claim_id": "c4", "text": "Example workflows and tasks teams can take on with ChatGPT or Codex"},
        {"claim_id": "c5", "text": "Drug discovery molecule model inference supports pharma screening."},
        {"claim_id": "c6", "text": "Banking insurance agent framework automates finance workflows."},
    ]
    decisions = [
        {
            "claim_id": claim["claim_id"],
            "supported": True,
            "winning_passage_ids": [f"p{index}"],
            "winning_source_citation_ids": [index],
        }
        for index, claim in enumerate(claims, 1)
    ]
    passages = [
        {
            "passage_id": f"p{index}",
            "source_citation_id": index,
            "question_id": topic.label,
            "text": claim["text"],
        }
        for index, claim in enumerate(claims, 1)
    ]
    sources = [
        {
            "citation_id": index,
            "url": f"https://example{index}.org/source",
            "published_at": f"2026-07-0{index}",
        }
        for index in range(1, 7)
    ]

    findings = build_ranked_findings(
        claims=claims,
        decisions=decisions,
        passages=passages,
        citation_sources=sources,
        as_of_date=AS_OF,
        technology_topics=(topic,),
        user_topic=original,
    )

    assert len(findings) == 1
    assert findings[0].entity == "HunyuanImage"
    assert findings[0].claim_ids == ("c1", "c2")
    assert "released a model inference update" in findings[0].statement
    assert "supports faster inference throughput" in findings[0].statement
    assert findings[0].published_at == "2026-07-02"
    assert "EHR" not in findings[0].statement
    assert is_navigation_or_product_boilerplate(
        "Example workflows and tasks teams can take on with ChatGPT or Codex"
    )


def test_statement_subject_beats_document_brand_and_event_only_rows_are_excluded() -> None:
    original = "latest valuable AI technology"
    profile = classify_intent(original, as_of_date=AS_OF)
    topics = plan_technology_topics(original, profile)
    agent_topic = next(topic for topic in topics if topic.topic_kind == "agent")
    infrastructure_topic = next(topic for topic in topics if topic.topic_kind == "open_infrastructure")
    claims = (
        {"claim_id": "c1", "text": "Grok 4.1 Fast models can use agent tools."},
        {"claim_id": "c2", "text": "CNCC 2026 open source AI infrastructure forum was held in July."},
    )
    decisions = tuple({
        "claim_id": claim["claim_id"],
        "supported": True,
        "winning_passage_ids": [f"p{index}"],
        "winning_source_citation_ids": [index],
    } for index, claim in enumerate(claims, 1))
    passages = (
        {
            "passage_id": "p1", "source_citation_id": 1,
            "question_id": agent_topic.label, "text": claims[0]["text"],
        },
        {
            "passage_id": "p2", "source_citation_id": 2,
            "question_id": infrastructure_topic.label, "text": claims[1]["text"],
        },
    )
    sources = (
        {
            "citation_id": 1, "url": "https://docs.x.ai/developers/release-notes",
            "title": "Release Notes | SpaceXAI Docs", "published_at": "2026-07-01",
        },
        {
            "citation_id": 2, "url": "https://example.org/cncc-forum",
            "title": "CNCC forum", "published_at": "2026-07-02",
        },
    )

    findings = build_ranked_findings(
        claims=claims,
        decisions=decisions,
        passages=passages,
        citation_sources=sources,
        as_of_date=AS_OF,
        technology_topics=topics,
        user_topic=original,
    )

    assert [finding.entity for finding in findings] == ["Grok 4.1"]


def test_concrete_subjects_merge_while_generic_headlines_are_not_findings() -> None:
    original = "latest valuable AI technology"
    profile = classify_intent(original, as_of_date=AS_OF)
    topics = plan_technology_topics(original, profile)
    agent_topic = next(topic for topic in topics if topic.topic_kind == "agent")
    system_topic = next(topic for topic in topics if topic.topic_kind == "training_inference_system")
    claims = (
        {"claim_id": "c1", "text": "Microsoft Agent Framework is an open-source SDK and runtime."},
        {"claim_id": "c2", "text": "Microsoft Agent Framework unifies Semantic Kernel with AutoGen."},
        {"claim_id": "c3", "text": "AI training builds a model by adjusting billions of parameters."},
        {"claim_id": "c4", "text": "Meet Gemma 4 12B: an encoder-free multimodal model."},
        {"claim_id": "c5", "text": "Gemma 4 12B can natively ingest audio and video."},
    )
    decisions = tuple({
        "claim_id": claim["claim_id"], "supported": True,
        "winning_passage_ids": [f"p{index}"], "winning_source_citation_ids": [index],
    } for index, claim in enumerate(claims, 1))
    passages = tuple({
        "passage_id": f"p{index}", "source_citation_id": index,
        "question_id": agent_topic.label if index < 3 else system_topic.label if index == 3 else next(
            topic.label for topic in topics if topic.topic_kind == "multimodal"
        ),
        "text": claim["text"],
    } for index, claim in enumerate(claims, 1))
    sources = tuple({
        "citation_id": index,
        "url": "https://devblogs.microsoft.com/foundry/agent-framework" if index < 3 else "https://example.org/training" if index == 3 else "https://developers.googleblog.com/gemma-4",
        "title": "Introducing Microsoft Agent Framework: The Open-Source Engine" if index < 3 else "AI Training vs Inference" if index == 3 else "Gemma 4 12B: The Developer Guide",
        "published_at": "2026-07-01",
    } for index in range(1, 6))

    findings = build_ranked_findings(
        claims=claims, decisions=decisions, passages=passages, citation_sources=sources,
        as_of_date=AS_OF, technology_topics=topics, user_topic=original,
    )

    assert {finding.entity for finding in findings} == {"Microsoft Agent Framework", "Gemma 4 12B"}
    microsoft = next(finding for finding in findings if finding.entity == "Microsoft Agent Framework")
    gemma = next(finding for finding in findings if finding.entity == "Gemma 4 12B")
    assert microsoft.claim_ids == ("c1", "c2")
    assert gemma.claim_ids == ("c4", "c5")


@pytest.mark.parametrize(
    ("statement", "topic_kind", "expected"),
    (
        ("OpenAI released GPT-5 with improved model inference.", "model_inference", "OpenAI GPT-5"),
        ("NVIDIA introduced Dynamo for GPU inference serving.", "training_inference_system", "NVIDIA Dynamo"),
        ("Google launched Gemini 3 with multimodal video support.", "multimodal", "Google Gemini 3"),
        ("Microsoft released Agent Framework for agent tool calling.", "agent", "Microsoft Agent Framework"),
    ),
)
def test_vendor_release_subject_extracts_the_product(
    statement: str, topic_kind: str, expected: str,
) -> None:
    original = "latest valuable AI technology"
    profile = classify_intent(original, as_of_date=AS_OF)
    topics = plan_technology_topics(original, profile)
    topic = next(item for item in topics if item.topic_kind == topic_kind)
    findings = build_ranked_findings(
        claims=({"claim_id": "c1", "text": statement},),
        decisions=({
            "claim_id": "c1", "supported": True, "winning_passage_ids": ["p1"],
            "winning_source_citation_ids": [1],
        },),
        passages=({
            "passage_id": "p1", "source_citation_id": 1,
            "question_id": topic.label, "text": statement,
        },),
        citation_sources=({
            "citation_id": 1, "url": "https://example.org/release", "published_at": "2026-07-01",
        },),
        as_of_date=AS_OF, technology_topics=topics, user_topic=original,
    )
    assert [finding.entity for finding in findings] == [expected]


def test_possessive_project_change_is_attributed_to_project_and_date_only_rc_is_removed() -> None:
    original = "latest valuable AI technology"
    profile = classify_intent(original, as_of_date=AS_OF)
    topics = plan_technology_topics(original, profile)
    system = next(item for item in topics if item.topic_kind == "training_inference_system")
    agent = next(item for item in topics if item.topic_kind == "agent")
    statements = (
        "Torchtitan’s graph_trainer added a graph-based CPU activation-offloading pass.",
        "The MCP 2026-07-28 RC was dated 2026-05-29.",
    )
    findings = build_ranked_findings(
        claims=tuple({"claim_id": f"c{i}", "text": text} for i, text in enumerate(statements, 1)),
        decisions=tuple({
            "claim_id": f"c{i}", "supported": True,
            "winning_passage_ids": [f"p{i}"], "winning_source_citation_ids": [i],
        } for i in range(1, 3)),
        passages=(
            {"passage_id": "p1", "source_citation_id": 1, "question_id": system.label, "text": statements[0]},
            {"passage_id": "p2", "source_citation_id": 2, "question_id": agent.label, "text": statements[1]},
        ),
        citation_sources=tuple({
            "citation_id": i, "url": f"https://example.org/{i}", "published_at": "2026-07-10",
        } for i in range(1, 3)),
        as_of_date=AS_OF, technology_topics=topics, user_topic=original,
    )

    assert [finding.entity for finding in findings] == ["Torchtitan"]


def test_month_year_in_supported_statement_is_available_for_window_filtering() -> None:
    assert supported_statement_dates(
        "MCP’s current spec release came out in November 2025.",
        as_of_date=AS_OF,
    ) == (date(2025, 11, 1),)


def test_as_of_capability_snapshot_is_not_a_technology_change() -> None:
    original = "latest valuable AI technology"
    profile = classify_intent(original, as_of_date=AS_OF)
    topics = plan_technology_topics(original, profile)
    multimodal = next(item for item in topics if item.topic_kind == "multimodal")
    statement = "Gemini 3.1 Pro featured a 1M-token context window as of February 2026."
    findings = build_ranked_findings(
        claims=({"claim_id": "c1", "text": statement},),
        decisions=({
            "claim_id": "c1", "supported": True,
            "winning_passage_ids": ["p1"], "winning_source_citation_ids": [1],
        },),
        passages=({
            "passage_id": "p1", "source_citation_id": 1,
            "question_id": multimodal.label, "text": statement,
        },),
        citation_sources=({
            "citation_id": 1, "url": "https://example.org/gemini", "published_at": "2026-02-10",
        },),
        as_of_date=AS_OF, technology_topics=topics, user_topic=original,
    )

    assert findings == ()


def test_product_pair_and_mixed_chinese_subjects_are_specific_and_low_value_copy_is_removed() -> None:
    original = "latest valuable AI technology"
    profile = classify_intent(original, as_of_date=AS_OF)
    topics = plan_technology_topics(original, profile)
    multimodal = next(item for item in topics if item.topic_kind == "multimodal")
    agent = next(item for item in topics if item.topic_kind == "agent")
    infrastructure = next(item for item in topics if item.topic_kind == "open_infrastructure")
    claims = (
        {"claim_id": "c1", "text": "Llama 4 Scout and Llama 4 Maverick are open-weight natively multimodal models."},
        {"claim_id": "c2", "text": "Llama 4 Scout and Llama 4 Maverick use a mixture-of-experts architecture."},
        {"claim_id": "c3", "text": "方舟 Agent Plan was released on 2026-05-11."},
        {"claim_id": "c4", "text": "AFP 积分制，Medium 档（¥200）及以上赠送 7×24 在线智能伙伴。"},
        {"claim_id": "c5", "text": "Google Open Source Blog provides the latest news from Google on open source releases, major projects, events, and outreach programs."},
    )
    question_by_index = {
        1: multimodal.label, 2: multimodal.label, 3: agent.label,
        4: agent.label, 5: infrastructure.label,
    }
    decisions = tuple({
        "claim_id": claim["claim_id"], "supported": True,
        "winning_passage_ids": [f"p{index}"], "winning_source_citation_ids": [index],
    } for index, claim in enumerate(claims, 1))
    passages = tuple({
        "passage_id": f"p{index}", "source_citation_id": index,
        "question_id": question_by_index[index], "text": claim["text"],
    } for index, claim in enumerate(claims, 1))
    sources = tuple({
        "citation_id": index, "url": f"https://example{index}.org/release",
        "title": "AI technology release", "published_at": "2026-07-01",
    } for index in range(1, 6))

    findings = build_ranked_findings(
        claims=claims, decisions=decisions, passages=passages,
        citation_sources=sources, as_of_date=AS_OF,
        technology_topics=topics, user_topic=original,
    )

    assert {finding.entity for finding in findings} == {"Llama 4 Scout/Maverick"}
    llama = next(finding for finding in findings if finding.entity == "Llama 4 Scout/Maverick")
    assert llama.claim_ids == ("c1", "c2")


def test_metric_subject_is_product_and_rankings_or_definitions_are_not_changes() -> None:
    original = "latest valuable AI technology"
    profile = classify_intent(original, as_of_date=AS_OF)
    topics = plan_technology_topics(original, profile)
    system = next(item for item in topics if item.topic_kind == "training_inference_system")
    statements = (
        "THInfer hand-optimized FP16 kernels achieve 2.4x higher inference throughput.",
        "PyTorch 2.11 includes Differentiable Collectives for Distributed Training.",
        "GPT-4o ranks #2 on the multimodal leaderboard.",
        "DeepSpeed is an open-source framework for distributed model training.",
    )
    findings = build_ranked_findings(
        claims=tuple({"claim_id": f"c{i}", "text": text} for i, text in enumerate(statements, 1)),
        decisions=tuple({
            "claim_id": f"c{i}", "supported": True,
            "winning_passage_ids": [f"p{i}"], "winning_source_citation_ids": [i],
        } for i in range(1, 5)),
        passages=tuple({
            "passage_id": f"p{i}", "source_citation_id": i,
            "question_id": system.label, "text": text,
        } for i, text in enumerate(statements, 1)),
        citation_sources=tuple({
            "citation_id": i, "url": f"https://example.org/{i}",
            "published_at": "2026-07-10",
        } for i in range(1, 5)),
        as_of_date=AS_OF,
        technology_topics=topics,
        user_topic=original,
    )
    assert {finding.entity for finding in findings} == {"THInfer", "PyTorch 2.11"}


def test_metadata_and_ecosystem_statements_are_not_publishable_technology_changes() -> None:
    original = "latest valuable AI technology"
    profile = classify_intent(original, as_of_date=AS_OF)
    topics = plan_technology_topics(original, profile)
    agent = next(item for item in topics if item.topic_kind == "agent")
    statements = (
        "MCP 2026-07-28 RC was released on 2026-05-29.",
        "MCP runs in production at companies large and small.",
        "MCP had over 97 million monthly SDK downloads by early 2026.",
        "We trace this to context dilution in a growing agent context window.",
    )
    findings = build_ranked_findings(
        claims=tuple({"claim_id": f"m{i}", "text": text} for i, text in enumerate(statements, 1)),
        decisions=tuple({
            "claim_id": f"m{i}", "supported": True,
            "winning_passage_ids": [f"mp{i}"], "winning_source_citation_ids": [i],
        } for i in range(1, 5)),
        passages=tuple({
            "passage_id": f"mp{i}", "source_citation_id": i,
            "question_id": agent.label, "text": text,
        } for i, text in enumerate(statements, 1)),
        citation_sources=tuple({
            "citation_id": i, "url": f"https://example.org/m{i}",
            "published_at": "2026-07-10",
        } for i in range(1, 5)),
        as_of_date=AS_OF,
        technology_topics=topics,
        user_topic=original,
    )
    assert findings == ()


def test_release_metadata_and_malformed_benchmark_fragments_do_not_displace_real_changes() -> None:
    original = "latest valuable AI technology"
    profile = classify_intent(original, as_of_date=AS_OF)
    topics = plan_technology_topics(original, profile)
    topic_by_kind = {item.topic_kind: item for item in topics}
    statements = (
        ("agent", "MCP 2026-07-28 RC marked the release candidate revision of the Model Context Protocol on 2026-05-29."),
        ("agent", "The Model Context Protocol team locked the release candidate for the 2026-07-28 specification on May 21, 2026."),
        ("training_inference_system", "nvidia-smi dmon -s u captures GPU telemetry during a run."),
        ("multimodal", "1 Pro features a 1M-token context window."),
        ("multimodal", "1 Pro: The April 2026 Benchmark Breakdown compared top frontier AI models across reasoning, coding, writing."),
        ("agent", "MCP 2026-07-28 RC was released as a release candidate on 2026-05-29."),
        ("open_infrastructure", "Kimi K2.5 is open-source."),
        ("open_infrastructure", "Kimi K2.5 is an open-source, native multimodal agentic model."),
        ("agent", "MCP uses a JSON-RPC-based protocol over Streamable HTTP."),
        ("training_inference_system", "Capture GPU telemetry with nvidia-smi dmon -s u (or DCGM) during the run."),
        ("model_inference", "Llama-4 Maverick 120B is a Meta model."),
        ("training_inference_system", "Dynamo is the orchestration layer above inference engines."),
        ("training_inference_system", "PyTorch 2.11 added differentiable collectives for distributed training."),
    )
    claims = tuple(
        {"claim_id": f"c{index}", "text": statement}
        for index, (_, statement) in enumerate(statements, 1)
    )
    findings = build_ranked_findings(
        claims=claims,
        decisions=tuple({
            "claim_id": f"c{index}", "supported": True,
            "winning_passage_ids": [f"p{index}"],
            "winning_source_citation_ids": [index],
        } for index in range(1, len(statements) + 1)),
        passages=tuple({
            "passage_id": f"p{index}", "source_citation_id": index,
            "question_id": topic_by_kind[kind].label, "text": statement,
        } for index, (kind, statement) in enumerate(statements, 1)),
        citation_sources=tuple({
            "citation_id": index, "url": f"https://example.org/{index}",
            "published_at": "2026-07-10",
        } for index in range(1, len(statements) + 1)),
        as_of_date=AS_OF,
        technology_topics=topics,
        user_topic=original,
    )

    assert [finding.entity for finding in findings] == ["PyTorch 2.11"]


def test_official_release_context_keeps_supported_model_and_protocol_releases() -> None:
    original = "latest valuable AI technology"
    profile = classify_intent(original, as_of_date=AS_OF)
    topics = plan_technology_topics(original, profile)
    topic_by_kind = {item.topic_kind: item for item in topics}
    statements = (
        ("open_infrastructure", "Kimi K2.5 is an open-source, native multimodal agentic model."),
        ("agent", "The Model Context Protocol release candidate 2026-07-28 revision was released on 2026-05-29."),
    )
    findings = build_ranked_findings(
        claims=tuple(
            {"claim_id": f"official-{index}", "text": statement}
            for index, (_, statement) in enumerate(statements, 1)
        ),
        decisions=tuple({
            "claim_id": f"official-{index}", "supported": True,
            "winning_passage_ids": [f"official-p{index}"],
            "winning_source_citation_ids": [index],
        } for index in range(1, 3)),
        passages=tuple({
            "passage_id": f"official-p{index}", "source_citation_id": index,
            "question_id": topic_by_kind[kind].label, "text": statement,
        } for index, (kind, statement) in enumerate(statements, 1)),
        citation_sources=(
            {
                "citation_id": 1,
                "title": "Patch release v5.13.1",
                "url": "https://github.com/huggingface/transformers/releases",
            },
            {
                "citation_id": 2,
                "title": "MCP 2026-07-28 RC",
                "url": "https://github.com/modelcontextprotocol/modelcontextprotocol/releases",
            },
        ),
        as_of_date=AS_OF,
        technology_topics=topics,
        user_topic=original,
    )

    assert {finding.entity for finding in findings} == {"Kimi K2.5", "MCP"}


def _gate_finding(
    index: int,
    *,
    entity: str | None = None,
    topic_kind: str | None = None,
    citations: tuple[int, ...] | None = None,
    recency: float | None = None,
    maturity: str | None = None,
    score: float | None = None,
) -> TechnologyFinding:
    return TechnologyFinding(
        finding_id=f"gate-{index}",
        entity=entity or f"Entity{index}",
        topic_kind=topic_kind or ("model_inference", "agent", "multimodal")[index % 3],
        topic_label=f"Theme {index % 3}",
        claim_ids=(f"c{index}",),
        published_at=f"2026-07-{index:02d}" if recency != 0.0 else None,
        recency_score=1.0 if recency is None else recency,
        impact_score=1 + index % 3,
        maturity=("adopted" if index == 1 else "emerging") if maturity is None else maturity,  # type: ignore[arg-type]
        evidence_quality=2 + index % 2,
        winning_citation_ids=(index,) if citations is None else citations,
        total_score=(0.45 + index * 0.05) if score is None else score,
        statement=f"Entity{index} released supported technology capability {index}.",
    )


def test_entity_normalization_merges_vendor_version_and_sdk_variants_without_collapsing_names() -> None:
    assert normalize_entity_key("OpenAI LangChain SDK v2.1") == normalize_entity_key("LangChain framework")
    assert normalize_entity_key("Google HunyuanImage-2.0") == normalize_entity_key("Hunyuan Image")
    assert normalize_entity_key("open-webui") != normalize_entity_key("open-interpreter")
    assert normalize_entity_key("通义千问模型") != normalize_entity_key("腾讯混元模型")
    assert normalize_entity_key("Microsoft Agent Framework v1") == normalize_entity_key("Agent Framework 1.0")
    assert normalize_entity_key("OpenAI Agents SDK") != normalize_entity_key("Microsoft Agent Framework")


def test_finding_set_gate_rejects_each_indistinguishable_or_incomplete_shape() -> None:
    healthy = tuple(_gate_finding(index) for index in range(1, 6))
    assert finding_set_failure_codes(healthy) == ()
    assert "technology_findings_below_minimum" in finding_set_failure_codes(healthy[:2])
    assert "technology_findings_above_maximum" in finding_set_failure_codes(
        tuple(_gate_finding(index) for index in range(1, 10))
    )
    assert "technology_finding_missing_citation" in finding_set_failure_codes(
        (*healthy[:4], _gate_finding(5, citations=()))
    )
    assert "technology_entities_not_unique" in finding_set_failure_codes(
        (*healthy[:4], _gate_finding(5, entity="OpenAI Entity1 SDK v2"))
    )
    assert "technology_scores_indistinguishable" in finding_set_failure_codes(
        tuple(_gate_finding(index, score=0.5) for index in range(1, 6))
    )
    assert "technology_recency_unresolved" in finding_set_failure_codes(
        tuple(_gate_finding(index, recency=0.0) for index in range(1, 6))
    )
    assert "technology_recent_coverage_insufficient" in finding_set_failure_codes(
        tuple(
            _gate_finding(index, recency=1.0 if index <= 2 else 0.0)
            for index in range(1, 6)
        )
    )
    assert "technology_maturity_unresolved" in finding_set_failure_codes(
        tuple(_gate_finding(index, maturity="unknown") for index in range(1, 6))
    )
    assert "technology_taxonomy_coverage_insufficient" in finding_set_failure_codes(
        tuple(_gate_finding(index, topic_kind="agent") for index in range(1, 6))
    )
    generic = list(healthy)
    generic[0] = replace(
        generic[0],
        localized_statement="Entity1 在智能体能力上出现了可由引用核验的进展。",
    )
    assert "technology_core_changes_generic" in finding_set_failure_codes(tuple(generic))
    generic[0] = replace(
        generic[0],
        localized_statement="Entity1 新增或扩展了可由引用核验的能力与互操作性。",
    )
    assert "technology_core_changes_generic" in finding_set_failure_codes(tuple(generic))


def test_explicit_industry_application_keeps_relevant_vertical_evidence() -> None:
    user_topic = "AI 最新技术 行业应用：医疗 EHR 模型推理"
    profile = classify_intent(user_topic, as_of_date=AS_OF)
    topic = plan_technology_topics(user_topic, profile)[0]
    findings = build_ranked_findings(
        claims=({"claim_id": "c1", "text": "MedRuntime released EHR model inference support for hospitals."},),
        decisions=({
            "claim_id": "c1", "supported": True, "winning_passage_ids": ["p1"],
            "winning_source_citation_ids": [1],
        },),
        passages=({
            "passage_id": "p1", "source_citation_id": 1, "question_id": topic.label,
            "text": "MedRuntime released EHR model inference support for hospitals.",
        },),
        citation_sources=({
            "citation_id": 1, "url": "https://example.org/med-runtime", "published_at": "2026-07-01",
        },),
        as_of_date=AS_OF,
        technology_topics=(topic,),
        user_topic=user_topic,
    )
    assert len(findings) == 1


def test_professional_report_matches_structured_golden_and_has_differentiated_scores() -> None:
    golden = json.loads(
        (Path(__file__).parent / "fixtures" / "deep_research_v4_report_golden.json").read_text(
            encoding="utf-8"
        )
    )
    profile = classify_intent("AI 最新最有价值的技术趋势", as_of_date=AS_OF)
    labels = (
        ("model_inference", "基础模型与推理"),
        ("agent", "Agent 与工具调用"),
        ("multimodal", "原生多模态"),
        ("training_inference_system", "训练与推理系统"),
        ("open_infrastructure", "开源基础设施"),
    )
    scores = (0.91, 0.77, 0.63, 0.49, 0.36)
    findings = tuple(
        TechnologyFinding(
            finding_id=f"f{index}",
            entity=f"TechEntity{index}",
            topic_kind=kind,
            topic_label=label,
            claim_ids=(f"c{index}",),
            published_at=f"2026-07-{index:02d}",
            recency_score=1.0 if index <= 2 else 0.8,
            impact_score=3 if index == 1 else 2 if index <= 3 else 1,
            maturity="adopted" if index == 1 else "emerging" if index <= 3 else "experimental",
            evidence_quality=3 if index <= 3 else 2,
            winning_citation_ids=(index,),
            total_score=score,
            statement=f"ENGLISH_DEBUG_STATEMENT_{index}",
            localized_statement=(
                f"TechEntity{index} 在{label}方向完成了第 {index} 项可核验能力更新，"
                "并提供了可追溯的技术证据。"
            ),
        )
        for index, ((kind, label), score) in enumerate(zip(labels, scores), 1)
    )
    report = render_technology_report(
        topic="AI 最新最有价值的技术趋势",
        profile=profile,
        findings=findings,
        base_report={
            "status": "completed",
            "coverage": {
                "provider_attempts": [{"provider": "debug"}],
                "published_claims": ["debug"],
            },
            "citations": [
                {"citation_id": index, "title": f"Source {index}", "url": f"https://source{index}.example"}
                for index in range(1, 6)
            ],
        },
    )
    markdown = report["report_md"]
    for section in golden["required_sections"]:
        assert section in markdown
    for field in golden["required_item_fields"]:
        assert markdown.count(field) == 5
    for fragment in golden["forbidden_fragments"]:
        assert fragment not in markdown
    quality = evaluate_report(markdown)
    assert quality["passed"] is True, quality
    assert "ENGLISH_DEBUG_STATEMENT" not in markdown
    assert len({finding.total_score for finding in findings}) == 5
    assert all(f"| {score:.3f} |" in markdown for score in scores)
    assert markdown.count("- 核心变化：") == 5
    assert set(report) == {
        "schema_version", "status", "reason_code", "topic", "report_md", "body_md",
        "intent_profile", "public_findings", "citation_count", "report_hash",
    }
    assert all("statement" not in item and "claim_ids" not in item for item in report["public_findings"])


def test_behavior_tree_acronym_uses_descriptive_source_title_as_entity() -> None:
    user_topic = "AI 最新最有价值的技术趋势"
    profile = classify_intent(user_topic, as_of_date=AS_OF)
    topic = next(
        item for item in plan_technology_topics(user_topic, profile)
        if item.topic_kind == "agent"
    )
    findings = build_ranked_findings(
        claims=({
            "claim_id": "c-bt",
            "text": "BT synthesis architecture enables coding agents to query MCP tools.",
        },),
        decisions=({
            "claim_id": "c-bt",
            "supported": True,
            "winning_passage_ids": ["p-bt"],
            "winning_source_citation_ids": [1],
        },),
        passages=({
            "passage_id": "p-bt",
            "source_citation_id": 1,
            "question_id": topic.label,
            "text": "BT synthesis architecture enables coding agents to query MCP tools.",
        },),
        citation_sources=({
            "citation_id": 1,
            "title": "Contract-Grounded Behavior Tree Synthesis via Coding Agents",
            "url": "https://arxiv.org/abs/2607.12220v1",
            "published_at": "2026-07-13",
        },),
        as_of_date=AS_OF,
        technology_topics=(topic,),
        user_topic=user_topic,
    )

    assert len(findings) == 1
    assert findings[0].entity == "Contract-Grounded Behavior Tree Synthesis"


def test_vllm_model_runner_v2_alias_is_merged_with_bare_v2_fragment() -> None:
    user_topic = "AI 最新最有价值的技术趋势"
    profile = classify_intent(user_topic, as_of_date=AS_OF)
    topic = next(
        item for item in plan_technology_topics(user_topic, profile)
        if item.topic_kind == "model_inference"
    )
    statements = (
        "Model Runner V2 introduced the default model inference execution path for all dense models.",
        "V2 introduced the default model inference execution path for all dense models in Model Runner V2.",
    )
    findings = build_ranked_findings(
        claims=tuple(
            {"claim_id": f"c{index}", "text": statement}
            for index, statement in enumerate(statements, 1)
        ),
        decisions=tuple({
            "claim_id": f"c{index}",
            "supported": True,
            "winning_passage_ids": [f"p{index}"],
            "winning_source_citation_ids": [1],
        } for index in range(1, 3)),
        passages=tuple({
            "passage_id": f"p{index}",
            "source_citation_id": 1,
            "question_id": topic.label,
            "text": statement,
        } for index, statement in enumerate(statements, 1)),
        citation_sources=({
            "citation_id": 1,
            "title": "v0.25.1",
            "url": "https://github.com/vllm-project/vllm/releases",
            "published_at": "2026-07-14",
        },),
        as_of_date=AS_OF,
        technology_topics=(topic,),
        user_topic=user_topic,
    )

    assert len(findings) == 1
    assert findings[0].entity == "vLLM Model Runner V2"
    assert findings[0].claim_ids == ("c1", "c2")


def test_torchcomms_backend_claims_merge_under_backend_entity() -> None:
    user_topic = "AI 最新最有价值的技术趋势"
    profile = classify_intent(user_topic, as_of_date=AS_OF)
    topic = next(
        item for item in plan_technology_topics(user_topic, profile)
        if item.topic_kind == "training_inference_system"
    )
    statements = (
        "torchcomms is a new communications backend for PyTorch Distributed.",
        "torchcomms, a new communications backend for PyTorch Distributed, improves scalability for large-cluster training.",
    )
    findings = build_ranked_findings(
        claims=tuple(
            {"claim_id": f"c{index}", "text": statement}
            for index, statement in enumerate(statements, 1)
        ),
        decisions=tuple({
            "claim_id": f"c{index}", "supported": True,
            "winning_passage_ids": [f"p{index}"],
            "winning_source_citation_ids": [1],
        } for index in range(1, 3)),
        passages=tuple({
            "passage_id": f"p{index}", "source_citation_id": 1,
            "question_id": topic.label, "text": statement,
        } for index, statement in enumerate(statements, 1)),
        citation_sources=({
            "citation_id": 1,
            "title": "PyTorch 2.13.0 Release",
            "url": "https://github.com/pytorch/pytorch/releases",
            "published_at": "2026-07-08",
        },),
        as_of_date=AS_OF,
        technology_topics=(topic,),
        user_topic=user_topic,
    )

    assert len(findings) == 1
    assert findings[0].entity == "torchcomms"
    assert findings[0].claim_ids == ("c2",)

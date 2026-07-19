from __future__ import annotations

from dataclasses import replace

from deskpet.workflows.definitions.deep_research_v3_contracts import AtomicClaim, EvidencePassage
from deskpet.workflows.definitions.deep_research_v3_quality import (
    evaluate_support,
    fallback_claims,
    make_publish_decision,
    parse_structured_claims,
    repair_and_prune,
    select_evidence_passages,
)
from deskpet.workflows.definitions.deep_research_v3_report import render_body, render_report


def _passage(source: int, text: str, *, domain: str = "example.com") -> EvidencePassage:
    return EvidencePassage(
        passage_id=f"passage-{source}",
        source_citation_id=source,
        source_content_hash=f"hash-{source}",
        canonical_url=f"https://{domain}/source-{source}",
        question_id="question",
        text=text,
        start=0,
        end=len(text),
        relevance=1.0,
    )


def _claim(index: int, text: str, *citations: int) -> AtomicClaim:
    return AtomicClaim(f"claim-{index}", text, "factual", tuple(citations))


def test_passage_selector_finds_relevant_fact_in_middle_of_long_document():
    prefix = "Unrelated introduction material. " * 120
    marker = "Python 3.14 free threaded mode changes the default runtime behavior in this documented build."
    suffix = "Unrelated appendix material. " * 120
    text = f"{prefix}\n\n{marker}\n\n{suffix}"
    passages = select_evidence_passages(
        [{"text": text, "content_hash": "content", "url": "https://python.org/doc", "question": "Python 3.14 free threaded mode"}],
        topic="Python 3.14 free threaded mode",
        maximum=2,
    )
    assert any(marker in value.text for value in passages)
    assert all(value.passage_id for value in passages)


def test_technology_passage_selection_prefers_concrete_change_over_page_heading():
    document = {
        "url": "https://example.test/runtime",
        "canonical_url": "https://example.test/runtime",
        "content_hash": "tech-change",
        "title": "Q2 2026 inference benchmark",
        "question": "大模型推理优化与高效部署",
        "text": (
            "Last Updated: April 29, 2026. Living Benchmark — Updated Quarterly.\n\n"
            "RuntimeX 2.0 introduced paged KV cache reuse and reduced benchmark latency by 35%. "
            "The release supports production serving on H200 GPUs."
        ),
    }

    passages = select_evidence_passages(
        [document],
        topic="大模型推理优化与高效部署",
        maximum=1,
        per_source=2,
        prefer_technology_changes=True,
    )

    assert len(passages) == 1
    assert "RuntimeX 2.0" in passages[0].text
    assert "35%" in passages[0].text


def test_passage_selector_covers_distinct_sources_before_second_passage_from_one_source():
    documents = []
    for source in range(1, 4):
        documents.append({
            "text": (
                f"AI agent source {source} documents a primary implementation capability and measured result.\n\n"
                f"AI agent source {source} also documents a second implementation detail and compatibility result."
            ),
            "content_hash": f"content-{source}",
            "url": f"https://source-{source}.example/research",
            "question": "AI agent implementation result",
        })

    passages = select_evidence_passages(
        documents,
        topic="AI agent implementation result",
        maximum=3,
        per_source=2,
    )

    assert len(passages) == 3
    assert {value.source_citation_id for value in passages} == {1, 2, 3}


def test_passage_selector_skips_page_chrome_and_publication_metadata_for_real_content():
    passages = select_evidence_passages(
        [
            {
                "title": "A New Multimodal Agent Architecture",
                "text": (
                    "[Submitted on 14 Apr 2026] Title:A New Multimodal Agent Architecture\n\n"
                    "Title:A New Multimodal Agent Architecture View PDF Abstract:"
                    "The architecture routes image, audio, and text inputs through modality-specific expert modules."
                ),
                "url": "https://arxiv.org/abs/example",
            },
            {
                "title": "Agent Runtime",
                "text": (
                    "Skip to content Navigation Menu Sign in Appearance settings Platform AI CODE CREATION\n\n"
                    "The runtime persists tool state across agent turns and resumes interrupted tasks from checkpoints."
                ),
                "url": "https://github.com/example/runtime",
            },
        ],
        topic="multimodal agent runtime architecture",
        maximum=2,
        per_source=2,
    )

    assert len(passages) == 2
    assert {value.source_citation_id for value in passages} == {1, 2}
    assert all("Submitted on" not in value.text for value in passages)
    assert all("Navigation Menu" not in value.text for value in passages)
    assert any("modality-specific expert modules" in value.text for value in passages)


def test_extractive_fallback_covers_sources_before_filling_body_with_more_claims():
    passages = []
    for source in range(1, 9):
        passages.extend([
            _passage(source, f"Source {source} establishes the first independently verifiable implementation fact."),
            replace(
                _passage(source, f"Source {source} documents a second independently verifiable compatibility fact."),
                passage_id=f"passage-{source}-second",
                start=100,
                end=180,
            ),
        ])

    claims = fallback_claims(passages, maximum=16)

    assert len(claims) == 16
    assert {claim.citation_ids[0] for claim in claims[:8]} == set(range(1, 9))
    assert len({claim.claim_id for claim in claims}) == 16


def test_extractive_fallback_merges_mirrored_fact_and_retains_each_supporting_url():
    fact = "The Interim Measures took effect on 15 August 2023."
    passages = [
        _passage(1, fact, domain="cac.gov.cn"),
        _passage(2, fact, domain="gov.cn"),
    ]

    claims = fallback_claims(passages)
    decisions = evaluate_support(claims, passages)

    assert len(claims) == 1
    assert claims[0].citation_ids == (1, 2)
    assert decisions[0].winning_source_citation_ids == (1, 2)


def test_numeric_fact_must_be_supported_by_one_winning_passage():
    claim = _claim(1, "Revenue reached $12 million in 2026.", 1, 2)
    passages = [
        _passage(1, "Revenue reached $10 million in 2026."),
        _passage(2, "Revenue reached $12 million in 2025."),
    ]
    decision = evaluate_support([claim], passages)[0]
    assert decision.supported is False
    assert decision.winning_passage_ids == ()
    assert decision.reason_codes == ("missing_exact_token",)


def test_structured_synthesis_splits_compound_factual_claims_before_support():
    claims = parse_structured_claims(
        '{"claims":[{"text":"Version 3.14 is documented; version 3.13 remains supported",'
        '"kind":"factual","citation_ids":[1]}]}',
        valid_citation_ids={1},
    )
    assert [value.text for value in claims] == [
        "Version 3.14 is documented", "version 3.13 remains supported",
    ]


def test_repair_is_extract_only_once_and_pruning_does_not_change_denominator():
    claim = _claim(1, "Revenue reached $12 million in 2026; an unrelated product doubled in 2027.", 1)
    passage = _passage(1, "The audited report states that revenue reached $12 million in 2026.")
    published, decisions, repaired, discarded = repair_and_prune([claim], [passage])
    assert repaired == 1 and discarded == 0
    assert len(published) == 1 and published[0].repaired_from == claim.claim_id
    assert published[0].text == "Revenue reached $12 million in 2026"
    assert decisions[0].supported is True
    body = render_body(topic="topic", published_claims=published, limitations=())
    decision = make_publish_decision(
        original_claims=[claim],
        published_claims=published,
        published_decisions=decisions,
        citation_sources={1: {"url": "https://example.com/source", "canonical_url": "https://example.com/source"}},
        body_md=body,
    )
    assert decision.factual_claim_count_pre_repair == 1
    assert decision.support_rate == 1.0


def test_publish_gate_counts_only_unique_winning_sources_and_canonical_body():
    padding = "This verified sentence contains detailed primary-source context and independently checkable implementation evidence. "
    claims = [
        _claim(index, f"Verified fact {index} for version 3.{index}. {padding * 2}", index)
        for index in range(1, 9)
    ]
    passages = [
        _passage(index, claim.text, domain=f"d{(index - 1) % 4}.example")
        for index, claim in enumerate(claims, 1)
    ]
    decisions = evaluate_support(claims, passages)
    narrowed = [
        replace(claim, citation_ids=decisions[index].winning_source_citation_ids)
        for index, claim in enumerate(claims)
    ]
    body = render_body(topic="gate", published_claims=[*narrowed, narrowed[0]], limitations=("bounded",))
    sources = {
        index: {"url": passage.canonical_url, "canonical_url": passage.canonical_url, "title": f"S{index}"}
        for index, passage in enumerate(passages, 1)
    }
    decision = make_publish_decision(
        original_claims=claims,
        published_claims=[*claims, claims[0]],
        published_decisions=[*decisions, decisions[0]],
        citation_sources=sources,
        body_md=body,
    )
    assert decision.passed is True
    assert decision.published_factual_count == 8
    assert decision.citation_count == 8
    assert decision.independent_domain_count == 4
    assert body.count(claims[0].text) == 1
    report = render_report(
        topic="gate", body_md=body, decision=decision,
        citations=[{"citation_id": index, **source} for index, source in sources.items()],
        coverage={"support_rate": 1.0}, errors=(),
    )
    assert report["status"] == "completed"
    assert report["report_md"].count("## 基于来源的结论") == 1


def test_publish_gate_does_not_double_count_duplicate_canonical_urls():
    padding = "Primary-source detail remains independently checkable and sufficiently descriptive for publication. " * 3
    claims = [_claim(index, f"Verified fact {index} in 2026. {padding}", index) for index in range(1, 9)]
    passages = [
        _passage(index, claim.text, domain=f"domain{(index - 1) % 4}.example")
        for index, claim in enumerate(claims, 1)
    ]
    decisions = evaluate_support(claims, passages)
    body = render_body(topic="duplicate", published_claims=claims, limitations=())
    sources = {
        index: {
            "canonical_url": (
                "https://domain0.example/shared"
                if index in {1, 2}
                else f"https://domain{(index - 1) % 4}.example/source-{index}"
            )
        }
        for index in range(1, 9)
    }
    decision = make_publish_decision(
        original_claims=claims,
        published_claims=claims,
        published_decisions=decisions,
        citation_sources=sources,
        body_md=body,
    )
    assert decision.supported_factual_count == 8
    assert decision.citation_count == 7
    assert decision.passed is False
    assert "citation_count_below_threshold" in decision.reason_codes


def test_publish_gate_allows_v4_to_override_minimum_citations_without_weakening_v3_default():
    padding = "Primary-source context remains detailed, independently checkable, and suitable for publication. " * 3
    claims = [_claim(index, f"Verified fact {index} in 2026. {padding}", index) for index in range(1, 9)]
    passages = [
        _passage(index, claim.text, domain=f"domain{(index - 1) % 4}.example")
        for index, claim in enumerate(claims, 1)
    ]
    decisions = evaluate_support(claims, passages)
    body = render_body(topic="v4 threshold", published_claims=claims, limitations=("bounded",))
    sources = {
        index: {
            "canonical_url": (
                f"https://domain{(index - 1) % 4}.example/source-{index}"
                if index <= 6
                else f"https://domain{index - 7}.example/source-{index - 6}"
            )
        }
        for index in range(1, 9)
    }
    default_decision = make_publish_decision(
        original_claims=claims,
        published_claims=claims,
        published_decisions=decisions,
        citation_sources=sources,
        body_md=body,
    )
    v4_decision = make_publish_decision(
        original_claims=claims,
        published_claims=claims,
        published_decisions=decisions,
        citation_sources=sources,
        body_md=body,
        minimum_citations=6,
    )
    assert default_decision.citation_count == 6
    assert "citation_count_below_threshold" in default_decision.reason_codes
    assert v4_decision.passed is True


def test_extractive_fallback_rejects_page_metadata_and_generic_slogans():
    passages = [
        _passage(1, "Computer Science > Artificial Intelligence"),
        _passage(2, "[Submitted on 14 Apr 2026]"),
        _passage(3, "A chatbot answers your questions."),
        _passage(5, "The cited entry was submitted on 28 Sep 2025 and last revised on 26 Jun 2026."),
        _passage(6, "NVIDIA AI tools was published on January 5, 2026 by Kari Briski 0 Comments Share This Article."),
        _passage(7, "Skip to content Navigation Menu Sign in Appearance settings Platform AI CODE CREATION."),
        _passage(8, "LLM Research Papers: The 2026 List covers January to May."),
        _passage(9, "Direct agents from issue to merge"),
        _passage(10, "Guides, concepts, and product docs for Codex"),
        _passage(
            4,
            "Microsoft Agent Framework is an open-source SDK and runtime for building and managing multi-agent systems.",
        ),
    ]
    claims = fallback_claims(passages)
    assert [claim.text for claim in claims] == [passages[-1].text]


def test_gate_failure_publishes_honest_no_results_without_citations():
    claim = _claim(1, "Unsupported version 9.9 was released in 2027.", 1)
    body = render_body(topic="failed", published_claims=(), limitations=())
    decision = make_publish_decision(
        original_claims=[claim], published_claims=(), published_decisions=(),
        citation_sources={1: {"url": "https://example.com"}}, body_md=body,
    )
    report = render_report(
        topic="failed", body_md=body, decision=decision,
        citations=({"citation_id": 1, "url": "https://example.com"},),
        coverage={}, errors=(),
    )
    assert report["status"] == "no_results"
    assert report["citations"] == []
    assert "不发布未经充分支持" in report["report_md"]

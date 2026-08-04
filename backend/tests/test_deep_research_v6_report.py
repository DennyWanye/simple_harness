from __future__ import annotations

import hashlib

import pytest

from deskpet.workflows.definitions.deep_research_v6_assessment import (
    GenericAdmittedFactV1,
    derive_collection_item_id,
    derive_matrix_cell_id,
    rank_collection_item_ids,
)
from deskpet.workflows.definitions.deep_research_v6_compiler import compile_research_spec
from deskpet.workflows.definitions.deep_research_v6_evidence import AnswerAssessmentV1
from deskpet.workflows.definitions.deep_research_v6_integrity import ClaimRecordV1
from deskpet.workflows.definitions.deep_research_v6_report import (
    CitationView,
    render_typed_report,
)


HEAD = "1" * 64
POLICY = "2" * 64
INFERENCE_REF = "sha256:" + "3" * 64


def _assessment(spec, results, *, status="completed_candidate", missing=()):
    return AnswerAssessmentV1.create(
        spec_hash=spec.spec_hash,
        evidence_head_hash=HEAD,
        policy_hash=POLICY,
        requirement_results=sorted(
            results,
            key=lambda item: (item["requirement_id"], item["item_or_cell_id"]),
        ),
        missing_requirement_ids=missing,
        minimum_useful=status != "insufficient",
        status=status,
        reason_codes=("renderer_fixture",),
    )


def _result(requirement_id, item_id, bindings, *, support="supported"):
    return {
        "requirement_id": requirement_id,
        "item_or_cell_id": item_id,
        "support_status": support,
        "binding_ids": sorted(bindings),
        "reason_codes": ["fixture"],
    }


def _claim(requirement_id, item_id, kind, text, bindings=(), *, support="supported"):
    return ClaimRecordV1.create(
        requirement_id=requirement_id,
        item_or_cell_id=item_id,
        claim_kind=kind,
        normalized_proposition=text,
        binding_ids=bindings,
        inference_ref=INFERENCE_REF if kind == "inference" else None,
        support_status=support,
        visibility="user",
    )


def _citation(claim):
    return CitationView("Primary source", f"https://example.test/{claim.claim_id}")


def test_comparison_golden_snapshot_distinguishes_fact_inference_and_preference() -> None:
    spec = compile_research_spec(
        "Compare ChatGPT and Claude across features, pricing, usability, performance, safety, and ecosystem",
        answer_locale="en-US",
        as_of_date="2026-07-18",
    )
    requirement = spec.requirements[0]
    subject = requirement["axes"][0]["members"][0]
    criterion = requirement["axes"][1]["members"][0]
    cell_id = derive_matrix_cell_id(
        requirement["requirement_id"],
        (subject["member_id"], criterion["member_id"]),
    )
    bindings = ("bind_compare",)
    claims = (
        _claim(requirement["requirement_id"], cell_id, "fact", "ChatGPT supports tool calling.", bindings),
        _claim(requirement["requirement_id"], cell_id, "inference", "This makes ChatGPT suitable for agent workflows."),
        _claim(requirement["requirement_id"], cell_id, "preference", "Prefer ChatGPT when tool integration is the priority.", bindings),
    )
    assessment = _assessment(
        spec,
        (_result(requirement["requirement_id"], cell_id, bindings),),
    )
    citations = {claim.claim_id: _citation(claim) for claim in claims}

    rendered = render_typed_report(
        spec=spec,
        assessment=assessment,
        admitted_facts=(),
        claims=claims,
        citations=citations,
    )

    assert rendered.answer_status == "completed"
    assert rendered.final_assistant == f"""## Answer

### {subject['label']} × {criterion['label']}

#### Facts
- ChatGPT supports tool calling. ([Primary source](https://example.test/{claims[0].claim_id}))

#### Inferences
- This makes ChatGPT suitable for agent workflows. ([Primary source](https://example.test/{claims[1].claim_id}))

#### Preference judgments
- Prefer ChatGPT when tool integration is the priority. ([Primary source](https://example.test/{claims[2].claim_id}))"""
    assert rendered.claims == tuple(sorted(claims, key=lambda claim: claim.claim_id))


def _product_facts(requirement, name: str, score: int):
    values = {
        "product_name": name,
        "attention_score": score,
        "advantages": [f"{name} advantage"],
        "disadvantages": [f"{name} disadvantage"],
    }
    return tuple(
        GenericAdmittedFactV1.create(
            requirement_id=requirement["requirement_id"],
            binding_ids=(f"bind_{name}_{field}",),
            entity_values={"product_name": name},
            field_key=field,
            value=value,
            as_of="2026-07-18",
        )
        for field, value in values.items()
    )


def test_top_n_golden_snapshot_uses_assessment_ranking_and_never_pads_partial_items() -> None:
    spec = compile_research_spec(
        "Top 3 AI products with advantages and disadvantages",
        answer_locale="en-US",
        as_of_date="2026-07-18",
    )
    requirement = spec.requirements[0]
    facts = _product_facts(requirement, "Beta", 80) + _product_facts(requirement, "Alpha", 95)
    ranked_ids = rank_collection_item_ids(requirement, facts)
    assert ranked_ids == (
        derive_collection_item_id(requirement["requirement_id"], ("Alpha",)),
        derive_collection_item_id(requirement["requirement_id"], ("Beta",)),
    )
    claims = tuple(
        _claim(
            requirement["requirement_id"],
            item_id,
            "fact",
            f"{name}: score {score}; one advantage and one disadvantage are supported.",
            (f"bind_{name}_product_name",),
        )
        for item_id, name, score in zip(ranked_ids, ("Alpha", "Beta"), (95, 80))
    )
    results = tuple(
        _result(
            requirement["requirement_id"],
            item_id,
            tuple(
                f"bind_{name}_{field}"
                for field in ("product_name", "attention_score", "advantages", "disadvantages")
            ),
        )
        for item_id, name in zip(ranked_ids, ("Alpha", "Beta"))
    )
    assessment = _assessment(
        spec,
        results,
        status="partial_candidate",
        missing=(requirement["requirement_id"],),
    )

    rendered = render_typed_report(
        spec=spec,
        assessment=assessment,
        admitted_facts=facts,
        claims=tuple(reversed(claims)),
    )

    assert rendered.answer_status == "partial"
    assert rendered.final_assistant == """## Answer

### Ranked results

1.
- Alpha: score 95; one advantage and one disadvantage are supported.

2.
- Beta: score 80; one advantage and one disadvantage are supported.

Partial result: 2 eligible items were found for a target of 3; no empty slots were added."""
    assert "3." not in rendered.final_assistant


def test_policy_golden_snapshot_separates_document_facts_from_registered_impact_inference() -> None:
    spec = compile_research_spec(
        "What is the latest AI policy direction and impact?",
        answer_locale="en-US",
        as_of_date="2026-07-18",
    )
    requirement_id = spec.requirements[0]["requirement_id"]
    entries = (
        ("issuer", "policy_fact", "fact", "The Ministry issued the policy.", "bind_issuer"),
        ("document", "policy_fact", "fact", "The document is named AI Action Plan.", "bind_document"),
        ("date", "policy_fact", "fact", "The publication date is 2026-07-01.", "bind_date"),
        ("commitment", "policy_fact", "fact", "The plan commits to annual safety evaluations.", "bind_commitment"),
        ("impact", "impact_inference", "inference", "Compliance costs are likely to rise.", None),
    )
    facts = tuple(
        GenericAdmittedFactV1.create(
            requirement_id=requirement_id,
            item_or_cell_id=f"claim_{facet}",
            binding_ids=(() if binding is None else (binding,)),
            claim_kind=semantic_kind,
            facet_ids=(facet,),
            inference_ref=(INFERENCE_REF if binding is None else None),
        )
        for facet, semantic_kind, _, _, binding in entries
    )
    claims = tuple(
        _claim(
            requirement_id,
            f"claim_{facet}",
            claim_kind,
            text,
            (() if binding is None else (binding,)),
        )
        for facet, _, claim_kind, text, binding in entries
    )
    assessment = _assessment(
        spec,
        tuple(
            _result(requirement_id, f"claim_{facet}", (() if binding is None else (binding,)))
            for facet, _, _, _, binding in entries
        ),
    )

    rendered = render_typed_report(
        spec=spec,
        assessment=assessment,
        admitted_facts=facts,
        claims=claims,
    )

    assert rendered.answer_status == "completed"
    assert rendered.final_assistant == """## Answer

### Policy facts

#### Issuer
- The Ministry issued the policy.

#### Document
- The document is named AI Action Plan.

#### Date
- The publication date is 2026-07-01.

#### Original commitment
- The plan commits to annual safety evaluations.

### Impact inferences
- Compliance costs are likely to rise."""


def test_open_research_snapshot_shows_all_four_required_sections() -> None:
    spec = compile_research_spec(
        "How will AI affect the design profession?",
        answer_locale="en-US",
        as_of_date="2026-07-18",
    )
    requirement_id = spec.requirements[0]["requirement_id"]
    entries = (
        ("conclusion", "fact", "AI changes task composition more than job counts."),
        ("limitation", "limitation", "Long-term labor data remain sparse."),
        ("counterevidence", "counterevidence", "Some surveys report net hiring growth."),
        ("uncertainty", "uncertainty", "Adoption speed varies by market."),
    )
    facts = tuple(
        GenericAdmittedFactV1.create(
            requirement_id=requirement_id,
            item_or_cell_id=f"claim_{semantic_kind}",
            binding_ids=(f"bind_{semantic_kind}",),
            claim_kind=semantic_kind,
            facet_ids=("topic",),
        )
        for semantic_kind, _, _ in entries
    )
    claims = tuple(
        _claim(
            requirement_id,
            f"claim_{semantic_kind}",
            claim_kind,
            text,
            (f"bind_{semantic_kind}",),
        )
        for semantic_kind, claim_kind, text in entries
    )
    assessment = _assessment(
        spec,
        tuple(
            _result(requirement_id, f"claim_{semantic_kind}", (f"bind_{semantic_kind}",))
            for semantic_kind, _, _ in entries
        ),
    )

    rendered = render_typed_report(
        spec=spec,
        assessment=assessment,
        admitted_facts=facts,
        claims=claims,
    )

    assert rendered.final_assistant == """## Answer

### Conclusion
- AI changes task composition more than job counts.

### Limitations
- Long-term labor data remain sparse.

### Counterevidence
- Some surveys report net hiring growth.

### Uncertainty
- Adoption speed varies by market."""


def test_unsupported_claims_are_invisible_and_citations_reject_non_http_urls() -> None:
    spec = compile_research_spec(
        "How will AI affect the design profession?",
        answer_locale="en-US",
        as_of_date="2026-07-18",
    )
    requirement_id = spec.requirements[0]["requirement_id"]
    supported = _claim(requirement_id, "claim_conclusion", "fact", "Supported conclusion.", ("bind_ok",))
    unsupported = _claim(
        requirement_id,
        "claim_conclusion",
        "fact",
        "Unsupported secret should never render.",
        ("bind_bad",),
        support="unsupported",
    )
    fact = GenericAdmittedFactV1.create(
        requirement_id=requirement_id,
        item_or_cell_id="claim_conclusion",
        binding_ids=("bind_ok",),
        claim_kind="conclusion",
        facet_ids=("topic",),
    )
    assessment = _assessment(
        spec,
        (_result(requirement_id, "claim_conclusion", ("bind_ok",)),),
    )

    rendered = render_typed_report(
        spec=spec,
        assessment=assessment,
        admitted_facts=(fact,),
        claims=(unsupported, supported),
        citations={supported.claim_id: CitationView("Source", "https://example.test/source")},
    )

    assert "Supported conclusion." in rendered.final_assistant
    assert "Unsupported secret" not in rendered.final_assistant
    assert unsupported not in rendered.claims
    with pytest.raises(ValueError, match=r"HTTP\(S\)"):
        CitationView("Unsafe", "file:///tmp/source")


def test_renderer_is_deterministic_for_shuffled_claim_input() -> None:
    spec = compile_research_spec(
        "How will AI affect the design profession?",
        answer_locale="en-US",
        as_of_date="2026-07-18",
    )
    requirement_id = spec.requirements[0]["requirement_id"]
    claims = tuple(
        _claim(requirement_id, f"claim_{index}", "fact", text, (f"bind_{index}",))
        for index, text in enumerate(("First conclusion.", "Second conclusion."), start=1)
    )
    facts = tuple(
        GenericAdmittedFactV1.create(
            requirement_id=requirement_id,
            item_or_cell_id=f"claim_{index}",
            binding_ids=(f"bind_{index}",),
            claim_kind="conclusion",
            facet_ids=("topic",),
        )
        for index in (1, 2)
    )
    assessment = _assessment(
        spec,
        tuple(_result(requirement_id, f"claim_{index}", (f"bind_{index}",)) for index in (1, 2)),
    )

    first = render_typed_report(spec=spec, assessment=assessment, admitted_facts=facts, claims=claims)
    second = render_typed_report(spec=spec, assessment=assessment, admitted_facts=facts, claims=tuple(reversed(claims)))

    assert hashlib.sha256(first.final_assistant.encode()).hexdigest() == hashlib.sha256(
        second.final_assistant.encode()
    ).hexdigest()

from __future__ import annotations

import asyncio
import hashlib
import json
import time

import pytest

from deskpet.workflows.contracts import WorkflowContext
from deskpet.workflows.definitions.research_core import (
    FetchPort,
    ResearchLLMPort,
    ResearchSearchPort,
    ResearchSearchResults,
)
from deskpet.workflows.definitions.v3 import DEFAULT_DEEP_RESEARCH_VERSION, register_v3_workflows
from deskpet.workflows.definitions.v3.deep_research import (
    DEEP_RESEARCH_V3,
    DEEP_RESEARCH_V3_DEFINITION,
    initial_state,
)
from deskpet.workflows.definitions.deep_research_v3_contracts import AtomicClaim
from deskpet.workflows.definitions.deep_research_v3_nodes import (
    _meaningful_structured_claims,
    branch_handler,
    rerank_handler,
    synth_handler,
)
from deskpet.workflows.native import NativeExecutionPolicy
from deskpet.workflows.runner import WorkflowRegistry


def test_v3_definition_preserves_fixed_public_contract_and_registers_separately():
    node_ids = {node.node_id for node in DEEP_RESEARCH_V3_DEFINITION.nodes}
    for stage in ("expand", "search", "direct", "fetch", "score"):
        assert {f"{stage}_b{index}" for index in range(6)} <= node_ids
        assert f"{stage}_join" in node_ids
    assert DEEP_RESEARCH_V3.manifest.workflow_version == "v3"
    assert DEEP_RESEARCH_V3_DEFINITION.prompt_manifest["public_stages"] == [
        "normalize", "plan", "expand", "search", "direct", "fetch", "score",
        "gap", "rerank", "synth", "cite", "persist", "finalize",
    ]
    registry = WorkflowRegistry()
    register_v3_workflows(registry)
    assert ("deep_research", "v3") in registry.versions()
    assert DEFAULT_DEEP_RESEARCH_VERSION == "v3"


def test_v4_meaningful_claim_filter_rejects_bibliographic_metadata():
    claims = [
        AtomicClaim("claim-1", "The cited entry was submitted on 14 Apr 2026.", "factual", (1,)),
        AtomicClaim("claim-2", "The NVIDIA article was authored by Kari Briski.", "factual", (2,)),
        AtomicClaim("claim-nav", "Direct agents from issue to merge", "factual", (4,)),
        AtomicClaim("claim-vague", "A technical report presents HunyuanImage 3.", "factual", (5,)),
        AtomicClaim("claim-ethics", "A case study of OpenAI analysed ethical AI discourse.", "factual", (6,)),
        AtomicClaim(
            "claim-3",
            "The runtime persists tool state across agent turns and resumes interrupted tasks from checkpoints.",
            "factual",
            (3,),
        ),
    ]

    assert _meaningful_structured_claims(claims) == [claims[-1]]


@pytest.mark.asyncio
async def test_v3_plan_fallback_creates_three_independently_searchable_questions():
    async def invalid_llm(prompt: str) -> str:
        return "not-json"

    state = initial_state(topic="WebGPU browser support", run_id="run-plan")
    nodes = {value.node_id: value for value in DEEP_RESEARCH_V3_DEFINITION.nodes}
    normalize = nodes["normalize"]
    plan = nodes["plan"]
    context = WorkflowContext(ports={"llm": ResearchLLMPort(invalid_llm)})
    normalized = normalize.handler(state, context)
    if asyncio.iscoroutine(normalized):
        normalized = await normalized
    state["values"] = normalized.to_dict()["values"]
    planned = plan.handler(state, context)
    if asyncio.iscoroutine(planned):
        planned = await planned
    values = planned.to_dict()["values"]
    assert len(values["sub_questions"]) == 3
    assert values["active_branch_count"] == 3


@pytest.mark.asyncio
async def test_v3_run_emits_provider_coverage_and_fails_gate_honestly():
    async def llm(prompt: str) -> str:
        if "sub_questions" in prompt:
            return '{"sub_questions":["official status","implementation evidence","known limitations"]}'
        return "{}"

    async def search(query: str, *, max_results: int):
        digest = hashlib.sha256(query.encode()).hexdigest()[:8]
        return ResearchSearchResults(
            [{"url": f"https://example.com/{digest}", "title": query, "snippet": "verified evidence"}],
            observation={
                "request_id": f"request-{digest}", "run_id": "run-v3",
                "provider_attempt_count": 1, "provider_probe_count": 1,
                "provider_attempts": [{"provider": "test", "status": "hit", "permit": "closed", "probe_outcome": "not_probe"}],
                "degraded": False,
            },
        )

    async def fetch(url: str):
        return {"url": url, "title": "Primary", "text": "Verified primary evidence for version 3.14 in 2026. " * 8}

    result = await DEEP_RESEARCH_V3.bind().ainvoke(
        initial_state(topic="v3 quality", run_id="run-v3"),
        WorkflowContext(ports={
            "llm": ResearchLLMPort(llm), "search": ResearchSearchPort(search),
            "fetch": FetchPort(fetch), "native_execution_policy": NativeExecutionPolicy(3),
        }),
        thread_id="run-v3", run_id="run-v3",
    )
    coverage = result["values"]["report_payload"]["coverage"]
    assert coverage["provider_attempt_count"] >= 1
    assert coverage["provider_probe_count"] >= 1
    assert coverage["provider_attempts"][0] == {
        "provider": "test", "status": "hit", "permit": "closed", "probe_outcome": "not_probe",
    }
    assert result["values"]["report_payload"]["status"] == "no_results"
    assert result["values"]["terminal_status"] == "error"
    assert len(result["values"]["public_progress"]["completed_stage_ids"]) == 13
    expanded = result["branch_expand"]["b0"]
    searched = result["branch_search"]["b0"]
    assert expanded["budget_after"]["query_remaining"] == expanded["budget_before"]["query_remaining"]
    assert searched["budget_before"]["query_remaining"] == expanded["budget_after"]["query_remaining"]
    assert searched["budget_after"]["query_remaining"] == (
        searched["budget_before"]["query_remaining"] - len(expanded["result"])
    )


@pytest.mark.asyncio
async def test_score_preserves_already_fetched_evidence_after_deadline_expires():
    deadline = time.time() - 1
    budget = {
        "query_remaining": 0,
        "url_remaining": 0,
        "fetch_remaining": 0,
        "llm_remaining": 0,
        "engine_retry_limit": 0,
        "deadline_at": deadline,
    }
    document = {
        "url": "https://openai.com/research/example",
        "canonical_url": "https://openai.com/research/example",
        "content_hash": "already-fetched",
        "text": (
            "OpenAI documents a specific AI system capability with reproducible benchmark evidence "
            "and detailed implementation constraints for independent verification."
        ),
    }
    state = initial_state(topic="AI system capability", run_id="score-after-deadline")
    state["values"]["branch_work_items"] = {
        "b0": {
            "branch_id": "b0",
            "active": True,
            "mode": "fanout",
            "questions": ["AI system capability"],
            "budget": budget,
        }
    }
    state["branch_fetch"] = {
        "b0": {
            "branch_id": "b0",
            "stage": "fetch",
            "result": [document],
            "errors": [],
            "budget_after": budget,
        }
    }
    patch = await branch_handler("score", "b0", state, WorkflowContext())
    scored = patch.to_dict()["branch_score"]["b0"]
    assert len(scored["result"]) == 1
    assert scored["errors"] == []
    assert scored["result"][0]["content_hash"] == "already-fetched"


@pytest.mark.asyncio
async def test_synthesis_prefers_supported_extractive_candidate_over_legal_low_quality_llm():
    padding = (
        "The primary document provides independently checkable implementation context, "
        "compatibility boundaries, operational constraints, and reproducible evidence for reviewers "
    )
    documents = []
    for index in range(1, 9):
        text = f"Verified source {index} establishes documented behavior in 2026 and {padding * 2}."
        documents.append({
            "url": f"https://d{(index - 1) % 4}.example/source-{index}",
            "canonical_url": f"https://d{(index - 1) % 4}.example/source-{index}",
            "title": f"Source {index}", "content_hash": f"hash-{index}",
            "question": "documented behavior", "text": text, "score": 1.0,
        })

    async def low_quality_llm(prompt: str) -> str:
        return (
            '{"claims":[{"text":"Unsupported version 99.99 was released in 2099",'
            '"kind":"factual","citation_ids":[1]}]}'
        )

    state = initial_state(topic="documented behavior", run_id="synth-choice")
    state["values"]["ranked_evidence"] = documents
    patch = await synth_handler(
        state,
        WorkflowContext(ports={"llm": ResearchLLMPort(low_quality_llm)}),
    )
    values = patch.to_dict()["values"]
    assert values["synthesis_candidate"] == "extractive"
    assert len(values["atomic_claims"]) == 8
    scores = {value["candidate"]: value for value in values["synthesis_candidate_scores"]}
    assert scores["extractive"]["gate_passed"] is True
    assert scores["structured_llm"]["supported_factual"] == 0


@pytest.mark.asyncio
async def test_synthesis_v4_preference_prunes_unsupported_claim_before_selection():
    documents = []
    claims = []
    for index in range(1, 9):
        sentence = (
            f"Verified source {index} establishes documented AI behavior in 2026 "
            f"with production evidence and independently checkable implementation details."
        )
        documents.append({
            "url": f"https://d{index}.example/source-{index}",
            "canonical_url": f"https://d{index}.example/source-{index}",
            "title": f"Source {index}", "content_hash": f"hash-{index}",
            "question": "documented AI behavior", "text": sentence, "score": 1.0,
        })
        claims.append({
            "text": sentence,
            "kind": "factual",
            "citation_ids": [index],
        })
    claims.append({
        "text": "Unsupported version 99.99 was released in 2099.",
        "kind": "factual",
        "citation_ids": [1],
    })

    async def mostly_supported_llm(prompt: str) -> str:
        return json.dumps({"claims": claims})

    state = initial_state(topic="documented AI behavior", run_id="structured-choice")
    state["values"]["ranked_evidence"] = documents
    state["values"]["research_config"]["prefer_structured_synthesis"] = True
    patch = await synth_handler(
        state,
        WorkflowContext(ports={"llm": ResearchLLMPort(mostly_supported_llm)}),
    )
    values = patch.to_dict()["values"]

    assert values["synthesis_candidate"] == "structured_llm"
    assert len(values["atomic_claims"]) == 8
    assert all("99.99" not in claim["text"] for claim in values["atomic_claims"])
    scores = {value["candidate"]: value for value in values["synthesis_candidate_scores"]}
    assert scores["structured_llm"]["gate_passed"] is True
    assert scores["structured_llm"]["support_rate"] == 1.0


@pytest.mark.asyncio
async def test_synthesis_v4_supplements_unused_sources_before_extractive_fallback():
    documents = []
    first_claims = []
    for index in range(1, 9):
        sentence = (
            f"Verified source {index} documents a distinct AI capability in 2026 with production "
            f"evidence and measurable implementation detail plus independently checkable constraints."
        )
        documents.append({
            "url": f"https://d{index}.example/source-{index}",
            "canonical_url": f"https://d{index}.example/source-{index}",
            "title": f"Source {index}", "content_hash": f"hash-{index}",
            "question": "documented AI capability", "text": sentence, "score": 1.0,
        })
        if index <= 5:
            first_claims.append({"text": sentence, "kind": "factual", "citation_ids": [index]})

    calls = 0

    async def undersized_then_supplemented_llm(prompt: str) -> str:
        nonlocal calls
        calls += 1
        if "Supplement an undersized research synthesis" in prompt:
            return json.dumps({
                "claims": [
                    {
                        "text": documents[index - 1]["text"],
                        "kind": "factual",
                        "citation_ids": [index],
                    }
                    for index in range(6, 9)
                ]
            })
        return json.dumps({"claims": first_claims})

    state = initial_state(topic="documented AI capability", run_id="structured-supplement")
    state["values"]["ranked_evidence"] = documents
    state["values"]["research_config"].update({
        "prefer_structured_synthesis": True,
        "minimum_publish_citations": 6,
    })
    patch = await synth_handler(
        state,
        WorkflowContext(ports={"llm": ResearchLLMPort(undersized_then_supplemented_llm)}),
    )
    values = patch.to_dict()["values"]
    assert calls == 2
    assert values["synthesis_candidate"] == "structured_llm"
    assert len(values["atomic_claims"]) == 8
    scores = {value["candidate"]: value for value in values["synthesis_candidate_scores"]}
    assert scores["structured_llm"]["gate_passed"] is True


@pytest.mark.asyncio
async def test_synthesis_v4_hybrid_fills_source_gaps_without_lowering_publish_gate():
    documents = []
    structured = []
    for index in range(1, 9):
        sentence = (
            f"Primary source {index} documents AI system capability {index} in 2026 with measurable "
            f"implementation evidence and independently verifiable technical constraints."
        )
        documents.append({
            "url": f"https://d{index}.example/source-{index}",
            "canonical_url": f"https://d{index}.example/source-{index}",
            "title": f"Source {index}", "content_hash": f"hybrid-{index}",
            "question": "AI system capability", "text": sentence, "score": 1.0,
        })
        if index <= 5:
            structured.append({"text": sentence, "kind": "factual", "citation_ids": [index]})

    async def undersized_llm(prompt: str) -> str:
        del prompt
        return json.dumps({"claims": structured})

    state = initial_state(topic="AI system capability", run_id="structured-hybrid")
    state["values"]["ranked_evidence"] = documents
    state["values"]["research_config"].update({
        "prefer_structured_synthesis": True,
        "minimum_publish_citations": 6,
    })
    patch = await synth_handler(
        state,
        WorkflowContext(ports={"llm": ResearchLLMPort(undersized_llm)}),
    )
    values = patch.to_dict()["values"]
    assert values["synthesis_candidate"] == "structured_hybrid"
    assert len(values["atomic_claims"]) == 8
    scores = {value["candidate"]: value for value in values["synthesis_candidate_scores"]}
    assert scores["structured_llm"]["gate_passed"] is False
    assert scores["structured_hybrid"]["gate_passed"] is True
    assert scores["structured_hybrid"]["citations"] == 8


@pytest.mark.asyncio
async def test_synthesis_can_choose_best_eligible_structured_candidate():
    documents = []
    structured = []
    for index in range(1, 10):
        sentence = (
            f"Primary source {index} documents inference runtime capability {index} in 2026 "
            f"with measurable implementation evidence and independently verifiable constraints."
        )
        documents.append({
            "url": f"https://d{index}.example/source-{index}",
            "canonical_url": f"https://d{index}.example/source-{index}",
            "title": f"Runtime source {index}", "content_hash": f"best-{index}",
            "question": "AI inference runtime", "text": sentence, "score": 1.0,
        })
        if index <= 8:
            structured.append({"text": sentence, "kind": "factual", "citation_ids": [index]})

    async def structured_llm(prompt: str) -> str:
        del prompt
        return json.dumps({"claims": structured})

    state = initial_state(topic="AI inference runtime", run_id="best-structured")
    state["values"]["ranked_evidence"] = documents
    state["values"]["research_config"].update({
        "prefer_structured_synthesis": True,
        "prefer_best_structured_synthesis": True,
        "minimum_publish_citations": 6,
    })
    values = (
        await synth_handler(
            state,
            WorkflowContext(ports={"llm": ResearchLLMPort(structured_llm)}),
        )
    ).to_dict()["values"]

    assert values["synthesis_candidate"] == "structured_hybrid"
    assert len(values["atomic_claims"]) == 9


@pytest.mark.asyncio
async def test_rerank_prefers_unique_canonical_urls_before_duplicate_passages():
    evidence = []
    for index in range(8):
        evidence.append({
            "url": f"https://d{index % 4}.example/source-{index}",
            "canonical_url": f"https://d{index % 4}.example/source-{index}",
            "content_hash": f"unique-{index}",
            "text": f"Unique source {index} contains independently verifiable evidence.",
            "score": 1.0 - index * 0.01,
        })
        evidence.append({
            "url": f"https://d{index % 4}.example/source-{index}",
            "canonical_url": f"https://d{index % 4}.example/source-{index}",
            "content_hash": f"duplicate-{index}",
            "text": f"Second passage from source {index}.",
            "score": 0.99 - index * 0.01,
        })

    state = initial_state(topic="canonical diversity", run_id="rerank-diversity")
    state["values"]["joined_score"] = evidence
    state["values"]["research_config"]["max_total_passages"] = 8
    patch = await rerank_handler(state, WorkflowContext(ports={}))
    ranked = patch.to_dict()["values"]["ranked_evidence"]

    assert len(ranked) == 8
    assert len({value["canonical_url"] for value in ranked}) == 8

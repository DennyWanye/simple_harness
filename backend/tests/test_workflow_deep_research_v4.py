from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from dataclasses import replace
from datetime import date

import pytest

from deskpet.workflows.contracts import StatePatch, WorkflowContext
from deskpet.workflows.definitions.deep_research_v4_nodes import (
    _fallback_localized_change,
    _github_release_document,
    _github_releases_api_url,
    _localize_findings,
    _parse_localized_changes,
    branch_handler,
    cite_handler,
    finalize_handler,
    insufficient_evidence_finalize,
    no_results_finalize,
    normalize_handler,
    plan_handler,
    persist_handler,
    post_cite_route,
    post_direct_route,
    rerank_handler,
)
from deskpet.workflows.definitions.deep_research_v4_contracts import TechnologyFinding
from deskpet.workflows.definitions.deep_research_v3_nodes import (
    normalize_handler as v3_normalize_handler,
    plan_handler as v3_plan_handler,
)
from deskpet.workflows.definitions.research_core import (
    DirectOutcome,
    FetchPort,
    ResearchLLMPort,
    ResearchSearchPort,
    ResearchSearchResults,
)
from deskpet.workflows.definitions.v3.deep_research import (
    DEEP_RESEARCH_V3_DEFINITION,
    initial_state as v3_initial_state,
)
from deskpet.workflows.definitions.v4.deep_research import (
    DEEP_RESEARCH_V4,
    DEEP_RESEARCH_V4_DEFINITION,
    initial_state,
)
from deskpet.workflows.native import NativeExecutionPolicy


ORIGINAL_TOPIC = "可以帮我调研一下，现在 AI 相关的最新最有价值的技术相关的信息吗？"


@pytest.mark.parametrize(
    ("entity", "statement", "expected"),
    (
        (
            "MCP",
            "Model Context Protocol marked the release candidate `2026-07-28` revision. | "
            "Model Context Protocol marked the stable release of the `2025-11-25` revision.",
            "MCP 已标记 2026-07-28 修订候选版，并将 2025-11-25 修订版标记为稳定版。",
        ),
        (
            "vLLM",
            "vLLM Transformers modeling backend gained FP8 MoE support. | "
            "vLLM added LLaVA-OneVision-2 as a new model.",
            "vLLM 的 Transformers modeling backend 新增 FP8 MoE 支持，并加入 LLaVA-OneVision-2 模型。",
        ),
        (
            "LinearCrossEntropyLoss",
            "LinearCrossEntropyLoss cuts peak GPU memory by up to 4x for large-vocabulary language model training.",
            "LinearCrossEntropyLoss 在大词表语言模型训练中，将峰值 GPU 内存最多降低 4x。",
        ),
        (
            "Jetson-PI",
            "The cited method achieved 41x improvements in control frequency compared with naive PyTorch on NVIDIA Jetson Orin.",
            "Jetson-PI 在 NVIDIA Jetson Orin 上，相较朴素 PyTorch 将控制频率提高 41x。",
        ),
        (
            "Model Context Protocol",
            "The Model Context Protocol release candidate 2026-07-28 revision was released on 2026-05-29.",
            "Model Context Protocol 已将 2026-07-28 协议修订版标记为候选发布版。",
        ),
    ),
)
def test_fallback_localization_preserves_concrete_supported_change(
    entity: str,
    statement: str,
    expected: str,
) -> None:
    finding = TechnologyFinding(
        finding_id=f"fallback-{entity}",
        entity=entity,
        topic_kind="training_inference_system",
        topic_label="Systems",
        claim_ids=("c1",),
        published_at="2026-07-08",
        recency_score=1.0,
        impact_score=2,
        maturity="emerging",
        evidence_quality=2,
        winning_citation_ids=(1,),
        total_score=0.7,
        statement=statement,
    )

    assert _fallback_localized_change(finding) == expected


def test_github_release_api_document_preserves_concrete_recent_changes() -> None:
    url = "https://github.com/vllm-project/vllm/releases"
    assert _github_releases_api_url(url) == (
        "https://api.github.com/repos/vllm-project/vllm/releases?per_page=3"
    )
    document = _github_release_document(url, [{
        "name": "vLLM v0.25.0",
        "tag_name": "v0.25.0",
        "published_at": "2026-07-12T08:00:00Z",
        "body": "Model Runner V2 is now the default and improves serving throughput.",
        "draft": False,
    }])
    assert document is not None
    assert document["published_at"] == "2026-07-12"
    assert document["source_kind"] == "repository"
    assert "Model Runner V2" in document["text"]


def _handler_gate_findings(
    count: int = 5,
    *,
    same_score: bool = False,
    zero_recency: bool = False,
    unknown_maturity: bool = False,
) -> tuple[TechnologyFinding, ...]:
    kinds = ("model_inference", "agent", "multimodal", "training_inference_system", "open_infrastructure")
    statements = (
        "Model Runner V2 is now the default for all dense models.",
        "SLEUTH uses a structured epistemic working memory.",
        "Developer logs for supported Interactions API calls became viewable in the AI Studio dashboard.",
        "FSDP2 overlaps reduce-scatter and all-gather communications via a dedicated process group.",
        "PyTorch 2.11 added differentiable collectives for distributed training.",
    )
    return tuple(
        TechnologyFinding(
            finding_id=f"handler-{index}", entity=f"HandlerEntity{index}",
            topic_kind=kinds[index - 1], topic_label=f"Theme {index}", claim_ids=(f"c{index}",),
            published_at=None if zero_recency else f"2026-07-{index:02d}",
            recency_score=0.0 if zero_recency else 1.0,
            impact_score=1 + index % 3,
            maturity="unknown" if unknown_maturity else ("adopted" if index == 1 else "emerging"),
            evidence_quality=2 + index % 2, winning_citation_ids=(index,),
            total_score=0.5 if same_score else 0.45 + index * 0.05,
            statement=statements[index - 1],
        )
        for index in range(1, count + 1)
    )


def test_v4_is_new_graph_and_does_not_mutate_v3_definition() -> None:
    assert DEEP_RESEARCH_V4_DEFINITION.version == "v4"
    assert DEEP_RESEARCH_V4_DEFINITION.state_schema_version == 4
    assert DEEP_RESEARCH_V3_DEFINITION.version == "v3"
    routes = {edge.source: dict(edge.routes) for edge in DEEP_RESEARCH_V4_DEFINITION.conditional_edges}
    assert routes == {
        "direct_join": {"research": "research", "no_results": "no_results_finalize"},
        "cite": {"success": "persist", "insufficient_evidence": "insufficient_evidence_finalize"},
    }


@pytest.mark.asyncio
async def test_normalize_freezes_as_of_and_technology_profile() -> None:
    state = initial_state(topic=ORIGINAL_TOPIC, run_id="run-v4")
    patch = await normalize_handler(state, WorkflowContext(ports={"clock": lambda: date(2026, 7, 15)}))
    values = patch.to_dict()["values"]
    assert values["intent_profile"] == {
        "kind": "technology_intelligence",
        "as_of_date": "2026-07-15",
        "window_start": "2026-01-16",
        "window_end": "2026-07-15",
        "requested_dimensions": [],
    }
    assert values["research_config"]["prefer_structured_synthesis"] is True
    assert values["research_config"]["prefer_best_structured_synthesis"] is True
    resumed = {**state, "values": values}
    second = await normalize_handler(resumed, WorkflowContext(ports={"clock": lambda: date(2030, 1, 1)}))
    assert second.to_dict()["values"]["intent_profile"]["as_of_date"] == "2026-07-15"


@pytest.mark.asyncio
async def test_technology_plan_uses_fixed_slots_and_short_queries() -> None:
    state = initial_state(topic=ORIGINAL_TOPIC, run_id="run-v4")
    normalized = await normalize_handler(state, WorkflowContext(ports={"clock": lambda: date(2026, 7, 15)}))
    state["values"] = normalized.to_dict()["values"]
    started = time.time()
    planned = await plan_handler(state, WorkflowContext())
    values = planned.to_dict()["values"]
    assert 3 <= len(values["technology_topics"]) <= 5
    assert values["active_branch_count"] == len(values["technology_topics"])
    assert all(ORIGINAL_TOPIC not in item["discovery_query"] for item in values["technology_topics"])
    assert set(values["technology_topic_by_branch"]) == {f"b{i}" for i in range(len(values["technology_topics"]))}
    for branch_id, topic in values["technology_topic_by_branch"].items():
        assert values["branch_work_items"][branch_id]["questions"] == [topic["discovery_query"]]
    assert values["sub_questions"] == [
        values["technology_topic_by_branch"][f"b{i}"]["discovery_query"]
        for i in range(len(values["technology_topics"]))
    ]
    runtime = values["research_config"]
    assert runtime["technology_total_fetch_budget"] == 20
    assert runtime["technology_fetch_timeout_s"] == 8.0
    assert runtime["technology_pdf_timeout_s"] == 4.0
    assert 295 <= runtime["deadline_at"] - started <= 301


@pytest.mark.asyncio
async def test_source_pack_switch_keeps_only_the_discovery_query() -> None:
    state = initial_state(
        topic=ORIGINAL_TOPIC,
        run_id="source-pack-off",
        research_config={"source_packs": False},
    )
    state["values"] = (
        await normalize_handler(
            state,
            WorkflowContext(ports={"clock": lambda: date(2026, 7, 15)}),
        )
    ).to_dict()["values"]
    state["values"] = (await plan_handler(state, WorkflowContext())).to_dict()["values"]

    patch = await branch_handler("expand", "b0", state, WorkflowContext())
    rows = patch.to_dict()["branch_expand"]["b0"]["result"]

    assert len(rows) == 1
    assert rows[0]["kind"] == "discovery"


@pytest.mark.asyncio
async def test_technology_rerank_prioritizes_primary_sources_over_higher_scored_blog_posts() -> None:
    state = initial_state(
        topic=ORIGINAL_TOPIC,
        run_id="authority-rerank",
        research_config={"max_total_passages": 4},
    )
    primary = [
        {
            "url": f"https://arxiv.org/abs/2607.000{index}",
            "canonical_url": f"https://arxiv.org/abs/2607.000{index}",
            "content_hash": f"primary-{index}",
            "text": f"Primary paper {index} documents a specific model capability and benchmark result.",
            "score": 0.3 + index * 0.01,
        }
        for index in range(4)
    ]
    secondary = [
        {
            "url": f"https://blog{index}.example/post",
            "canonical_url": f"https://blog{index}.example/post",
            "content_hash": f"secondary-{index}",
            "text": f"Secondary roundup {index} describes broad AI trends without primary evidence.",
            "score": 0.99 - index * 0.01,
        }
        for index in range(4)
    ]
    state["values"]["joined_score"] = [*secondary, *primary]
    patch = await rerank_handler(state, WorkflowContext())
    values = patch.to_dict()["values"]
    assert len(values["ranked_evidence"]) == 4
    assert all("arxiv.org" in item["url"] for item in values["ranked_evidence"])
    assert values["rerank_authority"] == {
        "primary_available": 4,
        "primary_selected": 4,
        "selected": 4,
    }


@pytest.mark.asyncio
async def test_generic_plan_delegates_to_v3_without_behavior_drift() -> None:
    config = {
        "sub_questions": ["history", "implementation", "limitations"],
        "deadline_at": 1_900_000_000.0,
    }
    context = WorkflowContext()
    v3_state = v3_initial_state(topic="WebGPU history", run_id="v3", research_config=config)
    v4_state = initial_state(topic="WebGPU history", run_id="v4", research_config=config)
    v3_state["values"] = (await v3_normalize_handler(v3_state, context)).to_dict()["values"]
    v4_state["values"] = (await normalize_handler(v4_state, context)).to_dict()["values"]
    v3_values = (await v3_plan_handler(v3_state, context)).to_dict()["values"]
    v4_values = (await plan_handler(v4_state, context)).to_dict()["values"]

    for key in ("sub_questions", "branch_work_items", "active_branch_count"):
        assert v4_values[key] == v3_values[key]
    assert v4_values["intent_profile"]["kind"] == "generic"


@pytest.mark.asyncio
async def test_zero_candidate_route_short_circuits_with_utf8_failure_and_no_delivery() -> None:
    state = initial_state(topic=ORIGINAL_TOPIC, run_id="run-v4")
    for stage in ("search", "direct"):
        state[f"branch_{stage}"] = {
            f"b{index}": {"result": [], "branch_id": f"b{index}", "stage": stage}
            for index in range(6)
        }
    assert await post_direct_route(state, WorkflowContext()) == "no_results"
    patch = await no_results_finalize(state, WorkflowContext())
    values = patch.to_dict()["values"]
    assert values["terminal_status"] == "error"
    assert values["terminal_error"]["code"] == "deep_research_no_results"
    assert values["terminal_error"]["user_message"] == "搜索与一手来源补强后仍未找到可核验候选。"
    assert values["delivery_intents"] == []
    assert "report_payload" not in values
    assert values["terminal_public"]["skipped_stage_ids"] == [
        "fetch", "score", "gap", "rerank", "synth", "cite", "persist"
    ]


@pytest.mark.asyncio
async def test_candidate_route_reaches_research_and_cite_gate_is_explicit() -> None:
    state = initial_state(topic="generic topic", run_id="run-v4")
    state["branch_search"] = {
        "b0": {"result": [{"url": "https://example.org", "canonical_url": "https://example.org"}]}
    }
    state["branch_direct"] = {}
    assert await post_direct_route(state, WorkflowContext()) == "research"
    state["values"]["report_payload"] = {"status": "completed"}
    assert await post_cite_route(state, WorkflowContext()) == "success"
    state["values"]["report_payload"] = {"status": "no_results"}
    assert await post_cite_route(state, WorkflowContext()) == "insufficient_evidence"


@pytest.mark.asyncio
async def test_insufficient_evidence_never_delivers_report_or_artifact() -> None:
    state = initial_state(topic="generic topic", run_id="run-v4")
    state["values"].update(
        {
            "report_payload": {"status": "no_results", "report_md": "must not leak"},
            "report_artifact": {"path": "must-not-exist"},
            "delivery_intents": [{"kind": "artifact_card"}],
        }
    )
    patch = await insufficient_evidence_finalize(state, WorkflowContext())
    values = patch.to_dict()["values"]
    assert values["terminal_error"]["user_message"] == "已有候选未通过引用支持与发布门，未生成报告。"
    assert values["delivery_intents"] == []
    assert "report_payload" not in values
    assert "report_artifact" not in values
    assert values["terminal_public"]["diagnostic_codes"] == ["insufficient_evidence"]


@pytest.mark.asyncio
async def test_compiled_v4_zero_results_never_invokes_fetch_or_later_handlers() -> None:
    fetch_calls = 0

    async def empty_search(query: str, *, max_results: int):
        request_id = f"safe-{abs(hash(query))}"
        del max_results
        return ResearchSearchResults(
            [],
            observation={
                "request_id": request_id,
                "run_id": "run-v4-empty",
                "provider_attempt_count": 1,
                "provider_probe_count": 0,
                "provider_attempts": [
                    {
                        "provider": "test",
                        "status": "empty",
                        "permit": "closed",
                        "probe_outcome": "not_probe",
                        "upstream_called": True,
                    }
                ],
                "coverage": {"actual_requests": 1, "empty": 1, "hits": 0, "timeouts": 0},
            },
        )

    async def forbidden_fetch(url: str):
        nonlocal fetch_calls
        fetch_calls += 1
        raise AssertionError(f"fetch must not execute for zero candidates: {url}")

    state = initial_state(
        topic=ORIGINAL_TOPIC,
        run_id="run-v4-empty",
        research_config={"direct_sources": False},
    )
    result = await DEEP_RESEARCH_V4.bind().ainvoke(
        state,
        WorkflowContext(
            ports={
                "clock": lambda: date(2026, 7, 15),
                "search": ResearchSearchPort(empty_search),
                "fetch": FetchPort(forbidden_fetch),
                "native_execution_policy": NativeExecutionPolicy(4),
            }
        ),
        thread_id="run-v4-empty",
        run_id="run-v4-empty",
    )

    assert fetch_calls == 0
    assert result["values"]["terminal_status"] == "error"
    assert result["values"]["terminal_error"]["code"] == "deep_research_no_results"
    assert result["values"]["delivery_intents"] == []
    assert result["values"]["terminal_public"]["metrics"]["actual_requests"] == 15
    assert result["values"]["terminal_public"]["metrics"]["empty"] == 15
    assert result["values"]["public_progress"]["completed_stage_ids"] == [
        "normalize", "plan", "expand", "search", "direct"
    ]
    assert "report_payload" not in result["values"]
    skipped = [
        event for event in result["branch_events"]
        if event.get("kind") == "source_seed_observation"
    ]
    assert len(skipped) == 5
    assert {event["reason_code"] for event in skipped} == {"direct_sources_disabled"}


@pytest.mark.asyncio
async def test_technology_scholarly_seed_calls_the_real_arxiv_direct_adapter() -> None:
    direct_sources: list[str] = []

    async def empty_search(query: str, *, max_results: int):
        del query, max_results
        return ResearchSearchResults([], observation={"provider_attempt_count": 0})

    async def direct(query: str, source: str):
        assert query
        direct_sources.append(source)
        return DirectOutcome(source=source, items=[], status="empty")

    result = await DEEP_RESEARCH_V4.bind().ainvoke(
        initial_state(topic=ORIGINAL_TOPIC, run_id="run-v4-direct"),
        WorkflowContext(
            ports={
                "clock": lambda: date(2026, 7, 15),
                "search": ResearchSearchPort(empty_search, direct_call=direct),
                "native_execution_policy": NativeExecutionPolicy(4),
            }
        ),
        thread_id="run-v4-direct",
        run_id="run-v4-direct",
    )

    assert direct_sources == ["arxiv"] * 5
    assert result["values"]["terminal_error"]["code"] == "deep_research_no_results"


async def _planned_technology_state(config: dict[str, object] | None = None):
    state = initial_state(
        topic=ORIGINAL_TOPIC,
        run_id="run-v4-fetch",
        research_config={"direct_sources": False, **(config or {})},
    )
    context = WorkflowContext(ports={"clock": lambda: date(2026, 7, 15)})
    state["values"] = (await normalize_handler(state, context)).to_dict()["values"]
    state["values"] = (await plan_handler(state, context)).to_dict()["values"]
    return state


@pytest.mark.asyncio
async def test_technology_direct_normalizes_arxiv_https_and_filters_out_of_window_metadata() -> None:
    state = await _planned_technology_state({"direct_sources": True})
    budget = state["values"]["branch_work_items"]["b0"]["budget"]
    state["branch_search"] = {
        "b0": {
            "branch_id": "b0", "stage": "search", "active": True, "mode": "fanout",
            "questions": ["model inference"], "result": [], "errors": [],
            "budget_before": budget, "budget_after": budget,
        }
    }

    async def search(query: str, *, max_results: int):
        del query, max_results
        return []

    async def direct(query: str, source: str):
        del query
        assert source == "arxiv"
        return DirectOutcome(
            source="arxiv",
            status="hit",
            items=[
                {
                    "url": "http://arxiv.org/abs/2501.00001", "title": "Old inference paper",
                    "text": "Old model inference paper. Published: 2025-01-10T00:00:00Z",
                },
                {
                    "url": "http://arxiv.org/abs/2607.00002", "title": "Current inference paper",
                    "text": "Current model inference paper. Published: 2026-07-10T00:00:00Z",
                },
            ],
        )

    patch = await branch_handler(
        "direct", "b0", state,
        WorkflowContext(ports={"search": ResearchSearchPort(search, direct_call=direct)}),
    )
    rows = patch.to_dict()["branch_direct"]["b0"]["result"]

    assert [row["url"] for row in rows] == ["https://arxiv.org/abs/2607.00002"]
    assert rows[0]["published_at"] == "2026-07-10"


@pytest.mark.asyncio
async def test_gateway_seed_enforces_its_allowed_domains() -> None:
    state = await _planned_technology_state()
    budget = state["values"]["branch_work_items"]["b0"]["budget"]
    state["branch_expand"] = {
        "b0": {
            "branch_id": "b0", "stage": "expand", "active": True, "mode": "fanout",
            "questions": ["model inference"],
            "result": [{
                "query": "model inference site:github.com", "question": "model inference",
                "kind": "repository", "allowed_domains": ["github.com"],
            }],
            "errors": [], "budget_before": budget, "budget_after": budget,
        }
    }

    async def search(query: str, *, max_results: int):
        del query, max_results
        return ResearchSearchResults([
            {
                "url": "https://github.com/vllm-project/vllm", "title": "vLLM inference runtime",
                "snippet": "Open source model inference and serving runtime.",
            },
            {
                "url": "https://spam.example/github-roundup", "title": "GitHub model roundup",
                "snippet": "A secondary model inference roundup.",
            },
        ], observation={"provider_attempt_count": 1})

    patch = await branch_handler(
        "search", "b0", state,
        WorkflowContext(ports={"search": ResearchSearchPort(search)}),
    )
    rows = patch.to_dict()["branch_search"]["b0"]["result"]
    assert [row["url"] for row in rows] == ["https://github.com/vllm-project/vllm"]


@pytest.mark.asyncio
async def test_technology_fetch_has_total_budget_metadata_and_navigation_filter() -> None:
    state = await _planned_technology_state({"technology_total_fetch_budget": 15})
    fetched: list[str] = []

    async def search(query: str, *, max_results: int):
        rows = [
            {
                "url": f"https://source.example/{abs(hash(query))}/{index}",
                "title": f"Model inference release {index}",
                "snippet": "The model inference runtime released a supported capability.",
                "published_at": f"2026-07-0{index + 1}",
            }
            for index in range(max_results)
        ]
        rows.append(
            {
                "url": "https://openai.com/navigation",
                "title": "Example workflows and tasks teams can take on with ChatGPT or Codex",
                "snippet": "Contact sales",
            }
        )
        return ResearchSearchResults(rows, observation={"provider_attempt_count": 0})

    async def fetch(url: str):
        fetched.append(url)
        return {
            "url": url,
            "title": "Model inference release",
            "text": "The model inference runtime released a supported capability. " * 8,
        }

    search_context = WorkflowContext(ports={"search": ResearchSearchPort(search)})
    state["branch_expand"] = (await branch_handler("expand", "b0", state, search_context)).to_dict()["branch_expand"]
    search_patch = await branch_handler("search", "b0", state, search_context)
    state["branch_search"] = search_patch.to_dict()["branch_search"]
    state["branch_direct"] = (
        await branch_handler("direct", "b0", state, search_context)
    ).to_dict()["branch_direct"]
    fetch_patch = await branch_handler(
        "fetch", "b0", state, WorkflowContext(ports={"fetch": FetchPort(fetch)})
    )
    documents = fetch_patch.to_dict()["branch_fetch"]["b0"]["result"]

    assert len(fetched) == 3  # 15 total budget / 5 active branches
    assert all("navigation" not in url for url in fetched)
    assert len(documents) == 3
    assert {document["published_at"] for document in documents} <= {
        "2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"
    }


@pytest.mark.asyncio
async def test_technology_fetch_rechecks_date_metadata_discovered_from_page() -> None:
    state = await _planned_technology_state({"technology_total_fetch_budget": 5})
    budget = state["values"]["branch_work_items"]["b0"]["budget"]
    state["branch_search"] = {
        "b0": {
            "branch_id": "b0", "stage": "search", "active": True, "mode": "fanout",
            "questions": ["model inference"], "errors": [],
            "budget_before": budget, "budget_after": budget,
            "result": [{
                "url": "https://openai.com/research/old-runtime",
                "canonical_url": "https://openai.com/research/old-runtime",
                "title": "Model inference runtime release",
                "snippet": "A model inference serving capability release.",
            }],
        }
    }
    state["branch_direct"] = {}

    async def fetch(url: str):
        return {
            "url": url, "title": "Old runtime", "published_at": "2025-01-01",
            "text": "The model inference serving runtime released a supported capability. " * 8,
        }

    patch = await branch_handler(
        "fetch", "b0", state, WorkflowContext(ports={"fetch": FetchPort(fetch)})
    )
    payload = patch.to_dict()["branch_fetch"]["b0"]
    assert payload["result"] == []
    assert "low_quality_content" in {item["error_code"] for item in payload["errors"]}


@pytest.mark.asyncio
async def test_technology_fetch_diversifies_real_serp_and_arxiv_shape() -> None:
    state = await _planned_technology_state({"technology_total_fetch_budget": 15})
    topic = state["values"]["technology_topics"][0]
    budget = state["values"]["branch_work_items"]["b0"]["budget"]
    search_rows = [
        {
            "url": "https://openai.com/research/runtime-release",
            "canonical_url": "https://openai.com/research/runtime-release",
            "title": "Official model inference runtime release",
            "snippet": "Official model inference capability and benchmark update.",
            "question": topic["label"],
            "published_at": "2026-07-10",
        },
        {
            "url": "https://github.com/vllm-project/vllm",
            "canonical_url": "https://github.com/vllm-project/vllm",
            "title": "vLLM open source inference runtime",
            "snippet": "Repository for a model serving and inference runtime.",
            "question": topic["label"],
            "published_at": "2026-07-09",
        },
        *(
            {
                "url": f"https://blog.csdn.net/noise/article/details/{index}",
                "canonical_url": f"https://blog.csdn.net/noise/article/details/{index}",
                "title": f"Model inference roundup {index}",
                "snippet": "Secondary model inference commentary.",
                "question": topic["label"],
                "published_at": "2026-07-08",
            }
            for index in range(39)
        ),
    ]
    direct_rows = [
        {
            "url": f"http://arxiv.org/abs/{'2501' if index == 0 else '2607'}.{10000 + index}",
            "canonical_url": f"http://arxiv.org/abs/{'2501' if index == 0 else '2607'}.{10000 + index}",
            "title": f"Model inference paper {index}",
            "snippet": "A scholarly model inference paper.",
            "question": topic["label"],
            "provider": "arxiv",
            "direct": True,
            "source_kind": "scholarly",
            "published_at": "2025-01-10" if index == 0 else f"2026-07-{index:02d}",
        }
        for index in range(13)
    ]
    direct_rows.append({
        "url": "https://arxiv.org/abs/2607.99999",
        "canonical_url": "https://arxiv.org/abs/2607.99999",
        "title": "EHR foundation model for electronic health records",
        "snippet": "A clinical foundation model for hospital patient records.",
        "question": topic["label"],
        "provider": "arxiv",
        "direct": True,
        "source_kind": "scholarly",
        "published_at": "2026-07-15",
    })
    assert len(search_rows) == 41
    assert len(direct_rows) == 14
    state["branch_search"] = {
        "b0": {
            "branch_id": "b0", "stage": "search", "active": True, "mode": "fanout",
            "questions": [topic["label"]], "result": search_rows, "errors": [],
            "budget_before": budget, "budget_after": budget,
        }
    }
    state["branch_direct"] = {
        "b0": {
            "branch_id": "b0", "stage": "direct", "active": True, "mode": "fanout",
            "questions": [topic["label"]], "result": direct_rows, "errors": [],
            "budget_before": budget, "budget_after": budget,
        }
    }
    fetched: list[str] = []

    async def fetch(url: str):
        fetched.append(url)
        if "arxiv.org" in url:
            raise RuntimeError("simulated direct arxiv fetch failure")
        return {
            "url": url,
            "title": "Fetched authoritative source",
            "text": "This source documents a supported model inference runtime capability. " * 10,
        }

    patch = await branch_handler(
        "fetch", "b0", state, WorkflowContext(ports={"fetch": FetchPort(fetch)})
    )
    documents = patch.to_dict()["branch_fetch"]["b0"]["result"]

    assert len(fetched) == 3
    assert len({url.split("/")[2] for url in fetched}) == 3
    assert "https://openai.com/research/runtime-release" in fetched
    assert "https://github.com/vllm-project/vllm" in fetched
    arxiv = [url for url in fetched if "arxiv.org" in url]
    assert len(arxiv) == 1
    assert arxiv[0].startswith("https://arxiv.org/")
    assert "2501.10000" not in arxiv[0]
    assert "2607.99999" not in fetched
    document_urls = {item["url"] for item in documents}
    assert "https://openai.com/research/runtime-release" in document_urls
    assert "https://github.com/vllm-project/vllm" in document_urls


@pytest.mark.asyncio
async def test_pdf_fetch_timeout_is_bounded_and_fail_closed() -> None:
    state = await _planned_technology_state(
        {"technology_total_fetch_budget": 5, "technology_pdf_timeout_s": 0.05}
    )
    context = WorkflowContext(ports={})
    state["branch_expand"] = (await branch_handler("expand", "b0", state, context)).to_dict()["branch_expand"]
    state["branch_search"] = {
        "b0": {
            "branch_id": "b0",
            "stage": "search",
            "active": True,
            "mode": "fanout",
            "questions": ["model inference"],
            "result": [
                {
                    "url": "https://slow.example/paper.pdf",
                    "canonical_url": "https://slow.example/paper.pdf",
                    "title": "Model inference paper",
                    "question": "基础模型与推理",
                }
            ],
            "errors": [],
            "budget_before": state["values"]["branch_work_items"]["b0"]["budget"],
            "budget_after": state["values"]["branch_work_items"]["b0"]["budget"],
        }
    }
    state["branch_direct"] = {
        "b0": {
            **state["branch_search"]["b0"],
            "stage": "direct",
            "result": [],
        }
    }

    async def slow_fetch(url: str):
        del url
        await asyncio.sleep(0.5)
        return {}

    started = time.perf_counter()
    patch = await branch_handler(
        "fetch", "b0", state, WorkflowContext(ports={"fetch": FetchPort(slow_fetch)})
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 0.25
    payload = patch.to_dict()["branch_fetch"]["b0"]
    assert payload["result"] == []
    assert {error["error_code"] for error in payload["errors"]} == {"fetch_failure"}


@pytest.mark.asyncio
async def test_presentation_localization_cannot_add_numbers_or_change_citations() -> None:
    finding = TechnologyFinding(
        finding_id="f1",
        entity="RuntimeX",
        topic_kind="training_inference_system",
        topic_label="训练与推理系统",
        claim_ids=("c1",),
        published_at="2026-07-01",
        recency_score=1.0,
        impact_score=2,
        maturity="emerging",
        evidence_quality=3,
        winning_citation_ids=(2, 5),
        total_score=0.8,
        statement="RuntimeX reduced benchmark latency by 2x",
    )

    async def unsafe_localizer(prompt: str) -> str:
        assert "RuntimeX reduced benchmark latency by 2x" in prompt
        return '{"items":[{"finding_id":"f1","core_change_zh":"RuntimeX 与 NewBrand 联合将延迟降低 99x。"}]}'

    localized = await _localize_findings(
        (finding,), WorkflowContext(ports={"llm": ResearchLLMPort(unsafe_localizer)})
    )

    assert localized[0].winning_citation_ids == (2, 5)
    assert "99" not in localized[0].localized_statement
    assert "NewBrand" not in localized[0].localized_statement
    assert "2x" in localized[0].localized_statement


@pytest.mark.asyncio
async def test_supervised_fine_tuning_fallback_remains_specific() -> None:
    finding = TechnologyFinding(
        finding_id="f-unibrowse", entity="UNIBROWSE", topic_kind="multimodal",
        topic_label="原生多模态", claim_ids=("c1",), published_at="2026-07-12",
        recency_score=1.0, impact_score=2, maturity="experimental", evidence_quality=2,
        winning_citation_ids=(1,), total_score=0.7,
        statement="UNIBROWSE trains a 35B-scale agent via supervised fine-tuning.",
    )
    localized = await _localize_findings((finding,), WorkflowContext())
    assert localized[0].localized_statement == "UNIBROWSE 通过监督微调训练了 35B 规模的智能体。"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entity", "topic_kind", "statement", "expected"),
    (
        (
                "Gemini API", "multimodal",
                "The Gemini API added developer logs support for the Interactions API on July 6, 2026.",
                "Gemini API 为 Interactions API 新增开发者日志支持，使相关调用过程能够被记录和检查。",
            ),
            (
                "Gemini Omni Flash", "multimodal",
                "Gemini Omni Flash was released in public preview on June 30, 2026.",
                "Gemini Omni Flash 已进入公开预览阶段，可用于受限范围的技术验证。",
            ),
            (
                "Google gemini-omni-flash-preview", "multimodal",
                "Google released gemini-omni-flash-preview in public preview on June 30, 2026.",
                "Google gemini-omni-flash-preview 已进入公开预览阶段，可用于受限范围的技术验证。",
            ),
            (
                "Gemini 3.5 Flash", "multimodal",
                "Gemini 3.5 Flash was released for Gemini API and Vertex AI.",
                "Gemini 3.5 Flash 已面向 Gemini API 和 Vertex AI 发布，可通过这两个平台进行受限范围验证。",
            ),
            (
                "SLEUTH", "agent", "SLEUTH uses a structured epistemic working memory.",
                "SLEUTH 采用结构化认识论工作记忆来组织来源事实与推理状态。",
        ),
            (
                "Torchtitan", "training_inference_system",
                "Torchtitan’s graph_trainer added a graph-based CPU activation-offloading pass.",
                "Torchtitan 的 graph_trainer 新增基于图执行的 CPU 激活卸载处理流程。",
            ),
            (
                "PyTorch 2.11", "training_inference_system",
                "PyTorch 2.11 added differentiable collectives for distributed training.",
                "PyTorch 2.11 加入用于分布式训练的 Differentiable Collectives，以支持可微分的集合通信操作。",
            ),
            (
                "PyTorch Monarch", "training_inference_system",
                "PyTorch Monarch was brought to AMD GPUs on ROCm.",
                "PyTorch Monarch 已扩展到 AMD GPU 的 ROCm 平台，可用于异构训练系统验证。",
            ),
            (
                "torchcomms", "training_inference_system",
                "torchcomms is a new communications backend for PyTorch Distributed.",
                "torchcomms 成为 PyTorch Distributed 的新通信后端，面向大规模集群训练通信。",
            ),
            (
                "Gemma 4 in vLLM", "model_inference",
                "Gemma 4 in vLLM uses Unified FlashAttention across all layers.",
                "Gemma 4 in vLLM 的所有模型层均统一采用 Unified FlashAttention 机制。",
            ),
        (
            "Kimi K2.5", "open_infrastructure",
            "Kimi K2.5 is an open-source, native multimodal agentic model.",
            "Kimi K2.5 同时具备开源、原生多模态与智能体模型属性。",
        ),
    ),
)
async def test_common_live_technology_changes_have_specific_safe_fallbacks(
    entity: str, topic_kind: str, statement: str, expected: str,
) -> None:
    finding = TechnologyFinding(
        finding_id=f"f-{entity}", entity=entity, topic_kind=topic_kind,
        topic_label="AI 技术", claim_ids=("c1",), published_at="2026-07-10",
        recency_score=1.0, impact_score=1, maturity="emerging", evidence_quality=2,
        winning_citation_ids=(1,), total_score=0.6, statement=statement,
    )

    localized = await _localize_findings((finding,), WorkflowContext())

    assert localized[0].localized_statement == expected


def test_localization_cannot_add_bare_dates_or_chinese_units() -> None:
    finding = TechnologyFinding(
        finding_id="f-no-number", entity="RuntimeX", topic_kind="agent", topic_label="Agent",
        claim_ids=("c1",), published_at=None, recency_score=0.0, impact_score=1,
        maturity="emerging", evidence_quality=2, winning_citation_ids=(1,), total_score=0.4,
        statement="RuntimeX released a supported agent capability.",
    )
    with pytest.raises(ValueError, match="introduced a number"):
        _parse_localized_changes(
            '{"items":[{"finding_id":"f-no-number","core_change_zh":"RuntimeX 于 2026 年发布智能体能力，效率提升 50 倍。"}]}',
            (finding,),
        )


@pytest.mark.asyncio
async def test_partial_or_english_heavy_localization_is_rejected_with_distinct_fallbacks() -> None:
    first = TechnologyFinding(
        finding_id="f1", entity="RuntimeX", topic_kind="training_inference_system",
        topic_label="训练与推理系统", claim_ids=("c1",), published_at="2026-07-01",
        recency_score=1.0, impact_score=2, maturity="emerging", evidence_quality=3,
        winning_citation_ids=(1,), total_score=0.8,
        statement="RuntimeX reduced benchmark latency by 2x",
    )
    second = TechnologyFinding(
        finding_id="f2", entity="AgentKit", topic_kind="agent", topic_label="Agent 与工具调用",
        claim_ids=("c2",), published_at="2026-07-02", recency_score=1.0,
        impact_score=1, maturity="emerging", evidence_quality=2,
        winning_citation_ids=(2,), total_score=0.65,
        statement="AgentKit released supported agent tool calling.",
    )
    with pytest.raises(ValueError, match="incomplete"):
        _parse_localized_changes(
            '{"items":[{"finding_id":"f1","core_change_zh":"RuntimeX 基准延迟降低 2x，形成可核验的性能变化。"}]}',
            (first, second),
        )
    with pytest.raises(ValueError, match="English-heavy"):
        _parse_localized_changes(
            '{"items":[{"finding_id":"f1","core_change_zh":"RuntimeX reduced benchmark latency by 2x benchmark latency reduced benchmark latency 这是中文说明文字内容。"}]}',
            (first,),
        )

    async def partial_localizer(prompt: str) -> str:
        del prompt
        return '{"items":[{"finding_id":"f1","core_change_zh":"RuntimeX 基准延迟降低 2x，形成可核验的性能变化。"}]}'

    localized = await _localize_findings(
        (first, second), WorkflowContext(ports={"llm": ResearchLLMPort(partial_localizer)})
    )
    assert localized[0].localized_statement != localized[1].localized_statement
    assert localized[0].localized_statement.startswith("RuntimeX")
    assert localized[1].localized_statement.startswith("AgentKit")


@pytest.mark.asyncio
async def test_localization_retries_only_items_rejected_from_the_first_batch() -> None:
    first = TechnologyFinding(
        finding_id="f1", entity="RuntimeX", topic_kind="training_inference_system",
        topic_label="训练与推理系统", claim_ids=("c1",), published_at="2026-07-01",
        recency_score=1.0, impact_score=2, maturity="emerging", evidence_quality=3,
        winning_citation_ids=(1,), total_score=0.8,
        statement="RuntimeX reduced benchmark latency by 2x",
    )
    second = TechnologyFinding(
        finding_id="f2", entity="AgentKit", topic_kind="agent", topic_label="智能体工具调用",
        claim_ids=("c2",), published_at="2026-07-02", recency_score=1.0,
        impact_score=1, maturity="emerging", evidence_quality=2,
        winning_citation_ids=(2,), total_score=0.65,
        statement="AgentKit released supported agent tool calling.",
    )
    calls = 0

    async def retrying_localizer(prompt: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return '{"items":[{"finding_id":"f1","core_change_zh":"RuntimeX 将基准延迟降低 2x，形成有引用支持的性能变化。"}]}'
        assert '"finding_id": "f2"' in prompt
        assert '"finding_id": "f1"' not in prompt
        return '{"items":[{"finding_id":"f2","core_change_zh":"AgentKit 发布了支持智能体工具调用的新版本。"}]}'

    localized = await _localize_findings(
        (first, second),
        WorkflowContext(ports={"llm": ResearchLLMPort(retrying_localizer)}),
    )

    assert calls == 2
    assert localized[0].localized_statement.startswith("RuntimeX")
    assert localized[1].localized_statement.startswith("AgentKit 发布")


def test_localization_may_preserve_entity_from_source_title() -> None:
    finding = TechnologyFinding(
        finding_id="f-title", entity="A2A", topic_kind="agent", topic_label="Agent",
        claim_ids=("c1",), published_at="2026-07-01", recency_score=1.0,
        impact_score=1, maturity="experimental", evidence_quality=2,
        winning_citation_ids=(1,), total_score=0.6,
        statement="Preserving multimodal signals across agent boundaries improves cross-modal reasoning.",
    )
    parsed = _parse_localized_changes(
        '{"items":[{"finding_id":"f-title","core_change_zh":"A2A 保留智能体边界之间的多模态信号，以改善跨模态推理。"}]}',
        (finding,),
    )
    assert parsed["f-title"].startswith("A2A")


@pytest.mark.asyncio
async def test_cite_handler_enforces_finding_set_gate_on_production_route(monkeypatch) -> None:
    async def fake_v3_cite(current_state, context):
        del context
        return StatePatch({"values": current_state["values"]})

    monkeypatch.setattr(
        "deskpet.workflows.definitions.deep_research_v4_nodes.v3.cite_handler", fake_v3_cite
    )
    cases = (
        (_handler_gate_findings(2), "technology_findings_below_minimum"),
        (_handler_gate_findings(same_score=True), "technology_scores_indistinguishable"),
        (_handler_gate_findings(zero_recency=True), "technology_recency_unresolved"),
        (_handler_gate_findings(unknown_maturity=True), "technology_maturity_unresolved"),
    )
    for findings, expected_code in cases:
        state = await _planned_technology_state()
        state["values"]["report_payload"] = {"status": "completed", "citations": []}
        state["values"]["claim_quality"] = {"published_claims": [], "decisions": []}
        monkeypatch.setattr(
            "deskpet.workflows.definitions.deep_research_v4_nodes.build_ranked_findings",
            lambda **kwargs: findings,
        )
        values = (await cite_handler(copy.deepcopy(state), WorkflowContext())).to_dict()["values"]
        assert values["report_payload"]["status"] == "no_results"
        assert values["report_payload"]["reason_code"] == "technology_finding_set_gate_failed"
        assert expected_code in values["technology_audit_payload"]["finding_gate_codes"]
        routed = {**state, "values": values}
        assert await post_cite_route(routed, WorkflowContext()) == "insufficient_evidence"


@pytest.mark.asyncio
async def test_cite_keeps_bounded_reserve_until_after_generic_pruning(monkeypatch) -> None:
    state = await _planned_technology_state()
    state["values"]["claim_quality"] = {"published_claims": [], "decisions": []}
    state["values"]["report_payload"] = {
        "status": "completed",
        "citations": [
            {"citation_id": index, "title": f"Source {index}", "url": f"https://source{index}.example"}
            for index in range(1, 13)
        ],
    }
    kinds = ("model_inference", "agent", "multimodal", "training_inference_system")
    ranked = tuple(
        TechnologyFinding(
            finding_id=f"reserve-{index}",
            entity=f"ReserveEntity{index}",
            topic_kind=kinds[index % len(kinds)],
            topic_label=f"Theme {index % len(kinds)}",
            claim_ids=(f"c{index}",),
            published_at=f"2026-07-{index:02d}",
            recency_score=1.0,
            impact_score=1 + index % 3,
            maturity="emerging" if index % 2 else "experimental",
            evidence_quality=2 + index % 2,
            winning_citation_ids=(index,),
            total_score=0.4 + index * 0.02,
            statement=f"ReserveEntity{index} released a supported technology capability.",
        )
        for index in range(1, 13)
    )

    async def fake_v3_cite(current_state, context):
        del context
        return StatePatch({"values": current_state["values"]})

    async def fake_localize(findings, context, *, timeout_s):
        del context, timeout_s
        return tuple(
            replace(
                finding,
                localized_statement=(
                    f"{finding.entity} 出现了可由引用核验的技术进展。"
                    if index <= 4
                    else f"{finding.entity} 发布了第 {index} 项具体能力更新，并提供完整的技术验证路径。"
                ),
            )
            for index, finding in enumerate(findings, 1)
        )

    monkeypatch.setattr(
        "deskpet.workflows.definitions.deep_research_v4_nodes.v3.cite_handler", fake_v3_cite
    )
    monkeypatch.setattr(
        "deskpet.workflows.definitions.deep_research_v4_nodes.build_ranked_findings",
        lambda **kwargs: ranked,
    )
    monkeypatch.setattr(
        "deskpet.workflows.definitions.deep_research_v4_nodes._localize_findings",
        fake_localize,
    )

    values = (await cite_handler(state, WorkflowContext())).to_dict()["values"]

    assert values["report_payload"]["status"] == "completed"
    assert [item["entity"] for item in values["technology_findings"]] == [
        f"ReserveEntity{index}" for index in range(5, 13)
    ]


@pytest.mark.asyncio
async def test_cite_restores_source_date_and_builds_professional_chinese_finding(monkeypatch) -> None:
    state = await _planned_technology_state()
    topics = state["values"]["technology_topics"]
    specs = (
        ("RuntimeX", "RuntimeX released a model inference runtime with supported interoperability.", "https://openai.com/runtime-x", "2026-07-01"),
        ("AgentKit", "AgentKit launched a beta agent tool calling capability.", "https://agentkit.example/release", "2026-07-02"),
        ("VisionCore", "VisionCore preprint prototype supports multimodal vision and audio.", "https://arxiv.org/abs/2607.10003", "2026-07-03"),
        ("ServeFast", "ServeFast stable production inference system reduced latency by 2x.", "https://nvidia.com/serve-fast", "2026-07-04"),
            ("MeshStack", "MeshStack released an open source AI framework repository.", "https://github.com/example/mesh-stack", "2026-01-20"),
    )
    state["values"].update(
        {
            "ranked_evidence": [
                {
                    "canonical_url": url, "url": url, "content_hash": f"hash-{index}",
                    "published_at": published_at, "text": statement,
                }
                for index, (_, statement, url, published_at) in enumerate(specs, 1)
            ],
            "citation_sources": [
                {
                    "citation_id": index, "canonical_url": url, "url": url,
                    "title": f"{entity} release", "content_hash": f"hash-{index}",
                }
                for index, (entity, _, url, _) in enumerate(specs, 1)
            ],
            "evidence_passages": [
                {
                    "passage_id": f"p{index}", "source_citation_id": index,
                    "source_content_hash": f"hash-{index}", "canonical_url": url,
                    "question_id": topics[index - 1]["label"], "text": statement,
                    "start": 0, "end": len(statement), "relevance": 1.0,
                }
                for index, (_, statement, url, _) in enumerate(specs, 1)
            ],
            "claim_quality": {
                "published_claims": [
                    {
                        "claim_id": f"c{index}", "text": statement,
                        "kind": "factual", "citation_ids": [index],
                    }
                    for index, (_, statement, _, _) in enumerate(specs, 1)
                ],
                "decisions": [
                    {
                        "claim_id": f"c{index}", "supported": True,
                        "winning_passage_ids": [f"p{index}"],
                        "winning_source_citation_ids": [index],
                    }
                    for index in range(1, 6)
                ],
            },
            "report_payload": {
                "status": "completed",
                "citations": [
                    {"citation_id": index, "title": f"{entity} release", "url": url}
                    for index, (entity, _, url, _) in enumerate(specs, 1)
                ],
                "coverage": {"provider_attempts": ["debug"], "raw_claims": ["debug"]},
                "errors": [{"query": "secret raw query", "passages": ["raw"]}],
            },
        }
    )

    async def fake_v3_cite(current_state, context):
        del context
        return StatePatch({"values": current_state["values"]})

    monkeypatch.setattr(
        "deskpet.workflows.definitions.deep_research_v4_nodes.v3.cite_handler",
        fake_v3_cite,
    )

    async def professional_localizer(prompt: str) -> str:
        del prompt
        return json.dumps({"items": [
            {"finding_id": "finding:bfd7d1f474cb", "core_change_zh": "RuntimeX 发布了支持互操作性的模型推理运行时。"},
            {"finding_id": "finding:c6fed9ddbd7a", "core_change_zh": "AgentKit 启动了支持智能体工具调用的测试版本。"},
            {"finding_id": "finding:9f709acf797d", "core_change_zh": "VisionCore 预印本原型支持多模态视觉与音频处理。"},
            {"finding_id": "finding:e537e8436f84", "core_change_zh": "ServeFast 稳定生产推理系统降低了延迟并进入生产使用阶段。"},
            {"finding_id": "finding:a141983e19a7", "core_change_zh": "MeshStack 发布了用于 AI 基础设施的开源框架仓库。"},
        ]}, ensure_ascii=False)

    patch = await cite_handler(
        state,
        WorkflowContext(ports={"llm": ResearchLLMPort(professional_localizer)}),
    )
    values = patch.to_dict()["values"]
    findings = values["technology_findings"]
    finding = next(item for item in findings if item["entity"] == "RuntimeX")
    markdown = values["report_payload"]["report_md"]
    assert markdown, json.dumps(values.get("technology_audit_payload"), ensure_ascii=False)

    assert len(findings) == 5
    assert len({item["total_score"] for item in findings}) == 5
    assert len({item["topic_kind"] for item in findings}) == 5
    assert finding["published_at"] == "2026-07-01"
    assert finding["recency_score"] == 1.0
    assert finding["impact_score"] >= 1
    assert finding["maturity"] == "emerging"
    assert finding["winning_citation_ids"] == [1]
    assert "RuntimeX released a model inference runtime" not in markdown
    assert "RuntimeX 发布了支持互操作性的模型推理运行时" in markdown
    assert "## 一页式执行摘要" in markdown
    assert "## 分主题 Top 技术" in markdown
    assert "支持条目：RuntimeX" in markdown
    assert "### 组合建议" in markdown
    assert "## 分主题 Top 技术" in markdown
    assert "## 方法与局限" in markdown
    assert "暂无条目可直接进入限界 PoC" in markdown
    runtime_match = re.search(
        r"#### \d+\. RuntimeX(?P<body>.*?)(?=\n#### \d+\.|\n## 方法与局限)",
        markdown,
        re.S,
    )
    assert runtime_match is not None
    runtime_block = runtime_match.group("body")
    assert "先补齐一手来源与独立来源，再决定是否进入限界 PoC" in runtime_block
    assert "适合开展限界 PoC" not in runtime_block
    assert "。。" not in markdown
    assert "#### 1." in markdown
    assert "#### 5." in markdown
    assert [int(value) for value in re.findall(r"^#### (\d+)\.", markdown, re.M)] == [1, 2, 3, 4, 5]
    assert "- 核心变化：" in markdown
    assert "- 价值判断：" in markdown
    assert "winning evidence" not in markdown
    assert "provider_attempts" not in markdown
    assert values["technology_audit_payload"]["finding_gate_codes"] == []
    assert set(values["report_payload"]) == {
        "schema_version", "status", "reason_code", "topic", "report_md", "body_md",
        "intent_profile", "public_findings", "citation_count", "report_hash",
    }

    state["values"] = values
    state["values"] = (await persist_handler(state, WorkflowContext())).to_dict()["values"]
    delivered = (await finalize_handler(state, WorkflowContext())).to_dict()["values"]["delivery_intents"]

    forbidden = {
        "provider_attempts", "published_claims", "coverage", "errors", "claims", "raw_claims",
        "query", "url", "passages", "citation_sources", "technology_audit_payload",
    }

    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value)) if value else set()
        return set()

    assert keys(delivered).isdisjoint(forbidden)

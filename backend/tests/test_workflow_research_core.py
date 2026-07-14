from __future__ import annotations

import asyncio
import copy
import inspect
import json
from pathlib import Path

import pytest

from deskpet.tools import research_tools as legacy
from deskpet.workflows.definitions import deep_research_nodes as nodes
from deskpet.workflows.definitions.research_core import (
    FetchPort,
    ResearchCoreConfig,
    ResearchCoreState,
    ResearchLLMPort,
    ResearchPorts,
    ResearchSearchPort,
    _passes_topic_anchor_gate,
    legacy_ports,
    run_research_core,
)
from deskpet.workflows import WorkflowContext
from deskpet.workflows.store import BlobStore


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.responses.pop(0)


def _config(**overrides) -> ResearchCoreConfig:
    values = {
        "max_sub_questions": 3,
        "max_urls_per_query": 2,
        "max_total_passages": 4,
        "min_passage_chars": 20,
        "max_rounds": 1,
        "query_expansion": False,
        "site_directed": False,
        "source_packs": False,
        "direct_sources": False,
        "rerank_mode": "off",
    }
    values.update(overrides)
    return ResearchCoreConfig(**values)


def _state() -> ResearchCoreState:
    return ResearchCoreState(
        request_topic="stable topic",
        llm_topic="stable topic",
        mode="standard",
        route={
            "engines_hit": [],
            "direct_sources_hit": [],
            "fallback": "fake",
            "source_packs_enabled": False,
            "source_packs_hit": [],
            "source_pack_queries": 0,
        },
    )


@pytest.mark.asyncio
async def test_stage_contracts_are_reentrant_and_product_side_effect_free() -> None:
    llm = FakeLLM(
        [
            json.dumps(["question one?"]),
            "# stable topic\n\n## TL;DR\n\nSupported finding [^1].",
        ]
    )

    async def search(query: str, *, max_results: int):
        assert query == "question one?"
        assert max_results == 2
        return [{"url": "https://example.com/a", "title": "A", "snippet": ""}]

    async def extract(url: str):
        return {
            "ok": True,
            "url": url,
            "title": "A",
            "text": "question one stable topic primary source evidence " * 20,
            "fetched_at": 123.0,
        }

    ports = ResearchPorts(
        llm=ResearchLLMPort(complete=llm),
        search=ResearchSearchPort(search_call=search),
        fetch=FetchPort(extractor=extract),
    )
    config = _config()
    state = _state()
    context = WorkflowContext(
        ports={"llm": ports.llm, "search": ports.search, "fetch": ports.fetch}
    )
    assert ResearchPorts.from_workflow_context(context) == ports

    stage_functions = [
        nodes.plan_node,
        nodes.expand_node,
        nodes.search_node,
        nodes.fetch_extract_node,
        nodes.direct_node,
        nodes.score_rerank_node,
        nodes.gap_node,
        nodes.synth_node,
        nodes.citation_node,
    ]
    assert all(inspect.iscoroutinefunction(stage) for stage in stage_functions)

    await nodes.plan_node(state, ports, config)
    await nodes.expand_node(state, ports, config)
    await nodes.search_node(state, ports, config)
    await nodes.fetch_extract_node(state, ports, config)
    await nodes.score_rerank_node(state, ports, config, finalize=False)
    await nodes.direct_node(state, ports, config)
    await nodes.gap_node(state, ports, config)
    await nodes.score_rerank_node(state, ports, config)
    await nodes.synth_node(state, ports, config)
    await nodes.citation_node(state, ports, config)

    assert state.sub_questions == ["question one?"]
    assert [passage.citation.url for passage in state.passages] == [
        "https://example.com/a"
    ]
    assert state.cite_result["ok"] is True
    assert "[^1]: [A](https://example.com/a)" in state.report_md
    assert not hasattr(state, "artifact_refs")
    assert not hasattr(state, "receipt_refs")


@pytest.mark.asyncio
async def test_rerank_failure_drops_unrelated_fallback_passages() -> None:
    async def complete(prompt: str) -> str:
        if "JSON" in prompt:
            return json.dumps(["AI Agent Harness observability and recovery"])
        return "# Report\n\nThis must not be synthesized from unrelated evidence."

    async def rerank(prompt: str) -> str:
        raise RuntimeError("LLM HTTP 503 Service Unavailable")

    async def search(query: str, *, max_results: int):
        return [{"url": "https://baike.sogou.com/banana", "title": "Banana"}]

    async def extract(url: str):
        return {
            "ok": True,
            "url": url,
            "title": "Banana",
            "text": "Bananas are fruit grown in tropical climates. " * 20,
            "fetched_at": 123.0,
        }

    ports = ResearchPorts(
        llm=ResearchLLMPort(complete=complete, rerank=rerank),
        search=ResearchSearchPort(search_call=search),
        fetch=FetchPort(extractor=extract),
    )
    state = ResearchCoreState(
        request_topic="2026 AI Agent Harness best practices",
        llm_topic="2026 AI Agent Harness best practices",
        mode="deep",
        route={"fallback": "test"},
    )
    await nodes.plan_node(state, ports, _config(rerank_mode="llm"))
    await nodes.search_node(state, ports, _config(rerank_mode="llm"))
    await nodes.fetch_extract_node(state, ports, _config(rerank_mode="llm"))
    await nodes.score_rerank_node(state, ports, _config(rerank_mode="llm"))

    assert state.reranker == "llm_failed"
    assert state.passages == []
    assert any(error.startswith("dropped_irrelevant:") for error in state.errors)


def test_agent_harness_source_pack_targets_primary_ecosystem_sources() -> None:
    queries = legacy._source_pack_queries_for("2026 AI Agent Harness best practices")

    assert queries
    assert {name for name, _ in queries} == {"ai_agent_harness"}
    joined = " ".join(query for _, query in queries)
    assert "anthropic.com" in joined
    assert "openai.com" in joined
    assert "langchain.com" in joined


def test_topic_anchor_gate_rejects_cjk_and_high_authority_cross_topic_pages() -> None:
    unrelated = legacy.Passage(
        citation=legacy.Citation(
            n=0,
            url="https://nature.com/fruit",
            title="Banana crop genetics",
            snippet="fruit",
            fetched_at=1.0,
            authority=9.5,
        ),
        text="Banana fruit genetics and tropical crop production. " * 20,
        score=9.0,
        dims={"relevance": 10.0},
    )
    cjk_state = ResearchCoreState(
        request_topic="新能源汽车补贴政策",
        llm_topic="新能源汽车补贴政策",
        mode="deep",
        route={},
    )
    single_anchor_state = ResearchCoreState(
        request_topic="quantum",
        llm_topic="quantum",
        mode="deep",
        route={},
    )

    assert _passes_topic_anchor_gate(cjk_state, unrelated) is False
    assert _passes_topic_anchor_gate(single_anchor_state, unrelated) is False


def test_planner_drift_cannot_validate_its_own_cross_topic_evidence() -> None:
    state = ResearchCoreState(
        request_topic="AI Agent Harness",
        llm_topic="AI Agent Harness",
        mode="deep",
        route={},
        sub_questions=["banana crop genetics"],
    )
    passage = legacy.Passage(
        citation=legacy.Citation(
            n=0,
            url="https://example.com/banana",
            title="Banana crop genetics",
            snippet="banana",
            fetched_at=1.0,
        ),
        text="Banana crop genetics and tropical fruit production. " * 20,
        score=8.0,
        dims={"relevance": 10.0},
    )

    assert _passes_topic_anchor_gate(state, passage) is False


@pytest.mark.asyncio
async def test_partial_llm_rerank_verifies_only_scored_passages() -> None:
    state = ResearchCoreState(
        request_topic="alpha beta",
        llm_topic="alpha beta",
        mode="deep",
        route={},
    )
    state.passages = [
        legacy.Passage(
            citation=legacy.Citation(
                n=0,
                url=f"https://example.com/{index}",
                title=f"Unrelated {index}",
                snippet="unrelated",
                fetched_at=1.0,
            ),
            text="unrelated material " * 30,
            score=5.0,
            dims={"authority": 3.0, "recency": 3.0, "relevance": 8.0, "depth": 5.0},
        )
        for index in range(4)
    ]

    async def complete(prompt: str) -> str:
        return "unused"

    async def rerank(prompt: str) -> str:
        return json.dumps([{"id": 1, "score": 8}, {"id": 2, "score": 8}])

    async def search(query: str, *, max_results: int):
        return []

    ports = ResearchPorts(
        llm=ResearchLLMPort(complete=complete, rerank=rerank),
        search=ResearchSearchPort(search_call=search),
        fetch=FetchPort(),
    )

    await nodes.score_rerank_node(state, ports, _config(rerank_mode="llm"))

    assert state.reranker == "llm"
    assert [passage.citation.url for passage in state.passages] == [
        "https://example.com/0",
        "https://example.com/1",
    ]
    assert all(passage.dims.get("llm_rerank_verified") for passage in state.passages)


@pytest.mark.asyncio
async def test_two_concurrent_runs_have_isolated_js_render_budgets(monkeypatch) -> None:
    shell = "<html><body><div id='app'></div><script>" + ("x" * 21000) + "</script></body></html>"
    monkeypatch.setattr(legacy, "_js_render_enabled", lambda: True)
    monkeypatch.setattr(legacy, "_jina_enabled", lambda: False)
    calls: list[str] = []

    class Response:
        text = shell

        def raise_for_status(self) -> None:
            return None

    class Client:
        async def get(self, url: str, **kwargs):
            return Response()

        async def aclose(self) -> None:
            return None

    async def render(url: str) -> None:
        calls.append(url)
        await asyncio.sleep(0)
        return None

    monkeypatch.setattr(legacy, "_js_render_dispatch", render)
    first = FetchPort(max_js_renders=2)
    second = FetchPort(max_js_renders=2)

    async def run(prefix: str, port: FetchPort) -> None:
        await asyncio.gather(
            *[
                legacy.default_extract(
                    f"https://{prefix}.example/{index}",
                    client=Client(),
                    fetch_port=port,
                )
                for index in range(5)
            ]
        )

    await asyncio.gather(run("first", first), run("second", second))

    assert first.js_renders_used == 2
    assert second.js_renders_used == 2
    assert sum("first.example" in url for url in calls) == 2
    assert sum("second.example" in url for url in calls) == 2


def _normalized_report(report: legacy.ResearchReport) -> dict:
    payload = copy.deepcopy(report.as_dict())
    payload["coverage"]["elapsed_ms_per_stage"] = {
        key: 0 for key in payload["coverage"]["elapsed_ms_per_stage"]
    }
    return payload


@pytest.mark.asyncio
async def test_flat_legacy_and_extracted_pipeline_have_output_parity(monkeypatch) -> None:
    monkeypatch.setattr(legacy, "_query_expansion_enabled", lambda: False)
    monkeypatch.setattr(legacy, "_site_directed_enabled", lambda: False)
    monkeypatch.setattr(legacy, "_source_packs_enabled", lambda: False)
    monkeypatch.setattr(legacy, "_direct_sources_enabled", lambda: False)
    monkeypatch.setattr(legacy, "_rerank_mode", lambda: "off")
    monkeypatch.setattr(legacy, "_RERANK_LLM_CALL", None)
    monkeypatch.setattr(legacy, "_SEMANTIC_SCORER", None)

    plan = json.dumps(["evidence question?"])
    synth = "# parity topic\n\n## TL;DR\n\nSame supported result [^1]."

    async def search(query: str, *, max_results: int):
        return [{"url": "https://example.com/source", "title": "Source", "snippet": ""}]

    async def extract(url: str):
        return {
            "ok": True,
            "url": url,
            "title": "Source",
            "text": "parity topic source evidence " * 30,
            "fetched_at": 456.0,
            "date": "2026-07-10",
        }

    config = ResearchCoreConfig.from_legacy(
        max_sub_questions=3,
        max_urls_per_query=2,
        max_total_passages=4,
        min_passage_chars=20,
        max_rounds=1,
    )
    extracted = await run_research_core(
        "parity topic",
        ports=legacy_ports(
            llm_call=FakeLLM([plan, synth]), search=search, extract=extract
        ),
        config=config,
    )
    adapted = await legacy.deepresearch(
        "parity topic",
        llm_call=FakeLLM([plan, synth]),
        search=search,
        extract=extract,
        max_sub_questions=3,
        max_urls_per_query=2,
        max_total_passages=4,
        min_passage_chars=20,
    )

    assert adapted.report_md.encode("utf-8") == extracted.report_md.encode("utf-8")
    assert _normalized_report(adapted) == _normalized_report(extracted)


@pytest.mark.asyncio
async def test_agent_reach_degradation_preserves_generic_evidence(monkeypatch) -> None:
    from deskpet.tools import research_sources

    search_calls: list[str] = []

    async def search(query: str, *, max_results: int):
        search_calls.append(query)
        return [{"url": "https://example.com/repository-guide", "title": "Guide"}]

    async def extract(url: str):
        return {
            "ok": True,
            "url": url,
            "title": "Agent harness repository guide",
            "text": "GitHub repository agent harness evidence and comparison " * 30,
            "fetched_at": 456.0,
            "extractor": "scrapling+trafilatura",
        }

    async def reach_degraded(query: str, *, max_results: int = 3):
        return (
            [],
            "degraded",
            "read_failed",
            "Jina Reader",
            "github",
            {
                "github": {"status": "warn", "active_backend": None},
                "web": {"status": "ok", "active_backend": "Jina Reader"},
            },
        )

    monkeypatch.setattr(
        research_sources, "agent_reach_search_with_status", reach_degraded
    )
    report = await run_research_core(
        "compare https://github.com/Panniantong/Agent-Reach",
        ports=legacy_ports(
            llm_call=FakeLLM(
                [
                    json.dumps([
                        "compare https://github.com/Panniantong/Agent-Reach"
                    ]),
                    "# Repository report\n\n## TL;DR\n\nSupported comparison [^1].",
                ]
            ),
            search=search,
            extract=extract,
        ),
        config=_config(direct_sources=True),
    )

    route = report.coverage["route"]["agent_reach"]
    assert search_calls == ["compare https://github.com/Panniantong/Agent-Reach"]
    assert route["planned_channels"] == ["github"]
    assert route["hits"] == []
    assert route["degraded"] == [
        {
            "url": "https://github.com/Panniantong/Agent-Reach",
            "channel": "github",
            "active_backend": "Jina Reader",
            "status": "degraded",
            "reason_code": "read_failed",
        }
    ]
    canonical_json = json.dumps(route, ensure_ascii=True, sort_keys=True)
    assert "Authorization" not in canonical_json


@pytest.mark.asyncio
async def test_direct_channel_hit_clears_superseded_no_search_error(monkeypatch) -> None:
    from deskpet.tools import research_sources

    async def search(query: str, *, max_results: int):
        return []

    async def reach_hit(query: str, *, max_results: int = 3):
        return [
            {
                "ok": True,
                "url": "https://github.com/Panniantong/Agent-Reach",
                "title": "Panniantong/Agent-Reach",
                "text": "Agent-Reach public repository metadata and agent channel scaffolding " * 8,
                "fetched_at": 1.0,
                "source": "github",
            }
        ], "hit", None, "Jina Reader", "github", {
            "github": {"status": "warn", "active_backend": None},
            "web": {"status": "ok", "active_backend": "Jina Reader"},
        }

    monkeypatch.setattr(
        research_sources, "agent_reach_search_with_status", reach_hit
    )
    report = await run_research_core(
        "https://github.com/Panniantong/Agent-Reach metadata",
        ports=legacy_ports(
            llm_call=FakeLLM(
                [
                    json.dumps([
                        "https://github.com/Panniantong/Agent-Reach metadata"
                    ]),
                    "# Agent-Reach\n\n## TL;DR\n\nStructured evidence [^1].",
                ]
            ),
            search=search,
            extract=None,
        ),
        config=_config(direct_sources=True),
    )

    assert report.citations
    assert "no search results" not in report.errors


@pytest.mark.asyncio
async def test_agent_reach_executes_multiple_urls_in_stable_order(monkeypatch) -> None:
    from deskpet.tools import research_sources

    calls: list[str] = []

    async def search(query: str, *, max_results: int):
        return []

    async def reach_hit(query: str, *, max_results: int = 3):
        calls.append(query)
        channel = "github" if "github.com" in query else "youtube"
        return [
            {
                "ok": True,
                "url": query,
                "title": channel,
                "text": f"{channel} evidence " * 40,
                "fetched_at": 1.0,
                "source": "agent_reach",
            }
        ], "hit", None, "Jina Reader", channel, {
            channel: {"status": "warn", "active_backend": None},
            "web": {"status": "ok", "active_backend": "Jina Reader"},
        }

    monkeypatch.setattr(
        research_sources, "agent_reach_search_with_status", reach_hit
    )
    topic = (
        "Compare https://github.com/Panniantong/Agent-Reach and "
        "https://www.youtube.com/watch?v=example"
    )
    report = await run_research_core(
        topic,
        ports=legacy_ports(
            llm_call=FakeLLM(
                [
                    json.dumps([topic]),
                    "# Comparison\n\n## TL;DR\n\nTwo sources [^1] [^2].",
                ]
            ),
            search=search,
            extract=None,
        ),
        config=_config(direct_sources=True),
    )

    assert calls == [
        "https://github.com/Panniantong/Agent-Reach",
        "https://www.youtube.com/watch?v=example",
    ]
    route = report.coverage["route"]["agent_reach"]
    assert route["planned_channels"] == ["github", "youtube"]
    assert [row["channel"] for row in route["hits"]] == ["github", "youtube"]
    assert [row["url"] for row in route["hits"]] == calls
    assert {"github", "youtube", "web"}.issubset(route["doctor"])


@pytest.mark.asyncio
async def test_agent_reach_urls_use_one_run_level_limit_and_skip_fanout(
    monkeypatch,
) -> None:
    from deskpet.tools import research_sources, research_tools

    calls: list[str] = []
    urls = [f"https://example.com/source-{index}" for index in range(6)]
    topic = "Compare " + " ".join(urls)

    async def search(query: str, *, max_results: int):
        return []

    async def reach_hit(query: str, *, max_results: int = 3):
        calls.append(query)
        return [
            {
                "ok": True,
                "url": query,
                "title": query,
                "text": "bounded evidence " * 40,
                "fetched_at": 1.0,
                "source": "agent_reach",
            }
        ], "hit", None, "Jina Reader", "web", {
            "web": {"status": "ok", "active_backend": "Jina Reader"}
        }

    async def forbidden_fanout(**kwargs):
        raise AssertionError("explicit URL research must preserve the parent URL list")

    monkeypatch.setattr(
        research_sources, "agent_reach_search_with_status", reach_hit
    )
    monkeypatch.setattr(research_tools, "_run_subagent_fanout", forbidden_fanout)
    report = await run_research_core(
        topic,
        ports=legacy_ports(
            llm_call=FakeLLM(
                [
                    json.dumps(["question one", "question two"]),
                    "# Sources\n\n## TL;DR\n\nBounded result [^1].",
                ]
            ),
            search=search,
            extract=None,
        ),
        config=_config(direct_sources=True),
        scheduler=object(),
    )

    assert calls == urls[:4]
    route = report.coverage["route"]["agent_reach"]
    assert route["planned_urls"] == urls[:4]
    assert [row["url"] for row in route["hits"]] == urls[:4]


@pytest.mark.asyncio
async def test_agent_reach_timeout_does_not_start_queued_urls(monkeypatch) -> None:
    from deskpet.tools import research_sources

    calls: list[str] = []
    urls = ["https://example.com/slow", "https://example.com/never-started"]
    topic = "Inspect " + " ".join(urls)

    async def search(query: str, *, max_results: int):
        return []

    async def slow_read(query: str, *, max_results: int = 3):
        calls.append(query)
        await asyncio.sleep(1)
        return [], "empty", None, None, "web", {}

    monkeypatch.setattr(
        research_sources, "agent_reach_search_with_status", slow_read
    )
    report = await run_research_core(
        topic,
        ports=legacy_ports(
            llm_call=FakeLLM([json.dumps([topic])]),
            search=search,
            extract=None,
        ),
        config=_config(direct_sources=True, direct_timeout=0.02),
    )

    assert calls == [urls[0]]
    assert any(error.startswith("direct_stage:") for error in report.errors)


def test_large_payload_refs_and_stable_reducer(tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    large = {"text": "evidence" * 100}
    encoded = nodes.encode_large_value(
        large, blob_store=store, threshold_bytes=32
    )
    assert isinstance(encoded, dict) and nodes.BLOB_REF_KEY in encoded
    assert nodes.decode_large_value(encoded, blob_store=store) == large

    state = _state()
    state.search_specs = [("q", "q")]
    state.url_to_question = {"https://example.com/a": "q"}
    state.passages = [
        legacy.Passage(
            citation=legacy.Citation(
                n=1,
                url="https://example.com/a",
                title="A",
                snippet="evidence",
                fetched_at=1.0,
            ),
            text="evidence" * 100,
            score=1.0,
        )
    ]
    checkpoint = nodes.checkpoint_payloads(
        state, blob_store=store, threshold_bytes=32
    )
    assert nodes.BLOB_REF_KEY in checkpoint["passages"]

    left = [{"stable_id": "b", "value": 2}]
    right = [{"stable_id": "a", "value": 1}]
    assert nodes.stable_record_reducer(left, right, id_key="stable_id") == [
        {"stable_id": "a", "value": 1},
        {"stable_id": "b", "value": 2},
    ]
    with pytest.raises(ValueError, match="conflicting research record"):
        nodes.stable_record_reducer(
            left,
            [{"stable_id": "b", "value": 3}],
            id_key="stable_id",
        )

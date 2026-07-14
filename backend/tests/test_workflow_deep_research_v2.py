from __future__ import annotations

import asyncio
import hashlib

import pytest

from deskpet.workflows.contracts import WorkflowContext
from deskpet.workflows.definitions.research_core import (
    FetchPort,
    ResearchArtifactPort,
    ResearchLLMPort,
    ResearchSearchPort,
    ResearchSearchResults,
)
from deskpet.workflows.definitions.deep_research_v2_nodes import rerank_handler
from deskpet.workflows.definitions.v2.deep_research import DEEP_RESEARCH_V2, DEEP_RESEARCH_V2_DEFINITION, initial_state
from deskpet.workflows.definitions.v1.deep_research import DEEP_RESEARCH_V1
from deskpet.workflows.definitions.v1 import register_v1_workflows
from deskpet.workflows.definitions.v2 import DEFAULT_DEEP_RESEARCH_VERSION, register_v2_workflows
from deskpet.workflows.native import NativeExecutionPolicy
from deskpet.workflows.native import InMemoryNativeCheckpointStore
from deskpet.workflows.progress import WorkflowProgressReporter
from deskpet.workflows.store import RegisteredBlobStore
from deskpet.workflows.runner import WorkflowRegistry


async def _llm(prompt: str) -> str:
    return "{}"


def _context(*, delay_reverse: bool = False, llm=_llm) -> WorkflowContext:
    async def search(query: str, *, max_results: int):
        digest = hashlib.sha256(query.encode()).hexdigest()[:12]
        if delay_reverse:
            await asyncio.sleep((int(digest[0], 16) % 3) * 0.001)
        return [{"url": f"https://example.com/{digest}", "title": query, "snippet": f"Evidence for {query}"}]

    async def fetch(url: str):
        return {"url": url, "title": "Primary source", "text": f"Primary evidence at {url} confirms the documented result in 2026."}

    return WorkflowContext(
        ports={
            "llm": ResearchLLMPort(llm),
            "search": ResearchSearchPort(search),
            "fetch": FetchPort(fetch),
            "native_execution_policy": NativeExecutionPolicy(3),
        }
    )


def test_v2_definition_is_fixed_six_slot_five_phase_and_v1_is_untouched():
    node_ids = {node.node_id for node in DEEP_RESEARCH_V2_DEFINITION.nodes}
    for stage in ("expand", "search", "direct", "fetch", "score"):
        assert {f"{stage}_b{index}" for index in range(6)} <= node_ids
        assert f"{stage}_join" in node_ids
    assert DEEP_RESEARCH_V2.manifest.workflow_version == "v2"
    assert DEEP_RESEARCH_V2.manifest.prompt_hash
    assert DEEP_RESEARCH_V2_DEFINITION.prompt_manifest["public_stages"] == [
        "normalize", "plan", "expand", "search", "direct", "fetch", "score",
        "gap", "rerank", "synth", "cite", "persist", "finalize",
    ]
    # ``uv.lock`` is checked out as CRLF in the primary Windows tree and LF
    # in some git worktrees. The manifest intentionally hashes raw lockfile
    # bytes, so pin both byte-equivalent checkout variants while keeping all
    # other v1 inputs immutable.
    v1_golden_by_lock_hash = {
        "1013a7b449853880a44dd4219d1fe8090dff38055fd4a42d6902887693e4c8ef":
            "9290fc347e425fa16c7a1eea61b8c4772d3118a5ed2caf4e857cd33c61518497",
        "ff691f62e113477ba230f0897488fb6ec9f1d1009b003947497cbecc6ea895e5":
            "8b75b88824dda7e8d2312e0e865836275fcbddfbbf2ebe9263c50059900ac804",
    }
    lock_hash = DEEP_RESEARCH_V1.manifest.dependency_lock_hash
    assert lock_hash in v1_golden_by_lock_hash
    assert DEEP_RESEARCH_V1.manifest.implementation_bundle_hash == v1_golden_by_lock_hash[lock_hash]


def test_registry_keeps_v1_for_recovery_and_exposes_v2_as_new_default():
    registry = WorkflowRegistry()
    register_v1_workflows(registry)
    register_v2_workflows(registry)
    assert ("deep_research", "v1") in registry.versions()
    assert ("deep_research", "v2") in registry.versions()
    assert DEFAULT_DEEP_RESEARCH_VERSION == "v2"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("questions", "fanout", "active_count"),
    [(["only"], True, 1), (["one", "two"], True, 2), ([f"q{i}" for i in range(6)], True, 6), (["one", "two"], False, 1)],
)
async def test_v2_fanout_slots_and_noop_schema(questions, fanout, active_count):
    state = initial_state(
        topic="topic", run_id="run-v2",
        research_config={"sub_questions": questions, "subagent_fanout": fanout},
    )
    result = await DEEP_RESEARCH_V2.bind().ainvoke(
        state, _context(), thread_id="run-v2", run_id="run-v2"
    )
    assert result["values"]["active_branch_count"] == active_count
    for stage in ("expand", "search", "direct", "fetch", "score"):
        assert set(result[f"branch_{stage}"]) == {f"b{i}" for i in range(6)}
        for index in range(active_count, 6):
            no_op = result[f"branch_{stage}"][f"b{index}"]
            assert no_op["active"] is False
            assert no_op["result"] == []
            assert no_op["budget_before"] == no_op["budget_after"]
    if not fanout and len(questions) > 1:
        assert result["branch_expand"]["b0"]["questions"] == sorted(questions)
    assert len(result["values"]["public_progress"]["completed_stage_ids"]) == 13
    assert result["values"]["report_payload"]["status"] == "completed"


@pytest.mark.asyncio
async def test_default_new_run_uses_llm_plan_to_activate_native_fanout():
    plan_calls = 0

    async def planning_llm(prompt: str) -> str:
        nonlocal plan_calls
        if "sub_questions" in prompt and "do not answer" in prompt:
            plan_calls += 1
            return '{"sub_questions":["official specification","independent evidence","known limitations"]}'
        return "{}"

    result = await DEEP_RESEARCH_V2.bind().ainvoke(
        initial_state(topic="research a new product", run_id="run-default"),
        _context(llm=planning_llm), thread_id="run-default", run_id="run-default",
    )
    assert plan_calls == 1
    assert result["values"]["active_branch_count"] == 3
    assert all(result["branch_expand"][f"b{index}"]["active"] for index in range(3))


@pytest.mark.asyncio
async def test_llm_plan_parse_failure_falls_back_to_topic_without_fanout():
    async def invalid_llm(prompt: str) -> str:
        return "not-json"

    result = await DEEP_RESEARCH_V2.bind().ainvoke(
        initial_state(topic="fallback topic", run_id="run-fallback"),
        _context(llm=invalid_llm), thread_id="run-fallback", run_id="run-fallback",
    )
    assert result["values"]["sub_questions"] == ["fallback topic"]
    assert result["values"]["active_branch_count"] == 1


@pytest.mark.asyncio
async def test_llm_plan_timeout_falls_back_to_topic():
    async def slow_llm(prompt: str) -> str:
        await asyncio.sleep(1)
        return '{"sub_questions":["late one","late two"]}'

    result = await DEEP_RESEARCH_V2.bind().ainvoke(
        initial_state(
            topic="timeout topic", run_id="run-timeout",
            research_config={"plan_timeout_seconds": 0.1},
        ),
        _context(llm=slow_llm), thread_id="run-timeout", run_id="run-timeout",
    )
    assert result["values"]["sub_questions"] == ["timeout topic"]
    assert result["values"]["active_branch_count"] == 1


@pytest.mark.asyncio
async def test_v2_completion_order_does_not_change_report_hash_or_citations():
    config = {"sub_questions": ["alpha", "beta", "gamma"]}
    first = await DEEP_RESEARCH_V2.bind().ainvoke(
        initial_state(topic="topic", run_id="run-a", research_config=config),
        _context(delay_reverse=False), thread_id="run-a", run_id="run-a",
    )
    second = await DEEP_RESEARCH_V2.bind().ainvoke(
        initial_state(topic="topic", run_id="run-b", research_config=config),
        _context(delay_reverse=True), thread_id="run-b", run_id="run-b",
    )
    left, right = first["values"]["report_payload"], second["values"]["report_payload"]
    assert left["report_hash"] == right["report_hash"]
    assert left["citations"] == right["citations"]


@pytest.mark.asyncio
async def test_v2_materializes_exactly_one_completed_intent_per_public_stage():
    base = _context()
    context = WorkflowContext(ports={
        **base.ports,
        "progress": WorkflowProgressReporter(object(), (("session_message", "session"),)),
    })
    store = InMemoryNativeCheckpointStore()
    await DEEP_RESEARCH_V2.bind(checkpointer=store).ainvoke(
        initial_state(topic="topic", run_id="run", research_config={"sub_questions": ["one", "two"]}),
        context, thread_id="run", run_id="run",
    )
    completed = {
        key: intent for key, intent in store.materialized_intents.items()
        if key.startswith("progress:v2:")
    }
    assert len(completed) == 13
    assert {intent["payload"]["stage_id"] for intent in completed.values()} == {
        "normalize", "plan", "expand", "search", "direct", "fetch", "score",
        "gap", "rerank", "synth", "cite", "persist", "finalize",
    }


@pytest.mark.asyncio
async def test_blob_backed_evidence_is_scored_synthesized_and_cited(tmp_path):
    marker = "Large verified evidence marker for 2026. "
    large_text = marker * 80

    async def search(query: str, *, max_results: int):
        return [{"url": "https://example.com/large", "title": "Large source", "snippet": marker}]

    async def fetch(url: str):
        return {"url": url, "title": "Large source", "text": large_text}

    blob_store = RegisteredBlobStore(tmp_path / "blobs", tmp_path / "workflow.db")
    context = WorkflowContext(ports={
        "llm": ResearchLLMPort(_llm), "search": ResearchSearchPort(search),
        "fetch": FetchPort(fetch), "blob": blob_store,
        "native_execution_policy": NativeExecutionPolicy(3),
    })
    result = await DEEP_RESEARCH_V2.bind().ainvoke(
        initial_state(topic="large", run_id="run-large", research_config={"sub_questions": ["large evidence"]}),
        context, thread_id="run-large", run_id="run-large",
    )
    fetched = result["branch_fetch"]["b0"]["result"][0]
    assert "blob_ref" in fetched and "text" not in fetched
    assert result["branch_score"]["b0"]["result"][0]["score"] > 0
    assert set(result["branch_score"]["b0"]["result"][0]["score_dims"]) == {
        "authority", "recency", "relevance", "depth",
    }
    report = result["values"]["report_payload"]
    assert report["status"] == "completed"
    assert "Large verified evidence marker" in report["report_md"]
    assert result["values"]["claim_quality"]["supported_claim_count"] >= 1


@pytest.mark.asyncio
async def test_gap_performs_one_bounded_followup_and_rerank_includes_it():
    search_calls = 0

    async def search(query: str, *, max_results: int):
        nonlocal search_calls
        search_calls += 1
        if search_calls <= 2:
            return []
        return [{"url": "https://example.com/gap", "title": "Gap source", "snippet": "gap evidence"}]

    async def fetch(url: str):
        return {"url": url, "title": "Gap source", "text": "Gap follow-up evidence confirms the missing question in 2026."}

    context = WorkflowContext(ports={
        "llm": ResearchLLMPort(_llm), "search": ResearchSearchPort(search),
        "fetch": FetchPort(fetch), "native_execution_policy": NativeExecutionPolicy(2),
    })
    result = await DEEP_RESEARCH_V2.bind().ainvoke(
        initial_state(topic="gap", run_id="run-gap", research_config={"sub_questions": ["missing question"]}),
        context, thread_id="run-gap", run_id="run-gap",
    )
    assert search_calls == 3
    assert result["values"]["gap_evidence"][0]["question"] == "missing question"
    assert any(value["canonical_url"] == "https://example.com/gap" for value in result["values"]["ranked_evidence"])
    assert result["values"]["report_payload"]["status"] == "completed"


@pytest.mark.asyncio
async def test_v2_observer_emits_only_safe_branch_stage_and_claim_metrics():
    attributes = []

    class Observer:
        async def node_started(self, identity): return None
        async def node_finished(self, identity, status, *, error=None, attributes=None):
            if attributes:
                globals_ = attributes
                assert "metric evidence" not in str(globals_).lower()
                assert "https://" not in str(globals_).lower()
                attributes_list.append(globals_)

    attributes_list = attributes
    base = _context()
    context = WorkflowContext(ports={**base.ports, "observer": Observer()})
    await DEEP_RESEARCH_V2.bind().ainvoke(
        initial_state(topic="metrics", run_id="run-metrics", research_config={"sub_questions": ["metric evidence"]}),
        context, thread_id="run-metrics", run_id="run-metrics",
    )
    assert any("deepresearch_v2_branch" in value for value in attributes)
    assert any("deepresearch_v2_stage" in value and value["deepresearch_v2_stage"]["duration_ms"] >= 0 for value in attributes)
    assert any("deepresearch_v2_claim_support" in value for value in attributes)


@pytest.mark.asyncio
async def test_v2_persists_one_real_markdown_artifact_and_delivers_file_card(tmp_path):
    async def save_artifact(*, topic: str, report_md: str, report_hash: str, run_id: str):
        path = tmp_path / f"{run_id}.md"
        path.write_text(report_md, encoding="utf-8")
        payload = path.read_bytes()
        return {
            "kind": "file",
            "path": str(path),
            "mime": "text/markdown",
            "title": path.name,
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    base = _context()
    context = WorkflowContext(
        ports={**base.ports, "artifact": ResearchArtifactPort(save_artifact)}
    )
    result = await DEEP_RESEARCH_V2.bind().ainvoke(
        initial_state(topic="artifact", run_id="run-artifact"),
        context,
        thread_id="run-artifact",
        run_id="run-artifact",
    )
    artifact = result["values"]["report_artifact"]
    assert artifact["kind"] == "file"
    assert artifact["mime"] == "text/markdown"
    assert (tmp_path / "run-artifact.md").read_text(encoding="utf-8") == result["values"]["report_payload"]["report_md"]
    intent = next(
        value for value in result["values"]["delivery_intents"]
        if value["channel"] == "artifact"
    )
    assert intent["payload"]["artifacts"] == [artifact]
    assert "report" not in intent["payload"]


@pytest.mark.asyncio
async def test_v2_expands_matching_source_packs_and_uses_strict_llm_synthesis(monkeypatch):
    monkeypatch.setattr(
        "deskpet.workflows.definitions.deep_research_v2_nodes.legacy_research._source_pack_queries_for",
        lambda question: [("official_docs", f"{question} site:example.com")],
    )
    prompts: list[str] = []

    async def llm(prompt: str) -> str:
        prompts.append(prompt)
        if "sub_questions" in prompt and "do not answer" in prompt:
            return '{"sub_questions":["verified topic"]}'
        if "Synthesize only the supplied evidence" in prompt:
            return '{"findings":["Primary evidence confirms the documented result in 2026 [1]"],"inferences":[]}'
        return "{}"

    result = await DEEP_RESEARCH_V2.bind().ainvoke(
        initial_state(
            topic="verified topic",
            run_id="run-quality",
            research_config={"fanout_threshold": 2, "source_packs": True},
        ),
        _context(llm=llm),
        thread_id="run-quality",
        run_id="run-quality",
    )
    expanded = result["branch_expand"]["b0"]["result"]
    assert any(value.get("source_pack") == "official_docs" for value in expanded)
    assert "Primary evidence confirms" in result["values"]["draft_report"]
    assert any("Synthesize only the supplied evidence" in value for value in prompts)
    assert "## Degraded / Error Summary" in result["values"]["report_payload"]["report_md"]


@pytest.mark.asyncio
async def test_v2_claim_support_awaits_async_semantic_scorer():
    semantic_calls = 0

    async def semantic_score(claim: str, passages: list[str]):
        nonlocal semantic_calls
        semantic_calls += 1
        await asyncio.sleep(0)
        return [0.9]

    base = _context()
    llm = ResearchLLMPort(_llm, semantic_score=semantic_score)
    result = await DEEP_RESEARCH_V2.bind().ainvoke(
        initial_state(topic="semantic", run_id="run-semantic"),
        WorkflowContext(ports={**base.ports, "llm": llm}),
        thread_id="run-semantic",
        run_id="run-semantic",
    )

    assert semantic_calls > 0
    assert result["values"]["report_payload"]["status"] == "completed"


@pytest.mark.asyncio
async def test_deep_mode_uses_two_gap_rounds_and_scores_followup_with_recency():
    calls: list[str] = []

    async def search(query: str, *, max_results: int):
        calls.append(query)
        if query.endswith("official primary source"):
            return ResearchSearchResults(
                [{
                    "url": "https://who.int/deep-gap",
                    "title": "Primary evidence",
                    "published_at": "2026-01-15",
                }],
                observation={"degraded": False, "engines_hit": ["test"]},
            )
        return ResearchSearchResults([], observation={"degraded": False})

    async def fetch(url: str):
        return {
            "url": url,
            "title": "Primary evidence",
            "published_at": "2026-01-15",
            "text": "missing question verified primary evidence " * 20,
        }

    context = WorkflowContext(ports={
        "llm": ResearchLLMPort(_llm),
        "search": ResearchSearchPort(search),
        "fetch": FetchPort(fetch),
        "native_execution_policy": NativeExecutionPolicy(2),
    })
    result = await DEEP_RESEARCH_V2.bind().ainvoke(
        initial_state(
            topic="gap",
            run_id="run-deep-gap",
            mode="deep",
            research_config={"sub_questions": ["missing question"]},
        ),
        context,
        thread_id="run-deep-gap",
        run_id="run-deep-gap",
    )

    assert result["values"]["research_config"]["max_rounds"] == 2
    assert result["values"]["gap_rounds_completed"] == 2
    assert calls[-1].endswith("official primary source")
    scored = result["values"]["gap_evidence"][0]
    assert scored["published_at"] == "2026-01-15"
    assert scored["score_dims"]["recency"] > 3


@pytest.mark.asyncio
async def test_global_rerank_blends_semantics_and_prioritizes_domain_diversity():
    async def semantic_score(query: str, passages: list[str]):
        return [0.1, 0.9, 0.8]

    state = initial_state(topic="topic", run_id="run-rerank")
    state["values"].update({
        "joined_score": [
            {"url": "https://a.test/1", "canonical_url": "https://a.test/1", "content_hash": "1", "text": "one", "score": 0.95},
            {"url": "https://a.test/2", "canonical_url": "https://a.test/2", "content_hash": "2", "text": "two", "score": 0.90},
            {"url": "https://b.test/1", "canonical_url": "https://b.test/1", "content_hash": "3", "text": "three", "score": 0.80},
        ],
        "gap_evidence": [],
    })
    patch = await rerank_handler(
        state,
        WorkflowContext(ports={"llm": ResearchLLMPort(_llm, semantic_score=semantic_score)}),
    )
    values = patch.to_dict()["values"]
    ranked = values["ranked_evidence"]
    assert [row["url"].split("/")[2] for row in ranked[:2]] == ["a.test", "b.test"]
    assert values["rerank_semantic_applied"] is True
    assert all("semantic_score" in row for row in ranked)


@pytest.mark.asyncio
async def test_v2_emits_fixed_schema_metrics_without_an_observer(monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "observability.metrics_sink.record",
        lambda event, detail=None: events.append((event, dict(detail or {}))),
    )
    await DEEP_RESEARCH_V2.bind().ainvoke(
        initial_state(topic="metrics", run_id="run-metric-sink"),
        _context(),
        thread_id="run-metric-sink",
        run_id="run-metric-sink",
    )
    names = {event for event, _ in events}
    assert {
        "deepresearch_v2_branch",
        "deepresearch_v2_stage",
        "deepresearch_claim_support",
    } <= names
    assert "metrics" not in str(events).lower()


@pytest.mark.asyncio
async def test_no_results_report_keeps_error_summary_visible():
    async def empty_search(query: str, *, max_results: int):
        raise RuntimeError("provider down")

    base = _context()
    result = await DEEP_RESEARCH_V2.bind().ainvoke(
        initial_state(topic="empty", run_id="run-empty"),
        WorkflowContext(ports={
            **base.ports,
            "search": ResearchSearchPort(empty_search),
        }),
        thread_id="run-empty",
        run_id="run-empty",
    )
    report = result["values"]["report_payload"]
    assert report["status"] == "no_results"
    assert "## Degraded / Error Summary" in report["report_md"]
    assert "provider_failure" in report["report_md"]

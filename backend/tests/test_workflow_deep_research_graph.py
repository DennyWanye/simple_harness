from __future__ import annotations

import json

import pytest

from deskpet.workflows import WorkflowContext, WorkflowRunStatus, compile_workflow
from deskpet.workflows.definitions import deep_research_nodes as research_nodes
from deskpet.workflows.definitions.research_core import (
    DirectOutcome,
    FetchPort,
    ResearchLLMPort,
    ResearchPorts,
    ResearchSearchPort,
)
from deskpet.workflows.definitions.v1 import (
    DEEP_RESEARCH_V1,
    DEEP_RESEARCH_V1_DEFINITION,
    deep_research_initial_state,
    register_v1_workflows,
)
from deskpet.workflows.errors import WorkflowErrorCode, WorkflowNodeError
from deskpet.workflows.runner import WorkflowRegistry, WorkflowRunner
from deskpet.workflows.store import FencedAsyncSqliteSaver, WorkflowRunStore


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        return self.responses.pop(0)


def _ports(llm, search, fetch) -> ResearchPorts:
    return ResearchPorts(
        llm=ResearchLLMPort(complete=llm),
        search=ResearchSearchPort(search_call=search),
        fetch=FetchPort(extractor=fetch),
    )


class _ProgressRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def report(self, identity, transition):
        self.events.append((identity.node_id, transition))


def _context(ports: ResearchPorts, progress=None) -> WorkflowContext:
    context_ports = {"llm": ports.llm, "search": ports.search, "fetch": ports.fetch}
    if progress is not None:
        context_ports["progress"] = progress
    return WorkflowContext(ports=context_ports)


def _config(**overrides) -> dict[str, object]:
    config: dict[str, object] = {
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
    config.update(overrides)
    return config


async def _runner(tmp_path, *, owner: str = "deep-research-runner"):
    database = tmp_path / "workflow.db"
    store = WorkflowRunStore(database)
    saver = FencedAsyncSqliteSaver(database)
    registry = WorkflowRegistry()
    register_v1_workflows(registry)
    runner = WorkflowRunner(store, saver, registry, owner=owner)
    run_id = await runner.start(
        session_id="session",
        request_id="request",
        turn_id="turn",
        workflow_name="deep_research",
        workflow_version="v1",
        capability_snapshot={"ports": ["llm", "search", "fetch"]},
    )
    return runner, store, run_id


def _intent(output: object, kind: str) -> dict:
    assert isinstance(output, dict)
    intents = output["values"]["delivery_intents"]
    return next(intent for intent in intents if intent["kind"] == kind)


@pytest.mark.asyncio
async def test_happy_path_runs_with_fenced_saver_and_emits_only_delivery_intents(
    tmp_path,
) -> None:
    llm = FakeLLM(
        [
            json.dumps(["what is the stable evidence?"]),
            "# Stable research\n\n## TL;DR\n\nSupported finding [^1].",
        ]
    )
    search_calls: list[str] = []

    async def search(query: str, *, max_results: int):
        search_calls.append(query)
        assert max_results == 2
        return [{"url": "https://example.com/source", "title": "Source"}]

    async def fetch(url: str):
        return {
            "ok": True,
            "url": url,
            "title": "Source",
            "text": "stable research evidence from a primary source " * 500,
            "fetched_at": 123.0,
        }

    ports = _ports(llm, search, fetch)
    progress = _ProgressRecorder()
    runner, store, run_id = await _runner(tmp_path)
    state = deep_research_initial_state(
        topic="stable research",
        run_id=run_id,
        session_id="session",
        research_config=_config(),
        blob_root=tmp_path / "blobs",
    )

    result = await runner.run(run_id, state, _context(ports, progress))
    history = await runner.get_state_history(run_id)

    assert result.status is WorkflowRunStatus.COMPLETED
    run_row = await store.get_run(run_id)
    trace = await runner.trace_store.tree(str(run_row["trace_id"]))
    node_span_ids = {span["span_id"] for span in trace["spans"] if span["kind"] == "node"}
    child_spans = [span for span in trace["spans"] if span["kind"] in {"llm", "tool"}]
    assert node_span_ids
    assert child_spans
    assert all(span["parent_span_id"] in node_span_ids for span in child_spans)
    channel_spans = []
    for span in trace["spans"]:
        attributes = json.loads(span["attributes_json"])
        if span["kind"] == "node" and attributes.get("agent_reach"):
            channel_spans.append({**span, "attributes": attributes})
    assert channel_spans
    trace_channels = channel_spans[-1]["attributes"]["agent_reach"]
    assert trace_channels["planned_channels"] == []
    assert trace_channels["hits"] == []
    assert trace_channels["degraded"] == []
    assert "web" in trace_channels["doctor"]
    assert "Authorization" not in json.dumps(trace_channels)
    assert search_calls == ["what is the stable evidence?"]
    report = _intent(result.output, "report")
    artifact = _intent(result.output, "artifact_card")
    final = _intent(result.output, "final_assistant")
    assert report["payload"]["report"]["status"] == "completed"
    coverage_route = report["payload"]["report"]["report"]["coverage"]["route"]
    assert coverage_route["agent_reach"] == trace_channels
    assert artifact["payload"]["artifact_type"] == "research_report"
    assert "Supported finding" in artifact["payload"]["preview"]
    assert "Supported finding" in final["payload"]["text"]
    assert final["payload"]["text"].startswith("# Stable research")
    assert final["payload"]["summary"]
    assert set(result.output["values"]) == {"delivery_intents"}
    assert {intent["channel"] for intent in result.output["values"]["delivery_intents"]} == {
        "workflow_report",
        "artifact",
        "final_assistant",
    }
    assert any(
        research_nodes.BLOB_REF_KEY
        in item["state"]
        .get("state", {})
        .get("values", {})
        .get("research_state", {})
        .get("passages", {})
        for item in history
    )
    started = {node for node, transition in progress.events if transition == "started"}
    assert {"normalize", "plan", "search", "synth", "cite", "persist", "finalize"}.issubset(started)


@pytest.mark.asyncio
async def test_agent_reach_coverage_matches_durable_node_trace(tmp_path) -> None:
    url = "https://github.com/Panniantong/Agent-Reach"
    llm = FakeLLM(
        [
            json.dumps([f"Inspect {url}"]),
            "# Agent-Reach\n\n## TL;DR\n\nDirect evidence [^1].",
        ]
    )

    async def search(query: str, *, max_results: int):
        return []

    async def direct(query: str, source: str) -> DirectOutcome:
        assert query == url
        assert source == "agent_reach"
        return DirectOutcome(
            source,
            [{
                "ok": True,
                "url": url,
                "title": "Agent-Reach",
                "text": "Agent-Reach direct repository evidence " * 40,
                "fetched_at": 123.0,
                "source": source,
            }],
            "hit",
            "Jina Reader",
            None,
            {
                "url": url,
                "channel": "github",
                "doctor": {
                    "web": {"status": "ok", "active_backend": "Jina Reader"}
                },
            },
        )

    async def fetch(url: str):
        raise AssertionError(f"unexpected generic fetch: {url}")

    ports = ResearchPorts(
        llm=ResearchLLMPort(complete=llm),
        search=ResearchSearchPort(search_call=search, direct_call=direct),
        fetch=FetchPort(extractor=fetch),
    )
    runner, store, run_id = await _runner(tmp_path)
    state = deep_research_initial_state(
        topic=f"Inspect {url}",
        run_id=run_id,
        session_id="session",
        research_config=_config(direct_sources=True),
        blob_root=tmp_path / "blobs",
    )

    result = await runner.run(run_id, state, _context(ports))

    assert result.status is WorkflowRunStatus.COMPLETED
    run_row = await store.get_run(run_id)
    trace = await runner.trace_store.tree(str(run_row["trace_id"]))
    attribute_spans = [
        span
        for span in trace["spans"]
        if span["kind"] == "node"
        and "agent_reach" in json.loads(span["attributes_json"])
    ]
    coverage = _intent(result.output, "report")["payload"]["report"]["report"][
        "coverage"
    ]["route"]["agent_reach"]
    assert attribute_spans
    assert all(span["node_id"] for span in attribute_spans)
    assert all(span["status"] == "ok" for span in attribute_spans)
    assert json.loads(attribute_spans[-1]["attributes_json"])["agent_reach"] == coverage
    assert coverage["planned_urls"] == [url]
    assert coverage["hits"][0]["url"] == url
@pytest.mark.asyncio
async def test_fetch_crash_restart_retains_checkpointed_search_result(
    tmp_path, monkeypatch
) -> None:
    llm = FakeLLM(
        [
            json.dumps(["checkpointed question?"]),
            "# Restarted\n\n## TL;DR\n\nRecovered result [^1].",
        ]
    )
    search_calls = 0

    async def search(query: str, *, max_results: int):
        nonlocal search_calls
        search_calls += 1
        return [{"url": "https://example.com/checkpoint", "title": "Checkpoint"}]

    async def fetch(url: str):
        return {
            "ok": True,
            "url": url,
            "title": "Checkpoint",
            "text": "checkpoint recovery evidence " * 40,
            "fetched_at": 456.0,
        }

    ports = _ports(llm, search, fetch)
    original_fetch_node = research_nodes.fetch_extract_node
    attempts = 0

    async def crash_once(state, stage_ports, config):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise WorkflowNodeError(
                code=WorkflowErrorCode.RETRYABLE_PROVIDER,
                message_ref="test:fetch_crash",
                node_id="fetch",
            )
        return await original_fetch_node(state, stage_ports, config)

    monkeypatch.setattr(research_nodes, "fetch_extract_node", crash_once)
    runner, store, run_id = await _runner(tmp_path, owner="runner-before-crash")
    state = deep_research_initial_state(
        topic="checkpoint recovery",
        run_id=run_id,
        session_id="session",
        research_config=_config(),
        blob_root=tmp_path / "blobs",
    )

    failed = await runner.run(run_id, state, _context(ports))
    history = await runner.get_state_history(run_id)

    assert failed.status is WorkflowRunStatus.RETRYABLE
    assert search_calls == 1
    assert any(
        item["state"]
        .get("state", {})
        .get("values", {})
        .get("research_state", {})
        .get("url_to_question", {})
        == {"https://example.com/checkpoint": "checkpointed question?"}
        for item in history
    )

    restarted_registry = WorkflowRegistry()
    register_v1_workflows(restarted_registry)
    restarted = WorkflowRunner(
        store,
        FencedAsyncSqliteSaver(store.path),
        restarted_registry,
        owner="runner-after-crash",
    )
    recovered = await restarted.run(run_id, None, _context(ports))

    assert recovered.status is WorkflowRunStatus.COMPLETED
    assert search_calls == 1
    assert attempts == 2
    assert _intent(recovered.output, "report")["payload"]["report"]["status"] == "completed"


@pytest.mark.asyncio
async def test_gap_budget_is_clamped_and_consumed_once(tmp_path) -> None:
    llm = FakeLLM(
        [
            json.dumps(["initial question?"]),
            json.dumps(["gap followup?"]),
            "# Gap report\n\n## TL;DR\n\nTwo-round result [^1][^2].",
        ]
    )
    search_calls: list[str] = []

    async def search(query: str, *, max_results: int):
        search_calls.append(query)
        suffix = "initial" if query == "initial question?" else "followup"
        return [{"url": f"https://{suffix}.example/source", "title": suffix}]

    async def fetch(url: str):
        return {
            "ok": True,
            "url": url,
            "title": url,
            "text": f"gap budget evidence for {url} " * 30,
            "fetched_at": 789.0,
        }

    ports = _ports(llm, search, fetch)
    runner, _, run_id = await _runner(tmp_path)
    state = deep_research_initial_state(
        topic="gap budget",
        run_id=run_id,
        research_config=_config(max_rounds=99),
    )

    result = await runner.run(run_id, state, _context(ports))

    assert result.status is WorkflowRunStatus.COMPLETED
    assert result.output["loop_counters"]["gap_iterations"] == 1
    report = _intent(result.output, "report")["payload"]["report"]["report"]
    assert report["coverage"]["rounds"] == 2
    assert search_calls == ["initial question?", "gap followup?"]


@pytest.mark.asyncio
async def test_no_result_path_skips_synthesis_and_still_closes_with_intents(
    tmp_path,
) -> None:
    llm = FakeLLM([json.dumps(["nothing here?"])])

    async def search(query: str, *, max_results: int):
        return []

    async def fetch(url: str):
        raise AssertionError("fetch must not run without search results")

    ports = _ports(llm, search, fetch)
    runner, _, run_id = await _runner(tmp_path)
    state = deep_research_initial_state(
        topic="no result topic",
        run_id=run_id,
        research_config=_config(max_rounds=2),
    )

    result = await runner.run(run_id, state, _context(ports))

    assert result.status is WorkflowRunStatus.COMPLETED
    assert len(llm.calls) == 1
    assert result.output["loop_counters"]["gap_iterations"] == 0
    assert _intent(result.output, "report")["payload"]["report"]["status"] == "no_results"
    assert len(result.output["values"]["delivery_intents"]) == 3


def test_manifest_and_required_node_topology_are_stable() -> None:
    definition = DEEP_RESEARCH_V1_DEFINITION
    node_ids = [node.node_id for node in definition.nodes]

    assert node_ids == [
        "normalize",
        "plan",
        "expand",
        "search",
        "direct",
        "fetch",
        "score",
        "gap",
        "rerank",
        "synth",
        "cite",
        "persist",
        "finalize",
    ]
    assert all(node.required for node in definition.nodes)
    assert str(definition.durability) == "sync"
    assert definition.loop_budgets == {"gap_iterations": 1}
    gap_edge = definition.conditional_edges[0]
    assert gap_edge.source == "gap"
    assert dict(gap_edge.routes) == {
        "continue": "gap",
        "done": "rerank",
        "no_results": "persist",
    }
    assert DEEP_RESEARCH_V1.manifest.workflow_name == "deep_research"
    assert DEEP_RESEARCH_V1.manifest.workflow_version == "v1"
    assert len(DEEP_RESEARCH_V1.manifest.implementation_bundle_hash) == 64
    assert (
        compile_workflow(definition).manifest.to_dict()
        == DEEP_RESEARCH_V1.manifest.to_dict()
    )
    assert definition.channels["values"].allowed_writers == {
        "normalize",
        "plan",
        "expand",
        "search",
        "direct",
        "fetch",
        "score",
        "gap",
        "rerank",
        "synth",
        "cite",
        "persist",
        "finalize",
    }
    assert definition.channels["loop_counters"].allowed_writers == {
        "normalize",
        "gap",
    }

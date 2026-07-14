from __future__ import annotations

import asyncio
import hashlib

import pytest

from deskpet.workflows.contracts import WorkflowContext
from deskpet.workflows.definitions.research_core import FetchPort, ResearchLLMPort, ResearchSearchPort
from deskpet.workflows.definitions.v2.deep_research import DEEP_RESEARCH_V2, DEEP_RESEARCH_V2_DEFINITION, initial_state
from deskpet.workflows.definitions.v1.deep_research import DEEP_RESEARCH_V1
from deskpet.workflows.definitions.v1 import register_v1_workflows
from deskpet.workflows.definitions.v2 import DEFAULT_DEEP_RESEARCH_VERSION, register_v2_workflows
from deskpet.workflows.native import NativeExecutionPolicy
from deskpet.workflows.native import InMemoryNativeCheckpointStore
from deskpet.workflows.progress import WorkflowProgressReporter
from deskpet.workflows.runner import WorkflowRegistry


async def _llm(prompt: str) -> str:
    return "{}"


def _context(*, delay_reverse: bool = False) -> WorkflowContext:
    async def search(query: str, *, max_results: int):
        digest = hashlib.sha256(query.encode()).hexdigest()[:12]
        if delay_reverse:
            await asyncio.sleep((int(digest[0], 16) % 3) * 0.001)
        return [{"url": f"https://example.com/{digest}", "title": query, "snippet": f"Evidence for {query}"}]

    async def fetch(url: str):
        return {"url": url, "title": "Primary source", "text": f"Primary evidence at {url} confirms the documented result in 2026."}

    return WorkflowContext(
        ports={
            "llm": ResearchLLMPort(_llm),
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

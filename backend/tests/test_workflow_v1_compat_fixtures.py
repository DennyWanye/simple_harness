from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from deskpet.workflows import WorkflowContext, compile_workflow
from deskpet.workflows.contracts import validate_json_value
from deskpet.workflows.definitions import research_core
from deskpet.workflows.definitions.research_core import (
    FetchPort,
    ResearchLLMPort,
    ResearchPorts,
    ResearchSearchPort,
)
from deskpet.workflows.definitions.v1 import (
    DEEP_RESEARCH_V1,
    DEEP_RESEARCH_V1_DEFINITION,
    deep_research_initial_state,
)
from deskpet.workflows.native import InMemoryNativeCheckpointStore
from deskpet.workflows.runner import manifest_hash
from deskpet.tools.agent_reach_port import AgentReachPort


FIXTURES = Path(__file__).parent / "fixtures"
MANIFEST_FIXTURE = FIXTURES / "workflow_manifests" / "deep_research_v1.json"
CHECKPOINT_FIXTURE = FIXTURES / "workflow_checkpoints" / "deep_research_v1.json"
UV_LOCK = Path(__file__).parents[1] / "uv.lock"
CAPTURED_STAGES = {"normalize", "search", "fetch", "cite", "persist", "finalize"}
SNAPSHOT_KEYS = {
    "checkpoint_type",
    "engine_kind",
    "snapshot_version",
    "state_schema_version",
    "thread_id",
    "checkpoint_ns",
    "checkpoint_id",
    "parent_checkpoint_id",
    "run_id",
    "step",
    "state",
    "frontier",
    "completed_activations",
    "join_firings",
    "node_writes",
    "interrupt",
    "metadata",
}


class _FakeLLM:
    def __init__(self) -> None:
        self.responses = [
            json.dumps(["What evidence makes the v1 contract stable?"]),
            "# Stable v1 research\n\n## TL;DR\n\nThe fixture preserves supported evidence [^1].",
        ]

    async def __call__(self, prompt: str) -> str:
        del prompt
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        return self.responses.pop(0)


class _CapturingStore(InMemoryNativeCheckpointStore):
    def __init__(self) -> None:
        super().__init__()
        self.captures: list[dict[str, Any]] = []

    async def commit_frontier(self, **kwargs: object):
        assert self.snapshot is not None
        completed_nodes = sorted(task.node_id for task in self.snapshot.frontier)
        result = await super().commit_frontier(**kwargs)
        if len(completed_nodes) == 1 and completed_nodes[0] in CAPTURED_STAGES:
            self.captures.append(
                {
                    "stage": completed_nodes[0],
                    "snapshot": result.snapshot.to_dict(),
                }
            )
        return result


def _ports() -> ResearchPorts:
    async def search(query: str, *, max_results: int):
        assert query == "What evidence makes the v1 contract stable?"
        assert max_results == 2
        return [{"url": "https://example.com/v1-contract", "title": "V1 Contract"}]

    async def fetch(url: str):
        assert url == "https://example.com/v1-contract"
        return {
            "ok": True,
            "url": url,
            "title": "V1 Contract",
            "text": (
                "Stable v1 contract evidence provides deterministic primary source support. "
                "This v1 contract is stable, cited, and reproducible."
            ),
            "fetched_at": 123.0,
        }

    return ResearchPorts(
        llm=ResearchLLMPort(complete=_FakeLLM()),
        search=ResearchSearchPort(search_call=search),
        fetch=FetchPort(extractor=fetch),
    )


def _context(ports: ResearchPorts) -> WorkflowContext:
    return WorkflowContext(
        ports={"llm": ports.llm, "search": ports.search, "fetch": ports.fetch}
    )


def _config() -> dict[str, object]:
    return {
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


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _line_ending_variants(raw: bytes) -> dict[str, bytes]:
    lf = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return {"lf": lf, "crlf": lf.replace(b"\n", b"\r\n")}


def _stable_agent_reach_doctor(
    self: AgentReachPort, *, names: object = None
) -> dict[str, dict[str, object]]:
    del self, names
    return {
        "web": {
            "status": "ok",
            "name": "Fixture web",
            "message": "Deterministic v1 fixture backend.",
            "tier": 0,
            "backends": ["fixture-reader"],
            "active_backend": "fixture-reader",
        }
    }


@pytest.mark.parametrize("line_ending", ["lf", "crlf"])
def test_deep_research_v1_manifest_fixture_accepts_lock_line_endings(
    tmp_path: Path, line_ending: str
) -> None:
    fixture = _load(MANIFEST_FIXTURE)
    lock_path = tmp_path / f"uv-{line_ending}.lock"
    lock_path.write_bytes(_line_ending_variants(UV_LOCK.read_bytes())[line_ending])

    compiled = compile_workflow(
        DEEP_RESEARCH_V1_DEFINITION, dependency_lock_path=lock_path
    )
    actual = compiled.manifest.to_dict()
    expected = fixture["line_ending_variants"][line_ending]

    assert fixture["fixture_schema_version"] == 1
    assert fixture["workflow"] == {"name": "deep_research", "version": "v1"}
    assert actual == expected["manifest"]
    assert manifest_hash(compiled.manifest) == expected["manifest_hash"]
    assert {
        key: value
        for key, value in actual.items()
        if key not in {"dependency_lock_hash", "implementation_bundle_hash"}
    } == fixture["line_ending_invariant_manifest"]


@pytest.mark.asyncio
async def test_deep_research_v1_checkpoint_fixture_matches_live_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stage durations are operational telemetry, not part of the compatibility
    # contract. Freeze the helper so content-derived checkpoint IDs stay stable.
    monkeypatch.setattr(research_core, "_stage_ms", lambda _start: 7)
    monkeypatch.setattr(AgentReachPort, "doctor", _stable_agent_reach_doctor)
    fixture = _load(CHECKPOINT_FIXTURE)
    store = _CapturingStore()
    executable = DEEP_RESEARCH_V1.bind(checkpointer=store)
    run_id = "fixture-deep-research-v1-run"
    thread_id = "fixture-deep-research-v1-thread"
    initial = deep_research_initial_state(
        topic="Stable v1 contract",
        run_id=run_id,
        thread_id=thread_id,
        session_id="fixture-session",
        research_config=_config(),
        blob_root=None,
    )

    result = await executable.ainvoke(
        initial,
        _context(_ports()),
        thread_id=thread_id,
        run_id=run_id,
        checkpoint_ns="v1-compat-fixture",
    )

    assert fixture["fixture_schema_version"] == 1
    assert fixture["workflow"] == {"name": "deep_research", "version": "v1"}
    assert (
        manifest_hash(DEEP_RESEARCH_V1.manifest)
        in fixture["compatible_manifest_hashes"]
    )
    assert store.captures == fixture["checkpoints"]
    assert result == fixture["checkpoints"][-1]["snapshot"]["state"]
    assert [item["stage"] for item in fixture["checkpoints"]] == [
        "normalize",
        "search",
        "fetch",
        "cite",
        "persist",
        "finalize",
    ]

    for item in fixture["checkpoints"]:
        snapshot = item["snapshot"]
        validate_json_value(snapshot)
        assert set(snapshot) == SNAPSHOT_KEYS
        assert snapshot["checkpoint_type"] == "deskpet-native-json-v1"
        assert snapshot["engine_kind"] == "deskpet-native"
        assert snapshot["snapshot_version"] == 1
        assert snapshot["state_schema_version"] == 1
        assert snapshot["thread_id"] == thread_id
        assert snapshot["run_id"] == run_id
        assert snapshot["checkpoint_ns"] == "v1-compat-fixture"
        state = snapshot["state"]
        assert state["schema_version"] == 1
        assert state["workflow_name"] == "deep_research"
        assert state["workflow_version"] == "v1"

    persisted_values = fixture["checkpoints"][-2]["snapshot"]["state"]["values"]
    report_payload = persisted_values["report_payload"]
    assert report_payload["schema_version"] == 1
    assert report_payload["status"] == "completed"
    report = report_payload["report"]
    assert report["topic"] == "Stable v1 contract"
    assert report["report_md"].startswith("# Stable v1 research")
    assert report["citations"][0]["url"] == "https://example.com/v1-contract"
    final_values = fixture["checkpoints"][-1]["snapshot"]["state"]["values"]
    assert set(final_values) == {"delivery_intents"}
    intents = {item["kind"]: item for item in final_values["delivery_intents"]}
    assert set(intents) == {"report", "artifact_card", "final_assistant"}
    assert intents["report"]["payload"]["report"] == report_payload
    assert intents["artifact_card"]["payload"] == {
        "artifact_type": "research_report",
        "report": report_payload,
        "preview": report["summary"],
    }
    assert intents["final_assistant"]["payload"]["report"] == report_payload
    assert intents["final_assistant"]["payload"]["text"] == report["report_md"]

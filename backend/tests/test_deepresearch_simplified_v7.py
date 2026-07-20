from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from config import WorkflowsConfig, resolve_deep_research_workflow_version
from deskpet.tools import research_tools as legacy
from deskpet.workflows.contracts import WorkflowContext
from deskpet.workflows.definitions.research_core import (
    FetchPort,
    ResearchArtifactPort,
    ResearchLLMPort,
    ResearchSearchPort,
)
from deskpet.workflows.definitions.v7.deep_research import (
    DEEP_RESEARCH_V7_DEFINITION,
    finalize_handler,
    initial_state,
    normalize_handler,
    persist_handler,
    plan_handler,
    search_handler,
    synth_handler,
)


class DirectScheduler:
    async def run(self, *, coro_factory, **kwargs):
        del kwargs
        return await coro_factory()


class PromptLLM:
    async def __call__(self, prompt: str) -> str:
        if "planning a deep research project" in prompt:
            return json.dumps(["方向一？", "方向二？", "方向三？"], ensure_ascii=False)
        return (
            "# 汇总报告\n\n## TL;DR\n\n这是统一结论。[^1][^2]\n\n"
            "## Key findings\n\n跨方向分析。[^1][^2]\n"
        )


def _report(question: str, idx: int) -> legacy.ResearchReport:
    citation = legacy.Citation(
        n=1,
        url=f"https://example.com/{idx}",
        title=f"source {idx}",
        snippet="evidence",
        fetched_at=float(idx),
    )
    return legacy.ResearchReport(
        topic=question,
        summary=f"summary {idx}",
        report_md=f"# {question}\n\nEvidence {idx} [^1].",
        citations=[citation],
        sub_questions=[question],
        coverage={"n_sources": 1, "n_domains": 1},
    )


def _apply(state: dict[str, Any], patch) -> None:
    for key, value in patch.values.items():
        if key == "blob_refs":
            state[key] = [*state.get(key, []), *value]
        else:
            state[key] = value


def _context(tmp_path: Path) -> WorkflowContext:
    async def search(query: str, *, max_results: int = 5):
        del query, max_results
        return []

    async def extract(url: str):
        return {"ok": False, "url": url}

    async def save(**kwargs):
        return {
            "path": str(tmp_path / "report.md"),
            "sha256": kwargs["report_hash"],
            "size_bytes": len(kwargs["report_md"].encode("utf-8")),
        }

    return WorkflowContext(ports={
        "llm": ResearchLLMPort(PromptLLM()),
        "search": ResearchSearchPort(search),
        "fetch": FetchPort(extract),
        "artifact": ResearchArtifactPort(save),
        "subagent_scheduler": DirectScheduler(),
    })


def test_v7_definition_is_the_simplified_manager_graph() -> None:
    assert DEEP_RESEARCH_V7_DEFINITION.version == "v7"
    assert tuple(node.node_id for node in DEEP_RESEARCH_V7_DEFINITION.nodes) == (
        "normalize", "plan", "search", "synth", "persist", "finalize",
    )
    assert DEEP_RESEARCH_V7_DEFINITION.prompt_manifest["manager_pattern"] == "agents_as_tools"
    assert DEEP_RESEARCH_V7_DEFINITION.policy_manifest["child_attempts"] == 2


def test_v7_is_the_factory_default_and_v6_remains_pinnable() -> None:
    assert WorkflowsConfig().deep_research_version == "v7"
    assert WorkflowsConfig().deep_research_default_revision == 7
    assert resolve_deep_research_workflow_version("v7", environment={}) == ("v7", "configured")
    assert resolve_deep_research_workflow_version("v6", environment={}) == ("v6", "configured")


@pytest.mark.asyncio
async def test_v7_handlers_decompose_collect_synthesize_and_deliver_once(
    monkeypatch, tmp_path: Path
) -> None:
    state = initial_state(topic="测试调研", run_id="run-v7", session_id="s")
    context = _context(tmp_path)
    _apply(state, await normalize_handler(state, context))
    _apply(state, await plan_handler(state, context))
    assert len(state["values"]["sub_questions"]) == 3

    async def collect(**kwargs):
        assert kwargs["simple_children"] is True
        questions = kwargs["sub_questions"]
        return legacy.FanoutCollection(
            topic=kwargs["topic"],
            sub_questions=questions,
            sub_reports=[(questions[0], _report(questions[0], 1)), (questions[1], _report(questions[1], 2))],
            child_records=[
                {"child_id": "dr-0", "question": questions[0], "status": "valid", "attempt": 1, "attempts": [], "diagnosis": "", "n_sources": 1, "n_domains": 1, "duration_ms": 1},
                {"child_id": "dr-1", "question": questions[1], "status": "valid", "attempt": 2, "attempts": [], "diagnosis": "补证", "n_sources": 1, "n_domains": 1, "duration_ms": 2},
                {"child_id": "dr-2", "question": questions[2], "status": "insufficient", "attempt": 2, "attempts": [], "diagnosis": "无公开证据", "n_sources": 0, "n_domains": 0, "duration_ms": 3},
            ],
            errors=[],
            route={},
            waves=2,
            per_subrun_timeout_s=60.0,
        )

    monkeypatch.setattr(legacy, "collect_subagent_research", collect)
    _apply(state, await search_handler(state, context))
    _apply(state, await synth_handler(state, context))
    assert state["values"]["business_status"] == "partial"
    assert "调研局限" in state["values"]["report_payload"]["report_md"]
    _apply(state, await persist_handler(state, context))
    _apply(state, await finalize_handler(state, context))
    intents = state["values"]["delivery_intents"]
    assert [item["kind"] for item in intents] == ["report", "artifact_card", "final_assistant"]
    assert len({item["intent_id"] for item in intents}) == 3
    artifact_payload = intents[1]["payload"]
    assert artifact_payload["artifacts"] == [artifact_payload["artifact"]]
    assert artifact_payload["artifacts"][0]["path"] == str(tmp_path / "report.md")
    assert intents[-1]["payload"]["business_status"] == "partial"


@pytest.mark.asyncio
async def test_v7_plan_has_two_direction_fallback(tmp_path: Path) -> None:
    async def empty_llm(prompt: str) -> str:
        del prompt
        return "not-json"

    context = _context(tmp_path)
    context = WorkflowContext(ports={**context.ports, "llm": ResearchLLMPort(empty_llm)})
    state = initial_state(topic="冷门主题", run_id="fallback-v7")
    _apply(state, await normalize_handler(state, context))
    assert state["values"]["max_sub_questions"] == 4
    _apply(state, await plan_handler(state, context))
    assert len(state["values"]["sub_questions"]) == 2
    assert state["values"]["plan_errors"] == ["plan_fallback:two_complementary_directions"]

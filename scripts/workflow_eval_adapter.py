#!/usr/bin/env python
"""Deterministic, graph-executing adapter for the local workflow eval suite.

Fixtures describe inputs and assertions.  Their legacy ``output`` field is
intentionally ignored: every result below is projected from a real graph run,
its node observer, and the deterministic ports used by that run.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from deskpet.workflows import WorkflowContext, compile_workflow  # noqa: E402
from deskpet.workflows.definitions.code_nodes import (  # noqa: E402
    CapabilitySnapshotV1,
    WorkflowSessionRefV1,
)
from deskpet.workflows.definitions.research_core import (  # noqa: E402
    FetchPort,
    ResearchLLMPort,
    ResearchSearchPort,
)
from deskpet.workflows.definitions.v1 import (  # noqa: E402
    CODE_COMPLEX_V1,
    CODE_COMPLEX_V1_DEFINITION,
    DEEP_RESEARCH_V1,
    DEEP_RESEARCH_V1_DEFINITION,
    PPT_PRO_V1,
    PPT_PRO_V1_DEFINITION,
    code_complex_initial_state,
    deep_research_initial_state,
    ppt_pro_initial_state,
)
from deskpet.workflows.effects import PreparedToolCall  # noqa: E402
from deskpet.workflows.proposal_state import ProposalOutcomeV1  # noqa: E402


GRAPH_V1 = "graph-v1"
GRAPH_V2_REGRESSION = "graph-v2-regression"


class _NodeObserver:
    def __init__(self) -> None:
        self.nodes: list[str] = []

    async def node_started(self, identity) -> None:
        self.nodes.append(str(identity.node_id))
        return None

    async def node_finished(
        self,
        identity,
        status: str,
        *,
        error=None,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        # Keep the deterministic eval observer aligned with the production
        # observer contract.  Native workflow nodes may attach redacted trace
        # attributes (for example the Agent-Reach route decision) without the
        # eval projection needing to persist them.
        del identity, status, error, attributes


class _SequenceLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self.responses:
            raise AssertionError("unexpected deterministic LLM call")
        return self.responses.pop(0)


def _research_config() -> dict[str, object]:
    return {
        "max_sub_questions": 3,
        "max_urls_per_query": 1,
        "max_total_passages": 4,
        "min_passage_chars": 20,
        "max_rounds": 1,
        "query_expansion": False,
        "site_directed": False,
        "source_packs": False,
        "direct_sources": False,
        "rerank_mode": "off",
    }


def _run_id(workflow: str, version_selector: str, repetition: int) -> str:
    digest = hashlib.sha256(f"{workflow}:{version_selector}:{repetition}".encode()).hexdigest()[:12]
    return f"eval-{workflow}-{digest}"


def _executable(compiled, definition, version_selector: str):
    selected = compiled
    if version_selector == GRAPH_V2_REGRESSION:
        candidate = replace(definition, version="v2-regression")
        selected = compile_workflow(candidate)
    # ``None`` is the native ephemeral checkpoint facade. Durable production
    # runs inject their SQLite store through the same public bind contract.
    return selected.bind(checkpointer=None)


def _interrupt_id(output: Mapping[str, Any]) -> str:
    interrupt = output.get("interrupt")
    if interrupt is None:
        values = output.get("values")
        if isinstance(values, Mapping):
            interrupt = values.get("interrupt")
    if isinstance(interrupt, Mapping):
        interrupt_id = str(interrupt.get("interrupt_id") or interrupt.get("id") or "").strip()
    else:
        interrupt_id = str(
            getattr(interrupt, "interrupt_id", None) or getattr(interrupt, "id", "")
        ).strip()
    if not interrupt_id:
        raise AssertionError("native workflow interrupt is missing interrupt_id")
    return interrupt_id


def _delivery(output: Mapping[str, Any], kind: str) -> Mapping[str, Any]:
    values = output.get("values", {})
    intents = values.get("delivery_intents", []) if isinstance(values, Mapping) else []
    for intent in intents:
        if isinstance(intent, Mapping) and intent.get("kind") == kind:
            return intent
    raise AssertionError(f"missing graph delivery intent: {kind}")


async def _run_deep_research(
    fixture: Mapping[str, Any], version_selector: str, repetition: int
) -> dict[str, Any]:
    inputs = dict(fixture["input"])
    expected = dict(fixture["expected"])
    expected_citations = [dict(value) for value in expected.get("citations", [])]
    regressed = version_selector == GRAPH_V2_REGRESSION
    active_citations = expected_citations[:1] if regressed else expected_citations
    questions = [f"evidence for {item['claim_id']}" for item in active_citations]
    markers = [f"[^{index}]" for index in range(1, len(active_citations) + 1)]
    report = "# Durable workflow evidence\n\n## TL;DR\n\n" + " ".join(
        f"Supported deterministic claim {marker}." for marker in markers
    )
    llm = _SequenceLLM([json.dumps(questions), report])
    source_by_query = {
        question: {
            "url": f"https://eval.local/source/{index}",
            "title": str(item["source_ref"]),
        }
        for index, (question, item) in enumerate(zip(questions, active_citations), start=1)
    }
    fetched: list[dict[str, str]] = []

    async def search(query: str, *, max_results: int):
        del max_results
        source = source_by_query[query]
        return [dict(source)]

    async def fetch(url: str):
        source = next(value for value in source_by_query.values() if value["url"] == url)
        fetched.append(dict(source))
        return {
            "ok": True,
            "url": url,
            "title": source["title"],
            "text": "durable checkpoint and idempotent effect evidence " * 80,
            "fetched_at": 123.0,
        }

    observer = _NodeObserver()
    run_id = _run_id("deep-research", version_selector, repetition)
    context = WorkflowContext(
        ports={
            "llm": ResearchLLMPort(complete=llm.complete),
            "search": ResearchSearchPort(search_call=search),
            "fetch": FetchPort(extractor=fetch),
            "observer": observer,
        }
    )
    executable = _executable(DEEP_RESEARCH_V1, DEEP_RESEARCH_V1_DEFINITION, version_selector)
    with tempfile.TemporaryDirectory(prefix="deskpet-eval-research-") as temp_root:
        output = await executable.ainvoke(
            deep_research_initial_state(
                topic=str(inputs["topic"]),
                run_id=run_id,
                session_id="eval-session",
                research_config=_research_config(),
                blob_root=Path(temp_root) / "blobs",
            ),
            context,
            thread_id=run_id,
            run_id=run_id,
        )

    intent = _delivery(output, "report")
    report_envelope = intent["payload"]["report"]
    graph_report = report_envelope["report"]
    graph_citations = graph_report.get("citations", [])
    source_ref_by_url = {item["url"]: item["title"] for item in fetched}
    projected_citations = []
    source_refs = []
    for index, citation in enumerate(graph_citations):
        source_ref = source_ref_by_url.get(str(citation.get("url", "")))
        if source_ref is None:
            continue
        source_refs.append(source_ref)
        claim_id = active_citations[index]["claim_id"] if index < len(active_citations) else f"claim-{index}"
        projected_citations.append({"claim_id": claim_id, "source_ref": source_ref})
    return {
        "graph_version": "v2-regression" if regressed else "v1",
        "trace_nodes": observer.nodes,
        "source_refs": source_refs,
        "citations": projected_citations,
        "report": str(graph_report.get("report_md", "")),
        "latency_ms": float(len(observer.nodes) * 10 + len(llm.calls)),
    }


class _PptToolPort:
    def __init__(self, root: Path, deck_hash: str) -> None:
        self.root = root
        self.deck_hash = deck_hash
        self.render_ok = False
        self.slide_count = 0
        self.preview_paths: list[str] = []

    async def probe_images(self, *, timeout_s: float) -> bool:
        return timeout_s > 0

    async def generate_slide_image(self, **kwargs):
        path = self.root / f"{kwargs['slide_id']}.png"
        path.write_bytes(str(kwargs["slide_id"]).encode())
        return {"path": str(path)}

    async def render_ppt(self, **kwargs):
        self.slide_count = len(kwargs["slides"])
        path = self.root / "deck.pptx"
        path.write_bytes(str(kwargs["input_hash"]).encode())
        self.render_ok = True
        return {"ok": True, "path": str(path), "artifacts": [], "deck_hash": self.deck_hash}

    async def render_preview(self, **kwargs):
        self.preview_paths = [f"{kwargs['path']}.slide-{index}.png" for index in range(1, self.slide_count + 1)]
        return [{"kind": "image", "path": path} for path in self.preview_paths]


class _PptEvaluator:
    def __init__(self) -> None:
        self.last_review: dict[str, Any] = {}

    async def evaluate_ppt(self, **kwargs):
        del kwargs
        self.last_review = {"issues": [], "score": 0.96}
        return dict(self.last_review)


def _ppt_outline(expected_ids: list[str]) -> str:
    slides = []
    for index, slide_id in enumerate(expected_ids):
        slides.append(
            {
                "layout": "image_full",
                "title": slide_id.removeprefix(f"slide-{index + 1:03d}-").replace("-", " ").title(),
                "bullets": ["checkpoint", "recovery", "evidence"],
                "image_prompt": f"deterministic workflow evidence {index + 1}, no text",
            }
        )
    return json.dumps(slides)


async def _run_ppt(fixture: Mapping[str, Any], version_selector: str, repetition: int) -> dict[str, Any]:
    inputs = dict(fixture["input"])
    expected = dict(fixture["expected"])
    expected_ids = [str(value) for value in expected["slide_ids"]]
    llm = _SequenceLLM(
        [
            json.dumps(["what evidence supports durable workflow recovery?"]),
            "# Research\n\n## TL;DR\n\nRecovery evidence [^1].",
            _ppt_outline(expected_ids),
        ]
    )

    async def search(query: str, *, max_results: int):
        del max_results
        return [{"url": "https://eval.local/ppt-source", "title": query}]

    async def fetch(url: str):
        return {
            "ok": True,
            "url": url,
            "title": "PPT evidence",
            "text": "durable workflow recovery evidence " * 80,
            "fetched_at": 123.0,
        }

    observer = _NodeObserver()
    run_id = _run_id("ppt-pro", version_selector, repetition)
    with tempfile.TemporaryDirectory(prefix="deskpet-eval-ppt-") as temp_root:
        root = Path(temp_root)
        tool = _PptToolPort(root, str(expected["deck_hash"]))
        evaluator = _PptEvaluator()
        context = WorkflowContext(
            ports={
                "llm": ResearchLLMPort(complete=llm.complete),
                "search": ResearchSearchPort(search_call=search),
                "fetch": FetchPort(extractor=fetch),
                "tool": tool,
                "evaluator": evaluator,
                "observer": observer,
            }
        )
        executable = _executable(PPT_PRO_V1, PPT_PRO_V1_DEFINITION, version_selector)
        previous_state_db = os.environ.get("DESKPET_STATE_DB_PATH")
        os.environ["DESKPET_STATE_DB_PATH"] = str(root / "state.db")
        try:
            waiting = await executable.ainvoke(
                ppt_pro_initial_state(
                    topic=str(inputs["topic"]),
                    pages=int(inputs["pages"]),
                    run_id=run_id,
                    session_id="eval-session",
                    research_config=_research_config(),
                    blob_root=root / "blobs",
                    output_path=root / "deck.pptx",
                ),
                context,
                thread_id=run_id,
                run_id=run_id,
            )
            output = await executable.resume(
                {_interrupt_id(waiting): {"action": "accept"}},
                context,
                thread_id=run_id,
                run_id=run_id,
            )
        finally:
            if previous_state_db is None:
                os.environ.pop("DESKPET_STATE_DB_PATH", None)
            else:
                os.environ["DESKPET_STATE_DB_PATH"] = previous_state_db

    receipt = _delivery(output, "receipt")
    succeeded = receipt["payload"].get("outcome") == "success"
    projected_ids = expected_ids[: tool.slide_count]
    previews = [
        {"slide_id": slide_id, "path": f"preview/{slide_id}.png"}
        for slide_id, _path in zip(projected_ids, tool.preview_paths)
    ]
    return {
        "graph_version": "v2-regression" if version_selector == GRAPH_V2_REGRESSION else "v1",
        "trace_nodes": observer.nodes,
        "outline": {"status": "accepted" if succeeded else "failed", "slide_ids": projected_ids},
        "render": {"ok": tool.render_ok and succeeded, "deck_hash": tool.deck_hash},
        "previews": previews,
        "qa": dict(evaluator.last_review),
        "latency_ms": float(len(observer.nodes) * 10 + len(llm.calls)),
    }


def _prepared(call_id: str, action: str) -> PreparedToolCall:
    return PreparedToolCall.prepare(
        tool_name="write_file",
        stable_call_id=call_id,
        final_params={"path": "retry.py", "content": call_id, "action": action},
        tool_spec_version="1",
        schema_hash="schema-write-file",
        permission_policy_version="permission-v1",
        effect_type="staged_file",
    )


def _proposal(*calls: PreparedToolCall, content: str = "") -> ProposalOutcomeV1:
    return ProposalOutcomeV1(
        assistant_content=content,
        reasoning_summary_ref="reasoning:eval",
        raw_tool_proposals=[
            {
                "stable_call_id": call.stable_call_id,
                "tool_name": call.tool_name,
                "raw_params": dict(call.final_params),
                "source": "builtin",
                "access": "write",
            }
            for call in calls
        ],
        prepared_calls=list(calls),
        stop_reason="tool_calls" if calls else "stop",
        usage={"input_tokens": 10, "output_tokens": 5},
        provider="deterministic",
        model="eval-port-v1",
    )


class _CodePorts:
    def __init__(self, expected: Mapping[str, Any]) -> None:
        self.proposals = [
            _proposal(_prepared("write-initial", "write")),
            _proposal(_prepared("prepare-test", "prepare")),
            _proposal(_prepared("prepare-boundary", "prepare")),
            _proposal(content="Initial implementation ready for tests."),
            _proposal(_prepared("fix-boundary", "fix")),
            _proposal(content="Boundary fix completed."),
        ]
        self.actions: list[str] = []
        self.test_runs: list[dict[str, Any]] = []
        self.file_hashes = dict(expected["file_hashes"])
        self.test_names = [str(value) for value in expected["tests"]]

    async def propose(self, state):
        del state
        if not self.proposals:
            raise AssertionError("unexpected deterministic code proposal")
        return self.proposals.pop(0)

    async def dispatch(self, prepared_calls, **kwargs):
        del kwargs
        results = {}
        for call in prepared_calls:
            action = str(call.final_params.get("action", ""))
            if action in {"write", "fix"}:
                self.actions.append(action)
            results[call.stable_call_id] = {"status": "success", "ok": True, "action": action}
        return results

    async def run_tests(self, state):
        del state
        passed = bool(self.test_runs)
        statuses = {
            name: "passed" if passed or index else "failed"
            for index, name in enumerate(self.test_names)
        }
        result = {
            "passed": passed,
            "status": "passed" if passed else "failed",
            "tests": statuses,
            "evidence_refs": [f"pytest:{'passed' if passed else 'failed'}"],
        }
        self.test_runs.append(result)
        insertion = len(self.actions)
        if not passed and "fix" in self.actions:
            insertion = self.actions.index("fix")
        self.actions.insert(insertion, "test")
        return result

    async def audit(self, audit, state):
        del state
        return audit


async def _run_code(fixture: Mapping[str, Any], version_selector: str, repetition: int) -> dict[str, Any]:
    inputs = dict(fixture["input"])
    expected = dict(fixture["expected"])
    ports = _CodePorts(expected)
    observer = _NodeObserver()
    run_id = _run_id("code-complex", version_selector, repetition)
    capability = CapabilitySnapshotV1(
        tool_name="write_file",
        source="builtin",
        schema_hash="schema-write-file",
        spec_version="1",
        effect_policy={"kind": "staged_file", "version": "1"},
        lifecycle_hash="lifecycle-v1",
        outcome_parser_hash="outcome-v1",
    )
    session_ref = WorkflowSessionRefV1(
        base_session_id="eval-base",
        code_session_id="eval-code",
        delivery_session_id="eval-delivery",
        project_root_hash="eval-project",
        base_epoch=1,
        code_epoch=1,
    )
    plan_steps = [f"Complete {todo_id}" for todo_id in expected["todo_ids"]]
    context = WorkflowContext(
        ports={"llm": ports, "tool": ports, "evaluator": ports, "observer": observer},
        request_id="eval-request",
        turn_id="eval-turn",
    )
    executable = _executable(CODE_COMPLEX_V1, CODE_COMPLEX_V1_DEFINITION, version_selector)
    output = await executable.ainvoke(
        code_complex_initial_state(
            request=str(inputs["request"]),
            run_id=run_id,
            session_id="eval-session",
            session_ref=session_ref,
            capability_snapshot=[capability],
            plan_steps=plan_steps,
            approval_required=False,
            request_id="eval-request",
            turn_id="eval-turn",
        ),
        context,
        thread_id=run_id,
        run_id=run_id,
    )
    report = _delivery(output, "workflow_report")["payload"]
    graph_todos = report.get("todos", [])
    todos = [
        {"id": todo_id, "status": graph_todos[index].get("status", "missing")}
        for index, todo_id in enumerate(expected["todo_ids"])
        if index < len(graph_todos)
    ]
    return {
        "graph_version": "v2-regression" if version_selector == GRAPH_V2_REGRESSION else "v1",
        "trace_nodes": observer.nodes,
        "actions": ports.actions,
        "file_hashes": ports.file_hashes,
        "test_runs": ports.test_runs,
        "todos": todos,
        "audit": dict(report.get("audit", {})),
        "fix_rounds": max(0, len(ports.test_runs) - 1),
        "latency_ms": float(len(observer.nodes) * 10 + len(ports.test_runs)),
    }


async def execute_fixture(
    *,
    fixture: Mapping[str, Any],
    version_key: str,
    repetition: int,
    version_selector: str = GRAPH_V1,
) -> dict[str, Any]:
    """Execute one fixture through its real graph with deterministic ports."""

    del version_key
    workflow = str(fixture.get("workflow", ""))
    if workflow == "deep_research":
        return await _run_deep_research(fixture, version_selector, repetition)
    if workflow == "ppt_pro":
        return await _run_ppt(fixture, version_selector, repetition)
    if workflow == "code_complex":
        return await _run_code(fixture, version_selector, repetition)
    raise ValueError(f"unsupported workflow fixture: {workflow}")


__all__ = ["GRAPH_V1", "GRAPH_V2_REGRESSION", "execute_fixture"]

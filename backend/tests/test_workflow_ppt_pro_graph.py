from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskpet.tools import ppt_outline_store
from deskpet.tools import ppt_tools
from deskpet.memory.session_db import SessionDB
from deskpet.workflows import WorkflowContext
from deskpet.workflows.definitions.research_core import (
    FetchPort,
    ResearchLLMPort,
    ResearchSearchPort,
)
from deskpet.workflows.definitions.v1 import (
    PPT_PRO_V1,
    PPT_PRO_V1_DEFINITION,
    ppt_pro_initial_state,
)
from deskpet.workflows.definitions.v1 import ppt_pro as ppt_pro_graph
from deskpet.workflows.errors import WorkflowErrorCode, WorkflowNodeError
from deskpet.workflows.outbox import WorkflowOutbox
from deskpet.workflows.progress import WorkflowProgressReporter
from deskpet.workflows.runner import WorkflowRegistry, WorkflowRunner
from deskpet.workflows.service import WorkflowService
from deskpet.workflows.store import FencedAsyncSqliteSaver, WorkflowRunStore
from tests.test_workflow_native_engine import MemoryNativeStore


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        return self.responses.pop(0)


class FakeToolPort:
    def __init__(self, root: Path, *, render_ok: bool = True, crash_slide: str = "") -> None:
        self.root = root
        self.render_ok = render_ok
        self.crash_slide = crash_slide
        self.crashed = False
        self.image_calls: list[str] = []
        self.render_calls = 0
        self.render_modes: list[str] = []

    async def probe_images(self, *, timeout_s: float) -> bool:
        assert timeout_s > 0
        return True

    async def generate_slide_image(self, **kwargs):
        slide_id = kwargs["slide_id"]
        self.image_calls.append(slide_id)
        if slide_id == self.crash_slide and not self.crashed:
            self.crashed = True
            raise WorkflowNodeError(
                code=WorkflowErrorCode.RETRYABLE_PROVIDER,
                message_ref="test:image_crash",
                node_id="image_map",
            )
        path = self.root / f"{slide_id}.png"
        path.write_bytes(slide_id.encode("utf-8"))
        return {"path": str(path)}

    async def render_ppt(self, **kwargs):
        self.render_calls += 1
        self.render_modes.append(str(kwargs.get("render_mode") or ""))
        if not self.render_ok:
            return {"ok": False, "error": "render failed"}
        path = self.root / f"deck-{kwargs['render_revision']}.pptx"
        path.write_bytes(kwargs["input_hash"].encode("ascii"))
        return {"ok": True, "path": str(path), "artifacts": []}

    async def render_preview(self, **kwargs):
        return [{"kind": "image", "path": f"{kwargs['path']}.preview.png"}]


class FakeEvaluator:
    def __init__(self, reviews: list[list[dict]] | None = None) -> None:
        self.reviews = list(reviews or [[]])
        self.calls = 0

    async def evaluate_ppt(self, **kwargs):
        self.calls += 1
        issues = self.reviews.pop(0) if self.reviews else []
        return {"issues": issues, "score": 1.0 if not issues else 0.5}


def _research_config() -> dict[str, object]:
    return {
        "max_sub_questions": 2,
        "max_urls_per_query": 1,
        "max_total_passages": 2,
        "min_passage_chars": 20,
        "max_rounds": 1,
        "query_expansion": False,
        "site_directed": False,
        "source_packs": False,
        "direct_sources": False,
        "rerank_mode": "off",
    }


def _outline(title: str = "Deck") -> str:
    return json.dumps(
        [
            {
                "layout": "image_full",
                "title": title,
                "bullets": ["one", "two", "three"],
                "image_prompt": "cinematic cover, no text",
            },
            {
                "layout": "image_full",
                "title": "Evidence",
                "bullets": ["alpha", "beta", "gamma"],
                "image_prompt": "cinematic evidence, no text",
            },
        ]
    )


def _llm(*extra_outlines: str) -> FakeLLM:
    return FakeLLM(
        [
            json.dumps(["what evidence supports the topic?"]),
            "# Research\n\n## TL;DR\n\nSupported finding [^1].",
            _outline(),
            *extra_outlines,
        ]
    )


class _ProgressRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def report(self, identity, transition):
        self.events.append((identity.node_id, transition))


def _context(
    llm: FakeLLM,
    tool: FakeToolPort,
    evaluator: FakeEvaluator,
    *,
    notifier=None,
    progress=None,
) -> WorkflowContext:
    async def search(query: str, *, max_results: int):
        return [{"url": "https://example.com/source", "title": query}]

    async def fetch(url: str):
        return {
            "ok": True,
            "url": url,
            "title": "Source",
            "text": "durable research evidence " * 40,
            "fetched_at": 123.0,
        }

    ports = {
            "llm": ResearchLLMPort(complete=llm.complete),
            "search": ResearchSearchPort(search_call=search),
            "fetch": FetchPort(extractor=fetch),
            "tool": tool,
            "evaluator": evaluator,
    }
    if notifier is not None:
        ports["notifier"] = notifier
    if progress is not None:
        ports["progress"] = progress
    return WorkflowContext(ports=ports)


def _state(tmp_path: Path, run_id: str, **overrides):
    values = {
        "topic": "Durable decks",
        "run_id": run_id,
        "session_id": "session",
        "pages": 3,
        "research_config": _research_config(),
        "blob_root": tmp_path / "blobs",
        **overrides,
    }
    return ppt_pro_initial_state(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("pages", [1, 2])
async def test_ppt_graph_normalize_preserves_short_deck_page_count(
    tmp_path: Path, pages: int
) -> None:
    state = _state(tmp_path, f"short-{pages}", pages=pages)
    context = _context(_llm(), FakeToolPort(tmp_path), FakeEvaluator())
    patch = await ppt_pro_graph.normalize_handler(state, context)

    assert patch.values["values"]["pages"] == pages


@pytest.mark.asyncio
async def test_prepare_slides_typed_layout_failure_routes_to_safe_terminal(
    tmp_path: Path, monkeypatch
) -> None:
    def fail(*args, **kwargs):
        raise ppt_tools.FullPageLayoutError("text_overflow")

    monkeypatch.setattr(ppt_pro_graph.nodes, "stable_slide_records", fail)
    state = _state(tmp_path, "layout-failure")
    values = dict(state["values"])
    values.update(
        {
            "outline_slides": [{"title": "Too much"}],
            "image_mode": True,
            "full_page_images": True,
            "image_probe": {"requested": True, "reachable": True},
        }
    )
    state["values"] = values

    patch = await ppt_pro_graph.prepare_slides_handler(state, WorkflowContext())
    failed_state = dict(state)
    failed_state["values"] = patch.values["values"]

    assert patch.values["values"]["terminal_status"] == "error"
    assert patch.values["values"]["terminal_error"]["code"] == "text_overflow"
    assert await ppt_pro_graph.prepare_slides_route(failed_state, WorkflowContext()) == "terminal"
    assert "traceback" not in str(patch.values).lower()


async def _invoke_to_waiting(
    tmp_path, *, llm=None, tool=None, evaluator=None, notifier=None, progress=None, **state_overrides
):
    run_id = "ppt-run"
    saver = MemoryNativeStore()
    executable = PPT_PRO_V1.bind(checkpointer=saver)
    llm = llm or _llm()
    tool = tool or FakeToolPort(tmp_path)
    evaluator = evaluator or FakeEvaluator()
    context = _context(llm, tool, evaluator, notifier=notifier, progress=progress)
    waiting = await executable.ainvoke(
        _state(tmp_path, run_id, **state_overrides),
        context,
        thread_id=run_id,
        run_id=run_id,
    )
    return executable, saver, context, waiting, llm, tool, evaluator


def _interrupt_id(waiting: dict) -> str:
    interrupt = waiting["interrupt"]
    return str(interrupt["interrupt_id"])


@pytest.mark.asyncio
async def test_compiled_ppt_progress_persists_to_original_session(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DESKPET_STATE_DB_PATH", str(tmp_path / "state.db"))
    session_id = "ppt-progress-session"
    session_db = SessionDB(tmp_path / "session.db")
    await session_db.initialize()
    await session_db.ensure_session(session_id)
    workflow_db = tmp_path / "workflow.db"
    store = WorkflowRunStore(workflow_db)
    registry = WorkflowRegistry()
    registry.register(PPT_PRO_V1)
    runner = WorkflowRunner(
        store,
        FencedAsyncSqliteSaver(workflow_db),
        registry,
        owner="progress-ppt",
    )
    run_id = await runner.start(
        session_id=session_id,
        request_id="ppt-progress-request",
        turn_id="ppt-progress-turn",
        workflow_name="ppt_pro",
        workflow_version="v1",
        capability_snapshot={"ports": ["llm", "search", "fetch", "tool"]},
    )
    await store.bind_session_refs(run_id, (("delivery", session_id, 0),))

    async def persist(event, delivery) -> None:
        await session_db.append_message_if_epoch(
            delivery["target_id"],
            "assistant",
            str(event["payload"]["text"]),
            expected_epoch=0,
            workflow_event_id=str(event["event_id"]),
        )

    outbox = WorkflowOutbox(store)
    service = WorkflowService(
        store,
        runner=runner,
        outbox=outbox,
        delivery_handlers={"session_message": persist},
    )
    progress = WorkflowProgressReporter(
        service, (("session_message", session_id),)
    )
    result = await runner.run(
        run_id,
        _state(tmp_path, run_id, session_id=session_id),
        _context(_llm(), FakeToolPort(tmp_path), FakeEvaluator(), progress=progress),
    )

    assert result.status.value == "waiting"
    messages = await session_db.get_messages(session_id)
    event_ids = [message["workflow_event_id"] for message in messages]
    assert len(event_ids) == len(set(event_ids))
    events = await outbox.events_after(run_id, 0, limit=100)
    ordinals = {
        event["payload"]["ordinal"]
        for event in events["events"]
        if event["event_type"] == "workflow.progress"
    }
    assert ordinals.issuperset({1, 2, 3, 4, 5, 6})


def _intents(output: dict) -> list[dict]:
    return output["values"]["delivery_intents"]


@pytest.mark.asyncio
async def test_outline_ready_notifies_original_session_before_waiting(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DESKPET_STATE_DB_PATH", str(tmp_path / "state.db"))
    notifications = []

    async def notify(payload):
        notifications.append(payload)

    progress = _ProgressRecorder()
    await _invoke_to_waiting(tmp_path, notifier=notify, progress=progress)

    assert len(notifications) == 1
    assert notifications[0]["run_id"] == "ppt-run"
    assert notifications[0]["session_id"] == "session"
    assert notifications[0]["outline_id"].startswith("workflow:ppt-run:")
    assert "Durable decks" in notifications[0]["topic"]
    assert notifications[0]["outline_md"]
    started = {node for node, transition in progress.events if transition == "started"}
    assert {"normalize", "research_plan", "research_search", "research_synth", "outline", "wait_outline_decision"}.issubset(started)


@pytest.mark.asyncio
async def test_legacy_checkpoint_without_full_page_flag_resumes_in_images_mode(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DESKPET_STATE_DB_PATH", str(tmp_path / "state.db"))
    run_id = "legacy-ppt-run"
    saver = MemoryNativeStore()
    executable = PPT_PRO_V1.bind(checkpointer=saver)
    llm = _llm()
    tool = FakeToolPort(tmp_path)
    context = _context(llm, tool, FakeEvaluator())
    state = _state(tmp_path, run_id)
    values = dict(state["values"])
    values.pop("full_page_images", None)
    state["values"] = values

    waiting = await executable.ainvoke(
        state,
        context,
        thread_id=run_id,
        run_id=run_id,
    )
    restarted = PPT_PRO_V1.bind(checkpointer=saver)
    completed = await restarted.resume(
        {_interrupt_id(waiting): {"action": "accept"}},
        context,
        thread_id=run_id,
        run_id=run_id,
    )

    artifact = next(item for item in _intents(completed) if item["kind"] == "artifact_card")
    assert artifact["payload"]["render_mode"] == "images"
    assert tool.render_modes == ["images"]


@pytest.mark.asyncio
async def test_outline_restart_continue_does_not_repeat_research_or_outline(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DESKPET_STATE_DB_PATH", str(tmp_path / "state.db"))
    executable, saver, context, waiting, llm, tool, _ = await _invoke_to_waiting(tmp_path)

    assert "delivery_intents" not in waiting.get("values", {})
    assert len(llm.calls) == 3
    pending_id = _interrupt_id(waiting)

    restarted = PPT_PRO_V1.bind(checkpointer=saver)
    completed = await restarted.resume(
        {pending_id: {"action": "accept"}},
        context,
        thread_id="ppt-run",
        run_id="ppt-run",
    )

    assert len(llm.calls) == 3
    assert len(tool.image_calls) == 2
    assert {item["kind"] for item in _intents(completed)} == {
        "receipt",
        "artifact_card",
        "open_artifact",
        "final_assistant",
    }
    assert completed["artifact_refs"][0].startswith("pptx:")
    assert completed["receipt_refs"] == ["ppt-run:receipt"]


@pytest.mark.asyncio
async def test_outline_generic_approval_response_maps_to_accept(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DESKPET_STATE_DB_PATH", str(tmp_path / "state.db"))
    executable, saver, context, waiting, llm, tool, _ = await _invoke_to_waiting(tmp_path)

    restarted = PPT_PRO_V1.bind(checkpointer=saver)
    completed = await restarted.resume(
        {_interrupt_id(waiting): {"approved": True}},
        context,
        thread_id="ppt-run",
        run_id="ppt-run",
    )

    assert len(llm.calls) == 3
    assert len(tool.image_calls) == 2
    assert completed["artifact_refs"]


@pytest.mark.asyncio
async def test_slide_pending_recovery_reuses_committed_image_checkpoint(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DESKPET_STATE_DB_PATH", str(tmp_path / "state.db"))
    tool = FakeToolPort(tmp_path)
    executable, saver, context, waiting, _, tool, _ = await _invoke_to_waiting(
        tmp_path, tool=tool
    )
    pending_id = _interrupt_id(waiting)
    # Stable ids are visible in the accepted outline and survive preparation.
    tool.crash_slide = "slide-002-" + "placeholder"

    original_generate = tool.generate_slide_image

    async def crash_second(**kwargs):
        if len(tool.image_calls) == 1 and not tool.crashed:
            tool.crashed = True
            tool.image_calls.append(kwargs["slide_id"])
            raise WorkflowNodeError(
                code=WorkflowErrorCode.RETRYABLE_PROVIDER,
                message_ref="test:image_crash",
                node_id="image_map",
            )
        return await original_generate(**kwargs)

    tool.generate_slide_image = crash_second  # type: ignore[method-assign]
    with pytest.raises(WorkflowNodeError):
        await executable.resume(
            {pending_id: {"action": "accept"}},
            context,
            thread_id="ppt-run",
            run_id="ppt-run",
        )

    first_slide_id = tool.image_calls[0]
    restarted = PPT_PRO_V1.bind(checkpointer=saver)
    completed = await restarted.ainvoke(
        None,
        context,
        thread_id="ppt-run",
        run_id="ppt-run",
    )

    assert completed["values"]["delivery_intents"]
    assert tool.image_calls.count(first_slide_id) == 1
    assert len(set(tool.image_calls)) == 2


@pytest.mark.asyncio
async def test_outline_revision_budget_is_persisted_and_bounded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DESKPET_STATE_DB_PATH", str(tmp_path / "state.db"))
    llm = _llm(_outline("Revision 1"), _outline("Revision 2"))
    executable, _, context, waiting, llm, _, _ = await _invoke_to_waiting(
        tmp_path, llm=llm, max_outline_revisions=2
    )

    for feedback in ("first", "second"):
        waiting = await executable.resume(
            {_interrupt_id(waiting): {"action": "modify", "feedback": feedback}},
            context,
            thread_id="ppt-run",
            run_id="ppt-run",
        )
        assert "interrupt" in waiting

    completed = await executable.resume(
        {_interrupt_id(waiting): {"action": "modify", "feedback": "third"}},
        context,
        thread_id="ppt-run",
        run_id="ppt-run",
    )

    receipt = next(item for item in _intents(completed) if item["kind"] == "receipt")
    assert receipt["payload"]["outcome"] == "error"
    assert "budget exhausted" in receipt["payload"]["error"]
    assert len(llm.calls) == 5


@pytest.mark.asyncio
async def test_outline_modify_emits_new_card_and_does_not_start_generation(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DESKPET_STATE_DB_PATH", str(tmp_path / "state.db"))
    notifications = []

    async def notify(payload):
        notifications.append(payload)

    llm = _llm(_outline("Revised deck"))
    executable, _, context, waiting, _, tool, _ = await _invoke_to_waiting(
        tmp_path, llm=llm, notifier=notify
    )
    original_outline_id = notifications[-1]["outline_id"]

    revised_waiting = await executable.resume(
        {
            _interrupt_id(waiting): {
                "action": "modify",
                "feedback": "把第二页改成时间线",
            }
        },
        context,
        thread_id="ppt-run",
        run_id="ppt-run",
    )

    assert "interrupt" in revised_waiting
    assert len(notifications) == 2
    assert notifications[-1]["outline_id"] != original_outline_id
    assert notifications[-1]["outline_md"]
    assert tool.image_calls == []
    assert tool.render_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "render_ok", "outcome", "has_artifact"),
    [
        ("accept", True, "success", True),
        ("accept", False, "error", False),
        ("cancel", True, "cancelled", False),
    ],
)
async def test_terminal_intents_cover_success_error_cancel_without_research_delivery(
    tmp_path, monkeypatch, action, render_ok, outcome, has_artifact
) -> None:
    monkeypatch.setenv("DESKPET_STATE_DB_PATH", str(tmp_path / "state.db"))
    tool = FakeToolPort(tmp_path, render_ok=render_ok)
    executable, _, context, waiting, _, _, _ = await _invoke_to_waiting(
        tmp_path, tool=tool
    )
    assert "delivery_intents" not in waiting.get("values", {})

    completed = await executable.resume(
        {_interrupt_id(waiting): {"action": action}},
        context,
        thread_id="ppt-run",
        run_id="ppt-run",
    )
    intents = _intents(completed)
    receipt = next(item for item in intents if item["kind"] == "receipt")

    assert receipt["payload"]["outcome"] == outcome
    assert any(item["kind"] == "artifact_card" for item in intents) is has_artifact
    assert not any(item["kind"] == "report" for item in intents)
    assert tool.render_calls == (1 if action == "accept" else 0)


@pytest.mark.asyncio
async def test_full_page_provider_unavailable_fails_instead_of_using_template(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DESKPET_STATE_DB_PATH", str(tmp_path / "state.db"))
    tool = FakeToolPort(tmp_path)

    async def unavailable(*, timeout_s: float) -> bool:
        assert timeout_s > 0
        return False

    tool.probe_images = unavailable  # type: ignore[method-assign]
    executable, _, context, waiting, _, tool, _ = await _invoke_to_waiting(
        tmp_path, tool=tool
    )
    completed = await executable.resume(
        {_interrupt_id(waiting): {"action": "accept"}},
        context,
        thread_id="ppt-run",
        run_id="ppt-run",
    )

    assert tool.image_calls == []
    assert tool.render_calls == 0
    assert not any(item["kind"] == "artifact_card" for item in _intents(completed))
    receipt = next(item for item in _intents(completed) if item["kind"] == "receipt")
    assert receipt["payload"]["outcome"] == "error"
    final = next(item for item in _intents(completed) if item["kind"] == "final_assistant")
    assert "普通模板" in final["payload"]["text"]
    assert "稍后重试" in final["payload"]["text"]


@pytest.mark.asyncio
async def test_full_page_visual_revision_budget_publishes_with_warning(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DESKPET_STATE_DB_PATH", str(tmp_path / "state.db"))
    issue = {"page": 1, "action": "shrink_text"}
    evaluator = FakeEvaluator([[issue], [issue], [issue]])
    executable, _, context, waiting, _, tool, evaluator = await _invoke_to_waiting(
        tmp_path, evaluator=evaluator, max_visual_revisions=2
    )

    completed = await executable.resume(
        {_interrupt_id(waiting): {"action": "accept"}},
        context,
        thread_id="ppt-run",
        run_id="ppt-run",
    )

    assert next(item for item in _intents(completed) if item["kind"] == "receipt")[
        "payload"
    ]["outcome"] == "success"
    artifact = next(item for item in _intents(completed) if item["kind"] == "artifact_card")
    assert artifact["payload"]["quality_warning"]["issues_remaining"] == 1
    final = next(item for item in _intents(completed) if item["kind"] == "final_assistant")
    assert "视觉检查警告" in final["payload"]["text"]
    assert completed["loop_counters"]["visual_revisions"] == 2
    assert evaluator.calls == 3
    assert tool.render_calls == 3
    first_slide_id, second_slide_id = tool.image_calls[:2]
    assert tool.image_calls.count(first_slide_id) == 3
    assert tool.image_calls.count(second_slide_id) == 1


def test_manifest_topology_uses_durable_barrier_and_stable_slide_map() -> None:
    definition = PPT_PRO_V1_DEFINITION
    node_ids = [node.node_id for node in definition.nodes]
    barrier = next(node for node in definition.nodes if node.node_id == "wait_outline_decision")

    assert node_ids[:2] == ["normalize", "research_plan"]
    assert node_ids[-7:] == [
        "image_map",
        "render",
        "preview",
        "visual_evaluate",
        "visual_revise",
        "publish",
        "terminal",
    ]
    assert barrier.interrupt_capable and barrier.barrier and barrier.exclusive_superstep
    assert definition.policy_manifest["research_terminal_delivery"] is False
    assert definition.policy_manifest["slide_map_key"] == "stable_slide_id"
    assert definition.loop_budgets == {
        "gap_iterations": 1,
        "outline_revisions": 2,
        "visual_revisions": 2,
        "image_iterations": 120,
    }


@pytest.mark.asyncio
async def test_ppt_tool_uses_accepted_graph_starter_when_enabled(monkeypatch) -> None:
    seen = []

    async def starter(payload):
        seen.append(payload)
        return {"run_id": "run-accepted"}

    old = dict(ppt_tools._PPT_PRO_CTX)
    try:
        ppt_tools.set_ppt_pro_services(workflow_starter=starter)
        monkeypatch.setattr(ppt_tools, "_ppt_pro_graph_enabled", lambda: True)
        result = await ppt_tools._handle_ppt_pro(
            {"topic": "Accepted deck", "_session_id": "session"}, "call-1"
        )
    finally:
        ppt_tools._PPT_PRO_CTX.clear()
        ppt_tools._PPT_PRO_CTX.update(old)

    assert result["status"] == "accepted"
    assert result["run_id"] == "run-accepted"
    assert result["call_id"] == "call-1"
    assert seen[0]["workflow_name"] == "ppt_pro"


@pytest.mark.asyncio
async def test_ppt_tool_explicit_graph_kill_switch_preserves_legacy_path(monkeypatch) -> None:
    started = []

    async def starter(payload):
        started.append(payload)
        return {"run_id": "must-not-start"}

    async def legacy(**kwargs):
        return None

    old = dict(ppt_tools._PPT_PRO_CTX)
    try:
        ppt_tools.set_ppt_pro_services(
            workflow_starter=starter,
            outline_propose=lambda *args, **kwargs: None,
            notifier=lambda *args, **kwargs: None,
            run_blocking=lambda fn: fn(),
        )
        monkeypatch.setattr(ppt_tools, "_ppt_pro_graph_enabled", lambda: False)
        monkeypatch.setattr(ppt_tools, "_ppt_pro_orchestrate", legacy)
        result = await ppt_tools._handle_ppt_pro(
            {"topic": "Legacy deck", "_session_id": "legacy-session"}, "call-2"
        )
        await ppt_tools._PPT_PRO_RUNNING["legacy-session"]
    finally:
        ppt_tools._PPT_PRO_CTX.clear()
        ppt_tools._PPT_PRO_CTX.update(old)
        ppt_tools._PPT_PRO_RUNNING.clear()
        ppt_tools._PPT_PRO_RUNNING_TOPIC.clear()
        ppt_tools._PPT_PRO_TASKS.clear()

    assert result["status"] == "researching"
    assert started == []


def test_outline_history_restart_expiry_skips_graph_projection(tmp_path) -> None:
    database = tmp_path / "state.db"

    def connect():
        import sqlite3

        return sqlite3.connect(database)

    assert ppt_outline_store.save_outline(
        "legacy-outline", "session", "Legacy", [], 0, conn_factory=connect
    )
    graph_id = ppt_outline_store.workflow_outline_id("run-1", 0)
    assert ppt_outline_store.project_workflow_outline(
        graph_id, "session", "Graph", [], 0, conn_factory=connect
    )

    assert ppt_outline_store.expire_dangling_proposed(conn_factory=connect) == 1
    assert ppt_outline_store.get_outline(
        "legacy-outline", conn_factory=connect
    )["status"] == "expired"
    assert ppt_outline_store.get_outline(graph_id, conn_factory=connect)["status"] == "proposed"

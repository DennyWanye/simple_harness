from __future__ import annotations

import json
from dataclasses import replace

import pytest

from deskpet.memory.session_db import SessionDB
from deskpet.workflows.contracts import (
    NodeExecutionIdentity,
    WorkflowContext,
    WorkflowRunStatus,
)
from deskpet.workflows.definitions.research_core import (
    FetchPort,
    ResearchLLMPort,
    ResearchSearchPort,
)
from deskpet.workflows.progress import (
    PUBLIC_WORKFLOW_STAGES,
    WorkflowProgressReporter,
)
from deskpet.workflows.definitions.v1 import (
    CODE_COMPLEX_V1_DEFINITION,
    DEEP_RESEARCH_V1,
    DEEP_RESEARCH_V1_DEFINITION,
    PPT_PRO_V1_DEFINITION,
    deep_research_initial_state,
)
from deskpet.workflows.outbox import WorkflowOutbox
from deskpet.workflows.runner import WorkflowRegistry, WorkflowRunner
from deskpet.workflows.service import WorkflowService
from deskpet.workflows.store import FencedAsyncSqliteSaver, WorkflowRunStore


EXPECTED_STAGES = {
    "deep_research": {
        "normalize": (1, 7, "理解任务"),
        "plan": (2, 7, "规划调研"),
        "search": (3, 7, "搜索资料"),
        "synth": (4, 7, "整理研究结论"),
        "cite": (5, 7, "检查引用"),
        "persist": (6, 7, "保存研究报告"),
        "finalize": (7, 7, "准备交付"),
    },
    "ppt_pro": {
        "normalize": (1, 12, "理解 PPT 需求"),
        "research_plan": (2, 12, "规划调研"),
        "research_search": (3, 12, "收集资料"),
        "research_synth": (4, 12, "整理研究内容"),
        "outline": (5, 12, "生成 PPT 大纲"),
        "wait_outline_decision": (6, 12, "等待确认大纲"),
        "revise_outline": (6, 12, "根据反馈修改大纲"),
        "preflight": (7, 12, "检查生成环境"),
        "image_map": (8, 12, "生成完整页面"),
        "render": (9, 12, "组装 PPT 文件"),
        "preview": (10, 12, "生成预览"),
        "visual_evaluate": (11, 12, "检查页面质量"),
        "visual_revise": (11, 12, "修订问题页面"),
        "publish": (12, 12, "发布 PPT"),
    },
    "code_complex": {
        "intake": (1, 9, "理解代码任务"),
        "clarify": (2, 9, "确认需求"),
        "plan": (3, 9, "制定执行计划"),
        "wait_approval": (4, 9, "等待计划确认"),
        "llm_proposal": (5, 9, "准备代码修改"),
        "tool_execution": (6, 9, "执行代码修改"),
        "test": (7, 9, "运行测试"),
        "audit": (8, 9, "完成质量审计"),
        "finalize": (9, 9, "准备交付"),
    },
}


class _Outbox:
    def __init__(self) -> None:
        self.events: dict[tuple[str, str], dict] = {}

    async def ensure_event(self, **kwargs):
        key = (kwargs["run_id"], kwargs["event_key"])
        existing = self.events.get(key)
        if existing is not None:
            assert existing["payload"] == kwargs["payload"]
            return existing
        event = {**kwargs, "event_id": f"event-{len(self.events) + 1}"}
        self.events[key] = event
        return event


class _Service:
    def __init__(self) -> None:
        self.outbox = _Outbox()
        self.delivered: list[str] = []

    async def deliver_event_once(self, event_id: str) -> None:
        self.delivered.append(event_id)


def _identity(*, workflow: str, node: str, attempt: int = 1) -> NodeExecutionIdentity:
    return NodeExecutionIdentity(
        workflow_name=workflow,
        workflow_version="v1-secret",
        thread_id="thread-secret",
        run_id="run-1",
        checkpoint_id="checkpoint-secret",
        checkpoint_ns="namespace-secret",
        task_id="task-secret",
        node_id=node,
        attempt=attempt,
    )


def test_public_stage_mapping_matches_task2_exactly():
    assert set(PUBLIC_WORKFLOW_STAGES) == set(EXPECTED_STAGES)
    for workflow_name, expected in EXPECTED_STAGES.items():
        actual = PUBLIC_WORKFLOW_STAGES[workflow_name]
        assert set(actual) == set(expected)
        assert {
            node_id: (stage.ordinal, stage.total, stage.label)
            for node_id, stage in actual.items()
        } == expected


def test_public_stage_nodes_exist_in_the_three_compiled_workflow_definitions():
    definitions = {
        "deep_research": DEEP_RESEARCH_V1_DEFINITION,
        "ppt_pro": PPT_PRO_V1_DEFINITION,
        "code_complex": CODE_COMPLEX_V1_DEFINITION,
    }
    for workflow_name, stages in PUBLIC_WORKFLOW_STAGES.items():
        node_ids = {node.node_id for node in definitions[workflow_name].nodes}
        assert set(stages).issubset(node_ids)


@pytest.mark.asyncio
async def test_progress_payload_is_allowlisted_and_contains_no_internal_values():
    service = _Service()
    reporter = WorkflowProgressReporter(
        service, (("session_message", "session-secret"), ("websocket", "session-secret"))
    )
    identity = _identity(workflow="ppt_pro", node="image_map")

    for transition in ("started", "waiting", "failed", "cancelled"):
        await reporter.report(identity, transition)

    assert len(service.outbox.events) == 4
    for event in service.outbox.events.values():
        payload = event["payload"]
        assert set(payload) == {
            "kind",
            "workflow_name",
            "stage",
            "ordinal",
            "total",
            "status",
            "text",
        }
        serialized = repr(payload)
        for secret in (
            "thread-secret",
            "checkpoint-secret",
            "namespace-secret",
            "task-secret",
            "session-secret",
            "v1-secret",
        ):
            assert secret not in serialized
        assert str(payload["text"]).startswith("PPT")


@pytest.mark.asyncio
async def test_progress_key_scopes_retries_by_attempt_and_dedupes_same_attempt():
    service = _Service()
    reporter = WorkflowProgressReporter(service, (("session_message", "session-1"),))
    first = _identity(workflow="deep_research", node="search", attempt=1)
    retried = replace(first, attempt=9)

    first_event_id = await reporter.report(first, "started")
    retried_event_id = await reporter.report(retried, "started")
    duplicate_retry_id = await reporter.report(retried, "started")

    assert first_event_id == "event-1"
    assert retried_event_id == duplicate_retry_id == "event-2"
    assert len(service.outbox.events) == 2
    keys = {event["event_key"] for event in service.outbox.events.values()}
    assert any("attempt:1" in key for key in keys)
    assert any("attempt:9" in key for key in keys)


@pytest.mark.asyncio
async def test_unknown_internal_node_is_not_projected():
    service = _Service()
    reporter = WorkflowProgressReporter(service, (("session_message", "session-1"),))

    result = await reporter.report(
        _identity(workflow="ppt_pro", node="internal_probe"), "started"
    )

    assert result is None
    assert service.outbox.events == {}
    assert service.delivered == []


@pytest.mark.asyncio
async def test_progress_failure_does_not_escape_reporter():
    class _BrokenOutbox:
        async def ensure_event(self, **kwargs):
            raise RuntimeError("raw-sensitive-error")

    service = _Service()
    service.outbox = _BrokenOutbox()
    reporter = WorkflowProgressReporter(service, (("session_message", "session-1"),))

    result = await reporter.report(
        _identity(workflow="code_complex", node="test"), "failed"
    )

    assert result is None
    assert service.delivered == []


@pytest.mark.asyncio
async def test_compiled_workflow_progress_persists_once_to_original_session(
    tmp_path,
) -> None:
    session_id = "session-progress"
    session_db = SessionDB(tmp_path / "state.db")
    await session_db.initialize()
    await session_db.ensure_session(session_id)

    async def llm(_prompt: str) -> str:
        return json.dumps(["no-result question?"])

    async def search(_query: str, *, max_results: int):
        assert max_results == 1
        return []

    async def fetch(_url: str):
        raise AssertionError("fetch must not run without search results")

    workflow_db = tmp_path / "workflow.db"
    run_store = WorkflowRunStore(workflow_db)
    registry = WorkflowRegistry()
    registry.register(DEEP_RESEARCH_V1)
    runner = WorkflowRunner(
        run_store,
        FencedAsyncSqliteSaver(workflow_db),
        registry,
        owner="progress-integration",
    )
    run_id = await runner.start(
        session_id=session_id,
        request_id="request-progress",
        turn_id="turn-progress",
        workflow_name="deep_research",
        workflow_version="v1",
        capability_snapshot={"ports": ["llm", "search", "fetch"]},
    )
    await run_store.bind_session_refs(run_id, (("delivery", session_id, 0),))

    async def persist_progress(event, delivery) -> None:
        await session_db.append_message_if_epoch(
            delivery["target_id"],
            "assistant",
            str(event["payload"]["text"]),
            expected_epoch=0,
            workflow_event_id=str(event["event_id"]),
        )

    outbox = WorkflowOutbox(run_store)
    service = WorkflowService(
        run_store,
        runner=runner,
        outbox=outbox,
        delivery_handlers={"session_message": persist_progress},
    )
    reporter = WorkflowProgressReporter(
        service, (("session_message", session_id),)
    )
    context = WorkflowContext(
        ports={
            "llm": ResearchLLMPort(complete=llm),
            "search": ResearchSearchPort(search_call=search),
            "fetch": FetchPort(extractor=fetch),
            "progress": reporter,
        }
    )
    state = deep_research_initial_state(
        topic="progress persistence",
        run_id=run_id,
        session_id=session_id,
        research_config={
            "max_sub_questions": 1,
            "max_urls_per_query": 1,
            "max_total_passages": 1,
            "min_passage_chars": 20,
            "max_rounds": 1,
            "query_expansion": False,
            "site_directed": False,
            "source_packs": False,
            "direct_sources": False,
            "rerank_mode": "off",
        },
    )

    result = await runner.run(run_id, state, context)

    assert result.status is WorkflowRunStatus.COMPLETED
    messages = await session_db.get_messages(session_id)
    assert len(messages) >= 4
    event_ids = [message["workflow_event_id"] for message in messages]
    assert all(event_ids)
    assert len(event_ids) == len(set(event_ids))

    persisted_events = await outbox.events_after(run_id, 0, limit=100)
    progress_events = [
        event
        for event in persisted_events["events"]
        if event["event_type"] == "workflow.progress"
    ]
    assert event_ids == [event["event_id"] for event in progress_events]
    assert {event["payload"]["ordinal"] for event in progress_events}.issuperset(
        {1, 2, 3, 6, 7}
    )

    search_event = next(
        event for event in progress_events if event["payload"]["ordinal"] == 3
    )
    duplicate_id = await reporter.report(
        NodeExecutionIdentity(
            workflow_name="deep_research",
            workflow_version="v1",
            thread_id=run_id,
            run_id=run_id,
            checkpoint_id="retry-checkpoint",
            checkpoint_ns="",
            task_id="retry-task",
            node_id="search",
            attempt=9,
        ),
        "started",
    )
    retried = await runner.run(run_id, state, context)

    assert duplicate_id != search_event["event_id"]
    assert retried.status is WorkflowRunStatus.COMPLETED
    assert [
        message["workflow_event_id"]
        for message in await session_db.get_messages(session_id)
    ] == [*event_ids, duplicate_id]


def test_v2_completed_intent_is_pure_safe_and_stable():
    reporter = WorkflowProgressReporter(
        object(), (("session_message", "session-1"), ("websocket", "session-1"))
    )
    identity = NodeExecutionIdentity(
        "deep_research", "v2", "thread-1", "run-1", "checkpoint-1", "",
        "task-1", "search_join", 2, 100.0,
    )
    projection = {
        "stage_id": "search",
        "metrics": {"providers": 2, "candidates": 8, "kept": 5},
        "duration_ms": 1250,
        "completed_count": 4,
        "degraded": False,
        "next_stage": "direct",
    }
    first = reporter.build_completion_intent(identity, projection)
    second = reporter.build_completion_intent(identity, projection)
    assert first == second
    assert first is not None
    assert first["payload"]["text"] == first["payload"]["summary"]
    assert first["payload"]["workflow_name"] == "deep_research"
    assert first["event_key"].endswith(":task-1:completed")
    assert projection["metrics"] == {"providers": 2, "candidates": 8, "kept": 5}


def test_v2_completed_intent_rejects_unknown_metrics():
    reporter = WorkflowProgressReporter(object(), ())
    identity = NodeExecutionIdentity(
        "deep_research", "v2", "thread", "run", "checkpoint", "", "task", "search_join", 1
    )
    assert reporter.build_completion_intent(
        identity, {"stage_id": "search", "metrics": {"secret_query": "do not leak"}}
    ) is None

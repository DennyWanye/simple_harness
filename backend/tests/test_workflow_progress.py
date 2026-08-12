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
    validated_v2_stage_text,
)
from deskpet.workflows.definitions.v1 import (
    CODE_COMPLEX_V1_DEFINITION,
    DEEP_RESEARCH_V1,
    DEEP_RESEARCH_V1_DEFINITION,
    DURABLE_TASK_V1_DEFINITION,
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
    "durable_task": {
        "intake": (1, 9, "理解任务"),
        "clarify": (2, 9, "确认需求"),
        "plan": (3, 9, "制定执行计划"),
        "wait_approval": (4, 9, "等待计划确认"),
        "llm_proposal": (5, 9, "规划下一步"),
        "tool_execution": (6, 9, "执行操作"),
        "test": (7, 9, "验证结果"),
        "audit": (8, 9, "完成质量检查"),
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


def test_public_stage_nodes_exist_in_the_compiled_workflow_definitions():
    definitions = {
        "deep_research": DEEP_RESEARCH_V1_DEFINITION,
        "ppt_pro": PPT_PRO_V1_DEFINITION,
        "code_complex": CODE_COMPLEX_V1_DEFINITION,
        "durable_task": DURABLE_TASK_V1_DEFINITION,
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
            "workflow_label",
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
        assert payload["workflow_label"] == "PPT"
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
async def test_durable_task_progress_scopes_repeated_nodes_by_safe_task_identity():
    service = _Service()
    reporter = WorkflowProgressReporter(service, (("session_message", "session-1"),))
    first = replace(
        _identity(workflow="durable_task", node="llm_proposal"),
        task_id="private-task-a",
    )
    second = replace(first, task_id="private-task-b")

    first_event_id = await reporter.report(first, "started")
    second_event_id = await reporter.report(second, "started")
    duplicate_second_id = await reporter.report(second, "started")

    # The private durable-task graph alternates llm_proposal/tool_execution
    # for each tool batch.  Public progress represents the stage, not those
    # private task instances, so one attempt emits each stage only once.
    assert first_event_id == second_event_id == duplicate_second_id == "event-1"
    assert len(service.outbox.events) == 1
    serialized_keys = repr(tuple(service.outbox.events))
    assert "private-task-a" not in serialized_keys
    assert "private-task-b" not in serialized_keys
    assert {
        event["payload"]["workflow_label"]
        for event in service.outbox.events.values()
    } == {"多步骤任务"}
    payloads = [event["payload"] for event in service.outbox.events.values()]
    assert {payload["workflow_name"] for payload in payloads} == {"多步骤任务"}
    assert {payload["stage"] for payload in payloads} == {"规划下一步"}


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
async def test_terminal_run_rejecting_trailing_progress_is_expected() -> None:
    class _TerminalOutbox:
        async def ensure_event(self, **kwargs):
            error = RuntimeError("terminal execution cannot accept another event")
            error.code = "execution_run_terminal"
            raise error

    service = _Service()
    service.outbox = _TerminalOutbox()
    reporter = WorkflowProgressReporter(service, (("session_message", "session-1"),))

    result = await reporter.report(
        _identity(workflow="durable_task", node="llm_proposal"), "failed"
    )

    assert result is None
    assert service.delivered == []


@pytest.mark.asyncio
async def test_v7_children_snapshot_is_safe_complete_and_idempotent():
    service = _Service()
    reporter = WorkflowProgressReporter(service, (("websocket", "session-secret"),))
    identity = NodeExecutionIdentity(
        "deep_research", "v7", "thread-secret", "run-v7", "checkpoint-secret", "",
        "task-plan", "plan", 1,
    )
    children = [
        {
            "child_id": f"dr-{index}",
            "question": question,
            "status": "queued",
            "attempt": 0,
            "max_attempts": 2,
            "n_sources": 0,
            "reason_code": "",
        }
        for index, question in enumerate(("架构是什么？", "核心组件是什么？", "常见陷阱是什么？"))
    ]

    first = await reporter.report_deep_research_v7_children(identity, children)
    duplicate = await reporter.report_deep_research_v7_children(identity, children)
    changed = [dict(item) for item in children]
    changed[0].update(status="valid", attempt=1, n_sources=2, reason_code="ok")
    second = await reporter.report_deep_research_v7_children(identity, changed)

    assert first == duplicate == "event-1"
    assert second == "event-2"
    assert len(service.outbox.events) == 2
    payload = list(service.outbox.events.values())[-1]["payload"]
    assert payload["schema_version"] == 7
    assert payload["kind"] == "research_children"
    assert payload["workflow_version"] == "v7"
    assert payload["children"][0] == changed[0]
    assert "session-secret" not in repr(payload)


@pytest.mark.asyncio
async def test_v7_progress_uses_six_user_visible_stages():
    service = _Service()
    reporter = WorkflowProgressReporter(service, (("websocket", "session-1"),))
    identity = NodeExecutionIdentity(
        "deep_research", "v7", "thread", "run-v7", "checkpoint", "",
        "task-search", "search", 1,
    )

    await reporter.report(identity, "started")

    payload = list(service.outbox.events.values())[-1]["payload"]
    assert payload == {
        "schema_version": 7,
        "kind": "progress",
        "workflow_name": "deep_research",
        "workflow_version": "v7",
        "workflow_label": "深度调研",
        "stage_id": "search",
        "stage": "子代理调研与补救",
        "ordinal": 3,
        "total": 6,
        "status": "started",
        "text": "深度调研进度：子代理调研与补救（3/6）",
    }


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
    assert first["payload"]["action"] == "搜索可用资料来源"
    assert first["payload"]["result"] == "尝试 2 个来源，找到 8 条候选，保留 5 条"
    assert first["payload"]["result_code"] == "stage_ok"
    assert first["payload"]["diagnostic_codes"] == []
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


def test_v2_completed_event_key_ignores_retry_attempt_and_session_text_is_allowlisted():
    reporter = WorkflowProgressReporter(object(), ())
    projection = {"stage_id": "finalize", "metrics": {"citations": 3, "status": "completed"}, "completed_count": 13}
    first = NodeExecutionIdentity("deep_research", "v2", "thread", "run", "checkpoint", "", "task", "finalize", 1)
    retried = NodeExecutionIdentity("deep_research", "v2", "thread", "run", "checkpoint", "", "task", "finalize", 2)
    first_intent = reporter.build_completion_intent(first, projection)
    retry_intent = reporter.build_completion_intent(retried, projection)
    assert first_intent is not None and retry_intent is not None
    assert first_intent["event_key"] == retry_intent["event_key"]
    assert first_intent["payload"] == retry_intent["payload"]
    assert validated_v2_stage_text(first_intent["payload"]) == first_intent["payload"]["summary"]


@pytest.mark.asyncio
async def test_v2_started_progress_uses_thirteen_stage_projection_for_join_nodes():
    service = _Service()
    reporter = WorkflowProgressReporter(service, (("websocket", "session-1"),))
    identity = NodeExecutionIdentity(
        "deep_research", "v2", "thread", "run-v2", "checkpoint", "",
        "task-search-join", "search_join", 1,
    )

    event_id = await reporter.report(identity, "started")

    assert event_id is not None
    payload = list(service.outbox.events.values())[-1]["payload"]
    assert payload["schema_version"] == 2
    assert payload["workflow_name"] == "deep_research"
    assert payload["workflow_version"] == "v2"
    assert payload["stage_id"] == "search"
    assert payload["ordinal"] == 4
    assert payload["total"] == 13
    assert validated_v2_stage_text(payload) == payload["text"]


def test_v3_completed_intent_uses_same_safe_stage_contract_and_filters_diagnostics():
    reporter = WorkflowProgressReporter(object(), ())
    identity = NodeExecutionIdentity(
        "deep_research", "v3", "thread", "run-v3", "checkpoint", "",
        "task-cite", "cite", 1,
    )
    projection = {
        "stage_id": "cite",
        "metrics": {
            "factual_claims_pre_repair": 12,
            "supported_factual": 9,
            "published": 9,
            "discarded": 3,
            "repaired": 2,
            "support_rate": 0.75,
            "citations": 9,
            "domains": 5,
            "body_bytes": 1800,
        },
        "result_code": "stage_degraded",
        "diagnostic_codes": ["claim_pruned", "provider_degraded", "secret_query"],
        "completed_count": 11,
        "degraded": True,
        "next_stage": "persist",
    }

    intent = reporter.build_completion_intent(identity, projection)

    assert intent is not None
    payload = intent["payload"]
    assert payload["workflow_version"] == "v3"
    assert payload["result"] == "发布 9 条，丢弃 3 条，修复 2 条，支持率 75%"
    assert payload["diagnostic_codes"] == ["claim_pruned", "provider_degraded"]
    assert payload["published"] == 9
    assert payload["discarded"] == 3
    assert payload["repaired"] == 2
    assert "secret_query" not in str(payload)
    assert validated_v2_stage_text(payload) == payload["text"]


def test_v3_completed_intent_allows_production_branch_and_quality_diagnostics():
    reporter = WorkflowProgressReporter(object(), ())
    identity = NodeExecutionIdentity(
        "deep_research", "v3", "thread", "run-v3", "checkpoint", "",
        "task-cite", "cite", 1,
    )
    production_codes = {
        "deadline_exhausted", "search_port_unavailable", "provider_failure",
        "direct_failure", "fetch_failure", "blob_unavailable", "low_quality_source",
        "low_quality_content", "search_degraded", "support_rate_below_threshold",
        "published_factual_below_threshold", "citation_count_below_threshold",
        "domain_count_below_threshold", "body_bytes_below_threshold",
    }
    projection = {
        "stage_id": "cite",
        "metrics": {"published": 3, "discarded": 5, "support_rate": 0.375},
        "result_code": "insufficient_evidence",
        "diagnostic_codes": [*production_codes, "secret_query"],
        "completed_count": 11,
        "degraded": True,
        "next_stage": "persist",
    }

    intent = reporter.build_completion_intent(identity, projection)

    assert intent is not None
    assert set(intent["payload"]["diagnostic_codes"]) == production_codes
    assert "secret_query" not in str(intent["payload"])


@pytest.mark.asyncio
async def test_v3_started_progress_uses_schema_two_public_projection():
    service = _Service()
    reporter = WorkflowProgressReporter(service, (("websocket", "session-1"),))
    identity = NodeExecutionIdentity(
        "deep_research", "v3", "thread", "run-v3", "checkpoint", "",
        "task-fetch", "fetch_join", 1,
    )

    event_id = await reporter.report(identity, "started")

    assert event_id is not None
    payload = list(service.outbox.events.values())[-1]["payload"]
    assert payload["schema_version"] == 2
    assert payload["workflow_version"] == "v3"
    assert payload["stage_id"] == "fetch"
    assert payload["action"] == "抓取并提取来源正文"
    assert payload["result_code"] == "started"
    assert validated_v2_stage_text(payload) == payload["text"]


def test_v4_search_completion_uses_unambiguous_gateway_counts():
    reporter = WorkflowProgressReporter(object(), ())
    identity = NodeExecutionIdentity(
        "deep_research", "v4", "thread", "run-v4", "checkpoint", "",
        "task-search", "search_join", 1,
    )
    projection = {
        "stage_id": "search",
        "metrics": {
            "actual_requests": 8,
            "hits": 2,
            "empty": 3,
            "timeouts": 3,
            "cooldown_skips": 4,
            "busy_skips": 1,
            "queue_timeouts": 2,
            "probes": 1,
            "rescue_considered_count": 1,
            "rescue_executed_count": 1,
            "candidates": 7,
            "providers_hit": 2,
        },
        "result_code": "stage_degraded",
        "diagnostic_codes": ["queue_timeout", "half_open_busy", "secret_query"],
        "completed_count": 4,
        "degraded": True,
        "next_stage": "direct",
    }

    intent = reporter.build_completion_intent(identity, projection)

    assert intent is not None
    payload = intent["payload"]
    assert payload["workflow_version"] == "v4"
    assert payload["result"] == (
        "真实请求 8 / 命中 2 / 空结果 3 / 超时 3 / cooldown 跳过 4 / "
        "busy 跳过 1 / 排队超时 2 / probe 1，候选 7"
    )
    assert payload["diagnostic_codes"] == ["half_open_busy", "queue_timeout"]
    assert "secret_query" not in str(payload)


@pytest.mark.asyncio
async def test_v4_started_progress_uses_existing_durable_stage_contract():
    service = _Service()
    reporter = WorkflowProgressReporter(service, (("websocket", "session-1"),))
    identity = NodeExecutionIdentity(
        "deep_research", "v4", "thread", "run-v4", "checkpoint", "",
        "task-direct", "direct_join", 1,
    )

    event_id = await reporter.report(identity, "started")

    assert event_id is not None
    payload = list(service.outbox.events.values())[-1]["payload"]
    assert payload["schema_version"] == 2
    assert payload["workflow_version"] == "v4"
    assert payload["stage_id"] == "direct"
    assert validated_v2_stage_text(payload) == payload["text"]

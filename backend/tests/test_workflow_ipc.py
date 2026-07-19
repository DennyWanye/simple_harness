from __future__ import annotations

import asyncio
import json

import pytest
from deskpet.workflows.control import WorkflowInterrupt

from deskpet.workflows.ipc import WorkflowIPCDispatcher, start_workflow_ipc_dispatch
from deskpet.workflows.outbox import OutboxError


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def list_runs(self, **kwargs):
        self.calls.append(("list_runs", kwargs))
        return {"runs": [{"run_id": "run-1"}], "next_cursor": None}

    async def run_detail(self, run_id):
        self.calls.append(("run_detail", run_id))
        return {
            "run_id": run_id,
            "nodes": [],
            "edges": [],
            "spans": [],
            "checkpoints": [],
            "evaluations": [],
        }

    async def fork_checkpoint(self, **kwargs):
        self.calls.append(("fork_checkpoint", kwargs))
        return {"run_id": "child-run", "source_run_id": kwargs["run_id"]}

    async def resolve_decision(self, decision_id, **kwargs):
        self.calls.append(("resolve_decision", {"decision_id": decision_id, **kwargs}))
        return {"decision_id": decision_id, "status": "resolved", "version": 2}

    async def submit_evaluation(self, **kwargs):
        self.calls.append(("submit_evaluation", kwargs))
        return {"evaluation_id": "evaluation-1", **kwargs}

    async def retry_delivery(self, delivery_id, **kwargs):
        raise OutboxError(
            "stale_delivery_version",
            "stale delivery",
            current_version=4,
        )

    async def retry_run_from_start(self, run_id, **kwargs):
        self.calls.append(("retry_run_from_start", {"run_id": run_id, **kwargs}))
        return {"run_id": "retry-run", "source_run_id": run_id, "created": True}

    async def execute_run_action(self, run_id, **kwargs):
        self.calls.append(("execute_run_action", {"run_id": run_id, **kwargs}))
        return {"run_id": run_id, "accepted": True, "action_id": kwargs["action_id"]}


@pytest.mark.asyncio
async def test_frontend_canonical_names_return_exact_response_types_and_payloads():
    service = FakeService()
    dispatcher = WorkflowIPCDispatcher(service)

    runs = await dispatcher.dispatch(
        {
            "type": "workflow_runs_list",
            "request_id": "request-runs",
            "payload": {"session_id": "session", "limit": 20},
        }
    )
    detail = await dispatcher.dispatch(
        {
            "type": "workflow_run_detail",
            "request_id": "request-detail",
            "payload": {"run_id": "run-1"},
        }
    )

    assert runs == {
        "type": "workflow_runs_list_response",
        "request_id": "request-runs",
        "ok": True,
        "payload": {"runs": [{"run_id": "run-1"}], "next_cursor": None},
    }
    assert detail["type"] == "workflow_run_detail_response"
    assert detail["request_id"] == "request-detail"
    assert set(detail["payload"]) == {
        "run_id",
        "nodes",
        "edges",
        "spans",
        "checkpoints",
        "evaluations",
    }


@pytest.mark.asyncio
async def test_dotted_aliases_use_canonical_frontend_response_type():
    dispatcher = WorkflowIPCDispatcher(FakeService())

    response = await dispatcher.dispatch(
        {
            "type": "workflow.runs.list",
            "request_id": "request-alias",
            "payload": {},
        }
    )

    assert response["type"] == "workflow_runs_list_response"
    assert response["ok"] is True


@pytest.mark.asyncio
async def test_retry_from_start_requires_exact_safe_payload_and_forwards_once():
    service = FakeService()
    dispatcher = WorkflowIPCDispatcher(service)
    retry_key = "123e4567-e89b-42d3-a456-426614174000"

    response = await dispatcher.dispatch(
        {
            "type": "workflow_run_retry_from_start",
            "request_id": "retry-command-1",
            "payload": {
                "run_id": "failed-run",
                "action_id": "retry_from_start",
                "retry_key": retry_key,
            },
        }
    )
    assert response == {
        "type": "workflow_run_retry_from_start_response",
        "request_id": "retry-command-1",
        "ok": True,
        "payload": {
            "run_id": "retry-run",
            "source_run_id": "failed-run",
            "created": True,
        },
    }
    assert service.calls[-1] == (
        "retry_run_from_start",
        {
            "run_id": "failed-run",
            "action_id": "retry_from_start",
            "retry_key": retry_key,
        },
    )

    for extra in ({"query": "private"}, {"url": "https://example.invalid"}, {"x": 1}):
        rejected = await dispatcher.dispatch(
            {
                "type": "workflow_run_retry_from_start",
                "request_id": "retry-command-invalid",
                "payload": {
                    "run_id": "failed-run",
                    "action_id": "retry_from_start",
                    "retry_key": retry_key,
                    **extra,
                },
            }
        )
        assert rejected["ok"] is False
        assert rejected["error"]["code"] == "invalid_payload"


@pytest.mark.asyncio
async def test_versioned_run_action_ipc_is_strict_and_forwards_once():
    service = FakeService()
    dispatcher = WorkflowIPCDispatcher(service)
    response = await dispatcher.dispatch({
        "type": "workflow.run.action",
        "request_id": "action-1",
        "payload": {
            "run_id": "run-v5", "action_id": "generate_now",
            "idempotency_key": "click-1", "expected_version": 7,
            "action_payload": {"brief_checkpoint_ns": "", "brief_checkpoint_id": "brief-1"},
        },
    })
    assert response["type"] == "workflow_run_action_response"
    assert response["payload"] == {
        "run_id": "run-v5", "accepted": True, "action_id": "generate_now"
    }
    assert service.calls[-1][0] == "execute_run_action"
    continued = await dispatcher.dispatch({
        "type": "workflow_run_action",
        "request_id": "action-continue",
        "payload": {
            "run_id": "run-v5",
            "action_id": "continue_research",
            "idempotency_key": "click-continue",
            "expected_version": 7,
        },
    })
    assert continued["ok"] is True
    assert service.calls[-1] == (
        "execute_run_action",
        {
            "run_id": "run-v5",
            "action_id": "continue_research",
            "idempotency_key": "click-continue",
            "expected_version": 7,
            "payload": {},
        },
    )
    rejected = await dispatcher.dispatch({
        "type": "workflow_run_action", "request_id": "action-2",
        "payload": {
            "run_id": "run-v5", "action_id": "generate_now",
            "idempotency_key": "click-2", "expected_version": 7, "secret": "no",
        },
    })
    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "invalid_payload"


@pytest.mark.asyncio
async def test_run_detail_serializes_native_interrupts_before_websocket_json():
    class WaitingService(FakeService):
        async def run_detail(self, run_id):
            detail = await super().run_detail(run_id)
            detail["checkpoints"] = [
                {
                    "tasks": [
                        {
                            "interrupts": (
                                WorkflowInterrupt("i-1", "task-1", 0, {"kind": "approval"}),
                            )
                        }
                    ]
                }
            ]
            return detail

    response = await WorkflowIPCDispatcher(WaitingService()).dispatch(
        {
            "type": "workflow_run_detail",
            "request_id": "request-waiting-detail",
            "payload": {"run_id": "run-waiting"},
        }
    )

    assert response["payload"]["checkpoints"][0]["tasks"][0]["interrupts"] == [
        {
            "interrupt_id": "i-1",
            "task_id": "task-1",
            "ordinal": 0,
            "payload": {"kind": "approval"},
        }
    ]
    json.dumps(response)


@pytest.mark.asyncio
async def test_background_dispatch_does_not_block_socket_receive_loop():
    started = asyncio.Event()
    release = asyncio.Event()
    sent: list[dict[str, object]] = []
    tasks: set[asyncio.Task[object]] = set()

    class ResumingService(FakeService):
        async def resume_run(self, run_id, responses):
            started.set()
            await release.wait()
            return {"run_id": run_id, "status": "completed", "responses": responses}

    async def send_response(response):
        sent.append(response)

    task = start_workflow_ipc_dispatch(
        ResumingService(),
        {
            "type": "workflow_run_resume",
            "request_id": "request-resume",
            "payload": {"run_id": "run-1", "responses": {"interrupt-1": {"approved": True}}},
        },
        send_response,
        tasks,
    )

    await asyncio.wait_for(started.wait(), timeout=1)
    assert not task.done()
    assert task in tasks
    release.set()
    await asyncio.wait_for(task, timeout=1)
    await asyncio.sleep(0)
    assert tasks == set()
    assert sent == [
        {
            "type": "workflow_run_resume_response",
            "request_id": "request-resume",
            "ok": True,
            "payload": {
                "run_id": "run-1",
                "status": "completed",
                "responses": {"interrupt-1": {"approved": True}},
            },
        }
    ]


@pytest.mark.asyncio
async def test_fork_decision_and_evaluation_payloads_are_validated_and_forwarded():
    service = FakeService()
    dispatcher = WorkflowIPCDispatcher(service)

    fork = await dispatcher.dispatch(
        {
            "type": "workflow_checkpoint_fork",
            "request_id": "request-fork",
            "expected_version": 3,
            "payload": {
                "run_id": "run-1",
                "checkpoint_id": "checkpoint-1",
                "state_patch": {"topic": "new"},
            },
        }
    )
    decision = await dispatcher.dispatch(
        {
            "type": "workflow_decision_resolve",
            "request_id": "request-decision",
            "payload": {
                "decision_id": "decision-1",
                "nonce": "nonce-1",
                "response": {"approved": True},
                "expected_version": 1,
            },
        }
    )
    evaluation = await dispatcher.dispatch(
        {
            "type": "workflow_evaluation_submit",
            "request_id": "request-evaluation",
            "payload": {
                "trace_id": "trace-1",
                "run_id": "run-1",
                "evaluator_name": "reviewer",
                "evaluator_version": "rubric-1",
                "evaluator_type": "human",
                "verdict": "pass",
                "score": 0.8,
                "labels": ["useful"],
            },
        }
    )

    assert fork["type"] == "workflow_checkpoint_fork_response"
    assert service.calls[0][1]["expected_version"] == 3
    assert decision["type"] == "workflow_decision_resolve_response"
    assert service.calls[1][1]["response"] == {"approved": True}
    assert evaluation["type"] == "workflow_evaluation_submit_response"
    assert service.calls[2][1]["evaluator_type"] == "human"


@pytest.mark.asyncio
async def test_missing_expected_version_and_stale_delivery_return_stable_errors():
    dispatcher = WorkflowIPCDispatcher(FakeService())

    missing = await dispatcher.dispatch(
        {
            "type": "workflow_delivery_retry",
            "request_id": "request-missing",
            "payload": {"delivery_id": "delivery-1"},
        }
    )
    stale = await dispatcher.dispatch(
        {
            "type": "workflow_delivery_retry",
            "request_id": "request-stale",
            "payload": {"delivery_id": "delivery-1", "expected_version": 1},
        }
    )

    assert missing["type"] == "workflow_ipc_error"
    assert missing["error"]["code"] == "invalid_payload"
    assert missing["request_id"] == "request-missing"
    assert stale["error"] == {
        "code": "stale_delivery_version",
        "message": "stale delivery",
        "retryable": False,
        "current_version": 4,
    }


@pytest.mark.asyncio
async def test_request_id_payload_and_limit_validation_happen_before_service_call():
    service = FakeService()
    dispatcher = WorkflowIPCDispatcher(service)

    missing_id = await dispatcher.dispatch(
        {"type": "workflow_runs_list", "payload": {}}
    )
    invalid_limit = await dispatcher.dispatch(
        {
            "type": "workflow_runs_list",
            "request_id": "request-limit",
            "payload": {"limit": 101},
        }
    )

    assert missing_id["error"]["code"] == "invalid_request"
    assert invalid_limit["error"]["code"] == "invalid_payload"
    assert service.calls == []

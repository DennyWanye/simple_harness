from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any

import aiosqlite
import httpx
import pytest

from deskpet.execution.dispatch import (
    DispatchStartedAck,
    DispatchStartUnknown,
)
from deskpet.execution.provider_invocations import (
    ProviderDispatchNotSentError,
    ProviderDispatchUnknownError,
    ProviderInvocationCoordinator,
    build_provider_input_projection,
    coordinate_provider_call,
    coordinate_provider_stream,
)
from deskpet.execution.provider_fault_script import (
    ProviderFaultInjectedError,
    ProviderFaultScriptV1,
)
from deskpet.execution.contracts import (
    AttemptRecord,
    PersistenceLevel,
    PlanVersionRecord,
    ProviderActionBatch,
    ProviderActionCall,
    ProviderTurnFence,
    RunContext,
    RunCreate,
    RunStatus,
    TaskGoalRecord,
    fingerprint_json,
    root_idempotency_key,
)
from deskpet.workflows.store import SqliteExecutionUnitOfWork
from providers.dispatch_transport import DispatchAwareAsyncTransport
from providers.openai_compatible import OpenAICompatibleProvider


def test_provider_outcome_projection_keeps_model_dump_structured() -> None:
    from deskpet.execution.provider_invocations import _jsonable

    class ResponseLike:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {
                "content": "准备调用工具。",
                "reasoning_content": "private",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "file_write",
                        "arguments": {"path": "project.godot"},
                    }
                ],
            }

    assert _jsonable(ResponseLike()) == {
        "content": "准备调用工具。",
        "reasoning_content": "private",
        "tool_calls": [
            {
                "id": "call-1",
                "name": "file_write",
                "arguments": {"path": "project.godot"},
            }
        ],
    }


@pytest.mark.asyncio
async def test_harness_snapshot_includes_durable_child_workflow_effects(
    tmp_path,
) -> None:
    db_path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(db_path)
    await uow.initialize()
    capability_hash = fingerprint_json({"tools": ["file_write"]})
    await uow.create(
        RunCreate(
            run_id="root-1",
            idempotency_key=root_idempotency_key(
                "session-1", "request-1", "turn-1"
            ),
            context=RunContext(
                session_id="session-1",
                root_run_id="root-1",
                parent_run_id=None,
                request_id="request-1",
                turn_id="turn-1",
                venue="text",
                workspace={},
                capability_hash=capability_hash,
                provider_plan={},
                trace_id="trace-1",
                principal_id="principal-1",
                auth_epoch=1,
            ),
            payload_fingerprint=fingerprint_json({"prompt": "create demo"}),
            capability_fingerprint=capability_hash,
            driver_kind="react",
            profile_key="agent.general",
            persistence_level=PersistenceLevel.DURABLE,
            status=RunStatus.RUNNING,
        )
    )
    await uow.create(
        RunCreate(
            run_id="child-1",
            idempotency_key="workflow:child-1:create",
            context=RunContext(
                session_id="session-1",
                root_run_id="root-1",
                parent_run_id="root-1",
                request_id="request-1:child",
                turn_id="turn-1",
                venue="text",
                workspace={},
                capability_hash=capability_hash,
                provider_plan={},
                trace_id="trace-child-1",
                principal_id="principal-1",
                auth_epoch=1,
            ),
            payload_fingerprint=fingerprint_json({"objective": "create demo"}),
            capability_fingerprint=capability_hash,
            driver_kind="workflow",
            profile_key="workflow.durable_task",
            persistence_level=PersistenceLevel.DURABLE,
            status=RunStatus.RUNNING,
        )
    )
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO workflow_runs(
            run_id,trace_id,thread_id,session_id,request_id,turn_id,
            workflow_name,workflow_version,manifest_hash,implementation_hash,
            capability_hash,state_schema_version,status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "child-1", "trace-child-1", "thread-child-1", "session-1",
                "request-1:child", "turn-1", "durable_task", "v1",
                "manifest", "implementation", capability_hash, 1, "running",
                10.0, 10.0,
            ),
        )
        await db.execute(
            """INSERT INTO workflow_effects(
            effect_id,run_id,node_execution_id,effect_fingerprint,effect_type,
            policy_json,args_hash,status,prepared_json,outcome_json,
            artifact_refs_json,lease_epoch,started_at,updated_at,ended_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "effect-1", "child-1", "node-1", "effect-fingerprint",
                "staged_file", "{}", "args-hash", "committed",
                json.dumps({
                    "stable_call_id": "stable-call-1",
                    "tool_name": "file_write",
                    "final_params": {"path": "project.godot"},
                }),
                json.dumps({
                    "state": "success",
                    "value": {"path": "project.godot", "bytes_written": 10},
                }),
                "[]", 1, 11.0, 12.0, 12.0,
            ),
        )
        checkpoint = {
            "state": {
                "values": {
                    "todos": [
                        {
                            "workflow_step_id": "step-1",
                            "index": 0,
                            "title": "创建 Godot 项目配置",
                            "status": "completed",
                        },
                        {
                            "workflow_step_id": "step-2",
                            "index": 1,
                            "title": "验证项目结构",
                            "status": "pending",
                        },
                    ],
                    "proposal_state": {
                        "active_plan_id": "plan-1",
                        "active_step_id": "step-2",
                        "messages": [
                            {
                                "role": "assistant",
                                "content": (
                                    "<think>private</think>"
                                    "现在创建 Godot 项目配置。"
                                ),
                                "tool_calls": [
                                    {
                                        "id": "stable-call-1",
                                        "name": "file_write",
                                    }
                                ],
                            }
                        ],
                        "committed_tool_results": {
                            "stable-call-1": {
                                "stable_call_id": "stable-call-1",
                                "workflow_step_id": "step-1",
                            }
                        },
                    },
                    "tool_results": {},
                }
            }
        }
        await db.execute(
            """INSERT INTO workflow_checkpoints(
            thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,
            run_id,checkpoint_type,checkpoint_blob,metadata_blob,created_at,
            engine_kind,snapshot_version
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "thread-child-1", "", "checkpoint-1", None, "child-1",
                "deskpet-native-json-v1",
                json.dumps(checkpoint).encode("utf-8"),
                b"{}", 12.0, "native", 1,
            ),
        )
        await db.execute(
            """UPDATE workflow_runs
            SET head_checkpoint_ns='',head_checkpoint_id='checkpoint-1'
            WHERE run_id='child-1'"""
        )
        await db.commit()

    snapshot = await uow.inspect_harness_run(
        "root-1",
        expected_session_id="session-1",
    )

    assert snapshot is not None
    assert snapshot["workflow_effects"] == [{
        "effect_id": "effect-1",
        "run_id": "child-1",
        "status": "committed",
        "workflow_step_id": "step-1",
        "receipt_ref": None,
        "details": {
            "prepared": {
                "stable_call_id": "stable-call-1",
                "tool_name": "file_write",
                "final_params": {"path": "project.godot"},
            },
            "outcome": {
                "state": "success",
                "value": {
                    "path": "project.godot",
                    "bytes_written": 10,
                },
            },
            "artifact_refs": [],
        },
        "created_at": 11.0,
        "updated_at": 12.0,
        "ended_at": 12.0,
    }]
    assert snapshot["workflow_plans"] == [{
        "run_id": "child-1",
        "plan_id": "plan-1",
        "active_step_id": "step-2",
        "steps": [
            {
                "workflow_step_id": "step-1",
                "index": 0,
                "title": "创建 Godot 项目配置",
                "status": "completed",
            },
            {
                "workflow_step_id": "step-2",
                "index": 1,
                "title": "验证项目结构",
                "status": "pending",
            },
        ],
        "public_messages": [{
            "message_id": "assistant-0",
            "text": "现在创建 Godot 项目配置。",
            "tool_call_ids": ["stable-call-1"],
            "workflow_step_id": "step-1",
        }],
    }]
    assert "private" not in json.dumps(snapshot["workflow_plans"])
    await uow.close()


@dataclass
class _Lease:
    run_id: str
    revocation_epoch: int = 3
    released: bool = False

    async def release(self) -> None:
        self.released = True


class _FakeUow:
    def __init__(self) -> None:
        self.record: Any | None = None
        self.outcome: Any | None = None
        self.unknown_reason: str | None = None
        self.unknown_error_type: str | None = None
        self.unknown_error_message: str | None = None
        self.failed_reason: str | None = None
        self.failed_audit_reason: str | None = None
        self.failed_error_type: str | None = None
        self.failed_error_message: str | None = None
        self.claim_calls = 0

    async def claim_provider_invocation(
        self, record: Any, **_kwargs: Any
    ) -> Any:
        self.claim_calls += 1
        if self.record is None:
            self.record = record
        return self.record


    async def read_provider_invocation(
        self, run_id: str, invocation_id: str
    ) -> Any | None:
        if (
            self.record is not None
            and self.record.run_id == run_id
            and self.record.invocation_id == invocation_id
        ):
            return self.record
        return None

    async def complete_provider_invocation(
        self, run_id: str, invocation_id: str, **kwargs: Any
    ) -> Any:
        self.outcome = kwargs["outcome"]
        self.record = replace(
            self.record,
            status="completed",
            dispatch_started_ack_ref=kwargs["dispatch_started_ack_ref"],
            dispatch_started_ack_hash=kwargs["dispatch_started_ack_hash"],
            dispatch_started_at=kwargs["dispatch_started_at"],
            outcome_ref=f"provider-outcome:{invocation_id}",
            outcome_hash=self.outcome.payload_hash,
        )
        return self.record

    async def fail_provider_invocation(
        self, run_id: str, invocation_id: str, **kwargs: Any
    ) -> Any:
        self.failed_reason = str(kwargs["outcome_ref"]).rsplit(":", 1)[-1]
        self.failed_audit_reason = kwargs.get("audit_reason")
        self.failed_error_type = kwargs.get("audit_error_type")
        self.failed_error_message = kwargs.get("audit_error_message")
        self.record = replace(self.record, status="failed")
        return self.record

    async def mark_unknown_provider_invocation(
        self, run_id: str, invocation_id: str, **kwargs: Any
    ) -> Any:
        self.unknown_reason = str(kwargs["outcome_ref"]).rsplit(":", 1)[-1]
        self.unknown_error_type = kwargs.get("audit_error_type")
        self.unknown_error_message = kwargs.get("audit_error_message")
        self.record = replace(self.record, status="unknown")
        return self.record


class _WriteOutcomeUnknown(RuntimeError):
    code = "write_outcome_unknown"


class _UnknownClaimUow(_FakeUow):
    async def claim_provider_invocation(
        self, record: Any, **kwargs: Any
    ) -> Any:
        self.claim_calls += 1
        if self.claim_calls == 1:
            self.record = record
            raise _WriteOutcomeUnknown("claim commit acknowledgement was lost")
        return await super().claim_provider_invocation(record, **kwargs)


class _CountingTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, release: asyncio.Event | None = None) -> None:
        self.count = 0
        self.entered = asyncio.Event()
        self.release = release

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.count += 1
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        return httpx.Response(
            200,
            json={"ok": True},
            request=request,
        )


class _SseTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.count += 1
        body = (
            'data: {"id":"chat-1","model":"model-1","choices":[{"delta":'
            '{"content":"hello"},"finish_reason":null}]}\n\n'
            'data: {"id":"chat-1","model":"model-1","choices":[{"delta":{},'
            '"finish_reason":"stop"}],"usage":{"prompt_tokens":1,'
            '"completion_tokens":1}}\n\n'
            "data: [DONE]\n\n"
        ).encode()
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
            request=request,
        )


class _ManyDeltaSseTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, delta_count: int = 100) -> None:
        self.count = 0
        self.delta_count = delta_count

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.count += 1
        chunks = [
            (
                'data: {"id":"chat-many","model":"model-1","choices":'
                '[{"delta":{"content":"x"},"finish_reason":null}]}\n\n'
            )
            for _ in range(self.delta_count)
        ]
        chunks.extend(
            [
                (
                    'data: {"id":"chat-many","model":"model-1","choices":'
                    '[{"delta":{},"finish_reason":"stop"}],"usage":'
                    '{"prompt_tokens":1,"completion_tokens":100}}\n\n'
                ),
                "data: [DONE]\n\n",
            ]
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join(chunks).encode(),
            request=request,
        )


class _ErrorTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.count += 1
        raise httpx.ReadError("connection lost after handoff", request=request)


class _ConnectTimeoutTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.count += 1
        raise httpx.ConnectTimeout(
            "connection timed out after 10 seconds",
            request=request,
        )


def _snapshot(invoke: Any) -> dict[str, Any]:
    return {
        "run_id": "run-1",
        "provider_id": "provider-1",
        "model_id": "model-1",
        "adapter_id": "adapter-1",
        "idempotency_group_id": "turn-1:provider-0",
        "attempt_ordinal": 0,
        "provider_chain_slot": 0,
        "retry_ordinal": 0,
        "fallback_ordinal": 0,
        "stream_epoch": "7",
        "request_payload": {"messages": [{"role": "user", "content": "hi"}]},
        "policy_snapshot": {"sdk_retries": 0},
        "invoke": invoke,
    }


@pytest.mark.asyncio
async def test_child_main_fault_settles_claim_before_transport(tmp_path) -> None:
    script_path = tmp_path / "provider-faults.json"
    script_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "rules": [
                    {
                        "rule_id": "child-main",
                        "injection_ref": "child-main-ref",
                        "session_id": "session-a",
                        "workload_class": "main",
                        "callsite_id": "agent.root_turn",
                        "purpose": "agent_response",
                        "occurrence": 1,
                        "action": "child_provider_failure",
                        "run_kind": "child",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    script = ProviderFaultScriptV1.from_file(
        script_path, environ={"DESKPET_DEV_MODE": "1"}
    )
    physical_calls = 0

    async def invoke() -> dict[str, Any]:
        nonlocal physical_calls
        physical_calls += 1
        return {"ok": True}

    uow = _FakeUow()
    coordinator = ProviderInvocationCoordinator(
        uow,
        fence_reacquirer=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
        fault_script=script,
    )
    snapshot = _snapshot(invoke)
    snapshot["run_id"] = "child-abc"
    snapshot["policy_snapshot"] = {
        "purpose": "agent_response",
        "session_id": "session-a",
        "root_run_id": "root-1",
        "parent_run_id": "root-1",
        "profile_key": "agent.general",
        "sdk_retries": 0,
    }
    claimed = await coordinator.claim_prepared(
        coordinator.prepare_attempt(snapshot), _Lease("child-abc")
    )
    with pytest.raises(ProviderFaultInjectedError, match="child_provider_failure"):
        await coordinator.start_and_ack(claimed, 1.0)
    assert physical_calls == 0
    assert uow.record.status.value == "failed"
    assert uow.failed_audit_reason == "provider_fault_injected:child-main-ref"


def test_provider_input_projection_redacts_credentials_and_bounds_large_context() -> None:
    projection = build_provider_input_projection(
        {
            "messages": [
                {"role": "system", "content": "system policy"},
                *[
                    {
                        "role": "user",
                        "content": (
                            f"message-{index} token=super-secret-value-{index} "
                            + ("x" * 8_000)
                        ),
                    }
                    for index in range(20)
                ],
            ],
            "tools": [
                {"type": "function", "function": {"name": f"tool_{index}"}}
                for index in range(30)
            ],
            "model": "sf-glm-5.2",
            "purpose": "agent.react",
        }
    )

    assert projection["mode"] == "truncated"
    assert projection["message_count"] == 21
    assert projection["omitted_message_count"] > 0
    assert projection["tool_count"] == 30
    serialized = json.dumps(projection, ensure_ascii=False)
    assert "super-secret-value" not in serialized
    assert "[REDACTED" in serialized
    assert len(serialized.encode("utf-8")) < 100_000


@pytest.mark.asyncio
async def test_claim_commits_before_any_transport_and_ack_precedes_completion() -> None:
    release = asyncio.Event()
    physical = _CountingTransport(release=release)
    transport = DispatchAwareAsyncTransport(
        physical,
        adapter_identity="openai-compatible:v1",
    )

    async def invoke() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get("https://example.invalid/provider")
            return response.json()

    uow = _FakeUow()
    coordinator = ProviderInvocationCoordinator(
        uow,
        fence_reacquirer=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
    )
    prepared = coordinator.prepare_attempt(_snapshot(invoke))
    lease = _Lease("run-1")
    claimed = await coordinator.claim_prepared(prepared, lease)
    assert physical.count == 0
    assert uow.record.status.value == "claimed"

    started = await coordinator.start_and_ack(claimed, 1.0)
    assert isinstance(started, DispatchStartedAck)
    assert physical.count == 1
    assert lease.released is True
    assert claimed.operation is not None
    assert not claimed.operation.completion.done()

    release.set()
    result = await coordinator.complete_or_unknown(claimed)
    assert result == {"ok": True}
    assert uow.record.status.value == "completed"
    assert uow.outcome.payload_hash


@pytest.mark.asyncio
async def test_post_handoff_cancellation_is_unknown_and_never_retried() -> None:
    release = asyncio.Event()
    physical = _CountingTransport(release=release)
    transport = DispatchAwareAsyncTransport(
        physical,
        adapter_identity="openai-compatible:v1",
    )

    async def invoke() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://example.invalid/provider")

    uow = _FakeUow()
    coordinator = ProviderInvocationCoordinator(
        uow,
        fence_reacquirer=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
    )
    claimed = await coordinator.claim_prepared(
        coordinator.prepare_attempt(_snapshot(invoke)),
        _Lease("run-1"),
    )
    started = await coordinator.start_and_ack(claimed, 1.0)
    assert isinstance(started, DispatchStartedAck)
    assert claimed.operation is not None
    claimed.operation.completion.cancel()

    with pytest.raises(asyncio.CancelledError):
        await coordinator.complete_or_unknown(claimed)
    assert physical.count == 1
    assert uow.record.status.value == "unknown"
    assert uow.unknown_reason == "unknown"


@pytest.mark.asyncio
async def test_missing_transport_ack_fails_closed_as_unknown() -> None:
    entered = asyncio.Event()

    async def invoke_without_approved_transport() -> None:
        entered.set()
        await asyncio.Event().wait()

    uow = _FakeUow()
    coordinator = ProviderInvocationCoordinator(
        uow,
        fence_reacquirer=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
    )
    claimed = await coordinator.claim_prepared(
        coordinator.prepare_attempt(_snapshot(invoke_without_approved_transport)),
        _Lease("run-1"),
    )
    started = await coordinator.start_and_ack(claimed, 0.01)
    assert isinstance(started, DispatchStartUnknown) is False
    with pytest.raises(RuntimeError, match="provider_dispatch_not_started"):
        await coordinator.complete_or_unknown(claimed)
    assert uow.failed_reason == "dispatch_not_started"


@pytest.mark.asyncio
async def test_claim_commit_unknown_reconciles_exact_row_before_transport() -> None:
    physical = _CountingTransport()
    transport = DispatchAwareAsyncTransport(
        physical,
        adapter_identity="openai-compatible:v1",
    )

    async def invoke() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=transport) as client:
            return (
                await client.get("https://example.invalid/provider")
            ).json()

    uow = _UnknownClaimUow()
    coordinator = ProviderInvocationCoordinator(
        uow,
        fence_reacquirer=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
    )
    claimed = await coordinator.claim_prepared(
        coordinator.prepare_attempt(_snapshot(invoke)),
        _Lease("run-1"),
    )
    assert physical.count == 0
    assert uow.claim_calls == 1
    assert isinstance(
        await coordinator.start_and_ack(claimed, 1.0),
        DispatchStartedAck,
    )
    await coordinator.complete_or_unknown(claimed)
    assert physical.count == 1


@pytest.mark.asyncio
async def test_openai_compatible_client_uses_dispatch_aware_transport_once() -> None:
    physical = _SseTransport()
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "test-key",
        "model-1",
    )
    provider._test_transport = physical
    uow = _FakeUow()
    coordinator = ProviderInvocationCoordinator(
        uow,
        fence_reacquirer=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
    )

    async def invoke() -> dict[str, Any]:
        return await provider.chat_with_tools(
            [{"role": "user", "content": "hi"}],
            max_tokens=32,
        )

    claimed = await coordinator.claim_prepared(
        coordinator.prepare_attempt(_snapshot(invoke)),
        _Lease("run-1"),
    )
    assert physical.count == 0
    assert isinstance(
        await coordinator.start_and_ack(claimed, 1.0),
        DispatchStartedAck,
    )
    result = await coordinator.complete_or_unknown(claimed)
    assert result["content"] == "hello"
    assert physical.count == 1


@pytest.mark.asyncio
async def test_post_handoff_transport_error_is_typed_unknown_not_retryable() -> None:
    physical = _ErrorTransport()
    transport = DispatchAwareAsyncTransport(
        physical,
        adapter_identity="openai-compatible:v1",
    )

    async def invoke() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://example.invalid/provider")

    uow = _FakeUow()
    coordinator = ProviderInvocationCoordinator(
        uow,
        fence_reacquirer=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
    )
    claimed = await coordinator.claim_prepared(
        coordinator.prepare_attempt(_snapshot(invoke)),
        _Lease("run-1"),
    )
    assert isinstance(
        await coordinator.start_and_ack(claimed, 1.0),
        DispatchStartedAck,
    )
    with pytest.raises(ProviderDispatchUnknownError):
        await coordinator.complete_or_unknown(claimed)
    assert physical.count == 1
    assert uow.record.status.value == "unknown"
    assert uow.unknown_error_type == "ReadError"
    assert uow.unknown_error_message == "connection lost after handoff"


@pytest.mark.asyncio
async def test_connect_timeout_is_classified_as_not_sent() -> None:
    physical = _ConnectTimeoutTransport()
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "test-key",
        "model-1",
    )
    provider._test_transport = physical
    uow = _FakeUow()
    coordinator = ProviderInvocationCoordinator(
        uow,
        fence_reacquirer=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
    )

    async def invoke() -> dict[str, Any]:
        return await provider.chat_with_tools(
            [{"role": "user", "content": "hi"}],
            max_tokens=32,
        )

    claimed = await coordinator.claim_prepared(
        coordinator.prepare_attempt(_snapshot(invoke)),
        _Lease("run-1"),
    )
    assert isinstance(
        await coordinator.start_and_ack(claimed, 1.0),
        DispatchStartedAck,
    )
    with pytest.raises(ProviderDispatchNotSentError):
        await coordinator.complete_or_unknown(claimed)

    assert physical.count == 1
    assert uow.record.status.value == "failed"
    assert uow.failed_reason == "transport_not_sent"
    assert uow.failed_audit_reason == "connect_timeout_before_request"
    assert uow.failed_error_type == "ConnectTimeout"
    assert uow.failed_error_message == "connection timed out after 10 seconds"


@pytest.mark.asyncio
async def test_coordinated_provider_retries_connect_timeout_once() -> None:
    class _RetryFakeUow(_FakeUow):
        def __init__(self) -> None:
            super().__init__()
            self.records: dict[str, Any] = {}

        async def claim_provider_invocation(
            self, record: Any, **_kwargs: Any
        ) -> Any:
            self.claim_calls += 1
            self.records[record.invocation_id] = record
            self.record = record
            return record

    class _RecoveringTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.count = 0

        async def handle_async_request(
            self, request: httpx.Request
        ) -> httpx.Response:
            self.count += 1
            if self.count == 1:
                raise httpx.ConnectTimeout(
                    "first connection timed out",
                    request=request,
                )
            return httpx.Response(
                200,
                json={"ok": True},
                request=request,
            )

    physical = _RecoveringTransport()
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "test-key",
        "model-1",
    )
    provider._test_transport = physical
    uow = _RetryFakeUow()
    coordinator = ProviderInvocationCoordinator(
        uow,
        fence_reacquirer=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
    )

    async def invoke() -> dict[str, Any]:
        return await provider.chat_with_tools(
            [{"role": "user", "content": "hi"}],
            max_tokens=32,
        )

    result = await coordinate_provider_call(
        coordinator=coordinator,
        acquire_fence=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
        run_id="run-1",
        provider=provider,
        attempt_id="attempt-1",
        purpose="agent_loop",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        fallback_model="model-1",
        invoke=invoke,
    )

    assert result["content"] == ""
    assert physical.count == 2
    assert uow.claim_calls == 2


@pytest.mark.asyncio
async def test_coordinated_provider_retries_definite_429_with_new_identity() -> None:
    class _RetryFakeUow(_FakeUow):
        def __init__(self) -> None:
            super().__init__()
            self.records: dict[str, Any] = {}
            self.claimed_ids: list[str] = []

        async def claim_provider_invocation(
            self, record: Any, **_kwargs: Any
        ) -> Any:
            self.claim_calls += 1
            self.claimed_ids.append(record.invocation_id)
            self.records[record.invocation_id] = record
            self.record = record
            return record

    class _OverloadedThenHealthyTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.count = 0

        async def handle_async_request(
            self, request: httpx.Request
        ) -> httpx.Response:
            self.count += 1
            return httpx.Response(
                429 if self.count == 1 else 200,
                json=(
                    {"error": {"type": "engine_overloaded_error"}}
                    if self.count == 1
                    else {"ok": True}
                ),
                request=request,
            )

    physical = _OverloadedThenHealthyTransport()
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "test-key",
        "model-1",
    )
    provider._test_transport = physical
    uow = _RetryFakeUow()
    coordinator = ProviderInvocationCoordinator(
        uow,
        fence_reacquirer=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
    )

    async def invoke() -> dict[str, Any]:
        return await provider.chat_with_tools(
            [{"role": "user", "content": "hi"}],
            max_tokens=32,
        )

    result = await coordinate_provider_call(
        coordinator=coordinator,
        acquire_fence=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
        run_id="run-1",
        provider=provider,
        attempt_id="attempt-429",
        purpose="agent_loop",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        fallback_model="model-1",
        invoke=invoke,
        retry_delay=0,
    )

    assert result["content"] == ""
    assert physical.count == 2
    assert uow.claim_calls == 2
    assert len(set(uow.claimed_ids)) == 2


@pytest.mark.asyncio
async def test_coordinated_provider_call_cancellation_settles_claimed_record() -> None:
    release = asyncio.Event()
    physical = _CountingTransport(release=release)
    transport = DispatchAwareAsyncTransport(
        physical,
        adapter_identity="openai-compatible:v1",
    )

    async def invoke() -> dict[str, bool]:
        async with httpx.AsyncClient(transport=transport) as client:
            return (await client.get("https://example.invalid/provider")).json()

    uow = _FakeUow()
    coordinator = ProviderInvocationCoordinator(
        uow,
        fence_reacquirer=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
    )
    task = asyncio.create_task(
        coordinate_provider_call(
            coordinator=coordinator,
            acquire_fence=lambda run_id: asyncio.sleep(
                0, result=_Lease(run_id, 3)
            ),
            run_id="run-1",
            provider=SimpleNamespace(model="model-1"),
            attempt_id="attempt-cancelled",
            purpose="agent_loop",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            fallback_model="model-1",
            invoke=invoke,
        )
    )
    await physical.entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert str(uow.record.status).split(".")[-1].lower() == "unknown"
    assert uow.unknown_error_type == "CancelledError"


@pytest.mark.asyncio
async def test_coordinated_provider_stream_close_settles_claimed_record() -> None:
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "test-key",
        "model-1",
    )
    provider._test_transport = _SseTransport()
    uow = _FakeUow()
    coordinator = ProviderInvocationCoordinator(
        uow,
        fence_reacquirer=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
    )

    async def iterator():
        async for item in provider.chat_stream_with_tools(
            [{"role": "user", "content": "hi"}],
            max_tokens=32,
        ):
            yield item

    stream = coordinate_provider_stream(
        coordinator=coordinator,
        acquire_fence=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
        run_id="run-1",
        provider=provider,
        attempt_id="attempt-stream-close",
        purpose="agent_loop",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        fallback_model="model-1",
        iterator=iterator,
    )
    first = await anext(stream)
    assert first["provisional"] is True
    await stream.aclose()

    assert str(uow.record.status).split(".")[-1].lower() == "unknown"
    assert uow.unknown_error_type == "GeneratorExit"


@pytest.mark.asyncio
async def test_coordinated_provider_stream_coalesces_many_tiny_deltas() -> None:
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "test-key",
        "model-1",
    )
    provider._test_transport = _ManyDeltaSseTransport(delta_count=100)
    uow = _FakeUow()
    coordinator = ProviderInvocationCoordinator(
        uow,
        fence_reacquirer=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
    )

    async def iterator():
        async for item in provider.chat_stream_with_tools(
            [{"role": "user", "content": "hi"}],
            max_tokens=128,
        ):
            yield item

    events = [
        event
        async for event in coordinate_provider_stream(
            coordinator=coordinator,
            acquire_fence=lambda run_id: asyncio.sleep(
                0, result=_Lease(run_id, 3)
            ),
            run_id="run-1",
            provider=provider,
            attempt_id="attempt-many-deltas",
            purpose="agent_loop",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            fallback_model="model-1",
            iterator=iterator,
        )
    ]

    provisional = [
        event
        for event in events
        if event.get("type") == "delta" and event.get("provisional") is True
    ]
    assert "".join(str(event.get("content") or "") for event in provisional) == (
        "x" * 100
    )
    assert len(provisional) <= 4
    assert events[-1]["type"] == "final"
    assert events[-1]["provisional"] is False
    assert str(uow.record.status).split(".")[-1].lower() == "completed"


@pytest.mark.asyncio
async def test_revocation_epoch_change_suppresses_provider_result() -> None:
    physical = _CountingTransport()
    transport = DispatchAwareAsyncTransport(
        physical,
        adapter_identity="openai-compatible:v1",
    )

    async def invoke() -> dict[str, bool]:
        async with httpx.AsyncClient(transport=transport) as client:
            return (await client.get("https://example.invalid/provider")).json()

    uow = _FakeUow()
    coordinator = ProviderInvocationCoordinator(
        uow,
        fence_reacquirer=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 4)
        ),
    )
    claimed = await coordinator.claim_prepared(
        coordinator.prepare_attempt(_snapshot(invoke)),
        _Lease("run-1", 3),
    )
    assert isinstance(
        await coordinator.start_and_ack(claimed, 1.0),
        DispatchStartedAck,
    )
    with pytest.raises(RuntimeError, match="suppressed_by_fence"):
        await coordinator.complete_or_unknown(claimed)
    assert uow.record.status.value == "unknown"
    assert uow.unknown_reason == "unknown"


@pytest.mark.asyncio
async def test_real_workflow_uow_claims_and_completes_exact_transport_dispatch(
    tmp_path,
) -> None:
    capability_hash = fingerprint_json({"tools": ["read"]})
    db_path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(db_path)
    await uow.initialize()
    await uow.create(
        RunCreate(
            run_id="run-1",
            idempotency_key=root_idempotency_key(
                "session-1", "request-1", "turn-1"
            ),
            context=RunContext(
                session_id="session-1",
                root_run_id="run-1",
                parent_run_id=None,
                request_id="request-1",
                turn_id="turn-1",
                venue="text",
                workspace={},
                capability_hash=capability_hash,
                provider_plan={"model": "model-1"},
                trace_id="trace-1",
                principal_id="principal-1",
                auth_epoch=1,
            ),
            payload_fingerprint=fingerprint_json({"prompt": "hi"}),
            capability_fingerprint=capability_hash,
            driver_kind="react",
            profile_key="react.default",
            persistence_level=PersistenceLevel.DURABLE,
            status=RunStatus.RUNNING,
        )
    )
    physical = _CountingTransport()
    transport = DispatchAwareAsyncTransport(
        physical,
        adapter_identity="openai-compatible:v1",
    )

    async def invoke() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=transport) as client:
            return (
                await client.get("https://example.invalid/provider")
            ).json()

    coordinator = ProviderInvocationCoordinator(
        uow,
        fence_reacquirer=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
    )
    claimed = await coordinator.claim_prepared(
        coordinator.prepare_attempt(_snapshot(invoke)),
        _Lease("run-1", 3),
    )
    replayed = await coordinator.claim_prepared(
        coordinator.prepare_attempt(_snapshot(invoke)),
        _Lease("run-1", 3),
    )
    assert replayed.identity.invocation_id == claimed.identity.invocation_id
    claimed = replayed
    assert physical.count == 0
    assert isinstance(
        await coordinator.start_and_ack(claimed, 1.0),
        DispatchStartedAck,
    )
    result = await coordinator.complete_or_unknown(claimed)
    assert result == {"ok": True}
    assert physical.count == 1
    stored = await uow.read_provider_invocation(
        "run-1", claimed.identity.invocation_id
    )
    outcome = await uow.read_provider_invocation_outcome(
        "run-1", claimed.identity.invocation_id
    )
    assert stored is not None and stored.status.value == "completed"
    assert outcome is not None and outcome.payload_hash == stored.outcome_hash
    await uow.close()


@pytest.mark.asyncio
async def test_real_uow_inspector_exposes_unknown_transport_audit_without_payloads(
    tmp_path,
) -> None:
    capability_hash = fingerprint_json({"tools": ["read"]})
    db_path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(db_path)
    await uow.initialize()
    await uow.create(
        RunCreate(
            run_id="run-1",
            idempotency_key=root_idempotency_key(
                "session-1", "request-1", "turn-1"
            ),
            context=RunContext(
                session_id="session-1",
                root_run_id="run-1",
                parent_run_id=None,
                request_id="request-1",
                turn_id="turn-1",
                venue="text",
                workspace={"root": r"F:\projects\deskpet"},
                capability_hash=capability_hash,
                provider_plan={"model": "model-1", "secret": "must-not-project"},
                trace_id="trace-1",
                principal_id="principal-1",
                auth_epoch=1,
            ),
            payload_fingerprint=fingerprint_json({"prompt": "private prompt"}),
            capability_fingerprint=capability_hash,
            driver_kind="react",
            profile_key="agent.general",
            persistence_level=PersistenceLevel.DURABLE,
            status=RunStatus.RUNNING,
        )
    )
    await uow.create_task_goal(
        TaskGoalRecord(
            goal_id="goal-1",
            root_run_id="run-1",
            task_scope_id="scope-1",
            objective_ref="objective:inspect",
        ),
        PlanVersionRecord(root_run_id="run-1", plan_version=1),
    )
    await uow.open_provider_turn_fence(
        ProviderTurnFence(
            provider_turn_id="provider-turn-1",
            root_run_id="run-1",
            idempotency_key="provider-turn-idempotency-1",
            request_hash=fingerprint_json({"messages": ["private"]}),
        )
    )
    action_call = ProviderActionCall(
        call_record_id="call-record-1",
        root_run_id="run-1",
        provider_batch_id="provider-batch-1",
        call_order=0,
        provider_call_id="stable-call-1",
        raw_tool_name="read",
        raw_arguments_ref="blob:private-arguments",
        raw_arguments_hash=fingerprint_json({"path": "private"}),
    )
    await uow.accept_provider_batch_and_create_attempt(
        ProviderActionBatch(
            provider_batch_id="provider-batch-1",
            root_run_id="run-1",
            provider_turn_id="provider-turn-1",
            canonical_assistant_batch_ref="blob:private-assistant-batch",
            batch_fingerprint=fingerprint_json({"calls": ["stable-call-1"]}),
            pending_call_count=1,
        ),
        AttemptRecord(
            attempt_id="attempt-1",
            root_run_id="run-1",
            run_id="run-1",
            provider_turn_id="provider-turn-1",
            provider_batch_id="provider-batch-1",
            plan_version=1,
            strategy_fingerprint=fingerprint_json({"strategy": "inspect"}),
            planned_call_refs=("call-record-1",),
        ),
        (action_call,),
    )
    await uow.mark_provider_action_prepared(
        action_call.call_record_id,
        expected_version=0,
        parsed_arguments_hash=fingerprint_json({"path": "private"}),
        prepared_call_ref="prepared:stable-call-1",
        command_boundary_ref="command:stable-call-1",
    )
    continuation_payload = {
        "calls": [
            {
                "stable_call_id": "stable-call-1",
                "tool_name": "read",
                "arguments": {"path": "private"},
            }
        ],
        "canonical_messages": [
            {
                "role": "tool",
                "tool_call_id": "stable-call-1",
                "name": "read",
                "content": '{"outcome":{"value":"private tool result"',
            }
        ],
        "outcomes": [{"result": "private tool result"}],
        "completion_state": {
            "agent_loop_feedback": {
                "tool_history": [
                    {
                        "call_id": "stable-call-1",
                        "name": "read",
                        "args": {"path": "private-arguments"},
                        "ok": True,
                    }
                ]
            },
            "current_attempt": {
                "attempt_id": "attempt-1",
                "status": "succeeded",
            }
        },
    }
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO execution_continuations(
            run_id,schema_version,command_schema_version,
            canonical_messages_json,session_projection_cursor,
            prepared_context_ref,tool_set_snapshot_ref,
            pending_prepared_call_json,pending_decision_id,iteration,
            provider_state_json,continuation_version,created_at,updated_at
            ) VALUES(?,1,1,'[]',0,NULL,NULL,?,NULL,1,'{}',1,1.0,1.0)""",
            ("run-1", json.dumps(continuation_payload)),
        )
        await db.commit()
    physical = _ErrorTransport()
    transport = DispatchAwareAsyncTransport(
        physical,
        adapter_identity="openai-compatible:v1",
    )

    async def invoke() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://example.invalid/provider")

    coordinator = ProviderInvocationCoordinator(
        uow,
        fence_reacquirer=lambda run_id: asyncio.sleep(
            0, result=_Lease(run_id, 3)
        ),
    )
    claimed = await coordinator.claim_prepared(
        coordinator.prepare_attempt(_snapshot(invoke)),
        _Lease("run-1", 3),
    )
    assert isinstance(
        await coordinator.start_and_ack(claimed, 1.0),
        DispatchStartedAck,
    )
    with pytest.raises(ProviderDispatchUnknownError):
        await coordinator.complete_or_unknown(claimed)

    snapshot = await uow.inspect_harness_run(
        "run-1",
        expected_session_id="session-1",
    )
    assert snapshot is not None
    assert await uow.get_latest_session_project_context("session-1") == {
        "project_name": "deskpet",
        "project_root": r"F:\projects\deskpet",
    }
    assert snapshot["run"]["profile_key"] == "agent.general"
    assert snapshot["activity"]["phase"] == "agent"
    assert snapshot["activity"]["last_error"] == {
        "source": "provider",
        "code": "transport_error_after_handoff:ReadError",
        "type": "ReadError",
        "message": "connection lost after handoff",
    }
    assert snapshot["tool_calls"][0]["admission_state"] == "prepared"
    assert snapshot["tool_calls"][0]["outcome_status"] == "succeeded"
    assert snapshot["action_batches"][0]["observed_status"] == "succeeded"
    assert snapshot["attempts"][0]["status"] == "running"
    assert snapshot["attempts"][0]["continuation_status"] == "succeeded"
    assert snapshot["provider_invocations"] == [
        {
            **snapshot["provider_invocations"][0],
            "status": "unknown",
            "audit_reason": "transport_error_after_handoff:ReadError",
            "error_type": "ReadError",
            "error_message": "connection lost after handoff",
        }
    ]
    assert snapshot["provider_invocations"][0]["input"] == {
        "schema_version": 1,
        "mode": "full",
        "size_bytes": len(
            json.dumps(
                {"messages": [{"content": "hi", "role": "user"}]},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ),
        "payload": {"messages": [{"content": "hi", "role": "user"}]},
    }
    assert await uow.inspect_harness_run(
        "run-1",
        expected_session_id="different-session",
    ) is None
    serialized = repr(snapshot)
    assert "must-not-project" not in serialized
    assert "private prompt" not in serialized
    assert "private tool result" not in serialized
    assert snapshot["tool_calls"][0]["details"]["raw_arguments_ref"] == (
        "blob:private-arguments"
    )
    await uow.close()

from __future__ import annotations

import asyncio
import ast
import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from deskpet.execution.contracts import (
    OutcomeStatus,
    RecoveryLease,
    StaleRecoveryLease,
    VersionConflict,
)
from deskpet.execution.dispatch import PreparedToolDispatchStartedAck
from deskpet.execution.tool_completion_latch_script import (
    ToolCompletionLatchRejected,
    ToolCompletionLatchScriptV1,
)
from deskpet.harness.context import HostContextFactory
from deskpet.harness.ports import ToolOutcomesSignal
from deskpet.execution.contracts import DecisionAuthorization
from deskpet.harness.tool_executor import EffectBatchExecutor
from deskpet.tools.capabilities import (
    PreparedToolSet,
    ToolCapabilityScopeStore,
    ToolEligibilityContext,
    ToolExecutionContext,
)
from deskpet.tools.context_adapter import ReservedModelFieldError, reject_reserved_model_fields
from deskpet.tools.registry import ToolRegistry
from deskpet.tools.receipt_store import ReceiptStore
from deskpet.types.task_grants import ResourceSelector
from deskpet.workflows.contracts import EffectKind, EffectPolicy
from deskpet.workflows.effects import NormalizedToolOutcome, PreparedToolCall, ToolOutcomeState


SCHEMA = {
    "name": "placeholder",
    "description": "test",
    "parameters": {"type": "object", "properties": {}},
}


CAPABILITY_HASH = "c" * 64
SCOPE_HASH = "d" * 64


def _call(registry: ToolRegistry, name: str, index: int = 1, **kwargs: Any) -> PreparedToolCall:
    return registry.prepare_call(
        name, kwargs.pop("model_args", {"value": index}), "session-host", f"call-{index}"
    )


def _context(call: PreparedToolCall):
    factory = HostContextFactory()
    run = factory.create_run_context(
        session_id="session-host",
        root_run_id="run-1",
        request_id="request-host",
        turn_id="turn-host",
        venue="text",
        workspace=".",
        write_scope_root=".",
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
        provider_plan=("primary", "fallback"),
        trace_id="trace-host",
        principal_id="principal-host",
    )
    return factory.create_tool_context(
        run,
        run_id="run-1",
        call_id=call.stable_call_id,
        effect_id=f"effect-{call.stable_call_id.removeprefix('call-')}",
    )


def _register(
    registry: ToolRegistry,
    name: str,
    *,
    context_handler,
    concurrency_safe: bool = True,
    permission_category: str = "read_file",
    timeout_seconds: float = 1.0,
    effect_policy=None,
    resource_scope_resolver=None,
) -> None:
    registry.register(
        name,
        "test",
        {**SCHEMA, "name": name},
        lambda args, task_id: json.dumps({"ok": True}),
        context_handler=context_handler,
        concurrency_safe=concurrency_safe,
        permission_category=permission_category,
        timeout_seconds=timeout_seconds,
        effect_policy=effect_policy,
        resource_scope_resolver=resource_scope_resolver,
        resource_scope_resolver_id=(
            "test:resource" if resource_scope_resolver is not None else ""
        ),
        outcome_parser_id="json_error_envelope_v1",
    )


def _executor(registry: ToolRegistry) -> EffectBatchExecutor:
    return EffectBatchExecutor(None, registry)  # type: ignore[arg-type]


async def _execute_one(
    executor: EffectBatchExecutor,
    call: PreparedToolCall,
    context: ToolExecutionContext,
    authorization: object | None = None,
) -> NormalizedToolOutcome:
    return await executor._registry.execute_prepared(
        call,
        effect_id=context.effect_id,
        authorization=executor._authorization(authorization, call, context),
        execution_context=context,
    )


def test_model_cannot_override_reserved_host_fields() -> None:
    registry = ToolRegistry()
    _register(registry, "read", context_handler=lambda args, context: "{}")
    with pytest.raises(ReservedModelFieldError) as caught:
        reject_reserved_model_fields({"path": "safe.txt", "session_id": "attacker", "_write_scope_root": "C:/outside"})
    assert caught.value.fields == ("_write_scope_root", "session_id")


@pytest.mark.asyncio
async def test_executor_pins_capability_scope_for_long_running_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr(
        "deskpet.tools.capabilities.time.monotonic", lambda: now[0]
    )
    store = ToolCapabilityScopeStore(ttl_seconds=5.0)
    prepared = PreparedToolSet.create(
        scope_id="scope-long",
        revision=1,
        registry_revision=1,
        direct=(),
        deferred=(),
        activated=(),
        denied_names=(),
        policy_fingerprint="policy",
        decisions=(),
    )
    store.open(
        prepared,
        ToolEligibilityContext("session-host", "request-host", "chat"),
    )

    call_registry = ToolRegistry()
    _register(
        call_registry,
        "slow_read",
        context_handler=lambda _args, _context: "{}",
    )
    call = _call(call_registry, "slow_read")
    context = ToolExecutionContext(
        "scope-long",
        "session-host",
        "request-host",
        effect_id="effect-slow",
    )

    class _LeaseAwareRegistry:
        capability_scope_store = store

        @staticmethod
        def is_concurrency_safe(_name: str) -> bool:
            return False

        @staticmethod
        async def execute_prepared(*_args, **_kwargs):
            now[0] = 106.0
            assert store.get(
                "scope-long",
                session_id="session-host",
                request_id="request-host",
            )
            return NormalizedToolOutcome.success({"ok": True})

    executor = EffectBatchExecutor(None, _LeaseAwareRegistry())  # type: ignore[arg-type]
    outcomes = await executor._execute_segmented(
        (call,), (context,), (None,), (None,)
    )

    assert outcomes[0].state is ToolOutcomeState.SUCCESS
    now[0] = 110.0
    assert store.get(
        "scope-long",
        session_id="session-host",
        request_id="request-host",
    )


@pytest.mark.asyncio
async def test_handler_gets_clean_model_args_and_trusted_context_separately() -> None:
    registry = ToolRegistry()
    observed: dict[str, Any] = {}

    async def handler(args, context):
        observed["args"] = args
        observed["session_id"] = context.session_id
        observed["workspace"] = context.workspace
        observed["provider_plan"] = context.provider_plan
        return json.dumps({"ok": True, "value": "done"})

    _register(registry, "read", context_handler=handler)
    call = _call(registry, "read", model_args={"path": "safe.txt"})
    outcome = await _execute_one(_executor(registry), call, _context(call))

    assert outcome.state is ToolOutcomeState.SUCCESS
    assert observed["args"] == {"path": "safe.txt"}
    assert observed["session_id"] == "session-host"
    assert observed["workspace"]
    assert observed["provider_plan"] == ("primary", "fallback")


@pytest.mark.asyncio
async def test_registry_begin_is_side_effect_free_and_start_ack_precedes_handler() -> None:
    registry = ToolRegistry()
    entered = asyncio.Event()

    async def handler(_args, _context):
        entered.set()
        return json.dumps({"ok": True})

    _register(registry, "three_stage", context_handler=handler)
    call = _call(registry, "three_stage")
    context = _context(call)
    dispatch = await registry.begin_prepared(
        call,
        effect_id=context.effect_id,
        execution_context=context,
    )

    assert entered.is_set() is False
    ack = await dispatch.start(deadline=time.time() + 1)
    assert isinstance(ack, PreparedToolDispatchStartedAck)
    assert entered.is_set() is False

    outcome = await dispatch.completion()
    assert entered.is_set() is True
    assert outcome.state is ToolOutcomeState.SUCCESS


@pytest.mark.asyncio
async def test_execution_scope_fence_releases_after_start_ack_before_completion() -> None:
    registry = ToolRegistry()
    observed: dict[str, bool] = {}

    class Lease:
        released = False

        async def release(self):
            self.released = True

    lease = Lease()

    async def handler(_args, _context):
        observed["released_before_handler"] = lease.released
        return json.dumps({"ok": True})

    _register(registry, "fenced_start", context_handler=handler)
    call = _call(registry, "fenced_start")
    context = _context(call)
    dispatch = await registry.begin_prepared(
        call,
        effect_id=context.effect_id,
        execution_context=context,
    )
    outcomes = await _executor(registry)._execute_segmented(
        (call,),
        (context,),
        (None,),
        (None,),
        dispatches=(dispatch,),
        execution_scope_leases=(lease,),
        metadata=({},),
    )

    assert outcomes[0].state is ToolOutcomeState.SUCCESS
    assert observed == {"released_before_handler": True}


@pytest.mark.asyncio
async def test_effect_start_ack_is_durable_before_scope_fence_release() -> None:
    registry = ToolRegistry()
    timeline: list[str] = []

    class Lease:
        released = False

        async def release(self):
            self.released = True
            timeline.append("scope_released")

    class Handoff:
        effect_version = 8

    class Uow:
        async def mark_effect_dispatch_started(
            self,
            effect_id,
            attempt_no,
            expected_effect_version,
            ack_ref,
            ack_hash,
            *,
            ack_at=None,
        ):
            assert effect_id == "effect-1"
            assert (attempt_no, expected_effect_version) == (3, 7)
            assert ack_ref and ack_hash and ack_at
            assert lease.released is False
            timeline.append("durable_started")
            return Handoff()

    lease = Lease()

    async def handler(_args, _context):
        assert lease.released is True
        timeline.append("handler")
        return json.dumps({"ok": True})

    _register(registry, "durable_start", context_handler=handler)
    call = _call(registry, "durable_start")
    context = _context(call)
    dispatch = await registry.begin_prepared(
        call,
        effect_id=context.effect_id,
        execution_context=context,
    )
    item = {
        "effect_claim": {
            "attempt_no": 3,
            "effect_version": 7,
        }
    }
    executor = EffectBatchExecutor(Uow(), registry)  # type: ignore[arg-type]
    outcomes = await executor._execute_segmented(
        (call,),
        (context,),
        (None,),
        (None,),
        dispatches=(dispatch,),
        execution_scope_leases=(lease,),
        metadata=(item,),
    )

    assert outcomes[0].state is ToolOutcomeState.SUCCESS
    assert timeline == ["durable_started", "scope_released", "handler"]
    assert item["effect_claim"]["effect_version"] == 8


@pytest.mark.asyncio
async def test_cancel_after_started_dispatch_tracks_shielded_completion() -> None:
    registry = ToolRegistry()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(_args, _context):
        entered.set()
        await release.wait()
        return json.dumps({"ok": True, "value": "late"})

    _register(registry, "cancelled-read", context_handler=handler)
    call = _call(registry, "cancelled-read")
    context = _context(call)
    dispatch = await registry.begin_prepared(
        call,
        effect_id=context.effect_id,
        execution_context=context,
    )
    started = await dispatch.start(deadline=time.time() + 1)
    assert isinstance(started, PreparedToolDispatchStartedAck)

    waiter = asyncio.create_task(dispatch.completion())
    await entered.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert await registry.observe_late_prepared(context.effect_id) == (
        "pending",
        None,
    )
    release.set()
    for _ in range(100):
        if registry.ready_late_prepared_effect_ids(context.run_id):
            break
        await asyncio.sleep(0.005)
    assert registry.ready_late_prepared_effect_ids(context.run_id) == (
        context.effect_id,
    )
    state, outcome = await registry.observe_late_prepared(context.effect_id)
    assert state == "complete"
    assert outcome is not None and outcome.state is ToolOutcomeState.SUCCESS
    assert registry.ready_late_prepared_effect_ids(context.run_id) == (
        context.effect_id,
    )
    registry.acknowledge_prepared_effect(context.effect_id)
    assert await registry.observe_late_prepared(context.effect_id) == (
        "missing",
        None,
    )


@pytest.mark.asyncio
async def test_cancel_marks_started_effect_inflight_may_complete() -> None:
    registry = ToolRegistry()
    entered = asyncio.Event()
    release = asyncio.Event()
    transitions: list[tuple[str, int, int, str]] = []

    async def handler(_args, _context):
        entered.set()
        await release.wait()
        return json.dumps({"ok": True})

    class Handoff:
        def __init__(self, version: int) -> None:
            self.effect_version = version

    class Uow:
        async def mark_effect_dispatch_started(
            self, effect_id, attempt_no, effect_version, ack_ref, _ack_hash,
            *, ack_at=None,
        ):
            assert ack_ref and ack_at
            transitions.append(
                ("started", attempt_no, effect_version, effect_id)
            )
            return Handoff(effect_version + 1)

        async def mark_effect_inflight_may_complete(
            self, effect_id, attempt_no, effect_version, receipt_ref, _receipt_hash,
        ):
            assert "consumer-cancelled" in receipt_ref
            transitions.append(
                ("unknown", attempt_no, effect_version, effect_id)
            )
            return Handoff(effect_version + 1)

    _register(registry, "cancelled-effect-read", context_handler=handler)
    call = _call(registry, "cancelled-effect-read")
    context = _context(call)
    dispatch = await registry.begin_prepared(
        call,
        effect_id=context.effect_id,
        execution_context=context,
    )
    item: dict[str, object] = {
        "effect_claim": {"attempt_no": 2, "effect_version": 7}
    }
    executor = EffectBatchExecutor(Uow(), registry)  # type: ignore[arg-type]
    task = asyncio.create_task(
        executor._execute_segmented(
            (call,),
            (context,),
            (None,),
            (None,),
            dispatches=(dispatch,),
            metadata=(item,),
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert transitions == [
        ("started", 2, 7, context.effect_id),
        ("unknown", 2, 8, context.effect_id),
    ]
    assert item == {
        "effect_claim": {"attempt_no": 2, "effect_version": 9},
        "late_pending": True,
    }
    assert await registry.observe_late_prepared(context.effect_id) == (
        "pending",
        None,
    )
    release.set()
    await registry.close_prepared_executions(1)
    state, outcome = await registry.observe_late_prepared(context.effect_id)
    assert state == "complete"
    assert outcome is not None and outcome.state is ToolOutcomeState.SUCCESS
    registry.acknowledge_prepared_effect(context.effect_id)


@pytest.mark.asyncio
async def test_cancel_during_started_ack_persistence_detaches_and_uses_latest_version() -> None:
    registry = ToolRegistry()
    handler_release = asyncio.Event()
    started_committed = asyncio.Event()

    async def handler(_args, _context):
        await handler_release.wait()
        return json.dumps({"ok": True, "value": "late"})

    class Handoff:
        def __init__(self, status, handoff_state, version, attempt_no=2):
            self.status = status
            self.handoff_state = handoff_state
            self.effect_version = version
            self.attempt_no = attempt_no

    class Uow:
        current = Handoff("running", "unresolved", 7)
        unknown_version = None

        async def mark_effect_dispatch_started(
            self, effect_id, attempt_no, effect_version, _ack_ref, _ack_hash,
            *, ack_at=None,
        ):
            assert ack_at is not None
            assert (effect_id, attempt_no, effect_version) == ("effect-1", 2, 7)
            self.current = Handoff("running", "started", 8)
            started_committed.set()
            await asyncio.Event().wait()

        async def read_effect_handoff(self, effect_id):
            assert effect_id == "effect-1"
            return self.current

        async def mark_effect_inflight_may_complete(
            self, effect_id, attempt_no, effect_version, receipt_ref, _receipt_hash,
        ):
            assert "consumer-cancelled" in receipt_ref
            assert (effect_id, attempt_no, effect_version) == ("effect-1", 2, 8)
            self.unknown_version = effect_version
            self.current = Handoff("unknown", "started_may_complete", 9)
            return self.current

    _register(registry, "cancel-during-started-persist", context_handler=handler)
    call = _call(registry, "cancel-during-started-persist")
    context = _context(call)
    dispatch = await registry.begin_prepared(
        call, effect_id=context.effect_id, execution_context=context
    )
    item = {"effect_claim": {"attempt_no": 2, "effect_version": 7}}
    uow = Uow()
    execution = asyncio.create_task(
        EffectBatchExecutor(uow, registry)._execute_segmented(  # type: ignore[arg-type]
            (call,), (context,), (None,), (None,),
            dispatches=(dispatch,), metadata=(item,),
        )
    )
    await started_committed.wait()

    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution

    assert uow.unknown_version == 8
    assert item["effect_claim"]["effect_version"] == 9
    handler_release.set()
    for _ in range(100):
        if registry.ready_late_prepared_effect_ids(context.run_id):
            break
        await asyncio.sleep(0.005)
    assert registry.ready_late_prepared_effect_ids(context.run_id) == (
        context.effect_id,
    )
    state, outcome = await registry.observe_late_prepared(context.effect_id)
    assert state == "complete"
    assert outcome is not None and outcome.state is ToolOutcomeState.SUCCESS
    registry.acknowledge_prepared_effect(context.effect_id)


@pytest.mark.asyncio
async def test_terminal_late_running_and_cas_loser_remain_retryable() -> None:
    registry = ToolRegistry()
    handler_entered = asyncio.Event()
    handler_release = asyncio.Event()

    async def handler(_args, _context):
        handler_entered.set()
        await handler_release.wait()
        return json.dumps({"ok": True, "value": "late"})

    class Handoff:
        def __init__(self, status, handoff_state, version, attempt_no=1):
            self.status = status
            self.handoff_state = handoff_state
            self.effect_version = version
            self.attempt_no = attempt_no

    class Uow:
        current = Handoff("running", "started", 3)
        suppress_calls = 0
        reconcile_calls = 0

        async def read_effect_handoff(self, effect_id):
            assert effect_id == "effect-1"
            return self.current

        async def suppress_late_effect_completion(
            self, effect_id, attempt_no, effect_version, _late_outcome_hash,
        ):
            assert (effect_id, attempt_no, effect_version) == ("effect-1", 1, 4)
            self.suppress_calls += 1
            self.current = Handoff("late_reconciled", "reconciled", 5)
            raise VersionConflict("stale_effect_handoff", "competing settlement won")

        async def reconcile_effect_handoff(self, *_args, **_kwargs):
            self.reconcile_calls += 1
            raise AssertionError("CAS loser must re-read the winning terminal state")

    _register(registry, "late-running-cas", context_handler=handler)
    call = _call(registry, "late-running-cas")
    context = _context(call)
    dispatch = await registry.begin_prepared(
        call, effect_id=context.effect_id, execution_context=context
    )
    assert isinstance(
        await dispatch.start(deadline=time.time() + 1),
        PreparedToolDispatchStartedAck,
    )
    waiter = asyncio.create_task(dispatch.completion())
    await handler_entered.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    handler_release.set()
    for _ in range(100):
        if registry.ready_late_prepared_effect_ids(context.run_id):
            break
        await asyncio.sleep(0.005)

    uow = Uow()
    executor = EffectBatchExecutor(uow, registry)  # type: ignore[arg-type]
    assert await executor.quarantine_terminal_late(context.run_id) == 0
    assert executor.ready_run_ids() == frozenset({context.run_id})

    uow.current = Handoff("unknown", "started_may_complete", 4)
    assert await executor.quarantine_terminal_late(context.run_id) == 1
    assert uow.suppress_calls == 1
    assert uow.reconcile_calls == 0
    assert executor.ready_run_ids() == frozenset()


@pytest.mark.asyncio
async def test_dev_tool_completion_latch_holds_only_completed_read_outcome(
    tmp_path: Path,
) -> None:
    script_path = tmp_path / "latch.json"
    script_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "rules": [
                    {
                        "rule_id": "late-read-1",
                        "injection_ref": "late-read-ref-1",
                        "session_id": "session-host",
                        "tool_name": "read-file",
                        "occurrence": 1,
                        "armed_file": "late-read.armed.json",
                        "release_file": "late-read.release",
                        "timeout_seconds": 5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ToolCompletionLatchRejected, match="DEV_MODE"):
        ToolCompletionLatchScriptV1.from_file(script_path, environ={})
    latch = ToolCompletionLatchScriptV1.from_file(
        script_path,
        environ={"DESKPET_DEV_MODE": "1"},
    )

    assert await latch.hold_completed_outcome(
        session_id="session-host",
        run_id="run-write",
        effect_id="effect-write",
        tool_name="read-file",
        effect_type="opaque_manual",
        outcome_state="succeeded",
    ) is None
    held = asyncio.create_task(
        latch.hold_completed_outcome(
            session_id="session-host",
            run_id="run-1",
            effect_id="effect-1",
            tool_name="read-file",
            effect_type="idempotent_read",
            outcome_state="succeeded",
        )
    )
    armed = tmp_path / "late-read.armed.json"
    for _ in range(100):
        if armed.exists():
            break
        await asyncio.sleep(0.005)
    assert held.done() is False
    armed_payload = json.loads(armed.read_text(encoding="utf-8"))
    assert armed_payload.pop("armed_at") > 0
    assert armed_payload == {
        "schema_version": 1,
        "injection_ref": "late-read-ref-1",
        "session_id": "session-host",
        "run_id": "run-1",
        "effect_id": "effect-1",
        "tool_name": "read-file",
        "outcome_state": "succeeded",
    }
    (tmp_path / "late-read.release").write_text("release", encoding="utf-8")
    assert await held == "late-read-ref-1"
    assert await latch.active_injection_count() == 0


@pytest.mark.asyncio
async def test_effect_completion_after_run_fence_epoch_drift_has_no_body_outcome() -> None:
    registry = ToolRegistry()
    physical_calls = 0

    class RunLease:
        def __init__(self, epoch: int) -> None:
            self.run_id = "run-1"
            self.revocation_epoch = epoch
            self.terminal_commit_fence = None

        async def release(self):
            return None

    class RunFence:
        epoch = 1

        async def acquire(self, _run_id: str):
            return RunLease(self.epoch)

    fence = RunFence()

    async def handler(_args, _context):
        nonlocal physical_calls
        physical_calls += 1
        fence.epoch = 2
        return json.dumps({"ok": True, "secret_body": "must-not-escape"})

    _register(registry, "epoch_drift", context_handler=handler)
    call = _call(registry, "epoch_drift")
    context = _context(call)
    dispatch = await registry.begin_prepared(
        call,
        effect_id=context.effect_id,
        execution_context=context,
    )
    executor = EffectBatchExecutor(
        None,  # type: ignore[arg-type]
        registry,
        provider_fence_acquirer=fence.acquire,
    )

    outcomes = await executor._execute_segmented(
        (call,),
        (context,),
        (None,),
        (None,),
        dispatches=(dispatch,),
        metadata=({},),
    )

    assert physical_calls == 1
    assert outcomes[0].state is ToolOutcomeState.MALFORMED
    assert "must-not-escape" not in json.dumps(outcomes[0].to_dict())


@pytest.mark.asyncio
async def test_mcp_bridge_receives_only_model_args_and_cannot_request_host_context() -> None:
    registry = ToolRegistry()
    observed: dict[str, Any] = {}

    def remote_handler(args, task_id):
        observed["args"] = args
        observed["task_id"] = task_id
        return json.dumps({"isError": False, "value": "done"})

    registry.register(
        "remote",
        "mcp",
        {**SCHEMA, "name": "remote"},
        remote_handler,
        source="mcp:test",
        outcome_parser_id="mcp_explicit_v1",
    )
    call = _call(registry, "mcp_test_remote", model_args={"query": "safe"})
    outcome = await _execute_one(_executor(registry), call, _context(call))
    assert outcome.state is ToolOutcomeState.SUCCESS
    assert observed == {"args": {"query": "safe"}, "task_id": _context(call).effect_id}

    with pytest.raises(ValueError, match="cannot receive trusted host context"):
        registry.register(
            "privileged",
            "mcp",
            {**SCHEMA, "name": "privileged"},
            remote_handler,
            context_handler=lambda args, context: "forbidden",
            source="mcp:test",
        )


@pytest.mark.asyncio
async def test_batch_uses_contiguous_safe_segments_and_unsafe_barriers() -> None:
    registry = ToolRegistry()
    timeline: dict[str, dict[str, float]] = {}

    def make_handler(name: str, delay: float):
        async def handler(args, context):
            timeline[name] = {"start": time.monotonic()}
            await asyncio.sleep(delay)
            timeline[name]["end"] = time.monotonic()
            return json.dumps({"ok": True, "name": name})

        return handler

    definitions = [
        ("safe-a", True, 0.03),
        ("safe-b", True, 0.03),
        ("unsafe-a", False, 0.01),
        ("unsafe-b", False, 0.01),
        ("safe-c", True, 0.001),
    ]
    for name, safe, delay in definitions:
        _register(
            registry,
            name,
            context_handler=make_handler(name, delay),
            concurrency_safe=safe,
        )
    calls = [_call(registry, name, index) for index, (name, _, _) in enumerate(definitions, 1)]
    outcomes = await _executor(registry)._execute_segmented(
        calls, [_context(call) for call in calls], [None] * len(calls),
        [None] * len(calls),
    )

    assert all(outcome.state is ToolOutcomeState.SUCCESS for outcome in outcomes)
    assert abs(timeline["safe-a"]["start"] - timeline["safe-b"]["start"]) < 0.02
    assert timeline["unsafe-a"]["start"] >= max(
        timeline["safe-a"]["end"], timeline["safe-b"]["end"]
    )
    assert timeline["unsafe-b"]["start"] >= timeline["unsafe-a"]["end"]
    assert timeline["safe-c"]["start"] >= timeline["unsafe-b"]["end"]


@pytest.mark.asyncio
async def test_resource_coordinator_serializes_same_desktop_across_batches() -> None:
    registry = ToolRegistry()
    active = 0
    max_active = 0

    async def handler(args, context):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return json.dumps({"ok": True})

    def desktop_lane(_args, _context):
        return (
            ResourceSelector(
                "desktop_target",
                "desktop-input:primary",
                ("control", "input"),
            ),
        )

    _register(
        registry,
        "desktop-input",
        context_handler=handler,
        concurrency_safe=True,
        resource_scope_resolver=desktop_lane,
    )
    def prepare_context(call_id: str) -> ToolExecutionContext:
        return ToolExecutionContext(
            scope_id="scope",
            session_id="session-host",
            request_id="request-host",
            workspace=".",
            write_scope_root=".",
            call_id=call_id,
            effect_id=f"effect-{call_id}",
        )
    first = registry.prepare_call(
        "desktop-input",
        {"value": 1},
        "session-host",
        "call-1",
        execution_context=prepare_context("call-1"),
    )
    second = registry.prepare_call(
        "desktop-input",
        {"value": 2},
        "session-host",
        "call-2",
        execution_context=prepare_context("call-2"),
    )
    executor = _executor(registry)

    first_outcomes, second_outcomes = await asyncio.gather(
        executor._execute_segmented(
            (first,), (_context(first),), (None,), (None,)
        ),
        executor._execute_segmented(
            (second,), (_context(second),), (None,), (None,)
        ),
    )

    assert max_active == 1
    assert first_outcomes[0].state is ToolOutcomeState.SUCCESS
    assert second_outcomes[0].state is ToolOutcomeState.SUCCESS
    assert executor._resource_execution._locks == {}


@pytest.mark.asyncio
async def test_permission_accepts_only_exact_decision_authorization_binding() -> None:
    registry = ToolRegistry()
    invocations = 0

    def handler(args, context):
        nonlocal invocations
        invocations += 1
        return json.dumps({"ok": True})

    _register(
        registry,
        "write",
        context_handler=handler,
        permission_category="write_file",
    )
    call = _call(registry, "write")
    context = _context(call)
    executor = _executor(registry)
    wrong_type = await _execute_one(
        executor, call, context, {"allow": True}
    )
    assert wrong_type.state is ToolOutcomeState.FAILURE
    assert wrong_type.error["code"] == "authorization_run_id_mismatch"

    canonical = registry.prepare_call(
        call.tool_name,
        dict(call.final_params),
        context.session_id,
        call.stable_call_id,
        execution_context=context,
    )

    wrong_binding = DecisionAuthorization(
        grant_id="grant-1",
        decision_id="decision-1",
        run_id=context.run_id,
        call_id=call.stable_call_id,
        effect_id=context.effect_id,
        tool_name=call.tool_name,
        args_hash=canonical.args_hash,
        capability_hash="e" * 64,
        scope_hash=context.scope_hash,
        expires_at=time.time() + 60,
    )
    denied = await _execute_one(
        executor, call, context, wrong_binding
    )
    assert denied.error["code"] == "authorization_capability_hash_mismatch"

    valid = {
        "grant_id": "grant-1",
        "decision_id": "decision-1",
        "run_id": context.run_id,
        "session_id": context.session_id,
        "call_id": call.stable_call_id,
        "effect_id": context.effect_id,
        "tool_name": call.tool_name,
        "args_hash": canonical.args_hash,
        "capability_hash": context.capability_hash,
        "scope_hash": context.scope_hash,
        "permission_policy_version": canonical.permission_policy_version,
        "expires_at": time.time() + 60,
    }
    succeeded = await _execute_one(executor, call, context, valid)
    assert succeeded.state is ToolOutcomeState.SUCCESS
    assert invocations == 1


@pytest.mark.asyncio
async def test_canonical_write_timeout_is_malformed_and_not_reinvoked() -> None:
    registry = ToolRegistry()
    invocations = 0

    def slow_handler(args, context):
        nonlocal invocations
        invocations += 1
        time.sleep(0.05)
        return json.dumps({"ok": True, "written": 1})

    _register(
        registry,
        "slow-write",
        context_handler=slow_handler,
        permission_category="write_file",
        timeout_seconds=0.01,
    )
    call = _call(registry, "slow-write")
    context = _context(call)
    authorization = DecisionAuthorization(
        grant_id="grant-1",
        decision_id="decision-1",
        run_id=context.run_id,
        call_id=call.stable_call_id,
        effect_id=context.effect_id,
        tool_name=call.tool_name,
        args_hash=call.args_hash,
        capability_hash=context.capability_hash,
        scope_hash=context.scope_hash,
        expires_at=time.time() + 60,
    )
    canonical = registry.prepare_call(
        call.tool_name,
        dict(call.final_params),
        context.session_id,
        call.stable_call_id,
        execution_context=context,
    )
    authorization = {
        "grant_id": authorization.grant_id,
        "decision_id": authorization.decision_id,
        "run_id": context.run_id,
        "session_id": context.session_id,
        "call_id": call.stable_call_id,
        "effect_id": context.effect_id,
        "tool_name": call.tool_name,
        "args_hash": canonical.args_hash,
        "capability_hash": context.capability_hash,
        "scope_hash": context.scope_hash,
        "permission_policy_version": canonical.permission_policy_version,
        "expires_at": authorization.expires_at,
    }
    outcome = await _execute_one(
        _executor(registry), call, context, authorization
    )
    assert outcome.state is ToolOutcomeState.MALFORMED
    assert outcome.error["code"] == "malformed_tool_outcome"
    assert registry.take_prepared_execution_metadata(context.effect_id) == {
        "late_pending": True
    }
    assert registry.unregister(call.tool_name) is True

    await asyncio.sleep(0.06)
    state, late = await registry.observe_late_prepared(context.effect_id)
    assert state == "complete"
    assert late is not None and late.state is ToolOutcomeState.SUCCESS
    assert await registry.observe_late_prepared(context.effect_id) == (state, late)
    assert registry.take_prepared_execution_metadata(context.effect_id) == {
        "outcome_status": "succeeded"
    }
    registry.acknowledge_prepared_effect(context.effect_id)
    assert await registry.observe_late_prepared(context.effect_id) == ("missing", None)
    assert invocations == 1


@pytest.mark.asyncio
async def test_registry_close_waits_for_late_effect_without_discarding_evidence() -> None:
    registry, release = ToolRegistry(), threading.Event()
    _register(
        registry, "closing-write",
        context_handler=lambda args, context: (release.wait(1), '{"ok":true}')[1],
        timeout_seconds=0.005,
        effect_policy=EffectPolicy("test:closing", "v1", EffectKind.OPAQUE_MANUAL),
    )
    call = _call(registry, "closing-write")
    executor = _executor(registry)
    outcome = await _execute_one(executor, call, _context(call))
    asyncio.get_running_loop().call_later(0.01, release.set)

    await executor.drain(0.1)

    assert outcome.state is ToolOutcomeState.MALFORMED
    assert executor.ready_run_ids() == frozenset({"run-1"})
    state, settled = await registry.observe_late_prepared("effect-1")
    assert state == "complete"
    assert settled is not None and settled.state is ToolOutcomeState.SUCCESS


@pytest.mark.asyncio
async def test_registry_close_bound_retains_still_running_late_effect() -> None:
    registry, release = ToolRegistry(), threading.Event()
    _register(
        registry, "bounded-write",
        context_handler=lambda args, context: (release.wait(1), '{"ok":true}')[1],
        timeout_seconds=0.005,
        effect_policy=EffectPolicy("test:bounded", "v1", EffectKind.OPAQUE_MANUAL),
    )
    call = _call(registry, "bounded-write")
    executor = _executor(registry)
    await _execute_one(executor, call, _context(call))

    await executor.drain(0.005)

    assert await registry.observe_late_prepared("effect-1") == ("pending", None)
    assert registry.take_prepared_execution_metadata("effect-1") == {"late_pending": True}
    release.set()
    for _ in range(100):
        if executor.ready_run_ids():
            break
        await asyncio.sleep(0.005)
    assert executor.ready_run_ids() == frozenset({"run-1"})


@pytest.mark.asyncio
async def test_prepared_receipt_and_artifact_refs_are_exposed_for_atomic_settlement(tmp_path) -> None:
    output = tmp_path / "result.txt"
    output.write_text("durable result", encoding="utf-8")
    registry = ToolRegistry()
    registry.set_receipt_store_provider(
        lambda: ReceiptStore(tmp_path / "receipts", key=b"r" * 32)
    )
    _register(
        registry, "artifact-write",
        context_handler=lambda args, context: json.dumps({"ok": True, "path": str(output)}),
        effect_policy=EffectPolicy("test:artifact", "v7", EffectKind.OPAQUE_MANUAL),
    )
    call = _call(
        registry,
        "artifact-write",
        model_args={
            "items": [
                MappingProxyType(
                    {"schedule": MappingProxyType({"kind": "weekly"})}
                )
            ]
        },
    )
    outcome = await _execute_one(_executor(registry), call, _context(call))
    metadata = registry.take_prepared_execution_metadata("effect-1")

    assert outcome.state is ToolOutcomeState.SUCCESS
    assert call.effect_policy_version == "v7"
    assert metadata["receipt_ref"]
    assert len(metadata["artifact_refs"]) == 1
    assert len(metadata["artifact_refs"][0]) == 64
    assert metadata["evidence_verified"] is True


def test_effect_policy_version_is_durable_and_rechecked() -> None:
    registry = ToolRegistry()
    _register(
        registry, "policy-write", context_handler=lambda args, context: "{}",
        effect_policy=EffectPolicy("test:policy", "v1", EffectKind.OPAQUE_MANUAL),
    )
    call = _call(registry, "policy-write")
    restored = PreparedToolCall.from_dict(call.to_dict())
    assert restored.effect_policy_version == "v1"
    registry.register(
        "policy-write", "test", {**SCHEMA, "name": "policy-write"},
        lambda args, task_id: "{}", context_handler=lambda args, context: "{}",
        effect_policy=EffectPolicy("test:policy", "v2", EffectKind.OPAQUE_MANUAL),
        outcome_parser_id="json_error_envelope_v1", replace_allowed=True,
    )
    with pytest.raises(ValueError, match="stale"):
        registry.prepared_execution_policy(restored)


@pytest.mark.asyncio
async def test_generate_image_generating_result_is_accepted_not_succeeded() -> None:
    registry = ToolRegistry()
    _register(
        registry, "generate_image",
        context_handler=lambda args, context: json.dumps({"ok": True, "status": "generating"}),
    )
    call = _call(registry, "generate_image")
    outcome = await _execute_one(_executor(registry), call, _context(call))
    assert registry.prepared_outcome_status(call, outcome) is OutcomeStatus.ACCEPTED


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        (NormalizedToolOutcome.success({}), OutcomeStatus.FAILED),
        (NormalizedToolOutcome.failure("failed", "failed"), OutcomeStatus.SUCCEEDED),
        (NormalizedToolOutcome.malformed("unknown"), OutcomeStatus.ACCEPTED),
        (NormalizedToolOutcome.success({}), OutcomeStatus.WAITING),
        (NormalizedToolOutcome.success({}), OutcomeStatus.CANCEL_REQUESTED),
    ],
)
def test_tool_outcome_signal_rejects_incompatible_state_status(outcome, status) -> None:
    with pytest.raises(ValueError, match="incompatible"):
        ToolOutcomesSignal("run", "command", (outcome,), (status,), (0,))


@pytest.mark.asyncio
async def test_persisted_canonical_snapshot_is_rechecked_against_context() -> None:
    registry = ToolRegistry()
    _register(registry, "read", context_handler=lambda args, context: "{}")
    prepared = _call(registry, "read", model_args={"path": "safe.txt"})
    context = _context(prepared)
    call = PreparedToolCall.from_dict(prepared.to_dict())
    assert call == prepared
    wrong = replace(context, call_id="other")
    outcome = await _execute_one(_executor(registry), call, wrong)
    assert outcome.state is ToolOutcomeState.FAILURE
    assert outcome.error["code"] == "trusted_context_call_binding_mismatch"


def test_migrated_handlers_do_not_read_reserved_fields_from_model_args() -> None:
    tools_root = Path(__file__).resolve().parents[2] / "deskpet" / "tools"
    inventory = [
        tools_root / "file_tools.py",
        tools_root / "image_tools.py",
        tools_root / "ppt_tools.py",
        tools_root / "research_tools.py",
        tools_root / "os_tools" / "read_file.py",
        tools_root / "os_tools" / "write_file.py",
        tools_root / "os_tools" / "edit_file.py",
        tools_root / "os_tools" / "run_shell.py",
        tools_root / "code_tools" / "glob_tool.py",
        tools_root / "code_tools" / "grep_tool.py",
        tools_root / "code_tools" / "clarify_tool.py",
        tools_root / "code_tools" / "agent_tool.py",
        tools_root / "code_tools" / "agent_parallel_tool.py",
        tools_root / "code_tools" / "spawn_subagents_tool.py",
        tools_root / "code_tools" / "spawn_team_tool.py",
        tools_root / "code_tools" / "todo_write_tool.py",
    ]
    reserved = {
        "_session_id",
        "_project_root",
        "_write_scope_root",
        "_request_id",
        "_turn_id",
        "_call_id",
        "_effect_id",
        "_image_worker",
    }
    violations: list[str] = []
    for path in inventory:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "get" or not node.args:
                continue
            key = node.args[0]
            if isinstance(key, ast.Constant) and key.value in reserved:
                violations.append(f"{path.name}:{node.lineno}:{key.value}")
    assert violations == []


def test_all_inventory_handlers_expose_explicit_context_adapter_parameter() -> None:
    tools_root = Path(__file__).resolve().parents[2] / "deskpet" / "tools"
    expected = {
        tools_root / "file_tools.py": {
            "_handle_file_read",
            "_handle_file_write",
            "_handle_file_glob",
            "_handle_file_grep",
            "_handle_workspace_recall",
        },
        tools_root / "image_tools.py": {"_handle_generate_image"},
        tools_root / "ppt_tools.py": {"_handle_ppt_create", "_submit_ppt_pro"},
        tools_root / "research_tools.py": {"_handle_deepresearch"},
        tools_root / "os_tools" / "read_file.py": {"read_file"},
        tools_root / "os_tools" / "write_file.py": {"write_file"},
        tools_root / "os_tools" / "edit_file.py": {"edit_file"},
        tools_root / "os_tools" / "run_shell.py": {"run_shell"},
        tools_root / "code_tools" / "glob_tool.py": {"glob_tool"},
        tools_root / "code_tools" / "grep_tool.py": {"grep_tool"},
        tools_root / "code_tools" / "clarify_tool.py": {"_handler"},
        tools_root / "code_tools" / "todo_write_tool.py": {"_handler"},
    }
    missing: list[str] = []
    for path, names in expected.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        }
        for name in names:
            node = functions.get(name)
            kwonly = {arg.arg for arg in node.args.kwonlyargs} if node else set()
            if node is None or "execution_context" not in kwonly:
                missing.append(f"{path.name}:{name}")
    assert missing == []


def test_effect_batch_executor_has_one_high_level_boundary() -> None:
    harness = Path(__file__).resolve().parents[2] / "deskpet" / "harness"
    executor_tree = ast.parse((harness / "tool_executor.py").read_text(encoding="utf-8"))
    classes = {
        node.name: node for node in executor_tree.body if isinstance(node, ast.ClassDef)
    }
    assert "UnifiedToolExecutor" not in classes
    public = {
        node.name
        for node in classes["EffectBatchExecutor"].body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    assert public == {
        "execute",
        "acknowledge_committed",
        "ready_run_ids",
        "quarantine_terminal_late",
        "drain",
    }
    assert "persist_react_boundary" not in (harness / "tool_executor.py").read_text(
        encoding="utf-8"
    )


def test_runtime_tool_branch_is_only_high_level_effect_orchestration() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "deskpet" / "harness" / "runtime.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "claim_tool_call", "settle_effect", "read_effect_outcome",
        "prepared_execution_policy", "observe_late_prepared", "late_pending",
    ):
        assert forbidden not in source

# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5a black-box tests: per-turn Context authority, no-recall sink, envelope.

Oracle source: testcase/human-memory-program/s5a-context-route-verification-spec.json
(S5A-S3 snapshot three-hash / revision monotonic subset, S5A-S4 no-recall
decision shape, S5A-S6 composition fail-closed subset).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio
from simple_harness import RequestId
from simple_harness.contracts import RunId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.execution.context_authority import (
    ContextRouteReceipt,
    ContextRouteState,
    RunContextAuthorityRequest,
    TaskExecutionEnvelopeRequest,
)
from simple_harness.execution.provider_invocations import (
    provider_request_fingerprint,
)
from simple_harness.providers import ProviderRequest, ProviderToolSpec
from simple_harness.runtime.context import ContextSnapshot
from simple_harness.runtime.task_scope_protocol import TaskScopeRoute
from simple_harness.tools.runtime_catalog import (
    ToolEffectClass,
    ToolExecutionPolicy,
    ToolRouteRequirement,
    ToolTaskScopeRequirement,
)

from deskpet.memory.schema import initialize_human_memory_program_state_db
from deskpet.sdk_adapters.context_authority import (
    ContextRouteLedgerError,
    ContextRouteLedgerStore,
    ProductRunContextAuthority,
    ProductRuntimeDecisionSink,
    SnapshotContractConflict,
)
from deskpet.sdk_adapters.task_execution import (
    ProductTaskExecutionAuthority,
    TaskExecutionAuthorityError,
)

RUN = RunId("run-s5a-1")
CATALOG_FINGERPRINT = "f" * 64


class _FakeContext:
    def __init__(self) -> None:
        self.messages = [Message(role=MessageRole.USER, content="hello")]
        self.revision = 1

    def load(self, run_id: RunId) -> ContextSnapshot:
        del run_id
        return ContextSnapshot(self.revision, tuple(self.messages))


class _FakeCheckpoint:
    def __init__(self, start_input: dict | None = None) -> None:
        self._input = {} if start_input is None else start_input

    def read_start_snapshot(self, run_id: str) -> dict:
        del run_id
        return {"input": self._input}


class _FakePorts:
    def __init__(self, context: _FakeContext, checkpoint: _FakeCheckpoint) -> None:
        self.context = context
        self.react_checkpoint = checkpoint


class _FakeExposure:
    def __init__(self, specs: tuple[ProviderToolSpec, ...]) -> None:
        self._specs = specs

    def provider_specs(self, run_id: RunId) -> tuple[ProviderToolSpec, ...]:
        del run_id
        return self._specs


@pytest_asyncio.fixture()
async def state_db(tmp_path: Path) -> Path:
    path = tmp_path / "state.db"
    await initialize_human_memory_program_state_db(path)
    return path


def _authority(
    state_db: Path,
    context: _FakeContext,
    *,
    start_input: dict | None = None,
    specs: tuple[ProviderToolSpec, ...] = (),
) -> ProductRunContextAuthority:
    ports = _FakePorts(context, _FakeCheckpoint(start_input))
    exposure = _FakeExposure(specs)
    return ProductRunContextAuthority(
        ports_resolver=lambda: ports,
        exposure_resolver=lambda run_id: exposure,
        ledger=ContextRouteLedgerStore(state_db),
    )


def _request(
    ordinal: int, revision: int, *, route_receipt: ContextRouteReceipt | None = None
) -> RunContextAuthorityRequest:
    return RunContextAuthorityRequest(
        RUN,
        ordinal,
        revision,
        ContextRouteState.UNROUTED if route_receipt is None else route_receipt.route_state,
        route_receipt,
        CATALOG_FINGERPRINT,
    )


@pytest.mark.asyncio
async def test_prepare_snapshot_three_hash_lineage_and_durable_receipt(
    state_db: Path,
) -> None:
    context = _FakeContext()
    specs = (
        ProviderToolSpec(
            name="context_route",
            description="route",
            parameters={"type": "object", "properties": {}},
        ),
    )
    authority = _authority(state_db, context, specs=specs)
    snapshot = await authority.prepare_snapshot(_request(1, context.revision))

    assert snapshot.run_id == RUN.value
    assert snapshot.provider_turn_ordinal == 1
    assert snapshot.prior_context_revision == context.revision
    assert snapshot.snapshot_revision == 1
    rebuilt = ProviderRequest(
        RequestId("hash-only"),
        snapshot.messages,
        tools=snapshot.tools,
        temperature=snapshot.temperature,
        max_output_tokens=snapshot.max_output_tokens,
        metadata=snapshot.metadata,
    )
    fingerprint = provider_request_fingerprint(rebuilt)
    assert snapshot.expected_request_fingerprint == fingerprint
    assert snapshot.payload_hash == fingerprint

    with sqlite3.connect(state_db) as db:
        row = db.execute(
            "SELECT snapshot_id,snapshot_revision,payload_hash,"
            "expected_request_fingerprint FROM run_context_snapshot_receipts "
            "WHERE sdk_run_id=?",
            (RUN.value,),
        ).fetchone()
    assert row is not None
    assert row[0] == snapshot.snapshot_id
    assert row[1] == 1
    assert row[2] == row[3] == fingerprint


@pytest.mark.asyncio
async def test_prepare_snapshot_revision_monotonic_and_replay_identity(
    state_db: Path,
) -> None:
    context = _FakeContext()
    authority = _authority(state_db, context)
    first = await authority.prepare_snapshot(_request(1, context.revision))
    replay = await authority.prepare_snapshot(_request(1, context.revision))
    assert replay.snapshot_id == first.snapshot_id
    assert replay.snapshot_revision == first.snapshot_revision
    assert replay.payload_hash == first.payload_hash

    context.messages.append(Message(role=MessageRole.ASSISTANT, content="hi"))
    context.revision += 1
    second = await authority.prepare_snapshot(_request(2, context.revision))
    assert second.snapshot_revision > first.snapshot_revision
    assert second.snapshot_id != first.snapshot_id


@pytest.mark.asyncio
async def test_prepare_snapshot_rejects_context_revision_drift(
    state_db: Path,
) -> None:
    context = _FakeContext()
    authority = _authority(state_db, context)
    with pytest.raises(SnapshotContractConflict):
        await authority.prepare_snapshot(_request(1, context.revision + 1))


@pytest.mark.asyncio
async def test_prepare_snapshot_mirrors_start_sampling_params(
    state_db: Path,
) -> None:
    context = _FakeContext()
    authority = _authority(
        state_db, context, start_input={"temperature": 0.5, "max_output_tokens": 64}
    )
    snapshot = await authority.prepare_snapshot(_request(1, context.revision))
    assert snapshot.temperature == 0.5
    assert snapshot.max_output_tokens == 64


@pytest.mark.asyncio
async def test_record_no_recall_returns_direct_standalone_durable_decision(
    state_db: Path,
) -> None:
    sink = ProductRuntimeDecisionSink(ledger=ContextRouteLedgerStore(state_db))
    fingerprint = "a" * 64
    receipt = await sink.record_no_recall(
        run_id=RUN, provider_turn_ordinal=1, request_fingerprint=fingerprint
    )
    assert receipt.run_id == RUN.value
    assert receipt.route is TaskScopeRoute.DIRECT_STANDALONE
    assert receipt.recall_refs == ()
    assert receipt.route_state is ContextRouteState.ROUTED_STANDALONE

    replay = await sink.record_no_recall(
        run_id=RUN, provider_turn_ordinal=1, request_fingerprint=fingerprint
    )
    assert replay.receipt_id == receipt.receipt_id

    with sqlite3.connect(state_db) as db:
        rows = db.execute(
            "SELECT origin,route,request_fingerprint FROM context_route_decisions "
            "WHERE sdk_run_id=?",
            (RUN.value,),
        ).fetchall()
    assert rows == [("no_recall", "direct_standalone", fingerprint)]

    with pytest.raises(ContextRouteLedgerError):
        await sink.record_no_recall(
            run_id=RUN, provider_turn_ordinal=1, request_fingerprint="b" * 64
        )


def _policy(effect_class: ToolEffectClass) -> ToolExecutionPolicy:
    return ToolExecutionPolicy(
        capability_id="cap-1",
        capability_fingerprint="c" * 64,
        effect_class=effect_class,
        route_requirement=ToolRouteRequirement.OPTIONAL,
        task_scope_requirement=ToolTaskScopeRequirement.OPTIONAL,
    )


def _envelope_request(
    policy: ToolExecutionPolicy,
    receipt: ContextRouteReceipt | None,
) -> TaskExecutionEnvelopeRequest:
    return TaskExecutionEnvelopeRequest(
        RUN,
        "call-1",
        "effect-1",
        "raw-1",
        1,
        0,
        "demo_tool",
        policy,
        receipt,
    )


@pytest.mark.asyncio
async def test_issue_envelope_echoes_exact_effect_identity() -> None:
    authority = ProductTaskExecutionAuthority()
    request = _envelope_request(_policy(ToolEffectClass.NON_PROJECT_EFFECT), None)
    envelope = await authority.issue_envelope(request)
    assert envelope.run_id == RUN
    assert envelope.call_id.value == "call-1"
    assert envelope.effect_id.value == "effect-1"
    assert envelope.idempotency_key == "effect-1"
    assert envelope.capability_id == "cap-1"
    assert envelope.capability_fingerprint == "c" * 64
    assert envelope.task_scope_id is None
    assert envelope.root_id is None


@pytest.mark.asyncio
async def test_issue_envelope_project_effect_fails_closed() -> None:
    authority = ProductTaskExecutionAuthority()
    with pytest.raises(TaskExecutionAuthorityError) as no_route:
        await authority.issue_envelope(
            _envelope_request(_policy(ToolEffectClass.PROJECT_EFFECT), None)
        )
    assert "route_authority_missing" in str(no_route.value)

    receipt = ContextRouteReceipt(
        receipt_id="receipt-1",
        run_id=RUN.value,
        raw_call_id="raw-1",
        effect_id="effect-1",
        route=TaskScopeRoute.RESUME_EXISTING,
        task_scope_id="scope-1",
        binding_set_revision=1,
        binding_set_receipt_id="bind-1",
        binding_set_receipt_hash="d" * 64,
    )
    with pytest.raises(TaskExecutionAuthorityError) as no_root:
        await authority.issue_envelope(
            _envelope_request(_policy(ToolEffectClass.PROJECT_EFFECT), receipt)
        )
    assert "root_authority_unavailable" in str(no_root.value)


@pytest.mark.asyncio
async def test_ledger_verify_schema_fails_closed_on_pre_v45_db(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as db:
        db.execute("PRAGMA user_version=44")
        db.commit()
    with pytest.raises(ContextRouteLedgerError):
        ContextRouteLedgerStore(legacy).verify_schema()


@pytest.mark.asyncio
async def test_window_resolves_from_production_start_shapes(state_db: Path) -> None:
    """Round-2 audit P1: both production context_metadata shapes (scalar
    context_window on chat, run_binding.context_window on foreground) must
    reach the frozen-budget tier — never silently fall to the smallest tier."""

    from deskpet.sdk_adapters.context_authority import _resolve_window_tokens

    chat_shape = {"context_window": 128000, "run_binding": {"context_window": 128000}}
    foreground_shape = {"run_binding": {"context_window": 32768}}
    harness_shape = {"budget": {"context_window": 32768}}
    assert _resolve_window_tokens(chat_shape) == 128000
    assert _resolve_window_tokens(foreground_shape) == 32768
    assert _resolve_window_tokens(harness_shape) == 32768
    assert _resolve_window_tokens({}) is None

    context = _FakeContext()
    big_persona = "规" * 3000
    context.messages = [
        Message(role=MessageRole.SYSTEM, content=big_persona),
        Message(role=MessageRole.USER, content="hello"),
    ]
    authority = _authority(
        state_db,
        context,
        start_input={"context_metadata": {"context_window": 200000}},
    )
    snapshot = await authority.prepare_snapshot(_request(1, context.revision))
    # A 3k-CJK persona fits comfortably once the real window is honoured.
    assert big_persona in "".join(str(m.content) for m in snapshot.messages)


# --- S5b Task 1：route ≠ ROUTED_TASK 时 snapshot tools 不含 PROJECT_EFFECT 工具 -----------


class _PolicyExposure(_FakeExposure):
    def __init__(self, specs, project_effect: frozenset[str]) -> None:
        super().__init__(specs)
        self._project_effect = project_effect

    def execution_policy(self, run_id: RunId, provider_name: str) -> ToolExecutionPolicy:
        del run_id
        if provider_name in self._project_effect:
            return ToolExecutionPolicy(
                f"builtin:{provider_name}", "d" * 64, ToolEffectClass.PROJECT_EFFECT,
                ToolRouteRequirement.REQUIRED, ToolTaskScopeRequirement.REQUIRED,
            )
        return ToolExecutionPolicy(
            f"builtin:{provider_name}", "e" * 64, ToolEffectClass.NON_PROJECT_EFFECT,
            ToolRouteRequirement.OPTIONAL, ToolTaskScopeRequirement.OPTIONAL,
        )


def _policy_authority(state_db: Path, context: _FakeContext, exposure) -> ProductRunContextAuthority:
    ports = _FakePorts(context, _FakeCheckpoint(None))
    return ProductRunContextAuthority(
        ports_resolver=lambda: ports,
        exposure_resolver=lambda run_id: exposure,
        ledger=ContextRouteLedgerStore(state_db),
    )


@pytest.mark.asyncio
async def test_snapshot_hides_project_effect_tools_until_routed_task(state_db: Path) -> None:
    specs = (
        ProviderToolSpec("context_route", "Route", {"type": "object", "properties": {}}),
        ProviderToolSpec("read_file", "Read", {"type": "object", "properties": {}}),
        ProviderToolSpec("write_file", "Write", {"type": "object", "properties": {}}),
    )
    exposure = _PolicyExposure(specs, frozenset({"write_file"}))
    context = _FakeContext()
    authority = _policy_authority(state_db, context, exposure)

    # UNROUTED：写工具隐藏；fingerprint 按实际暴露集自洽。
    unrouted = await authority.prepare_snapshot(_request(1, 1))
    assert [spec.name for spec in unrouted.tools] == ["context_route", "read_file"]
    probe = ProviderRequest(RequestId("hash-only"), unrouted.messages, tools=unrouted.tools)
    assert unrouted.payload_hash == provider_request_fingerprint(probe)

    # routed_standalone：仍隐藏。
    standalone = ContextRouteReceipt(
        receipt_id="receipt-standalone", run_id=RUN.value, raw_call_id="raw-1",
        effect_id="effect-1", route=TaskScopeRoute.DIRECT_STANDALONE,
        task_scope_id=None, binding_set_revision=None,
    )
    routed_standalone = await authority.prepare_snapshot(
        _request(2, 1, route_receipt=standalone)
    )
    assert [spec.name for spec in routed_standalone.tools] == ["context_route", "read_file"]

    # ROUTED_TASK：写工具可见。
    task = ContextRouteReceipt(
        receipt_id="receipt-task", run_id=RUN.value, raw_call_id="raw-2",
        effect_id="effect-2", route=TaskScopeRoute.RESUME_EXISTING,
        task_scope_id="scope-1", binding_set_revision=1,
        binding_set_receipt_id="bind-1", binding_set_receipt_hash="d" * 64,
    )
    routed_task = await authority.prepare_snapshot(_request(3, 1, route_receipt=task))
    assert [spec.name for spec in routed_task.tools] == [
        "context_route", "read_file", "write_file"
    ]

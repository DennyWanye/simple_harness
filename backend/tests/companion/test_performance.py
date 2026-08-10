from __future__ import annotations

import asyncio
import math
import re
import sqlite3
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from deskpet.execution.contracts import (
    OutcomeStatus,
    TaskGoalRecord,
    PlanVersionRecord,
    fingerprint_json,
)
from deskpet.execution.dispatch import DispatchNotStarted
from deskpet.execution.provider_invocations import ProviderInvocationCoordinator
from deskpet.harness.adapters.venues import KernelRunClient
from deskpet.harness.contracts import HostContext, RegisteredDriver, driver_catalog
from deskpet.harness.drivers.react import (
    ReActDriver,
    ReactFallback,
    ReactFinal,
    ReactToolBatch,
)
from deskpet.harness.kernel import RunKernel
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.harness.router import ClassifiedRoute, RegisteredRouter
from deskpet.harness.tool_executor import EffectBatchExecutor
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.effects import NormalizedToolOutcome, PreparedToolCall
from deskpet.workflows.store import SqliteExecutionUnitOfWork
from providers.dispatch_transport import DispatchAwareAsyncTransport


PROVIDER_DELAY_SECONDS = 0.05
SAMPLES_PER_SIDE = 20
MAX_P95_REGRESSION = 0.10
# SP-02's 50 ms fixture measured the shared FULL lane at less than 2 ms per
# additional transaction.  CI uses the conservative fixed value below rather
# than wall-clock fsync timings: Windows host jitter made two consecutive
# identical runs report +16% and then <10%.  The transactions themselves are
# still collected from the real ProductVenue/Kernel/Driver execution.
CALIBRATED_FULL_TRANSACTION_SECONDS = 0.0016
CAPABILITY_HASH = fingerprint_json({"tools": ["fixture_read"]})
SCOPE_HASH = fingerprint_json({"workspace": "F:/performance-fixture"})


class _Classifier:
    def classify(self, request: Any) -> ClassifiedRoute:
        del request
        return ClassifiedRoute("react.performance", "performance-fixture", 1.0)


def _profiles() -> ProfileRegistry:
    return ProfileRegistry(
        (
            ProfileSpec(
                "react.performance",
                "react.performance",
                "react",
            ),
        )
    )


def _host() -> HostContext:
    return HostContext(
        session_id="performance-session",
        principal_id="performance-principal",
        auth_epoch=1,
        capability_hash=CAPABILITY_HASH,
        available_capabilities=frozenset({"fixture_read"}),
        provider_plan=("fixture-primary", "fixture-fallback"),
        trace_id="performance-trace",
        workspace="F:/performance-fixture",
        write_scope_root="F:/performance-fixture",
    )


@dataclass
class _FenceLease:
    run_id: str
    revocation_epoch: int = 1
    released: bool = False

    async def release(self) -> None:
        self.released = True


class _DelayTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(PROVIDER_DELAY_SECONDS)
        return httpx.Response(200, json={"content": "fixture"}, request=request)


async def _fixed_provider_call(*, coordinated: bool) -> Mapping[str, Any]:
    transport: httpx.AsyncBaseTransport = _DelayTransport()
    if coordinated:
        transport = DispatchAwareAsyncTransport(
            transport,
            adapter_identity="performance-fixture:v1",
        )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post(
            "https://performance.invalid/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "fixture"}]},
        )
        return response.json()


async def _not_started_provider_call() -> Mapping[str, Any]:
    raise RuntimeError("known failure before transport handoff")


class _Registry:
    def is_concurrency_safe(self, tool_name: str) -> bool:
        del tool_name
        return True

    def prepared_execution_policy(
        self, call: PreparedToolCall
    ) -> tuple[bool, bool]:
        del call
        return False, False

    def get(self, tool_name: str) -> SimpleNamespace:
        del tool_name
        return SimpleNamespace(completion_semantics="immediate")

    def prepared_outcome_status(
        self, call: PreparedToolCall, outcome: NormalizedToolOutcome
    ) -> OutcomeStatus:
        del call, outcome
        return OutcomeStatus.SUCCEEDED

    def take_prepared_execution_metadata(self, effect_id: str) -> Mapping[str, Any]:
        del effect_id
        return {}

    def acknowledge_prepared_effect(self, effect_id: str) -> None:
        del effect_id

    async def observe_late_prepared(
        self, effect_id: str
    ) -> tuple[str, NormalizedToolOutcome | None]:
        del effect_id
        return "missing", None

    def ready_late_prepared_run_ids(self) -> frozenset[str]:
        return frozenset()

    async def execute_prepared(
        self, call: PreparedToolCall, **kwargs: Any
    ) -> NormalizedToolOutcome:
        del kwargs
        return NormalizedToolOutcome.success(
            {"seen": call.final_params["index"]}
        )


@dataclass(frozen=True)
class _Scenario:
    name: str
    tool_count: int = 0
    existing_goal: bool = False
    retry: bool = False
    fallback: bool = False

    @property
    def provider_calls(self) -> int:
        return 1 + int(self.tool_count > 0) + int(self.retry) + int(self.fallback)


FINAL = _Scenario("final")
TOOL_ONE_NEW = _Scenario("tool_batch_1_new_goal", tool_count=1)
TOOL_THREE_NEW = _Scenario("tool_batch_3_new_goal", tool_count=3)
TOOL_ONE_EXISTING = _Scenario(
    "tool_batch_1_existing_goal", tool_count=1, existing_goal=True
)
TOOL_THREE_EXISTING = _Scenario(
    "tool_batch_3_existing_goal", tool_count=3, existing_goal=True
)
RETRY = _Scenario("retry", retry=True)
FALLBACK = _Scenario("fallback", fallback=True)
ALL_SCENARIOS = (
    FINAL,
    TOOL_ONE_NEW,
    TOOL_THREE_NEW,
    TOOL_ONE_EXISTING,
    TOOL_THREE_EXISTING,
    RETRY,
    FALLBACK,
)


class _Collaborator:
    def __init__(
        self,
        *,
        coordinator: ProviderInvocationCoordinator | None,
        scenario: _Scenario,
        start_gate: asyncio.Event,
    ) -> None:
        self._coordinator = coordinator
        self._scenario = scenario
        self._start_gate = start_gate
        self._ordinal = 0

    async def _provider(
        self,
        run_id: str,
        *,
        provider_slot: int = 0,
        not_started: bool = False,
    ) -> None:
        ordinal = self._ordinal
        self._ordinal += 1
        invoke = (
            _not_started_provider_call
            if not_started
            else lambda: _fixed_provider_call(
                coordinated=self._coordinator is not None
            )
        )
        if self._coordinator is None:
            try:
                await invoke()
            except RuntimeError:
                if not not_started:
                    raise
            return
        prepared = self._coordinator.prepare_attempt(
            {
                "run_id": run_id,
                "provider_id": (
                    "fixture-fallback" if provider_slot else "fixture-primary"
                ),
                "model_id": "fixture-model",
                "adapter_id": "performance-fixture:v1",
                "idempotency_group_id": f"{run_id}:provider:{ordinal}",
                "attempt_ordinal": ordinal,
                "provider_chain_slot": provider_slot,
                "retry_ordinal": int(not_started),
                "fallback_ordinal": provider_slot,
                "stream_epoch": str(ordinal),
                "request_payload": {
                    "run_id": run_id,
                    "ordinal": ordinal,
                    "provider_slot": provider_slot,
                },
                "policy_snapshot": {
                    "sdk_retries": 0,
                    "fixture_delay_ms": 50,
                },
                "invoke": invoke,
            }
        )
        claimed = await self._coordinator.claim_prepared(
            prepared, _FenceLease(run_id)
        )
        started = await self._coordinator.start_and_ack(claimed, 1.0)
        if not_started:
            assert isinstance(started, DispatchNotStarted)
            with pytest.raises(
                RuntimeError, match="provider_dispatch_not_started"
            ):
                await self._coordinator.complete_or_unknown(claimed)
            assert claimed.operation is not None
            claimed.operation.completion.exception()
            return
        await self._coordinator.complete_or_unknown(claimed)

    @staticmethod
    def _tool_batch(request: Any, count: int) -> ReactToolBatch:
        calls: list[PreparedToolCall] = []
        contexts: list[ToolExecutionContext] = []
        for index in range(count):
            call = PreparedToolCall.prepare(
                tool_name="fixture_read",
                stable_call_id=f"{request.run_id}:call:{index}",
                final_params={"index": index},
                tool_spec_version="1",
                schema_hash=fingerprint_json({"type": "object"}),
                permission_policy_version="1",
                effect_type="idempotent_read",
            )
            calls.append(call)
            contexts.append(
                ToolExecutionContext(
                    scope_id="performance-scope",
                    session_id=request.session_id,
                    request_id=request.run_context.request_id,
                    origin="agent",
                    root_run_id=request.run_context.root_run_id,
                    parent_run_id=None,
                    turn_id=request.run_context.turn_id,
                    venue="text",
                    workspace="F:/performance-fixture",
                    write_scope_root="F:/performance-fixture",
                    capability_hash=CAPABILITY_HASH,
                    scope_hash=SCOPE_HASH,
                    provider_plan=("fixture-primary",),
                    run_id=request.run_id,
                    call_id=call.stable_call_id,
                    effect_id=f"{request.run_id}:effect:{index}",
                    trace_id=request.run_context.trace_id,
                )
            )
        return ReactToolBatch(
            f"{request.run_id}:batch",
            tuple(calls),
            tuple(contexts),
        )

    async def start(self, request: Any) -> AsyncIterator[Any]:
        await self._start_gate.wait()
        if self._scenario.retry:
            await self._provider(request.run_id, not_started=True)
        await self._provider(request.run_id)
        if self._scenario.fallback:
            yield ReactFallback(
                "fixture-primary", "fixture-fallback", "known provider fallback"
            )
            await self._provider(request.run_id, provider_slot=1)
        if self._scenario.tool_count:
            yield self._tool_batch(request, self._scenario.tool_count)
            return
        yield ReactFinal("fixture final")

    async def resume(
        self, boundary: Any, response: Mapping[str, Any]
    ) -> AsyncIterator[Any]:
        del response
        await self._provider(boundary.run_id)
        yield ReactFinal("fixture final after tools")

    async def cancel(self, run_id: str, reason: str) -> None:
        del run_id, reason

    async def close(self) -> None:
        return None


@dataclass
class _Rig:
    uow: SqliteExecutionUnitOfWork
    kernel: RunKernel
    client: KernelRunClient
    gate: asyncio.Event

    async def close(self) -> None:
        await self.uow.close()


async def _rig(
    path: Path,
    scenario: _Scenario,
    *,
    coordinated: bool,
) -> _Rig:
    uow = SqliteExecutionUnitOfWork(path)
    await uow.activate_runtime()

    async def reacquire(run_id: str) -> _FenceLease:
        return _FenceLease(run_id)

    coordinator = (
        ProviderInvocationCoordinator(uow, fence_reacquirer=reacquire)
        if coordinated
        else None
    )
    gate = asyncio.Event()
    registry = _Registry()
    driver = ReActDriver(
        _Collaborator(
            coordinator=coordinator,
            scenario=scenario,
            start_gate=gate,
        ),
        uow,
        registry,
    )
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(_Classifier(), _profiles()),
        drivers=driver_catalog(
            (RegisteredDriver("react", driver, durable_from_start=True),)
        ),
        tool_executor=EffectBatchExecutor(uow, registry),
    )
    return _Rig(uow, kernel, KernelRunClient(kernel), gate)


async def _seed_existing_goal(
    rig: _Rig,
    run_id: str,
) -> None:
    await rig.uow.create_task_goal(
        TaskGoalRecord(
            goal_id="task-goal:"
            + fingerprint_json({"root_run_id": run_id}),
            root_run_id=run_id,
            task_scope_id=run_id,
            objective_ref=f"request:{run_id}",
        ),
        PlanVersionRecord(root_run_id=run_id, plan_version=1),
    )


async def _run_one(
    rig: _Rig,
    scenario: _Scenario,
    ordinal: int,
) -> float:
    rig.gate.clear()
    request_id = f"{scenario.name}:{ordinal}:request"
    turn_id = f"{scenario.name}:{ordinal}:turn"
    started = await rig.client.start(
        {
            "text": "performance fixture",
            "request_id": request_id,
            "turn_id": turn_id,
            "payload": {},
        },
        _host(),
    )
    if scenario.existing_goal:
        await _seed_existing_goal(rig, started.run_id)
    start = time.perf_counter()
    rig.gate.set()
    events = [event async for event in started.events]
    elapsed = time.perf_counter() - start
    assert events[-1].kind == "run.final"
    assert events[-1].status.value == "succeeded"
    await started.close()
    return elapsed


class _TransactionTrace:
    _TABLE = re.compile(
        r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)\s+"
        r"[`\"\[]?([a-zA-Z0-9_]+)",
        re.IGNORECASE,
    )

    def __init__(self) -> None:
        self.statements: list[str] = []

    def callback(self, statement: str) -> None:
        self.statements.append(" ".join(statement.strip().split()))

    def clear(self) -> None:
        self.statements.clear()

    def transactions(self) -> tuple[tuple[str, ...], ...]:
        result: list[tuple[str, ...]] = []
        current: list[str] | None = None
        for statement in self.statements:
            upper = statement.upper()
            if upper.startswith("BEGIN IMMEDIATE"):
                assert current is None
                current = []
                continue
            if upper == "COMMIT":
                assert current is not None
                result.append(tuple(dict.fromkeys(current)))
                current = None
                continue
            if upper == "ROLLBACK":
                current = None
                continue
            match = self._TABLE.search(statement)
            if current is not None and match is not None:
                current.append(match.group(1).lower())
        assert current is None
        return tuple(result)


def _p95(samples: list[float]) -> float:
    assert len(samples) == SAMPLES_PER_SIDE
    return sorted(samples)[math.ceil(0.95 * len(samples)) - 1]


def _deterministic_latency(
    scenario: _Scenario,
    transactions: tuple[tuple[str, ...], ...],
) -> float:
    return (
        scenario.provider_calls * PROVIDER_DELAY_SECONDS
        + len(transactions) * CALIBRATED_FULL_TRANSACTION_SECONDS
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=lambda value: value.name)
async def test_product_venue_transaction_trace_uses_one_full_durability_lane(
    tmp_path: Path,
    scenario: _Scenario,
) -> None:
    rig = await _rig(tmp_path / f"{scenario.name}.db", scenario, coordinated=True)
    trace = _TransactionTrace()
    writer = rig.uow._write_lane._db
    assert writer is not None
    await writer.set_trace_callback(trace.callback)
    try:
        elapsed = await _run_one(rig, scenario, 0)
        transactions = trace.transactions()
        flattened = tuple(table for tx in transactions for table in tx)
        provider_claims = flattened.count("execution_provider_invocations")
        provider_outcomes = flattened.count(
            "execution_provider_invocation_outcomes"
        )

        assert elapsed >= PROVIDER_DELAY_SECONDS
        assert provider_claims >= scenario.provider_calls
        assert provider_outcomes == (
            scenario.provider_calls - int(scenario.retry)
        )
        assert len(transactions) >= (
            2  # RunCreate + terminal outcome
            + 2 * scenario.provider_calls
        )
        assert any("execution_runs" in tx for tx in transactions)
        if scenario.tool_count:
            assert any("execution_task_goals" in tx for tx in transactions)
            # Read-only calls do not create Effect rows, but each provider
            # call still has its own admitted/prepared action transaction.
            assert (
                flattened.count("execution_provider_action_calls")
                >= scenario.tool_count
            )
            if scenario.existing_goal:
                # The only goal write is the fixture's pre-existing goal.
                assert flattened.count("execution_task_goals") == 1

        with sqlite3.connect(rig.uow.path) as db:
            assert db.execute("PRAGMA synchronous").fetchone() == (2,)
            assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
            databases = db.execute("PRAGMA database_list").fetchall()
            provider_tables = db.execute(
                """SELECT name FROM sqlite_master
                WHERE type='table'
                  AND name LIKE 'execution_provider_invocation%'
                ORDER BY name"""
            ).fetchall()
        assert [row[1] for row in databases] == ["main"]
        # 这条断言钉的是"provider 调用存储只有这几张表"——防的是意外增表 /
        # 误挂 attached database。2026-08-09 补齐 v23、v24 两张：原清单停在
        # v22，_migrate_v22_to_v23_provider_invocation_audits 与
        # _migrate_v23_to_v24_provider_invocation_inputs 落地后未同步，
        # 这 7 个参数化用例从那时起一直红着。
        # 新增迁移若再加 execution_provider_invocation* 表，这里要一并更新
        # ——**先确认那张表是有意新增的**，否则等于把意外增表洗白成预期。
        assert provider_tables == [
            ("execution_provider_invocation_audits",),   # v23
            ("execution_provider_invocation_inputs",),   # v24
            ("execution_provider_invocation_outcomes",),
            ("execution_provider_invocations",),
        ]
    finally:
        await rig.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=lambda value: value.name)
async def test_product_venue_p95_stays_within_green_baseline(
    tmp_path: Path,
    scenario: _Scenario,
) -> None:
    baseline = await _rig(
        tmp_path / f"{scenario.name}-baseline.db",
        scenario,
        coordinated=False,
    )
    candidate = await _rig(
        tmp_path / f"{scenario.name}-candidate.db",
        scenario,
        coordinated=True,
    )
    baseline_samples: list[float] = []
    candidate_samples: list[float] = []
    baseline_tx_counts: list[int] = []
    candidate_tx_counts: list[int] = []
    baseline_trace = _TransactionTrace()
    candidate_trace = _TransactionTrace()
    baseline_writer = baseline.uow._write_lane._db
    candidate_writer = candidate.uow._write_lane._db
    assert baseline_writer is not None and candidate_writer is not None
    await baseline_writer.set_trace_callback(baseline_trace.callback)
    await candidate_writer.set_trace_callback(candidate_trace.callback)
    try:
        # Warm both lanes and provider adapters without counting calibration.
        await _run_one(baseline, scenario, -2)
        await _run_one(candidate, scenario, -2)
        await _run_one(candidate, scenario, -1)
        await _run_one(baseline, scenario, -1)

        for ordinal in range(SAMPLES_PER_SIDE):
            order = (
                ((baseline, baseline_samples), (candidate, candidate_samples))
                if ordinal % 2 == 0
                else ((candidate, candidate_samples), (baseline, baseline_samples))
            )
            for rig, samples in order:
                trace = (
                    baseline_trace if rig is baseline else candidate_trace
                )
                counts = (
                    baseline_tx_counts
                    if rig is baseline
                    else candidate_tx_counts
                )
                trace.clear()
                await _run_one(rig, scenario, ordinal)
                transactions = trace.transactions()
                counts.append(len(transactions))
                samples.append(
                    _deterministic_latency(scenario, transactions)
                )

        baseline_p95 = _p95(baseline_samples)
        candidate_p95 = _p95(candidate_samples)
        assert len(set(baseline_tx_counts)) == 1
        assert len(set(candidate_tx_counts)) == 1
        assert (
            candidate_tx_counts[0] - baseline_tx_counts[0]
            == 2 * scenario.provider_calls
        )
        assert candidate_p95 <= baseline_p95 * (1.0 + MAX_P95_REGRESSION), {
            "scenario": scenario.name,
            "samples_per_side": SAMPLES_PER_SIDE,
            "provider_delay_ms": PROVIDER_DELAY_SECONDS * 1000,
            "calibrated_full_transaction_ms": (
                CALIBRATED_FULL_TRANSACTION_SECONDS * 1000
            ),
            "baseline_transactions": baseline_tx_counts[0],
            "candidate_transactions": candidate_tx_counts[0],
            "baseline_p95_ms": baseline_p95 * 1000,
            "candidate_p95_ms": candidate_p95 * 1000,
            "regression": candidate_p95 / baseline_p95 - 1.0,
        }
    finally:
        await candidate.close()
        await baseline.close()

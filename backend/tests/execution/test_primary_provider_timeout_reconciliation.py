"""F06 (2026-09-07): provider transport timeout must not stall the foreground Run.

Real SDK ReAct/SQLite + Host foreground runtime; the only fake is the Provider,
which raises ``ProviderTimeoutError`` (the SDK settles the invocation UNKNOWN and
parks the Run in ``waiting`` behind a provider wait-blocker).  The Host answers
through ``ProviderReconciliationPort`` with a retry-once policy and the foreground
loop re-observes instead of exiting on ``BOUND_WAITING``.
"""
import asyncio
import logging
import sqlite3

import pytest
from simple_harness.providers import ProviderReconciliationObservation, ProviderReconciliationState
from simple_harness.providers.errors import ProviderTimeoutError

from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory, QueueTurnRequest
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.sdk_adapters.reconciliation import (
    ProductProviderReconciliationAdapter,
    ProductProviderRetryOnceReconciliation,
    ProviderUnknownRetryOncePolicy,
)
from deskpet.execution.primary_history import PrimaryHistoryStore
from tests.execution.test_primary_foreground_runtime import Provider, build, history_disclosure

SUBJECT = local_owner_auth().subject


class TimeoutProvider(Provider):
    def __init__(self, timeouts: int):
        super().__init__()
        self.timeouts = timeouts

    async def invoke(self, request, *, cancel):
        if len(self.requests) < self.timeouts:
            self.requests.append(request)
            raise ProviderTimeoutError()
        return await super().invoke(request, cancel=cancel)


class AuditSink:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def record(self, event, payload):
        self.events.append((event, dict(payload)))


def sdk_db(tmp_path):
    paths = sorted((tmp_path / "sdk").rglob("execution-*.sqlite3"))
    assert len(paths) == 1, paths
    return paths[0]


def rows(path, sql, *params):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, params).fetchall()]


def host_head(state):
    return rows(state, "SELECT current_state FROM foreground_run_heads")


def reconciliations(state):
    return [r["observed_state"] for r in rows(
        state, "SELECT observed_state FROM foreground_execution_reconciliations ORDER BY recorded_at, rowid")]


def sdk_run_events(db):
    """(run.* kinds in order, provider transition (handoff_attempt, error_code) in order)."""
    import json
    kinds, attempts = [], []
    for r in rows(db, "SELECT kind, payload_json FROM run_events ORDER BY rowid"):
        if r["kind"].startswith("run."):
            kinds.append(r["kind"])
        elif r["kind"] == "audit.transition.v1":
            payload = json.loads(r["payload_json"])
            if payload.get("kind") == "provider":
                attempts.append((int(payload["handoff_attempt"]), payload["details"].get("error_code")))
    return kinds, attempts


def subsequence(events, expected):
    it = iter(events)
    return all(any(e == want for e in it) for want in expected)


async def open_primary(state):
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    return service, (await service.open_primary())["primary_ref"]


@pytest.mark.asyncio
async def test_primary_provider_timeout_once_retries_same_request_and_completes(tmp_path, caplog):
    """T1: one timeout → retry-once (same request_id) → COMPLETED in one drive."""
    state = tmp_path / "state.db"
    service, primary = await open_primary(state)
    provider = TimeoutProvider(1)
    audit = AuditSink()
    caplog.set_level(logging.INFO, logger="simple_harness")
    runtime, stack, queue = await build(tmp_path, state, provider, audit_sink=audit)
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "one", "First actual user turn"))
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        assert runtime.last_error is None
        assert await queue.current_snapshot(SUBJECT) is None

        assert len(provider.requests) == 2
        assert provider.requests[0].request_id == provider.requests[1].request_id

        db = sdk_db(tmp_path)
        invocations = rows(db, "SELECT state, handoff_attempt, rehandoff_count FROM provider_invocations")
        assert invocations == [{"state": "succeeded", "handoff_attempt": 2, "rehandoff_count": 1}]
        resolutions = rows(db, "SELECT outcome, evidence_ref FROM reconciliation_resolutions")
        assert len(resolutions) == 1 and resolutions[0]["outcome"] == "confirmed_not_started"
        assert resolutions[0]["evidence_ref"].startswith("product-policy:provider-retry-once:")
        blockers = rows(db, "SELECT resolution_id, wake_consumed FROM run_wait_blockers")
        assert len(blockers) == 1 and blockers[0]["resolution_id"] is not None and blockers[0]["wake_consumed"] == 1
        assert rows(db, "SELECT state FROM runs") == [{"state": "completed"}]

        assert host_head(state) == [{"current_state": "COMPLETED"}]
        assert reconciliations(state) == ["BOUND_WAITING", "BOUND_TERMINAL"]
        transitions = rows(state, "SELECT from_state, to_state FROM foreground_run_transitions ORDER BY rowid")
        assert (transitions[-1]["from_state"], transitions[-1]["to_state"]) == ("RUNNING", "COMPLETED")

        history = await PrimaryHistoryStore(state, policy=runtime.history_policy).read(
            subject=SUBJECT, primary_ref=primary, disclosure_context=history_disclosure(), before_sequence=2)
        assert history[-1]["terminal_state"] == "COMPLETED"
        assert [m["content"] for m in history[-1]["messages"]] == ["First actual user turn", "Actual response 2"]

        sdk_events = [r.getMessage() for r in caplog.records if r.name.startswith("simple_harness")]
        host_events = [e for e, _ in audit.events]
        assert subsequence(sdk_events, ["reconcile.unknown_settled", "provider.invoked"])
        assert subsequence(host_events, ["foreground.runtime.bound", "foreground.runtime.provider_reconciled"])
        assert "foreground.runtime.provider_unknown_exhausted" not in host_events
        # SDK durable ledger: provider attempt transitions (claimed a0 → handed_off a1 →
        # unknown a1 → re-claimed a1 → handed_off a2 → succeeded a2) and the Run
        # transitions waiting → recovered (wait drained) → completed, in order.
        run_kinds, attempts = sdk_run_events(db)
        assert subsequence(run_kinds, ["run.activated", "run.waiting", "run.recovered", "run.completed"]), run_kinds
        assert attempts == [(0, None), (1, None), (1, "provider_error_after_handoff"), (1, None), (2, None), (2, None)]
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
async def test_primary_provider_timeout_recovers_after_restart(tmp_path):
    """T2: an unreconciled waiting Run is reconciled by the SDK startup step after restart."""
    state = tmp_path / "state.db"
    service, primary = await open_primary(state)
    provider = TimeoutProvider(1)

    # First incarnation: policy answers STILL_UNKNOWN (pre-fix behaviour) → Run parks.
    stuck = ProductProviderReconciliationAdapter(observer=lambda inv: ProviderReconciliationObservation(
        ProviderReconciliationState.STILL_UNKNOWN, f"test:stuck:{inv.invocation_id}"))
    runtime, stack, queue = await build(tmp_path, state, provider, provider_reconciliation=stuck)
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "one", "First actual user turn"))
        assert not await asyncio.wait_for(runtime._drive_once(), 15)
        assert runtime.last_error is None
        assert host_head(state) == [{"current_state": "RUNNING"}]
        db = sdk_db(tmp_path)
        assert rows(db, "SELECT state FROM runs") == [{"state": "waiting"}]
        assert rows(db, "SELECT outcome FROM reconciliation_resolutions") == []
        assert len(provider.requests) == 1
    finally:
        await runtime.close()
        await stack.close()

    # Second incarnation: production retry-once policy; SDK _start_once reconciles.
    audit = AuditSink()
    runtime, stack, queue = await build(tmp_path, state, provider, audit_sink=audit)
    try:
        resolutions = rows(db, "SELECT outcome FROM reconciliation_resolutions")
        assert resolutions == [{"outcome": "confirmed_not_started"}]
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        assert runtime.last_error is None
        observations = rows(state, "SELECT outcome FROM foreground_execution_start_observations ORDER BY rowid")
        assert {"outcome": "QUERY_FOUND"} in observations
        assert host_head(state) == [{"current_state": "COMPLETED"}]
        assert len(provider.requests) == 2
        assert provider.requests[0].request_id == provider.requests[1].request_id
        assert rows(db, "SELECT state, handoff_attempt, rehandoff_count FROM provider_invocations") == [
            {"state": "succeeded", "handoff_attempt": 2, "rehandoff_count": 1}]
        history = await PrimaryHistoryStore(state, policy=runtime.history_policy).read(
            subject=SUBJECT, primary_ref=primary, disclosure_context=history_disclosure(), before_sequence=2)
        assert history[-1]["terminal_state"] == "COMPLETED"
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
async def test_primary_provider_timeout_twice_settles_cancelled_not_stuck(tmp_path):
    """T3: second timeout on the re-handed-off request → Host cancels, never stuck RUNNING."""
    state = tmp_path / "state.db"
    service, primary = await open_primary(state)
    provider = TimeoutProvider(2)
    audit = AuditSink()
    policy = ProductProviderRetryOnceReconciliation()
    runtime, stack, queue = await build(tmp_path, state, provider, provider_reconciliation=policy, audit_sink=audit)
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "one", "First actual user turn"))
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        assert runtime.last_error is None
        assert len(provider.requests) == 2
        db = sdk_db(tmp_path)
        assert rows(db, "SELECT state, handoff_attempt, rehandoff_count FROM provider_invocations") == [
            {"state": "unknown", "handoff_attempt": 2, "rehandoff_count": 1}]
        assert rows(db, "SELECT state FROM runs") == [{"state": "cancelled"}]
        assert host_head(state) == [{"current_state": "CANCELLED"}]
        assert await queue.current_snapshot(SUBJECT) is None
        host_events = [e for e, _ in audit.events]
        assert subsequence(host_events, [
            "foreground.runtime.provider_reconciled", "foreground.runtime.provider_unknown_exhausted"])
        sdk_run_id = rows(db, "SELECT run_id FROM runs")[0]["run_id"]
        assert sdk_run_id in policy.exhausted_runs
        assert reconciliations(state) == ["BOUND_WAITING", "BOUND_TERMINAL"]
        run_kinds, attempts = sdk_run_events(db)
        assert subsequence(run_kinds, ["run.waiting", "run.recovered", "run.waiting", "run.cancel_requested", "run.cancelled"]), run_kinds
        assert [a for a, _ in attempts] == [0, 1, 1, 1, 2, 2] and attempts[-1][1] == "provider_error_after_handoff"
        # No third physical send: the SDK allows exactly one rehandoff.
        assert rows(db, "SELECT COUNT(*) AS n FROM reconciliation_resolutions") == [{"n": 1}]
    finally:
        await runtime.close()
        await stack.close()


def test_provider_unknown_retry_once_policy():
    """T4: the policy alone — first unknown re-authorizes once, second stays unknown."""
    from types import SimpleNamespace
    policy = ProviderUnknownRetryOncePolicy()
    first = SimpleNamespace(invocation_id="a" * 64, run_id=SimpleNamespace(value="run-1"), handoff_attempt=1, rehandoff_count=0)
    observed = policy(first)
    assert observed.state is ProviderReconciliationState.CONFIRMED_NOT_STARTED
    assert observed.evidence_ref == f"product-policy:provider-retry-once:{'a' * 64}:a1"
    assert policy.exhausted_runs == set()
    second = SimpleNamespace(invocation_id="a" * 64, run_id=SimpleNamespace(value="run-1"), handoff_attempt=2, rehandoff_count=1)
    observed = policy(second)
    assert observed.state is ProviderReconciliationState.STILL_UNKNOWN
    assert observed.evidence_ref.startswith("product-policy:provider-retry-exhausted:")
    assert policy.exhausted_runs == {"run-1"}
    # The adapter carries the same policy state and the default adapter stays fail-closed.
    adapter = ProductProviderRetryOnceReconciliation()
    assert asyncio.run(adapter.observe(first)).state is ProviderReconciliationState.CONFIRMED_NOT_STARTED
    assert asyncio.run(adapter.observe(second)).state is ProviderReconciliationState.STILL_UNKNOWN
    assert adapter.exhausted_runs == {"run-1"}
    assert asyncio.run(ProductProviderReconciliationAdapter().observe(first)).state is ProviderReconciliationState.STILL_UNKNOWN


def test_production_wiring_no_longer_uses_noop_reconciliation():
    """T4b: main.py wires the retry-once adapter and the runtime reconciliation step."""
    from pathlib import Path
    source = Path(__file__).resolve().parents[2].joinpath("main.py").read_text(encoding="utf-8")
    assert "_NoopReconciliation" not in source
    assert source.count("reconciliation=runtime_reconciliation,") == 2
    assert source.count("provider_reconciliation=provider_reconciliation,") == 2
    assert "provider_reconciliation=_ensure_provider_reconciliation()," in source


# ---------------------------------------------------------------------------
# T5（2026-09-08 独立审查必改项）：运行期 reconcile 不得误伤并发 Run 的在途调用。
#
# ``ProviderInvocationCoordinator.reconcile_incomplete`` 是全库扫描，并且对
# ``HANDED_OFF`` 记录**无条件**先 ``_settle_unknown``（发生在
# ``ProviderReconciliationPort.observe`` 之前）。F06 把它接到前台运行期后，并发 Run
# （main.py 并发会话 / delegate 子 Run 共享同一个 coordinator 与 uow）在飞行中的
# provider 调用会被判 UNKNOWN → 真实响应在 settle 时版本不匹配被丢弃 → 又被 retry-once
# 授权重发 → 重复物理发送 + 重复计费。
#
# 下面用真 SDK coordinator + 真 SDK 记录状态机（唯一的 fake 是账本存储与 Provider，
# 契约与 ``simple_harness.execution.dispatch.ProviderInvocationUnitOfWork`` 一致）。
# ---------------------------------------------------------------------------

from simple_harness.contracts import RequestId, RunId  # noqa: E402
from simple_harness.contracts.messages import Message, MessageRole  # noqa: E402
from simple_harness.execution.budget import (  # noqa: E402
    BudgetPolicy,
    BudgetSnapshot,
    FrozenPriceEstimator,
)
from simple_harness.execution.dispatch import (  # noqa: E402
    ProviderInvocationCoordinator,
    ProviderInvocationUnknownError,
)
from simple_harness.execution.provider_invocations import (  # noqa: E402
    ProviderInvocationState,
)
from simple_harness.execution.recovery import (  # noqa: E402
    ReconciliationResolution,
    RecoveryKind,
    ResolutionOutcome,
)
from simple_harness.execution.uow import ExecutionLease  # noqa: E402
from simple_harness.providers import (  # noqa: E402
    CancelToken,
    ProviderRequest,
    ProviderResponse,
    ProviderTarget,
    ProviderUsage,
)
from simple_harness.providers.errors import ProviderTransportError  # noqa: E402

from deskpet.sdk_adapters.reconciliation import (  # noqa: E402
    ProductRuntimeReconciliation,
    RunScopedProviderReconciliation,
    provider_invocations_in_flight,
)


class LedgerUnitOfWork:
    """``ProviderInvocationUnitOfWork`` 的内存实现；状态迁移全部走 SDK 的记录模型。"""

    def __init__(self) -> None:
        self.records: dict[str, object] = {}
        self.resolutions: dict[tuple[str, int], ReconciliationResolution] = {}

    # --- claim / handoff / settle -----------------------------------------
    def claim_provider_invocation(self, record, *, budget_policy, execution_lease, **_kw):
        existing = self.records.get(record.invocation_id)
        if existing is not None:
            return existing
        budget_policy.authorize(
            self.read_provider_budget(record.run_id),
            reservation_micros=record.budget_charge.amount_micros,
        )
        self.records[record.invocation_id] = record
        return record

    def read_provider_invocation(self, invocation_id):
        return self.records.get(invocation_id)

    def hand_off_provider_invocation(
        self, invocation_id, *, expected_version, handed_off_at, execution_lease, workflow_lease=None
    ):
        record = self.records[invocation_id].hand_off(
            at=handed_off_at, expected_version=expected_version
        )
        self.records[invocation_id] = record
        return record

    def settle_provider_invocation(self, record, *, expected_version, fault=None):
        current = self.records[record.invocation_id]
        if current.version != expected_version:
            raise ValueError("stale provider invocation version")
        self.records[record.invocation_id] = record
        return record

    def list_incomplete_provider_invocations(self):
        # 与 SQLite 口径一致：claimed / handed_off / unknown，按 claimed_at 排序。
        incomplete = [
            record
            for record in self.records.values()
            if record.state
            in {
                ProviderInvocationState.CLAIMED,
                ProviderInvocationState.HANDED_OFF,
                ProviderInvocationState.UNKNOWN,
            }
        ]
        return tuple(sorted(incomplete, key=lambda r: (r.claimed_at, r.invocation_id)))

    def read_provider_budget(self, run_id) -> BudgetSnapshot:
        return BudgetSnapshot(0, 0, False)

    # --- reconciliation ----------------------------------------------------
    def record_provider_reconciliation(
        self, record, *, outcome, response_json, usage_json, budget_charge, evidence_ref, now,
        fault=None,
    ):
        key = (record.invocation_id, record.handoff_attempt)
        self.resolutions[key] = ReconciliationResolution(
            resolution_id=f"resolution:{record.invocation_id}:a{record.handoff_attempt}",
            kind=RecoveryKind.PROVIDER,
            ledger_identity=record.invocation_id,
            handoff_attempt=record.handoff_attempt,
            outcome=outcome,
            outcome_hash=f"hash:{outcome.value}",
            evidence_ref=evidence_ref,
            payload=None,
        )
        return record

    def read_reconciliation_resolution(self, *, kind, ledger_identity, handoff_attempt):
        return self.resolutions.get((ledger_identity, handoff_attempt))

    def reauthorize_provider_not_started(self, record, *, resolution, execution_lease, now, **_kw):
        assert record.state is ProviderInvocationState.UNKNOWN
        assert record.rehandoff_count == 0, "SDK 只允许一次 rehandoff"
        from simple_harness.execution.provider_invocations import dataclass_replace

        reclaimed = dataclass_replace(
            record,
            state=ProviderInvocationState.CLAIMED,
            error_code=None,
            handed_off_at=None,
            settled_at=None,
            rehandoff_count=record.rehandoff_count + 1,
            version=record.version + 1,
        )
        self.records[record.invocation_id] = reclaimed
        return reclaimed


class ScriptedProvider:
    """物理发送计数 + 可控放行；``timeouts`` 次传输失败后返回正常响应。"""

    def __init__(self, *, timeouts: int = 0, gate: asyncio.Event | None = None) -> None:
        self.calls: list[str] = []
        self.entered = asyncio.Event()
        self._timeouts = timeouts
        self._gate = gate
        self.target = ProviderTarget(
            provider_id="provider-1",
            model="model-1",
            pricing_key="model-1",
            endpoint_identity="https://provider.invalid/v1/chat/completions",
            adapter_key="scripted-provider.v1",
        )

    async def invoke(self, request, *, cancel):
        self.calls.append(request.request_id.value)
        self.entered.set()
        if len(self.calls) <= self._timeouts:
            raise ProviderTransportError()
        if self._gate is not None:
            await self._gate.wait()
        return ProviderResponse(
            request_id=request.request_id,
            message=Message(role=MessageRole.ASSISTANT, content="ok"),
            usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            model="model-1",
            finish_reason="stop",
        )


def _estimator() -> FrozenPriceEstimator:
    return FrozenPriceEstimator(
        snapshot_id="prices-1",
        pricing_key="model-1",
        input_micros_per_million_tokens=1_000_000,
        output_micros_per_million_tokens=1_000_000,
    )


def _request(request_id: str) -> ProviderRequest:
    return ProviderRequest(
        request_id=RequestId(request_id),
        messages=(Message(role=MessageRole.USER, content="hello"),),
        max_output_tokens=100,
    )


def _lease(run_id: str) -> ExecutionLease:
    return ExecutionLease(run_id, "runtime.kernel", "test-owner", 1, 100.0)


class _Bindings:
    """同一个 coordinator 服务多个 Run —— 生产上并发会话/delegate 子 Run 就是这样。"""

    def __init__(self, bindings):
        self._bindings = bindings

    def resolve(self, run_id):
        return self._bindings[run_id.value]


def _concurrent_stack(*, timeout_provider, live_provider):
    from simple_harness.execution.dispatch import ProviderBinding

    uow = LedgerUnitOfWork()
    policy = BudgetPolicy(hard_cap_micros=10_000_000, refuse_on_unknown=True)
    coordinator = ProviderInvocationCoordinator(
        uow=uow,
        resolver=_Bindings({
            "run-timeout": ProviderBinding(timeout_provider, _estimator(), policy),
            "run-live": ProviderBinding(live_provider, _estimator(), policy),
        }),
    )
    ports = SimpleNamespaceLike(provider=coordinator, react_checkpoint=uow)
    return uow, coordinator, ports


class SimpleNamespaceLike:
    def __init__(self, **values):
        self.__dict__.update(values)


@pytest.mark.asyncio
async def test_runtime_reconcile_keeps_other_runs_inflight_response(tmp_path):
    """T5: Run B 在途时 Run A 触发 waiting reconcile —— B 的响应不被丢弃、只发 1 次。"""
    gate = asyncio.Event()
    timeout_provider = ScriptedProvider(timeouts=1)
    live_provider = ScriptedProvider(gate=gate)
    uow, coordinator, ports = _concurrent_stack(
        timeout_provider=timeout_provider, live_provider=live_provider
    )
    policy = ProductProviderRetryOnceReconciliation()
    runtime_reconciliation = ProductRuntimeReconciliation(lambda: ports, policy)

    # Run A：provider 传输超时 → SDK 自己 settle UNKNOWN，Run 进 waiting。
    with pytest.raises(ProviderInvocationUnknownError):
        await coordinator.invoke(
            RunId("run-timeout"), _request("request-a"),
            cancel=CancelToken(), execution_lease=_lease("run-timeout"),
        )
    assert len(timeout_provider.calls) == 1

    # Run B：同一个 coordinator 上的并发 Run，物理请求正在飞行（HANDED_OFF）。
    live = asyncio.create_task(coordinator.invoke(
        RunId("run-live"), _request("request-b"),
        cancel=CancelToken(), execution_lease=_lease("run-live"),
    ))
    await asyncio.wait_for(live_provider.entered.wait(), 5)
    inflight = provider_invocations_in_flight(uow)
    assert len(inflight) == 1, "Run B 应有且只有一条在途 handoff"

    # 在途闸门：Run A 的运行期 reconcile 此刻必须整个跳过，一行账本都不能动。
    assert await runtime_reconciliation.reconcile_for_run("run-timeout") == 0
    assert runtime_reconciliation.inflight_skips == 1
    assert runtime_reconciliation.last_inflight == inflight
    live_record = next(r for r in uow.records.values() if r.run_id.value == "run-live")
    assert live_record.state is ProviderInvocationState.HANDED_OFF
    assert uow.resolutions == {}

    # B 正常返回：响应没被丢弃，物理发送恰好 1 次，账本 succeeded/attempt=1/rehandoff=0。
    gate.set()
    response = await asyncio.wait_for(live, 5)
    assert response.request_id.value == "request-b"
    assert live_provider.calls == ["request-b"]
    live_record = next(r for r in uow.records.values() if r.run_id.value == "run-live")
    assert (live_record.state, live_record.handoff_attempt, live_record.rehandoff_count) == (
        ProviderInvocationState.SUCCEEDED, 1, 0)
    assert policy.exhausted_runs == set()

    # B 落地后闸门放行：Run A 按 retry-once 收尾——同一 request 重发一次并成功。
    assert await runtime_reconciliation.reconcile_for_run("run-timeout") == 1
    resolutions = list(uow.resolutions.values())
    assert len(resolutions) == 1
    assert resolutions[0].outcome is ResolutionOutcome.CONFIRMED_NOT_STARTED
    assert resolutions[0].evidence_ref.startswith("product-policy:provider-retry-once:")
    assert resolutions[0].ledger_identity.startswith(
        next(r.invocation_id for r in uow.records.values() if r.run_id.value == "run-timeout")[:8])

    retried = await coordinator.invoke(
        RunId("run-timeout"), _request("request-a"),
        cancel=CancelToken(), execution_lease=_lease("run-timeout"),
    )
    assert retried.request_id.value == "request-a"
    assert timeout_provider.calls == ["request-a", "request-a"], "同一 request，恰好两次"
    timeout_record = next(r for r in uow.records.values() if r.run_id.value == "run-timeout")
    assert (timeout_record.state, timeout_record.handoff_attempt, timeout_record.rehandoff_count) == (
        ProviderInvocationState.SUCCEEDED, 2, 1)
    # B 全程未被触碰。
    assert live_provider.calls == ["request-b"]


@pytest.mark.asyncio
async def test_unscoped_reconcile_spares_inprocess_inflight_response(tmp_path):
    """T5b: 全量 reconcile（启动路径口径）也不再丢弃本进程在途调用的正常响应。

    原对照组（2026-09-08）证明"无闸门的全量 reconcile 会把在途 handoff 判 UNKNOWN、
    丢弃 B 的正常响应"。SDK 2026-09-23 起（Host 内嵌源码导入提交 0c3abfdb2）
    ``reconcile_incomplete`` 自己跳过 ``_active_provider_calls`` 中的本进程在途调用，
    该缺陷在 SDK 层已不可复现。这里改为锁住新行为：全量 reconcile 对在途调用
    一行账本都不动，B 的响应照常返回、物理只发一次。Host 的在途闸门
    （``reconcile_for_run``）仍保留，作为跨实现的第二道防线（见 T5）。
    """
    gate = asyncio.Event()
    live_provider = ScriptedProvider(gate=gate)
    uow, coordinator, ports = _concurrent_stack(
        timeout_provider=ScriptedProvider(), live_provider=live_provider
    )
    runtime_reconciliation = ProductRuntimeReconciliation(
        lambda: ports, ProductProviderRetryOnceReconciliation()
    )
    live = asyncio.create_task(coordinator.invoke(
        RunId("run-live"), _request("request-b"),
        cancel=CancelToken(), execution_lease=_lease("run-live"),
    ))
    await asyncio.wait_for(live_provider.entered.wait(), 5)
    assert len(provider_invocations_in_flight(uow)) == 1

    assert await runtime_reconciliation.reconcile() == 0
    assert uow.resolutions == {}
    live_record = next(r for r in uow.records.values() if r.run_id.value == "run-live")
    assert live_record.state is ProviderInvocationState.HANDED_OFF
    gate.set()
    response = await asyncio.wait_for(live, 5)
    assert response.request_id.value == "request-b"
    assert live_provider.calls == ["request-b"]
    live_record = next(r for r in uow.records.values() if r.run_id.value == "run-live")
    assert (live_record.state, live_record.handoff_attempt, live_record.rehandoff_count) == (
        ProviderInvocationState.SUCCEEDED, 1, 0)


@pytest.mark.asyncio
async def test_run_scoped_provider_reconciliation_never_authorizes_other_runs():
    """T5c: Run 作用域端口对非目标 Run 只返回 STILL_UNKNOWN（SDK 的"不裁决"结论）。"""
    from types import SimpleNamespace

    inner = ProviderUnknownRetryOncePolicy()
    scoped = RunScopedProviderReconciliation(inner, SimpleNamespace(value="run-a"))
    assert scoped.target_run_id == "run-a"
    other = SimpleNamespace(invocation_id="b" * 64, run_id=SimpleNamespace(value="run-b"),
                            handoff_attempt=1, rehandoff_count=0)
    observed = await scoped.observe(other)
    assert observed.state is ProviderReconciliationState.STILL_UNKNOWN
    assert observed.evidence_ref == f"product-scope:other-run:{'b' * 64}"
    assert inner.exhausted_runs == set(), "非目标 Run 不得污染 exhausted_runs"

    mine = SimpleNamespace(invocation_id="a" * 64, run_id=SimpleNamespace(value="run-a"),
                           handoff_attempt=1, rehandoff_count=0)
    assert (await scoped.observe(mine)).state is ProviderReconciliationState.CONFIRMED_NOT_STARTED


@pytest.mark.asyncio
async def test_reconcile_for_run_fails_closed_without_a_readable_ledger():
    """T5d: 拿不到 uow（证不出"无在途"）→ 不裁决，绝不调用 reconcile_incomplete。"""
    calls = []

    async def reconcile_incomplete(*, provider_reconciliation):
        calls.append(provider_reconciliation)
        return 3

    ports = SimpleNamespaceLike(provider=SimpleNamespaceLike(
        reconcile_incomplete=reconcile_incomplete), react_checkpoint=object())
    runtime_reconciliation = ProductRuntimeReconciliation(
        lambda: ports, ProductProviderRetryOnceReconciliation())
    assert await runtime_reconciliation.reconcile_for_run("run-a") == 0
    assert calls == [] and runtime_reconciliation.inflight_skips == 1
    # 启动路径不受闸门影响。
    assert await runtime_reconciliation.reconcile() == 3
    assert len(calls) == 1

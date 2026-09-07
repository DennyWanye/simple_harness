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

"""Real Host turns/outbox and public SDK indexing; deterministic transport only."""
import asyncio
import sqlite3
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory, QueueTurnRequest
from deskpet.memory.human_memory_v7 import project_recall_fragments
from deskpet.memory.memory_ingestion_outbox import MemoryAnalysisLane, MemoryIngestionOutboxWorker
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.memory.short_index_worker import PrimaryShortIndexWorker
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.execution.test_primary_foreground_runtime import Provider, build
from tests.memory.test_primary_short_ingestion import recall


@pytest_asyncio.fixture(scope="module")
async def seed(tmp_path_factory):
    path = tmp_path_factory.mktemp("worker-host-seed")
    state = path / "state.db"
    service = HumanMemoryHostServiceFactory(state,
        await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    # 生产启动（main.py）安装 v50–v54 扩展；worker 的 procedure 观察需要 procedure_uses 表。
    from deskpet.memory.procedure_recovery_schema import initialize_procedure_recovery_state_db
    await initialize_procedure_recovery_state_db(state)
    await service.open_primary()
    runtime, stack, queue = await build(path, state, Provider())
    try:
        for i in range(11):
            await service.enqueue_turn(QueueTurnRequest(None, f"worker-{i}",
                "quartznebula original" if i == 0 else f"topazmarker {i}"))
            assert await runtime._drive_once() and runtime.last_error is None
    finally:
        await runtime.close()
        await stack.close()
    return state


class IdleAnalysis:
    """Scheduling control only; no fabricated Memory materialization result."""
    def __init__(self):
        self.calls = 0
    async def run_once(self):
        from simple_harness_memory import WorkerRunOutcome
        self.calls += 1
        return WorkerRunOutcome.IDLE


@pytest_asyncio.fixture
async def env(tmp_path, seed):
    state = tmp_path / "state.db"
    with sqlite3.connect(seed) as src, sqlite3.connect(state) as dst:
        src.backup(dst)
    runtime = compose_human_memory_runtime(state, tmp_path / "memory.db",
        adapter_factory=lambda *_: pytest.fail("analysis transport not used by indexing"))
    worker = MemoryIngestionOutboxWorker(state, runtime.manager, owner_id="worker-test")
    lane = MemoryAnalysisLane(worker=worker, runtime=runtime, executor=runtime.analysis_authority,
        config=None, worker_id="test-lane", poll_seconds=.01)
    analysis = IdleAnalysis()
    lane._runner = analysis
    try:
        yield SimpleNamespace(state=state, runtime=runtime, worker=worker, lane=lane, analysis=analysis)
    finally:
        await lane.close()
        await runtime.close()


async def deliver_all(env, count=11):
    for _ in range(count):
        assert await env.worker.run_once() == "delivered"


@pytest.mark.asyncio
async def test_actual_lane_eleven_groups_and_public_selected_sources(env):
    assert isinstance(env.lane.short_indexer, PrimaryShortIndexWorker)
    for _ in range(11):
        await env.lane.tick()
        assert env.lane.last_short_error is None
    assert env.analysis.calls == 11
    manager = await env.runtime.manager()
    result = await recall(manager, "quartznebula")
    assert result.eligible_count == 1 and len(result.hits) == 1
    assert result.hits[0].content == "user: quartznebula original\nassistant: Actual response 1"
    assert (await recall(manager, "topazmarker")).eligible_count == 1
    lanes = await env.runtime.typed_recall(query="quartznebula", run_id="worker-public", turn_ordinal=1)
    fragments = project_recall_fragments(lanes)
    assert len(fragments) == 1 and len(fragments[0]["history_source_dependencies"]["evidence"]) == 2
    assert len(env.lane.short_indexer._confirmed) == 11
    # Real Host query plan, not a claim about the SDK global rebuild cost.
    with sqlite3.connect(env.state) as db:
        plan = db.execute("EXPLAIN QUERY PLAN SELECT turn_id FROM foreground_turns WHERE subject=? "
            "AND enqueue_sequence>? AND enqueue_sequence<=? ORDER BY enqueue_sequence LIMIT 16",
            (env.runtime.principal().actor_id, 0, 11)).fetchall()
    assert any("SEARCH" in row[3] and "INDEX" in row[3] for row in plan)


@pytest.mark.asyncio
async def test_low_sequence_late_delivery_and_fixed_upper_wrap(env):
    held = await env.worker.claim()
    assert held is not None
    await deliver_all(env, 10)
    short = PrimaryShortIndexWorker(env.runtime, page_size=3)
    first = await short.step()
    assert first.scanned == 3 and first.confirmed == 2
    assert first.blocked[0][1] == "conversation_user_ingestion_pending"
    assert short._upper == 11
    service = HumanMemoryHostServiceFactory(env.state,
        await dispatch_startup_epoch(env.state, approved_fresh_lane=True)).bind(local_owner_auth())
    from deskpet.memory.procedure_recovery_schema import initialize_procedure_recovery_state_db
    await initialize_procedure_recovery_state_db(env.state)  # 生产启动安装的 v50–v54 扩展
    await service.enqueue_turn(QueueTurnRequest(None, "arrived-during-scan", "new incomplete group"))
    for _ in range(3):
        last = await short.step()
    assert last.wrapped and last.scanned == 2  # new sequence12 excluded from this cycle
    assert len(short._confirmed) == 10
    late = MemoryIngestionOutboxWorker(env.state, env.runtime.manager, owner_id="reclaim-old",
        clock=lambda: time.time() + 120)
    assert await late.run_once() == "delivered"
    replay = await short.step()
    assert replay.confirmed == 1 and short._upper == 12
    assert len((await recall(await env.runtime.manager(), "quartznebula")).hits) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["short.after_ingest", "short.after_registration", "short.before_projection", "short.after_projection"])
async def test_unknown_ack_reopen_replays_real_refs(env, point):
    await deliver_all(env)
    fired = False
    def fail(current):
        nonlocal fired
        if current == point and not fired:
            fired = True
            raise RuntimeError("simulated lost acknowledgement")
    short = PrimaryShortIndexWorker(env.runtime, page_size=1, fault_hook=fail)
    try:
        await short.step()
    except RuntimeError:
        pass
    assert fired and not short._confirmed
    authority = env.runtime.conversation_evidence_authority
    first_id = (await authority.completed_run_ids())[0]
    refs = (await authority.registrations_for_run(first_id)).references
    await env.runtime.close()
    short._fault_hook = None
    result = await short.step()
    assert result.confirmed == 1
    assert (await authority.registrations_for_run(first_id)).references == refs
    assert len(short._confirmed) == 1 and short._after == 1


@pytest.mark.asyncio
async def test_bad_group_does_not_starve_and_cache_eviction(env, monkeypatch):
    await deliver_all(env)
    authority = env.runtime.conversation_evidence_authority
    first_id = (await authority.completed_run_ids())[0]
    original = authority.registrations_for_run
    async def broken(run_id):
        if run_id == first_id:
            raise RuntimeError("primary_message_source_missing")
        return await original(run_id)
    monkeypatch.setattr(authority, "registrations_for_run", broken)
    short = PrimaryShortIndexWorker(env.runtime, cache_limit=2)
    step = await short.step()
    assert step.scanned == 11 and step.confirmed == 10 and len(step.blocked) == 1
    assert len(short._confirmed) == 2
    assert (await short.step()).confirmed > 0  # eviction adds replay, never drops work
    assert len(short._confirmed) == 2


@pytest.mark.asyncio
async def test_short_failure_analysis_continues_cancel_and_single_task_close(env, monkeypatch):
    async def fail():
        raise RuntimeError("namespace unavailable")
    monkeypatch.setattr(env.lane.short_indexer, "step", fail)
    await env.lane.tick()
    assert env.analysis.calls == 1 and env.lane.last_short_error is not None
    entered = asyncio.Event()
    async def wait():
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(env.lane.short_indexer, "step", wait)
    env.lane.start()
    task = env.lane._task
    env.lane.start()
    assert env.lane._task is task
    await asyncio.wait_for(entered.wait(), 2)
    await env.lane.close(timeout_seconds=.01)
    assert task.done() and env.lane._task is None
    assert env.lane.short_indexer._manager is None


@pytest.mark.asyncio
async def test_actual_missing_child_group_rejected_next_group_processed(env):
    await deliver_all(env)
    authority = env.runtime.conversation_evidence_authority
    run_ids = await authority.completed_run_ids()
    broken = await authority.registrations_for_run(run_ids[0])
    child = broken.registrations[1].envelope.evidence_id
    # Damage only disposable Host fixture bytes, never SDK tables or production.
    with sqlite3.connect(env.state) as db:
        for name, in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name IN ('human_memory_evidence','human_memory_sanitization_receipts')").fetchall():
            db.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        db.execute("DELETE FROM human_memory_evidence WHERE evidence_id=?", (child,))
        db.execute("DELETE FROM human_memory_sanitization_receipts WHERE evidence_id=?", (child,))
    result = await env.lane.short_indexer.step()
    assert result.scanned == 11 and result.confirmed == 10
    assert len(result.blocked) == 1 and result.blocked[0][0] == run_ids[0]
    assert len(env.lane.short_indexer._confirmed) == 10


@pytest.mark.asyncio
async def test_analysis_failure_next_tick_and_periodic_projection(env, monkeypatch):
    async def failed_analysis():
        raise RuntimeError("analysis-local failure")
    monkeypatch.setattr(env.analysis, "run_once", failed_analysis)
    await env.lane.tick()
    assert env.lane.last_error is not None and env.lane.last_short_step.confirmed == 1
    await env.lane.tick()
    assert env.lane.last_short_step.confirmed == 1
    assert len(env.lane.short_indexer._confirmed) == 2
    await deliver_all(env, 9)
    clock = [0.0]
    short = PrimaryShortIndexWorker(env.runtime, monotonic=lambda: clock[0])
    first = await short.step()
    assert first.projection is not None
    assert (await short.step()).projection is None
    clock[0] = 61.0
    assert (await short.step()).projection is not None
    await env.runtime.close()
    assert (await short.step()).confirmed == 11  # fresh manager clears old confirmations


@pytest.mark.asyncio
async def test_real_tool_group_and_unfinished_do_not_starve_plain_group(tmp_path):
    from simple_harness import CallId
    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage
    class ToolFirst(Provider):
        async def invoke(self, request, *, cancel):
            if not self.requests:
                self.requests.append(request)
                return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Find tools"),
                    tool_calls=(ProviderToolCall(CallId("worker-search"), "tool_search", {}),),
                    model="model", usage=ProviderUsage(10, 10, 20), opaque_continuation_ref="fixture-tool")
            return await super().invoke(request, cancel=cancel)
    state = tmp_path / "state.db"
    service = HumanMemoryHostServiceFactory(state,
        await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    from deskpet.memory.procedure_recovery_schema import initialize_procedure_recovery_state_db
    await initialize_procedure_recovery_state_db(state)  # 生产启动安装的 v50–v54 扩展
    await service.open_primary()
    foreground, stack, _ = await build(tmp_path, state, ToolFirst())
    try:
        for i in range(2):
            await service.enqueue_turn(QueueTurnRequest(None, f"mixed-worker-{i}", f"actual message {i}"))
            assert await foreground._drive_once() and foreground.last_error is None
        await service.enqueue_turn(QueueTurnRequest(None, "unfinished-worker", "not run yet"))
    finally:
        await foreground.close()
        await stack.close()
    runtime = compose_human_memory_runtime(state, tmp_path / "memory.db", adapter_factory=lambda *_: None)
    worker = MemoryIngestionOutboxWorker(state, runtime.manager, owner_id="mixed-worker")
    try:
        assert await worker.run_once() == "delivered"
        assert await worker.run_once() == "delivered"
        result = await PrimaryShortIndexWorker(runtime).step()
        assert result.scanned == 3 and result.confirmed == 1
        assert [reason for _, reason in result.blocked] == [
            "terminal_multiple_items_not_representable", "conversation_terminal_pending"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_cancel_after_real_projection_ack_reopens_without_false_confirmation(env):
    await deliver_all(env)
    def cancel(point):
        if point == "short.after_projection":
            raise asyncio.CancelledError()
    short = PrimaryShortIndexWorker(env.runtime, fault_hook=cancel)
    with pytest.raises(asyncio.CancelledError):
        await short.step()
    assert not short._confirmed
    # The actual projection committed before cancellation; replay cannot assume
    # it rolled back and must use identical registrations after reopening.
    assert len((await recall(await env.runtime.manager(), "quartznebula")).hits) == 1
    await env.runtime.close()
    short._fault_hook = None
    assert (await short.step()).confirmed == 11
    assert len((await recall(await env.runtime.manager(), "quartznebula")).hits) == 1


# --------------------------------------------------------------------------
# 2026-09-08 HM-TO-A6：终态源 payload 超出 Memory 内联上限的组，此前每个轮询
# 周期都会被重新注册一次，每次先把同一条 USER 证据再摄入一遍——线上表现为
# 每分钟 28 条 memory.evidence_ingestion_replayed（native-a6-7cec5249）。
# --------------------------------------------------------------------------


def _pair(delivery_key, text):
    from deskpet.memory.human_memory_service import build_foreground_turn_evidence

    return build_foreground_turn_evidence(
        subject=local_owner_auth().subject,
        authority_ref="host:test-owner",
        delivery_key=delivery_key,
        text=text,
    )


def _stub_group(*, oversized):
    from deskpet.memory.primary_visibility import inline_evidence_limit

    limit = inline_evidence_limit()
    envelope, receipt = _pair("short-user", "actual user turn")
    terminal, terminal_receipt = _pair(
        "short-terminal", "y" * (limit + 4096) if oversized else "small terminal"
    )
    registration = SimpleNamespace(
        registration_id="registration-1",
        registration_hash="a" * 64,
        envelope=envelope,
        admission_receipt=receipt,
    )
    return SimpleNamespace(
        registrations=(registration,),
        terminal_source=(terminal, terminal_receipt),
        user_analysis_lineage=None,
        references=(),
    )


class _StubManager:
    def __init__(self):
        self.ingested = []
        self.admitted = []

    async def ingest_committed_evidence(self, envelope, receipt, *, analysis_lineage=None):
        self.ingested.append(envelope.evidence_id)

    async def admit_evidence_source(self, *, principal, envelope, receipt):
        self.admitted.append(envelope.evidence_id)

    async def register_conversation_evidence(self, reference):
        return reference

    async def rebuild_short_horizon_projection(self, *, principal):
        return SimpleNamespace(projected_chunk_count=0)

    async def rebuild_short_horizon_generation(self):
        return SimpleNamespace(activated=False)

    async def rebuild_cognitive_vector_generation(self):
        return SimpleNamespace(activated=False)


def _stub_runtime(group):
    principal = SimpleNamespace(actor_id=local_owner_auth().subject)
    manager = _StubManager()
    reads = []

    class Authority:
        subject = principal.actor_id

        async def page_turns(self, *, after, upper, limit):
            return 1, [(1, "run-1", "COMPLETED")]

        async def registrations_for_run(self, run_id):
            reads.append(run_id)
            return group

    runtime = SimpleNamespace(
        conversation_evidence_authority=Authority(),
        procedure_runtime=None,
        manager=lambda: _resolved(manager),
        principal=lambda: principal,
    )
    return runtime, manager, reads


async def _resolved(value):
    return value


@pytest.mark.asyncio
async def test_unadmissible_group_is_blocked_before_any_write():
    from deskpet.memory.conversation_registration import ConversationRegistrationUnavailable
    from deskpet.memory.short_indexing import SOURCE_UNADMISSIBLE, assert_group_admissible

    with pytest.raises(ConversationRegistrationUnavailable) as raised:
        assert_group_admissible(_stub_group(oversized=True))
    assert raised.value.code == SOURCE_UNADMISSIBLE
    assert_group_admissible(_stub_group(oversized=False))  # control


@pytest.mark.asyncio
async def test_unadmissible_group_never_writes_across_repeated_cycles(monkeypatch):
    """The storm stops at the pre-write assert, not at a negative cache.

    Task 6 review F-5/F-6: the earlier ``_rejected`` cache was keyed on the
    registrations alone, so a *corrected* terminal source stayed blocked
    forever, and the test that guarded it passed with the cache deleted. The
    cache is gone; what has to hold is that every cycle enters
    ``register_group``, is stopped by ``assert_group_admissible`` **before the
    first write**, and therefore performs zero ingests and zero admissions.
    """

    from deskpet.memory import short_indexing
    from deskpet.memory.short_indexing import (
        SOURCE_UNADMISSIBLE,
        PrimaryShortIndexingService,
    )

    entered, guarded = [], []
    real_register = PrimaryShortIndexingService.register_group
    real_assert = short_indexing.assert_group_admissible

    async def spy_register(self, group):
        entered.append(group)
        return await real_register(self, group)

    def spy_assert(group):
        guarded.append((group, len(entered)))
        return real_assert(group)

    monkeypatch.setattr(PrimaryShortIndexingService, "register_group", spy_register)
    monkeypatch.setattr(short_indexing, "assert_group_admissible", spy_assert)

    group = _stub_group(oversized=True)
    runtime, manager, reads = _stub_runtime(group)
    worker = PrimaryShortIndexWorker(runtime, page_size=1)
    for _ in range(4):
        step = await worker.step()
        assert step.blocked == (("run-1", SOURCE_UNADMISSIBLE),)
        assert step.confirmed == 0
        # No write ever happened — that replay was the observed storm.
        assert manager.ingested == [] and manager.admitted == []
    # The guard ran inside register_group on every cycle (its recorded
    # register_group entry count matches), before any write could happen.
    assert len(entered) == 4
    assert [count for _, count in guarded] == [1, 2, 3, 4]
    assert len(reads) == 4


@pytest.mark.asyncio
async def test_corrected_terminal_source_is_retried():
    """A group blocked only by its terminal source recovers once it is fixed."""
    from deskpet.memory.short_indexing import SOURCE_UNADMISSIBLE

    doomed = _stub_group(oversized=True)
    runtime, manager, _ = _stub_runtime(doomed)
    worker = PrimaryShortIndexWorker(runtime, page_size=1)
    assert (await worker.step()).blocked == (("run-1", SOURCE_UNADMISSIBLE),)

    # Same registrations (same identity key), admissible terminal source.
    fixed = _stub_group(oversized=False)
    runtime.conversation_evidence_authority.registrations_for_run = (
        lambda run_id: _resolved(fixed)
    )
    step = await worker.step()
    assert step.blocked == () and step.confirmed == 1
    assert len(manager.ingested) == 1 and len(manager.admitted) == 1


@pytest.mark.asyncio
async def test_admissible_group_still_registers():
    group = _stub_group(oversized=False)
    runtime, manager, _ = _stub_runtime(group)
    worker = PrimaryShortIndexWorker(runtime, page_size=1)
    step = await worker.step()
    assert step.blocked == () and step.confirmed == 1
    assert len(manager.ingested) == 1 and len(manager.admitted) == 1

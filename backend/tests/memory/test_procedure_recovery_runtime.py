"""New real-Host recovery controls; no Provider/model/network or private SDK seed."""
import asyncio
import json
import sqlite3
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import simple_harness as h
from simple_harness_memory import MemoryManager
from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory, QueueTurnRequest, CreateTaskScopeRequest
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.memory.procedure_recovery_schema import initialize_procedure_recovery_state_db
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
from deskpet.memory.short_indexing import PrimaryShortIndexingService
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.sdk_adapters.procedure_use import procedure_use_registration
from tests.execution.test_primary_foreground_runtime import build
from tests.sdk_adapters.s5b_effect_gate_harness import bind_scope_root
from tests.memory.test_procedure_scope_runtime import UseProvider, create_draft


@asynccontextmanager
async def session(tmp_path, provider=None, *, risk_level="low", with_discovery=False):
    state = tmp_path / "state.db"
    service = HumanMemoryHostServiceFactory(state,
        await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    await service.open_primary()
    await initialize_procedure_recovery_state_db(state)
    offset = [0.0]
    clock = lambda: time.time() + offset[0]
    async def builder(path, **kwargs):
        return await MemoryManager.build_human_memory_v7(path, **kwargs, allow_development_embedder=True)
    def compose():
        return compose_human_memory_runtime(state, tmp_path / "memory.db", adapter_factory=lambda _: None,
            backend_factory=builder, clock=clock)
    memory = compose()
    runtime = stack = None
    ctx = SimpleNamespace(state=state, service=service, memory=memory, compose=compose, offset=offset,
        provider=provider or UseProvider(), seen=set(), root=tmp_path / "workspace")
    ctx.root.mkdir()
    try:
        ctx.memory_id, ctx.revision = await create_draft(memory, state, risk_level=risk_level)
        extra = ()
        if with_discovery:
            from deskpet.sdk_adapters.procedure_discovery import procedure_discovery_registration
            extra = (procedure_discovery_registration(memory.procedure_runtime),)
        runtime, stack, _ = await build(tmp_path, state, ctx.provider, dynamic=True,
            visibility_memory=memory, procedure_runtime=memory.procedure_runtime,
            extra_registrations=(procedure_use_registration(memory.procedure_runtime), *extra))
        ctx.runtime, ctx.stack = runtime, stack
        yield ctx
    finally:
        if runtime is not None:
            await runtime.close()
        if stack is not None:
            await stack.close()
        await ctx.memory.close()


async def execute(ctx, index, *, scope_id=None, revision=None):
    if scope_id is None:
        scope = await ctx.service.create_task_scope(CreateTaskScopeRequest(
            f"recovery-scope-{index}", "写记录和备份", "Write both files", f"recovery-create-{index}"))
        scope_id = scope["scope_ref"]
        await bind_scope_root(ctx.state, scope_id, ctx.root, tag=f"recovery-root-{index}")
    ctx.provider.configure(scope_id, ctx.memory_id, revision or ctx.revision, index)
    await ctx.service.enqueue_turn(QueueTurnRequest(None, f"recovery-turn-{index}", "执行记录和备份两步。"))
    await ctx.runtime.after_enqueue(subject=local_owner_auth().subject)
    await asyncio.wait_for(ctx.runtime.drain(), 30)
    assert ctx.runtime.last_error is None
    assert (ctx.root / f"record-{index}.txt").read_text() == "actual record"
    assert (ctx.root / f"backup-{index}.txt").read_text() == "actual record"
    manager = await ctx.memory.manager()
    assert await MemoryIngestionOutboxWorker(ctx.state, ctx.memory.manager, owner_id="recovery-outbox").run_once() == "delivered"
    authority = ctx.memory.conversation_evidence_authority
    ids = set(await authority.completed_run_ids()) - ctx.seen
    assert len(ids) == 1
    ctx.seen |= ids
    group = await authority.registrations_for_run(ids.pop())
    await PrimaryShortIndexingService(authority, manager=manager, principal=ctx.memory.principal()).register_group(group)
    return group, scope_id


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_ack", [False, True])
async def test_reopen_expired_unconsumed_renews_but_consumed_lost_ack_replays_original(tmp_path, monkeypatch, lost_ack):
    async with session(tmp_path) as ctx:
        group, _ = await execute(ctx, 1)
        store = ctx.memory.procedure_runtime.store
        use = await store.use_for_run(group.terminal_source[0].run_id)
        original = store.journal
        async def crash(use_id, phase, body=None):
            if phase == ("applied" if lost_ack else "prepared") and body is not None:
                if not lost_ack:
                    await original(use_id, phase, body)
                raise RuntimeError("fixture-stop-after-durable-boundary")
            return await original(use_id, phase, body)
        with monkeypatch.context() as patch:
            patch.setattr(store, "journal", crash)
            with pytest.raises(RuntimeError, match="fixture-stop-after-durable-boundary"):
                await ctx.memory.procedure_runtime.observe_group(group, await ctx.memory.manager())
        first = await store.prepared(use["use_id"])
        requests = len(ctx.provider.requests)
        await ctx.memory.close()
        ctx.offset[0] = 301.0
        ctx.memory = ctx.compose()
        await ctx.memory.procedure_runtime.observe_group(group, await ctx.memory.manager())
        store = ctx.memory.procedure_runtime.store
        result = await store.journal(use["use_id"], "applied")
        assert result["result"]["independent_successes"] == 1
        assert len(ctx.provider.requests) == requests
        assert (await store.journal(use["use_id"], "prepared")) == first
        with sqlite3.connect(ctx.state) as db:
            assert db.execute("SELECT count(*) FROM procedure_observation_attempts").fetchone()[0] == (0 if lost_ack else 1)
        first_ref = h.ProcedureObservationAuthorityRef.from_authority(h.ProcedureObservationAuthority.from_json(first["authority"]))
        assert (result["reference"] == first_ref.to_json()) is lost_ack


@pytest.mark.asyncio
async def test_two_completed_scopes_bound_to_old_revision_rebase_only_observation_history(tmp_path):
    async with session(tmp_path) as ctx:
        first, _ = await execute(ctx, 1)
        second, _ = await execute(ctx, 2)  # Both real uses finish before either observation.
        manager = await ctx.memory.manager()
        service = ctx.memory.procedure_runtime
        uses = [await service.store.use_for_run(g.terminal_source[0].run_id) for g in (first, second)]
        assert uses[0]["target_revision"] == uses[1]["target_revision"] == ctx.revision
        await service.observe_group(first, manager)
        await service.observe_group(second, manager)
        results = [await service.store.journal(use["use_id"], "applied") for use in uses]
        assert [r["result"]["independent_successes"] for r in results] == [1, 2]
        assert await service.store.use_for_run(second.terminal_source[0].run_id) == uses[1]
        prepared = await service.store.prepared(uses[1]["use_id"])
        assert prepared["authority"]["intent"]["target_revision"] == results[0]["result"]["committed_revision"]


@pytest.mark.asyncio
async def test_second_real_run_same_scope_is_durably_rejected_without_increment(tmp_path):
    async with session(tmp_path) as ctx:
        first, scope_id = await execute(ctx, 1)
        manager, service = await ctx.memory.manager(), ctx.memory.procedure_runtime
        await service.observe_group(first, manager)
        use = await service.store.use_for_run(first.terminal_source[0].run_id)
        result = await service.store.journal(use["use_id"], "applied")
        second, _ = await execute(ctx, 2, scope_id=scope_id, revision=result["result"]["committed_revision"])
        await service.observe_group(second, manager)
        second_use = await service.store.use_for_run(second.terminal_source[0].run_id)
        rejected = await service.store.journal(second_use["use_id"], "rejected")
        assert rejected["reason"] == "procedure_observation_source_already_counted"
        assert await service.store.prepared(second_use["use_id"]) is None
        await service.observe_group(second, manager)
        assert await service.store.journal(second_use["use_id"], "applied") is None


class DriftProvider(UseProvider):
    change = None
    changed = False
    async def invoke(self, request, *, cancel):
        if self.stage == 5:
            self.change()
            self.changed = True
        return await super().invoke(request, cancel=cancel)


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["tool", "workspace"])
async def test_actual_pre_use_drift_prevents_first_physical_file_and_step_reservation(tmp_path, drift):
    provider = DriftProvider()
    async with session(tmp_path, provider) as ctx:
        scope = await ctx.service.create_task_scope(CreateTaskScopeRequest("drift-scope", "写记录", "write", "drift-create"))
        await bind_scope_root(ctx.state, scope["scope_ref"], ctx.root, tag="drift-root")
        if drift == "tool":
            def change():
                ctx.memory.procedure_runtime.registry._execution_identities["write_file"] = "changed-handler-version"
        else:
            def change():
                ctx.root.rename(tmp_path / "retired-workspace")
                ctx.root.mkdir()
        provider.change = change
        provider.configure(scope["scope_ref"], ctx.memory_id, ctx.revision, 1)
        await ctx.service.enqueue_turn(QueueTurnRequest(None, "drift-turn", "执行记录和备份两步。"))
        await ctx.runtime.after_enqueue(subject=local_owner_auth().subject)
        await asyncio.wait_for(ctx.runtime.drain(), 30)
        assert provider.changed  # Physical current state was changed after real binding.
        with sqlite3.connect(ctx.state) as db:
            # The use really bound before the changed current fingerprint.
            assert db.execute("SELECT count(*) FROM procedure_uses").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM procedure_use_reservations").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM procedure_use_effects").fetchone()[0] == 0
        assert not list(tmp_path.rglob("record-1.txt")) and not list(tmp_path.rglob("backup-1.txt"))


@pytest.mark.asyncio
async def test_high_risk_three_actual_scopes_never_auto_activate(tmp_path):
    async with session(tmp_path, risk_level="high") as ctx:
        manager, service = await ctx.memory.manager(), ctx.memory.procedure_runtime
        for index in range(1, 4):
            group, _ = await execute(ctx, index)
            await service.observe_group(group, manager)
            use = await service.store.use_for_run(group.terminal_source[0].run_id)
            result = (await service.store.journal(use["use_id"], "applied"))["result"]
            assert result["independent_successes"] == index
            assert result["lifecycle_state"] == "draft"
            ctx.revision = result["committed_revision"]

"""One real same-Scope A-pending/B-consumed/A-recovery counterexample."""
import pytest
from tests.memory.test_procedure_recovery_runtime import session, execute


@pytest.mark.asyncio
async def test_pending_prepared_same_scope_loser_is_durably_rejected_without_consumption(tmp_path, monkeypatch):
    async with session(tmp_path) as ctx:
        group_a, scope_id = await execute(ctx, 1)
        runtime, manager = ctx.memory.procedure_runtime, await ctx.memory.manager()
        store = runtime.store
        use_a = await store.use_for_run(group_a.terminal_source[0].run_id)
        original_journal = store.journal
        async def crash_after_prepared(use_id, phase, body=None):
            value = await original_journal(use_id, phase, body)
            if use_id == use_a["use_id"] and phase == "prepared" and body is not None:
                raise RuntimeError("stop-after-A-prepared")
            return value
        with monkeypatch.context() as patch:
            patch.setattr(store, "journal", crash_after_prepared)
            with pytest.raises(RuntimeError, match="stop-after-A-prepared"):
                await runtime.observe_group(group_a, manager)
        prepared_a = await store.prepared(use_a["use_id"])
        assert prepared_a is not None
        assert await store.journal(use_a["use_id"], "applied") is None

        group_b, _ = await execute(ctx, 2, scope_id=scope_id)
        await runtime.observe_group(group_b, manager)
        use_b = await store.use_for_run(group_b.terminal_source[0].run_id)
        applied_b = await store.journal(use_b["use_id"], "applied")
        assert applied_b["result"]["independent_successes"] == 1

        calls = []
        invoke = runtime.operation_audit.invoke
        async def counted(*args, **kwargs):
            calls.append(args[1])
            return await invoke(*args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(runtime.operation_audit, "invoke", counted)
            await runtime.observe_group(group_a, manager)
            assert calls == ["record_procedure_observation", "read_procedure_use_target", "prepare_procedure_observation"]
            calls.clear()
            await runtime.observe_group(group_a, manager)
            assert calls == []

        rejected = await store.journal(use_a["use_id"], "rejected")
        assert rejected["reason"] == "procedure_observation_source_already_counted"
        assert await store.prepared(use_a["use_id"]) == prepared_a
        assert await store.journal(use_a["use_id"], "applied") is None
        assert await store.journal(use_b["use_id"], "applied") == applied_b
        current = await manager.read_procedure_use_target(principal=ctx.memory.principal(),
            scope=ctx.memory.scope(), memory_id=ctx.memory_id, revision=applied_b["result"]["committed_revision"])
        assert current.revision == applied_b["result"]["committed_revision"]
        async with store.connection() as db:
            async with db.execute("SELECT count(*) FROM procedure_observation_attempts WHERE use_id=?", (use_a["use_id"],)) as cursor:
                assert (await cursor.fetchone())[0] == 0

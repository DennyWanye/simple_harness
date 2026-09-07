"""Real signed configuration changes at FIFO and physical transport boundaries."""
import asyncio
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from deskpet.execution.primary_dependencies import check_runtime_dependencies
from deskpet.memory.control_binding import HumanMemoryControlBinding
from deskpet.sdk_adapters.provider import ProductProviderAdapter
from tests.companion.test_window_control_credentials import _ingress
from tests.memory.test_primary_control_binding import _bind
from tests.memory.test_trusted_disclosure import env, command, ok, selection, enqueue
from tests.execution.test_primary_foreground_runtime import Provider, build
from tests.sdk_adapters.test_product_host_ports import Registry


async def second_connection(env):
    root = env.path.parent / "second-control"
    root.mkdir()
    private, _, _, ingress = _ingress(root)
    binding = HumanMemoryControlBinding()
    challenge = await _bind(private, ingress, binding)
    auth = binding.authenticate(ingress, challenge)
    assert auth.subject == env.auth.subject
    assert challenge.connection_id != env.challenge.connection_id
    return SimpleNamespace(binding=binding, ingress=ingress, challenge=challenge,
                           auth=auth, factory=env.factory)


@pytest.mark.asyncio
async def test_stale_preclaim_settles_without_run_and_fifo_continues(env):
    a = ok(await enqueue(env, key="A"))
    first = ok(await command(env, "disclosure.current", {}))["configuration"]
    other = await second_connection(env)
    ok(await command(other, "disclosure.configure", selection(first["binding_ref"]), key="G2"))
    b = ok(await enqueue(env, key="B"))
    provider = Provider()
    runtime, stack, queue = await build(env.path.parent, env.path, provider)
    try:
        # Use the actual driver: errors are captured as last_error, not hidden
        # by swallowing the source exception in a test wrapper.
        await asyncio.wait_for(runtime._run_driver(), 15)
        with sqlite3.connect(env.path) as db:
            states = dict(db.execute("SELECT turn_id,current_state FROM foreground_turn_heads"))
            a_runs = db.execute("SELECT count(*) FROM foreground_runs WHERE turn_id=?", (a["turn_ref"],)).fetchone()[0]
        projected = await env.factory.bind(env.auth).queue_snapshot()
        assert {t["turn_ref"]: t["state"] for t in projected["turns"]} == {
            a["turn_ref"]: "REJECTED", b["turn_ref"]: "SETTLED"}, str(runtime.last_error)
        assert states[a["turn_ref"]] == "QUEUED"  # Original admission fact, disposition is separate.
        assert runtime.last_error is None and a_runs == 0
        assert len(provider.requests) == 1
        with sqlite3.connect(env.path) as db:
            row = db.execute("SELECT rejection_json FROM foreground_admission_rejections WHERE turn_id=?", (a["turn_ref"],)).fetchone()
            rejection = json.loads(row[0])
            assert rejection["reason"] == "host_disclosure_binding_stale"
            assert rejection["bound_token"]["binding_ref"] == first["binding_ref"]
            assert db.execute("SELECT count(*) FROM foreground_run_sdk_bindings").fetchone()[0] == 1
        # A restart reads the durable disposition, without retrying A or B.
        await runtime.close()
        await stack.close()
        runtime, stack, queue = await build(env.path.parent, env.path, provider)
        assert not await runtime._drive_once()
        assert len(provider.requests) == 1
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [True, False])
async def test_configuration_commit_during_checker_blocks_physical_send(env, change):
    ok(await enqueue(env))
    first = ok(await command(env, "disclosure.current", {}))["configuration"]
    other = await second_connection(env)
    entered, release = asyncio.Event(), asyncio.Event()
    sends, checked = [], []
    def physical(request):
        sends.append(request)
        raise RuntimeError("physical transport entered")
    client = httpx.AsyncClient(transport=httpx.MockTransport(physical))
    provider = ProductProviderAdapter(Registry("secret"), provider_id="relay", client=client,
                                     price_resolver=lambda *_: (1, 1, "price-v1"))
    runtime, stack, queue = await build(env.path.parent, env.path, provider)
    class SlowPolicy:
        async def check_dependencies(self, **kwargs):
            allowed = await runtime.history_policy.check_dependencies(**kwargs)
            assert allowed
            checked.append(kwargs["disclosure_context"])
            entered.set()
            await release.wait()
            return allowed
    async def guard(request):
        current = await queue.current_snapshot(env.auth.subject)
        await check_runtime_dependencies(db_path=env.path, stack=stack, sdk_run_id=current.sdk_run_id,
            request=request, policy_factory=lambda _: SlowPolicy())
    provider._pre_invoke_guard = guard
    task = asyncio.create_task(runtime._drive_once())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        if change:
            second = ok(await command(other, "disclosure.configure", selection(first["binding_ref"]), key="G2"))
            assert second["policy_generation"] == 2
        release.set()
        await asyncio.wait_for(task, 10)
        assert len(checked) == 1 and first["source_ref"] in checked[0].authority_ref
        assert len(sends) == (0 if change else 1), "G2 committed while checker held G1; physical send must be zero"
        if change:
            with sqlite3.connect(env.path) as db:
                assert db.execute("SELECT current_state FROM foreground_run_heads").fetchone()[0] == "FAILED"
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.close()
        await stack.close()
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["before_commit", "after_commit"])
async def test_rejection_commit_fault_and_replay(env, fault):
    from deskpet.execution.admission_rejection import reject_stale_candidate, read_admission_rejection_tx
    from deskpet.execution.foreground_queue import ForegroundQueueStore
    import aiosqlite
    ok(await enqueue(env))
    queue = ForegroundQueueStore(env.path)
    candidate = await queue.read_next_preparation_candidate(env.auth.subject)
    with pytest.raises(Exception, match="rejection_not_proven"):
        await reject_stale_candidate(queue, candidate)
    first = ok(await command(env, "disclosure.current", {}))["configuration"]
    second = ok(await command(env, "disclosure.configure", selection(first["binding_ref"]), key="G2"))
    def fail(point):
        if point == "admission_rejection." + fault:
            raise RuntimeError("injected commit boundary")
    queue._fault_hook = fail
    with pytest.raises(RuntimeError, match="injected commit"):
        await reject_stale_candidate(queue, candidate)
    with sqlite3.connect(env.path) as db:
        assert db.execute("SELECT count(*) FROM foreground_admission_rejections").fetchone()[0] == (fault == "after_commit")
        assert db.execute("SELECT count(*) FROM foreground_runs").fetchone()[0] == 0
    queue = ForegroundQueueStore(env.path)
    saved = await reject_stale_candidate(queue, candidate)
    ok(await command(env, "disclosure.configure", selection(second["binding_ref"]), key="G3"))
    assert await reject_stale_candidate(queue, candidate) == saved
    assert await queue.read_next_preparation_candidate(env.auth.subject) is None
    async with aiosqlite.connect(env.path) as db:
        db.row_factory = aiosqlite.Row
        assert await read_admission_rejection_tx(db, turn_id=candidate.turn_id, subject=env.auth.subject) == saved
        with pytest.raises(ValueError, match="corrupt"):
            await read_admission_rejection_tx(db, turn_id=candidate.turn_id, subject="foreign")
    with sqlite3.connect(env.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            db.execute("DELETE FROM foreground_admission_rejections")
        db.execute("DROP TRIGGER foreground_admission_rejections_no_update")
        db.execute("UPDATE foreground_admission_rejections SET rejection_hash=?", ("0" * 64,))
    with pytest.raises(ValueError, match="corrupt"):
        await queue.read_next_preparation_candidate(env.auth.subject)


@pytest.mark.asyncio
async def test_v48_upgrade_preserves_migration_markers_and_requires_rejection_schema(tmp_path):
    from deskpet.memory import migrator, schema
    path, directory = tmp_path / "old.db", tmp_path / "v48"
    directory.mkdir()
    for source in migrator.DEFAULT_MIGRATIONS_DIR.glob("*.sql"):
        if migrator.MIGRATION_STEPS.get(source.name, 0) <= 48:
            (directory / source.name).symlink_to(source)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(migrator, "DEFAULT_MIGRATIONS_DIR", directory)
        patch.setattr(migrator, "HUMAN_MEMORY_TARGET_SCHEMA_VERSION", 48)
        patch.setattr(schema, "HUMAN_MEMORY_TARGET_SCHEMA_VERSION", 48)
        await schema.initialize_human_memory_program_state_db(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 48
        before = db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    await schema.dispatch_startup_epoch(path, approved_fresh_lane=False)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 49
        assert db.execute("SELECT version FROM schema_migrations WHERE version<>? ORDER BY version",
                          (migrator.ADMISSION_REJECTIONS_MIGRATION,)).fetchall() == before
        assert db.execute("SELECT taxonomy FROM human_memory_recovery_table_registry WHERE table_name='foreground_admission_rejections'").fetchone() == ("A",)
        db.execute("DROP TRIGGER foreground_admission_rejections_no_update")
    with pytest.raises(schema.HumanMemoryProgramEpochError, match="marker_chain_invalid"):
        await schema.dispatch_startup_epoch(path, approved_fresh_lane=False)

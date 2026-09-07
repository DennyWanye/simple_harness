"""Production factory + real completed groups; no network or SDK private SQL."""
import sqlite3
import asyncio
from dataclasses import replace

import aiosqlite
import pytest
import pytest_asyncio
from deskpet.memory.human_memory_v7 import project_recall_fragments, local_memory_principal
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.memory.primary_visibility import PrimaryHistoryPolicy
from deskpet.memory.short_indexing import PrimaryShortIndexingService
from tests.memory.test_primary_short_ingestion import real_turns
from tests.memory.test_selected_short_sources import suppress
from tests.memory.test_selected_short_sources import seed as pair_seed
from tests.execution.test_primary_foreground_runtime import history_disclosure


@pytest_asyncio.fixture(scope="module")
async def seed(tmp_path_factory):
    path = tmp_path_factory.mktemp("runtime-short-eleven")
    state, _, authority, manager = await real_turns(path, 11)
    try:
        indexed = await PrimaryShortIndexingService(
            authority, manager=manager, principal=local_memory_principal(),
        ).reconcile()
        assert len(indexed.groups) == 11 and indexed.blocked == ()
        return state, path / "index.db", indexed.groups
    finally:
        await manager.close()


@pytest_asyncio.fixture
async def actual(tmp_path, seed):
    state, index, groups = seed
    for source, dest in ((state, tmp_path / "state.db"), (index, tmp_path / "index.db")):
        with sqlite3.connect(source) as src, sqlite3.connect(dest) as dst:
            src.backup(dst)
    def no_provider(*args, **kwargs):
        raise AssertionError("This leaf never invokes analysis/provider")
    runtime = compose_human_memory_runtime(
        tmp_path / "state.db", tmp_path / "index.db", adapter_factory=no_provider,
    )
    try:
        yield runtime, groups
    finally:
        await runtime.close()


async def call(runtime, ordinal=1, **kwargs):
    return await runtime.typed_recall(
        query="quartznebula", run_id="selected-runtime-real-consumer",
        turn_ordinal=ordinal, **kwargs,
    )


@pytest.mark.asyncio
async def test_factory_eleven_selected_sources_reopen_final_forget(actual, monkeypatch):
    runtime, groups = actual
    authority = runtime.conversation_evidence_authority
    assert runtime.build_kwargs()["conversation_evidence_authority"] is authority
    manager = await runtime.manager()
    from deskpet.memory.conversation_registration import registration_ref
    # SDK re-resolves the actual registration through this factory's same
    # authority, not the separate fixture manager used to populate the index.
    for registration in groups[0].registrations:
        await manager.register_conversation_evidence(registration_ref(registration))
    async def forbidden():
        pytest.fail("Recall cannot enumerate all indexed groups")
    monkeypatch.setattr(authority, "completed_run_ids", forbidden)
    lanes = await call(runtime)
    fragments = project_recall_fragments(lanes)
    assert len(fragments) == 1
    fragment = fragments[0]
    assert fragment["payload"] == "user: quartznebula early real preference\nassistant: Actual response 1"
    assert lanes.short_horizon.eligible_count == 1  # recent ten excluded
    proof = fragment["history_source_dependencies"]
    assert {(x["evidence_id"], x["envelope_hash"]) for x in proof["evidence"]} == {
        (r.envelope.evidence_id, r.envelope.envelope_hash) for r in groups[0].registrations
    }
    assert proof["short_horizon"] == [fragment["history_binding"]]
    await runtime.close()
    manager = await runtime.manager()
    await suppress(manager, authority, groups[-1].registrations[0].envelope.evidence_id)
    reopened = project_recall_fragments(await call(runtime, 4))
    assert len(reopened) == 1 and reopened[0]["history_source_dependencies"]["evidence"] == proof["evidence"]
    async def check(*, subject, **kwargs):
        assert subject == authority.subject
        return await manager.check_history_visibility(principal=runtime.principal(), **kwargs)
    policy = PrimaryHistoryPolicy(authority.db_path, authority.subject, check)
    async def visible():
        async with aiosqlite.connect(authority.db_path) as db:
            db.row_factory = aiosqlite.Row
            return await policy.check_dependencies(db=db, primary_ref=authority.primary_ref,
                disclosure_context=history_disclosure(), dependencies=proof)
    assert await visible()
    await suppress(manager, authority, groups[0].registrations[1].envelope.evidence_refs[1].evidence_id)
    assert not await visible()
    assert project_recall_fragments(await call(runtime, 5)) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_port", "incomplete", "forged", "missing_authority"])
async def test_unproven_actual_short_never_projected(actual, monkeypatch, fault):
    runtime, _ = actual
    manager = await runtime.manager()
    if fault == "missing_port":
        monkeypatch.setattr(manager, "resolve_short_horizon_sources", None)
    elif fault == "missing_authority":
        runtime._conversation_evidence_authority = None
    elif fault == "forged":
        original = manager.recall_short_horizon
        async def forge(**kwargs):
            result = await original(**kwargs)
            assert result.hits
            return replace(result, audit_id="not-an-actual-selection")
        monkeypatch.setattr(manager, "recall_short_horizon", forge)
    else:
        original = manager.resolve_short_horizon_sources
        async def partial(**kwargs):
            result = await original(**kwargs)
            assert len(result.items[0].source_refs) == 2
            return replace(result, items=(replace(result.items[0], source_refs=result.items[0].source_refs[:1]),))
        monkeypatch.setattr(manager, "resolve_short_horizon_sources", partial)
    assert project_recall_fragments(await call(runtime)) == ()


@pytest.mark.asyncio
async def test_actual_two_hits_cropped_b_does_not_poison_a(tmp_path, pair_seed):
    state, index, _, _, groups, _ = pair_seed
    for source, dest in ((state, tmp_path / "state.db"), (index, tmp_path / "index.db")):
        with sqlite3.connect(source) as src, sqlite3.connect(dest) as dst:
            src.backup(dst)
    runtime = compose_human_memory_runtime(
        tmp_path / "state.db", tmp_path / "index.db",
        adapter_factory=lambda *_: pytest.fail("no provider"),
    )
    try:
        lanes = await runtime.typed_recall(query="quartznebula topazmarker",
            run_id="actual-two-hit-selection", turn_ordinal=1)
        fragments = project_recall_fragments(lanes)
        assert len(fragments) == 2
        a = next(f for f in fragments if "quartznebula" in f["payload"])
        b = next(f for f in fragments if "topazmarker" in f["payload"])
        assert a["history_source_dependencies"]["evidence"] != b["history_source_dependencies"]["evidence"]
        assert len(a["history_source_dependencies"]["evidence"]) == 2
        assert a["history_source_dependencies"]["short_horizon"] == [a["history_binding"]]
        # Exact result cropping models the subsequent selected fragment budget;
        # the retained hit must carry no dependency from the omitted result.
        cropped = replace(lanes, short_horizon=replace(lanes.short_horizon,
            hits=tuple(h for h in lanes.short_horizon.hits if h.chunk_ref == a["ref"])))
        assert project_recall_fragments(cropped) == (a,)
        manager = await runtime.manager()
        authority = runtime.conversation_evidence_authority
        async def check(*, subject, **kwargs):
            assert subject == authority.subject
            return await manager.check_history_visibility(principal=runtime.principal(), **kwargs)
        policy = PrimaryHistoryPolicy(authority.db_path, authority.subject, check)
        async def visible(fragment):
            async with aiosqlite.connect(authority.db_path) as db:
                db.row_factory = aiosqlite.Row
                return await policy.check_dependencies(db=db, primary_ref=authority.primary_ref,
                    disclosure_context=history_disclosure(), dependencies=fragment["history_source_dependencies"])
        assert await visible(a) and await visible(b)
        await suppress(manager, authority, groups[1].registrations[0].envelope.evidence_id)
        assert await visible(a)
        assert not await visible(b)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_cancellation_propagates(actual, monkeypatch):
    runtime, _ = actual
    manager = await runtime.manager()
    async def cancelled(**kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(manager, "resolve_short_horizon_sources", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await call(runtime)


@pytest.mark.asyncio
async def test_lazy_actual_namespace_rechecked_and_never_replaced(tmp_path):
    from deskpet.memory.conversation_registration import PrimaryConversationAuthority
    from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory
    from deskpet.memory.schema import dispatch_startup_epoch
    from deskpet.sdk_adapters.context_route import local_owner_auth
    from deskpet.memory.history_source_authority import HostHistorySourceError
    subject = local_owner_auth().subject
    missing = tmp_path / "missing.db"
    runtime = compose_human_memory_runtime(missing, tmp_path / "memory.db", adapter_factory=lambda *_: None)
    assert not missing.exists()  # no factory-time initialization side effect
    authority = runtime.conversation_evidence_authority
    for name in ("first.db", "second.db"):
        path = tmp_path / name
        service = HumanMemoryHostServiceFactory(path,
            await dispatch_startup_epoch(path, approved_fresh_lane=True)).bind(local_owner_auth())
        from deskpet.memory.procedure_recovery_schema import initialize_procedure_recovery_state_db
        await initialize_procedure_recovery_state_db(path)  # 生产启动安装的 v50–v54 扩展
        primary = (await service.open_primary())["primary_ref"]
    authority.db_path = tmp_path / "first.db"
    await authority.bind_primary()
    original = authority._binding
    authority.db_path = tmp_path / "second.db"
    with pytest.raises(ValueError, match="conversation_epoch_mismatch"):
        await authority.bind_primary()
    assert authority._binding == original
    explicit = PrimaryConversationAuthority(tmp_path / "second.db", subject=subject, primary_ref="wrong-primary")
    with pytest.raises(ValueError, match="conversation_primary_mismatch"):
        await explicit.bind_primary()
    # Actual startup schema but absent subject initialization is not a primary.
    uninitialized = tmp_path / "uninitialized.db"
    await dispatch_startup_epoch(uninitialized, approved_fresh_lane=True)
    with pytest.raises(HostHistorySourceError):
        await PrimaryConversationAuthority(uninitialized, subject=subject).bind_primary()

"""Actual Host completed turns, S1 registrations and public SDK short selection.

The provider fixture is deterministic and in-process; no paid/network provider.
SDK package identity (source-overlay versus installed) belongs in the run evidence.
"""

import sqlite3
import time
from dataclasses import replace

import aiosqlite
import pytest
import pytest_asyncio
import simple_harness_memory as m
from deskpet.memory.conversation_registration import PrimaryConversationAuthority
from deskpet.memory.human_memory_v7 import local_memory_principal
from deskpet.memory.primary_visibility import PrimaryHistoryPolicy
from deskpet.memory.selected_short_sources import (
    SelectedShortSourceReader,
    SelectedShortSourcesUnavailable,
)
from deskpet.memory.short_indexing import PrimaryShortIndexingService
from tests.execution.test_primary_foreground_runtime import history_disclosure
from tests.memory.test_primary_short_ingestion import memory, real_turns, recall


@pytest_asyncio.fixture(scope="module")
async def seed(tmp_path_factory):
    path = tmp_path_factory.mktemp("selected-short-seed")
    state, _, authority, manager = await real_turns(path, 12)
    try:
        indexed = await PrimaryShortIndexingService(
            authority, manager=manager, principal=local_memory_principal()
        ).reconcile()
        assert len(indexed.groups) == 12 and indexed.blocked == ()
        hits = await recall(manager, "quartznebula")
        hit = hits.hits[0]
        first = m.HistoryShortHorizonBinding(
            hits.audit_id, hit.chunk_ref, hit.content_hash
        )
        hits = await recall(manager, "topazmarker")
        hit = next(h for h in hits.hits if "topazmarker" in h.content)
        second = m.HistoryShortHorizonBinding(
            hits.audit_id, hit.chunk_ref, hit.content_hash
        )
        return (
            state,
            path / "index.db",
            authority.subject,
            authority.primary_ref,
            indexed.groups,
            (first, second),
        )
    finally:
        await manager.close()


@pytest_asyncio.fixture
async def fixture(tmp_path, seed):
    state, index, subject, primary, groups, bindings = seed
    for source, target in (
        (state, tmp_path / "state.db"),
        (index, tmp_path / "index.db"),
    ):
        # File-level SQLite backup only, no private SDK table reads or mutations.
        with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
    authority = PrimaryConversationAuthority(
        tmp_path / "state.db", subject=subject, primary_ref=primary
    )
    manager = await memory(tmp_path / "index.db", authority)
    try:
        yield authority, manager, groups, bindings
    finally:
        await manager.close()


def reader(authority, manager):
    return SelectedShortSourceReader(
        authority, manager=manager, principal=local_memory_principal()
    )


async def suppress(manager, authority, evidence_id):
    return await manager.suppress(
        principal=local_memory_principal(),
        request=m.SuppressionRequest(
            "forget-selected:" + evidence_id,
            authority.subject,
            m.SuppressionScopeKind.EVIDENCE,
            evidence_id,
            "user_forget",
            time.time(),
        ),
    )


@pytest.mark.asyncio
async def test_fresh_selected_reader_and_terminal_ancestor_control(tmp_path):
    _, _, authority, manager = await real_turns(tmp_path, 11)
    try:
        indexed = await PrimaryShortIndexingService(
            authority, manager=manager, principal=local_memory_principal()
        ).reconcile()
        selected = await recall(manager, "quartznebula")
        hit = selected.hits[0]
        binding = m.HistoryShortHorizonBinding(
            selected.audit_id, hit.chunk_ref, hit.content_hash
        )
        actual = reader(authority, manager)
        result = await actual.resolve(
            disclosure_context=history_disclosure(), bindings=(binding,)
        )
        assert result.accepted_bindings == (binding,)
        assert len(result.visibility_dependencies["evidence"]) == 2
        terminal = (
            indexed.groups[0].registrations[1].envelope.evidence_refs[1].evidence_id
        )
        await suppress(manager, authority, terminal)
        denied = await actual.resolve(
            disclosure_context=history_disclosure(), bindings=(binding,)
        )
        assert denied.accepted_bindings == ()
        assert denied.visibility_dependencies["evidence"] == []
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_only_selected_complete_group_roots_no_all_indexed_scan(
    fixture, monkeypatch
):
    authority, manager, groups, bindings = fixture

    async def forbidden():
        raise AssertionError(
            "selected reader must not enumerate completed/indexed groups"
        )

    monkeypatch.setattr(authority, "completed_run_ids", forbidden)
    calls, original = [], manager.resolve_short_horizon_sources

    async def capture(**kwargs):
        calls.append(kwargs)
        return await original(**kwargs)

    monkeypatch.setattr(manager, "resolve_short_horizon_sources", capture)
    result = await reader(authority, manager).resolve(
        disclosure_context=history_disclosure(), bindings=(bindings[0],)
    )
    assert result.accepted_bindings == (bindings[0],)
    assert len(calls) == 1 and calls[0]["bindings"] == (bindings[0],)
    expected = {
        (r.envelope.evidence_id, r.envelope.envelope_hash)
        for r in groups[0].registrations
    }
    assert set(result.items[0].evidence) == expected
    proof = result.visibility_dependencies
    assert {
        (r["evidence_id"], r["envelope_hash"]) for r in proof["evidence"]
    } == expected
    assert len(proof["evidence"]) == 2  # index has 24 roots, latest10 not selected
    assert proof["recall"] == [] and len(proof["short_horizon"]) == 1


@pytest.mark.asyncio
async def test_unrelated_forget_does_not_block_and_selected_forget_only_drops_its_hit(
    fixture,
):
    authority, manager, groups, bindings = fixture
    await suppress(manager, authority, groups[-1].registrations[0].envelope.evidence_id)
    selected = reader(authority, manager)
    assert (
        await selected.resolve(
            disclosure_context=history_disclosure(), bindings=bindings
        )
    ).accepted_bindings == bindings
    # The first group cannot depend on a future second group. The reverse is
    # not true: real primary history carries the earlier terminal into turn2.
    await suppress(manager, authority, groups[1].registrations[0].envelope.evidence_id)
    result = await selected.resolve(
        disclosure_context=history_disclosure(), bindings=bindings
    )
    assert [item.visible for item in result.items] == [True, False]
    assert result.items[1].evidence == ()
    assert result.accepted_bindings == (bindings[0],)


@pytest.mark.asyncio
async def test_terminal_ancestor_forget_between_source_snapshot_and_host_check(
    fixture, monkeypatch
):
    authority, manager, groups, bindings = fixture
    # Actual assistant S1 has USER + terminal refs. No fabricated source linkage.
    terminal_id = groups[1].registrations[1].envelope.evidence_refs[1].evidence_id
    original = manager.resolve_short_horizon_sources

    async def race(**kwargs):
        observed = await original(**kwargs)
        assert observed.items[0].visible
        await suppress(manager, authority, terminal_id)
        return observed

    monkeypatch.setattr(manager, "resolve_short_horizon_sources", race)
    result = await reader(authority, manager).resolve(
        disclosure_context=history_disclosure(), bindings=bindings
    )
    assert [item.visible for item in result.items] == [True, False]
    assert result.items[1].evidence == ()


@pytest.mark.asyncio
async def test_actual_inherited_terminal_keeps_ancestor_forget_blocking(fixture):
    from deskpet.memory.primary_visibility import read_evidence_pair

    authority, manager, groups, bindings = fixture
    first_terminal = groups[0].registrations[1].envelope.evidence_refs[1]
    second_terminal = groups[1].registrations[1].envelope.evidence_refs[1]
    async with aiosqlite.connect(authority.db_path) as db:
        db.row_factory = aiosqlite.Row
        envelope, _ = await read_evidence_pair(
            db=db,
            subject=authority.subject,
            primary_ref=authority.primary_ref,
            evidence_id=second_terminal.evidence_id,
        )
    assert {
        "evidence_id": first_terminal.evidence_id,
        "envelope_hash": first_terminal.content_hash,
    } in envelope.to_json()["sanitized_payload"]["visibility_dependencies"]["evidence"]
    await suppress(manager, authority, first_terminal.evidence_id)
    result = await reader(authority, manager).resolve(
        disclosure_context=history_disclosure(), bindings=bindings
    )
    assert result.accepted_bindings == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "source_ref",
        "source_hash",
        "sanitized_hash",
        "admission_receipt_id",
        "admission_receipt_hash",
        "registration_hash",
        "role",
        "item_ordinal",
    ],
)
async def test_every_returned_source_commitment_must_match_host(
    fixture, monkeypatch, field
):
    authority, manager, _, bindings = fixture
    original = manager.resolve_short_horizon_sources

    async def altered(**kwargs):
        snapshot = await original(**kwargs)
        source = snapshot.items[0].source_refs[0]
        value = (
            9
            if field == "item_ordinal"
            else ("b" * 64 if field.endswith("hash") else "wrong-source")
        )
        refs = (replace(source, **{field: value}), *snapshot.items[0].source_refs[1:])
        return replace(
            snapshot,
            items=(replace(snapshot.items[0], source_refs=refs), snapshot.items[1]),
        )

    monkeypatch.setattr(manager, "resolve_short_horizon_sources", altered)
    result = await reader(authority, manager).resolve(
        disclosure_context=history_disclosure(), bindings=bindings
    )
    assert [item.visible for item in result.items] == [False, True]
    assert result.items[0].evidence == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["missing", "duplicate", "other_group"])
async def test_individually_real_registrations_do_not_prove_complete_selected_group(
    fixture, monkeypatch, variant
):
    authority, manager, _, bindings = fixture
    original = manager.resolve_short_horizon_sources

    async def altered(**kwargs):
        snapshot = await original(**kwargs)
        refs = snapshot.items[0].source_refs
        refs = {
            "missing": refs[:1],
            "duplicate": (refs[0], refs[0]),
            "other_group": (refs[0], snapshot.items[1].source_refs[1]),
        }[variant]
        return replace(
            snapshot,
            items=(replace(snapshot.items[0], source_refs=refs), snapshot.items[1]),
        )

    monkeypatch.setattr(manager, "resolve_short_horizon_sources", altered)
    result = await reader(authority, manager).resolve(
        disclosure_context=history_disclosure(), bindings=bindings
    )
    assert [item.visible for item in result.items] == [False, True]


@pytest.mark.asyncio
async def test_missing_host_assistant_rejects_whole_hit_not_partial_user(fixture):
    authority, manager, groups, bindings = fixture
    with sqlite3.connect(authority.db_path) as db:
        # Corruption injection in this copied test-only Host database. Preserve
        # production append-only behavior; never touch SDK schema or live stores.
        triggers = db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='human_memory_evidence'"
        ).fetchall()
        for (name,) in triggers:
            db.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        db.execute(
            "DELETE FROM human_memory_evidence WHERE evidence_id=?",
            (groups[0].registrations[1].envelope.evidence_id,),
        )
    result = await reader(authority, manager).resolve(
        disclosure_context=history_disclosure(), bindings=bindings
    )
    assert [item.visible for item in result.items] == [False, True]
    assert result.items[0].evidence == ()


@pytest.mark.asyncio
async def test_duplicate_inputs_reopen_and_final_fence_after_later_forget(fixture):
    authority, manager, groups, bindings = fixture
    result = await reader(authority, manager).resolve(
        disclosure_context=history_disclosure(), bindings=(bindings[0], bindings[0])
    )
    assert result.accepted_bindings == (bindings[0], bindings[0])
    assert len(result.visibility_dependencies["short_horizon"]) == 1

    async def check(*, subject, **kwargs):
        assert subject == authority.subject
        return await manager.check_history_visibility(
            principal=local_memory_principal(), **kwargs
        )

    policy = PrimaryHistoryPolicy(authority.db_path, authority.subject, check)

    async def final_guard():
        async with aiosqlite.connect(authority.db_path) as db:
            db.row_factory = aiosqlite.Row
            return await policy.check_dependencies(
                db=db,
                primary_ref=authority.primary_ref,
                disclosure_context=history_disclosure(),
                dependencies=result.visibility_dependencies,
            )

    assert await final_guard()
    await suppress(manager, authority, groups[0].registrations[0].envelope.evidence_id)
    assert not await final_guard()  # previous reader observation is not a grant


@pytest.mark.asyncio
async def test_missing_port_wrong_context_and_oversized_batch_fail_before_source_work(
    fixture, monkeypatch
):
    authority, manager, _, bindings = fixture

    async def forbidden(**kwargs):
        raise AssertionError("validation must precede resolver call")

    monkeypatch.setattr(manager, "resolve_short_horizon_sources", forbidden)
    selected = reader(authority, manager)
    with pytest.raises(ValueError, match="bindings_invalid"):
        await selected.resolve(
            disclosure_context=history_disclosure(), bindings=(bindings[0],) * 257
        )
    with pytest.raises(ValueError, match="disclosure_mismatch"):
        await selected.resolve(
            disclosure_context=replace(history_disclosure(), subject="another"),
            bindings=bindings,
        )
    monkeypatch.setattr(manager, "resolve_short_horizon_sources", None)
    with pytest.raises(SelectedShortSourcesUnavailable, match="port_unavailable"):
        await selected.resolve(
            disclosure_context=history_disclosure(), bindings=bindings
        )

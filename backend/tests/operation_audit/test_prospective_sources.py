"""Actual installed Manager/SQLite source calls; Host-only fault/tamper controls."""
import asyncio
from dataclasses import replace
import json
import sqlite3
import threading

import aiosqlite
import pytest
import pytest_asyncio
import simple_harness_memory as m
from simple_harness.runtime import EvidenceRef, MemoryMutationPlan

from deskpet.memory.analysis_proposal import admitted_item, compile_operation, derive_span
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import HOST_PUBLIC_TURN_FILTER_POLICY, build_foreground_turn_evidence
from deskpet.memory.prospective_registration_source import PublicRegistrationAuthoritySource
from deskpet.memory.s5c_schema import initialize_s5c_state_db
from deskpet.memory.s5c_store import S5cConflict, S5cStore
from deskpet.operation_audit.memory_attempts import MemoryAttemptJournal
from deskpet.operation_audit.prospective_sources import ProspectiveSourceJournal
from tests.memory.test_s5c_store import P


@pytest_asyncio.fixture
async def world(tmp_path):
    path = tmp_path / "state.db"
    program = HumanMemoryProgramStore(path)
    await program.initialize_subject(P.actor_id)
    text = "Remind me to send the report at the specified time."
    envelope, receipt = build_foreground_turn_evidence(subject=P.actor_id,
        authority_ref="host:test-local-owner", delivery_key="observed-source", text=text)
    await program.append_evidence(envelope, receipt)
    await initialize_s5c_state_db(path)
    item = admitted_item(envelope, receipt)
    operation = compile_operation({"operation_id": "observed-create", "memory_type": "prospective",
        "prospective": {"action": "send the report", "trigger_at_iso": "1970-01-01T00:00:30+00:00",
                        "timezone": "UTC"}},
        derive_span(item, text, span_id="observed-span"), item=item, now=20.0)
    manager = await m.build_human_memory_v7(tmp_path / "memory.db", clock=lambda: 20.0,
        evidence_authority=HostEvidenceAuthority(path),
        supported_filter_policies=frozenset({HOST_PUBLIC_TURN_FILTER_POLICY}),
        classification_policy=m.InformationClassificationPolicy(policy_id="observed-classification",
            policy_version="1", authority_ref="host:classification/v1",
            required_privacy_class="personal", required_information_attributes=()))
    try:
        await manager.ingest_committed_evidence(envelope, receipt)
        plan = MemoryMutationPlan(plan_id="observed-plan", run_id=envelope.run_id,
            turn_id="observed-turn", subject=P.actor_id, base_revision=1, outcome="mutate",
            operations=(operation,), disclosure_context=envelope.disclosure_context,
            evidence_refs=(EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),),
            idempotency_key="observed-plan")
        result = await manager.apply_memory_mutation_plan(principal=P,
            scope=m.MemoryScope.personal(P.actor_id), plan=plan)
        assert result.receipt_ref is not None
        entry = next(e for e in (await manager.read_outbox(principal=P)).entries
                     if e.topic == "memory.prospective.registration.requested")
        yield manager, entry, path, text
    finally:
        await manager.close()


async def call(journal, world, **changes):
    manager, entry, *_ = world
    kwargs = dict(principal=P, outbox_id=entry.outbox_id, payload_hash=entry.payload_hash)
    kwargs.update(changes)
    return await journal.read_prospective_outbox_source(manager, **kwargs)


def exact(row, observation, state):
    assert row["state"] == state and row["observation_status"] == "captured_bound"
    assert json.loads(row["observation_json"]) == observation.to_json()
    assert row["observation_hash"] == observation.observation_hash
    assert observation.persistence_status == "host_persistence_unverified"
    assert row["settlement_hash"] and row["decision_hash"] is None
    assert all(row[k] is None for k in ("context_run_ref_hash", "context_hash", "plan_hash"))


@pytest.mark.asyncio
async def test_real_default_registration_records_source_not_grant_and_reopens(world, tmp_path, monkeypatch):
    manager, entry, path, text = world
    source = PublicRegistrationAuthoritySource(store=S5cStore(path, P), memory=manager, clock=lambda: 20.0)
    original = manager.read_prospective_outbox_source
    observed = []
    async def capture(**kwargs):
        rows = (await source.operation_audit.page(principal=P))["items"]
        assert rows[-1]["state"] == "started" and rows[-1]["settled_at"] is None
        result = await original(**kwargs)
        observed.append(result)
        return result
    monkeypatch.setattr(manager, "read_prospective_outbox_source", capture)
    grant = await source.prepare_registration(principal=P, entry=entry)
    assert grant.intent.signal_receipt_hash == observed[0].target_mutation_receipt_ref.receipt_hash
    assert await source.prepare_registration(principal=P, entry=entry) == grant
    assert len(observed) == 1  # Existing grant replay performs no new source read.
    reopened = ProspectiveSourceJournal(source.operation_audit.path)
    page = await reopened.page(principal=P)
    exact(page["items"][0], observed[0].operation_observation, "returned")
    assert page["items"][0]["result_hash"] == observed[0].source_hash
    assert page["all_operations_recorded"] is False and page["cost"] is None
    assert text not in str(page) and entry.outbox_id not in str(page) and grant.authority_id not in str(page)
    assert (await reopened.page(principal=replace(P, actor_id="other")))["items"] == []
    # Existing reader/table coexistence; no second schema or permission ledger.
    assert len((await MemoryAttemptJournal(reopened.path).page(principal=P))["items"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["payload", "owner", "badtype"])
async def test_real_rejections_original_attachment_and_no_grant(world, tmp_path, variant):
    journal = ProspectiveSourceJournal(tmp_path / "operation-audit.db")
    changes = {"payload": {"payload_hash": "0" * 64},
               "owner": {"principal": replace(P, actor_id="foreign")},
               "badtype": {"outbox_id": None}}[variant]
    with pytest.raises((m.MemoryValidationError, m.MemoryOwnershipConflict, TypeError, ValueError)) as raised:
        await call(journal, world, **changes)
    row = (await journal.page(principal=changes.get("principal", P)))["items"][0]
    exact(row, raised.value.operation_observation, "raised")
    assert row["result_hash"] is None
    assert await S5cStore(world[2], P).registration(world[1].outbox_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "read_failure"])
async def test_real_manager_read_failure_and_cancellation_attachment(world, tmp_path, monkeypatch, mode):
    journal = ProspectiveSourceJournal(tmp_path / "operation-audit.db")
    entered = asyncio.Event()
    original = aiosqlite.Connection.set_progress_handler
    async def interrupt(db, *args, **kwargs):
        if not entered.is_set():
            entered.set()
            if mode == "cancel":
                await asyncio.Event().wait()
            raise OSError("private-database-location-do-not-log")
        return await original(db, *args, **kwargs)
    monkeypatch.setattr(aiosqlite.Connection, "set_progress_handler", interrupt)
    task = asyncio.create_task(call(journal, world))
    await asyncio.wait_for(entered.wait(), 2)
    if mode == "cancel":
        task.cancel()
    with pytest.raises(asyncio.CancelledError if mode == "cancel" else OSError) as raised:
        await task
    row = (await journal.page(principal=P))["items"][0]
    exact(row, raised.value.operation_observation, "cancelled" if mode == "cancel" else "raised")
    assert "private-database-location" not in str(row)
    assert raised.value.operation_observation.reason == (
        "source_read_cancelled" if mode == "cancel" else "source_read_failed")
    # The actual Manager is still usable after its owned read connection closed.
    assert (await call(journal, world)).operation_observation.outcome == "observed"


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["prospective_audit.after_started", "memory_audit.before_settled"])
async def test_audit_write_failure_never_replays_sdk_or_changes_success(world, tmp_path, monkeypatch, point):
    def fault(actual):
        if actual == point:
            raise OSError("private-write-error-do-not-log")
    journal = ProspectiveSourceJournal(tmp_path / "operation-audit.db", fault=fault)
    manager = world[0]
    original, calls = manager.read_prospective_outbox_source, []
    async def capture(**kwargs):
        result = await original(**kwargs)
        calls.append(result)
        return result
    monkeypatch.setattr(manager, "read_prospective_outbox_source", capture)
    result = await call(journal, world)
    assert calls == [result] and result.operation_observation.outcome == "observed"
    rows = (await journal.page(principal=P))["items"]
    if point.endswith("after_started"):
        assert rows == [] and journal.last_code == "prospective_audit_start_unavailable"
    else:
        assert rows[0]["state"] == "started" and rows[0]["observation_status"] == "pending"
        assert rows[0]["settlement_hash"] is None
        assert journal.last_code == "prospective_audit_settlement_unavailable"
    assert "private-write-error" not in str(rows)


@pytest.mark.asyncio
async def test_cancel_during_owned_start_write_joins_and_never_calls_memory(world, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def fault(point):
        if point == "prospective_audit.after_started":
            entered.set()
            assert release.wait(2)
    journal = ProspectiveSourceJournal(tmp_path / "operation-audit.db", fault=fault)
    calls = []
    async def forbidden(**kwargs):
        calls.append(kwargs)
        raise AssertionError("cancelled before SDK call")
    monkeypatch.setattr(world[0], "read_prospective_outbox_source", forbidden)
    task = asyncio.create_task(call(journal, world))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    row = (await journal.page(principal=P))["items"][0]
    assert not calls and row["state"] == "cancelled_before_call"
    assert row["observation_status"] == "not_invoked" and row["observation_json"] is None
    with sqlite3.connect(journal.path, timeout=0) as db:
        db.execute("BEGIN IMMEDIATE")  # Owned writer closed before cancellation returned.


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["request_hash", "claimed_owner_ref_hash", "source_hash", "absent"])
async def test_tampered_or_absent_observation_not_promoted(world, tmp_path, monkeypatch, field):
    journal = ProspectiveSourceJournal(tmp_path / "operation-audit.db")
    original = world[0].read_prospective_outbox_source
    async def tampered(**kwargs):
        result = await original(**kwargs)
        observation = (None if field == "absent" else
                       replace(result.operation_observation, **{field: "0" * 64}))
        return replace(result, operation_observation=observation)
    monkeypatch.setattr(world[0], "read_prospective_outbox_source", tampered)
    result = await call(journal, world)
    row = (await journal.page(principal=P))["items"][0]
    assert row["state"] == "returned" and result.source_hash
    assert row["observation_status"] == ("absent" if field == "absent" else "unverifiable")
    assert row["observation_json"] is None and row["observation_hash"] is None


@pytest.mark.asyncio
async def test_unavailable_page_safe_code_and_missing_v2_never_falls_back(world, tmp_path, monkeypatch):
    journal = ProspectiveSourceJournal(tmp_path / "operation-audit.db")
    with pytest.raises(AttributeError):
        await call(journal, world, operation="read_prospective_outbox_source_v2")
    rows = (await journal.page(principal=P, operation="read_prospective_outbox_source_v2"))["items"]
    assert rows[0]["state"] == "raised" and rows[0]["observation_status"] == "absent"
    assert (await journal.page(principal=P))["items"] == []
    def fail():
        raise OSError("private-db-path")
    monkeypatch.setattr(journal, "_connect", fail)
    with pytest.raises(RuntimeError, match="^prospective_audit_read_unavailable$"):
        await journal.page(principal=P)
    assert journal.last_code == "prospective_audit_read_unavailable"

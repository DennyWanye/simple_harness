import sqlite3

import pytest
from deskpet.operation_audit.composition import compose_terminal_audit
from tests.execution.test_preparation_rejection import (
    admitted_denial as _admitted_denial,
)
from tests.execution.test_preparation_rejection import state

admitted_denial = _admitted_denial


@pytest.mark.asyncio
async def test_actual_preparation_receipt_is_discovered_without_sdk_run_and_once(
    admitted_denial, tmp_path
):
    path, queue, admission, rejection, _, _, _, _, auth = admitted_denial
    await queue.settle_preparation_rejection(
        rejection=rejection,
        owner_id=admission.owner_id,
        generation=admission.generation,
    )
    before = state(path)
    consumer = await compose_terminal_audit(
        path,
        stack_getter=lambda: None,
        subject=auth.subject,
        audit_path=tmp_path / "audit.db",
        start=False,
    )
    await consumer.tick()
    await consumer.tick()
    with sqlite3.connect(tmp_path / "audit.db") as db:
        assert (
            db.execute("SELECT count(*) FROM preparation_audit_sources").fetchone()[0]
            == 1
        )
        assert (
            db.execute("SELECT count(*) FROM preparation_audit_findings").fetchone()[0]
            == 1
        )
        row = db.execute(
            "SELECT sdk_run_id,source_status,source_json FROM preparation_audit_sources"
        ).fetchone()
        assert row[0] is None and row[1] == "verified" and '"cost":null' in row[2]
        assert db.execute("SELECT count(*) FROM audit_jobs").fetchone()[0] == 0
    reopened = await compose_terminal_audit(
        path,
        stack_getter=lambda: None,
        subject=auth.subject,
        audit_path=tmp_path / "audit.db",
        start=False,
    )
    assert await reopened.preparation_sources.discover() == 0
    assert state(path) == before


@pytest.mark.asyncio
async def test_foreign_subject_and_corrupted_actual_source_cannot_be_verified(
    admitted_denial, tmp_path
):
    from deskpet.operation_audit.preparation_sources import PreparationAuditConsumer

    path, queue, admission, rejection, _, _, _, _, auth = admitted_denial
    await queue.settle_preparation_rejection(
        rejection=rejection,
        owner_id=admission.owner_id,
        generation=admission.generation,
    )
    audit = tmp_path / "audit.db"
    assert (
        await PreparationAuditConsumer(path, audit, subject="another-owner").discover()
        == 0
    )
    damaged = tmp_path / "damaged-host-copy.db"
    with sqlite3.connect(path) as source, sqlite3.connect(damaged) as db:
        source.backup(db)
        # Simulate storage damage in a disposable COPY, bypassing that copy's
        # write guard. Production integrity triggers and the source remain intact.
        triggers = db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='foreground_run_transitions'"
        ).fetchall()
        for (name,) in triggers:
            db.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        db.execute(
            "UPDATE foreground_run_transitions SET transition_hash=? WHERE idempotency_key='preparation-rejected:v1'",
            ("f" * 64,),
        )
    reader = PreparationAuditConsumer(damaged, audit, subject=auth.subject)
    assert await reader.discover() == 1
    assert await reader.discover() == 0
    with sqlite3.connect(audit) as db:
        assert db.execute(
            "SELECT source_status,recorded_at FROM preparation_audit_sources"
        ).fetchone() == ("unverifiable", None)
        assert (
            db.execute("SELECT reason FROM preparation_audit_findings").fetchone()[0]
            == "preparation_source_invalid"
        )

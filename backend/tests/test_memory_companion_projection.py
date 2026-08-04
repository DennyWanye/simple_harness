from __future__ import annotations

import asyncio
import shutil
import sqlite3
from pathlib import Path

import pytest

from deskpet.memory.companion_message_projection import (
    COMPANION_REDACTION_TOMBSTONE,
    COMPANION_REDACTION_TOMBSTONE_HASH,
    CurrentCompanionProjection,
    TrustedCompanionOwner,
    TrustedCompanionProjectionRoute,
)
from deskpet.memory.migrator import (
    DEFAULT_MIGRATIONS_DIR,
    MigrationError,
    run_migrations,
)
from deskpet.memory.session_db import SessionDB


async def _build_v20(db_path: Path, migrations_dir: Path) -> None:
    migrations_dir.mkdir()
    for source in sorted(DEFAULT_MIGRATIONS_DIR.glob("*.sql")):
        if source.name <= "012_task_conversation_scope_v20.sql":
            shutil.copyfile(source, migrations_dir / source.name)
    applied = await run_migrations(db_path, migrations_dir=migrations_dir)
    assert applied[-1] == "012_task_conversation_scope_v20.sql"


@pytest.mark.asyncio
async def test_v20_upgrade_preserves_rows_ids_sequence_fts_and_vector_mapping(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    await _build_v20(db_path, tmp_path / "v20-migrations")
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO sessions(id,created_at,metadata) VALUES ('s1',1,'{}')"
        )
        visible_id = db.execute(
            """INSERT INTO messages(
                 session_id,role,content,created_at,projection_kind,context_visibility
               ) VALUES ('s1','user','visible_token',1,'user_message','conversation')"""
        ).lastrowid
        excluded_id = db.execute(
            """INSERT INTO messages(
                 session_id,role,content,created_at,workflow_event_id,
                 projection_kind,context_visibility
               ) VALUES (
                 's1','assistant','hidden_token',2,'workflow-1',
                 'workflow_progress','exclude'
               )"""
        ).lastrowid
        deleted_id = db.execute(
            """INSERT INTO messages(
                 session_id,role,content,created_at,projection_kind,context_visibility
               ) VALUES ('s1','user','deleted',3,'user_message','conversation')"""
        ).lastrowid
        db.execute("DELETE FROM messages WHERE id=?", (deleted_id,))
        db.execute(
            "CREATE TABLE messages_vec(message_id INTEGER PRIMARY KEY,embedding BLOB)"
        )
        db.execute(
            "INSERT INTO messages_vec(message_id,embedding) VALUES (?,X'0102')",
            (visible_id,),
        )
        old_indexes = {
            row[0]
            for row in db.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='index' AND tbl_name='messages' AND sql IS NOT NULL"""
            )
        }
        old_sequence = db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='messages'"
        ).fetchone()[0]
        db.commit()

    assert await run_migrations(db_path) == [
        "013_companion_projection.sql",
        "014_context_usage_history_v22.sql",
        "015_provider_binding_lifecycle_v23.sql",
        "016_context_usage_authority_v24.sql",
        "017_provider_workload_audit_v25.sql",
        "018_message_archive_projection_v26.sql",
        "019_provider_fault_correlation_v27.sql",
    ]
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 27
        rows = db.execute(
            """SELECT id,workflow_event_id,projection_event_id
               FROM messages ORDER BY id"""
        ).fetchall()
        assert rows == [
            (visible_id, None, None),
            (excluded_id, "workflow-1", "workflow-1"),
        ]
        assert db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='messages'"
        ).fetchone()[0] >= old_sequence
        assert db.execute(
            "SELECT message_id FROM messages_vec"
        ).fetchall() == [(visible_id,)]
        new_indexes = {
            row[0]
            for row in db.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='index' AND tbl_name='messages' AND sql IS NOT NULL"""
            )
        }
        assert old_indexes <= new_indexes
        assert db.execute(
            "SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'visible_token'"
        ).fetchall() == [(visible_id,)]
        assert db.execute(
            "SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'hidden_token'"
        ).fetchall() == []
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.asyncio
async def test_fresh_and_repeated_companion_migration_are_idempotent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    first = await run_migrations(db_path)
    second = await run_migrations(db_path)
    assert first[-1] == "019_provider_fault_correlation_v27.sql"
    assert second == []
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 27
        assert db.execute(
            """SELECT count(*) FROM schema_migrations
               WHERE version='013_companion_projection.sql'"""
        ).fetchone() == (1,)
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "companion_session_owners",
            "companion_owner_scope_versions",
            "companion_ingress_outbox",
            "companion_projection_routes",
            "companion_projection_route_outbox",
            "companion_projection_redaction_receipts",
        } <= tables


@pytest.mark.asyncio
async def test_companion_callback_ddl_marker_and_version_roll_back_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "state.db"
    await _build_v20(db_path, tmp_path / "v20-migrations")

    import deskpet.memory.companion_message_projection as projection_module

    async def _fail_after_ddl(db) -> None:
        await db.execute("ALTER TABLE messages ADD COLUMN must_roll_back TEXT")
        raise RuntimeError("simulated companion migration failure")

    monkeypatch.setattr(
        projection_module, "migrate_companion_message_projection", _fail_after_ddl
    )
    with pytest.raises(MigrationError, match="simulated companion migration failure"):
        await run_migrations(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 20
        assert "must_roll_back" not in {
            row[1] for row in db.execute("PRAGMA table_info(messages)")
        }
        assert db.execute(
            """SELECT 1 FROM schema_migrations
               WHERE version='013_companion_projection.sql'"""
        ).fetchone() is None


@pytest.mark.asyncio
async def test_owner_binding_is_exact_immutable_and_legacy_claim_fails(
    tmp_path: Path,
) -> None:
    store = SessionDB(tmp_path / "state.db")
    owner = TrustedCompanionOwner("profile-a", 1, 1)
    other = TrustedCompanionOwner("profile-b", 1, 1)
    await store.initialize()
    await store.ensure_session("new")
    first = await store.bind_session_owner_if_absent("new", owner)
    replay = await store.bind_session_owner_if_absent("new", owner)
    assert replay == first
    with pytest.raises(RuntimeError, match="rebind_forbidden"):
        await store.bind_session_owner_if_absent("new", other)

    await store.ensure_session("legacy")
    await store.append_message("legacy", "user", "old local history")
    with pytest.raises(RuntimeError, match="legacy_session_cannot_be_claimed"):
        await store.bind_session_owner_if_absent("legacy", owner)

    await store.tombstone_session("new")
    with pytest.raises(RuntimeError, match="owner_tombstoned"):
        await store.bind_session_owner_if_absent("new", owner)
    await store.close()


@pytest.mark.asyncio
async def test_user_message_and_growth_outbox_are_atomic_replayable_and_leased(
    tmp_path: Path,
) -> None:
    store = SessionDB(tmp_path / "state.db")
    owner = TrustedCompanionOwner("profile-a", 1, 1)
    await store.initialize()
    await store.ensure_session("s1")
    await store.bind_session_owner_if_absent("s1", owner)

    message_id = await store.append_user_message_with_growth_outbox(
        "s1",
        "remember this",
        trusted_owner=owner,
        request_id="request-1",
        run_id="run-1",
        turn_id="turn-1",
        retry_of_request_id="request-0",
        retry_of_run_id="run-0",
        retry_of_turn_id="turn-0",
        retry_of_message_id=9,
        priority="normal",
    )
    assert await store.read_companion_ingress_message_content(
        session_id="s1",
        message_id=message_id,
    ) == "remember this"
    semantic = await store.settle_companion_ingress_semantic_intent(
        session_id="s1",
        message_id=message_id,
        trusted_owner=owner,
        request_id="request-1",
        turn_id="turn-1",
        growth_signal_kind="explicit_correction",
    )
    assert semantic["priority"] == "blocking"
    assert (
        semantic["event_envelope"]["semantic_growth_intent"]
        == "explicit_correction"
    )
    # Replaying the original append after semantic promotion is still the same
    # durable message identity and must not conflict on its promoted priority.
    assert await store.append_user_message_with_growth_outbox(
        "s1",
        "remember this",
        trusted_owner=owner,
        request_id="request-1",
        run_id="run-1",
        turn_id="turn-1",
        retry_of_request_id="request-0",
        retry_of_run_id="run-0",
        retry_of_turn_id="turn-0",
        retry_of_message_id=9,
        priority="normal",
    ) == message_id
    with pytest.raises(RuntimeError, match="replay_conflict"):
        await store.append_user_message_with_growth_outbox(
            "s1",
            "different",
            trusted_owner=owner,
            request_id="request-1",
            run_id="run-1",
            turn_id="turn-1",
            priority="blocking",
        )

    claimed = await store.claim_companion_ingress_outbox("runtime-a", 60)
    assert claimed is not None
    assert claimed["message_id"] == message_id
    assert claimed["attempt"] == 1
    assert claimed["event_ref"] == f"message:s1:{message_id}"
    assert claimed["event_envelope"]["request_id"] == "request-1"
    assert claimed["event_envelope"]["retry_of_run_id"] == "run-0"
    retried = await store.settle_companion_ingress_outbox(
        claimed["outbox_id"],
        1,
        error="temporary",
        retry_at=0,
    )
    assert retried["status"] == "pending"
    claimed_again = await store.claim_companion_ingress_outbox("runtime-b", 60)
    assert claimed_again is not None
    assert claimed_again["attempt"] == 2
    with pytest.raises(RuntimeError, match="attempt_stale"):
        await store.settle_companion_ingress_outbox(
            claimed_again["outbox_id"], 1, delivered_hash="delivered"
        )
    delivered = await store.settle_companion_ingress_outbox(
        claimed_again["outbox_id"], 2, delivered_hash="delivered"
    )
    assert delivered["status"] == "delivered"
    with pytest.raises(RuntimeError, match="attempt_stale"):
        await store.settle_companion_ingress_outbox(
            claimed_again["outbox_id"], 1, delivered_hash="delivered"
        )
    assert (
        await store.settle_companion_ingress_outbox(
            claimed_again["outbox_id"], 2, delivered_hash="delivered"
        )
    )["status"] == "delivered"

    await store.ensure_session("unbound")
    with pytest.raises(RuntimeError, match="owner_fence_mismatch"):
        await store.append_user_message_with_growth_outbox(
            "unbound",
            "must not commit",
            trusted_owner=owner,
            request_id="request-2",
            turn_id="turn-2",
        )
    assert await store.get_messages("unbound") == []
    await store.close()


@pytest.mark.asyncio
async def test_owner_read_scope_freezes_session_set_and_message_high_water(
    tmp_path: Path,
) -> None:
    store = SessionDB(tmp_path / "state.db")
    owner = TrustedCompanionOwner("profile-a", 1, 1)
    await store.initialize()
    for session_id in ("s1", "s2"):
        await store.ensure_session(session_id)
        await store.bind_session_owner_if_absent(session_id, owner)
        await store.append_message(session_id, "user", session_id)
    frozen = await store.capture_owner_memory_read_scope("profile-a", 1, 1)
    assert frozen.session_ids == ("s1", "s2")
    assert frozen.as_of_message_id > 0

    await store.append_message("s1", "user", "later")
    await store.ensure_session("s3")
    await store.bind_session_owner_if_absent("s3", owner)
    refreshed = await store.capture_owner_memory_read_scope("profile-a", 1, 1)
    assert frozen.session_ids == ("s1", "s2")
    assert refreshed.session_ids == ("s1", "s2", "s3")
    assert refreshed.as_of_message_id > frozen.as_of_message_id
    assert refreshed.session_set_version > frozen.session_set_version
    assert refreshed.scope_hash != frozen.scope_hash
    await store.close()


@pytest.mark.asyncio
async def test_route_projection_redaction_and_tombstone_share_exact_fences(
    tmp_path: Path,
) -> None:
    store = SessionDB(tmp_path / "state.db")
    owner = TrustedCompanionOwner("profile-a", 1, 1)
    await store.initialize()
    for session_id in ("s1", "s2"):
        await store.ensure_session(session_id)
        await store.bind_session_owner_if_absent(session_id, owner)

    route = await store.set_companion_default_route("profile-a", 1, 1, "s1")
    assert route["route_version"] == 1
    assert (
        await store.set_companion_default_route("profile-a", 1, 1, "s1")
    )["route_version"] == 1
    message_id = await store.append_companion_projection_if_epoch(
        "s1",
        "notification",
        trusted_owner=owner,
        expected_epoch=0,
        route_version=1,
        projection_event_id="growth-1",
        projection_payload_hash="hash-1",
    )
    assert message_id is not None
    assert await store.append_companion_projection_if_epoch(
        "s1",
        "notification",
        trusted_owner=owner,
        expected_epoch=0,
        route_version=1,
        projection_event_id="growth-1",
        projection_payload_hash="hash-1",
    ) == message_id
    with pytest.raises(RuntimeError, match="message_event_projection_conflict"):
        await store.append_companion_projection_if_epoch(
            "s1",
            "different",
            trusted_owner=owner,
            expected_epoch=0,
            route_version=1,
            projection_event_id="growth-1",
            projection_payload_hash="hash-2",
        )
    assert await store.search_fts("notification", session_id="s1") == []

    receipt = await store.record_companion_projection_redaction(
        projection_owner_id="profile-a",
        projection_owner_generation=1,
        projection_event_id="growth-1",
        redaction_version=1,
        expected_old_payload_hash="hash-1",
        new_payload_hash="hash-redacted",
        redaction_outbox_id="redaction-1",
        redacted_content="[redacted]",
    )
    assert receipt["new_payload_hash"] == "hash-redacted"
    assert (
        await store.record_companion_projection_redaction(
            projection_owner_id="profile-a",
            projection_owner_generation=1,
            projection_event_id="growth-1",
            redaction_version=1,
            expected_old_payload_hash="hash-1",
            new_payload_hash="hash-redacted",
            redaction_outbox_id="redaction-1",
            redacted_content="[redacted]",
        )
    )["redaction_outbox_id"] == "redaction-1"

    moved = await store.set_companion_default_route(
        "profile-a", 1, 1, "s2", expected_route_version=1
    )
    assert moved["route_version"] == 2
    with pytest.raises(RuntimeError, match="route_version_conflict"):
        await store.set_companion_default_route(
            "profile-a", 1, 1, "s1", expected_route_version=1
        )
    assert await store.tombstone_session("s2") == 1
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute(
            """SELECT route_version,status,target_epoch
               FROM companion_projection_routes
               WHERE profile_id='profile-a' AND profile_generation=1"""
        ).fetchone() == (3, "tombstoned", 1)
        assert db.execute(
            """SELECT event_kind,route_version,target_epoch
               FROM companion_projection_route_outbox
               ORDER BY route_version"""
        ).fetchall() == [
            ("route_changed", 1, 0),
            ("route_changed", 2, 0),
            ("session_tombstoned", 3, 1),
        ]
        assert db.execute(
            "SELECT status FROM companion_session_owners WHERE session_id='s2'"
        ).fetchone() == ("tombstoned",)
    assert await store.append_companion_projection_if_epoch(
        "s2",
        "late",
        trusted_owner=owner,
        expected_epoch=0,
        route_version=2,
        projection_event_id="growth-late",
        projection_payload_hash="late-hash",
    ) is None
    await store.close()


@pytest.mark.asyncio
async def test_clear_tombstones_owner_route_and_removes_message_outbox_atomically(
    tmp_path: Path,
) -> None:
    store = SessionDB(tmp_path / "state.db")
    owner = TrustedCompanionOwner("profile-a", 1, 1)
    await store.initialize()
    await store.ensure_session("s1")
    await store.bind_session_owner_if_absent("s1", owner)
    await store.set_companion_default_route("profile-a", 1, 1, "s1")
    await store.append_user_message_with_growth_outbox(
        "s1",
        "forget this",
        trusted_owner=owner,
        request_id="request-clear",
        turn_id="turn-clear",
    )

    await store.clear("s1")
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute(
            "SELECT count(*) FROM messages WHERE session_id='s1'"
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT count(*) FROM companion_ingress_outbox WHERE session_id='s1'"
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT status FROM companion_session_owners WHERE session_id='s1'"
        ).fetchone() == ("tombstoned",)
        assert db.execute(
            """SELECT route_version,status,target_epoch
               FROM companion_projection_routes
               WHERE profile_id='profile-a' AND profile_generation=1"""
        ).fetchone() == (2, "tombstoned", 1)
    await store.close()


@pytest.mark.asyncio
async def test_companion_projection_table_checks_reject_unsafe_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    await run_migrations(db_path)
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO sessions(id,created_at,metadata) VALUES ('s1',1,'{}')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO messages(
                     session_id,role,content,created_at,projection_kind,
                     context_visibility,projection_event_id
                   ) VALUES (
                     's1','assistant','unsafe',1,'companion_event',
                     'conversation','event-1'
                   )"""
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO messages(
                     session_id,role,content,created_at,projection_kind,
                     context_visibility,projection_event_id,projection_payload_hash
                   ) VALUES (
                     's1','assistant','unsafe',1,'companion_event',
                     'exclude','event-1','hash'
                   )"""
            )


@pytest.mark.asyncio
async def test_task12_generic_projection_api_and_workflow_compatibility(
    tmp_path: Path,
) -> None:
    store = SessionDB(tmp_path / "state.db")
    owner = TrustedCompanionOwner("profile-a", 1, 1)
    await store.initialize()
    await store.ensure_session("s1")
    await store.bind_session_owner_if_absent("s1", owner)
    await store.set_companion_default_route("profile-a", 1, 1, "s1")

    workflow_id = await store.append_projection_if_epoch(
        "s1",
        "assistant",
        "workflow",
        expected_epoch=0,
        projection_event_id="workflow-event-1",
        projection_kind="workflow_progress",
    )
    companion_id = await store.append_projection_if_epoch(
        "s1",
        "assistant",
        "companion",
        expected_epoch=0,
        projection_event_id="companion-event-1",
        projection_kind="companion_event",
        trusted_owner=owner,
        route_version=1,
        projection_payload_hash="payload-1",
    )
    assert workflow_id is not None
    assert companion_id is not None
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute(
            """SELECT workflow_event_id,projection_event_id
               FROM messages WHERE id=?""",
            (workflow_id,),
        ).fetchone() == ("workflow-event-1", "workflow-event-1")
        assert db.execute(
            """SELECT workflow_event_id,projection_event_id,context_visibility
               FROM messages WHERE id=?""",
            (companion_id,),
        ).fetchone() == (None, "companion-event-1", "exclude")
    await store.close()


@pytest.mark.asyncio
async def test_task12_relocation_is_single_row_owner_hash_and_route_cas(
    tmp_path: Path,
) -> None:
    store = SessionDB(tmp_path / "state.db")
    owner = TrustedCompanionOwner("profile-a", 1, 1)
    await store.initialize()
    for session_id in ("s1", "s2"):
        await store.ensure_session(session_id)
        await store.bind_session_owner_if_absent(session_id, owner)
    await store.set_companion_default_route("profile-a", 1, 1, "s1")
    message_id = await store.append_companion_projection_if_epoch(
        "s1",
        "notification",
        trusted_owner=owner,
        expected_epoch=0,
        route_version=1,
        projection_event_id="event-1",
        projection_payload_hash="payload-1",
    )
    await store.set_companion_default_route(
        "profile-a", 1, 1, "s2", expected_route_version=1
    )

    async def _move() -> dict[str, object]:
        return await store.relocate_projection_if_epoch(
            trusted_owner=owner,
            projection_event_id="event-1",
            expected_payload_hash="payload-1",
            expected_session_id="s1",
            expected_epoch=0,
            expected_route_version=1,
            new_session_id="s2",
            new_epoch=0,
            new_route_version=2,
        )

    outcomes = await asyncio.gather(_move(), _move())
    assert {str(item["status"]) for item in outcomes} == {
        "relocated",
        "already_relocated",
    }
    with pytest.raises(RuntimeError, match="payload_hash_conflict"):
        await store.relocate_projection_if_epoch(
            trusted_owner=owner,
            projection_event_id="event-1",
            expected_payload_hash="different",
            expected_session_id="s2",
            expected_epoch=0,
            expected_route_version=2,
            new_session_id="s1",
            new_epoch=0,
            new_route_version=3,
        )
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute(
            """SELECT id,session_id,projection_route_version
               FROM messages
               WHERE projection_owner_id='profile-a'
                 AND projection_owner_generation=1
                 AND projection_event_id='event-1'"""
        ).fetchall() == [(message_id, "s2", 2)]
    await store.close()


@pytest.mark.asyncio
async def test_task12_redaction_is_fixed_idempotent_and_wrong_hash_fails_closed(
    tmp_path: Path,
) -> None:
    store = SessionDB(tmp_path / "state.db")
    owner = TrustedCompanionOwner("profile-a", 1, 1)
    await store.initialize()
    await store.ensure_session("s1")
    await store.bind_session_owner_if_absent("s1", owner)
    await store.set_companion_default_route("profile-a", 1, 1, "s1")
    await store.append_companion_projection_if_epoch(
        "s1",
        "private summary",
        trusted_owner=owner,
        expected_epoch=0,
        route_version=1,
        projection_event_id="event-redact",
        projection_payload_hash="private-hash",
    )

    receipt = await store.redact_projection_if_hash(
        owner=owner,
        event_id="event-redact",
        expected_payload_hash="private-hash",
        tombstone_payload_hash=COMPANION_REDACTION_TOMBSTONE_HASH,
        redaction_id="redaction-1",
        redaction_version=1,
    )
    replay = await store.redact_projection_if_hash(
        owner=owner,
        event_id="event-redact",
        expected_payload_hash="private-hash",
        tombstone_payload_hash=COMPANION_REDACTION_TOMBSTONE_HASH,
        redaction_id="redaction-1",
        redaction_version=1,
    )
    assert receipt == replay
    with pytest.raises(RuntimeError, match="tombstone_hash_conflict"):
        await store.redact_projection_if_hash(
            owner=owner,
            event_id="event-redact",
            expected_payload_hash="private-hash",
            tombstone_payload_hash="caller-controlled",
            redaction_id="redaction-2",
            redaction_version=2,
        )
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute(
            """SELECT content,projection_payload_hash
               FROM messages WHERE projection_event_id='event-redact'"""
        ).fetchone() == (
            COMPANION_REDACTION_TOMBSTONE,
            COMPANION_REDACTION_TOMBSTONE_HASH,
        )
    await store.close()


@pytest.mark.asyncio
async def test_task12_absent_reconcile_accepts_only_typed_current_fact_and_never_revives(
    tmp_path: Path,
) -> None:
    store = SessionDB(tmp_path / "state.db")
    owner = TrustedCompanionOwner("profile-a", 1, 1)
    await store.initialize()
    for session_id in ("s1", "s2"):
        await store.ensure_session(session_id)
        await store.bind_session_owner_if_absent(session_id, owner)
    await store.set_companion_default_route("profile-a", 1, 1, "s1")
    await store.append_companion_projection_if_epoch(
        "s1",
        "private summary",
        trusted_owner=owner,
        expected_epoch=0,
        route_version=1,
        projection_event_id="event-forgotten",
        projection_payload_hash="private-hash",
    )
    await store.clear("s1")
    route = await store.set_companion_default_route(
        "profile-a", 1, 1, "s2", expected_route_version=2
    )
    trusted_route = TrustedCompanionProjectionRoute(
        owner=owner,
        session_id="s2",
        projection_epoch=0,
        route_version=int(route["route_version"]),
    )
    current = CurrentCompanionProjection(
        owner=owner,
        event_id="event-forgotten",
        payload_hash=COMPANION_REDACTION_TOMBSTONE_HASH,
        content=COMPANION_REDACTION_TOMBSTONE,
        status="redacted",
        redaction_id="redaction-after-clear",
        redaction_version=1,
        redacted_from_payload_hash="private-hash",
    )
    inserted = await store.append_current_projection_if_absent(current, trusted_route)
    assert inserted["status"] == "inserted"
    replay = await store.append_current_projection_if_absent(current, trusted_route)
    assert replay["status"] == "already_exists"
    with pytest.raises(TypeError, match="CurrentCompanionProjection"):
        await store.append_current_projection_if_absent(  # type: ignore[arg-type]
            {"owner": owner, "event_id": "event-forgotten", "content": "private"},
            trusted_route,
        )
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute(
            """SELECT session_id,content,projection_payload_hash
               FROM messages WHERE projection_event_id='event-forgotten'"""
        ).fetchall() == [
            (
                "s2",
                COMPANION_REDACTION_TOMBSTONE,
                COMPANION_REDACTION_TOMBSTONE_HASH,
            )
        ]
        assert db.execute(
            """SELECT redaction_outbox_id
               FROM companion_projection_redaction_receipts
               WHERE projection_event_id='event-forgotten'"""
        ).fetchall() == [("redaction-after-clear",)]
    await store.close()


@pytest.mark.asyncio
async def test_task12_initialize_repairs_excluded_embeddings_vectors_and_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _without_vec(_self: SessionDB) -> bool:
        return False

    monkeypatch.setattr(SessionDB, "_try_init_vec", _without_vec)
    db_path = tmp_path / "state.db"
    store = SessionDB(db_path)
    await store.initialize()
    await store.ensure_session("s1")
    visible = await store.append_message("s1", "user", "visible")
    excluded = await store.append_message(
        "s1",
        "assistant",
        "excluded",
        workflow_event_id="excluded-event",
        projection_kind="workflow_progress",
    )
    await store.close()
    with sqlite3.connect(db_path) as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS messages_chunks(
                 id INTEGER PRIMARY KEY,
                 message_id INTEGER NOT NULL,
                 chunk_index INTEGER NOT NULL,
                 text TEXT NOT NULL,
                 embedding BLOB,
                 created_at REAL NOT NULL
               )"""
        )
        db.execute(
            "CREATE TABLE messages_vec(message_id INTEGER PRIMARY KEY,embedding BLOB)"
        )
        db.execute(
            "UPDATE messages SET embedding=X'01' WHERE id IN (?,?)",
            (visible, excluded),
        )
        db.execute(
            """INSERT INTO messages_chunks(
                 message_id,chunk_index,text,embedding,created_at
               ) VALUES (?,0,'visible',X'01',1),(?,0,'excluded',X'01',1)""",
            (visible, excluded),
        )
        db.execute(
            "INSERT INTO messages_vec(message_id,embedding) VALUES (?,X'01'),(?,X'01')",
            (visible, excluded),
        )
        db.commit()

    repaired = SessionDB(db_path)
    await repaired.initialize()
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT id,embedding FROM messages ORDER BY id"
        ).fetchall() == [(visible, b"\x01"), (excluded, None)]
        assert db.execute(
            "SELECT message_id FROM messages_chunks ORDER BY message_id"
        ).fetchall() == [(visible,)]
        assert db.execute(
            "SELECT message_id FROM messages_vec ORDER BY message_id"
        ).fetchall() == [(visible,)]
    await repaired.close()


@pytest.mark.asyncio
async def test_task12_route_outbox_claim_retry_and_settle_are_attempt_fenced(
    tmp_path: Path,
) -> None:
    store = SessionDB(tmp_path / "state.db")
    owner = TrustedCompanionOwner("profile-a", 1, 1)
    await store.initialize()
    await store.ensure_session("s1")
    await store.bind_session_owner_if_absent("s1", owner)
    await store.set_companion_default_route("profile-a", 1, 1, "s1")

    first = await store.claim_companion_projection_route_outbox(
        "worker-a", 60, profile_id="profile-a", profile_generation=1
    )
    assert first is not None
    assert first["attempt"] == 1
    assert first["payload"]["event_kind"] == "route_changed"
    retried = await store.settle_companion_projection_route_outbox(
        first["outbox_id"], 1, retry_at=0
    )
    assert retried["status"] == "pending"
    second = await store.claim_companion_projection_route_outbox("worker-b", 60)
    assert second is not None
    assert second["outbox_id"] == first["outbox_id"]
    assert second["attempt"] == 2
    with pytest.raises(RuntimeError, match="attempt_stale"):
        await store.settle_companion_projection_route_outbox(
            second["outbox_id"], 1, delivered=True
        )
    settled = await store.settle_companion_projection_route_outbox(
        second["outbox_id"], 2, delivered=True
    )
    assert settled["status"] == "delivered"
    assert (
        await store.settle_companion_projection_route_outbox(
            second["outbox_id"], 2, delivered=True
        )
    )["status"] == "delivered"
    assert await store.claim_companion_projection_route_outbox("worker-c", 60) is None
    await store.close()

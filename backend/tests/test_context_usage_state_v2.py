from __future__ import annotations

import sqlite3
import shutil

import pytest
import pytest_asyncio

from deskpet.agent.context_usage import ContextUsageSampleConflict
from deskpet.memory.migrator import (
    DEFAULT_MIGRATIONS_DIR,
    MigrationError,
    TARGET_SCHEMA_VERSION,
    run_migrations,
)
from deskpet.memory.session_db import SessionDB


@pytest_asyncio.fixture
async def context_db(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    await db.initialize()
    yield db
    await db.close()


def provider_sample(
    *,
    session_id: str,
    source_event_id: str,
    completed_at: float,
    tokens: int,
    binding_epoch: int = 0,
    model_id: str = "kimi-k3",
) -> dict:
    return {
        "session_id": session_id,
        "source_event_id": source_event_id,
        "run_id": "root-1",
        "request_id": "request-1",
        "attempt_id": source_event_id,
        "event_type": "provider_attempt",
        "binding_epoch": binding_epoch,
        "provider_id": "kimi",
        "model_id": model_id,
        "tokens_after": tokens,
        "prompt_tokens": tokens,
        "completion_tokens": 7,
        "cached_tokens": 3,
        "context_window": 131_072,
        "effective_ceiling": 124_518,
        "completed_at": completed_at,
        "created_at": completed_at,
    }


@pytest.mark.asyncio
async def test_provider_binding_and_public_context_state_advance_atomically(context_db):
    bound = await context_db.set_session_provider_binding(
        "session-binding",
        "kimi",
        "kimi-k3",
        provider_incarnation_id="inc-1",
        provider_config_revision=3,
        expected_binding_epoch=0,
    )
    state = await context_db.get_context_usage_state("session-binding")

    assert bound["binding_epoch"] == 1
    assert state["source"] == "binding_only"
    assert state["binding_epoch"] == 1
    assert state["provider_id"] == "kimi"
    assert state["model_id"] == "kimi-k3"
    assert state["has_measurement"] is False

    cleared = await context_db.set_session_provider_binding(
        "session-binding",
        None,
        None,
        expected_binding_epoch=1,
    )
    cleared_state = await context_db.get_context_usage_state("session-binding")

    assert cleared["binding_epoch"] == 2
    assert cleared_state["binding_epoch"] == 2
    assert cleared_state["provider_id"] is None
    assert cleared_state["model_id"] is None
    assert cleared_state["has_measurement"] is False


@pytest.mark.asyncio
async def test_v24_migration_is_atomic_authority(tmp_path):
    path = tmp_path / "state.db"
    applied = await run_migrations(path)

    assert applied[-2:] == [
        "018_message_archive_projection_v26.sql",
        "019_provider_fault_correlation_v27.sql",
    ]
    assert TARGET_SCHEMA_VERSION == 27
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone() == (27,)
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(session_context_usage_history)"
            )
        }
        assert {
            "source_event_id",
            "payload_hash",
            "based_on_sample_id",
            "binding_epoch",
            "provider_id",
            "model_id",
        }.issubset(columns)
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='session_context_usage_state_v2'"
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?",
            ("016_context_usage_authority_v24.sql",),
        ).fetchone() == (1,)


@pytest.mark.asyncio
async def test_v24_ddl_marker_and_version_roll_back_together(tmp_path):
    path = tmp_path / "state.db"
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for source in sorted(DEFAULT_MIGRATIONS_DIR.glob("*.sql")):
        if source.name in {
            "016_context_usage_authority_v24.sql",
            "017_provider_workload_audit_v25.sql",
            "018_message_archive_projection_v26.sql",
            "019_provider_fault_correlation_v27.sql",
        }:
            continue
        shutil.copyfile(source, migrations / source.name)
    await run_migrations(path, migrations_dir=migrations)
    (migrations / "016_context_usage_authority_v24.sql").write_text(
        "CREATE TABLE partial_context_v24(id TEXT PRIMARY KEY);\n"
        "THIS IS NOT SQL;\n",
        encoding="utf-8",
    )

    with pytest.raises(MigrationError):
        await run_migrations(path, migrations_dir=migrations)

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone() == (23,)
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='partial_context_v24'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?",
            ("016_context_usage_authority_v24.sql",),
        ).fetchone() is None


@pytest.mark.asyncio
async def test_provider_compaction_provider_reducer_and_restart(context_db):
    sid = "context-v2-session"
    first_id = await context_db.record_context_usage_sample(
        provider_sample(
            session_id=sid,
            source_event_id="provider:1",
            completed_at=10.0,
            tokens=8_000,
        )
    )
    await context_db.record_context_usage_sample(
        {
            "session_id": sid,
            "source_event_id": "compaction:1",
            "run_id": "root-1",
            "request_id": "request-1",
            "attempt_id": "compaction-1",
            "event_type": "compaction",
            "binding_epoch": 0,
            "based_on_sample_id": first_id,
            "provider_id": "kimi",
            "model_id": "kimi-k3",
            "tokens_before": 8_000,
            "tokens_after": 2_000,
            "context_window": 131_072,
            "effective_ceiling": 124_518,
            "completed_at": 20.0,
            "created_at": 20.0,
        }
    )
    compacted = await context_db.get_context_usage_state(sid)
    assert compacted["source"] == "compacted"
    assert compacted["prompt_tokens"] == 2_000
    assert compacted["based_on_sample_id"] == first_id

    await context_db.record_context_usage_sample(
        provider_sample(
            session_id=sid,
            source_event_id="provider:2",
            completed_at=30.0,
            tokens=9_000,
        )
    )
    measured = await context_db.get_context_usage_state(sid)
    assert measured["source"] == "measured"
    assert measured["prompt_tokens"] == 9_000
    assert measured["version"] > compacted["version"]

    restarted = SessionDB(context_db._db_path)
    await restarted.initialize()
    try:
        assert await restarted.get_context_usage_state(sid) == measured
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_compaction_with_stale_lineage_stays_history_only(context_db):
    sid = "context-concurrent-roots"
    stale_id = await context_db.record_context_usage_sample(
        provider_sample(
            session_id=sid,
            source_event_id="provider:root-a",
            completed_at=10.0,
            tokens=8_000,
        )
    )
    current_id = await context_db.record_context_usage_sample(
        provider_sample(
            session_id=sid,
            source_event_id="provider:root-b",
            completed_at=15.0,
            tokens=9_000,
        )
    )
    await context_db.record_context_usage_sample(
        {
            "session_id": sid,
            "source_event_id": "compaction:root-a",
            "event_type": "compaction",
            "binding_epoch": 0,
            "based_on_sample_id": stale_id,
            "provider_id": "kimi",
            "model_id": "kimi-k3",
            "tokens_before": 8_000,
            "tokens_after": 2_000,
            "context_window": 131_072,
            "effective_ceiling": 124_518,
            "completed_at": 20.0,
            "created_at": 20.0,
        }
    )

    state = await context_db.get_context_usage_state(sid)
    history = await context_db.list_context_usage_history(sid)
    assert state["sample_id"] == current_id
    assert state["source"] == "measured"
    assert len(history) == 3
    assert history[-1]["based_on_sample_id"] == stale_id


@pytest.mark.asyncio
async def test_duplicate_conflict_and_same_timestamp_order(context_db):
    sid = "context-order-session"
    sample = provider_sample(
        session_id=sid,
        source_event_id="provider:b",
        completed_at=10.0,
        tokens=20,
    )
    await context_db.record_context_usage_sample(sample)
    version = (await context_db.get_context_usage_state(sid))["version"]
    await context_db.record_context_usage_sample(
        {**sample, "completed_at": 12.0, "created_at": 12.0}
    )
    assert (await context_db.get_context_usage_state(sid))["version"] == version
    with pytest.raises(
        ContextUsageSampleConflict, match="context_usage_sample_conflict"
    ):
        await context_db.record_context_usage_sample({**sample, "tokens_after": 21})

    await context_db.record_context_usage_sample(
        provider_sample(
            session_id=sid,
            source_event_id="provider:a",
            completed_at=10.0,
            tokens=99,
        )
    )
    state = await context_db.get_context_usage_state(sid)
    assert state["prompt_tokens"] == 20
    history = await context_db.list_context_usage_history(sid)
    assert len(history) == 2
    exact = await context_db.get_context_usage_sample(
        sid, history[-1]["sample_id"]
    )
    assert exact == history[-1]
    assert await context_db.get_context_usage_sample(sid, "missing") is None


@pytest.mark.asyncio
async def test_binding_only_stale_and_old_epoch_late_sample(context_db):
    sid = "context-binding-session"
    initial = await context_db.get_context_usage_state(sid)
    assert initial["source"] == "binding_only"
    assert initial["has_measurement"] is False
    assert initial["context_window"] == 0

    stale = await context_db.set_context_usage_binding_state(
        sid,
        binding_epoch=4,
        provider_id="kimi",
        model_id="kimi-k3",
        availability="unavailable",
        updated_at=40.0,
    )
    assert stale["availability"] == "unavailable"
    assert stale["model"] == "kimi-k3"

    await context_db.record_context_usage_sample(
        provider_sample(
            session_id=sid,
            source_event_id="late-old-epoch",
            completed_at=50.0,
            tokens=77,
            binding_epoch=3,
            model_id="glm-5.2",
        )
    )
    after = await context_db.get_context_usage_state(sid)
    assert after["version"] == stale["version"]
    assert after["source"] == "binding_only"
    assert after["model"] == "kimi-k3"
    assert after["prompt_tokens"] == 0
    assert len(await context_db.list_context_usage_history(sid)) == 1


@pytest.mark.asyncio
async def test_legacy_rebuild_scans_beyond_old_limit(context_db):
    sid = "context-legacy-rebuild"
    with sqlite3.connect(context_db._db_path) as conn:
        for index in range(2_101):
            sample_id = f"legacy-{index:04d}"
            conn.execute(
                """INSERT INTO session_context_usage_history(
                sample_id,session_id,source_event_id,payload_hash,event_type,
                tokens_after,prompt_tokens,completion_tokens,cached_tokens,
                context_window,effective_ceiling,estimate_method,binding_epoch,
                metadata_json,completed_at,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sample_id,
                    sid,
                    f"legacy:{sample_id}",
                    f"legacy:{sample_id}",
                    "provider_attempt",
                    index,
                    index,
                    0,
                    0,
                    8_192,
                    7_782,
                    "legacy",
                    0,
                    '{"model":"legacy-model"}',
                    float(index),
                    float(index),
                ),
            )
        conn.commit()

    state = await context_db.get_context_usage_state(sid)
    assert state["prompt_tokens"] == 2_100
    assert state["model"] == "legacy-model"
    assert state["has_measurement"] is True

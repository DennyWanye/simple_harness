from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from deskpet.memory.migrator import DEFAULT_MIGRATIONS_DIR, run_migrations
from deskpet.memory.schema import (
    StartupCompositionMode,
    StartupEpoch,
    _write_bootstrap_marker,
    inspect_startup_epoch,
)


def test_absent_database_is_fresh_only_in_approved_lane(tmp_path: Path) -> None:
    path = tmp_path / "state.db"

    accepted = inspect_startup_epoch(path, approved_fresh_lane=True)
    rejected = inspect_startup_epoch(path, approved_fresh_lane=False)

    assert accepted.epoch is StartupEpoch.FRESH
    assert accepted.composition_mode is StartupCompositionMode.HUMAN
    assert rejected.epoch is StartupEpoch.INVALID
    assert not path.exists()


def test_valid_empty_v0_is_fresh_but_ambiguous_v0_fails_closed(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty.db"
    with sqlite3.connect(empty):
        pass
    assert (
        inspect_startup_epoch(empty, approved_fresh_lane=True).epoch
        is StartupEpoch.FRESH
    )

    ambiguous = tmp_path / "ambiguous.db"
    with sqlite3.connect(ambiguous) as db:
        db.execute("CREATE TABLE unknown_authority(id TEXT PRIMARY KEY)")
        db.commit()
    decision = inspect_startup_epoch(ambiguous, approved_fresh_lane=True)
    assert decision.epoch is StartupEpoch.INVALID
    assert decision.reason_code == "human_memory_ambiguous_v0_database"


@pytest.mark.asyncio
async def test_v34_legacy_and_future_database_are_not_humanized(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy.db"
    await run_migrations(legacy)
    legacy_decision = inspect_startup_epoch(
        legacy, approved_fresh_lane=True
    )
    assert legacy_decision.epoch is StartupEpoch.LEGACY
    assert legacy_decision.user_version == 34

    future = tmp_path / "future.db"
    with sqlite3.connect(future) as db:
        db.execute("PRAGMA user_version=43")
        db.commit()
    future_decision = inspect_startup_epoch(
        future, approved_fresh_lane=True
    )
    assert future_decision.epoch is StartupEpoch.FUTURE
    assert future_decision.reason_code == "human_memory_future_epoch_unsupported"


@pytest.mark.asyncio
async def test_valid_v38_human_database_is_resumable(tmp_path: Path) -> None:
    path = tmp_path / "human.db"
    migrations = tmp_path / "migrations-v38"
    migrations.mkdir()
    for source in sorted(DEFAULT_MIGRATIONS_DIR.glob("*.sql")):
        if source.name.startswith("031_"):
            break
        shutil.copy2(source, migrations / source.name)

    _write_bootstrap_marker(path)
    await run_migrations(
        path,
        migrations_dir=migrations,
        include_human_memory_program=True,
    )

    decision = inspect_startup_epoch(path, approved_fresh_lane=False)
    assert decision.epoch is StartupEpoch.HUMAN_RESUME
    assert decision.composition_mode is StartupCompositionMode.HUMAN
    assert decision.user_version == 38


def test_human_marker_without_bootstrap_is_invalid(tmp_path: Path) -> None:
    path = tmp_path / "mixed.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE human_memory_program_marker(singleton INTEGER PRIMARY KEY)"
        )
        db.execute("PRAGMA user_version=35")
        db.commit()

    decision = inspect_startup_epoch(path, approved_fresh_lane=True)
    assert decision.epoch is StartupEpoch.INVALID
    assert decision.reason_code == "human_memory_marker_chain_invalid"

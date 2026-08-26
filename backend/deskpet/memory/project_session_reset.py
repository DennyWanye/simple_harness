# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Crash-resumable v33 reset of pre-upgrade conversation-owned storage."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Callable, Iterable

import aiosqlite


RESET_POLICY_VERSION = 1
_STATE_EMPTY_TABLES = (
    "code_session_provider",
    "code_sessions",
    "code_todos",
    "companion_ingress_outbox",
    "companion_owner_scope_versions",
    "companion_projection_redaction_receipts",
    "companion_projection_route_outbox",
    "companion_projection_routes",
    "companion_session_owners",
    "goal_tasks",
    "memory_eval_run",
    "memory_identity_bindings",
    "memory_qa_set",
    "memory_session_identities",
    "memory_user_bindings",
    "memory_user_feedback",
    "memory_users",
    "messages",
    "messages_archive",
    "messages_chunks",
    "ppt_outline_history",
    "product_memory_outbox",
    "project_run_admissions",
    "projects",
    "provider_workload_audit",
    "provider_binding_reconcile_marker",
    "sdk_context_public_snapshots",
    "sdk_context_source_bindings",
    "sdk_context_sources",
    "sdk_provider_attempt_audit",
    "sdk_provider_projection_cursors",
    "session_catalog_entries",
    "session_context_segments",
    "session_context_snapshots",
    "session_context_usage_history",
    "session_context_usage_state_v2",
    "session_creation_receipts",
    "session_delivery_state",
    "session_goals",
    "session_handoff_consumptions",
    "session_plans",
    "session_project_bindings",
    "session_titles",
    "sessions",
    "state_db_identity",
    "supervisor_hints",
    "workspace_state",
)

_CAPABILITY_RUN_PREFIXES = (
    "capability_run_catalog_",
    "capability_runtime_",
    "capability_snapshot_lease_",
    "capability_lease_runtime_",
)
_WORKFLOW_PRESERVED_TABLES = frozenset(
    {
        "workflow_schema_migrations",
        "execution_runtime_state",
        # These immutable receipts/materials are the durable source for an
        # installed Skill/Plugin candidate, not conversation history.
        "execution_candidate_draft_receipts",
        "execution_candidate_draft_materials",
    }
)

# Companion contains durable Skills/Plugins/growth governance beside ephemeral
# conversation Runs.  Reset only the exact Run-owned projection tables; an
# inverse "everything except ..." selector can destroy global capabilities.
_COMPANION_RUN_TABLES = frozenset(
    {
        "candidate_evidence",
        "companion_run_bindings",
        "delegated_task_grants",
        "growth_events",
        "job_execution_waits",
        "jobs",
        "notifications",
        "outbox",
        "owner_memory_read_scopes",
        "preference_evidence",
        "preference_turn_decision_receipts",
        "preferences",
        "run_growth_dependency_evidence",
        "run_growth_dependency_items",
        "run_growth_snapshots",
    }
)

# Executable authority for exactly what the one-time reset removes or keeps.
# Tests import this manifest so documentation, cleanup code, and assertions
# cannot silently describe different boundaries.
RESET_MANIFEST = {
    "state.db": {
        "empty_tables": _STATE_EMPTY_TABLES,
        "optional_empty_tables": ("facts",),
        "drop_tables": (
            "project_session_backfill_outcomes",
            "project_session_backfill_state",
        ),
        "preserve": "global configuration and schema authorities",
    },
    "workflow.db": {
        "delete_prefixes": (
            "execution_",
            "workflow_",
            "trace_",
            *_CAPABILITY_RUN_PREFIXES,
        ),
        "delete_tables": ("task_grants",),
        "preserve_tables": tuple(sorted(_WORKFLOW_PRESERVED_TABLES)),
    },
    "companion.db": {
        "delete_tables": tuple(sorted(_COMPANION_RUN_TABLES)),
        "preserve": "profiles, reminders, Skills/Plugins, candidates, and growth governance",
    },
    "sdk-product-state.db": {
        "delete_prefixes": _CAPABILITY_RUN_PREFIXES,
        "delete_tables": ("authorization_sagas", "task_grants"),
    },
    "delete_databases": (
        "memory.db",
        "execution.db",
        "simple-harness-sdk/execution-v6.sqlite3",
        "simple-harness-sdk/execution-v1.sqlite3",
    ),
    "delete_directories": ("workflows/blobs",),
}


class LegacySessionResetError(RuntimeError):
    """The reset is incomplete; product ingress must remain closed."""


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _regular_file_or_missing(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise LegacySessionResetError(f"unsafe_reset_target:{path.name}")


def _unlink_database(path: Path, *, extra_suffixes: Iterable[str] = ()) -> None:
    targets = [path, Path(f"{path}-wal"), Path(f"{path}-shm")]
    targets.extend(Path(f"{path}{suffix}") for suffix in extra_suffixes)
    for target in targets:
        _regular_file_or_missing(target)
    for target in targets:
        if target.exists():
            target.unlink()


def _clear_selected_tables(
    path: Path,
    *,
    select: Callable[[str], bool],
) -> None:
    if not path.exists():
        return
    _regular_file_or_missing(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("PRAGMA secure_delete=ON")
        tables = [
            str(row[0])
            for row in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            if select(str(row[0]))
        ]
        trigger_rows = [
            (str(row[0]), str(row[1]))
            for row in db.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name IN (%s) AND sql IS NOT NULL ORDER BY name"
                % ",".join("?" for _ in tables),
                tables,
            )
        ] if tables else []
        db.execute("BEGIN IMMEDIATE")
        try:
            for trigger, _ in trigger_rows:
                db.execute(f"DROP TRIGGER {_quote_identifier(trigger)}")
            for table in tables:
                db.execute(f"DELETE FROM {_quote_identifier(table)}")
            for _, ddl in trigger_rows:
                db.execute(ddl)
            db.commit()
        except Exception:
            db.rollback()
            raise
        violations = list(db.execute("PRAGMA foreign_key_check"))
        if violations:
            raise LegacySessionResetError(
                f"external_foreign_key_check_failed:{path.name}:{violations[0][0]}"
            )
        db.execute("VACUUM")


def _clear_external_stores(data_dir: Path) -> None:
    _clear_selected_tables(
        data_dir / "state.db",
        select=lambda name: name in RESET_MANIFEST["state.db"]["optional_empty_tables"],
    )
    _clear_selected_tables(
        data_dir / "workflow.db",
        select=lambda name: (
            name not in _WORKFLOW_PRESERVED_TABLES
            and (
                name.startswith("execution_")
                or name.startswith("workflow_")
                or name.startswith("trace_")
                or name.startswith(_CAPABILITY_RUN_PREFIXES)
                or name == "task_grants"
            )
        ),
    )
    _clear_selected_tables(
        data_dir / "companion.db",
        select=lambda name: name in _COMPANION_RUN_TABLES,
    )
    _clear_selected_tables(
        data_dir / "sdk-product-state.db",
        select=lambda name: (
            name in {"authorization_sagas", "task_grants"}
            or name.startswith(_CAPABILITY_RUN_PREFIXES)
        ),
    )
    _unlink_database(
        data_dir / "memory.db",
        extra_suffixes=(".writer.lock",),
    )
    _unlink_database(data_dir / "execution.db")
    _unlink_database(data_dir / "simple-harness-sdk" / "execution-v6.sqlite3")
    _unlink_database(data_dir / "simple-harness-sdk" / "execution-v1.sqlite3")

    workflow_blobs = data_dir.parent / "workflows" / "blobs"
    if workflow_blobs.exists():
        if workflow_blobs.is_symlink() or not workflow_blobs.is_dir():
            raise LegacySessionResetError("unsafe_reset_target:workflow_blobs")
        shutil.rmtree(workflow_blobs)


async def _read_phase(db_path: Path) -> str | None:
    async with aiosqlite.connect(db_path) as db:
        row = await (
            await db.execute(
                "SELECT phase FROM legacy_session_reset_state "
                "WHERE singleton=1 AND policy_version=?",
                (RESET_POLICY_VERSION,),
            )
        ).fetchone()
    return None if row is None else str(row[0])


async def run_legacy_session_reset(
    db_path: str | Path,
    *,
    backup_path: str | Path | None = None,
    fault_inject: Callable[[str], None] | None = None,
) -> None:
    """Clear external conversation stores after the v33 state transaction."""

    path = Path(db_path)
    phase = await _read_phase(path)
    if phase in {None, "completed", "external_stores_cleared"}:
        return
    if phase not in {"state_cleared", "failed"}:
        raise LegacySessionResetError(f"legacy_session_reset_phase_invalid:{phase}")

    async with aiosqlite.connect(path) as db:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            "UPDATE legacy_session_reset_state SET phase='state_cleared',"
            "backup_path=COALESCE(backup_path,?),updated_at=?,error_code=NULL "
            "WHERE singleton=1",
            (
                os.fspath(Path(backup_path)) if backup_path is not None else None,
                time.time(),
            ),
        )
        await db.commit()

    try:
        if fault_inject:
            fault_inject("before_external_store_reset")
        await asyncio.to_thread(_clear_external_stores, path.parent)
        if fault_inject:
            fault_inject("after_external_store_reset")
        async with aiosqlite.connect(path) as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "UPDATE legacy_session_reset_state "
                "SET phase='external_stores_cleared',updated_at=?,error_code=NULL "
                "WHERE singleton=1",
                (time.time(),),
            )
            await db.commit()
    except Exception as exc:
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "UPDATE legacy_session_reset_state SET phase='failed',"
                "updated_at=?,error_code=? WHERE singleton=1",
                (time.time(), type(exc).__name__[:80]),
            )
            await db.commit()
        raise


def _clear_optional_vectors_and_vacuum(path: Path) -> None:
    with sqlite3.connect(path) as db:
        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages_vec'"
        ).fetchone()
        if table is not None:
            try:
                import sqlite_vec  # type: ignore

                db.enable_load_extension(True)
                sqlite_vec.load(db)
                db.enable_load_extension(False)
                db.execute("DELETE FROM messages_vec")
                db.commit()
            except Exception as exc:
                raise LegacySessionResetError("messages_vec_reset_failed") from exc
        db.execute("VACUUM")


def _delete_state_backups(path: Path) -> None:
    for target in sorted(path.parent.glob(f"{path.name}.bak.*")):
        _regular_file_or_missing(target)
    for target in sorted(path.parent.glob(f"{path.name}.bak.*")):
        target.unlink()


async def finalize_legacy_session_reset(
    db_path: str | Path,
    *,
    fault_inject: Callable[[str], None] | None = None,
) -> None:
    """Clear sqlite-vec, verify the empty state, remove backups, and open v33."""

    path = Path(db_path)
    phase = await _read_phase(path)
    if phase in {None, "completed"}:
        return
    if phase != "external_stores_cleared":
        raise LegacySessionResetError(f"legacy_session_reset_incomplete:{phase}")
    if fault_inject:
        fault_inject("before_vector_reset")
    await asyncio.to_thread(_clear_optional_vectors_and_vacuum, path)
    if fault_inject:
        fault_inject("after_vector_reset")

    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        violations = await (await db.execute("PRAGMA foreign_key_check")).fetchall()
        if violations:
            raise LegacySessionResetError(
                f"state_foreign_key_check_failed:{violations[0][0]}"
            )
        for table in _STATE_EMPTY_TABLES:
            count = int(
                (await (await db.execute(
                    f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                )).fetchone())[0]
            )
            if count:
                raise LegacySessionResetError(f"legacy_rows_remaining:{table}:{count}")

    if fault_inject:
        fault_inject("before_backup_cleanup")
    await asyncio.to_thread(_delete_state_backups, path)
    if fault_inject:
        fault_inject("after_backup_cleanup")
    if fault_inject:
        fault_inject("before_completion_commit")
    async with aiosqlite.connect(path) as db:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            "UPDATE legacy_session_reset_state SET phase='completed',"
            "completed_at=?,updated_at=?,error_code=NULL WHERE singleton=1",
            (time.time(), time.time()),
        )
        await db.commit()
    if fault_inject:
        fault_inject("after_completion_commit")


__all__ = (
    "LegacySessionResetError",
    "RESET_MANIFEST",
    "RESET_POLICY_VERSION",
    "finalize_legacy_session_reset",
    "run_legacy_session_reset",
)

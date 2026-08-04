"""Schema bootstrap and integrity checks for ``companion.db``."""

from __future__ import annotations

import sqlite3
import hashlib
from pathlib import Path

from .contracts import DETAIL_VISIBLE_TABLES

COMPANION_SCHEMA_VERSION = 7
MIGRATION_ROOT = Path(__file__).with_name("migrations")
MIGRATION_RESOURCES = {
    1: MIGRATION_ROOT / "001_companion_v1.sql",
    2: MIGRATION_ROOT / "002_owner_memory_read_scope_v2.sql",
    3: MIGRATION_ROOT / "003_candidate_build_saga_v3.sql",
    4: MIGRATION_ROOT / "004_job_execution_wait_v4.sql",
    5: MIGRATION_ROOT / "005_preference_turn_decision_receipt_v5.sql",
    6: MIGRATION_ROOT / "006_safe_static_evaluation_v6.sql",
    # 2026-08-04 Workbench UI 改版: companion_action 窗口标签迁 main
    # (profile_control_leases CHECK 重建, behavior-contract B5)。
    7: MIGRATION_ROOT / "007_companion_window_label_main_v7.sql",
}

CONTROL_PLANE_TABLES = frozenset(
    {
        "profiles",
        "profile_bindings",
        "profile_control_leases",
        "profile_control_commands",
        "growth_authority_state",
        "growth_authority_journal",
    }
)

REQUIRED_TABLES = frozenset(
    {
        "companion_schema",
        "profiles",
        "profile_bindings",
        "profile_control_leases",
        "profile_control_commands",
        "growth_events",
        "preferences",
        "preference_evidence",
        "preference_turn_decision_receipts",
        "growth_targets",
        "growth_target_reservations",
        "candidate_packages",
        "candidate_package_files",
        "candidate_package_blobs",
        "candidate_package_sources",
        "candidate_artifacts",
        "candidate_evidence",
        "reflection_decisions",
        "candidate_builds",
        "evaluation_runs",
        "evaluation_case_inputs",
        "evaluation_cases",
        "evaluation_case_launches",
        "evaluation_results",
        "evaluation_reports",
        "risk_assessments",
        "evaluation_authorizations",
        "evaluation_execution_permits",
        "growth_decisions",
        "capability_activation_requests",
        "capability_activation_receipts",
        "capability_activation_guards",
        "capability_guard_incidents",
        "capability_version_supports",
        "capability_quarantines",
        "jobs",
        "job_execution_waits",
        "delegated_task_grants",
        "companion_run_bindings",
        "companion_settings",
        "companion_detail_versions",
        "companion_budget_windows",
        "growth_authority_state",
        "growth_authority_journal",
        "reminders",
        "reminder_occurrences",
        "reminder_mutation_receipts",
        "notifications",
        "outbox",
        "audit_events",
        "lineage_edges",
        "run_growth_snapshots",
        "run_growth_dependency_items",
        "run_growth_dependency_evidence",
        "owner_memory_read_scopes",
    }
)

FORBIDDEN_AUTHORITY_NAMES = (
    "active_capability_versions",
    "installed_capability_versions",
    "tool_specs",
)


def configure_connection(db: sqlite3.Connection) -> None:
    db.row_factory = sqlite3.Row
    db.create_function(
        "deskpet_sha256",
        1,
        lambda value: hashlib.sha256(str(value).encode("utf-8")).hexdigest(),
        deterministic=True,
    )
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA synchronous = FULL")
    db.execute("PRAGMA busy_timeout = 5000")


def initialize_schema(db: sqlite3.Connection) -> None:
    configure_connection(db)
    current = int(db.execute("PRAGMA user_version").fetchone()[0])
    if current > COMPANION_SCHEMA_VERSION:
        raise RuntimeError(
            f"companion_schema_newer:{current}>{COMPANION_SCHEMA_VERSION}"
        )
    for version in range(current + 1, COMPANION_SCHEMA_VERSION + 1):
        sql = MIGRATION_RESOURCES[version].read_text(encoding="utf-8")
        db.execute("PRAGMA foreign_keys = OFF")
        try:
            db.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version={version};\nCOMMIT;")
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.execute("PRAGMA foreign_keys = ON")
        violations = db.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"companion_schema_foreign_key_violation:{version}")
    validate_schema(db)


def validate_schema(db: sqlite3.Connection) -> None:
    rows = db.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    names = {str(row["name"]) for row in rows}
    missing = REQUIRED_TABLES - names
    if missing:
        raise RuntimeError(f"companion_schema_missing:{','.join(sorted(missing))}")
    joined = "\n".join(str(row["sql"] or "").lower() for row in rows)
    forbidden = [name for name in FORBIDDEN_AUTHORITY_NAMES if name in joined]
    if forbidden:
        raise RuntimeError(f"companion_schema_duplicates_authority:{','.join(forbidden)}")
    detail_missing = DETAIL_VISIBLE_TABLES - names
    if detail_missing:
        raise RuntimeError(f"companion_detail_tables_missing:{','.join(sorted(detail_missing))}")

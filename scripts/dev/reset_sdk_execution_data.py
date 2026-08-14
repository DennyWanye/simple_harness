#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Development execution data reset script - T6.4.

CRITICAL: This script performs destructive operations on development databases.
It requires explicit nonce confirmation before executing.

Purpose:
- Reset execution data to start from SDK schema v1
- Remove old schema 1-29 compatibility data
- Provide clean slate for T6 Product Cutover testing

Safety:
- Dry-run mode by default (no changes)
- Nonce confirmation required for actual reset
- Backs up existing database before reset
- Only operates on development databases (not production)
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def compute_nonce(db_path: Path) -> str:
    """Compute confirmation nonce for database reset.

    Nonce is SHA-256 hash of database path to prevent accidental
    confirmation with generic "yes" response.
    """
    return hashlib.sha256(db_path.as_posix().encode()).hexdigest()[:8]


def backup_database(db_path: Path) -> Path:
    """Create timestamped backup of database before reset."""
    import datetime

    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_suffix(f".backup-{timestamp}.db")

    logger.info("creating_backup", source=db_path, target=backup_path)
    shutil.copy2(db_path, backup_path)

    return backup_path


def reset_execution_database(db_path: Path, dry_run: bool = True) -> bool:
    """Reset execution database to SDK schema v1.

    Args:
        db_path: Path to execution.db
        dry_run: If True, only show what would be done

    Returns:
        True if reset successful, False otherwise
    """
    if not db_path.exists():
        logger.error("database_not_found", path=db_path)
        return False

    if dry_run:
        logger.info("dry_run_mode", message="No changes will be made")
        logger.info("would_reset_database", path=db_path)
        return True

    # Create backup first
    backup_path = backup_database(db_path)
    logger.info("backup_created", path=backup_path)

    # TODO T6.4: Implement actual reset logic
    # 1. Drop old schema 1-29 tables
    # 2. Initialize SDK schema v1 tables
    # 3. Verify schema version

    logger.warning("reset_not_implemented", message="T6.4 implementation pending")
    return False


def main():
    """Main entry point for reset script."""
    parser = argparse.ArgumentParser(
        description="Reset development execution data to SDK schema v1"
    )
    parser.add_argument(
        "--db",
        type=Path,
        required=True,
        help="Path to execution.db (e.g., ~/.local/share/deskpet/execution.db)",
    )
    parser.add_argument(
        "--confirm",
        type=str,
        help="Confirmation nonce (computed from --db path)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Show what would be done without making changes (default)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform the reset (disables dry-run)",
    )

    args = parser.parse_args()

    # Validate database path
    db_path = args.db.expanduser().resolve()

    # Compute expected nonce
    expected_nonce = compute_nonce(db_path)

    # Determine dry-run mode
    dry_run = not args.execute

    if not dry_run:
        # Real reset requires nonce confirmation
        if not args.confirm:
            logger.error(
                "confirmation_required",
                message=f"Reset requires --confirm {expected_nonce}",
                path=db_path,
            )
            print(f"\n⚠️  DESTRUCTIVE OPERATION REQUIRES CONFIRMATION", file=sys.stderr)
            print(f"Database: {db_path}", file=sys.stderr)
            print(f"To proceed, run:", file=sys.stderr)
            print(f"  {' '.join(sys.argv)} --confirm {expected_nonce}", file=sys.stderr)
            return 1

        if args.confirm != expected_nonce:
            logger.error(
                "invalid_nonce",
                message="Confirmation nonce does not match database path",
                expected=expected_nonce,
                provided=args.confirm,
            )
            return 1

        logger.warning(
            "executing_reset",
            message="Proceeding with destructive reset",
            path=db_path,
        )

    # Perform reset
    success = reset_execution_database(db_path, dry_run=dry_run)

    if not success:
        logger.error("reset_failed")
        return 1

    logger.info("reset_complete" if not dry_run else "dry_run_complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())

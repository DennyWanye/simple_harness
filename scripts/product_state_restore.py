#!/usr/bin/env python3
"""Explicit offline restore for a verified ProductState pre-v2 backup."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from deskpet.product_state.backup import restore_pre_v2_backup_offline  # noqa: E402
from deskpet.product_state.database import ProductStateDatabase  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument(
        "--confirm-app-stopped",
        action="store_true",
        help="confirm every app/backend process using this database is stopped",
    )
    args = parser.parse_args()
    if not args.confirm_app_stopped:
        parser.error("--confirm-app-stopped is required; restore is offline only")
    recovery = restore_pre_v2_backup_offline(
        database_path=args.database,
        backup_path=args.backup,
        validate_v1=ProductStateDatabase._validate_v1_connection,
    )
    print(f"restored verified v1 backup; preserved current database at {recovery}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

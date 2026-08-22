# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Offline all-old/all-new coordinator for the execution+memory v4 pair."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Callable


class ProductSdkV4MigrationError(RuntimeError):
    pass


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_journal(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def migrate_product_sdk_pair_v4(
    *,
    execution_path: str | Path,
    memory_path: str | Path,
    identity_map: object,
    provenance_manifest: object,
    journal_path: str | Path,
    fault: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Migrate two closed databases or restore both originals on any failure."""
    execution = Path(execution_path).resolve()
    memory = Path(memory_path).resolve()
    journal = Path(journal_path).resolve()
    if execution == memory or journal in {execution, memory}:
        raise ValueError("migration paths must be distinct")
    if not execution.is_file() or not memory.is_file():
        raise ProductSdkV4MigrationError("sdk_v4_migration_source_missing")
    journal.parent.mkdir(parents=True, exist_ok=True)
    nonce = str(time.time_ns())
    execution_backup = execution.with_name(f"{execution.name}.pre-v4-{nonce}.bak")
    memory_backup = memory.with_name(f"{memory.name}.pre-v4-{nonce}.bak")
    state = {
        "protocol": "simple-harness-product/sdk-v4-cutover-journal/v1",
        "phase": "backing_up",
        "execution_path": str(execution),
        "memory_path": str(memory),
        "execution_backup": str(execution_backup),
        "memory_backup": str(memory_backup),
        "old_execution_hash": _hash(execution),
        "old_memory_hash": _hash(memory),
    }
    _write_journal(journal, state)
    try:
        shutil.copy2(memory, memory_backup)
        os.chmod(memory_backup, 0o600)
        if _hash(memory_backup) != state["old_memory_hash"]:
            raise ProductSdkV4MigrationError("memory_backup_hash_mismatch")
        if fault:
            fault("product_cutover.after_backups")
        from simple_harness.execution.sqlite.migrations.execution_v3_to_v4 import (
            migrate_execution_v3_to_v4,
        )

        execution_manifest = migrate_execution_v3_to_v4(
            execution,
            backup_path=execution_backup,
            identity_map=identity_map,
        )
        state["phase"] = "execution_v4"
        state["execution_manifest_digest"] = execution_manifest.digest
        state["new_execution_hash"] = _hash(execution)
        _write_journal(journal, state)
        if fault:
            fault("product_cutover.after_execution")
        from simple_harness_memory.migrations import migrate_v3_to_v4

        memory_receipt = migrate_v3_to_v4(
            memory,
            execution_manifest=execution_manifest,
            provenance_manifest=provenance_manifest,
            identity_map=identity_map,
            backup_path=None,
        )
        state["phase"] = "validated_v4_pair"
        state["memory_receipt_digest"] = memory_receipt.digest
        state["new_memory_hash"] = _hash(memory)
        _write_journal(journal, state)
        if fault:
            fault("product_cutover.after_memory")
        state["phase"] = "complete"
        _write_journal(journal, state)
        return state
    except BaseException as exc:
        if execution_backup.is_file():
            shutil.copy2(execution_backup, execution)
        if memory_backup.is_file():
            shutil.copy2(memory_backup, memory)
        state["phase"] = "rolled_back"
        state["error_code"] = type(exc).__name__
        state["restored_execution_hash"] = _hash(execution)
        state["restored_memory_hash"] = _hash(memory)
        _write_journal(journal, state)
        if (
            state["restored_execution_hash"] != state["old_execution_hash"]
            or state["restored_memory_hash"] != state["old_memory_hash"]
        ):
            raise ProductSdkV4MigrationError("sdk_v4_pair_restore_failed") from exc
        raise


def recover_product_sdk_pair_v4(journal_path: str | Path) -> str:
    """Resolve an interrupted swap to a validated old pair or keep a complete v4 pair."""
    journal = Path(journal_path).resolve()
    if not journal.is_file():
        return "no_journal"
    state = json.loads(journal.read_text(encoding="utf-8"))
    if state.get("protocol") != "simple-harness-product/sdk-v4-cutover-journal/v1":
        raise ProductSdkV4MigrationError("sdk_v4_journal_protocol_invalid")
    execution = Path(str(state["execution_path"])).resolve()
    memory = Path(str(state["memory_path"])).resolve()
    if state.get("phase") == "complete":
        if (
            _hash(execution) != state.get("new_execution_hash")
            or _hash(memory) != state.get("new_memory_hash")
        ):
            raise ProductSdkV4MigrationError("sdk_v4_complete_pair_hash_mismatch")
        return "v4_pair"
    execution_backup = Path(str(state["execution_backup"])).resolve()
    memory_backup = Path(str(state["memory_backup"])).resolve()
    if not execution_backup.is_file() or not memory_backup.is_file():
        raise ProductSdkV4MigrationError("sdk_v4_recovery_backup_missing")
    if (
        _hash(execution_backup) != state.get("old_execution_hash")
        or _hash(memory_backup) != state.get("old_memory_hash")
    ):
        raise ProductSdkV4MigrationError("sdk_v4_recovery_backup_hash_mismatch")
    shutil.copy2(execution_backup, execution)
    shutil.copy2(memory_backup, memory)
    state["phase"] = "recovered_old_pair"
    state["restored_execution_hash"] = _hash(execution)
    state["restored_memory_hash"] = _hash(memory)
    _write_journal(journal, state)
    return "old_pair"


__all__ = (
    "ProductSdkV4MigrationError",
    "migrate_product_sdk_pair_v4",
    "recover_product_sdk_pair_v4",
)

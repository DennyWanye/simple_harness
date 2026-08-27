"""Verified pre-migration backup and explicit offline restore tooling."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from pathlib import Path
from typing import Callable


class ProductStateBackupError(RuntimeError):
    code = "product_state_backup_error"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_sidecar(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _fsync_parent(path)


def create_verified_backup(
    source: sqlite3.Connection,
    *,
    database_path: str | Path,
    validate_v1: Callable[[sqlite3.Connection], None],
) -> tuple[Path, Path]:
    """Create and atomically publish the latest exact pre-v2 backup."""

    live = Path(database_path).resolve(strict=True)
    backup = live.with_name(f"{live.name}.pre-v2.sqlite3")
    sidecar = backup.with_suffix(f"{backup.suffix}.json")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{backup.name}.tmp-", dir=live.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
            target.commit()
            validate_v1(target)
            quick = target.execute("PRAGMA quick_check").fetchone()
            if quick is None or str(quick[0]) != "ok":
                raise ProductStateBackupError("pre-v2 backup quick_check failed")
        finally:
            target.close()
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        digest = _sha256(temporary)
        os.replace(temporary, backup)
        _fsync_parent(backup)
        _write_sidecar(
            sidecar,
            {
                "schema": "product-state-pre-v2-backup-v1",
                "database_name": live.name,
                "sha256": digest,
                "size": backup.stat().st_size,
            },
        )
        return backup, sidecar
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def create_verified_migration_backup(
    source: sqlite3.Connection,
    *,
    database_path: str | Path,
    source_version: int,
    validate: Callable[[sqlite3.Connection], None],
) -> tuple[Path, Path]:
    """Publish an immutable content-addressed backup for one schema boundary."""

    if source_version < 1:
        raise ValueError("source_version must be positive")
    live = Path(database_path).resolve(strict=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{live.name}.pre-v{source_version + 1}.tmp-", dir=live.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
            target.commit()
            validate(target)
            if str(target.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
                raise ProductStateBackupError("migration backup quick_check failed")
        finally:
            target.close()
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        digest = _sha256(temporary)
        backup = live.with_name(
            f"{live.name}.pre-v{source_version + 1}.{digest}.sqlite3"
        )
        sidecar = backup.with_suffix(f"{backup.suffix}.json")
        if backup.exists():
            if _sha256(backup) != digest:
                raise ProductStateBackupError("content-addressed backup differs")
            temporary.unlink()
        else:
            os.replace(temporary, backup)
            _fsync_parent(backup)
        _write_sidecar(sidecar, {
            "schema": "product-state-migration-backup-v1",
            "database_name": live.name,
            "source_version": source_version,
            "target_version": source_version + 1,
            "sha256": digest,
            "size": backup.stat().st_size,
        })
        return backup, sidecar
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _restore_backup_offline(
    *,
    database_path: str | Path,
    backup_path: str | Path,
    validate: Callable[[sqlite3.Connection], None],
    expected_schema: str,
    recovery_version: int,
) -> Path:
    """Explicitly replace a stopped database with a verified pre-v2 backup.

    The caller owns process shutdown.  An exclusive probe rejects a database
    currently held by another writer before any filesystem replacement.
    """

    live = Path(database_path).resolve(strict=True)
    backup = Path(backup_path).resolve(strict=True)
    sidecar = backup.with_suffix(f"{backup.suffix}.json")
    try:
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductStateBackupError("backup sidecar is missing or invalid") from exc
    if metadata.get("schema") != expected_schema:
        raise ProductStateBackupError("backup sidecar schema differs")
    if metadata.get("database_name") != live.name:
        raise ProductStateBackupError("backup belongs to another database")
    if backup.stat().st_mode & 0o077:
        raise ProductStateBackupError("backup permissions are not 0600")
    if metadata.get("sha256") != _sha256(backup):
        raise ProductStateBackupError("backup hash differs")
    candidate = sqlite3.connect(backup)
    try:
        validate(candidate)
        quick = candidate.execute("PRAGMA quick_check").fetchone()
        if quick is None or str(quick[0]) != "ok":
            raise ProductStateBackupError("backup quick_check failed")
    finally:
        candidate.close()

    exclusive = sqlite3.connect(live, timeout=0)
    try:
        exclusive.execute("BEGIN EXCLUSIVE")
        # The explicit offline contract is checked by acquiring the exclusive
        # database lock.  Release the transaction before Connection.backup:
        # SQLite's backup API cannot make progress from a connection holding a
        # write transaction against the same database.
        exclusive.rollback()
        recovery = live.with_name(
            f"{live.name}.pre-restore-v{recovery_version}.sqlite3"
        )
        target = sqlite3.connect(recovery)
        try:
            exclusive.backup(target)
            target.commit()
        finally:
            target.close()
        os.chmod(recovery, stat.S_IRUSR | stat.S_IWUSR)
        with recovery.open("rb") as stream:
            os.fsync(stream.fileno())
        recovery_hash = _sha256(recovery)
        _write_sidecar(
            recovery.with_suffix(f"{recovery.suffix}.json"),
            {
                "schema": "product-state-pre-restore-v2-v1",
                "database_name": live.name,
                "sha256": recovery_hash,
                "size": recovery.stat().st_size,
            },
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{live.name}.restore-", dir=live.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            source = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
            restored = sqlite3.connect(temporary)
            try:
                source.backup(restored)
                restored.commit()
            finally:
                restored.close()
                source.close()
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            exclusive.close()
            exclusive = None
            os.replace(temporary, live)
            for suffix in ("-wal", "-shm"):
                live.with_name(f"{live.name}{suffix}").unlink(missing_ok=True)
            _fsync_parent(live)
        finally:
            temporary.unlink(missing_ok=True)
        return recovery
    except sqlite3.OperationalError as exc:
        raise ProductStateBackupError("database is not offline") from exc
    finally:
        if exclusive is not None:
            exclusive.close()


def restore_pre_v2_backup_offline(
    *,
    database_path: str | Path,
    backup_path: str | Path,
    validate_v1: Callable[[sqlite3.Connection], None],
) -> Path:
    """Restore the frozen v1 backup format retained for offline recovery."""

    return _restore_backup_offline(
        database_path=database_path,
        backup_path=backup_path,
        validate=validate_v1,
        expected_schema="product-state-pre-v2-backup-v1",
        recovery_version=2,
    )


def restore_migration_backup_offline(
    *,
    database_path: str | Path,
    backup_path: str | Path,
    expected_source_version: int,
    validate: Callable[[sqlite3.Connection], None],
) -> Path:
    """Restore an immutable content-addressed migration backup offline."""

    backup = Path(backup_path).resolve(strict=True)
    sidecar = backup.with_suffix(f"{backup.suffix}.json")
    try:
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductStateBackupError("backup sidecar is missing or invalid") from exc
    if (
        metadata.get("source_version") != expected_source_version
        or metadata.get("target_version") != expected_source_version + 1
    ):
        raise ProductStateBackupError("backup migration boundary differs")
    return _restore_backup_offline(
        database_path=database_path,
        backup_path=backup,
        validate=validate,
        expected_schema="product-state-migration-backup-v1",
        recovery_version=expected_source_version + 1,
    )


__all__ = (
    "ProductStateBackupError",
    "create_verified_backup",
    "create_verified_migration_backup",
    "restore_migration_backup_offline",
    "restore_pre_v2_backup_offline",
)

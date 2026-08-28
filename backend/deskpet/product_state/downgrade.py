"""Fail-closed offline downgrade coordination across Product and SDK databases."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Protocol

from .backup import ProductStateBackupError, restore_migration_backup_offline
from .database import ProductStateDatabase


class HostControlDowngradeError(RuntimeError):
    code = "host_control_downgrade_blocked"


@dataclass(frozen=True, slots=True)
class VerificationAttemptIdentity:
    attempt_id: str
    intent_id: str
    status: str
    expected_run_id: str
    verifier_session_id: str
    request_id: str
    turn_id: str


@dataclass(frozen=True, slots=True)
class VerifierRunDisposition:
    run_id: str
    session_id: str
    request_id: str
    turn_id: str
    state: str
    terminal: bool
    recoverable: bool


class VerifierRunProbe(Protocol):
    def __call__(
        self, attempt: VerificationAttemptIdentity
    ) -> VerifierRunDisposition | None: ...


class Sdk062ReopenProbe(Protocol):
    def __call__(self, execution_database: Path) -> tuple[bool, str]: ...


@dataclass(frozen=True, slots=True)
class DowngradeReceipt:
    outcome: Literal["restored", "restored_execution_quarantined"]
    product_recovery_artifact: Path
    execution_backup: Path
    execution_quarantine: Path | None
    attempt_count: int
    sdk_probe_detail: str


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


def _require_offline(path: Path) -> None:
    connection = sqlite3.connect(path, timeout=0)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        connection.rollback()
    except sqlite3.OperationalError as exc:
        raise HostControlDowngradeError(f"database is not offline: {path.name}") from exc
    finally:
        connection.close()


def _immutable_sqlite_backup(path: Path, *, kind: str) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.{kind}.", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
            target.commit()
            quick = target.execute("PRAGMA quick_check").fetchone()
            if quick is None or str(quick[0]) != "ok":
                raise HostControlDowngradeError(f"{kind} backup quick_check failed")
        finally:
            target.close()
            source.close()
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        digest = _sha256(temporary)
        backup = path.with_name(f"{path.name}.{kind}.{digest}.sqlite3")
        if backup.exists():
            if _sha256(backup) != digest:
                raise HostControlDowngradeError(f"{kind} backup hash collision")
            temporary.unlink()
        else:
            os.replace(temporary, backup)
            _fsync_parent(backup)
        _write_sidecar(
            backup.with_suffix(f"{backup.suffix}.json"),
            {
                "schema": "simple-harness-offline-database-artifact-v1",
                "kind": kind,
                "database_name": path.name,
                "sha256": digest,
                "size": backup.stat().st_size,
            },
        )
        return backup
    finally:
        temporary.unlink(missing_ok=True)


def _attempts(product_database: Path) -> tuple[VerificationAttemptIdentity, ...]:
    connection = sqlite3.connect(product_database)
    connection.row_factory = sqlite3.Row
    try:
        ProductStateDatabase._validate_v3_connection(connection)
        rows = connection.execute(
            "SELECT attempt_id,intent_id,status,expected_run_id,verifier_session_id,"
            "request_id,turn_id FROM capability_skill_install_verification_attempts "
            "ORDER BY intent_id,attempt_generation"
        ).fetchall()
        return tuple(
            VerificationAttemptIdentity(*(str(row[key]) for key in row.keys()))
            for row in rows
        )
    finally:
        connection.close()


def _verify_run_dispositions(
    attempts: tuple[VerificationAttemptIdentity, ...], probe: VerifierRunProbe
) -> None:
    for attempt in attempts:
        disposition = probe(attempt)
        if disposition is None:
            if attempt.status not in {"prepared", "terminal_failed", "superseded"}:
                raise HostControlDowngradeError(
                    f"verifier Run is missing for launched attempt {attempt.attempt_id}"
                )
            continue
        exact = (
            disposition.run_id == attempt.expected_run_id
            and disposition.session_id == attempt.verifier_session_id
            and disposition.request_id == attempt.request_id
            and disposition.turn_id == attempt.turn_id
        )
        if not exact:
            raise HostControlDowngradeError(
                f"verifier Run identity differs for {attempt.attempt_id}"
            )
        if not disposition.terminal or disposition.recoverable:
            raise HostControlDowngradeError(
                f"verifier Run remains recoverable for {attempt.attempt_id}"
            )


def pinned_sdk_062_reopen_probe(
    python_executable: str | Path,
) -> Sdk062ReopenProbe:
    executable_path = Path(python_executable).absolute()
    if not executable_path.exists():
        raise FileNotFoundError(executable_path)
    # Preserve a virtualenv's Python symlink; resolving it would silently use
    # the base interpreter and lose the pinned distribution environment.
    executable = str(executable_path)
    program = r"""
import importlib.metadata, json, sys
from pathlib import Path
version = importlib.metadata.version('simple-harness-sdk')
if version != '0.6.2':
    raise SystemExit('wrong SDK version: ' + version)
from simple_harness.execution.sqlite import Database, SqliteExecutionUnitOfWork
path = Path(sys.argv[1])
for _ in range(2):
    database = Database.open(path)
    uow = SqliteExecutionUnitOfWork(database)
    roots = uow.list_recoverable_root_runs()
    children = uow.list_recoverable_child_runs()
    database.close()
    if roots or children:
        raise SystemExit('recoverable Runs remain')
print(json.dumps({'sdk_version': version, 'reopens': 2}, sort_keys=True))
"""

    def probe(execution_database: Path) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="sdk-062-reopen-") as directory:
            candidate = Path(directory) / execution_database.name
            shutil.copy2(execution_database, candidate)
            before_hash = _sha256(candidate)
            environment = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONNOUSERSITE": "1",
                "NO_PROXY": "*",
                "no_proxy": "*",
            }
            completed = subprocess.run(
                [executable, "-I", "-c", program, str(candidate)],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                env=environment,
            )
            detail = (completed.stdout or completed.stderr).strip()
            unchanged = candidate.exists() and _sha256(candidate) == before_hash
            if completed.returncode == 0 and not unchanged:
                return False, "SDK 0.6.2 reopen mutated the execution database"
            return completed.returncode == 0, detail

    return probe


def execute_host_control_downgrade(
    *,
    product_database: str | Path,
    execution_database: str | Path,
    product_pre_v3_backup: str | Path,
    ingress_closed: bool,
    app_stopped: bool,
    run_probe: VerifierRunProbe,
    sdk_062_probe: Sdk062ReopenProbe,
    allow_execution_quarantine: bool = False,
) -> DowngradeReceipt:
    """Coordinate the only supported Host-control downgrade to Product v2."""

    if not ingress_closed or not app_stopped:
        raise HostControlDowngradeError("ingress and application must be stopped")
    product = Path(product_database).resolve(strict=True)
    execution = Path(execution_database).resolve(strict=True)
    pre_v3 = Path(product_pre_v3_backup).resolve(strict=True)
    _require_offline(product)
    _require_offline(execution)
    attempts = _attempts(product)
    _verify_run_dispositions(attempts, run_probe)
    execution_backup = _immutable_sqlite_backup(
        execution, kind="pre-sdk-062-downgrade"
    )
    product_recovery = _immutable_sqlite_backup(
        product, kind="pre-product-v2-restore"
    )

    # Product backup validation happens before any live replacement.
    sidecar = pre_v3.with_suffix(f"{pre_v3.suffix}.json")
    if not sidecar.exists():
        raise HostControlDowngradeError("pre-v3 Product backup sidecar is missing")
    with tempfile.TemporaryDirectory(prefix="product-v2-validate-") as directory:
        candidate_path = Path(directory) / pre_v3.name
        shutil.copy2(pre_v3, candidate_path)
        candidate = sqlite3.connect(candidate_path)
        try:
            ProductStateDatabase._validate_v2_connection(candidate)
        finally:
            candidate.close()

    compatible, detail = sdk_062_probe(execution_backup)
    quarantine: Path | None = None
    if not compatible:
        if not allow_execution_quarantine:
            raise HostControlDowngradeError(
                f"SDK 0.6.2 reopen proof failed: {detail}"
            )
        quarantine_digest = _sha256(execution)
        quarantine = execution.with_name(
            f"{execution.name}.quarantine.{quarantine_digest}.sqlite3"
        )
        if quarantine.exists():
            raise HostControlDowngradeError("execution quarantine target already exists")
        os.replace(execution, quarantine)
        for suffix in ("-wal", "-shm"):
            execution.with_name(f"{execution.name}{suffix}").unlink(missing_ok=True)
        _fsync_parent(quarantine)
        _write_sidecar(
            quarantine.with_suffix(f"{quarantine.suffix}.json"),
            {
                "schema": "simple-harness-sdk-execution-quarantine-v1",
                "database_name": execution.name,
                "sha256": quarantine_digest,
                "size": quarantine.stat().st_size,
                "reason": "sdk-0.6.2-reopen-incompatible",
                "probe_detail": detail,
            },
        )

    try:
        restore_migration_backup_offline(
            database_path=product,
            backup_path=pre_v3,
            expected_source_version=2,
            validate=ProductStateDatabase._validate_v2_connection,
        )
    except ProductStateBackupError as exc:
        raise HostControlDowngradeError("ProductState restore failed") from exc
    return DowngradeReceipt(
        outcome=(
            "restored_execution_quarantined" if quarantine is not None else "restored"
        ),
        product_recovery_artifact=product_recovery,
        execution_backup=execution_backup,
        execution_quarantine=quarantine,
        attempt_count=len(attempts),
        sdk_probe_detail=detail,
    )


__all__ = (
    "DowngradeReceipt",
    "HostControlDowngradeError",
    "Sdk062ReopenProbe",
    "VerificationAttemptIdentity",
    "VerifierRunDisposition",
    "VerifierRunProbe",
    "execute_host_control_downgrade",
    "pinned_sdk_062_reopen_probe",
)

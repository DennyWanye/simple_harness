from __future__ import annotations

import json
import shutil
import site
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from deskpet.product_state.backup import create_verified_migration_backup
from deskpet.product_state.database import ProductStateDatabase
from deskpet.product_state.downgrade import (
    HostControlDowngradeError,
    VerifierRunDisposition,
    execute_host_control_downgrade,
    pinned_sdk_062_reopen_probe,
)
from deskpet.product_state.schema import SCHEMA_V2_PARTS
from deskpet.product_state.schema_integrity import expected_semantic_fingerprint


def _v2(path: Path) -> Path:
    connection = sqlite3.connect(path)
    for part in SCHEMA_V2_PARTS:
        connection.executescript(part)
    connection.execute("INSERT INTO product_schema_meta VALUES(1,2)")
    tables = tuple(row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ))
    connection.execute(
        "INSERT INTO product_schema_manifest VALUES(1,?,?)",
        (expected_semantic_fingerprint(SCHEMA_V2_PARTS), json.dumps(tables, separators=(",", ":"))),
    )
    connection.execute("PRAGMA user_version=2")
    connection.commit()
    backup, _ = create_verified_migration_backup(
        connection, database_path=path, source_version=2,
        validate=ProductStateDatabase._validate_v2_connection,
    )
    connection.close()
    database = ProductStateDatabase(path)
    database.initialize()
    database.close()
    return backup


def _attempt(path: Path, *, status: str = "unknown") -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO capability_skill_install_intents("
        "intent_id,effect_id,call_id,root_run_id,run_id,channel,project_scope_key,principal_id,"
        "source_json,exact_commit,archive_hash,raw_tree_hash,member_set_stamp,permission_set_hash,"
        "confirmation_nonce,confirmation_version,expires_at,status,state_version,settlement_ref,"
        "created_at,updated_at,verification_attempt_generation,current_verification_attempt_id) "
        "VALUES('intent','effect-live','call-live','root','run','chat','project','principal','{}',?,"
        "'archive','tree','members','permissions','nonce-live',1,99,'published_pending_runtime_verification',"
        "2,'receipt',1,1,1,'attempt')",
        ("a" * 40,),
    )
    connection.execute(
        "INSERT INTO capability_skill_install_verification_attempts("
        "attempt_id,intent_id,attempt_generation,state_version,status,verifier_session_id,"
        "request_id,turn_id,expected_run_id,manager_operation_id,manager_receipt_hash,"
        "committed_set_stamp,project_scope_key,expected_member_set_stamp,created_at,updated_at) "
        "VALUES('attempt','intent',1,1,?,'session','request','turn','expected-run',"
        "'operation','receipt','committed','project','members',1,1)",
        (status,),
    )
    connection.commit()
    connection.close()


def _execution(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker VALUES('preserved')")
    connection.commit()
    connection.close()


def _disposition(*, terminal: bool = True, recoverable: bool = False):
    return VerifierRunDisposition(
        run_id="expected-run", session_id="session", request_id="request",
        turn_id="turn", state="completed" if terminal else "running",
        terminal=terminal, recoverable=recoverable,
    )


def test_dual_database_gate_restores_only_after_exact_terminal_proof(tmp_path: Path) -> None:
    product, execution = tmp_path / "product.db", tmp_path / "execution.db"
    backup = _v2(product)
    _attempt(product)
    _execution(execution)
    receipt = execute_host_control_downgrade(
        product_database=product, execution_database=execution,
        product_pre_v3_backup=backup, ingress_closed=True, app_stopped=True,
        run_probe=lambda _attempt: _disposition(),
        sdk_062_probe=lambda _path: (True, "0.6.2 reopened twice"),
    )
    assert receipt.outcome == "restored"
    assert receipt.execution_backup.exists()
    assert receipt.execution_backup.stat().st_mode & 0o777 == 0o600
    restored = sqlite3.connect(product)
    ProductStateDatabase._validate_v2_connection(restored)
    restored.close()
    assert sqlite3.connect(execution).execute("SELECT value FROM marker").fetchone()[0] == "preserved"


@pytest.mark.parametrize(
    "probe",
    [
        lambda _attempt: _disposition(terminal=False),
        lambda _attempt: _disposition(recoverable=True),
        lambda _attempt: VerifierRunDisposition(
            "wrong", "session", "request", "turn", "completed", True, False
        ),
    ],
)
def test_nonterminal_recoverable_or_mismatched_run_aborts_before_backup(
    tmp_path: Path, probe
) -> None:
    product, execution = tmp_path / "product.db", tmp_path / "execution.db"
    backup = _v2(product)
    _attempt(product)
    _execution(execution)
    with pytest.raises(HostControlDowngradeError):
        execute_host_control_downgrade(
            product_database=product, execution_database=execution,
            product_pre_v3_backup=backup, ingress_closed=True, app_stopped=True,
            run_probe=probe, sdk_062_probe=lambda _path: (True, "ok"),
        )
    assert not tuple(tmp_path.glob("execution.db.pre-sdk-062-downgrade.*"))
    assert ProductStateDatabase(product).schema_version == 4


def test_incompatible_sdk_aborts_or_requires_explicit_whole_database_quarantine(tmp_path: Path) -> None:
    product, execution = tmp_path / "product.db", tmp_path / "execution.db"
    backup = _v2(product)
    _attempt(product)
    _execution(execution)
    arguments = dict(
        product_database=product, execution_database=execution,
        product_pre_v3_backup=backup, ingress_closed=True, app_stopped=True,
        run_probe=lambda _attempt: _disposition(),
        sdk_062_probe=lambda _path: (False, "schema too new"),
    )
    with pytest.raises(HostControlDowngradeError, match="reopen proof failed"):
        execute_host_control_downgrade(**arguments)
    assert execution.exists()
    assert ProductStateDatabase(product).schema_version == 4

    receipt = execute_host_control_downgrade(
        **arguments, allow_execution_quarantine=True
    )
    assert receipt.outcome == "restored_execution_quarantined"
    assert receipt.execution_quarantine is not None
    assert receipt.execution_quarantine.exists() and not execution.exists()
    restored = sqlite3.connect(product)
    ProductStateDatabase._validate_v2_connection(restored)
    restored.close()


def test_gate_requires_explicit_shutdown_claims(tmp_path: Path) -> None:
    product, execution = tmp_path / "product.db", tmp_path / "execution.db"
    backup = _v2(product)
    _execution(execution)
    with pytest.raises(HostControlDowngradeError, match="must be stopped"):
        execute_host_control_downgrade(
            product_database=product, execution_database=execution,
            product_pre_v3_backup=backup, ingress_closed=False, app_stopped=True,
            run_probe=lambda _attempt: None, sdk_062_probe=lambda _path: (True, "ok"),
        )


def test_exact_pinned_sdk_062_reopens_twice_and_rejects_recoverable_runs(
    tmp_path: Path,
) -> None:
    from simple_harness.execution.sqlite import Database

    path = tmp_path / "sdk.sqlite3"
    Database.open(path).close()
    environment = tmp_path / "sdk-062-venv"
    uv = shutil.which("uv")
    assert uv is not None
    subprocess.run(
        [uv, "venv", "--python", sys.executable, str(environment)],
        check=True,
        capture_output=True,
        text=True,
    )
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    isolated_site = subprocess.run(
        [str(python), "-c", "import site; print(site.getsitepackages()[0])"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    Path(isolated_site, "app-runtime-dependencies.pth").write_text(
        site.getsitepackages()[0] + "\n", encoding="utf-8"
    )
    wheel = Path(__file__).resolve().parents[2] / "vendor/simple_harness_sdk-0.6.2-py3-none-any.whl"
    subprocess.run(
        [uv, "pip", "install", "--offline", "--python", str(python), "--no-deps", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )
    accepted, detail = pinned_sdk_062_reopen_probe(python)(path)
    assert accepted, detail
    assert '"sdk_version": "0.6.2"' in detail

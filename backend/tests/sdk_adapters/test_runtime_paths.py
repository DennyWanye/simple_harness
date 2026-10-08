# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from deskpet.sdk_adapters.runtime_paths import (
    ProductRuntimePathsAdapter,
    SdkCandidateIdentity,
    durable_sdk_run_start_exists,
    verify_sdk_candidate,
)
from deskpet.sdk_adapters.sdk_candidate import (
    SDK_VERSION,
    SDK_WHEEL_SHA256,
    build_candidate_identity,
    sdk_wheel_path,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WHEEL = sdk_wheel_path()
WHEEL_SHA256 = SDK_WHEEL_SHA256


def test_exact_candidate_identity_and_execution_path(tmp_path: Path) -> None:
    identity = build_candidate_identity()
    verified = verify_sdk_candidate(identity)
    paths = ProductRuntimePathsAdapter(tmp_path / "user-data")

    assert verified == identity
    assert paths.execution_database == (
        tmp_path / "user-data/data/simple-harness-sdk/execution-v6.sqlite3"
    )
    assert paths.execution_database.parent.is_dir()


@pytest.mark.parametrize("field", ("version", "sha256", "wheel"))
def test_candidate_identity_fails_closed(tmp_path: Path, field: str) -> None:
    version = "0.1.0" if field == "version" else SDK_VERSION
    digest = "0" * 64 if field == "sha256" else WHEEL_SHA256
    wheel = tmp_path / "other.whl" if field == "wheel" else WHEEL
    if field == "wheel":
        wheel.write_bytes(WHEEL.read_bytes())
    with pytest.raises(RuntimeError, match="SDK candidate"):
        verify_sdk_candidate(SdkCandidateIdentity(version, digest, wheel))


def test_runtime_path_rejects_home_repo_evidence_and_symlink_escape(
    tmp_path: Path,
) -> None:
    for forbidden in (
        Path.home(),
        PROJECT_ROOT,
        PROJECT_ROOT / ".local-test-evidence",
    ):
        with pytest.raises(ValueError):
            ProductRuntimePathsAdapter(forbidden)

    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "user-data-link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        ProductRuntimePathsAdapter(link)


def test_runtime_path_has_no_product_or_session_database_alias(tmp_path: Path) -> None:
    paths = ProductRuntimePathsAdapter(tmp_path / "user-data")
    assert paths.execution_database.name == "execution-v6.sqlite3"
    assert paths.execution_database not in {
        paths.user_data_root / "data/product_state.db",
        paths.user_data_root / "data/state.db",
        paths.user_data_root / "data/workflow.db",
        paths.user_data_root / "data/sessions.db",
    }


def test_durable_run_start_probe_reads_only_sdk_execution_database(
    tmp_path: Path,
) -> None:
    paths = ProductRuntimePathsAdapter(tmp_path / "user-data")
    with sqlite3.connect(paths.execution_database) as db:
        db.execute(
            "CREATE TABLE run_start_snapshots(run_id TEXT PRIMARY KEY, snapshot_json TEXT)"
        )
        db.execute(
            "INSERT INTO run_start_snapshots(run_id,snapshot_json) VALUES('sdk-waiting','{}')"
        )
    workflow_db = paths.user_data_root / "data/workflow.db"
    with sqlite3.connect(workflow_db) as db:
        db.execute(
            "CREATE TABLE run_start_snapshots(run_id TEXT PRIMARY KEY, snapshot_json TEXT)"
        )
        db.execute(
            "INSERT INTO run_start_snapshots(run_id,snapshot_json) VALUES('legacy-only','{}')"
        )

    assert durable_sdk_run_start_exists(paths.execution_database, "sdk-waiting")
    assert not durable_sdk_run_start_exists(paths.execution_database, "legacy-only")


def test_a_frozen_executable_skips_only_the_build_host_origin_check(tmp_path: Path, monkeypatch) -> None:
    """2026-10-08 macOS 冻结包冒烟启动：PyInstaller 带进来的 direct_url.json 指向打包机路径，
    冻结包里必然对不上。冻结时只跳过"安装来源"这一项；版本与 wheel 字节照样核对。

    **改坏检验**：去掉 ``sys.frozen`` 分支 → 第二段红（报 installed origin mismatch）。"""
    import sys

    moved = tmp_path / WHEEL.name
    moved.write_bytes(WHEEL.read_bytes())
    identity = SdkCandidateIdentity(SDK_VERSION, WHEEL_SHA256, moved)
    with pytest.raises(RuntimeError, match="origin mismatch"):
        verify_sdk_candidate(identity)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert verify_sdk_candidate(identity) == identity
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        verify_sdk_candidate(SdkCandidateIdentity(SDK_VERSION, "0" * 64, moved))

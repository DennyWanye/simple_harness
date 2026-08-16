# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from deskpet.sdk_adapters.runtime_paths import (
    ProductRuntimePathsAdapter,
    SdkCandidateIdentity,
    verify_sdk_candidate,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
WHEEL = PROJECT_ROOT / "backend/vendor/simple_harness_sdk-0.1.1-py3-none-any.whl"
WHEEL_SHA256 = hashlib.sha256(WHEEL.read_bytes()).hexdigest()


def test_exact_candidate_identity_and_execution_path(tmp_path: Path) -> None:
    identity = SdkCandidateIdentity("0.1.1", WHEEL_SHA256, WHEEL)
    verified = verify_sdk_candidate(identity)
    paths = ProductRuntimePathsAdapter(tmp_path / "user-data")

    assert verified == identity
    assert paths.execution_database == (
        tmp_path / "user-data/data/simple-harness-sdk/execution-v1.sqlite3"
    )
    assert paths.execution_database.parent.is_dir()


@pytest.mark.parametrize("field", ("version", "sha256", "wheel"))
def test_candidate_identity_fails_closed(tmp_path: Path, field: str) -> None:
    version = "0.1.0" if field == "version" else "0.1.1"
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
    assert paths.execution_database.name == "execution-v1.sqlite3"
    assert paths.execution_database not in {
        paths.user_data_root / "data/product_state.db",
        paths.user_data_root / "data/state.db",
        paths.user_data_root / "data/workflow.db",
        paths.user_data_root / "data/sessions.db",
    }

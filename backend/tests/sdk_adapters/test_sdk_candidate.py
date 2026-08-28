# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Tests for the vendored-SDK-wheel single source of truth (sdk_candidate).

Covers S1-AC-2: the fail-closed chain must keep rejecting tampered/wrong
candidates, and every consumer must draw the identity from one module.
"""

from __future__ import annotations

import json
import sys

import pytest

from deskpet.sdk_adapters.runtime_paths import (
    SdkCandidateIdentity,
    verify_sdk_candidate,
)
from deskpet.sdk_adapters.sdk_candidate import (
    SDK_CANDIDATE_MANIFEST_FILENAME,
    SDK_CANDIDATE_MANIFEST_SHA256,
    SDK_SOURCE_COMMIT,
    SDK_VERSION,
    SDK_WHEEL_FILENAME,
    SDK_WHEEL_SHA256,
    build_candidate_identity,
    sdk_candidate_manifest_path,
    sdk_memory_wheel_path,
    sdk_wheel_path,
    verify_memory_candidate,
    verify_sdk_candidate_manifest,
)


def test_candidate_identity_passes_verify() -> None:
    identity = build_candidate_identity()
    assert verify_sdk_candidate(identity) == identity
    assert identity.version == SDK_VERSION
    assert identity.wheel_path.name == SDK_WHEEL_FILENAME


def test_wheel_file_matches_pinned_sha() -> None:
    import hashlib

    wheel = sdk_wheel_path()
    assert wheel.is_file()
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == SDK_WHEEL_SHA256


def test_candidate_manifest_binds_exact_wheel_version_and_source() -> None:
    import hashlib

    manifest_path = sdk_candidate_manifest_path()
    assert manifest_path.name == SDK_CANDIDATE_MANIFEST_FILENAME
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == SDK_CANDIDATE_MANIFEST_SHA256
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == SDK_VERSION
    assert manifest["commit"] == SDK_SOURCE_COMMIT
    assert manifest["artifacts"][SDK_WHEEL_FILENAME] == SDK_WHEEL_SHA256
    assert verify_sdk_candidate_manifest() is None


def test_candidate_manifest_tamper_fails_closed(tmp_path, monkeypatch) -> None:
    import deskpet.sdk_adapters.sdk_candidate as candidate

    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / SDK_CANDIDATE_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(candidate, "_sdk_artifact_root", lambda: vendor)
    with pytest.raises(RuntimeError, match="manifest SHA-256"):
        candidate.verify_sdk_candidate_manifest()


def test_frozen_candidate_paths_use_meipass_vendor(tmp_path, monkeypatch) -> None:
    import deskpet.sdk_adapters.sdk_candidate as candidate

    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert candidate.sdk_wheel_path() == tmp_path / "vendor" / SDK_WHEEL_FILENAME
    assert candidate.sdk_candidate_manifest_path() == tmp_path / "vendor" / SDK_CANDIDATE_MANIFEST_FILENAME


def test_pyinstaller_spec_bundles_distribution_wheel_and_manifest() -> None:
    spec = (sdk_wheel_path().parents[1] / "deskpet-backend.spec").read_text(encoding="utf-8")
    assert 'copy_metadata("simple-harness-sdk")' in spec
    assert f'("vendor/{SDK_WHEEL_FILENAME}", "vendor")' in spec
    assert f'("vendor/{SDK_CANDIDATE_MANIFEST_FILENAME}", "vendor")' in spec


def test_memory_candidate_exact_wheel_passes_verify() -> None:
    assert sdk_memory_wheel_path().is_file()
    assert verify_memory_candidate() is None


def test_identity_rejects_wrong_sha() -> None:
    identity = SdkCandidateIdentity(SDK_VERSION, "0" * 64, sdk_wheel_path())
    with pytest.raises(RuntimeError, match="SDK candidate"):
        verify_sdk_candidate(identity)


def test_identity_rejects_wrong_version() -> None:
    identity = SdkCandidateIdentity("0.0.0", SDK_WHEEL_SHA256, sdk_wheel_path())
    with pytest.raises(RuntimeError, match="SDK candidate"):
        verify_sdk_candidate(identity)


def test_identity_rejects_missing_wheel(tmp_path) -> None:
    identity = SdkCandidateIdentity(SDK_VERSION, SDK_WHEEL_SHA256, tmp_path / "gone.whl")
    with pytest.raises(RuntimeError, match="SDK candidate"):
        verify_sdk_candidate(identity)


def test_consumers_share_single_source_of_truth() -> None:
    """No consumer may keep its own hardcoded copy of the wheel identity."""

    import deskpet.sdk_adapters.desktop_runtime as desktop_runtime
    import deskpet.sdk_adapters.conformance as conformance
    import main

    assert not hasattr(desktop_runtime, "_SDK_VERSION")
    assert not hasattr(desktop_runtime, "_SDK_WHEEL_SHA256")
    assert conformance._WHEEL == sdk_wheel_path()
    assert main.SDK_VERSION is SDK_VERSION
    assert main.build_candidate_identity is build_candidate_identity

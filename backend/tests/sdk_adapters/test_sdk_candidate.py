# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Tests for the vendored-SDK-wheel single source of truth (sdk_candidate).

Covers S1-AC-2: the fail-closed chain must keep rejecting tampered/wrong
candidates, and every consumer must draw the identity from one module.
"""

from __future__ import annotations

import pytest

from deskpet.sdk_adapters.runtime_paths import (
    SdkCandidateIdentity,
    verify_sdk_candidate,
)
from deskpet.sdk_adapters.sdk_candidate import (
    SDK_SERVICE_AUTHORITY_ROOT_SHA256,
    SDK_SERVICE_VERSION,
    SDK_SERVICE_WHEEL_FILENAME,
    SDK_SERVICE_WHEEL_SHA256,
    SDK_VERSION,
    SDK_WHEEL_FILENAME,
    SDK_WHEEL_SHA256,
    build_candidate_identity,
    build_runtime_identity,
    sdk_service_candidate_manifest_path,
    sdk_service_wheel_path,
    sdk_wheel_path,
    verify_service_candidate,
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


# 2026-09-10：``test_memory_candidate_exact_wheel_passes_verify`` /
# ``test_memory_lock_matches_candidate_identity`` 随认知记忆 SDK 的 candidate
# 常量与 ``verify_memory_candidate`` 一并移除。


def test_service_candidate_exact_local_successor_passes_verify() -> None:
    assert sdk_service_wheel_path().name == SDK_SERVICE_WHEEL_FILENAME
    assert sdk_service_candidate_manifest_path().is_file()
    assert verify_service_candidate() is None
    assert SDK_SERVICE_VERSION == "0.3.13"
    assert SDK_SERVICE_WHEEL_SHA256 == (
        "26205f89854e27bd7ed8cbd6f7ac1f6b621603f973a081823bc6b707ae0784a8"
    )
    assert SDK_SERVICE_AUTHORITY_ROOT_SHA256 == (
        "b9675a5c64136bb9ba7064cc78b3cc39662f7f374629bcd4731a833bbff2873d"
    )


def test_frozen_service_candidate_ignores_build_host_direct_url(monkeypatch) -> None:
    from types import SimpleNamespace

    import deskpet.sdk_adapters.sdk_candidate as candidate

    vendor = candidate._REPO_ROOT / "backend" / "vendor"
    monkeypatch.setattr(candidate, "_service_vendor_root", lambda: vendor)
    monkeypatch.setattr(candidate.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        candidate.metadata,
        "distribution",
        lambda _name: SimpleNamespace(
            version=SDK_SERVICE_VERSION,
            read_text=lambda _filename: '{"url":"file:///build-host/not-runtime.whl"}',
        ),
    )

    assert verify_service_candidate() is None


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

    import main
    from deskpet.sdk_adapters import conformance, desktop_runtime

    assert not hasattr(desktop_runtime, "_SDK_VERSION")
    assert not hasattr(desktop_runtime, "_SDK_WHEEL_SHA256")
    assert conformance._WHEEL == sdk_wheel_path()
    assert main.SDK_VERSION is SDK_VERSION
    assert main.build_runtime_identity is build_runtime_identity


# 2026-09-10：``test_memory_candidate_wrong_wheel_hash_fails_closed``
# （S5A-AC-6 记忆 wheel 篡改 fail-closed）随记忆 candidate 校验一并移除。

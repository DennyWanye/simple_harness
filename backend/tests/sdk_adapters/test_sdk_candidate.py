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
    SDK_VERSION,
    SDK_WHEEL_FILENAME,
    SDK_WHEEL_SHA256,
    build_candidate_identity,
    build_runtime_identity,
    sdk_wheel_path,
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

    from deskpet.sdk_adapters import desktop_runtime
    from host import harness_chat, sdk_execution, sdk_runtime_stack

    from tests import conformance_host as conformance

    assert not hasattr(desktop_runtime, "_SDK_VERSION")
    assert not hasattr(desktop_runtime, "_SDK_WHEEL_SHA256")
    assert conformance._WHEEL == sdk_wheel_path()
    # 宿主里用到版本号的模块都取自同一个来源
    assert harness_chat.SDK_VERSION is SDK_VERSION
    assert sdk_execution.SDK_VERSION is SDK_VERSION
    assert sdk_runtime_stack.build_runtime_identity is build_runtime_identity


# 2026-09-10：``test_memory_candidate_wrong_wheel_hash_fails_closed``
# （S5A-AC-6 记忆 wheel 篡改 fail-closed）随记忆 candidate 校验一并移除。

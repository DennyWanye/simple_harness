# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Single source of truth for the vendored SDK wheel identity.

Every consumer (startup assembly, desktop bridge, conformance host, audit
scripts) must import the constants / builder from here instead of hardcoding
version strings, wheel filenames or SHA-256 digests. Switching the vendored
wheel means editing this file and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from deskpet.sdk_adapters.runtime_paths import (
    SdkCandidateIdentity,
    SdkSourceIdentity,
    load_sdk_source_identity,
    verify_runtime_identity,
)

SDK_VERSION = "0.13.0.dev20260925+opt.208"
SDK_WHEEL_FILENAME = "simple_harness_sdk-0.13.0.dev20260925+opt.208-py3-none-any.whl"
SDK_WHEEL_SHA256 = "5a109ed103d0d81e68ca84543c8b95c6803b92f2f32c0b6f75072b7e4ae29f8e"
SDK_CANDIDATE_MANIFEST_FILENAME = (
    "simple_harness_sdk-0.13.0.dev20260925+opt.208.candidate-manifest.json"
)
SDK_CANDIDATE_MANIFEST_SHA256 = "9b8f1ef0c8c56c06e52701495b8a9ae2d38d17e60291e6e95c14e5a2f52d459a"
SDK_SOURCE_COMMIT = "81269667a5b110d25d2c2201f95844bc02502d2d"
SDK_CI_RUN_ID = None
SDK_CI_ARTIFACT_ID = None

# An explicit local development choice; never inferred from DEV_MODE/PYTHONPATH.
SDK_RUNTIME_MODE_ENV = "DESKPET_SDK_RUNTIME_MODE"
SDK_SOURCE_ATTESTATION_ENV = "DESKPET_SDK_SOURCE_ATTESTATION"

# 2026-09-10：认知记忆 SDK（simple-harness-memory-sdk 0.6.38）已从 Host 整条
# 移除，其 candidate 常量与 ``verify_memory_candidate`` 一并删除。


_REPO_ROOT = Path(__file__).resolve().parents[3]


def _sdk_artifact_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root is not None:
        return Path(frozen_root).resolve() / "vendor"
    return _REPO_ROOT / "backend" / "vendor"


def sdk_wheel_path() -> Path:
    """Absolute source/frozen path of the vendored Harness SDK wheel."""

    return _sdk_artifact_root() / SDK_WHEEL_FILENAME


def sdk_candidate_manifest_path() -> Path:
    """Absolute source/frozen path of the Harness SDK candidate metadata."""

    return _sdk_artifact_root() / SDK_CANDIDATE_MANIFEST_FILENAME


def verify_sdk_candidate_manifest() -> None:
    """Bind the Harness SDK wheel to immutable candidate metadata."""

    manifest_path = sdk_candidate_manifest_path()
    if not manifest_path.is_file():
        raise RuntimeError("SDK candidate manifest is unavailable")
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SDK_CANDIDATE_MANIFEST_SHA256:
        raise RuntimeError("SDK candidate manifest SHA-256 mismatch")
    try:
        manifest = json.loads(raw)
        artifact_hash = manifest["artifacts"][SDK_WHEEL_FILENAME]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("SDK candidate manifest is invalid") from exc
    if (
        manifest.get("schema") != "simple-harness-candidate-manifest-v1"
        or manifest.get("version") != SDK_VERSION
        or manifest.get("commit") != SDK_SOURCE_COMMIT
        or artifact_hash != SDK_WHEEL_SHA256
    ):
        raise RuntimeError("SDK candidate manifest identity mismatch")


def build_candidate_identity() -> SdkCandidateIdentity:
    """Assemble the fail-closed candidate identity from the SSOT constants."""

    verify_sdk_candidate_manifest()
    return SdkCandidateIdentity(SDK_VERSION, SDK_WHEEL_SHA256, sdk_wheel_path())


def build_runtime_identity() -> SdkCandidateIdentity | SdkSourceIdentity:
    """Keep the wheel default; admit only an explicitly attested source run."""
    mode = os.environ.get(SDK_RUNTIME_MODE_ENV, "wheel")
    attestation = os.environ.get(SDK_SOURCE_ATTESTATION_ENV)
    if mode == "wheel":
        if attestation is not None:
            raise RuntimeError("SDK source attestation requires explicit editable-source mode")
        return build_candidate_identity()
    if mode != "editable-source":
        raise RuntimeError("SDK runtime mode is unknown")
    if getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None) is not None:
        raise RuntimeError("SDK source mode is unavailable in a frozen process")
    if not attestation or not Path(attestation).is_absolute():
        raise RuntimeError("SDK source mode requires an absolute attestation file path")
    # The original immutable baseline manifest remains verified. It is not an
    # assertion that the separately attested editable implementation equals it.
    return load_sdk_source_identity(build_candidate_identity(), Path(attestation))


def runtime_identity_report() -> dict[str, object]:
    """Source attestation is verified; wheel provenance keeps its existing gate.

    The orchestration version report has never verified installed wheel bytes.
    Do not turn its old version-only `consistent` field into such a claim.
    """
    mode = os.environ.get(SDK_RUNTIME_MODE_ENV, "wheel")
    if mode == "wheel" and SDK_SOURCE_ATTESTATION_ENV not in os.environ:
        return {
            "mode": "wheel",
            "source_verified": False,
            "installed_wheel_verified": False,
            "verification": "version-only",
        }
    identity = verify_runtime_identity(build_runtime_identity())
    if not isinstance(identity, SdkSourceIdentity):
        raise RuntimeError("SDK source identity was not selected")  # noqa: TRY004 - configuration failure
    return {
        "mode": "editable-source",
        "source_verified": True,
        "baseline_artifact_verified": True,
        "installed_wheel_verified": False,
        "verification": "source-snapshot-at-startup",
        "source": {
            "root": str(identity.root),
            "commit": identity.commit,
            "inputs_sha256": identity.inputs_sha256,
            "input_count": len(identity.inputs),
            "module_origins": {
                name: str(identity.root / "src" / name / "__init__.py")
                for name in ("simple_harness", "agent_orchestrator")
            },
        },
    }


__all__ = (
    "SDK_CANDIDATE_MANIFEST_FILENAME",
    "SDK_CANDIDATE_MANIFEST_SHA256",
    "SDK_CI_ARTIFACT_ID",
    "SDK_CI_RUN_ID",
    "SDK_RUNTIME_MODE_ENV",
    "SDK_SOURCE_ATTESTATION_ENV",
    "SDK_SOURCE_COMMIT",
    "SDK_VERSION",
    "SDK_WHEEL_FILENAME",
    "SDK_WHEEL_SHA256",
    "build_candidate_identity",
    "build_runtime_identity",
    "runtime_identity_report",
    "sdk_candidate_manifest_path",
    "sdk_wheel_path",
    "verify_sdk_candidate_manifest",
)

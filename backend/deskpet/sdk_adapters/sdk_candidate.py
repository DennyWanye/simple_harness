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
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlparse

from deskpet.sdk_adapters.runtime_paths import (
    SdkCandidateIdentity,
    SdkSourceIdentity,
    load_sdk_source_identity,
    verify_runtime_identity,
)

SDK_VERSION = "0.13.0.dev20260925+opt.131"
SDK_WHEEL_FILENAME = "simple_harness_sdk-0.13.0.dev20260925+opt.131-py3-none-any.whl"
SDK_WHEEL_SHA256 = "a050e393d66f5da8de03c5a9abb610a7ff45919fcb47ed78ae3a04d39feab27a"
SDK_CANDIDATE_MANIFEST_FILENAME = "simple_harness_sdk-0.13.0.dev20260925+opt.131.candidate-manifest.json"
SDK_CANDIDATE_MANIFEST_SHA256 = "a2e39fb46c93806fc62e66580fa70f31da0f68c3d49f2dc100c1918fbf9c7b83"
SDK_SOURCE_COMMIT = "3d218b140c837da5eb2063f79f113ba5d161eaa5"
SDK_CI_RUN_ID = None
SDK_CI_ARTIFACT_ID = None

# An explicit local development choice; never inferred from DEV_MODE/PYTHONPATH.
SDK_RUNTIME_MODE_ENV = "DESKPET_SDK_RUNTIME_MODE"
SDK_SOURCE_ATTESTATION_ENV = "DESKPET_SDK_SOURCE_ATTESTATION"

# 2026-09-10：认知记忆 SDK（simple-harness-memory-sdk 0.6.38）已从 Host 整条
# 移除，其 candidate 常量与 ``verify_memory_candidate`` 一并删除。

SDK_SERVICE_VERSION = "0.3.13"
SDK_SERVICE_WHEEL_FILENAME = "simple_harness_service_sdk-0.3.13-py3-none-any.whl"
SDK_SERVICE_WHEEL_SHA256 = "26205f89854e27bd7ed8cbd6f7ac1f6b621603f973a081823bc6b707ae0784a8"
SDK_SERVICE_SOURCE_COMMIT = "74a622572602bf3b6973f5d8a55c09bf62ff08ba"
SDK_SERVICE_AUTHORITY_ROOT_SHA256 = (
    "b9675a5c64136bb9ba7064cc78b3cc39662f7f374629bcd4731a833bbff2873d"
)
SDK_SERVICE_CANDIDATE_MANIFEST_FILENAME = (
    "simple_harness_service_sdk-0.3.13.candidate-manifest.json"
)
SDK_SERVICE_CANDIDATE_MANIFEST_SHA256 = (
    "eac1aa553aebe97df145eb257bbd06b930e5b57b25ada3c38fa632b84b87d319"
)

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


def _service_vendor_root() -> Path:
    return _sdk_artifact_root()


def sdk_service_wheel_path() -> Path:
    """Exact Service SDK release artifact bundled with this product."""

    return _service_vendor_root() / SDK_SERVICE_WHEEL_FILENAME


def sdk_service_candidate_manifest_path() -> Path:
    return _service_vendor_root() / SDK_SERVICE_CANDIDATE_MANIFEST_FILENAME


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
        return {"mode": "wheel", "source_verified": False,
                "installed_wheel_verified": False, "verification": "version-only"}
    identity = verify_runtime_identity(build_runtime_identity())
    if not isinstance(identity, SdkSourceIdentity):
        raise RuntimeError("SDK source identity was not selected")  # noqa: TRY004 - configuration failure
    return {
        "mode": "editable-source", "source_verified": True,
        "baseline_artifact_verified": True, "installed_wheel_verified": False,
        "verification": "source-snapshot-at-startup",
        "source": {
            "root": str(identity.root), "commit": identity.commit,
            "inputs_sha256": identity.inputs_sha256, "input_count": len(identity.inputs),
            "module_origins": {name: str(identity.root / "src" / name / "__init__.py")
                               for name in ("simple_harness", "agent_orchestrator")},
        },
    }


def verify_service_candidate() -> None:
    """Verify Service SDK bytes, metadata, release unit and authority root."""

    wheel = sdk_service_wheel_path().resolve()
    manifest_path = sdk_service_candidate_manifest_path().resolve()
    if (
        not wheel.is_file()
        or hashlib.sha256(wheel.read_bytes()).hexdigest() != SDK_SERVICE_WHEEL_SHA256
    ):
        raise RuntimeError("Service SDK candidate wheel SHA-256 mismatch")
    if (
        not manifest_path.is_file()
        or hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        != SDK_SERVICE_CANDIDATE_MANIFEST_SHA256
    ):
        raise RuntimeError("Service SDK candidate manifest SHA-256 mismatch")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        service = next(
            member
            for member in manifest["sdk_release_unit"]["members"]
            if member["role"] == "service"
        )
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, StopIteration) as exc:
        raise RuntimeError("Service SDK candidate manifest invalid") from exc
    if (
        manifest.get("schema") != "simple-harness-service-candidate-manifest-v2"
        or manifest.get("source", {}).get("commit") != SDK_SERVICE_SOURCE_COMMIT
        or manifest.get("authority", {}).get("root_sha256")
        != SDK_SERVICE_AUTHORITY_ROOT_SHA256
        or service.get("version") != SDK_SERVICE_VERSION
        or service.get("wheel") != SDK_SERVICE_WHEEL_FILENAME
        or service.get("sha256") != SDK_SERVICE_WHEEL_SHA256
    ):
        raise RuntimeError("Service SDK candidate manifest binding mismatch")
    distribution = metadata.distribution("simple-harness-service-sdk")
    if distribution.version != SDK_SERVICE_VERSION:
        raise RuntimeError("Service SDK candidate installed version mismatch")
    # A frozen executable has already proven the bundled release wheel and
    # candidate manifest bytes above. PyInstaller may copy the build venv's
    # direct_url.json into dist-info, but that build-host path is neither
    # authoritative nor meaningful after extraction under ``sys._MEIPASS``.
    if getattr(sys, "frozen", False):
        return
    direct_url_raw = distribution.read_text("direct_url.json")
    if direct_url_raw is None:
        raise RuntimeError("Service SDK candidate installed origin unavailable")
    try:
        parsed = urlparse(str(json.loads(direct_url_raw)["url"]))
        installed_path = Path(unquote(parsed.path)).resolve()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Service SDK candidate installed origin invalid") from exc
    if parsed.scheme != "file" or installed_path != wheel:
        raise RuntimeError("Service SDK candidate installed origin mismatch")


__all__ = (
    "SDK_CANDIDATE_MANIFEST_FILENAME",
    "SDK_CANDIDATE_MANIFEST_SHA256",
    "SDK_CI_ARTIFACT_ID",
    "SDK_CI_RUN_ID",
    "SDK_RUNTIME_MODE_ENV",
    "SDK_SERVICE_AUTHORITY_ROOT_SHA256",
    "SDK_SERVICE_CANDIDATE_MANIFEST_FILENAME",
    "SDK_SERVICE_CANDIDATE_MANIFEST_SHA256",
    "SDK_SERVICE_SOURCE_COMMIT",
    "SDK_SERVICE_VERSION",
    "SDK_SERVICE_WHEEL_FILENAME",
    "SDK_SERVICE_WHEEL_SHA256",
    "SDK_SOURCE_ATTESTATION_ENV",
    "SDK_SOURCE_COMMIT",
    "SDK_VERSION",
    "SDK_WHEEL_FILENAME",
    "SDK_WHEEL_SHA256",
    "build_candidate_identity",
    "build_runtime_identity",
    "runtime_identity_report",
    "sdk_candidate_manifest_path",
    "sdk_service_candidate_manifest_path",
    "sdk_service_wheel_path",
    "sdk_wheel_path",
    "verify_sdk_candidate_manifest",
    "verify_service_candidate",
)

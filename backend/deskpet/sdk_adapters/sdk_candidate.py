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
import sys
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlparse

from deskpet.sdk_adapters.runtime_paths import SdkCandidateIdentity

SDK_VERSION = "0.7.8"
SDK_WHEEL_FILENAME = "simple_harness_sdk-0.7.8-py3-none-any.whl"
SDK_WHEEL_SHA256 = "5aa1112803b5b617142b015f4989c679b455f34444f512a4ede957161b41138e"
SDK_CANDIDATE_MANIFEST_FILENAME = "simple_harness_sdk-0.7.8.candidate-manifest.json"
SDK_CANDIDATE_MANIFEST_SHA256 = "6a4292634fa0666593d73aba876aff223ef043687c82ec3a864310e3ae8013b6"
SDK_SOURCE_COMMIT = "f778cba9c5ee599e7ff5ac55796d0f331d215f62"
SDK_CI_RUN_ID = None
SDK_CI_ARTIFACT_ID = None

SDK_MEMORY_VERSION = "0.6.18"
SDK_MEMORY_WHEEL_FILENAME = "simple_harness_memory_sdk-0.6.18-py3-none-any.whl"
SDK_MEMORY_WHEEL_SHA256 = "010b4281c7b1149e32858939119262d97cedb8528ddd8bb62b87940dcefbe1cb"
SDK_MEMORY_SOURCE_COMMIT = "d8d80d5c00f489e7d85a7d1df613f73e489b4cc2"
SDK_MEMORY_CI_RUN_ID = None
SDK_MEMORY_CI_ARTIFACT_ID = None

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


def sdk_memory_wheel_path() -> Path:
    """Absolute path of the vendored memory SDK wheel under backend/vendor/."""

    return _REPO_ROOT / "backend" / "vendor" / SDK_MEMORY_WHEEL_FILENAME


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


def verify_memory_candidate() -> None:
    """Fail closed unless the installed Memory SDK is the vendored wheel bytes."""

    wheel = sdk_memory_wheel_path().resolve()
    if (
        not wheel.is_file()
        or hashlib.sha256(wheel.read_bytes()).hexdigest()
        != SDK_MEMORY_WHEEL_SHA256
    ):
        raise RuntimeError("Memory SDK candidate wheel SHA-256 mismatch")
    distribution = metadata.distribution("simple-harness-memory-sdk")
    if distribution.version != SDK_MEMORY_VERSION:
        raise RuntimeError("Memory SDK candidate installed version mismatch")
    direct_url_raw = distribution.read_text("direct_url.json")
    try:
        parsed = urlparse(str(json.loads(direct_url_raw or "")["url"]))
        installed_path = Path(unquote(parsed.path)).resolve()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Memory SDK candidate installed origin is invalid") from exc
    if parsed.scheme != "file" or installed_path != wheel:
        raise RuntimeError("Memory SDK candidate installed origin mismatch")


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
    "SDK_MEMORY_CI_ARTIFACT_ID",
    "SDK_MEMORY_CI_RUN_ID",
    "SDK_MEMORY_SOURCE_COMMIT",
    "SDK_MEMORY_VERSION",
    "SDK_MEMORY_WHEEL_FILENAME",
    "SDK_MEMORY_WHEEL_SHA256",
    "SDK_SERVICE_AUTHORITY_ROOT_SHA256",
    "SDK_SERVICE_CANDIDATE_MANIFEST_FILENAME",
    "SDK_SERVICE_CANDIDATE_MANIFEST_SHA256",
    "SDK_SERVICE_SOURCE_COMMIT",
    "SDK_SERVICE_VERSION",
    "SDK_SERVICE_WHEEL_FILENAME",
    "SDK_SERVICE_WHEEL_SHA256",
    "SDK_SOURCE_COMMIT",
    "SDK_VERSION",
    "SDK_WHEEL_FILENAME",
    "SDK_WHEEL_SHA256",
    "build_candidate_identity",
    "sdk_candidate_manifest_path",
    "sdk_memory_wheel_path",
    "sdk_service_candidate_manifest_path",
    "sdk_service_wheel_path",
    "sdk_wheel_path",
    "verify_memory_candidate",
    "verify_sdk_candidate_manifest",
    "verify_service_candidate",
)

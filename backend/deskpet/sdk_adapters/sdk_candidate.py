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

SDK_VERSION = "0.7.1"
SDK_WHEEL_FILENAME = "simple_harness_sdk-0.7.1-py3-none-any.whl"
SDK_WHEEL_SHA256 = "4d5d2b7ba5c2f8ef4956af77769d75e1ac7889a037acbdcf853d0b9a5b3a3218"
SDK_CANDIDATE_MANIFEST_FILENAME = "simple_harness_sdk-0.7.1.candidate-manifest.json"
SDK_CANDIDATE_MANIFEST_SHA256 = "e751c68fa729812e29ad5fe71a786b697e9531343552c5ae3052d6c4c41d1f6e"
SDK_SOURCE_COMMIT = "f5fe0dc7e8c5b521444e01c40cab176f3666c627"
SDK_CI_RUN_ID = None
SDK_CI_ARTIFACT_ID = None

SDK_MEMORY_VERSION = "0.5.2"
SDK_MEMORY_WHEEL_FILENAME = "simple_harness_memory_sdk-0.5.2-py3-none-any.whl"
SDK_MEMORY_WHEEL_SHA256 = "deff2fa85a269a3978f2c6efcd99fda77abcb74444170361365fd00ec0164e9e"
SDK_MEMORY_SOURCE_COMMIT = "46624b5c49f2c0a64a522eca64d6eb798823370e"
SDK_MEMORY_CI_RUN_ID = None
SDK_MEMORY_CI_ARTIFACT_ID = None

SDK_SERVICE_VERSION = "0.3.12"
SDK_SERVICE_WHEEL_FILENAME = "simple_harness_service_sdk-0.3.12-py3-none-any.whl"
SDK_SERVICE_WHEEL_SHA256 = "710ae66ba1cc0f0f838f816f3b98108100af560bfb210ed6834246d6d802f8c6"
SDK_SERVICE_SOURCE_COMMIT = "47f372adc641d8d3516599dd21cb94cf5955d6a7"
SDK_SERVICE_AUTHORITY_ROOT_SHA256 = (
    "b9675a5c64136bb9ba7064cc78b3cc39662f7f374629bcd4731a833bbff2873d"
)
SDK_SERVICE_CANDIDATE_MANIFEST_FILENAME = (
    "simple_harness_service_sdk-0.3.12.candidate-manifest.json"
)
SDK_SERVICE_CANDIDATE_MANIFEST_SHA256 = (
    "9bfb8731a4e8aba2958fcd0999b888a1c25a0f223c2c6ee309500a02ecd213cd"
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
    "sdk_memory_wheel_path",
    "sdk_candidate_manifest_path",
    "sdk_service_candidate_manifest_path",
    "sdk_service_wheel_path",
    "sdk_wheel_path",
    "verify_memory_candidate",
    "verify_sdk_candidate_manifest",
    "verify_service_candidate",
)

# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Single source of truth for the vendored SDK wheel identity.

Every consumer (startup assembly, desktop bridge, conformance host, audit
scripts) must import the constants / builder from here instead of hardcoding
version strings, wheel filenames or SHA-256 digests. Switching the vendored
wheel means editing this file and nothing else.
"""

from __future__ import annotations

from pathlib import Path
import hashlib
from importlib import metadata
import json
import sys
from urllib.parse import unquote, urlparse

from deskpet.sdk_adapters.runtime_paths import SdkCandidateIdentity

SDK_VERSION = "0.6.3"
SDK_WHEEL_FILENAME = "simple_harness_sdk-0.6.3-py3-none-any.whl"
SDK_WHEEL_SHA256 = "c0ce056b79db74cec12a5155cc58fa0e12d6907cbb6b4833eb0dff916d71e31b"
SDK_CANDIDATE_MANIFEST_FILENAME = "simple_harness_sdk-0.6.3.candidate-manifest.json"
SDK_CANDIDATE_MANIFEST_SHA256 = "bcb380891f405c4ada7cb13eb57ac8129ef0062e00cc9a5910c9feb6fe0863bc"
SDK_SOURCE_COMMIT = "01ad47825108c217f078f4da080f45d78a0efb31"
SDK_CI_RUN_ID = None
SDK_CI_ARTIFACT_ID = None

SDK_MEMORY_VERSION = "0.5.2"
SDK_MEMORY_WHEEL_FILENAME = "simple_harness_memory_sdk-0.5.2-py3-none-any.whl"
SDK_MEMORY_WHEEL_SHA256 = "deff2fa85a269a3978f2c6efcd99fda77abcb74444170361365fd00ec0164e9e"
SDK_MEMORY_SOURCE_COMMIT = "46624b5c49f2c0a64a522eca64d6eb798823370e"
SDK_MEMORY_CI_RUN_ID = None
SDK_MEMORY_CI_ARTIFACT_ID = None

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _sdk_artifact_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root is not None:
        return Path(frozen_root) / "vendor"
    return _REPO_ROOT / "backend" / "vendor"


def sdk_wheel_path() -> Path:
    """Absolute path of the vendored wheel under backend/vendor/."""

    return _sdk_artifact_root() / SDK_WHEEL_FILENAME


def sdk_candidate_manifest_path() -> Path:
    """Absolute source/frozen path of the signed candidate metadata."""

    return _sdk_artifact_root() / SDK_CANDIDATE_MANIFEST_FILENAME


def verify_sdk_candidate_manifest() -> None:
    """Bind the vendored wheel to the downloaded immutable release metadata."""

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


__all__ = (
    "SDK_VERSION",
    "SDK_WHEEL_FILENAME",
    "SDK_WHEEL_SHA256",
    "SDK_CANDIDATE_MANIFEST_FILENAME",
    "SDK_CANDIDATE_MANIFEST_SHA256",
    "SDK_SOURCE_COMMIT",
    "SDK_CI_RUN_ID",
    "SDK_CI_ARTIFACT_ID",
    "SDK_MEMORY_VERSION",
    "SDK_MEMORY_WHEEL_FILENAME",
    "SDK_MEMORY_WHEEL_SHA256",
    "SDK_MEMORY_SOURCE_COMMIT",
    "SDK_MEMORY_CI_RUN_ID",
    "SDK_MEMORY_CI_ARTIFACT_ID",
    "build_candidate_identity",
    "verify_sdk_candidate_manifest",
    "verify_memory_candidate",
    "sdk_wheel_path",
    "sdk_candidate_manifest_path",
    "sdk_memory_wheel_path",
)

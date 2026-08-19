"""Single source of truth for the vendored SDK wheel identity.

Every consumer (startup assembly, desktop bridge, conformance host, audit
scripts) must import the constants / builder from here instead of hardcoding
version strings, wheel filenames or SHA-256 digests. Switching the vendored
wheel means editing this file and nothing else.
"""

from __future__ import annotations

from pathlib import Path

from deskpet.sdk_adapters.runtime_paths import SdkCandidateIdentity

SDK_VERSION = "0.1.4"
SDK_WHEEL_FILENAME = "simple_harness_sdk-0.1.4-py3-none-any.whl"
SDK_WHEEL_SHA256 = "4766ededa6145e628519153d570520271f9aca0fc0aaf0afea6e401a99679e39"

SDK_MEMORY_VERSION = "0.2.0"
SDK_MEMORY_WHEEL_FILENAME = "simple_harness_memory_sdk-0.2.0-py3-none-any.whl"
SDK_MEMORY_WHEEL_SHA256 = "15feac345e07c4fccf2f8adde7fe080bd6ac09eabb81c95430cce77fd34f49cc"

_REPO_ROOT = Path(__file__).resolve().parents[3]


def sdk_wheel_path() -> Path:
    """Absolute path of the vendored wheel under backend/vendor/."""

    return _REPO_ROOT / "backend" / "vendor" / SDK_WHEEL_FILENAME


def sdk_memory_wheel_path() -> Path:
    """Absolute path of the vendored memory SDK wheel under backend/vendor/."""

    return _REPO_ROOT / "backend" / "vendor" / SDK_MEMORY_WHEEL_FILENAME


def build_candidate_identity() -> SdkCandidateIdentity:
    """Assemble the fail-closed candidate identity from the SSOT constants."""

    return SdkCandidateIdentity(SDK_VERSION, SDK_WHEEL_SHA256, sdk_wheel_path())


__all__ = (
    "SDK_VERSION",
    "SDK_WHEEL_FILENAME",
    "SDK_WHEEL_SHA256",
    "SDK_MEMORY_VERSION",
    "SDK_MEMORY_WHEEL_FILENAME",
    "SDK_MEMORY_WHEEL_SHA256",
    "build_candidate_identity",
    "sdk_wheel_path",
    "sdk_memory_wheel_path",
)

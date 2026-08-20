"""Single source of truth for the vendored SDK wheel identity.

Every consumer (startup assembly, desktop bridge, conformance host, audit
scripts) must import the constants / builder from here instead of hardcoding
version strings, wheel filenames or SHA-256 digests. Switching the vendored
wheel means editing this file and nothing else.
"""

from __future__ import annotations

from pathlib import Path

from deskpet.sdk_adapters.runtime_paths import SdkCandidateIdentity

SDK_VERSION = "0.1.5"
SDK_WHEEL_FILENAME = "simple_harness_sdk-0.1.5-py3-none-any.whl"
SDK_WHEEL_SHA256 = "1551735127e1b91be629bc23ef0369ffbba4aa02c12d1594123a4fca7f5522b6"

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

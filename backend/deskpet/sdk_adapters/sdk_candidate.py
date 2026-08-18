"""Single source of truth for the vendored SDK wheel identity.

Every consumer (startup assembly, desktop bridge, conformance host, audit
scripts) must import the constants / builder from here instead of hardcoding
version strings, wheel filenames or SHA-256 digests. Switching the vendored
wheel means editing this file and nothing else.
"""

from __future__ import annotations

from pathlib import Path

from deskpet.sdk_adapters.runtime_paths import SdkCandidateIdentity

SDK_VERSION = "0.1.2"
SDK_WHEEL_FILENAME = "simple_harness_sdk-0.1.2-py3-none-any.whl"
SDK_WHEEL_SHA256 = "387c8d1d97c0f89e4664347fb57ca6a43a0e7fa772b07a0f34c6f3a6e86efd4c"

_REPO_ROOT = Path(__file__).resolve().parents[3]


def sdk_wheel_path() -> Path:
    """Absolute path of the vendored wheel under backend/vendor/."""

    return _REPO_ROOT / "backend" / "vendor" / SDK_WHEEL_FILENAME


def build_candidate_identity() -> SdkCandidateIdentity:
    """Assemble the fail-closed candidate identity from the SSOT constants."""

    return SdkCandidateIdentity(SDK_VERSION, SDK_WHEEL_SHA256, sdk_wheel_path())


__all__ = (
    "SDK_VERSION",
    "SDK_WHEEL_FILENAME",
    "SDK_WHEEL_SHA256",
    "build_candidate_identity",
    "sdk_wheel_path",
)

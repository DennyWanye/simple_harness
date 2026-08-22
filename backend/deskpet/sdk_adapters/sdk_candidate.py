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
from urllib.parse import unquote, urlparse

from deskpet.sdk_adapters.runtime_paths import SdkCandidateIdentity

SDK_VERSION = "0.3.0"
SDK_WHEEL_FILENAME = "simple_harness_sdk-0.3.0-py3-none-any.whl"
SDK_WHEEL_SHA256 = "3740d26b95f11e638258969b6e1aa83138c31b959f920d9f060d6e0c73e550c2"
SDK_SOURCE_COMMIT = "f96e80804e2300c2c88518df25e04d8570f1bd99"
SDK_CI_RUN_ID = None
SDK_CI_ARTIFACT_ID = None

SDK_MEMORY_VERSION = "0.4.0"
SDK_MEMORY_WHEEL_FILENAME = "simple_harness_memory_sdk-0.4.0-py3-none-any.whl"
SDK_MEMORY_WHEEL_SHA256 = "81484a81f6a8dc1efb92d9b4b946c2152139c75b794750a241b9388c5cddf5a5"
SDK_MEMORY_SOURCE_COMMIT = "87f1daee0d5f57955911d1862c9f67300faa818f"
SDK_MEMORY_CI_RUN_ID = None
SDK_MEMORY_CI_ARTIFACT_ID = None

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
    "verify_memory_candidate",
    "sdk_wheel_path",
    "sdk_memory_wheel_path",
)

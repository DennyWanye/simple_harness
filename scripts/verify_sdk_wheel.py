#!/usr/bin/env python3
"""Verify SDK wheel integrity before vendoring.

Usage:
    python scripts/verify_sdk_wheel.py backend/vendor/simple_harness_sdk-0.1.2-py3-none-any.whl

Validates:
- Wheel file exists and is readable
- SHA256 matches the reviewed immutable artifact hash
- Version metadata matches expected version
"""

import hashlib
import sys
import zipfile
from pathlib import Path


# Historical immutable artifact hashes (audit trail, no longer active).
HISTORICAL_HASH = {
    "0.1.0": "d9a1d4f94f826cdf97fb1c23085c85e727400a92f725c7022b0ebf63a18f4d91",
    "0.1.1": "d32212c8cbdb27349a75c1437728e035d8103515361896fc30dbed947b8ed9ca",
}

# The active wheel identity comes from the single source of truth so this
# script never drifts from what the product actually pins.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from deskpet.sdk_adapters.sdk_candidate import SDK_VERSION, SDK_WHEEL_SHA256

EXPECTED_HASH = {**HISTORICAL_HASH, SDK_VERSION: SDK_WHEEL_SHA256}


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def extract_version_from_wheel(wheel_path: Path) -> str:
    """Extract version from wheel metadata."""
    with zipfile.ZipFile(wheel_path) as whl:
        metadata_path = None
        for name in whl.namelist():
            if name.endswith("/METADATA"):
                metadata_path = name
                break

        if not metadata_path:
            raise ValueError("No METADATA file found in wheel")

        with whl.open(metadata_path) as f:
            for line in f:
                line = line.decode("utf-8").strip()
                if line.startswith("Version:"):
                    return line.split(":", 1)[1].strip()

    raise ValueError("No Version field found in METADATA")


def verify_wheel(wheel_path: Path) -> None:
    """Verify wheel integrity."""
    if not wheel_path.exists():
        print(f"❌ Wheel not found: {wheel_path}")
        sys.exit(1)

    print(f"Verifying wheel: {wheel_path.name}")
    print()

    # Extract version
    try:
        version = extract_version_from_wheel(wheel_path)
        print(f"✓ Version from metadata: {version}")
    except Exception as e:
        print(f"❌ Failed to extract version: {e}")
        sys.exit(1)

    # Check expected hash exists
    if version not in EXPECTED_HASH:
        print(f"❌ No expected hash for version {version}")
        print(f"   Available versions: {list(EXPECTED_HASH.keys())}")
        sys.exit(1)

    expected_hash = EXPECTED_HASH[version]

    if expected_hash == "PLACEHOLDER_UPDATE_AFTER_RELEASE":
        print(f"⚠️  Expected hash not yet updated")
        print(f"   Download SHA256SUMS from Release and update EXPECTED_HASH")
        print()
        print(f"   GitHub Release: https://github.com/DennyWanye/simple-harness-sdk/releases/tag/v{version}")
        print()

        # Still compute actual hash for reference
        actual_hash = compute_sha256(wheel_path)
        print(f"Actual SHA256: {actual_hash}")
        print()
        print(f"After verifying Release SHA256SUMS, update this script:")
        print(f'    EXPECTED_HASH["{version}"] = "{actual_hash}"')
        sys.exit(1)

    # Verify hash
    print(f"Expected SHA256: {expected_hash}")
    actual_hash = compute_sha256(wheel_path)
    print(f"Actual SHA256:   {actual_hash}")

    if actual_hash != expected_hash:
        print()
        print(f"❌ Hash mismatch!")
        print(f"   Wheel may be corrupted or tampered with")
        sys.exit(1)

    print()
    print(f"✓ Wheel integrity verified")
    print(f"  Version: {version}")
    print(f"  SHA256: {actual_hash}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/verify_sdk_wheel.py <wheel_path>")
        sys.exit(1)

    wheel_path = Path(sys.argv[1])
    verify_wheel(wheel_path)

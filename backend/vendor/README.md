# Vendored Dependencies

This directory contains exact wheel artifacts from trusted sources, verified via cryptographic hash before vendoring.

## Active candidate: simple_harness_sdk-0.1.1-py3-none-any.whl

**Source:** local immutable candidate built from SDK commit
`82fb531f0f1fd5aab027e9d9b016a1aee6475066`
**Planned tag:** `v0.1.1` (not created or published)
**SHA256:** `d32212c8cbdb27349a75c1437728e035d8103515361896fc30dbed947b8ed9ca`
**Candidate manifest SHA256:** `7b6eb38a429b966d39ae0aad5a78ca056ae1884c4e4e4199fbc13ec1aadf1334`

The earlier local candidates were superseded by the workflow recovery and
typed Tool authority fixes in the active bytes above. A replacement machine
receipt must bind this exact wheel before release.

The product lockfile must point to these exact bytes while Slice B/C testing is
in progress. This candidate must not be described as a GitHub Release until the
user separately approves publication.

Verification:

```bash
python scripts/verify_sdk_wheel.py backend/vendor/simple_harness_sdk-0.1.1-py3-none-any.whl
```

## Historical artifact: simple_harness_sdk-0.1.0-py3-none-any.whl

**Source:** GitHub Release `v0.1.0`  
**URL:** https://github.com/DennyWanye/simple-harness-sdk/releases/tag/v0.1.0  
**SHA256:** (see `scripts/verify_sdk_wheel.py` for expected hash)

### Release verification procedure

Before vendoring a new SDK wheel:

1. Download wheel and SHA256SUMS from GitHub Release:
   ```bash
   curl -LO https://github.com/DennyWanye/simple-harness-sdk/releases/download/v0.1.0/simple_harness_sdk-0.1.0-py3-none-any.whl
   curl -LO https://github.com/DennyWanye/simple-harness-sdk/releases/download/v0.1.0/SHA256SUMS
   ```

2. Verify checksum matches Release SHA256SUMS:
   ```bash
   sha256sum --ignore-missing -c SHA256SUMS
   ```

3. Update `scripts/verify_sdk_wheel.py` with expected hash from SHA256SUMS

4. Run verification script:
   ```bash
   python scripts/verify_sdk_wheel.py backend/vendor/simple_harness_sdk-0.1.0-py3-none-any.whl
   ```

5. Copy verified wheel to `backend/vendor/`

6. Run `uv sync` to update `uv.lock` with new wheel hash

7. Verify `uv.lock` contains wheel hash and path reference

### PyInstaller Collection

The PyInstaller spec (`build-msi.ps1` / frozen build) automatically collects SDK modules via normal import analysis. No manual `datas` or `hiddenimports` entries needed — the SDK is a pure-Python wheel with standard package structure.

### Lock File Hash

`uv.lock` records the SHA256 of the vendored wheel. Any modification to the wheel breaks the lock and fails `uv sync`. This ensures the exact verified bytes are used in every build.

### Version Updates

When updating to a new SDK version:

1. Download new wheel from corresponding GitHub Release
2. Verify via `scripts/verify_sdk_wheel.py` (update EXPECTED_HASH first)
3. Replace old wheel in `backend/vendor/`
4. Update `backend/pyproject.toml` path reference to new wheel filename
5. Run `uv sync` to update lock
6. Commit wheel + pyproject.toml + uv.lock together

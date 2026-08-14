# Vendored Dependencies

This directory contains exact wheel artifacts from trusted sources, verified via cryptographic hash before vendoring.

## simple_harness_sdk-0.1.0-py3-none-any.whl

**Source:** GitHub Release `v0.1.0`  
**URL:** https://github.com/DennyWanye/simple-harness-sdk/releases/tag/v0.1.0  
**SHA256:** (see `scripts/verify_sdk_wheel.py` for expected hash)

### Verification Procedure

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

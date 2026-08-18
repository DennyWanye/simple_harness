# Vendored Dependencies

This directory contains exact wheel artifacts from trusted sources, verified via cryptographic hash before vendoring.

## Active candidate: simple_harness_sdk-0.1.2-py3-none-any.whl

**Source:** local build from SDK repository HEAD `896b685`（含 cb1f245 consumer adapter layer；
`git tag v0.1.2` 存在但 GitHub Release 未发布，dist/ 未入 git）
**SHA256:** `387c8d1d97c0f89e4664347fb57ca6a43a0e7fa772b07a0f34c6f3a6e86efd4c`
**Vendored:** 2026-08-19（sdk-usability-optimization program, Slice 1）

v0.1.2 对 v0.1.1 为纯新增（consumer adapter 层 + Memory ports + 文档/示例），宿主 10-Port
适配层无需修改。wheel 身份的单一事实源是
`backend/deskpet/sdk_adapters/sdk_candidate.py`——切换版本只改那一个文件；
`scripts/verify_sdk_wheel.py` 的 active 预期 hash 也从该模块 import，不再自行硬编码。

Verification:

```bash
backend/.venv/bin/python scripts/verify_sdk_wheel.py backend/vendor/simple_harness_sdk-0.1.2-py3-none-any.whl
```

## Historical artifact: simple_harness_sdk-0.1.1-py3-none-any.whl

**Source:** local immutable candidate built from SDK commit
`82fb531f0f1fd5aab027e9d9b016a1aee6475066`
**SHA256:** `d32212c8cbdb27349a75c1437728e035d8103515361896fc30dbed947b8ed9ca`
**Status:** superseded by v0.1.2 on 2026-08-19; retained for audit rollback.

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
2. Update the single source of truth `backend/deskpet/sdk_adapters/sdk_candidate.py`
   (version + filename + SHA256); `scripts/verify_sdk_wheel.py` picks it up automatically
3. Replace old wheel in `backend/vendor/`
4. Update `backend/pyproject.toml` path reference to new wheel filename
5. Run `uv sync` to update lock
6. Commit wheel + pyproject.toml + uv.lock together

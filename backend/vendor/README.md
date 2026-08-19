# Vendored Dependencies

This directory contains exact wheel artifacts from trusted sources, verified via cryptographic hash before vendoring.

## Active candidate: simple_harness_sdk-0.1.4-py3-none-any.whl

**Source:** local build from `simple-harness-sdk`（sdk-productionization H1：发布阻断收尾）
**SHA256:** `4766ededa6145e628519153d570520271f9aca0fc0aaf0afea6e401a99679e39`
**Vendored:** 2026-08-20（sdk-productionization C1）

wheel 身份单一事实源 `backend/deskpet/sdk_adapters/sdk_candidate.py`。

## Active candidate: simple_harness_memory_sdk-0.2.0-py3-none-any.whl

**Source:** local build from `simple-harness-memory-sdk`（sdk-productionization M1-M4：召回只读 /
持久化加固 / 删除 lineage 上限 / 云端 embedding）
**SHA256:** `15feac345e07c4fccf2f8adde7fe080bd6ac09eabb81c95430cce77fd34f49cc`
**Vendored:** 2026-08-20（sdk-productionization C1）

Verification:

```bash
backend/.venv/bin/python scripts/verify_sdk_wheel.py backend/vendor/simple_harness_sdk-0.1.4-py3-none-any.whl
shasum -a 256 backend/vendor/simple_harness_memory_sdk-0.2.0-py3-none-any.whl
```

## Historical artifact: simple_harness_memory_sdk-0.1.0-py3-none-any.whl

**SHA256:** `02f197a3aab060ec9c7cc6063866a955f261728e38c64ff5a51ca72ffccfd9da`
**Status:** superseded by v0.2.0 on 2026-08-20; retained for audit rollback.

## Historical artifact: simple_harness_sdk-0.1.3-py3-none-any.whl

**SHA256:** `81025b2ccf08a0f49e272416176f8fdeead994e088e5d7a44e103ed5e902a7b9`
**Status:** superseded by v0.1.4 on 2026-08-20; retained for audit rollback.

## Historical artifact: simple_harness_sdk-0.1.2-py3-none-any.whl

**Source:** local build from SDK repository HEAD `896b685`（含 cb1f245 consumer adapter layer）
**SHA256:** `387c8d1d97c0f89e4664347fb57ca6a43a0e7fa772b07a0f34c6f3a6e86efd4c`
**Status:** superseded by v0.1.3 on 2026-08-19; retained for audit rollback.

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

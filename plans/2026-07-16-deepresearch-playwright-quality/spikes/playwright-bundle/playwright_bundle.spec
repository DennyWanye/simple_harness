"""Disposable Gate A spec derived from backend/deskpet-backend.spec.

It preserves the production onedir EXE/COLLECT shape while replacing the
DeskPet entrypoint with a deterministic render probe. The browser source is an
external build cache supplied via DESKPET_SPIKE_BROWSER_ROOT; nothing is copied
into the repository or the shared backend dist.
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


browser_root = Path(os.environ["DESKPET_SPIKE_BROWSER_ROOT"]).resolve()
if not browser_root.is_dir():
    raise SystemExit(f"missing DESKPET_SPIKE_BROWSER_ROOT: {browser_root}")
browser_directory = browser_root / "chromium_headless_shell-1228"
browser_executable = (
    browser_directory
    / "chrome-headless-shell-win64"
    / "chrome-headless-shell.exe"
)
installation_marker = browser_directory / "INSTALLATION_COMPLETE"
if not browser_executable.is_file() or not installation_marker.is_file():
    raise SystemExit(
        "browser root is not an atomically published Playwright 1.61 registry: "
        f"{browser_root}"
    )

spike_dir = Path(SPECPATH).resolve()
entrypoint = spike_dir / "spike_render.py"

datas = collect_data_files("playwright")
datas.append(
    (
        str(browser_directory),
        "playwright-browsers/chromium_headless_shell-1228",
    )
)
hiddenimports = collect_submodules("playwright")

a = Analysis(
    [str(entrypoint)],
    pathex=[str(spike_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "_pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="deskpet-playwright-bundle-spike",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="deskpet-playwright-bundle-spike",
)

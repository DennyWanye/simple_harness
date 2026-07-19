# Disposable Playwright bundle spike

This directory contains a minimal onedir PyInstaller spike derived from the production `EXE` + `COLLECT` shape:

- `spike_render.py` serves a loopback-only JavaScript fixture, launches the explicitly supplied bundled browser root, verifies rendered text, measures launch/page/RSS/cleanup, and reports tracked orphan processes.
- `playwright_bundle.spec` collects the official Playwright driver package plus only the atomically published `chromium_headless_shell-1228` registry directory supplied by `DESKPET_SPIKE_BROWSER_ROOT`; downloads, locks, and acquisition logs are excluded.
- `DESKPET_SPIKE_OFFLINE=1` starts a loopback deny proxy, sends Chromium traffic and Playwright download/CDN hosts to it, and bypasses only the fixed 127.0.0.1 fixture. This provides per-process offline evidence without changing the host firewall or network adapter.

The spec intentionally writes no product files and does not target the shared backend or Tauri output directories.

Status on 2026-07-16: **PASS** on Windows 11 x64 with Playwright 1.61.0 / Chromium Headless Shell revision 1228. Development, minimal PyInstaller onedir, and isolated offline renders all produced the expected dynamic text hash and zero tracked browser/driver orphans. See `evidence/win11/playwright-bundle-spike.md`.

The first frozen COLLECT attempt under the long `DeskPetBuildCache/.../frozen-spike-*` prefix hit Windows `MAX_PATH` inside Chromium resources. The successful disposable build used the short isolated `%LOCALAPPDATA%\DPW-T0\b2` work/dist root; the browser's real registry and bundle-relative layout were not shortened or altered.

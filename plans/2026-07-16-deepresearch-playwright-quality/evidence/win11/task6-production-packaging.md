# Task 6 production packaging evidence (Win11-only)

Status: **production packaging contract PASS; minimal frozen onedir render PASS**.
The full backend and NSIS installer were intentionally not built in this slice;
installer/update/uninstall E2E remains Task 15.

## Locked product artifact

- `playwright==1.61.0`, Chromium Headless Shell revision `1228`, browser
  `149.0.7827.55`.
- The acquisition command validates archive length, MD5, SHA256, ZIP CRC/member
  count/path safety, revision, executable length/SHA256, and Playwright
  `browsers.json` SHA256 before same-volume staging and atomic publication.
- The private owner is `playwright-browsers/chromium_headless_shell-1228`.
  Runtime context selects exactly one authoritative owner: test override,
  frozen `_MEIPASS`, Tauri resource, or dev cache. It never falls through from
  a broken frozen/Tauri package to an ambient developer cache.

## Packaging ownership

- The production PyInstaller spec collects official Playwright driver data and
  metadata plus only the verified revision root. Downloads, links, and other
  revisions are excluded.
- Tauri already maps the frozen backend onedir exactly once and has no separate
  Chromium/Playwright resource, so updater/uninstaller ownership remains the
  versioned backend resource tree.
- Playwright's Apache license, Chromium's shipped `ABOUT` and
  `LICENSE.headless_shell`, and DeskPet's third-party notice are present.

## Isolated verification

- Cache/work/dist: `C:\DPW6\pw`, `C:\DPW6\w`, `C:\DPW6\d`.
- Unit/fault-injection tests: `16 passed`, including corrupt length/digests/ZIP,
  executable tamper, foreign revision, authoritative-owner non-fallback, and
  atomic-replace failure cleanup.
- Minimal PyInstaller onedir: 512,849,293 bytes / 545 files before launch.
  The versioned browser root is 282,540,718 bytes after Playwright creates its
  zero-byte dependency-validation marker; this is the unpacked whole-root
  updater delta ceiling for a browser revision change. No NSIS compression
  estimate is claimed without building NSIS.
- Frozen smoke resolved source `frozen`, rendered `bundle-ok` under a
  process-local deny proxy, and exited with zero browser/driver processes from
  the isolated `C:\DPW6` artifact. Process ownership was checked by executable
  path so concurrent Gate A tests could not contaminate the result.
- DeskPet PID `90580` remained alive. Shared backend dist, Tauri target, global
  `%LOCALAPPDATA%/ms-playwright`, Hyper-V, and host network/firewall state were
  not touched.

Machine-readable measurements and hashes are in
`task6-production-packaging.json`.

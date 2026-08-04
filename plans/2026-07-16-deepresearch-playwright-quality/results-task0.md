# Task 0 result — Win11-only Playwright bundle Gate A

Status: **PASS**
Date: 2026-07-16
Scope: Windows 11 x64 only; no Hyper-V, VM, ISO, restart, system install, global browser cache, product-code change, lock-file change, or shared build overwrite.

## Outcome

The official Playwright 1.61.0 Chromium Headless Shell artifact is complete, checksum-verified, published with the real revision-1228 registry layout, and executable in development plus a minimal PyInstaller onedir. The frozen artifact rendered the deterministic JavaScript fixture from its own `_internal/playwright-browsers` directory with an isolated user profile and per-process network/CDN deny proxy. It did not create or consult a global `ms-playwright` cache.

All observed Playwright driver and headless-shell children exited. DeskPet PID 90580 remained alive and untouched. This passes the Task 0 feasibility gate; it does not claim that Task 6 production spec/Tauri packaging or Task 15 installer E2E is already complete.

## Locked decision

| Item | Accepted result |
|---|---|
| Host | Microsoft Windows 11 Pro x64, build 22621 |
| Python / PyInstaller | 3.11.9 / 6.21.0 |
| Playwright | exact `1.61.0` |
| Browser | Chromium Headless Shell revision `1228`, browser `149.0.7827.55` |
| Official ZIP | 119,099,822 bytes; MD5 `1BC51B5A9F308F4B7F47AC15A1BE049D`; SHA-256 `5CFDA0C763AA6A867CE2EFAD0C467E3220E9C5C01C4CBA02FD57AFE49EDE5457`; 290 entries |
| Registry root | `chromium_headless_shell-1228` with `INSTALLATION_COMPLETE` |
| Browser executable | 203,034,112 bytes; SHA-256 `28016DF6864D302434C9231E1F9F1A8A7ECC512CB2FE3FAABB2A36130B96BCF1` |
| Published registry payload | 282,539,741 bytes; 291 files including marker |
| Minimal frozen onedir | 513,058,675 bytes; 546 files; manifest SHA-256 `4E38521C9D3BCE6348F6E40A6DD287DA4541EE7A5AA73B3C44C04E34006C579D` |
| Frozen probe EXE | 3,434,924 bytes; SHA-256 `18A7B076A76D11B8A721B299DA5292A64E6A29ED2ABE3CA32AB2AC21DBB7ACA0` |

The development package `browsers.json` SHA-256 remains `EE39BC924BC3D1BD895626C2910F1292D109BBFEEB5ABD113ACB45E1951CC942`, and its declared revision/browser version agree with the accepted artifact.

## Runtime measurements

| Probe | Driver start | Browser launch | First dynamic page | Peak process-tree RSS | Cleanup | Result |
|---|---:|---:|---:|---:|---:|---|
| Development | 0.298 s | 1.487 s | 1.694 s | 361,529,344 bytes | 0.424 s | PASS |
| Final frozen onedir | 1.847 s | 1.347 s | 1.607 s | 367,017,984 bytes | 0.386 s | PASS |
| Frozen isolated offline | 0.289 s | 0.385 s | 1.623 s | 357,482,496 bytes | 0.427 s | PASS |

A separate fresh-process frozen measurement completed end to end in 3.362 seconds, with driver 0.290 s, browser 0.384 s, first page 1.612 s, peak RSS 358,408,192 bytes, and cleanup 0.392 s. These are feasibility-spike measurements, not final DeskPet backend or installer performance numbers.

Every probe produced rendered-text SHA-256 `9C755CDF608FF70ADF305C797C716B4C895A1CBA52CFABE163635B3EA86FD94F`.

## Offline and cleanup evidence

- Frozen `browser_root` resolved inside the onedir `_internal/playwright-browsers`; no development override was present.
- `USERPROFILE`, `LOCALAPPDATA`, `APPDATA`, and `HOME` were assigned to a new isolated profile.
- Offline mode routed Chromium and Playwright download/CDN endpoints to an in-process deny proxy and bypassed only `127.0.0.1,localhost` for the fixture. The render succeeded and no external request was attempted.
- The isolated profile and original user profile both had no `ms-playwright` directory after the probe.
- Each run reported `orphan_processes=[]`; independent post-run enumeration found zero headless-shell and bundled Playwright node processes.
- The disposable temp profile created by the probe was removed after each run.

Windows Firewall profiles were disabled on this host, so adding rules would not have provided real blocking. They were not enabled because that could disrupt the running DeskPet instance; the process-local deny proxy supplied the bounded offline gate instead.

## Build finding and Task 6 handoff

The first COLLECT attempt under the long `DeskPetBuildCache/.../frozen-spike-*` prefix failed with WinError 3 at Chromium's `PrivacySandboxAttestationsPreloaded` subtree because the destination exceeded legacy Windows path limits. The browser layout was not modified. Rebuilding from the short isolated `%LOCALAPPDATA%\DPW-T0\b2` work/dist root passed.

Task 6 should therefore:

1. pin `playwright==1.61.0` directly and lock revision 1228 plus the recorded archive/browser hashes;
2. acquire into same-volume staging and atomically publish only after length/hash/ZIP/executable validation;
3. collect the official driver and only `chromium_headless_shell-1228` into one onedir-owned browser root;
4. keep PyInstaller work/dist prefixes short enough for Chromium resource paths and add a build-time path-length assertion;
5. let Tauri carry the complete backend onedir once, without duplicating the browser resource;
6. retain runtime resolver/hash diagnostics and repeat offline installer E2E in Task 15.

No product file was changed by this spike.

Evidence:

- `evidence/win11/playwright-bundle-spike.md`
- `evidence/win11/gate-a-audit.json`
- `evidence/win11/download-blocker.md` (historical acquisition blocker and resolution)
- `spikes/playwright-bundle/spike_render.py`
- `spikes/playwright-bundle/playwright_bundle.spec`

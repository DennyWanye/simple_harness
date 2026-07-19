# Gate A Playwright bundle spike evidence

Captured: 2026-07-16T19:15:07+08:00
Decision: **PASS — Windows 11 x64 Task 0 feasibility gate**

## Artifact chain

1. Official object `chrome-headless-shell-win64-149.0.7827.55.zip` reached the declared 119,099,822 bytes.
2. MD5 `1BC51B5A9F308F4B7F47AC15A1BE049D` matched the official ETag; SHA-256 is `5CFDA0C763AA6A867CE2EFAD0C467E3220E9C5C01C4CBA02FD57AFE49EDE5457`.
3. ZIP validation opened all 290 entries. Its first/last entries were `chrome-headless-shell-win64/ABOUT` and `chrome-headless-shell-win64/vulkan-1.dll`.
4. Extraction occurred under same-volume staging. Publication to `chromium_headless_shell-1228` happened only after the expected executable was present; `INSTALLATION_COMPLETE` was written before the final rename.
5. The published executable is 203,034,112 bytes with SHA-256 `28016DF6864D302434C9231E1F9F1A8A7ECC512CB2FE3FAABB2A36130B96BCF1`.
6. The published registry contains 291 files and 282,539,741 bytes, including the marker. `%LOCALAPPDATA%\ms-playwright`, `backend/.local-browsers`, and `backend/ms-playwright` remained absent.

## Development render

The development probe used only the explicit isolated registry root and `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`.

- Playwright: 1.61.0
- driver start: 0.298 s
- browser launch: 1.487 s
- first page: 1.694 s
- rendered text: `DeskPet bundled Chromium dynamic fixture`
- rendered-text SHA-256: `9C755CDF608FF70ADF305C797C716B4C895A1CBA52CFABE163635B3EA86FD94F`
- peak process-tree RSS: 361,529,344 bytes
- browser close: 0.254 s
- full cleanup: 0.424 s
- tracked orphan list: empty

## Frozen build and render

The disposable spec collected official Playwright data plus only the atomically published revision directory. It did not copy downloads, cache links, locks, or logs.

The first COLLECT attempt used a long cache/build prefix and failed honestly with WinError 3 while creating Chromium's nested `PrivacySandboxAttestationsPreloaded` destination. A new isolated short work/dist root `%LOCALAPPDATA%\DPW-T0\b2` was used; no shared backend or Tauri artifact was touched.

Final artifact:

- onedir: 513,058,675 bytes, 546 files
- manifest SHA-256 over sorted `relative-path|length|sha256` records: `4E38521C9D3BCE6348F6E40A6DD287DA4541EE7A5AA73B3C44C04E34006C579D`
- probe EXE: 3,434,924 bytes; SHA-256 `18A7B076A76D11B8A721B299DA5292A64E6A29ED2ABE3CA32AB2AC21DBB7ACA0`
- bundled browser hash: exactly matches the isolated registry hash
- bundled `INSTALLATION_COMPLETE`: present

Final frozen render:

- driver start: 1.847 s
- browser launch: 1.347 s
- first page: 1.607 s
- peak process-tree RSS: 367,017,984 bytes
- full cleanup: 0.386 s
- rendered-text hash: matches development
- tracked and independently enumerated orphans: zero

The later fresh-process measurement completed in 3.362 s wall time; its internal driver/browser/page/cleanup measurements were 0.290/0.384/1.612/0.392 s, with peak RSS 358,408,192 bytes.

## Isolated offline render

The final frozen EXE ran with a new isolated `USERPROFILE`, `LOCALAPPDATA`, `APPDATA`, and `HOME`. No `DESKPET_SPIKE_BROWSER_ROOT` or `PLAYWRIGHT_BROWSERS_PATH` override was supplied, so the diagnostic path had to resolve from the frozen onedir.

Because all Windows Firewall profiles were disabled, enabling the host firewall merely for this probe would have risked the running DeskPet process. Instead, `DESKPET_SPIKE_OFFLINE=1` created a process-local loopback deny proxy, configured Chromium's launch proxy and all Playwright/Node proxy and download-host variables to that endpoint, and bypassed only `127.0.0.1,localhost` for the fixed fixture.

Observed result:

- browser root: frozen `_internal/playwright-browsers`
- network mode: `loopback_deny_proxy`
- external/CDN request attempts: 0
- isolated or original global `ms-playwright` created: no
- driver start: 0.289 s
- browser launch: 0.385 s
- first page: 1.623 s
- peak process-tree RSS: 357,482,496 bytes
- full cleanup: 0.427 s
- dynamic render/hash: pass and identical
- tracked and independently enumerated orphans: zero

## Non-interference

The pre-existing DeskPet development executable PID 90580 remained alive at every checkpoint. The spike never wrote product specs, dependency files, backend dist directories, Tauri target directories, or the global Playwright cache.

After final verification, the failed long-path output (132,026,943 bytes) and superseded b1 output (525,136,045 bytes) were deleted with absolute-path containment checks. The final b2 artifact/work logs, verified ZIP, and published registry were retained.

## Boundary of this evidence

This proves the exact Playwright/browser pairing and disposable PyInstaller onedir feasibility on the current Windows 11 x64 host. It does not replace Task 6 production packaging or Task 15 isolated Tauri/NSIS install, update, uninstall, and UI validation.

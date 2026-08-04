# Gate A browser download blocker

Date: 2026-07-16 (Asia/Shanghai)

## What was attempted

All browser writes were isolated under `C:\Users\Administrator\AppData\Local\DeskPetBuildCache`; `%LOCALAPPDATA%\ms-playwright` and the repository browser paths remained absent.

1. `python -m playwright install chromium` started from Playwright 1.61.0. The CDN redirected to the official Chrome-for-Testing object, but the visible temporary full-Chromium archive stopped at 39,286,471 of 192,511,857 bytes. The installer was stopped and its child processes were removed.
2. One bounded retry used `python -m playwright install --only-shell --no-progress chromium`. The headless-shell download again made intermittent progress and failed to reach the declared Content-Length.
3. The incomplete headless archive was resumed against the same official object, not a different browser or mirror. The transfer repeatedly ended early and entered retry. To honor the gate's no-more-retries instruction, the remaining transfer process was stopped.

Final headless partial:

- Expected: 119,099,822 bytes
- Received: 104,581,926 bytes (87.81%)
- SHA-256 of partial: `F727F2310EDA82448449CF91223AFB954067EAA172C0C1EB6900891487C54639`
- ZIP validation: `zipfile.BadZipFile: File is not a zip file`
- Official object metadata: ETag `1bc51b5a9f308f4b7f47ac15a1be049d`, Last-Modified `2026-05-29T03:12:27Z`

This is not a Playwright version/revision ambiguity. The local 1.61.0 metadata, the frozen Patchright metadata, and the official object all agree on Chromium revision 1228 / browser 149.0.7827.55. The blocker is acquisition of a complete immutable browser artifact in this network session.

## Cleanup evidence

After stopping the transfer, no `curl`, Playwright-driver `node.exe`, Chromium, or Chrome Headless Shell process remained. The pre-existing DeskPet development instance, PID 90580, remained running from its original start time and was not stopped or overwritten.

No render claim is made: an incomplete ZIP was not extracted, no system Edge fallback was substituted, and no frozen/offline smoke was run.

## Resolution in the same Task 0 run

The resumable official object later reached its declared 119,099,822-byte length. MD5 `1BC51B5A9F308F4B7F47AC15A1BE049D` matched the published ETag and SHA-256 was recorded as `5CFDA0C763AA6A867CE2EFAD0C467E3220E9C5C01C4CBA02FD57AFE49EDE5457`; all 290 ZIP entries were readable.

It was extracted to same-volume staging, validated for the expected executable, given Playwright's `INSTALLATION_COMPLETE` marker, and atomically renamed to `chromium_headless_shell-1228`. Development, frozen, and isolated offline probes subsequently passed. This file remains as the historical acquisition-failure record; the final evidence is `playwright-bundle-spike.md` and `gate-a-audit.json`.

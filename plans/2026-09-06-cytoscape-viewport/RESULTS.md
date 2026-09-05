# Cytoscape viewport repair — scoped handoff

2026-09-06. Base `65a604f8`; branch `feat/cytoscape-native-canvas`.
Worktree `/Users/denny/projects/simple_harness-cytoscape-canvas`.

## Cause and correction

Actual Cytoscape 3.34.2 already injects `position: relative` on its container.
The reproduced defect is clipping in the primary memory pane (`maxHeight:50%`):
headers/filters consume the upper area while the original 340px canvas centers
nodes below the visible strip. Once page scrolling settles, Cytoscape wheel zoom
prevents ordinary scrolling over that strip. The supplied native PNG, as inspected,
already contains a blue node; it does not independently prove renderer pixel loss.

Only three frontend product files change. Canvas height is `min(340px,40vh)`;
first graph open and explicit fit/zoom reveal the canvas. Ordinary wheel now scrolls
the pane; explicit +/- zoom, drag pan and pixel selection remain available.
A panel-owned boolean prevents producer refresh or owner-keyed graph remount from
repeating initial reveal. RAF cleanup cancels an obsolete mount; focused form inputs
are not displaced. Identity-keyed graph teardown and all request/authority behavior
remain intact. No backend, dependency lock, SDK/pin or native changes.

## Evidence

Actual headless WebKit, React StrictMode, production CSS/PrimaryMemoryPanel and
unmocked Cytoscape renderer; seven explicitly synthetic layout nodes, zero edges.
This is a layout fixture, not a backend/SDK/protocol acceptance or production data.
At both 1000x700 and supported minimum 800x560:

- Original source: 8 failed checks (initial reveal, wheel, fit, explicit tab reopen,
  each viewport), 14 controls passed. Original source was restored temporarily for
  the same final oracle and the candidate bytes then restored in `finally`.
- Candidate: **22/22 checks PASS**, including actual blue pixel visibility, real
  mouse wheel with unchanged graph pixel area, pixel click to selection, drag pan,
  both zoom buttons, filter focus, producer/rebind non-reveal, explicit reopen.
- Focused existing frontend: **9 passed / 1 skipped**. The skipped optional test
  needs a separate real installed SDK API fixture and is not claimed by this repair.
- Typecheck, production build and focused ESLint PASS. Build retains existing large
  chunk warning. No broad suite or Provider call.

Source fixture and runner: `tauri-app/layout-tests/`. From repo root start the owned
layout server with `./tauri-app/node_modules/.bin/vite --config tauri-app/layout-tests/vite.config.mjs --host 127.0.0.1 --port 18175 --strictPort`.
Run `PLAYWRIGHT_MODULE=<matching installed Playwright package> LAYOUT_EVIDENCE=$PWD/.local-test-evidence/<date>/canvas-layout node tauri-app/layout-tests/memory-graph.cjs`.
Set `LAYOUT_URL` if using a different free port. This run used the read-only matching
Playwright package in main backend venv (WebKit2311); no environment mutation.
This worktree has its own cloned npm directory, not a symlink to main.

Raw evidence is ignored under `.local-test-evidence/2026-09-05/canvas-native-layout/`
(the investigation's original date index). SHA-256:

| Artifact | SHA-256 |
|---|---|
| `red-final/results.json` | `b2605bf8d2fa90adcf4dc1f01a89030f6453526fbdf8bd529997e5bd1763efdb` |
| `green/results.json` | `43b8b17919f535b52ac8c9f9552804115e6306a547a3261b0ba0535dcefaf025` |
| `red-final/800-initial.png` | `6c5f57b5b0f17ada6569d6051e735bfc2aaa906c569f517e71ef1b0ef4b8fb83` |
| `green/800-initial.png` | `4a7b5745d33234f7b4ea37a53f630e8712c0ab1c648c0a8ad47db326c967b165` |
| `green/1000-initial.png` | `5302ad84f1fd010c7c9a974799dd912a27102162ec711a4b1059e6411aa5e043` |
| `green/800-selected.png` | `86980f4d49bd0a5bdb613b74cc3b0e3beb0c7160c5d1cdfcc960078b9f063ec6` |
| `green/1000-selected.png` | `d307f0f1c483c222260841c57f4a2504afccb244ab94e2a101c00d05d09e4e13` |
| `build.log` | `b841db097d157fd2024d343f393e8abe53352d4b896ce040ab9ca7be49a00d78` |

## Review and remaining boundary

Dirac independently ACCEPTED fixed source `c9907e14f365c3d0ebe001f0b179d6d32795a64c`
with no P0/P1. All three product files match that commit; all eight evidence hashes
above were independently checked, along with red/green counts, an actual renderer
screenshot and build log. The reviewer did not rerun the suites. This handoff does not claim native, HM-AC6, relation generation or full
program PASS. Main owns exact native rebuild and visible canvas/node selection,
filter, fit/zoom verification against the existing autumn memory, without repeating
CREATE. No native processes, port18120, user data or runtime environments touched.
HUMAN audit grant remains paused with contract-only WIP in its separate tree.

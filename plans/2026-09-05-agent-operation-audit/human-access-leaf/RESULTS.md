# HUMAN metadata audit access — source verification

Last updated: 2026-09-06. Isolated base54156f1e, branch
`feat/human-memory-audit-access`. Fixed source
`1097b2727af67a5e857115dea0e84ba596627b87` has Dirac's independent scoped ACCEPT:
no new P0/P1. Review verified the actual bound cache mechanism, final main sender
scope, source restoration hash and all five raw evidence hashes below without
rerunning tests. The limits below remain open; acceptance is not expanded by it.

## Result and limits

The explicit signed HUMAN entry issues a server-only SDK sealed receipt and reads
only public Memory operation metadata. Actual Host S1 admission and SDK authority,
snapshot pages, durable ACK replay, unknown recovery, expiry, close, rebind and
final sender checks pass the focused backend fixture. Production composition and
main WS call site are wired. React tests exercise the actual ControlChannel cache,
boundPrimaryPort, PrimaryChatView and panels with only WebSocket transport replaced.

This is not a browser/native run or a new native production acceptance. The full
main WS TestClient test is deliberately not included in this bounded environment:
the isolated venv contains Memory0.6.12/Harness0.7.2 and focused dependencies, not
the complete Host/Service runtime installation. The actual final sender function
is exercised under real signed request scope; static main call-site review is
separate from that evidence. No every-network-interleaving claim is made.

Memory0.6.12 has only SDK purpose `sealed_evidence_audit`; Host restricts this
server-only capability to operation metadata. SDK-enforced narrower purpose is
not claimed. No API key, authority nonce, receipt, raw SDK cursor, subject selector,
plaintext history or Agent-input route is exposed through this UI.

## Runs

All runs were serial, with a process-group watchdog stopping above 1 GiB RSS or
120 seconds. No limit was reached. Test slot released after PID11533 exited;
its watchdog PID11531 exited too. No models, embedding, browser, native or build.

| Check | Result | Time | Peak RSS |
| --- | --- | --- | --- |
| New signed Host/public SDK audit controls | 14 passed | 6.42s | 129744 KiB |
| Backend controls + signed binding + semantic runtime neighbors | 40 passed, 1 deselected | 28.62s | 145152 KiB |
| New frontend helper/parent/binding controls | 14 passed | 1.52s | 423488 KiB |
| Frontend plus existing MemoryPanel/PrimaryChatView/ControlChannel | 25 passed | 2.35s | 418000 KiB |
| TypeScript `--noEmit -p tsconfig.app.json --incremental false` | exit 0 | bounded run | 618320 KiB |
| Old unconditional bound revocation counterfactual | expected 1 failed, 4 unselected | 73ms test | 365664 KiB |

The counterfactual failed because the actual parent lost the explicit audit-open
button after cached bound replay. The source was restored in `finally`, byte-equal
to its prior SHA256 `020b10d0b5df4c7c0f36c7e0fb257bef26b847ff48839916943206e031e17ee0`.
The subsequent 25-test frontend run includes that same now-green case. It is not
a weakened oracle or a fake bare-port replacement.

The earlier 10-pass/1-fail backend run remains historical evidence: its rebind test
entered an already-stale request scope and could not reach the final sender. The
corrected test enters while current, revokes, then checks the sender; it passes in
the runs above. No product authority check was relaxed for that correction.

Dependencies: frontend package-lock matched the reviewed Cytoscape checkout;
node_modules was copied with APFS copy-on-write into this isolated tree. It is not
a symlink, and no shared/main dependency directory was modified.

## Commands

In repo root, with `PYTHONPATH=backend` and this tree's ignored venv Python:

```text
-m pytest backend/tests/operation_audit/test_human_access.py -q --tb=short
-m pytest backend/tests/operation_audit/test_human_access.py backend/tests/memory/test_primary_control_binding.py backend/tests/memory/test_semantic_correction.py -q --tb=short -k 'not default_control_socket'
```

In tauri-app, through the same bounded runner using local node_modules:

```text
node node_modules/vitest/vitest.mjs run src/primary/auditRequests.test.ts src/components/PrimaryAuditPanel.test.tsx src/components/PrimaryAuditBinding.test.tsx src/components/PrimaryMemoryPanel.test.tsx src/views/PrimaryChatView.test.tsx src/ws/ControlChannel.test.ts --maxWorkers=1 --minWorkers=1 --no-file-parallelism
node node_modules/typescript/bin/tsc --noEmit -p tsconfig.app.json --incremental false
```

## Local evidence

Ignored root: `.local-test-evidence/2026-09-05/human-audit-access/`.

| File | SHA-256 |
| --- | --- |
| backend-adjacent.log | efa0a3ea09ed303d570816ba91c2e8c698e80d7b2d84bd9aabd3be21e2a35058 |
| frontend-adjacent.log | bbb4858efabac759c3246a9e1d8493741731d0c67c88a8ae1971712d667b9880 |
| frontend-typecheck.log (empty success output) | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 |
| bound-counterfactual-red.log | 4b0f4de69e5cdedf022dc7e810e265ef5481dff1e050ee8a07acafab8ad9bbd4 |
| bound-counterfactual-restore.json | ba2d3fbb8cde62a08802f778cef905b3be2babf859e20212bc66e3777682e55c |

Original all-operation coverage remains unfinished. Page/output bounds do not
bound the initial SDK snapshot scan. Physical Provider calls, usage and cost are
not summed; absent values are not zero. This leaf does not add missing business
producers or claim complete operation recording/audit reasoning.

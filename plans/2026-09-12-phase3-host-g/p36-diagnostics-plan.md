# P36 Mission diagnostics slice

plan-status: finalized (functional refinement of approved wholePhase3, 2026-09-14)

Original P36-A02/A03/A08 and §8.1 require selecting a failed Mission in the App, reviewing its actual history/attribution and creating a local redacted support bundle. Reuse SDK replay/projection/attribution; no new replay engine or automatic reevaluation. Packaging/release and strategy promotion UI remain separate work.

Before implementation oracle:

| ID | Action | Required result |
|---|---|---|
| PD1 | Read selected completed/failed Mission diagnostics through authenticated control service | Existing SDK formal replay and failure timeline/attribution, explicit not_covered/breaks; zero Provider/tool/connector invocations and no state or usage mutation |
| PD2 | Select foreign/missing Mission, or request arbitrary destination/history selection | Same not_found/no identifiers leaked; destination/history overrides refused; never read/export another Mission before ownership proof |
| PD3 | Export selected Mission twice, with credential canary and unrelated Mission/workspace content present | Local JSON support file, stable content/hash/path if underlying state unchanged; safe report has actual versions, selected bounded input hashes, event/verification/attribution/usage evidence, artifacts as hashes only; no raw SDK journal, binary files, secrets or unrelated history |
| PD4 | Native App failed task -> diagnostics -> support export -> cold reopen | Visible honest failure/replay/coverage and local support file receipt; one selected Mission only; expired responses after switching Missions cannot overwrite the current panel; source/real and unpriced labels remain honest |

Support operation writes only a derived local report under the service's own support directory; it does not send/upload anything. Reports over2MiB reject clearly instead of silently truncating evidence. Credentials use existing redactor plus active provider key. Inputs are hash references, not a full history dump. App report buttons appear only if current Host declares capability. Production/SDK versions must be observed, no hardcoded current release claim.

Work: parent Host service/router/UI wiring and all execution/native testing. Scoped child may own new diagnostics.py plus its tests; no shared writers. During current realP34 paid test the SDK tree is frozen. Current status NOT_RUN.

## Review and execution checkpoint (2026-09-14 CST)

Independent Sol/high review found direct SDK reports retain event prose/source paths, source-only capability incorrectly excludes the pinned wheel, and display omits unknown/reserved/reconciled usage plus actual source identity. Explicit safe projections and version identity are being repaired in the helper; parent changed capability to running API detection at startup/rebuild and shows ledger limitations. Parent also found and covered an in-flight Mission-change freshness race. No policy, verifier or Provider code changed. PD4 native remains NOT_RUN.

Initial UI6+78PASS/0.880s (runner1.38s); unknown-usage addition7+78PASS/0.839s (runner1.30s). Current routing13PASS/16.05s (runner16.57s), actual SDK source attestation. Final helper/privacy, current committed tests and native gate still pending. Earlier root `tsc --noEmit` was ineffective; actual `npm run typecheck` now checks the referenced app and passes. First actual check found a missing component closing brace; corrected before testing. Raw logs remain ignored.

## Software checkpoint before committed native acceptance

PD1–PD3 source helper/actual facade6PASS10.26s/runner10.80s, no skip; existing installedSDK0.11.1 wheel16PASS24.37s/24.93s (version-only runtime identity honestly retained, not a new packaged build). SDK currentdc2f156 step06/08/09/p36 mechanisms164PASS3opt-in-realSKIP214.27s/214.58s; paid opt-ins are not counted as passed. Parent front-end snapshot replacement avoids effect-reset flash; request-inflight Mission changes retain stale warning. Latest8diagnostics+78Mission tests86PASS0.941s/runner1.40s; formal typecheck and new-component lint PASS. Original lint caught effect-driven state reset and test import ordering; corrected. SDK raw report copying was rejected by independent review before native or commit. PD4 remains NOT_RUN; no fullPhase3 completion.

Evidence roots: Host `.local-test-evidence/2026-09-13/p33-g/` for p36-ui-v4.log, p36-typecheck-v6.log, p36-eslint-v2.log; SDK `.local-test-evidence/2026-09-12/p33-g/` for g-p36-host-diagnostics-v1, g-p36-host-wheel-v1, g-p36-host-routing-v1, g-p36-sdk-mechanisms-v1 receipts/logs. Original failures retained.

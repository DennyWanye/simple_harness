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

Native v31 retains FAIL_SETUP: fixture only accepted doc6/doc7 while actual source offered doc9, leaving Planner unresolved; UI read showed PLANNING, then UI cancel and normal quit. Lifecycle163.682s, no paid provider. Material smart-dash input was corrected by paste before submit (paste timeout was only tool acknowledgement; AX confirmed exact text). Native also revealed that nonzero reserved funds require incomplete-accounting wording even when imported unknown-row count is zero. Parent corrected both; doc8/doc9 only replace role templates and use same frozen verification semantics. Current-document actual Host case suite plus diagnostics11PASS20.69s/runner21.25s; UI11+78=89PASS0.921s/1.50s, typecheck/lint PASS. Original case/raw screenshot retained; fresh committed native PD4 still pending.

Current committed Host5655793a source tests11PASS20.05s/runner20.59s. Nativev32 correctly failed bad quote, displayed FAILED/33events/600tokens/0reserve and all verification layers; two exports same8205byte SHA25669968c7329d7c02d5fb1149d0aea23da71b3731fc1f79a4e4023cf97410290b1, all selected durable table hashes unchanged (4controlled calls/0rehandoff). Actual runtime/context/verifier identities verified; no rawsource/text/path in support report. Lifecycle249.911s, no paidprovider. Screenshot caught long receipt overflow; final one-line wrapping fix and current UI/cold acceptance follow. This v32 record is PARTIAL_NATIVE, not PD4complete.

## PD4 current source-native result (2026-09-14 01:33 CST)

PASS within PD1–PD4 diagnostics slice. Tested clean Host `d093f55cb325e525a4fec042ef47d9d5e4a66da5`, SDK snapshot `dc2f1564b18eab70e55ad04e7a9c99175eed1b7b`, source-snapshot-v33. Actual native creation used the unchanged n4-bad-quote source file (hash8badc63e91c3ccdad8c595cf0d9c1326b66962b40bbc88e60e56c79c3d4e5348). Mission mission-cec4506147d6dd75 FAILED/max_attempts_reached, formatPASS/ruleFAIL/criticSKIPPED,4 controlled calls/600tokens/0reserve/0rehandoff. This is an expected negative fixture, not real-provider document-quality success.

Parent clicked diagnostics, repeat export, quit, cold launched the identical snapshot and userdata, selected the original Mission, read diagnostics and exported again. Native UI showed FAILED,34events,0uncovered/0differences/0gaps; screenshot proves long path/hash wraps within the pane. Support JSON is8205bytes and SHA256 `5baf9a5e9e488b2bd3abb5d929e9c54113332d1e78d3c36cff81362aedb2d245` in all exports. Actual Context/SDK/source identities and verification layers retained; raw input/workspace/path/artifact bytes omitted. Before repeat operations, after export and after cold-read selected durable table hashes are identical; four calls remain four, no rehandoff. Catalog startup GET is not a model invocation.

Raw ignored root `.local-test-evidence/2026-09-13/p33-g/source-ui-p36-diagnostics-v33/`; case-summary.json SHA256 `223fc5e785295d5d226f66ba8ffcef1a11ce0b428a6c94ab7524ed7086210de7`. Screenshot02-support-receipt-wrap.png and03-cold-diagnostics.png; capturebefore-diagnostics/after-export/after-cold JSON contain full selected-table hashes. Native lifecycle1016.772s includes report/user/analysis waiting, not active test time; cold lifecycle62.016s. Owned groups13453/18796 exited0 without residual; caffeinate remains. v31FAIL_SETUP andv32PARTIAL_NATIVE retained.

Software evidence remains committed Host5655793a11PASS20.05s, UI89PASS0.921s, latest CSS-specific11PASS0.075s and actual `npm run typecheck` PASS; d093f55c changes only receipt wrapping. Independent Sol privacy/runtime review findings corrected before native acceptance. PD1–PD3 actual authorization/canary/unchanged-state/race tests and PD4 UI form this slice evidence. This does not close all P36 original conditions or wholePhase3. No installer/package/release/push.

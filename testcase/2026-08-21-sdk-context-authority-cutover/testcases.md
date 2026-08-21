# SDK Context authority cutover — black-box testcases

Status: CTX-1～CTX-5 and SMOKE-CRITICAL executed with macOS Computer Use + real DeepSeek on
2026-08-21 and PASS. Deterministic/automated and wider affected/full surface gates retain their own
independent receipts; this file does not promote them from this manual run. Reviewable conclusions are in
`plans/2026-08-21-sdk-context-authority-cutover/verification/run-1/manual-results.md`. Raw evidence belongs
under `.local-test-evidence/2026-08-21/`; credentials, cookies, Authorization, private reasoning and
attachment bodies must never be captured.

For every case record scenario, Session/root/request/snapshot/invocation ids, binding epoch and
catalog generation/fingerprint where applicable. UI cases use Computer Use only: before each action
declare `坐标=(x,y)|动作=...|期望=...`, then capture before/after screenshots and correlated redacted
logs/receipts. Direct WS injection is not UI evidence. Missing evidence remains NOT_RUN.

## Automated required cases

### AUT-01 — single prepared snapshot and complete first request

Bindings: AC-10, AC-11; TO-A10, TO-A11, TO-R7. Lane: automated-contract.

1. Create an isolated Session fixture with CJK persona, scoped memory, selected Skill, 12 complete
   conversation turns, current input, structured text attachment, project/task and deliberate
   optional-source misses. Start one fresh SDK Run through the public product ingress.
   - Oracle: Context is prepared exactly once before physical dispatch; a stable snapshot id/version/
     fingerprint is created.
2. Compare public RunStart, redacted first physical request and Inspector lineage.
   - Oracle: all use the same snapshot/root/request identity and section ordering. Persona/system,
     Memory, Skill, allowlisted history, current input, structured attachment and project/task are
     present according to policy; optional misses are explicitly empty/unavailable, never invented.
3. Replay canonical snapshot serialization without starting another Run.
   - Oracle: fingerprint is unchanged and preparation count stays one.

Evidence: preparation receipt, three lineage headers, redacted request digest, section matrix.

### AUT-02 — fresh-Run isolation and same-Run tool protocol

Bindings: AC-12, AC-17; TO-A12, TO-A17, TO-R8. Lane: automated-adversarial.

1. Seed Session A conversation plus distinct activity/reasoning-summary/artifact/technical/tool/private-
   reasoning canaries, and a different Session-B canary. Start fresh Run A.
   - Oracle: request contains only A's conversation allowlist; every projection and Session-B canary is absent.
2. In that Run return assistant tool call + private Provider reasoning, matching tool result and next turn.
   - Oracle: only protocol-required call/private metadata/result continue inside the Run. Private reasoning
     is absent from UI, Session conversation, Inspector and logs.
3. Start the next fresh A Run.
   - Oracle: prior tool lifecycle, activity and private reasoning are absent.

Evidence: first/tool/fresh request hashes and negative-canary scans.

### AUT-03 — physical usage, replay, ordering and cross-store recovery

Bindings: AC-13, AC-15, AC-17; TO-A13, TO-A15, TO-A17, TO-R7, TO-R9, TO-R11.
Lane: automated-integration.

1. Settle physical attempts as succeeded-with input/output/cache usage, succeeded-without-usage,
   definite-failed, cancelled-after-handoff and unknown.
   - Oracle: each has unique durable invocation/provider/model/root/request/attempt lineage; only trusted
     succeeded-with-usage creates a measurement. Missing values remain unavailable, not zero.
2. Replay each settlement and deliver an older attempt after a newer successful one.
   - Oracle: no duplicate attempts/samples; late attempt stays auditable but current authority does not regress.
3. Repeat at fault points before settle, after settle/before projection, after projection/before Session
   commit and after Session commit/before cursor. Restart reconciliation after each.
   - Oracle: no loss/duplication; same identity with different payload fails closed.
4. Fully restart and read Context.
   - Oracle: actual model/trusted usage recover with the same lineage; other attempts remain unavailable.

Evidence: durable counts, attempt/sample/cursor receipts, restart authority.

### AUT-04 — immutable catalog, leases and tolerant public progress

Bindings: AC-14, AC-17; TO-A14, TO-A17, TO-R10. Lane: automated-contract.

1. Start with at least two complete tool schemas.
   - Oracle: Provider, executor and Inspector have exactly equal ordered names, generation, catalog and
     per-schema fingerprints; tokens derive from frozen schemas.
2. Verify `deskpet_public_progress` is optional string, then exercise missing/blank/wrong-type/non-empty.
   - Oracle: first three do not block the business tool and create no narration; non-empty creates one.
3. Publish a new catalog while old Run is running then WAITING; restart and resume.
   - Oracle: old Run retains old generation through WAITING/restart until terminal; new Run gets new generation.

Evidence: three-way schema digest, tool outcomes, narration count, lease receipt.

### AUT-05 — Context UI authority and hydration fences

Bindings: AC-10, AC-15, AC-17; TO-A10, TO-A15, TO-A17, TO-R7, TO-R11. Lane: automated-ui.

1. Open Context for binding-only Session.
   - Oracle: binding is separate; no real request/measurement is explicitly unavailable, not `(no model yet)`
     or numeric 0 presented as measurement.
2. Complete measured Run and open with fresh correlation id.
   - Oracle: ring/modal show actual response model, snapshot identity, real usage and estimated/measured labels.
3. Deliver wrong/missing Session, older version, exact duplicate and equal-version/different-payload frames.
   - Oracle: only exact idempotent duplicate is accepted; current authority never regresses.
4. Switch Session, reconnect and restart.
   - Oracle: each Session hydrates only its own durable snapshot/usage.

Evidence: UI snapshots, correlation/version receipts, restart hydration.

### AUT-06 — concurrent per-Run bindings and future-only settings

Bindings: AC-13, AC-17; TO-A13, TO-A17, TO-R7, TO-R9. Lane: automated-concurrency.

1. Configure A as declared DeepSeek Thinking and B as different provider/model/Fast; start behind barrier.
   - Oracle: physical intervals overlap; request, usage, estimator/charge availability and catalog match each binding.
2. While active, change A model params and Provider default.
   - Oracle: active targets/wire params stay frozen; next A Run changes; B is unaffected.
3. Exercise declared DeepSeek, always-thinking, toggleable and unknown OpenAI-compatible profiles.
   - Oracle: only declared capabilities generate wire fields; URL/model text is not guessed; UI semantics match wire.
4. Put one Run in WAITING, restart and resume.
   - Oracle: bindings remain distinct; no runtime swap closes the other Run.

Evidence: overlap receipt, per-Run request digests, binding/restart records.

### AUT-07 — attachment bounds and public-data boundary

Bindings: AC-11, AC-12, AC-15, AC-17; TO-A11, TO-A12, TO-A15, TO-A17, TO-R8, TO-R11.
Lane: automated-security.

1. Send structured text attachment with known first line and body canary.
   - Oracle: exact content reaches Provider; public snapshot/UI/logs contain only bounded manifest, not body.
2. Send one block over 8 MiB and one Run over 16 MiB.
   - Oracle: rejected before physical dispatch with clear bounded error; no partial snapshot/fake usage.
3. Delete isolated Session data and inspect diagnostics/history.
   - Oracle: private body follows lifecycle and never appears in diagnostic/public projections.

Evidence: physical-call count, redacted manifest, error receipt, canary scan.

### AUT-08 — production legacy reachability closure

Bindings: AC-16; TO-A16. Lane: automated-wiring.

1. Traverse public chat, Context, usage hydration, catalog and attachment entries.
   - Oracle: each returns new snapshot/invocation/catalog lineage or explicit unavailable; none reaches a
     second assembler, dynamic Inspector probe, legacy usage/registry, summary Provider call or global refresh.
2. Read supported legacy message/usage records.
   - Oracle: readable as legacy incomplete only; never authority for a new SDK request.

Evidence: production reference graph/reachability report and public lineage.

## Computer Use + real-LLM required cases

### CTX-1 — persona/history answer (minimum 2 independent roots)

Bindings: AC-10,11,13,15,17; TO-A10,A11,A13,A15,A17,R7,R11.

1. Establish one benign preference in isolated Session A, then send exactly: `根据我们刚才聊过的内容，
   用两句话告诉我你记得我的哪项偏好；不需要调用工具。`
   - Oracle: non-empty two-sentence answer uses only A facts; no fake tool card.
2. Open Context during each Run and after terminal.
   - Oracle: same snapshot; usage is real or explicitly unavailable if Provider omitted it.
3. Repeat as a second root.
   - Oracle: distinct root/request/attempt ids and no duplicate/cross-Run data.

### CTX-2 — ≥10-turn scoped recall (minimum 2 independent roots)

Bindings: AC-11,12,13,15,17; TO-A11,A12,A13,A15,A17,R8,R9,R11.

1. Build ≥10 complete turns in A with benign location/drink facts; put canaries in activity/tool UI and B.
2. Send exactly: `我之前说过住在哪里、喜欢喝什么？不确定就直说。`
   - Oracle: grounded only in A conversation/memory; activity/B canaries absent; uncertainty is not guessed.
3. Repeat as another root and inspect after terminal.
   - Oracle: distinct attempts and Session-scoped snapshot/usage across switch/reopen.

### CTX-3 — attachment/project/tool (minimum 2 independent roots)

Bindings: AC-10,11,13,14,15,17; TO-A10,A11,A13,A14,A15,A17,R7,R10,R11.

1. Through UI attach generated public text and bind isolated project. Send exactly: `读取我刚附上的这个
   公开文本文件，并告诉我第一行；需要的话使用文件工具。`
   - Oracle: answer equals known first line; any tool permission/call/result settles normally.
2. Open Context during/after.
   - Oracle: attachment type/count and catalog lineage without body; three catalog fingerprints equal.
3. Repeat as second root.
   - Oracle: independent durable roots; no duplicate tool/usage after reopen.

### CTX-4 — missing-file negative safety (minimum 1 root)

Bindings: AC-12,13,14,15,17; TO-A12,A13,A14,A15,A17,R8,R9,R11.

1. Send exactly: `读取 /definitely-not-present/context-canary.txt；找不到就明确说明，不要猜。`
   - Oracle: accurate bounded failure; no guessed content or fabricated success/measurement.
2. Start fresh ordinary text Run.
   - Oracle: prior failure card/public summary is absent from request.

### CTX-5 — true cold start and full restart (minimum 1 root)

Bindings: AC-10,13,15,17; TO-A10,A13,A15,A17,R7,R9,R11.

1. Start with never-used isolated userdata and configure test Provider through visible UI; operator enters
   credentials without capture.
   - Oracle: true clean-data configuration; existing userdata untouched.
2. Execute CTX-1 and open Context; fully exit all App-owned processes, restart same isolated userdata,
   reopen same Session/Context without Provider reconfiguration.
   - Oracle: binding/snapshot/model/usage recover with identical lineage; another Session remains empty.

## Surface smoke

- SMOKE-CRITICAL (AC-15,17; TO-A15,A17,R11): launch, Provider settings, Session list, chat send/stop,
  history reopen and Context modal each get one real UI shot.
- SMOKE-AFFECTED (AC-10..17; all obligations): Provider CRUD/reorder, Session model params, ordinary/tool/
  attachment chat, permission/wait/cancel/recovery, thinking group, durable outcomes, Context empty/
  measured/missing-usage/failure/restart/multi-Session.
- SMOKE-FULL (AC-16,17; TO-A16,A17,R10,R11): all above plus memory, file, shell and web success/failure.
  Shared startup/provider/catalog changes make full surface required.

After hashes are recorded, stop App-owned processes and run cleanup only against the exact marked fixture
root. Retain raw evidence locally until a future NAS copy has been verified.

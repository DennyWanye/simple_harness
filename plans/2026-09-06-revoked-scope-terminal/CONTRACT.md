# Failed Run with revoked sources: preserve debt, settle the real terminal

2026-09-06. Base75cd51a5; branch feat/primary-revoked-scope-terminal. No SDK/version/schema/main/userdata change. No non-SELF work.

## Verified trigger and production distinction

Current-r3 made real project writes, paged their public result, then suppressed the original USER source. The real physical guard refused the next request and SDK committed FAILED. Host record_sdk_terminal refused because the write scope lacked either a semantic closing receipt or its own pending receipt. Original evidence remains immutable.

The reduced runtime fixture did not compose ClosureFallback. Main does compose _BoundClosureFallback -> ClosureFallback.settle. Its existing non-COMPLETED branch writes outcome=pending/reason=closure_run_not_completed with zero Provider calls. Therefore current-r3 is not evidence that the default main composition must fail identically. The missing evidence is this actual production fallback under revocation/cold recovery, without pre-closing the task.

## Minimal change and invariant

For an SDK non-success terminal, preserve the existing pending receipt/idempotency, material-event watermark and terminal gate. Move the non-COMPLETED branch before building _scope_observation_tx (task state, event summaries, source reference lists) or reading a final assistant answer. No source-bearing request, Provider attempt or mutation plan may be built for this path. A replay of an existing pending receipt must report pending rather than already_closed.

The actual terminal observer/public SDK proof, Host effective scope, durable ingestion gate, current owner/generation and immutable event hash checks are unchanged. Pending records explain uncompleted semantic work; they never advance the closing watermark, mutate canonical scope state or fabricate no_mutation. Host terminal remains FAILED, its turn settles, new independent input can run. The same old scope retains pending debt and its existing current-visibility gates; no forced closure, source restoration or old-data restamping.

No new ledger/DDL or caller-supplied visibility bypass. Main already owns default composition. Tests must inject the real ClosureFallback into the actual runtime constructor, matching main's path; absence of a fallback is not silently redefined as successful closure.

## Decisive validation

1. Original unclosed-write/page/revoke chain with the real fallback: public SDK FAILED + same terminal identity, zero post-revocation send, existing pending receipt, dirty scope/canonical revision unchanged, Host FAILED/turn SETTLED. Trap source-observation rendering, Provider invoker, run-answer reader and mutation service: none may be called for non-success settlement.
2. One failure at Host terminal.before_commit after pending persisted; rebuild runtime/SDK stack, resume same bound failed Run, reuse exact pending identity without a new send/receipt/closing decision. New independent foreground input must finish; original source remains unavailable.
3. Necessary negative: existing terminal identity/generation and completed-without-closure protections remain; no artificial COMPLETED or source permission is accepted. Use retained actual fixture/public facts rather than faking SDK terminal rows. Limit tests to this leaf.

Receipt write and Host terminal are existing separate transactions; a crash between them is recovered by idempotent reuse, not claimed cross-database atomicity. Raw evidence stays ignored. Original current-r3 remains failed evidence until this exact unclosed path is run through production composition; no pre-close fixture substitution.

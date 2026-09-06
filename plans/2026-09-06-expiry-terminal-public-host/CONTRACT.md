# Host H077 terminal recovery connection — 2026-09-06

Base762af1ab. Only composition public metadata/explicit recovery and the foreground
terminal observer invocation are changed. No main/provider/analysis/closure wiring.

Composition reads exact `uow.read_run_terminal_record(RunId)` and cross-checks the
returned actual event identity against its unchanged public audit proof. SDK SQL
has been removed from this reader. Missing public capability refuses; no old fallback.

The terminal observer only requests explicit SDK recovery for a bound failed Run
whose public read raises exactly `terminal_event_unavailable`. Before invoking, it
revalidates Host subject/owner/generation/HostRun/SDKRun binding. Other SDK audit
errors (including multiple/conflicting terminal) propagate. The SDK's eligible
witness is read through its public port and fully revalidated by its same-TX recovery.
The original Host terminal authority, lease/generation fence and original terminal
identity checks still decide Host settlement. This is not database-open repair.

Test scope: real old installed H075 Host authorization -> deadline expiry -> failed
with no public terminal -> actual Stop intent, full close, genuinely new SDK/Host
Runtime stack over the same test stores using installed077 -> official lease
reclaim -> exact SDK recovery and real Host FAILED receipt, no new Provider calls
or new Run. Unknown/foreign/multiple-event shapes must fail closed. Explicitly no
native or original r6 userdata mutation. Original lease10/analysis14 are not rerun.


## Cold cleanup and two-store fence boundary

Actual first cold test reached SDK proof and Host durable terminal then failed
clearing an absent old Run from the new process's tool registry. Only after real
terminal verification and durable Host commit, `mark_terminal_if_registered` checks
the exact registry lookup: absent is no-op; existing registration uses unchanged
cleanup outside the KeyError catch. No old authority is recreated.

The Host precheck and SDK write are not a cross-database atomic lease fence. SDK
may append its independently verified historical proof if Host ownership changes
between them; the existing Host final write still rejects stale owner/generation.
The two negative tests are error-dispatch controls, not full production foreign or
stale binding tests. Original userdata/native remain separate acceptance work.

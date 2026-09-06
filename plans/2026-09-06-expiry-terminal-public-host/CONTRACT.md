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

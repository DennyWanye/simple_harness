# r16: process mandatory context after a zero-tool direct answer

2026-09-06. Host base eaa210b3, separate branch from the accepted timer runtime;
nonSELF branch and frozen H078/M618 artifacts remain separate. Main allocates H079
after source review. Native r16 remains a real failure, never relabelled by tests.

The timer applied one genuine overdue occurrence. Two occurrence rows are claimed
and presented phases of the same key; four timer rows are prepared/claimed/
handed_off/applied phases of the same signal. No legacy mandatory-exit row is
correct before real ACK. The first actual Provider answer had no tools; no-recall
correctly refused its pending occurrence, but Host threw before SDK checkpointing
the successful response and the Run failed.

Owned changes: `provider.py` moves the typed terminal write to the SDK's public
post-response-checkpoint `prepare_context_use_terminal` hook; original terminal
verification still follows. `context_authority.py` supplies the exact, bounded
SDK feedback SYSTEM message before snapshot hashing and exposes the existing
pending check independently of writing a no-recall route. `main.py` adds only the
same check to its existing sink proxy. `typed_context_use.py` documents ordering.

The successor SDK stores each repair identity in schema8 checkpoint and records
known `context.no_recall` and `context.apply` audit boundaries. Max two repairs
inherit all original budgets. Every repair-bearing proposed terminal, even after
context_route, rechecks real ACK/current pending. A route or physical read does
not acknowledge. Unrelated/foreign/permission errors do not become repair loops.
The next request receives a fresh snapshot, actual Memory use grant and original
physical dependency/pending-source guard. No request is mutated after hashing.

New controls must start with an ordinary arithmetic USER and a successful
zero-tool Provider response. Actual main sink proxy/Host authority/Harness SQLite/
public Memory/timer/ACK are used with deterministic HTTP. One route-without-ACK
case must fail boundedly; a second-request public forget must prevent physical
send. The successful control also reads public audit pages and matches repair
identity. SDK source controls separately cover four crash boundaries and exact
response replay. These are not model or native evidence.

Original r16 failed Run/events/userdata are not changed. Native r17 should use
ordinary input without prompting the model to remember a reminder, process the
still-pending item through real tools, then verify no duplicate on another turn
and cold reopen. Main owns that installed/native gate.

# Primary short source registration — bounded Host leaf

plan-status: finalized (original program authorization; 2026-09-05 user request)
Base: Host 7dcfce8bba0f41b8f673c8009fb4179c5bb03f6b. SDK wheels remain frozen Harness 0.7.2 / Memory 0.6.7.
Owner: new memory/conversation registration + indexing modules and dedicated tests only.
Runtime/main.py/human_memory_v7 wiring belongs to Carver/main.

## Original obligations and source
S1 Task7: authenticated conversation metadata/classification. S3 Task4: complete groups beyond latest10, age<=5 days;
S3 Task5 and S5 Task1–3: actual route authorization, source/current visibility and continuation remain integration obligations.
Acceptance numbers/thresholds unchanged. This leaf does not complete S3 or the program.

Read Host foreground turns/runs/SDK bindings/terminal receipts and human_memory_evidence/sanitization receipts in a read transaction.
Reuse read_primary_terminal_identity_tx + terminal_observation_tx + read_evidence_pair; verify canonical commitments, subject,
primary, turn, completed terminal, original USER evidence hash, actual transcript USER text and ordered roles.
The terminal observer already persisted real public messages, raw SDK terminal hash and visibility_dependencies.
No model metadata, text similarity, generated Run, new S1 stamp or new evidence identity is authority.

## Registration contract, before implementation
`PrimaryConversationAuthority(db_path, *, subject, primary_ref)` derives immutable registrations from those Host facts.
`await registrations_for_run(host_run_id)` returns a complete ordered group, or an explicit unavailable reason.
`await resolve_conversation_registration(reference)` re-derives and compares all four public ref bindings.
IDs are deterministic domain/canonical-hash derivations of the existing immutable evidence and group commitments.
Group ID=real Host run; sequence=real enqueue_sequence; ordinal=actual public transcript position (1-based);
count=full transcript count; manifest commits ordered evidence/hash/pointer/role bindings plus terminal identity.
USER uses its original S1 /text, authenticated-user actor/provenance, original receipt admitted_at.
ASSISTANT uses exact terminal S1 /messages/1/content, assistant/model-output provenance, actual terminal occurred_at.
Host personal classification floor uses existing host:classification/v1 policy; no external entity inference.
TaskScope is the actual frozen foreground run scope (None is legitimate); entities stay empty absent authoritative entity facts.
`authorize_conversation_public_text` derives pointer/text hash/classification binding from exact item authority.
Metadata receipt deterministically binds every metadata + S1 field; no registration ledger is necessary.

Frozen SDK limitation: registration evidence_id is UNIQUE and each registration binds ONE string pointer;
_complete_group also rejects repeated evidence IDs. Multi-assistant/tool transcripts share one terminal S1.
Therefore this leaf must refuse the WHOLE such group with `terminal_multiple_items_not_representable`, not truncate it or
fake new evidence IDs. Artifact/non-string content likewise remains unindexed. The original tool-group obligation stays open.
This is a representational gap for independent review, not evidence that terminal source is missing.

## Producer / retry API
`PrimaryShortIndexingService(authority, *, manager, principal, fault_hook=None)`;
`await reconcile()` scans actual completed terminals, derives full groups, ingests the exact original S1 pairs via public
manager.ingest_committed_evidence, registers all via public manager.register_conversation_evidence, then calls public
manager.rebuild_short_horizon_projection(principal=principal). It never reads SDK SQL.
Existing immutable terminal facts are the replayable outbox: no second terminal state machine, cursor or producer ledger.
Crash after ingest/one registration/before rebuild: restart replays identical IDs and bytes; rebuild occurs only after the whole
scan succeeds. Existing analysis outbox delivered semantics are unchanged; no fabricated analysis lineage/job.
Incomplete/unsupported groups are reported separately, never individual USER-only groups.
Runtime composition injects authority through builder conversation_evidence_authority and invokes reconcile on terminal commit
and startup catch-up. This leaf exposes an explicit callable; it does not change Carver's files.

## Outbound dependency boundary
Generated terminal text can depend on prior evidence/typed/short recall. SDK .067 checks registered short source suppression,
not arbitrary Host visibility_dependencies. Registration does NOT grant future disclosure. Host must retain/recheck terminal
and USER dependencies using PrimaryHistoryPolicy at every output, as with history. Existing public short hit lacks source IDs.
Before consuming this index, conservatively include the terminal evidence roots of all groups considered in its scan, with exact
hashes, in a returned visibility dependency proof; PrimaryHistoryPolicy expands their actual persisted proofs. This may invalidate
unrelated short hits when another indexed source is denied; it must not falsely allow. Missing/over-limit proof fails closed.
The service returns these roots, not a permission boolean or replacement SDK grant; caller still checks exact067 triple.
No SDK suppression mutations are issued to simulate Host dependency filtering.

## Independent oracle / tests
- Real foreground service + real SDK ReAct/SQLite with deterministic provider completes 11+ ordinary USER/assistant groups.
  Reconcile's ingest/register/rebuild -> early group's exact text hits, newest10 do not. No hand-built group metadata.
- Before terminal completion there are no registrations for that run; forged/corrupt/incomplete source is rejected.
- Crash after one registration, reconstruct service and reopen Memory, replay -> same refs and same recall content, no duplicate items.
- EVIDENCE suppression of original USER and terminal source denies existing early hit including reopen; current public .067 binding denies.
- Conservatively returned roots include generated terminal dependencies; Host policy blocks output after a prior dependency is suppressed.
- Multiple terminal items/tool transcript and artifact content are explicit blocked whole groups, not PASS by omission.
- Reconcile uses immutable fact replay; classification/public pointer comes from actual bytes; no SDK SQL or production fixtures changed.

Evidence is ignored under .local-test-evidence/2026-09-05/primary-short-ingestion. Deterministic integration is not native/provider quality proof.

## A2-1: actual terminal role/source contract conflict (2026-09-05)
The first decisive test produced 11 real completed foreground groups and delivered all 11 real USER outbox entries.
Frozen Harness .072 then rejected the FIRST complete registration group:
`ValueError: conversation role differs from admitted evidence source_kind`.
Harness `runtime/evidence_protocol.py` requires role ASSISTANT -> source_kind ASSISTANT_MESSAGE;
the actual immutable terminal S1 is RUNTIME_EVENT. Therefore even the simple two-message group is not representable as
proposed above. Its public transcript is present; the issue is the carrier contract, not missing production evidence.
The proposed direct assistant registration is RED; do not ship/wire this candidate. No registration/projection PASS claimed.
Do not relabel assistant as runtime, mutate the envelope, or mint/restamp derived evidence to avoid this check.
A future bounded protocol change must explicitly admit authenticated item roles inside a runtime container (plus multiple
items per evidence), or a new per-message producer needs a separately correct source-lineage contract. Frozen .067 stays intact.

Implementation first checks the existing USER ingestion outbox is delivered, avoiding first-ingest NULL analysis lineage.
This retains the existing analysis job's source identity. Terminal ingestion without lineage must be considered separately
before integration: the SDK ingest API also creates mutation work, and that is not a demonstrated terminal analysis chain.

Selected-only source disclosure is a successor public SDK seam, per main's explicit direction. This leaf keeps the conservative
whole indexed roots concept; it must remain BLOCKED for unknown ownership, uncovered existing chunks, over-limit roots or any
root denial. Do not implement a Host copy of chunk identity algorithms. No claim of complete short-lane availability.

Current independent checks: positive value test FAIL (above); pending USER/no terminal control PASS and real role-conflict
reproduction/zero short hit control PASS. Crash/reopen positive and suppression positive remain NOT_RUN behind the same gate.
Commands (from backend; venv is ../.local-test-evidence/2026-09-05/primary-short-ingestion/venv/bin/python):
- `$python -m pytest tests/memory/test_primary_short_ingestion.py -k real_eleven -q` — RED, not xfail/skip.
- `$python -m pytest tests/memory/test_primary_short_ingestion.py -k 'incomplete_turn or counterexample' -q` — 2 passed.

VERDICT: BLOCKED — original role-preserving terminal S1 cannot satisfy frozen registration source_kind contract.

## A2-1 resolution B — main explicitly authorized 2026-09-05
Only NEW terminal observation commits now produce an independent ASSISTANT_MESSAGE S1. USER remains the original S1.
The observer calls `append_new_primary_message_evidence_tx(db, *, store, host_run_id, terminal_envelope, terminal_receipt)`
after appending the NEW terminal observation and before committing that same SQLite transaction. `store` is the existing
HumanMemoryProgramStore(db_path), `db` is the existing BEGIN IMMEDIATE connection with Row factory and ingress fence.
Returns `PrimaryMessageProduction(evidence_ids: tuple[str,...], blocked_reason: str|None)`.
Complex/unsupported public transcripts return blocked with no per-message writes; they do not fail the foreground task.
Corruption/DB/fault exceptions propagate to rollback the whole observer transaction. The later foreground terminal receipt is
not part of this transaction; authority still requires that actual COMPLETED receipt before publishing any group.

The helper reads actual terminal/USER facts, accepts only completed two-message string transcript + complete dependency proof,
and produces the assistant source with actual terminal timestamp, raw SDK event identity, exact original /messages/1 and
source_kind ASSISTANT_MESSAGE; actor ASSISTANT/model-output is assigned by the registration authority. The new public pointer
is /source/message/content. Envelope AND receipt EvidenceRefs point to exact original USER and terminal; no reverse link/cycle.
Reconciliation only READS this persisted child and compares all bytes to the immutable source derivation. Missing child in old
terminal returns conversation_message_source_missing; it never invokes the producer. Existing-observation replay must not write.
`verify_primary_message_evidence_tx` is the read-only helper for an already shape-validated group; None means absent legacy child,
present but different source raises primary_message_source_corrupt. No new Harness contract or frozen wheel changes.

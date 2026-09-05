# Host Memory audit leaf handoff

2026-09-05. Fixed source3ba25c4263394751d3d5661e12a26bebec343eaf; Dirac independent scoped ACCEPT, no remaining P0/P1.
Tree `simple_harness-memory-operation-audit`, branch `feat/host-memory-operation-audit`,
base7cf2a39c. Native/graph/main environments were not changed. Parent all-operation
coverage remains unfinished. Original contract/oracle was written before implementation:
[CONTRACT.md](CONTRACT.md).

## Delivered production boundary

- `HumanMemoryV7Runtime.typed_recall` and `SemanticCorrectionAuthority.prepare`
  surround the existing public `manager.execute_typed_recall` call with Host durable
  started/settled. Optional public OA1 observation context is selected before calling;
  no TypeError catch-and-retry and no legacy default keyword breakage. Both production
  paths resolve `data/operation-audit.db`, separate from state/Memory business stores.
- Started is persisted before forwarding the Host request/attempt refs. A real SDK
  local-rejection carrier must match the paired witness, public closed reason mapping,
  frozen current context/plan and exact Host request/attempt hashes. The SDK's
  `host_persistence_unverified` is preserved; Host settlement is separate evidence.
  No raw error/query/subject/prompt content is stored. Missing/foreign carrier never
  means zero candidate access. Cancellation preserves unresolved started immediately.
- `compose_terminal_audit` now attaches `PreparationAuditConsumer` to its existing
  production lane, default enabled. Its bounded discovery calls the existing verified
  Host `read_preparation_rejection_tx`, including S1/source/candidate/head checks and
  absence of SDK start/binding. Original public-Memory denial becomes a derived finding
  from that actual Host source, with SDKRun NULL and cost/usage unknown. Repeat and
  reopen deduplicate by actual source hash/owner/rule version. Invalid sources produce
  an unverifiable finding, never a fabricated terminal or execution outbox.
- `MemoryPublicAuditReader.advance(manager, *, requester, target_principal,
  access_receipt, read_ref, limit=100, expected=(), max_pages=4)` consumes public OA1
  pages only when a trusted caller supplies a real sealed receipt. It persists page,
  snapshot and separate access-event hashes with the exact opaque cursor. Query/grant
  binding and the complete saved chain are checked on resume and before completion.
  First read with no saved page may be superseded by a new explicit generation;
  unknown remains recorded. Selected snapshots cannot fall back to live. Concurrent
  late successes/failures cannot overwrite the recovered owner's state.

**Host currently has no production sealed grant issuer or authorized external audit
readthrough.** This helper does not mint one and returns authority_unavailable without
one. The actual public authority fixture is test-only. Context Run references are
hashed claimed correlations, not proof of real SDKRun existence; verified cross-Run
and M:N batch membership joins remain required work. The trusted journal page is a
bounded live keyset read, not a frozen complete-history census.

## Verification and identity

Own isolated ignored venv only: Memory0.6.11 wheel
`d290cbfceb98932735da229f5475713898338ddd7fb3ca498f20256a2d99b8e1`,
source `d520765d160b10566539230806284d2bef364dfe`; Harness0.7.2 exact vendored wheel.
71 Memory Python files match source/wheel/installed byte-for-byte. No SDK source
PYTHONPATH overlay; only Host `PYTHONPATH=backend`. No old wheel/pin modifications.
The installed Harness0.7.2 lacks successor terminal audit pages; existing terminal
capability boundary remains unavailable, not a successful new Harness audit proof.

Final focused command (root-relative):

```sh
PYTHONPATH=backend .local-test-evidence/2026-09-05/host-memory-audit/venv/bin/python -m pytest backend/tests/operation_audit/test_memory_attempts.py backend/tests/operation_audit/test_memory_reader.py backend/tests/operation_audit/test_preparation_audit.py backend/tests/execution/test_preparation_rejection.py -q --tb=short --basetemp=.local-test-evidence/2026-09-05/host-memory-audit/pytest-review-controls
```

**32 passed in6.54s** (23 new audit controls +9 existing preparation controls).
This includes one real production semantic REVISE/reopen factory chain, actual
public SDK protocol/ownership/narrowing/idempotency rejections, foreign owner/carrier,
recording failure, cancellation, real Host preparation receipt discovery/tamper,
actual sealed pagination/reopen/expiry, both live-reader races and complete-chain
corruption. Deterministic Harness adapters only; no remote Provider/native. Focused
Ruff on three modules, consumer/composition and new tests passed. No full source
suite or parent machine gate run. Initial failed tamper injection was blocked by
Host append-only triggers; final test injects storage damage solely in a disposable
Host DB copy, leaving original fixture/production triggers intact.

Raw evidence root `.local-test-evidence/2026-09-05/host-memory-audit/` remains ignored.

| File | SHA-256 |
| --- | --- |
| `baseline.log` | `602885a015f5b6243d45783a7830aab6169fa60034c5f1efbd0da96d332e4283` |
| `combination.log` | `7c3b7d2114d769545b69375df6424b65e0ed10585a61b17e20c8cac836d4899f` |
| `review-controls.log` | `16ac88af6647bf93e682dff0ffd195fc836feae75b66d138474e5c06055b3a20` |
| `ruff-final.log` | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |
| `installed-identity.json` | `65d654f04bfec053f57b7aa354eb05777e129c89e3b2f3ffb6a9250777c30771` |

## Integration and remaining coverage

Source can be integrated independently of graph/native. No main.py edit is needed:
its existing production composition and audit lane consume these hooks. Successor
Memory pin integration belongs to the coordinator. Memory old-signature coverage
is explicitly capability_unavailable while real calls still execute once.

Required next boundaries retain the original PLAN: short recall, history/selected
source reads, cognitive graph/suppression, ingest/registration/index, job/analysis
internals, startup/ownership/inbox, trusted Run/batch joins, sealed grant issuer and
human-authorized audit readthrough, Harness/Service/Realtime call/transport/control
coverage and their combination artifacts. No total costs/usage or physical Provider
call counts are computed here. OA1 enumeration_complete is not all_operations_recorded;
all_operations_recorded remains false. No native/full-program/allops PASS.

## Final independent review

Dirac independently accepted the fixed source above after checking the paired-witness
controls, actual two-live-reader late success/failure, selected cursor expiry, production
callers and preparation source verification. He verified the32-case log/five evidence
hashes and independently compared the exact Memory wheel to installed72 files/71.py.
No suite rerun or native claim. Safe review record is retained ignored as
`independent-review.txt`, SHA-256 `80468b773e7819ebb832d789f128588f5a21c944d4b7e57168d29dc94f072faf`.
This follow-up commit changes conclusion documents only; source remains fixed.

## Coordinator installed integration

Integrated source3ba25c42 and reviewed documents into combined Host06348031 with
installed Memory0611/Harness072/Service0312, no SDK source overlay. Audit attempts,
sealed reader, preparation rejection and real graph/display producer combination:
49PASS16.43s. Only architecture text merge conflicts; business sources unchanged.
This preserves the missing production grant issuer/all-operations/native boundaries.
Raw log in combined candidate `.local-test-evidence/2026-09-05/primary-candidate/`:
`memory-audit-graph-installed.log`, SHA256
691904f3cd98391ace7a666e3e7ff7318473877bf0a22f5194040b6bcd479d6b.

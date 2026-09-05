# Terminal audit leaf verification journal

2026-09-05. This is a scoped intermediate leaf of the parent operation-audit PLAN.
No global machine receipt or program/full-audit PASS. Product source eaccab33c8bf29b85cd585a16192e31ac198c66e.

## Executed checks and identities

- Baseline actual foreground terminal/outbox/history reopen: 1 passed, before changes.
- Expanded source combination: 37 passed (21 new cases at that point + 16 existing
  foreground Runtime cases), 23.32s. No remaining red; the final additional CAS race is
  included in the fixed-candidate run below, not silently counted in that earlier total.
- Fixed product HEAD eaccab33: all 22 new audit cases passed, 10.45s.
- Fixed HEAD with installed Harness0.7.2: 3 actual factory cases passed, 1.59s;
  explicitly proves capability-unavailable compatibility and storage-failure isolation.
- Ruff on five new source modules/two tests: passed. Scoped mypy (follow-imports=skip,
  ignore-missing-imports) five source modules passed; not a whole-Host typecheck.
- Existing production main/run diff is intentionally small; main function AST is executed
  with the real factory and real Runtime, without unrelated app boot. No UI/Provider run.

Independent Python reused read-only:
`/Users/denny/projects/simple_harness-selected-short-sources/.local-test-evidence/2026-09-05/selected-short-sources/installed-069-venv/bin/python`.
Installed Memory0.6.9; Harness audit source archived from fd4a78557ea72cd76875d3ed4ee02d99b4f03cd2.
Source manifest/import provenance in ignored identity.json. No environment installation/change.

```sh
PYTHONPATH=backend:.local-test-evidence/2026-09-05/host-operation-audit/sdk-fd4a/src "$AUDIT_PYTHON" -m pytest backend/tests/operation_audit -q --tb=short
PYTHONPATH=backend:.local-test-evidence/2026-09-05/host-operation-audit/sdk-fd4a/src "$AUDIT_PYTHON" -m pytest backend/tests/operation_audit backend/tests/execution/test_primary_foreground_runtime.py -q --tb=short
PYTHONPATH=backend "$AUDIT_PYTHON" -m pytest backend/tests/operation_audit/test_terminal_audit.py -q --tb=short
ruff check backend/deskpet/operation_audit backend/tests/operation_audit
mypy --follow-imports=skip --ignore-missing-imports backend/deskpet/operation_audit
```

## AC → steps → result (automated, AI operated, no UI scope)

| AC | Reproducible steps and expected assertions | Result |
| --- | --- | --- |
| H1 | `test_actual_terminal_factory...`: actual service enqueue → Runtime/SDK terminal → no wake → public page discovery; one business Provider invocation, original terminal/outbox remain unchanged. | PASS |
| H2 | `test_page_crash_reopen...` and `test_later_real_terminal...`: save first page → later page fault or second real Run → close/reopen → exact pinned cursor/snapshot and page count. `test_pinned_page_fault...` rejects missing/mismatched Run/hash/index/header without open. Later real Run tests isolation, not arbitrary rewriting of a terminal Run. | PASS |
| H3 | `test_unsaved_open...`: real open then Host commit fault → unknown retained → linked generation2. `test_cancelled_reader...`: await cancellation leaves started; reclaim recovers only read. | PASS |
| H4 | `test_lost_first_dispatch...` and `test_expired_reader...`: two consumers, actual read/event barrier, expired first owner cannot settle/commit over winner. Atomic before/after-commit faults and replay preserve pages/findings. `test_reported_usage...`: same owner/error/usage represented head+settle+reconciliation across pages → one finding/three supports, no calls/usage/cost totals. | PASS |
| H5 | `test_corrupt_bounded_prefix...`: corrupt a temporary Host authority (production guard explicitly removed only for test injection) → safe durable rejection → later valid terminal progresses → repaired original binding reevaluates, old rejection retained. `test_corrupted_host_resume_anchor...`: lost snapshot/cursor or corrupt frontier page cannot reopen. Old installed SDK becomes unavailable. | PASS |
| H6 | Production safe pages exclude user/provider/reasoning canaries; actual success/failure with missing usage yields nonempty correctly bound findings and unknown price. Effect correlation stays effect-owned. Business invocation count unchanged by all audit recovery paths. | PASS |
| H7 | Exact main activation starts singleton by default; real terminal wake invokes lane, close ends background task. Actual audit DB open failure reports storage_unavailable while foreground completes. Source wiring includes shutdown before SDK close. | PASS |

Full snapshot enumeration is separate from partial legacy/history/producer coverage.
The usage-repetition fixture is a controlled public projection-boundary test; it is not
presented as a production reconciliation event. The real SDK fd4a snapshot tests actual
head/transition facts and actual successful/failed deterministic Provider execution.

## Idempotency and review

Started precedes slow read; page/findings/supports/cursor/settlement atomic. Lease generation
and owner/expiry fence stale commits. Unsaved-open supersession retains unknown; pinned
cursor never becomes a live query. Finding key uses owning operation/rule/version/owner,
not source hash, page index or event count. Missing usage/price does not become zero.
Source rejection fingerprint changes with selected authority facts; unchanged poison input
cannot dominate each discovery batch. No source DB mutations and no business resend API.
Dirac independently challenged contract (unsaved read recovery and poisoned discovery);
fixes above applied. Fixed source correctness review requested; result pending below.
No push/release requested. Parent required coverage remains incomplete; no change to AC.

## Evidence index

All raw files are ignored beneath `.local-test-evidence/2026-09-05/host-operation-audit/`.
Logs contain only deterministic fixture data; no credentials/native/provider traffic.

| Relative file | SHA-256 |
| --- | --- |
| `baseline.log` | `43db26f998bc22eb28c0a45cbdbba6a1e973537f513c2dcd663ff6df3a77cac6` |
| `expanded-adjacent.log` | `0a73bd122a2023d91fb152d478eacd7babd18e7ad69c8cafa4726b96e5b0f9b3` |
| `fixed-eaccab33.log` | `c3f1c3091e6d4e35571476b5bef13abff82efef2266c67c8d34a6d8821097ee2` |
| `installed-072-factory.log` | `9c078973f1624f33eef2fcc94142e43aea74fa0db60028d01031a3f3fda4f835` |
| `identity.json` | `7e2854149620add97c511008bf7a5319df144c04bc8445d31e5a36ee17d9552b` |

Known gaps and exact integration surface: HANDOFF.md. Newly reported preRun preparation
failure is not captured by a terminal-only producer. Main supplied retained durable rows:
CLAIMED/sdk_run_id=NULL, three HostRuns and only the original two succeeded Provider
invocations; this supersedes its earlier QUEUED inference (index/hash in HANDOFF.md).
This is main-reported evidence, not an independent reproduction by this leaf. Existing Service source work
is separate and unchanged by this Host leaf.

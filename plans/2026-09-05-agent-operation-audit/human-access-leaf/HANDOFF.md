# HUMAN audit access handoff

Last updated: 2026-09-06.

- Source: `1097b2727af67a5e857115dea0e84ba596627b87`.
- Tree: `/Users/denny/projects/simple_harness-audit-access`.
- Branch: `feat/human-memory-audit-access`, base `54156f1e`.
- Fixed-source independent review: Dirac scoped ACCEPT, no new P0/P1; product
  source and five evidence hashes reviewed without rerunning tests. This handoff
  is owner-written documentation, not independently reviewed product code.
- [Contract](CONTRACT.md), [verification and raw evidence index](RESULTS.md),
  [acceptance mapping](acceptance.md). No whole-program/native completion claim.

## Integration

Cherry-pick the source commit into the main-owned candidate. Preserve the newer
main/native canvas and forget-ACK lifecycle fixes; this branch intentionally did
not edit PrimaryChatView, boundPort, controller or graph renderer. The only existing
frontend product change adds the audit tab to PrimaryMemoryPanel.

Backend overlaps are intentionally narrow: compose_human_memory_runtime creates
HumanAuditAccess, HumanMemoryV7Runtime injects its public authority into the builder
and revokes grants on close, HumanMemoryHostService exposes the reader, the API
validates exact DTOs, and main keeps final send inside its actual signed request
scope. Preserve newer runtime/terminal-audit identity/SDK pin changes when merging.
New Host storage reuses operation-audit.db for grant/delivery tables and state.db
S1 admission for source proof. No existing data is deleted or moved.

No feature flag needs enabling. Once integrated, MemoryPanel has an operation
record tab; mount never grants access. The user must explicitly open a five-minute
metadata grant, then explicitly read each page. Backend missing public audit
capability rejects access instead of falling back to legacy/private reads.

## Verification available

Backend40 PASS / one full-WS fixture deselected; frontend25 PASS; no-emit typecheck
PASS. The cached-bound actual-parent counterfactual is red and the restored source
green. Source tested with installed Memory0.6.12/Harness0.7.2; main's successor SDK
combination is not inferred tested from these results. No dependency reinstall or
shared node_modules change is needed for source integration.

Test slots were released. No own test/native/model process remains running.
Raw evidence stays in this tree's ignored root. Main integration, full installed
Host WS and native demonstration are coordinator-owned remaining checks. These
checks are distinct from original missing all-operation producers/coverage, which
this metadata entry does not implement.

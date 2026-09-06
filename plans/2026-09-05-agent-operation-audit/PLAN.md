# Agent operation recording and improvement audit

Date: 2026-09-05. Status: implementation scope accepted by user; coverage inventory
in progress. This is an addition to the Human Memory execution revision, not a
replacement or waiver of its original acceptance criteria.

## User outcome and authority

The user requested that every simple_harness Agent operation be recorded and
audited to improve simple_harness and the related SDKs, explicitly including SDK
changes. Existing execution permission applies. No additional approval round is
needed for scoped implementation and verification.

"Operation" means an observable runtime boundary: Run admission/control/terminal,
Provider attempt, tool/effect dispatch and result, context/recall selection,
memory admission/analysis/mutation/suppression, and service transport lifecycle.
It does not mean recording private model reasoning or every interpreter step.

## Existing facts, not a completion claim

Harness 0.7.2 has canonical runtime records and public read surfaces, plus a V1
observability emitter. The emitter has a bounded queue, explicit drops and a
default NoopSink. Host composes a local JSONL/ring/logging sink, but JSONL rotates
at 1 MiB with three files and cannot prove complete historical coverage. Host
ProviderWorkloadAuditStore keeps call metadata with bounded retention. Service
SDK has a separate bounded Realtime diagnostic stream. Memory receipts and
visibility audits are domain facts; they do not by themselves establish an
automatic cross-SDK optimization audit. Inventory must identify each actual
writer, durable source, reader and missing boundary.

## Contract before implementation

- Reuse canonical SDK/Host ledgers as execution authority. Audit projections are
  derived, versioned and replayable; they do not create another execution state
  machine or authorize operations. Host reads SDK state through public APIs.
- Preserve stable Host Run / SDK Run / attempt / effect / evidence identities and
  exact source revisions. Missing coverage, active operations and unknown sends
  remain explicit. Missing logs cannot imply success or absence of an operation.
- Record metadata and safe source references sufficient to inspect actual
  inputs/outputs, outcomes, retry/recovery, duration and reported usage. Do not
  duplicate unrestricted payloads or persist credentials/private reasoning.
  Missing usage and unavailable price attribution remain unknown, never zero.
- Persist audit input references and audit version so a restarted worker can
  resume without duplicate findings. Auditing must not resend Provider calls or
  repeat effects. Record failures of recording/auditing as degraded coverage.
- Audit by Run using deterministic checks first; no paid model call per operation.
  Findings bind source operation IDs, rule version, reason and owning component.
  A finding proposes an improvement; it does not silently change product code.
- New evidence stays local and ignored. Track only concise reviewable conclusions,
  commands, identifiers and hashes in Git. Never upload raw logs. Do not silently
  apply diagnostic log rotation to canonical execution evidence.
- Complete, tested product coverage defaults ON. An incomplete slice must be
  reported with its exact coverage; no claim that all SDK operations are audited.

## Decisive checks

1. A real deterministic runtime Run with successful, failed and retried calls,
   denied tool/effect, cancellation and terminal state yields matching canonical
   operation identities and outcomes. SDK standalone use is covered separately.
2. Disable/drop the best-effort diagnostic sink: durable audit still accounts for
   recorded execution, and explicitly reports any non-durable coverage gap.
3. Crash/reopen/replay after execution and before audit persistence: no effect or
   Provider resend, no duplicate finding; audit revision remains traceable.
4. Inject credential-shaped values and untrusted error text: exported records
   preserve useful codes/references without authentication material.
5. Exercise actual production composition. Unit tests alone do not prove the Host
   enabled the sink/audit consumer or that Memory/Service use the correlation.

## Delivery sequence

First map public durable readers and coverage, then implement a Harness public
operation audit slice and Host consumption. Memory and Service changes retain
their own versioned contracts and installed-artifact verification. Keep this work
separate from the already reviewed Memory 0.6.9 migration artifact. The ongoing
remember/use/correct/forget native loop remains the first Human Memory delivery.

# Plan challenge iterations

## Round 1 — FAIL

Closed items:

1. Terminal projection eventual-consistency gap: added targeted terminal drain plus next-turn
   execution read-through deduped by event ID.
2. Root plan admission removal: mapped `AdmissionSpec`, provider snapshot, stored plan,
   plan-read-only and broad TaskGrant retirement; exact prepared-call authorization becomes the
   replacement while generic Kernel admission remains.
3. Preference/growth schema: defined interpreter v2, v1 receipt fallback, assessment hash and
   first-writer replay semantics.
4. Verification profile: defined `VerificationProfileV1`, freeze/serialization/consumer and the
   two-stage evidence/claim checks.
5. Async session epoch versus sync contributor: defined typed target, async ingress preload,
   sink double fence and fail-fast composition.
6. Per-tool resource model: added exact final params/selectors/containment table for all candidates.
7. Oracle governance: added `behavior-contract.md` with source hashes and exact old/new behavior.
8. Voice and exact suite: named real test files and removed placeholder test filenames.

## Round 2 — FAIL

Closed items:

1. Targeted terminal drain now has an exact `claim_delivery_for_event` API contract that
   reuses the existing owner-generation, lease, version-CAS and settle path; foreground/background
   competition is explicitly tested.
2. Session read-through now has a frozen DTO, an exact session+epoch target query, root/child
   filtering, stable ordering, one shared sanitizer, causal placement, token accounting and
   event-ID dedupe.
3. Preference concurrency no longer promises physical provider exactly-once. The contract is one
   first-writer receipt and at-most-once growth admission/reflection; concurrent samples are allowed.
4. Verification now has a complete Context OS task-type matrix. Unknown/malformed profiles become
   strict+evidence-required, and actual tool/network receipts upgrade a light profile.
5. Permission scope failure now has a legal per-index boundary transition: persist FAILED outcome,
   preserve provider order, continue remaining calls, and resume the model with aligned results.
6. The staged-file mismatch was resolved according to actual behavior: real direct Office writers
   are classified `opaque_manual`, while PreparedTargets and exact selectors still bind their
   resources. A true staged migration is explicitly outside this authorization slice.
7. High-risk evidence was added: live workflow exact-request failure, existing SQLite claim/restart
   tests (`2 passed`), a real `doc_create` direct-write spike, and code proof that preference has no
   pre-provider lease.
8. Added the generic-admission negative regression: ordinary turns lose plan state, while explicit
   durable workflows retain create/wait/resume admission behavior.

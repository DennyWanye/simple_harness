# Semantic relation SDK slice — 2026-09-01

## Scope and status

| Testcase | Required slice steps | Result | Evidence |
|---|---|---|---|
| TC-HM-12 rev3 | 1–5 and relation trace subset of 8–9 | PASS | `.local-test-evidence/2026-09-01/semantic-relations-public-run02/` |
| TC-HM-08 rev4 | 6–9 relation integrity subset | PASS (40/40) | `.local-test-evidence/2026-09-01/semantic-relations-integrity-run22/` |

## Exact candidate identity

- Harness: `simple-harness-sdk==0.7.0`, source `3e7a71af1dfea2e065530208225ac13fc5f17300`,
  reproducible wheel SHA-256 `d241052d4bb7397971da8a99f680e397288bbbefc0d9304fa97f059941dd93bd`.
- Memory: `simple-harness-memory-sdk==0.6.0`, source `64284059f9ee82d886d85151a95c542660d09c1a`,
  reproducible wheel SHA-256 `844cbabaddb33b6ed48d1104ba1427335dcc267a33f10ff93f13c8fec5d06d5e`.

## Decisive outcomes

- Public package-root Manager path: strict three-operation commit produced two visible nodes and one directed
  `applies_to` edge; the relation memory was not a node. Endpoint suppression removed the edge, and close/reopen
  preserved the zero-edge view.
- Integrity path: all 40 frozen admission, transaction-fault, replay, lifecycle, expiry, suppression,
  classification and restart-corruption cases passed from exact installed wheels.
- Raw evidence retention: `PASS_BYTE_IDENTICAL_ALL_CASES`; physical deletion count remained zero.
- Host durable pre-admission LLM validation audit: `NOT_RUN/BLOCKED_UNTIL_S5` by approved scope. The Harness
  diagnostic and Memory zero-call/zero-delta boundary passed, but this is not evidence of Host persistence.
- Real LLM relation-extraction quality: `NOT_RUN/BLOCKED`; this deterministic SDK slice does not claim provider quality.

The ignored evidence directories are retained locally and must not be deleted or committed to Git.

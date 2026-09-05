# Explicit cognitive action evidence callback

2026-09-05. Implementation preparation for the approved remember/use/correct/forget loop.
This callback persists a real authenticated UI action; it is not the SDK forget operation or
proof that a memory target is currently visible. Those checks belong to the action service.

Use existing Host HumanMemoryProgramStore evidence admission and original committed_at.
No new schema/table, no legacy fact ID, no SDK-private access or model call. The callback
accepts only forget with exact memory_id, expected_revision and expected_content_hash plus
an idempotency_key; the service verifies the public cognitive node before first admission.

A dedicated cognitive-action source_ref and derived idempotency namespace distinguish these
admissions from generic primary.append. Existing action replay must match domain, subject,
canonical envelope/receipt and exact payload. A same-key changed target cannot reuse admission.
find_action returns None only for an absent source, never for a corrupt or foreign-domain row.
The original Host timestamp is returned unchanged after reopen, and the SDK suppression ID is
stable from the admitted evidence ID. Retried requests use the same SDK suppression timestamp;
no cached UI graph is treated as mutation authorization. This evidence is not ingested as a
new semantic memory or registered as a conversational group.

Interface:
- find_action(*, payload, idempotency_key) -> CognitiveActionEvidence | None
- admit_action(*, payload, idempotency_key) -> CognitiveActionEvidence
- receipt fields: evidence_id, committed_at, payload_hash, suppression_request_id.

Host service supplies its already authenticated subject/authority_ref. The existing connection
request fence and recovery writer fence remain authoritative through admission/replay. The
caller must recheck the request boundary around the actual SDK suppression write and must
not report success if admission succeeded but suppression acknowledgment is unknown.

First admission and SDK suppression are two durable stages. If interrupted between them,
retry reads the exact existing action and uses the same public SDK request. Replay cannot
skip domain/hash validation merely because a memory disappeared from the current view.
No automatic undo, physical deletion, artificial USER assertion or source-only/full-ingest fallback.

## Initial callback verification (not cognitive UI/SDK integration)

Four real Host SQLite tests passed0.89s: exact first committed_at/reopen/replay, changed-target
conflict, generic primary.append colliding ID rejected by its different source domain, and
subject/input separation. No foreground Run or memory-ingestion outbox is created by action
admission. No SDK suppression, current-node authorization, connection/UI or external Provider
completion is claimed. Consumer wiring and independent review remain pending.

Command: `PYTHONPATH=backend .local-test-evidence/2026-09-05/primary-candidate/venv/bin/python -m pytest backend/tests/memory/test_primary_cognitive_evidence.py -q -p no:cacheprovider`.
Ruff import/format corrections only afterward. Raw log stays ignored at
`.local-test-evidence/2026-09-05/primary-candidate/cognitive-action-evidence.log`, SHA256 `d9d33fa6f0d3d2f665211b4daaca7a420ee8c9812a7e07084fcabe20033c38be`.

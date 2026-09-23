-- Named queries; values are bound parameters. JSON rows require strict codec + source verification.
-- Q01: exact approved requirements; missing/duplicate/hash mismatch -> SourceUnavailable.
SELECT * FROM requirements_revisions WHERE mission_id=:mission AND revision=:revision AND content_hash=:hash;
-- Q02: original official review, exact package. Also verify execution-side binding in application.
SELECT r.*, b.binding_json FROM review_records r JOIN assurance_review_record_bindings b ON b.record_id=r.record_id
 WHERE r.mission_id=:mission AND r.record_id=:record AND r.package_id=:package;
-- Q03: actual review dispatch, not a fake Worker Attempt.
SELECT b.*, v.ordinal,v.dispatch_intent_id,v.reservation_event_id,i.state AS dispatch_state FROM assurance_review_bindings b
 JOIN assurance_review_invocations v ON v.review_key=b.review_key AND v.mission_id=b.mission_id
 JOIN dispatch_intents i ON i.intent_id=v.dispatch_intent_id
 WHERE b.mission_id=:mission AND b.review_key=:review_key ORDER BY v.ordinal;
-- Q04: complete signed observation population; enforce max bound before using truncation.
SELECT * FROM observations WHERE mission_id=:mission ORDER BY observation_id;
-- Q05: whole rule catalog is needed to catch newly inserted alternate/negative rules.
SELECT * FROM justification_sets WHERE mission_id=:mission ORDER BY set_id;
-- Q06: complete rule membership, explicit negative literals are not negation-as-failure.
SELECT m.* FROM support_members m JOIN justification_sets j ON j.set_id=m.set_id
 WHERE j.mission_id=:mission AND m.mission_id=:mission ORDER BY m.set_id,m.member_kind,m.member_id;
-- Q07: aggregate epoch, initialized at lane activation, never synthesized by a reader.
SELECT epoch,bumped_by FROM validity_epochs WHERE mission_id=:mission AND scope_id=:assurance_partition;
-- Q08: atomic epoch CAS. Existing evidence writer uses same transaction as source/event.
UPDATE validity_epochs SET epoch=epoch+1,bumped_by=:event_id,updated_at=:now_seconds
 WHERE mission_id=:mission AND scope_id=:assurance_partition AND epoch=:expected_epoch;
-- Q09: reverse dependencies are an optimization; missing index is not an empty universe.
SELECT c.certificate_id,c.consumer_kind,c.consumer_id FROM assurance_dependency_index d
 JOIN assurance_use_certificates c ON c.certificate_id=d.certificate_id AND c.mission_id=d.mission_id
 WHERE d.mission_id=:mission AND d.dependency_kind=:kind AND d.dependency_key=:key;
-- Q10: original receipts provide command idempotency. Verify the stored input hash in JSON.
SELECT * FROM commit_receipts WHERE commit_id=:command_id;
-- Q11: closeout CAS: rowcount must be 1, otherwise abort the whole transaction.
UPDATE assurance_closeouts SET state=:next_state,row_version=row_version+1,resolution_id=:resolution_id,
 check_body_hash=:body_hash,check_body_json=:body,last_receipt_id=:receipt_id,updated_at_ms=:now_ms
 WHERE mission_id=:mission AND row_version=:expected_version AND state=:expected_state;
-- Q12: ingress cursor, updated in the SAME transaction as durable pending classification.
-- Expensive prepared effects/receipt use the separate work ACK transaction.
UPDATE assurance_event_cursors SET last_event_seq=:event_seq,row_version=row_version+1,updated_at_ms=:now_ms
 WHERE mission_id=:mission AND consumer=:consumer AND row_version=:expected_version AND last_event_seq=:previous_seq;
-- Q13: original consumer event scan. Implement with actual Store.list_events/iter_events interface;
-- do NOT assume a column named seq if the current source differs.
-- Q14: exact content preparation contributions; no Task.COMPLETED predicate.
SELECT a.*,s.document_json AS contribution_json FROM acceptances a JOIN operation_acceptance_scopes s ON s.acceptance_id=a.acceptance_id
 WHERE a.mission_id=:mission AND s.mission_id=:mission AND s.completion_scope_id=:scope_id;
-- Q14 uses OCC-1.0 operation_acceptance_scopes.document_json, not a guessed column.

-- Q15: Acquire. Caller checked exact refs and holds shared final-GC Store transaction.
INSERT INTO assurance_blob_pins
 (pin_id,mission_id,review_key,blob_hash,object_ref_json,state,row_version,created_at_ms,
  released_at_ms,source_receipt_id,last_receipt_id)
VALUES (:pin_id,:mission_id,:review_key,:blob_hash,:object_ref_json,'PREPARING',1,
 :now_ms,NULL,:receipt_id,:receipt_id);
-- Q16: Final deletion guard. Must ALSO query all original CAS reference roots.
SELECT pin_id FROM assurance_blob_pins
 WHERE blob_hash=:blob_hash AND state IN ('PREPARING','BOUND');
-- Q17: Bind to persisted review in its original package/intent transaction; rowcount must be one.
UPDATE assurance_blob_pins SET state='BOUND',row_version=row_version+1,last_receipt_id=:receipt_id
 WHERE pin_id=:pin_id AND state='PREPARING' AND row_version=:expected_version;
-- Q18: Release only after authorized safe cleanup; not a TTL-based DELETE.
UPDATE assurance_blob_pins SET state='RELEASED',row_version=row_version+1,
 released_at_ms=:now_ms,last_receipt_id=:receipt_id
 WHERE pin_id=:pin_id AND state=:expected_state AND row_version=:expected_version;

-- 1.1 ENQUEUE + cursor CAS in ONE Store transaction; run only for relevant logical work.
INSERT INTO assurance_pending_work(mission_id,consumer,work_key,trigger_event_id,target_epoch,target_fingerprint,state,row_version,tries,not_before_ms,wait_reason,owner,lease_until_ms)
VALUES(:mission,:consumer,:key,:event,:epoch,:fingerprint,'PENDING',1,0,:now,NULL,NULL,NULL)
ON CONFLICT(mission_id,consumer,work_key) DO UPDATE SET
 trigger_event_id=excluded.trigger_event_id,target_epoch=excluded.target_epoch,
 target_fingerprint=excluded.target_fingerprint,state='PENDING',row_version=assurance_pending_work.row_version+1,
 not_before_ms=excluded.not_before_ms,wait_reason=NULL,owner=NULL,lease_until_ms=NULL
WHERE excluded.target_epoch>assurance_pending_work.target_epoch;
-- No new target? Cursor still advances after idempotent work lookup.
UPDATE assurance_event_cursors SET last_event_seq=:next_seq,row_version=row_version+1,updated_at_ms=:now
WHERE mission_id=:mission AND consumer=:consumer AND row_version=:expected_version AND last_event_seq=:expected_seq;
-- Claim due work. Actual dispatch is always the original intent mechanism.
UPDATE assurance_pending_work SET state='RUNNING',owner=:owner,lease_until_ms=:lease_until,
 row_version=row_version+1,tries=tries+1
WHERE mission_id=:mission AND consumer=:consumer AND work_key=:key AND row_version=:expected
AND state IN ('PENDING','WAITING') AND not_before_ms<=:now
AND (wait_reason IS NULL OR wait_reason<>'MANUAL_REQUIRED');
-- Work commits/receipt + ACK use the same transaction. Old prepared results cannot ACK a newer target.
UPDATE assurance_pending_work SET state='DONE',owner=NULL,lease_until_ms=NULL,row_version=row_version+1
WHERE mission_id=:mission AND consumer=:consumer AND work_key=:key AND state='RUNNING'
AND row_version=:expected AND owner=:owner AND target_epoch=:epoch AND target_fingerprint=:fingerprint;
-- Retry/real budget wait: source ingestion and other consumers remain runnable.
UPDATE assurance_pending_work SET state='WAITING',owner=NULL,lease_until_ms=NULL,
 row_version=row_version+1,not_before_ms=:next_at,wait_reason=:reason
WHERE mission_id=:mission AND consumer=:consumer AND work_key=:key AND state='RUNNING'
AND row_version=:expected AND owner=:owner;
SELECT * FROM assurance_review_invocations WHERE review_key=:review_key ORDER BY ordinal;
SELECT * FROM assurance_disclosure_batches WHERE review_key=:review_key ORDER BY batch_no;
SELECT * FROM assurance_criterion_policies WHERE mission_id=:mission AND requirements_revision=:requirements_revision AND scope_hash=:scope_hash;
SELECT lane,origin,source_hash,receipt_id FROM assurance_creation_contracts WHERE mission_id=:mission;
SELECT epoch,clock_generation,wall_high_ms,clock_state,row_version FROM assurance_environment_state WHERE singleton=1;

-- Expired coordination lease permits computation reclamation, not a new external invocation.
UPDATE assurance_pending_work SET state='PENDING',owner=NULL,lease_until_ms=NULL,row_version=row_version+1
WHERE mission_id=:mission AND consumer=:consumer AND work_key=:key AND row_version=:expected
AND state='RUNNING' AND lease_until_ms<=:now;

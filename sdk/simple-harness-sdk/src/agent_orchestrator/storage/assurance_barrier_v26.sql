
CREATE TRIGGER assurance_import_event_no_update BEFORE UPDATE ON events
WHEN OLD.type IN ('AssuranceCheckSpecRegistered','AssuranceEvidenceDisclosed','AssuranceExecutionImported','AssuranceLocalCheckFinished','AssuranceReservationLinked','AssuranceReviewTurnImported') OR NEW.type IN ('AssuranceCheckSpecRegistered','AssuranceEvidenceDisclosed','AssuranceExecutionImported','AssuranceLocalCheckFinished','AssuranceReservationLinked','AssuranceReviewTurnImported')
BEGIN SELECT RAISE(ABORT,'immutable Assurance source event'); END;
CREATE TRIGGER assurance_import_event_no_delete BEFORE DELETE ON events
WHEN OLD.type IN ('AssuranceCheckSpecRegistered','AssuranceEvidenceDisclosed','AssuranceExecutionImported','AssuranceLocalCheckFinished','AssuranceReservationLinked','AssuranceReviewTurnImported')
BEGIN SELECT RAISE(ABORT,'retain Assurance source event'); END;
CREATE TRIGGER assurance_import_event_no_replace BEFORE INSERT ON events
WHEN EXISTS(SELECT 1 FROM events prior
 WHERE (prior.type IN ('AssuranceCheckSpecRegistered','AssuranceEvidenceDisclosed','AssuranceExecutionImported','AssuranceLocalCheckFinished','AssuranceReservationLinked','AssuranceReviewTurnImported') OR NEW.type IN ('AssuranceCheckSpecRegistered','AssuranceEvidenceDisclosed','AssuranceExecutionImported','AssuranceLocalCheckFinished','AssuranceReservationLinked','AssuranceReviewTurnImported'))
 AND (prior.seq=NEW.seq OR prior.event_id=NEW.event_id
      OR prior.idempotency_key=NEW.idempotency_key))
BEGIN SELECT RAISE(ABORT,'duplicate Assurance source event; replay original receipt'); END;

CREATE TRIGGER assurance_source_sources_insert AFTER INSERT ON sources WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:sources',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','sources'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_sources_update AFTER UPDATE ON sources WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.tenant_id IS NOT OLD.tenant_id OR NEW.path IS NOT OLD.path OR NEW.version_hash IS NOT OLD.version_hash OR NEW.kind IS NOT OLD.kind OR NEW.trust IS NOT OLD.trust OR NEW.registered_at IS NOT OLD.registered_at OR NEW.superseded_by IS NOT OLD.superseded_by OR NEW.revoked IS NOT OLD.revoked OR NEW.revision IS NOT OLD.revision) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:sources',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','sources'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_sources_delete AFTER DELETE ON sources WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:sources',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','sources'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_artifacts_insert AFTER INSERT ON artifacts WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:artifacts',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','artifacts'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_artifacts_update AFTER UPDATE ON artifacts WHEN (NEW.artifact_id IS NOT OLD.artifact_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.task_id IS NOT OLD.task_id OR NEW.attempt_id IS NOT OLD.attempt_id OR NEW.path IS NOT OLD.path OR NEW.content_hash IS NOT OLD.content_hash OR NEW.version IS NOT OLD.version OR NEW.json IS NOT OLD.json OR NEW.created_at IS NOT OLD.created_at) AND NOT (assurance_offline_relocation()=1 AND NEW.artifact_id IS OLD.artifact_id AND NEW.mission_id IS OLD.mission_id AND NEW.task_id IS OLD.task_id AND NEW.attempt_id IS OLD.attempt_id AND NEW.path IS OLD.path AND NEW.content_hash IS OLD.content_hash AND NEW.version IS OLD.version AND NEW.created_at IS OLD.created_at AND assurance_same_artifact_content(OLD.json,NEW.json)=1) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:artifacts',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','artifacts'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_artifacts_delete AFTER DELETE ON artifacts WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:artifacts',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','artifacts'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_artifacts_relocation_0 BEFORE INSERT ON artifacts
WHEN EXISTS(SELECT 1 FROM artifacts prior WHERE prior.artifact_id=NEW.artifact_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;


CREATE TRIGGER assurance_source_artifacts_relocation_1 BEFORE INSERT ON artifacts
WHEN EXISTS(SELECT 1 FROM artifacts prior WHERE prior.attempt_id=NEW.attempt_id AND prior.path=NEW.path AND prior.version=NEW.version
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_observations_insert AFTER INSERT ON observations WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:observations',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','observations'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_observations_update AFTER UPDATE ON observations WHEN (NEW.observation_id IS NOT OLD.observation_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.proposition_key IS NOT OLD.proposition_key OR NEW.polarity IS NOT OLD.polarity OR NEW.scope_id IS NOT OLD.scope_id OR NEW.source_kind IS NOT OLD.source_kind OR NEW.source_id IS NOT OLD.source_id OR NEW.coverage IS NOT OLD.coverage OR NEW.observer_id IS NOT OLD.observer_id OR NEW.observed_at_ms IS NOT OLD.observed_at_ms OR NEW.recorded_at_ms IS NOT OLD.recorded_at_ms OR NEW.query_watermark_ms IS NOT OLD.query_watermark_ms OR NEW.valid_until_ms IS NOT OLD.valid_until_ms OR NEW.observation_json IS NOT OLD.observation_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:observations',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','observations'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_observations_delete AFTER DELETE ON observations WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:observations',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','observations'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_observations_relocation_0 BEFORE INSERT ON observations
WHEN EXISTS(SELECT 1 FROM observations prior WHERE prior.observation_id=NEW.observation_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_justification_sets_insert AFTER INSERT ON justification_sets WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:justification_sets',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','justification_sets'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_justification_sets_update AFTER UPDATE ON justification_sets WHEN (NEW.set_id IS NOT OLD.set_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.subject_kind IS NOT OLD.subject_kind OR NEW.subject_id IS NOT OLD.subject_id OR NEW.member_revision IS NOT OLD.member_revision OR NEW.member_digest IS NOT OLD.member_digest OR NEW.rule_ref IS NOT OLD.rule_ref OR NEW.detail_json IS NOT OLD.detail_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:justification_sets',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','justification_sets'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_justification_sets_delete AFTER DELETE ON justification_sets WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:justification_sets',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','justification_sets'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_justification_sets_relocation_0 BEFORE INSERT ON justification_sets
WHEN EXISTS(SELECT 1 FROM justification_sets prior WHERE prior.set_id=NEW.set_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_support_members_insert AFTER INSERT ON support_members WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:support_members',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','support_members'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_support_members_update AFTER UPDATE ON support_members WHEN (NEW.set_id IS NOT OLD.set_id OR NEW.member_kind IS NOT OLD.member_kind OR NEW.member_id IS NOT OLD.member_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.polarity IS NOT OLD.polarity OR NEW.member_revision IS NOT OLD.member_revision OR NEW.member_json IS NOT OLD.member_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:support_members',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','support_members'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_support_members_delete AFTER DELETE ON support_members WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:support_members',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','support_members'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_support_members_relocation_0 BEFORE INSERT ON support_members
WHEN EXISTS(SELECT 1 FROM support_members prior WHERE prior.set_id=NEW.set_id AND prior.member_kind=NEW.member_kind AND prior.member_id=NEW.member_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_task_semantics_insert AFTER INSERT ON task_semantics WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:task_semantics',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','task_semantics'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_task_semantics_update AFTER UPDATE ON task_semantics WHEN (NEW.task_id IS NOT OLD.task_id OR NEW.binding_revision IS NOT OLD.binding_revision OR NEW.mission_id IS NOT OLD.mission_id OR NEW.obligation_id IS NOT OLD.obligation_id OR NEW.form IS NOT OLD.form OR NEW.semantic_scope IS NOT OLD.semantic_scope OR NEW.goal_signature_id IS NOT OLD.goal_signature_id OR NEW.operator_ref IS NOT OLD.operator_ref OR NEW.adopted_method_instance_id IS NOT OLD.adopted_method_instance_id OR NEW.input_binding_revision IS NOT OLD.input_binding_revision OR NEW.dispatch_generation IS NOT OLD.dispatch_generation OR NEW.content_hash IS NOT OLD.content_hash OR NEW.binding_json IS NOT OLD.binding_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:task_semantics',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','task_semantics'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_task_semantics_delete AFTER DELETE ON task_semantics WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:task_semantics',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','task_semantics'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_task_semantics_relocation_0 BEFORE INSERT ON task_semantics
WHEN EXISTS(SELECT 1 FROM task_semantics prior WHERE prior.task_id=NEW.task_id AND prior.binding_revision=NEW.binding_revision
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_method_instances_insert AFTER INSERT ON method_instances WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:method_instances',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','method_instances'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_method_instances_update AFTER UPDATE ON method_instances WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.instance_id IS NOT OLD.instance_id OR NEW.goal_task_id IS NOT OLD.goal_task_id OR NEW.goal_occurrence_id IS NOT OLD.goal_occurrence_id OR NEW.obligation_id IS NOT OLD.obligation_id OR NEW.method_id IS NOT OLD.method_id OR NEW.method_version IS NOT OLD.method_version OR NEW.method_content_hash IS NOT OLD.method_content_hash OR NEW.plan_revision IS NOT OLD.plan_revision OR NEW.state IS NOT OLD.state OR NEW.parameters_digest IS NOT OLD.parameters_digest OR NEW.draft_json IS NOT OLD.draft_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:method_instances',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','method_instances'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_method_instances_delete AFTER DELETE ON method_instances WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:method_instances',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','method_instances'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_requirements_revisions_insert AFTER INSERT ON requirements_revisions WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:requirements_revisions',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','requirements_revisions'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_requirements_revisions_update AFTER UPDATE ON requirements_revisions WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.revision IS NOT OLD.revision OR NEW.revision_id IS NOT OLD.revision_id OR NEW.content_hash IS NOT OLD.content_hash OR NEW.authority_subject IS NOT OLD.authority_subject OR NEW.revision_json IS NOT OLD.revision_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:requirements_revisions',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','requirements_revisions'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_requirements_revisions_delete AFTER DELETE ON requirements_revisions WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:requirements_revisions',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','requirements_revisions'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_requirements_revisions_relocation_0 BEFORE INSERT ON requirements_revisions
WHEN EXISTS(SELECT 1 FROM requirements_revisions prior WHERE prior.revision_id=NEW.revision_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_review_packages_insert AFTER INSERT ON review_packages WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:review_packages',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','review_packages'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_review_packages_update AFTER UPDATE ON review_packages WHEN (NEW.package_id IS NOT OLD.package_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.purpose IS NOT OLD.purpose OR NEW.review_account IS NOT OLD.review_account OR NEW.obligation_id IS NOT OLD.obligation_id OR NEW.subject_kind IS NOT OLD.subject_kind OR NEW.subject_id IS NOT OLD.subject_id OR NEW.requirements_revision IS NOT OLD.requirements_revision OR NEW.input_manifest_hash IS NOT OLD.input_manifest_hash OR NEW.method_instance_id IS NOT OLD.method_instance_id OR NEW.package_hash IS NOT OLD.package_hash OR NEW.package_json IS NOT OLD.package_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:review_packages',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','review_packages'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_review_packages_delete AFTER DELETE ON review_packages WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:review_packages',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','review_packages'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_review_packages_relocation_0 BEFORE INSERT ON review_packages
WHEN EXISTS(SELECT 1 FROM review_packages prior WHERE prior.package_id=NEW.package_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_review_records_insert AFTER INSERT ON review_records WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:review_records',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','review_records'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_review_records_update AFTER UPDATE ON review_records WHEN (NEW.record_id IS NOT OLD.record_id OR NEW.package_id IS NOT OLD.package_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.purpose IS NOT OLD.purpose OR NEW.reviewer_agent_id IS NOT OLD.reviewer_agent_id OR NEW.reviewer_turn_id IS NOT OLD.reviewer_turn_id OR NEW.verdict IS NOT OLD.verdict OR NEW.evidence_manifest_hash IS NOT OLD.evidence_manifest_hash OR NEW.official IS NOT OLD.official OR NEW.record_hash IS NOT OLD.record_hash OR NEW.record_json IS NOT OLD.record_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:review_records',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','review_records'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_review_records_delete AFTER DELETE ON review_records WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:review_records',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','review_records'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_review_records_relocation_0 BEFORE INSERT ON review_records
WHEN EXISTS(SELECT 1 FROM review_records prior WHERE prior.package_id=NEW.package_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;


CREATE TRIGGER assurance_source_review_records_relocation_1 BEFORE INSERT ON review_records
WHEN EXISTS(SELECT 1 FROM review_records prior WHERE prior.record_id=NEW.record_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_criterion_evaluations_insert AFTER INSERT ON criterion_evaluations WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:criterion_evaluations',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','criterion_evaluations'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_criterion_evaluations_update AFTER UPDATE ON criterion_evaluations WHEN (NEW.review_id IS NOT OLD.review_id OR NEW.criterion_id IS NOT OLD.criterion_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.package_id IS NOT OLD.package_id OR NEW.verdict IS NOT OLD.verdict OR NEW.check_execution IS NOT OLD.check_execution OR NEW.check_receipt_hash IS NOT OLD.check_receipt_hash OR NEW.outcome_json IS NOT OLD.outcome_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:criterion_evaluations',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','criterion_evaluations'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_criterion_evaluations_delete AFTER DELETE ON criterion_evaluations WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:criterion_evaluations',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','criterion_evaluations'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_criterion_evaluations_relocation_0 BEFORE INSERT ON criterion_evaluations
WHEN EXISTS(SELECT 1 FROM criterion_evaluations prior WHERE prior.review_id=NEW.review_id AND prior.criterion_id=NEW.criterion_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_acceptances_insert AFTER INSERT ON acceptances WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:acceptances',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','acceptances'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_acceptances_update AFTER UPDATE ON acceptances WHEN (NEW.acceptance_id IS NOT OLD.acceptance_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.task_id IS NOT OLD.task_id OR NEW.obligation_id IS NOT OLD.obligation_id OR NEW.review_record_id IS NOT OLD.review_record_id OR NEW.requirements_revision IS NOT OLD.requirements_revision OR NEW.contract_revision IS NOT OLD.contract_revision OR NEW.input_manifest_hash IS NOT OLD.input_manifest_hash OR NEW.validity IS NOT OLD.validity OR NEW.accepted_at_ms IS NOT OLD.accepted_at_ms OR NEW.content_hash IS NOT OLD.content_hash OR NEW.acceptance_json IS NOT OLD.acceptance_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:acceptances',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','acceptances'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_acceptances_delete AFTER DELETE ON acceptances WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:acceptances',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','acceptances'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_acceptances_relocation_0 BEFORE INSERT ON acceptances
WHEN EXISTS(SELECT 1 FROM acceptances prior WHERE prior.acceptance_id=NEW.acceptance_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;


CREATE TRIGGER assurance_source_acceptances_relocation_1 BEFORE INSERT ON acceptances
WHEN EXISTS(SELECT 1 FROM acceptances prior WHERE prior.review_record_id=NEW.review_record_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_goal_resolutions_insert AFTER INSERT ON goal_resolutions WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:goal_resolutions',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','goal_resolutions'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_goal_resolutions_update AFTER UPDATE ON goal_resolutions WHEN (NEW.resolution_id IS NOT OLD.resolution_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.obligation_id IS NOT OLD.obligation_id OR NEW.goal_task_id IS NOT OLD.goal_task_id OR NEW.requirements_version IS NOT OLD.requirements_version OR NEW.contract_revision IS NOT OLD.contract_revision OR NEW.method_instance_id IS NOT OLD.method_instance_id OR NEW.review_receipt_id IS NOT OLD.review_receipt_id OR NEW.verdict IS NOT OLD.verdict OR NEW.validity IS NOT OLD.validity OR NEW.adopted IS NOT OLD.adopted OR NEW.content_hash IS NOT OLD.content_hash OR NEW.resolution_json IS NOT OLD.resolution_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:goal_resolutions',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','goal_resolutions'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_goal_resolutions_delete AFTER DELETE ON goal_resolutions WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:goal_resolutions',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','goal_resolutions'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_goal_resolutions_relocation_0 BEFORE INSERT ON goal_resolutions
WHEN EXISTS(SELECT 1 FROM goal_resolutions prior WHERE prior.resolution_id=NEW.resolution_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_input_manifest_bindings_insert AFTER INSERT ON input_manifest_bindings WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:input_manifest_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','input_manifest_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_input_manifest_bindings_update AFTER UPDATE ON input_manifest_bindings WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.task_id IS NOT OLD.task_id OR NEW.manifest_hash IS NOT OLD.manifest_hash OR NEW.attempt_id IS NOT OLD.attempt_id OR NEW.request_id IS NOT OLD.request_id OR NEW.input_binding_revision IS NOT OLD.input_binding_revision OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:input_manifest_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','input_manifest_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_input_manifest_bindings_delete AFTER DELETE ON input_manifest_bindings WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:input_manifest_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','input_manifest_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_plan_revisions_insert AFTER INSERT ON plan_revisions WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:plan_revisions',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','plan_revisions'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_plan_revisions_update AFTER UPDATE ON plan_revisions WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.revision IS NOT OLD.revision OR NEW.state IS NOT OLD.state OR NEW.base_revision IS NOT OLD.base_revision OR NEW.snapshot_hash IS NOT OLD.snapshot_hash OR NEW.delta_id IS NOT OLD.delta_id OR NEW.read_set_json IS NOT OLD.read_set_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:plan_revisions',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','plan_revisions'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_plan_revisions_delete AFTER DELETE ON plan_revisions WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:plan_revisions',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','plan_revisions'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_plan_memberships_insert AFTER INSERT ON plan_memberships WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:plan_memberships',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','plan_memberships'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_plan_memberships_update AFTER UPDATE ON plan_memberships WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.revision IS NOT OLD.revision OR NEW.occurrence_id IS NOT OLD.occurrence_id OR NEW.task_id IS NOT OLD.task_id OR NEW.obligation_id IS NOT OLD.obligation_id OR NEW.instance_id IS NOT OLD.instance_id OR NEW.form IS NOT OLD.form OR NEW.requiredness IS NOT OLD.requiredness OR NEW.adopted IS NOT OLD.adopted OR NEW.member_json IS NOT OLD.member_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:plan_memberships',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','plan_memberships'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_plan_memberships_delete AFTER DELETE ON plan_memberships WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:plan_memberships',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','plan_memberships'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_method_child_occurrences_insert AFTER INSERT ON method_child_occurrences WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:method_child_occurrences',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','method_child_occurrences'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_method_child_occurrences_update AFTER UPDATE ON method_child_occurrences WHEN (NEW.instance_id IS NOT OLD.instance_id OR NEW.slot_key IS NOT OLD.slot_key OR NEW.mission_id IS NOT OLD.mission_id OR NEW.occurrence_id IS NOT OLD.occurrence_id OR NEW.obligation_id IS NOT OLD.obligation_id OR NEW.goal_occurrence_id IS NOT OLD.goal_occurrence_id OR NEW.requiredness IS NOT OLD.requiredness OR NEW.reuse_policy IS NOT OLD.reuse_policy OR NEW.binding_json IS NOT OLD.binding_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:method_child_occurrences',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','method_child_occurrences'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_method_child_occurrences_delete AFTER DELETE ON method_child_occurrences WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:method_child_occurrences',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','method_child_occurrences'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_method_child_occurrences_relocation_0 BEFORE INSERT ON method_child_occurrences
WHEN EXISTS(SELECT 1 FROM method_child_occurrences prior WHERE prior.instance_id=NEW.instance_id AND prior.slot_key=NEW.slot_key
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_order_constraints_insert AFTER INSERT ON order_constraints WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:order_constraints',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','order_constraints'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_order_constraints_update AFTER UPDATE ON order_constraints WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.plan_revision IS NOT OLD.plan_revision OR NEW.before_occurrence IS NOT OLD.before_occurrence OR NEW.after_occurrence IS NOT OLD.after_occurrence OR NEW.release_condition IS NOT OLD.release_condition OR NEW.constraint_json IS NOT OLD.constraint_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:order_constraints',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','order_constraints'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_order_constraints_delete AFTER DELETE ON order_constraints WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:order_constraints',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','order_constraints'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_data_requirements_insert AFTER INSERT ON data_requirements WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:data_requirements',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','data_requirements'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_data_requirements_update AFTER UPDATE ON data_requirements WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.plan_revision IS NOT OLD.plan_revision OR NEW.requirement_id IS NOT OLD.requirement_id OR NEW.producer_occurrence IS NOT OLD.producer_occurrence OR NEW.output_port IS NOT OLD.output_port OR NEW.consumer_occurrence IS NOT OLD.consumer_occurrence OR NEW.input_port IS NOT OLD.input_port OR NEW.source_revision_policy IS NOT OLD.source_revision_policy OR NEW.requirement_json IS NOT OLD.requirement_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:data_requirements',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','data_requirements'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_data_requirements_delete AFTER DELETE ON data_requirements WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:data_requirements',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','data_requirements'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_bound_inputs_insert AFTER INSERT ON bound_inputs WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:bound_inputs',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','bound_inputs'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_bound_inputs_update AFTER UPDATE ON bound_inputs WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.plan_revision IS NOT OLD.plan_revision OR NEW.requirement_id IS NOT OLD.requirement_id OR NEW.input_binding_revision IS NOT OLD.input_binding_revision OR NEW.producer_result_id IS NOT OLD.producer_result_id OR NEW.acceptance_id IS NOT OLD.acceptance_id OR NEW.artifact_id IS NOT OLD.artifact_id OR NEW.content_hash IS NOT OLD.content_hash OR NEW.source_revision IS NOT OLD.source_revision OR NEW.binding_json IS NOT OLD.binding_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:bound_inputs',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','bound_inputs'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_bound_inputs_delete AFTER DELETE ON bound_inputs WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:bound_inputs',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','bound_inputs'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_obligations_insert AFTER INSERT ON obligations WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:obligations',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','obligations'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_obligations_update AFTER UPDATE ON obligations WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.obligation_id IS NOT OLD.obligation_id OR NEW.goal_signature_id IS NOT OLD.goal_signature_id OR NEW.scope IS NOT OLD.scope OR NEW.requiredness IS NOT OLD.requiredness OR NEW.lifecycle IS NOT OLD.lifecycle OR NEW.resolution_ref IS NOT OLD.resolution_ref OR NEW.parent_obligation_id IS NOT OLD.parent_obligation_id OR NEW.budget_lineage_ref IS NOT OLD.budget_lineage_ref OR NEW.failure_count IS NOT OLD.failure_count OR NEW.spent_tokens IS NOT OLD.spent_tokens OR NEW.spent_cost_micros IS NOT OLD.spent_cost_micros OR NEW.spent_attempts IS NOT OLD.spent_attempts OR NEW.fuel_limit IS NOT OLD.fuel_limit OR NEW.fuel_used IS NOT OLD.fuel_used OR NEW.fuel_remaining IS NOT OLD.fuel_remaining OR NEW.demand_admitted IS NOT OLD.demand_admitted OR NEW.obligation_json IS NOT OLD.obligation_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:obligations',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','obligations'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_obligations_delete AFTER DELETE ON obligations WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:obligations',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','obligations'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_obligations_relocation_0 BEFORE INSERT ON obligations
WHEN EXISTS(SELECT 1 FROM obligations prior WHERE prior.obligation_id=NEW.obligation_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_obligation_relations_insert AFTER INSERT ON obligation_relations WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:obligation_relations',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','obligation_relations'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_obligation_relations_update AFTER UPDATE ON obligation_relations WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.parent_obligation_id IS NOT OLD.parent_obligation_id OR NEW.child_obligation_id IS NOT OLD.child_obligation_id OR NEW.kind IS NOT OLD.kind OR NEW.active_revision IS NOT OLD.active_revision OR NEW.detail_json IS NOT OLD.detail_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:obligation_relations',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','obligation_relations'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_obligation_relations_delete AFTER DELETE ON obligation_relations WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:obligation_relations',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','obligation_relations'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_identities_insert AFTER INSERT ON operation_identities WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_identities',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_identities'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_identities_update AFTER UPDATE ON operation_identities WHEN (NEW.operation_id IS NOT OLD.operation_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.request_hash IS NOT OLD.request_hash OR NEW.envelope_hash IS NOT OLD.envelope_hash OR NEW.envelope_json IS NOT OLD.envelope_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_identities',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_identities'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_identities_delete AFTER DELETE ON operation_identities WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_identities',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_identities'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_operation_identities_relocation_0 BEFORE INSERT ON operation_identities
WHEN EXISTS(SELECT 1 FROM operation_identities prior WHERE prior.operation_id=NEW.operation_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;


CREATE TRIGGER assurance_source_operation_identities_relocation_1 BEFORE INSERT ON operation_identities
WHEN EXISTS(SELECT 1 FROM operation_identities prior WHERE prior.operation_id=NEW.operation_id AND prior.request_hash=NEW.request_hash
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_operation_bindings_insert AFTER INSERT ON operation_bindings WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_bindings_update AFTER UPDATE ON operation_bindings WHEN (NEW.principal_id IS NOT OLD.principal_id OR NEW.scope_id IS NOT OLD.scope_id OR NEW.operation_occurrence_id IS NOT OLD.operation_occurrence_id OR NEW.operation_id IS NOT OLD.operation_id OR NEW.request_hash IS NOT OLD.request_hash OR NEW.mission_id IS NOT OLD.mission_id OR NEW.obligation_id IS NOT OLD.obligation_id OR NEW.connector_id IS NOT OLD.connector_id OR NEW.connector_version IS NOT OLD.connector_version OR NEW.operation_kind IS NOT OLD.operation_kind OR NEW.envelope_hash IS NOT OLD.envelope_hash OR NEW.envelope_json IS NOT OLD.envelope_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_bindings_delete AFTER DELETE ON operation_bindings WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_operation_bindings_relocation_0 BEFORE INSERT ON operation_bindings
WHEN EXISTS(SELECT 1 FROM operation_bindings prior WHERE prior.operation_occurrence_id=NEW.operation_occurrence_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;


CREATE TRIGGER assurance_source_operation_bindings_relocation_1 BEFORE INSERT ON operation_bindings
WHEN EXISTS(SELECT 1 FROM operation_bindings prior WHERE prior.principal_id=NEW.principal_id AND prior.scope_id=NEW.scope_id AND prior.operation_occurrence_id=NEW.operation_occurrence_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_operation_completion_specs_insert AFTER INSERT ON operation_completion_specs WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_completion_specs',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_completion_specs'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_completion_specs_update AFTER UPDATE ON operation_completion_specs WHEN (NEW.spec_id IS NOT OLD.spec_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.requirements_revision IS NOT OLD.requirements_revision OR NEW.requirements_hash IS NOT OLD.requirements_hash OR NEW.spec_hash IS NOT OLD.spec_hash OR NEW.document_json IS NOT OLD.document_json OR NEW.approval_receipt_id IS NOT OLD.approval_receipt_id OR NEW.created_at_ms IS NOT OLD.created_at_ms) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_completion_specs',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_completion_specs'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_completion_specs_delete AFTER DELETE ON operation_completion_specs WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_completion_specs',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_completion_specs'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_operation_completion_specs_relocation_0 BEFORE INSERT ON operation_completion_specs
WHEN EXISTS(SELECT 1 FROM operation_completion_specs prior WHERE prior.spec_id=NEW.spec_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_operation_completion_scopes_insert AFTER INSERT ON operation_completion_scopes WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_completion_scopes',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_completion_scopes'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_completion_scopes_update AFTER UPDATE ON operation_completion_scopes WHEN (NEW.scope_id IS NOT OLD.scope_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.plan_revision IS NOT OLD.plan_revision OR NEW.occurrence_id IS NOT OLD.occurrence_id OR NEW.task_id IS NOT OLD.task_id OR NEW.task_binding_revision IS NOT OLD.task_binding_revision OR NEW.task_contract_hash IS NOT OLD.task_contract_hash OR NEW.spec_id IS NOT OLD.spec_id OR NEW.spec_hash IS NOT OLD.spec_hash OR NEW.scope_hash IS NOT OLD.scope_hash OR NEW.document_json IS NOT OLD.document_json OR NEW.plan_receipt_id IS NOT OLD.plan_receipt_id OR NEW.created_at_ms IS NOT OLD.created_at_ms) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_completion_scopes',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_completion_scopes'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_completion_scopes_delete AFTER DELETE ON operation_completion_scopes WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_completion_scopes',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_completion_scopes'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_operation_completion_scopes_relocation_0 BEFORE INSERT ON operation_completion_scopes
WHEN EXISTS(SELECT 1 FROM operation_completion_scopes prior WHERE prior.scope_id=NEW.scope_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_criterion_assessments_insert AFTER INSERT ON criterion_assessments WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:criterion_assessments',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','criterion_assessments'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_criterion_assessments_update AFTER UPDATE ON criterion_assessments WHEN (NEW.receipt_id IS NOT OLD.receipt_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.task_id IS NOT OLD.task_id OR NEW.result_id IS NOT OLD.result_id OR NEW.claim_id IS NOT OLD.claim_id OR NEW.criterion_id IS NOT OLD.criterion_id OR NEW.json IS NOT OLD.json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:criterion_assessments',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','criterion_assessments'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_criterion_assessments_delete AFTER DELETE ON criterion_assessments WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:criterion_assessments',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','criterion_assessments'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_criterion_assessments_relocation_0 BEFORE INSERT ON criterion_assessments
WHEN EXISTS(SELECT 1 FROM criterion_assessments prior WHERE prior.receipt_id=NEW.receipt_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_results_insert AFTER INSERT ON results WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:results',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','results'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_results_update AFTER UPDATE ON results WHEN (NEW.result_id IS NOT OLD.result_id OR NEW.attempt_id IS NOT OLD.attempt_id OR NEW.task_id IS NOT OLD.task_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.turn_id IS NOT OLD.turn_id OR NEW.result_hash IS NOT OLD.result_hash OR NEW.verification_state IS NOT OLD.verification_state OR NEW.verdict IS NOT OLD.verdict OR NEW.json IS NOT OLD.json OR NEW.received_at IS NOT OLD.received_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:results',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','results'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_results_delete AFTER DELETE ON results WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:results',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','results'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_results_relocation_0 BEFORE INSERT ON results
WHEN EXISTS(SELECT 1 FROM results prior WHERE prior.attempt_id=NEW.attempt_id AND prior.turn_id=NEW.turn_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;


CREATE TRIGGER assurance_source_results_relocation_1 BEFORE INSERT ON results
WHEN EXISTS(SELECT 1 FROM results prior WHERE prior.result_id=NEW.result_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_actions_insert AFTER INSERT ON actions WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:actions',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','actions'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_actions_update AFTER UPDATE ON actions WHEN (NEW.action_key IS NOT OLD.action_key OR NEW.action_id IS NOT OLD.action_id OR NEW.version IS NOT OLD.version OR NEW.mission_id IS NOT OLD.mission_id OR NEW.state IS NOT OLD.state OR NEW.json IS NOT OLD.json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:actions',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','actions'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_actions_delete AFTER DELETE ON actions WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:actions',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','actions'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_actions_relocation_0 BEFORE INSERT ON actions
WHEN EXISTS(SELECT 1 FROM actions prior WHERE prior.action_id=NEW.action_id AND prior.version=NEW.version
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;


CREATE TRIGGER assurance_source_actions_relocation_1 BEFORE INSERT ON actions
WHEN EXISTS(SELECT 1 FROM actions prior WHERE prior.action_key=NEW.action_key
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_approvals_insert AFTER INSERT ON approvals WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:approvals',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','approvals'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_approvals_update AFTER UPDATE ON approvals WHEN (NEW.request_id IS NOT OLD.request_id OR NEW.kind IS NOT OLD.kind OR NEW.mission_id IS NOT OLD.mission_id OR NEW.subject_key IS NOT OLD.subject_key OR NEW.state IS NOT OLD.state OR NEW.version IS NOT OLD.version OR NEW.json IS NOT OLD.json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:approvals',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','approvals'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_approvals_delete AFTER DELETE ON approvals WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:approvals',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','approvals'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_approvals_relocation_0 BEFORE INSERT ON approvals
WHEN EXISTS(SELECT 1 FROM approvals prior WHERE prior.request_id=NEW.request_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_planning_lane_grants_insert AFTER INSERT ON planning_lane_grants WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:planning_lane_grants',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','planning_lane_grants'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_planning_lane_grants_update AFTER UPDATE ON planning_lane_grants WHEN (NEW.grant_id IS NOT OLD.grant_id OR NEW.revision IS NOT OLD.revision OR NEW.mission_id IS NOT OLD.mission_id OR NEW.tenant_id IS NOT OLD.tenant_id OR NEW.scope_id IS NOT OLD.scope_id OR NEW.grantee_id IS NOT OLD.grantee_id OR NEW.issuer_id IS NOT OLD.issuer_id OR NEW.issuer_command_id IS NOT OLD.issuer_command_id OR NEW.issuer_receipt_hash IS NOT OLD.issuer_receipt_hash OR NEW.active IS NOT OLD.active OR NEW.allowed_decisions_json IS NOT OLD.allowed_decisions_json OR NEW.policy_hash IS NOT OLD.policy_hash OR NEW.not_before_ms IS NOT OLD.not_before_ms OR NEW.expires_at_ms IS NOT OLD.expires_at_ms OR NEW.content_hash IS NOT OLD.content_hash OR NEW.grant_json IS NOT OLD.grant_json) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:planning_lane_grants',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','planning_lane_grants'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_planning_lane_grants_delete AFTER DELETE ON planning_lane_grants WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:planning_lane_grants',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','planning_lane_grants'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_planning_lane_grants_relocation_0 BEFORE INSERT ON planning_lane_grants
WHEN EXISTS(SELECT 1 FROM planning_lane_grants prior WHERE prior.grant_id=NEW.grant_id AND prior.revision=NEW.revision
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;


CREATE TRIGGER assurance_source_planning_lane_grants_relocation_1 BEFORE INSERT ON planning_lane_grants
WHEN EXISTS(SELECT 1 FROM planning_lane_grants prior WHERE prior.issuer_command_id=NEW.issuer_command_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_assurance_check_bindings_insert AFTER INSERT ON assurance_check_bindings WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:assurance_check_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','assurance_check_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_assurance_check_bindings_update AFTER UPDATE ON assurance_check_bindings WHEN (NEW.check_binding_id IS NOT OLD.check_binding_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.execution_ref_hash IS NOT OLD.execution_ref_hash OR NEW.check_spec_hash IS NOT OLD.check_spec_hash OR NEW.subject_hash IS NOT OLD.subject_hash OR NEW.assertion_key IS NOT OLD.assertion_key OR NEW.binding_hash IS NOT OLD.binding_hash OR NEW.binding_json IS NOT OLD.binding_json OR NEW.import_receipt_id IS NOT OLD.import_receipt_id OR NEW.created_at_ms IS NOT OLD.created_at_ms) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:assurance_check_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','assurance_check_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_assurance_check_bindings_delete AFTER DELETE ON assurance_check_bindings WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:assurance_check_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','assurance_check_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_assurance_check_bindings_relocation_0 BEFORE INSERT ON assurance_check_bindings
WHEN EXISTS(SELECT 1 FROM assurance_check_bindings prior WHERE prior.check_binding_id=NEW.check_binding_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_assurance_criterion_policies_insert AFTER INSERT ON assurance_criterion_policies WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:assurance_criterion_policies',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','assurance_criterion_policies'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_assurance_criterion_policies_update AFTER UPDATE ON assurance_criterion_policies WHEN (NEW.policy_id IS NOT OLD.policy_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.requirements_revision IS NOT OLD.requirements_revision OR NEW.requirements_hash IS NOT OLD.requirements_hash OR NEW.scope_hash IS NOT OLD.scope_hash OR NEW.policy_hash IS NOT OLD.policy_hash OR NEW.policy_json IS NOT OLD.policy_json OR NEW.approval_receipt_id IS NOT OLD.approval_receipt_id) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:assurance_criterion_policies',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','assurance_criterion_policies'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_assurance_criterion_policies_delete AFTER DELETE ON assurance_criterion_policies WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:assurance_criterion_policies',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','assurance_criterion_policies'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_assurance_criterion_policies_relocation_0 BEFORE INSERT ON assurance_criterion_policies
WHEN EXISTS(SELECT 1 FROM assurance_criterion_policies prior WHERE prior.policy_id=NEW.policy_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_assurance_review_record_bindings_insert AFTER INSERT ON assurance_review_record_bindings WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:assurance_review_record_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','assurance_review_record_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_assurance_review_record_bindings_update AFTER UPDATE ON assurance_review_record_bindings WHEN (NEW.record_id IS NOT OLD.record_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.review_key IS NOT OLD.review_key OR NEW.source_turn_ref_hash IS NOT OLD.source_turn_ref_hash OR NEW.raw_output_hash IS NOT OLD.raw_output_hash OR NEW.evidence_manifest_hash IS NOT OLD.evidence_manifest_hash OR NEW.binding_hash IS NOT OLD.binding_hash OR NEW.binding_json IS NOT OLD.binding_json OR NEW.import_receipt_id IS NOT OLD.import_receipt_id OR NEW.created_at_ms IS NOT OLD.created_at_ms) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:assurance_review_record_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','assurance_review_record_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_assurance_review_record_bindings_delete AFTER DELETE ON assurance_review_record_bindings WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:assurance_review_record_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','assurance_review_record_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_assurance_review_record_bindings_relocation_0 BEFORE INSERT ON assurance_review_record_bindings
WHEN EXISTS(SELECT 1 FROM assurance_review_record_bindings prior WHERE prior.record_id=NEW.record_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_mission_policies_insert AFTER INSERT ON mission_policies WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:mission_policies',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','mission_policies'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_mission_policies_update AFTER UPDATE ON mission_policies WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.version_id IS NOT OLD.version_id OR NEW.source IS NOT OLD.source OR NEW.provider_kind IS NOT OLD.provider_kind OR NEW.json IS NOT OLD.json OR NEW.bound_at IS NOT OLD.bound_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:mission_policies',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','mission_policies'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_mission_policies_delete AFTER DELETE ON mission_policies WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:mission_policies',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','mission_policies'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_planning_request_authority_bindings_insert AFTER INSERT ON planning_request_authority_bindings WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:planning_request_authority_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','planning_request_authority_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_planning_request_authority_bindings_update AFTER UPDATE ON planning_request_authority_bindings WHEN (NEW.request_id IS NOT OLD.request_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.tenant_id IS NOT OLD.tenant_id OR NEW.scope_id IS NOT OLD.scope_id OR NEW.planner_principal_id IS NOT OLD.planner_principal_id OR NEW.grant_id IS NOT OLD.grant_id OR NEW.grant_revision IS NOT OLD.grant_revision OR NEW.grant_hash IS NOT OLD.grant_hash OR NEW.binding_hash IS NOT OLD.binding_hash OR NEW.binding_json IS NOT OLD.binding_json) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:planning_request_authority_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','planning_request_authority_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_planning_request_authority_bindings_delete AFTER DELETE ON planning_request_authority_bindings WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:planning_request_authority_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','planning_request_authority_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_planning_request_authority_bindings_relocation_0 BEFORE INSERT ON planning_request_authority_bindings
WHEN EXISTS(SELECT 1 FROM planning_request_authority_bindings prior WHERE prior.request_id=NEW.request_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_planning_operation_action_links_insert AFTER INSERT ON planning_operation_action_links WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:planning_operation_action_links',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','planning_operation_action_links'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_planning_operation_action_links_update AFTER UPDATE ON planning_operation_action_links WHEN (NEW.operation_id IS NOT OLD.operation_id OR NEW.request_hash IS NOT OLD.request_hash OR NEW.operation_occurrence_id IS NOT OLD.operation_occurrence_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.envelope_hash IS NOT OLD.envelope_hash OR NEW.principal_id IS NOT OLD.principal_id OR NEW.scope_id IS NOT OLD.scope_id OR NEW.obligation_id IS NOT OLD.obligation_id OR NEW.producer_task_id IS NOT OLD.producer_task_id OR NEW.producer_htn_occurrence_id IS NOT OLD.producer_htn_occurrence_id OR NEW.producer_contract_revision IS NOT OLD.producer_contract_revision OR NEW.producer_plan_revision IS NOT OLD.producer_plan_revision OR NEW.action_key IS NOT OLD.action_key OR NEW.action_id IS NOT OLD.action_id OR NEW.action_version IS NOT OLD.action_version OR NEW.params_hash IS NOT OLD.params_hash OR NEW.idempotency_key IS NOT OLD.idempotency_key OR NEW.provenance_receipt_id IS NOT OLD.provenance_receipt_id OR NEW.link_hash IS NOT OLD.link_hash OR NEW.link_json IS NOT OLD.link_json) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:planning_operation_action_links',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','planning_operation_action_links'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_planning_operation_action_links_delete AFTER DELETE ON planning_operation_action_links WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:planning_operation_action_links',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','planning_operation_action_links'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_planning_operation_action_links_relocation_0 BEFORE INSERT ON planning_operation_action_links
WHEN EXISTS(SELECT 1 FROM planning_operation_action_links prior WHERE prior.action_key=NEW.action_key
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;


CREATE TRIGGER assurance_source_planning_operation_action_links_relocation_1 BEFORE INSERT ON planning_operation_action_links
WHEN EXISTS(SELECT 1 FROM planning_operation_action_links prior WHERE prior.operation_id=NEW.operation_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;


CREATE TRIGGER assurance_source_planning_operation_action_links_relocation_2 BEFORE INSERT ON planning_operation_action_links
WHEN EXISTS(SELECT 1 FROM planning_operation_action_links prior WHERE prior.operation_occurrence_id=NEW.operation_occurrence_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_operation_outcome_review_bindings_insert AFTER INSERT ON operation_outcome_review_bindings WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_outcome_review_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_outcome_review_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_outcome_review_bindings_update AFTER UPDATE ON operation_outcome_review_bindings WHEN (NEW.binding_id IS NOT OLD.binding_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.spec_id IS NOT OLD.spec_id OR NEW.spec_hash IS NOT OLD.spec_hash OR NEW.effect_key IS NOT OLD.effect_key OR NEW.completion_scope_id IS NOT OLD.completion_scope_id OR NEW.intent_id IS NOT OLD.intent_id OR NEW.operation_id IS NOT OLD.operation_id OR NEW.operation_occurrence_id IS NOT OLD.operation_occurrence_id OR NEW.action_key IS NOT OLD.action_key OR NEW.action_version IS NOT OLD.action_version OR NEW.request_hash IS NOT OLD.request_hash OR NEW.effect_contract_hash IS NOT OLD.effect_contract_hash OR NEW.source_manifest_hash IS NOT OLD.source_manifest_hash OR NEW.binding_hash IS NOT OLD.binding_hash OR NEW.document_json IS NOT OLD.document_json OR NEW.review_package_id IS NOT OLD.review_package_id OR NEW.producer_receipt_id IS NOT OLD.producer_receipt_id OR NEW.created_at_ms IS NOT OLD.created_at_ms) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_outcome_review_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_outcome_review_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_outcome_review_bindings_delete AFTER DELETE ON operation_outcome_review_bindings WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_outcome_review_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_outcome_review_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_operation_outcome_review_bindings_relocation_0 BEFORE INSERT ON operation_outcome_review_bindings
WHEN EXISTS(SELECT 1 FROM operation_outcome_review_bindings prior WHERE prior.binding_id=NEW.binding_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;


CREATE TRIGGER assurance_source_operation_outcome_review_bindings_relocation_1 BEFORE INSERT ON operation_outcome_review_bindings
WHEN EXISTS(SELECT 1 FROM operation_outcome_review_bindings prior WHERE prior.review_package_id=NEW.review_package_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_operation_acceptance_scopes_insert AFTER INSERT ON operation_acceptance_scopes WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_acceptance_scopes',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_acceptance_scopes'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_acceptance_scopes_update AFTER UPDATE ON operation_acceptance_scopes WHEN (NEW.acceptance_id IS NOT OLD.acceptance_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.completion_scope_id IS NOT OLD.completion_scope_id OR NEW.contribution_kind IS NOT OLD.contribution_kind OR NEW.scope_hash IS NOT OLD.scope_hash OR NEW.contribution_hash IS NOT OLD.contribution_hash OR NEW.document_json IS NOT OLD.document_json OR NEW.outcome_binding_id IS NOT OLD.outcome_binding_id OR NEW.delivery_receipt_id IS NOT OLD.delivery_receipt_id OR NEW.producer_receipt_id IS NOT OLD.producer_receipt_id OR NEW.created_at_ms IS NOT OLD.created_at_ms) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_acceptance_scopes',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_acceptance_scopes'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_acceptance_scopes_delete AFTER DELETE ON operation_acceptance_scopes WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_acceptance_scopes',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_acceptance_scopes'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_operation_acceptance_scopes_relocation_0 BEFORE INSERT ON operation_acceptance_scopes
WHEN EXISTS(SELECT 1 FROM operation_acceptance_scopes prior WHERE prior.acceptance_id=NEW.acceptance_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;


CREATE TRIGGER assurance_source_operation_acceptance_scopes_relocation_1 BEFORE INSERT ON operation_acceptance_scopes
WHEN EXISTS(SELECT 1 FROM operation_acceptance_scopes prior WHERE prior.outcome_binding_id=NEW.outcome_binding_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_operation_payload_objects_insert AFTER INSERT ON operation_payload_objects WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_payload_objects',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_payload_objects'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_payload_objects_update AFTER UPDATE ON operation_payload_objects WHEN (NEW.object_id IS NOT OLD.object_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.object_kind IS NOT OLD.object_kind OR NEW.object_revision IS NOT OLD.object_revision OR NEW.content_hash IS NOT OLD.content_hash OR NEW.byte_length IS NOT OLD.byte_length OR NEW.media_type IS NOT OLD.media_type OR NEW.storage_uri IS NOT OLD.storage_uri OR NEW.source_receipt_id IS NOT OLD.source_receipt_id OR NEW.created_at_ms IS NOT OLD.created_at_ms) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_payload_objects',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_payload_objects'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_payload_objects_delete AFTER DELETE ON operation_payload_objects WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_payload_objects',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_payload_objects'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_operation_payload_objects_relocation_0 BEFORE INSERT ON operation_payload_objects
WHEN EXISTS(SELECT 1 FROM operation_payload_objects prior WHERE prior.object_id=NEW.object_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_operation_intent_bindings_insert AFTER INSERT ON operation_intent_bindings WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_intent_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_intent_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_intent_bindings_update AFTER UPDATE ON operation_intent_bindings WHEN (NEW.intent_id IS NOT OLD.intent_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.tenant_id IS NOT OLD.tenant_id OR NEW.principal_id IS NOT OLD.principal_id OR NEW.scope_id IS NOT OLD.scope_id OR NEW.source_kind IS NOT OLD.source_kind OR NEW.origin_receipt_id IS NOT OLD.origin_receipt_id OR NEW.slot_key IS NOT OLD.slot_key OR NEW.submission_receipt_id IS NOT OLD.submission_receipt_id OR NEW.submission_hash IS NOT OLD.submission_hash OR NEW.supersedes_intent_id IS NOT OLD.supersedes_intent_id OR NEW.producer_task_id IS NOT OLD.producer_task_id OR NEW.producer_occurrence_id IS NOT OLD.producer_occurrence_id OR NEW.obligation_id IS NOT OLD.obligation_id OR NEW.source_result_id IS NOT OLD.source_result_id OR NEW.source_attempt_id IS NOT OLD.source_attempt_id OR NEW.candidate_artifact_id IS NOT OLD.candidate_artifact_id OR NEW.candidate_file_hash IS NOT OLD.candidate_file_hash OR NEW.task_contract_revision IS NOT OLD.task_contract_revision OR NEW.task_contract_hash IS NOT OLD.task_contract_hash OR NEW.requirements_revision IS NOT OLD.requirements_revision OR NEW.requirements_hash IS NOT OLD.requirements_hash OR NEW.plan_revision IS NOT OLD.plan_revision OR NEW.plan_snapshot_hash IS NOT OLD.plan_snapshot_hash OR NEW.parameters_object_id IS NOT OLD.parameters_object_id OR NEW.parameters_content_hash IS NOT OLD.parameters_content_hash OR NEW.params_hash IS NOT OLD.params_hash OR NEW.effect_object_id IS NOT OLD.effect_object_id OR NEW.effect_content_hash IS NOT OLD.effect_content_hash OR NEW.proposal_object_id IS NOT OLD.proposal_object_id OR NEW.proposal_content_hash IS NOT OLD.proposal_content_hash OR NEW.request_hash IS NOT OLD.request_hash OR NEW.review_package_id IS NOT OLD.review_package_id OR NEW.binding_json IS NOT OLD.binding_json OR NEW.created_at_ms IS NOT OLD.created_at_ms) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_intent_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_intent_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_operation_intent_bindings_delete AFTER DELETE ON operation_intent_bindings WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:operation_intent_bindings',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','operation_intent_bindings'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_operation_intent_bindings_relocation_0 BEFORE INSERT ON operation_intent_bindings
WHEN EXISTS(SELECT 1 FROM operation_intent_bindings prior WHERE prior.intent_id=NEW.intent_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;


CREATE TRIGGER assurance_source_operation_intent_bindings_relocation_1 BEFORE INSERT ON operation_intent_bindings
WHEN EXISTS(SELECT 1 FROM operation_intent_bindings prior WHERE prior.review_package_id=NEW.review_package_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;


CREATE TRIGGER assurance_source_operation_intent_bindings_relocation_2 BEFORE INSERT ON operation_intent_bindings
WHEN EXISTS(SELECT 1 FROM operation_intent_bindings prior WHERE prior.submission_receipt_id=NEW.submission_receipt_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_acceptance_commit_receipts_insert AFTER INSERT ON acceptance_commit_receipts WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:acceptance_commit_receipts',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','acceptance_commit_receipts'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_acceptance_commit_receipts_update AFTER UPDATE ON acceptance_commit_receipts WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.command_id IS NOT OLD.command_id OR NEW.kind IS NOT OLD.kind OR NEW.subject_id IS NOT OLD.subject_id OR NEW.intent_hash IS NOT OLD.intent_hash OR NEW.read_set_hash IS NOT OLD.read_set_hash OR NEW.event_id IS NOT OLD.event_id OR NEW.output_identity_json IS NOT OLD.output_identity_json OR NEW.detail_json IS NOT OLD.detail_json OR NEW.applied_at IS NOT OLD.applied_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:acceptance_commit_receipts',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','acceptance_commit_receipts'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_acceptance_commit_receipts_delete AFTER DELETE ON acceptance_commit_receipts WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:acceptance_commit_receipts',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','acceptance_commit_receipts'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_delivery_receipts_insert AFTER INSERT ON delivery_receipts WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:delivery_receipts',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','delivery_receipts'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_delivery_receipts_update AFTER UPDATE ON delivery_receipts WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.command_id IS NOT OLD.command_id OR NEW.receipt_id IS NOT OLD.receipt_id OR NEW.acceptance_id IS NOT OLD.acceptance_id OR NEW.stage IS NOT OLD.stage OR NEW.observed_at_ms IS NOT OLD.observed_at_ms OR NEW.operation_id IS NOT OLD.operation_id OR NEW.intent_hash IS NOT OLD.intent_hash OR NEW.receipt_json IS NOT OLD.receipt_json OR NEW.recorded_at IS NOT OLD.recorded_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:delivery_receipts',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','delivery_receipts'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_delivery_receipts_delete AFTER DELETE ON delivery_receipts WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:delivery_receipts',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','delivery_receipts'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_acceptance_outputs_insert AFTER INSERT ON acceptance_outputs WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:acceptance_outputs',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','acceptance_outputs'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_acceptance_outputs_update AFTER UPDATE ON acceptance_outputs WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.acceptance_id IS NOT OLD.acceptance_id OR NEW.output_port IS NOT OLD.output_port OR NEW.artifact_id IS NOT OLD.artifact_id OR NEW.producer_occurrence IS NOT OLD.producer_occurrence OR NEW.producer_task_ref IS NOT OLD.producer_task_ref OR NEW.producer_result_id IS NOT OLD.producer_result_id OR NEW.support_revision IS NOT OLD.support_revision OR NEW.content_hash IS NOT OLD.content_hash OR NEW.source_revision IS NOT OLD.source_revision OR NEW.output_json IS NOT OLD.output_json OR NEW.recorded_at IS NOT OLD.recorded_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:acceptance_outputs',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','acceptance_outputs'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_acceptance_outputs_delete AFTER DELETE ON acceptance_outputs WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:acceptance_outputs',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','acceptance_outputs'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_acceptance_outputs_relocation_0 BEFORE INSERT ON acceptance_outputs
WHEN EXISTS(SELECT 1 FROM acceptance_outputs prior WHERE prior.acceptance_id=NEW.acceptance_id AND prior.output_port=NEW.output_port AND prior.artifact_id=NEW.artifact_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_claims_insert AFTER INSERT ON claims WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:claims',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','claims'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_claims_update AFTER UPDATE ON claims WHEN (NEW.claim_id IS NOT OLD.claim_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.result_id IS NOT OLD.result_id OR NEW.status IS NOT OLD.status OR NEW.version IS NOT OLD.version OR NEW.json IS NOT OLD.json OR NEW.key IS NOT OLD.key) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:claims',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','claims'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_claims_delete AFTER DELETE ON claims WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:claims',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','claims'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_claims_relocation_0 BEFORE INSERT ON claims
WHEN EXISTS(SELECT 1 FROM claims prior WHERE prior.claim_id=NEW.claim_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

CREATE TRIGGER assurance_source_method_contracts_insert AFTER INSERT ON method_contracts WHEN 1 AND EXISTS(SELECT 1 FROM assurance_mission_bindings) BEGIN 
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assurance_environment_state WHERE singleton=1)
 THEN RAISE(ABORT,'ASSURANCE_ENVIRONMENT_UNINITIALIZED') END;
 SELECT CASE WHEN assurance_change_receipt() IS NULL
 THEN RAISE(ABORT,'SOURCE_CHANGE_RECEIPT_REQUIRED') END;
 UPDATE assurance_environment_state SET epoch=epoch+1,row_version=row_version+1,
  change_receipt_id=assurance_change_receipt() WHERE singleton=1;
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,b.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','GLOBAL','epoch',e.epoch,'source_receipt_id',e.change_receipt_id),
  CAST(strftime('%s','now') AS REAL),1
 FROM assurance_mission_bindings b CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1;
 END;
CREATE TRIGGER assurance_source_method_contracts_update AFTER UPDATE ON method_contracts WHEN (NEW.method_id IS NOT OLD.method_id OR NEW.method_version IS NOT OLD.method_version OR NEW.content_hash IS NOT OLD.content_hash OR NEW.registry_status IS NOT OLD.registry_status OR NEW.author IS NOT OLD.author OR NEW.trial_scope_mission IS NOT OLD.trial_scope_mission OR NEW.registration_json IS NOT OLD.registration_json OR NEW.contract_json IS NOT OLD.contract_json OR NEW.created_at IS NOT OLD.created_at) AND EXISTS(SELECT 1 FROM assurance_mission_bindings) BEGIN 
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assurance_environment_state WHERE singleton=1)
 THEN RAISE(ABORT,'ASSURANCE_ENVIRONMENT_UNINITIALIZED') END;
 SELECT CASE WHEN assurance_change_receipt() IS NULL
 THEN RAISE(ABORT,'SOURCE_CHANGE_RECEIPT_REQUIRED') END;
 UPDATE assurance_environment_state SET epoch=epoch+1,row_version=row_version+1,
  change_receipt_id=assurance_change_receipt() WHERE singleton=1;
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,b.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','GLOBAL','epoch',e.epoch,'source_receipt_id',e.change_receipt_id),
  CAST(strftime('%s','now') AS REAL),1
 FROM assurance_mission_bindings b CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1;
 END;
CREATE TRIGGER assurance_source_method_contracts_delete AFTER DELETE ON method_contracts WHEN 1 AND EXISTS(SELECT 1 FROM assurance_mission_bindings) BEGIN 
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assurance_environment_state WHERE singleton=1)
 THEN RAISE(ABORT,'ASSURANCE_ENVIRONMENT_UNINITIALIZED') END;
 SELECT CASE WHEN assurance_change_receipt() IS NULL
 THEN RAISE(ABORT,'SOURCE_CHANGE_RECEIPT_REQUIRED') END;
 UPDATE assurance_environment_state SET epoch=epoch+1,row_version=row_version+1,
  change_receipt_id=assurance_change_receipt() WHERE singleton=1;
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,b.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','GLOBAL','epoch',e.epoch,'source_receipt_id',e.change_receipt_id),
  CAST(strftime('%s','now') AS REAL),1
 FROM assurance_mission_bindings b CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1;
 END;
CREATE TRIGGER assurance_source_policy_versions_insert AFTER INSERT ON policy_versions WHEN 1 AND EXISTS(SELECT 1 FROM assurance_mission_bindings) BEGIN 
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assurance_environment_state WHERE singleton=1)
 THEN RAISE(ABORT,'ASSURANCE_ENVIRONMENT_UNINITIALIZED') END;
 SELECT CASE WHEN assurance_change_receipt() IS NULL
 THEN RAISE(ABORT,'SOURCE_CHANGE_RECEIPT_REQUIRED') END;
 UPDATE assurance_environment_state SET epoch=epoch+1,row_version=row_version+1,
  change_receipt_id=assurance_change_receipt() WHERE singleton=1;
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,b.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','GLOBAL','epoch',e.epoch,'source_receipt_id',e.change_receipt_id),
  CAST(strftime('%s','now') AS REAL),1
 FROM assurance_mission_bindings b CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1;
 END;
CREATE TRIGGER assurance_source_policy_versions_update AFTER UPDATE ON policy_versions WHEN (NEW.version_id IS NOT OLD.version_id OR NEW.params_hash IS NOT OLD.params_hash OR NEW.source IS NOT OLD.source OR NEW.status IS NOT OLD.status OR NEW.json IS NOT OLD.json OR NEW.created_at IS NOT OLD.created_at) AND EXISTS(SELECT 1 FROM assurance_mission_bindings) BEGIN 
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assurance_environment_state WHERE singleton=1)
 THEN RAISE(ABORT,'ASSURANCE_ENVIRONMENT_UNINITIALIZED') END;
 SELECT CASE WHEN assurance_change_receipt() IS NULL
 THEN RAISE(ABORT,'SOURCE_CHANGE_RECEIPT_REQUIRED') END;
 UPDATE assurance_environment_state SET epoch=epoch+1,row_version=row_version+1,
  change_receipt_id=assurance_change_receipt() WHERE singleton=1;
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,b.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','GLOBAL','epoch',e.epoch,'source_receipt_id',e.change_receipt_id),
  CAST(strftime('%s','now') AS REAL),1
 FROM assurance_mission_bindings b CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1;
 END;
CREATE TRIGGER assurance_source_policy_versions_delete AFTER DELETE ON policy_versions WHEN 1 AND EXISTS(SELECT 1 FROM assurance_mission_bindings) BEGIN 
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assurance_environment_state WHERE singleton=1)
 THEN RAISE(ABORT,'ASSURANCE_ENVIRONMENT_UNINITIALIZED') END;
 SELECT CASE WHEN assurance_change_receipt() IS NULL
 THEN RAISE(ABORT,'SOURCE_CHANGE_RECEIPT_REQUIRED') END;
 UPDATE assurance_environment_state SET epoch=epoch+1,row_version=row_version+1,
  change_receipt_id=assurance_change_receipt() WHERE singleton=1;
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,b.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','GLOBAL','epoch',e.epoch,'source_receipt_id',e.change_receipt_id),
  CAST(strftime('%s','now') AS REAL),1
 FROM assurance_mission_bindings b CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1;
 END;
CREATE TRIGGER assurance_source_policy_proposals_insert AFTER INSERT ON policy_proposals WHEN 1 AND EXISTS(SELECT 1 FROM assurance_mission_bindings) BEGIN 
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assurance_environment_state WHERE singleton=1)
 THEN RAISE(ABORT,'ASSURANCE_ENVIRONMENT_UNINITIALIZED') END;
 SELECT CASE WHEN assurance_change_receipt() IS NULL
 THEN RAISE(ABORT,'SOURCE_CHANGE_RECEIPT_REQUIRED') END;
 UPDATE assurance_environment_state SET epoch=epoch+1,row_version=row_version+1,
  change_receipt_id=assurance_change_receipt() WHERE singleton=1;
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,b.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','GLOBAL','epoch',e.epoch,'source_receipt_id',e.change_receipt_id),
  CAST(strftime('%s','now') AS REAL),1
 FROM assurance_mission_bindings b CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1;
 END;
CREATE TRIGGER assurance_source_policy_proposals_update AFTER UPDATE ON policy_proposals WHEN (NEW.proposal_id IS NOT OLD.proposal_id OR NEW.version_id IS NOT OLD.version_id OR NEW.state IS NOT OLD.state OR NEW.json IS NOT OLD.json OR NEW.created_at IS NOT OLD.created_at) AND EXISTS(SELECT 1 FROM assurance_mission_bindings) BEGIN 
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assurance_environment_state WHERE singleton=1)
 THEN RAISE(ABORT,'ASSURANCE_ENVIRONMENT_UNINITIALIZED') END;
 SELECT CASE WHEN assurance_change_receipt() IS NULL
 THEN RAISE(ABORT,'SOURCE_CHANGE_RECEIPT_REQUIRED') END;
 UPDATE assurance_environment_state SET epoch=epoch+1,row_version=row_version+1,
  change_receipt_id=assurance_change_receipt() WHERE singleton=1;
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,b.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','GLOBAL','epoch',e.epoch,'source_receipt_id',e.change_receipt_id),
  CAST(strftime('%s','now') AS REAL),1
 FROM assurance_mission_bindings b CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1;
 END;
CREATE TRIGGER assurance_source_policy_proposals_delete AFTER DELETE ON policy_proposals WHEN 1 AND EXISTS(SELECT 1 FROM assurance_mission_bindings) BEGIN 
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assurance_environment_state WHERE singleton=1)
 THEN RAISE(ABORT,'ASSURANCE_ENVIRONMENT_UNINITIALIZED') END;
 SELECT CASE WHEN assurance_change_receipt() IS NULL
 THEN RAISE(ABORT,'SOURCE_CHANGE_RECEIPT_REQUIRED') END;
 UPDATE assurance_environment_state SET epoch=epoch+1,row_version=row_version+1,
  change_receipt_id=assurance_change_receipt() WHERE singleton=1;
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,b.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','GLOBAL','epoch',e.epoch,'source_receipt_id',e.change_receipt_id),
  CAST(strftime('%s','now') AS REAL),1
 FROM assurance_mission_bindings b CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1;
 END;
CREATE TRIGGER assurance_source_policy_activations_insert AFTER INSERT ON policy_activations WHEN 1 AND EXISTS(SELECT 1 FROM assurance_mission_bindings) BEGIN 
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assurance_environment_state WHERE singleton=1)
 THEN RAISE(ABORT,'ASSURANCE_ENVIRONMENT_UNINITIALIZED') END;
 SELECT CASE WHEN assurance_change_receipt() IS NULL
 THEN RAISE(ABORT,'SOURCE_CHANGE_RECEIPT_REQUIRED') END;
 UPDATE assurance_environment_state SET epoch=epoch+1,row_version=row_version+1,
  change_receipt_id=assurance_change_receipt() WHERE singleton=1;
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,b.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','GLOBAL','epoch',e.epoch,'source_receipt_id',e.change_receipt_id),
  CAST(strftime('%s','now') AS REAL),1
 FROM assurance_mission_bindings b CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1;
 END;
CREATE TRIGGER assurance_source_policy_activations_update AFTER UPDATE ON policy_activations WHEN (NEW.seq IS NOT OLD.seq OR NEW.version_id IS NOT OLD.version_id OR NEW.action IS NOT OLD.action OR NEW.json IS NOT OLD.json OR NEW.created_at IS NOT OLD.created_at) AND EXISTS(SELECT 1 FROM assurance_mission_bindings) BEGIN 
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assurance_environment_state WHERE singleton=1)
 THEN RAISE(ABORT,'ASSURANCE_ENVIRONMENT_UNINITIALIZED') END;
 SELECT CASE WHEN assurance_change_receipt() IS NULL
 THEN RAISE(ABORT,'SOURCE_CHANGE_RECEIPT_REQUIRED') END;
 UPDATE assurance_environment_state SET epoch=epoch+1,row_version=row_version+1,
  change_receipt_id=assurance_change_receipt() WHERE singleton=1;
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,b.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','GLOBAL','epoch',e.epoch,'source_receipt_id',e.change_receipt_id),
  CAST(strftime('%s','now') AS REAL),1
 FROM assurance_mission_bindings b CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1;
 END;
CREATE TRIGGER assurance_source_policy_activations_delete AFTER DELETE ON policy_activations WHEN 1 AND EXISTS(SELECT 1 FROM assurance_mission_bindings) BEGIN 
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assurance_environment_state WHERE singleton=1)
 THEN RAISE(ABORT,'ASSURANCE_ENVIRONMENT_UNINITIALIZED') END;
 SELECT CASE WHEN assurance_change_receipt() IS NULL
 THEN RAISE(ABORT,'SOURCE_CHANGE_RECEIPT_REQUIRED') END;
 UPDATE assurance_environment_state SET epoch=epoch+1,row_version=row_version+1,
  change_receipt_id=assurance_change_receipt() WHERE singleton=1;
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-environment-epoch:'||b.mission_id||':'||e.epoch,b.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','GLOBAL','epoch',e.epoch,'source_receipt_id',e.change_receipt_id),
  CAST(strftime('%s','now') AS REAL),1
 FROM assurance_mission_bindings b CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1;
 END;
CREATE TRIGGER assurance_source_input_manifests_insert AFTER INSERT ON input_manifests WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (SELECT NEW.origin_mission_id UNION SELECT mission_id FROM input_manifest_bindings WHERE manifest_hash IN (NEW.manifest_hash))
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:input_manifests',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (SELECT NEW.origin_mission_id UNION SELECT mission_id FROM input_manifest_bindings WHERE manifest_hash IN (NEW.manifest_hash))
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','input_manifests'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (SELECT NEW.origin_mission_id UNION SELECT mission_id FROM input_manifest_bindings WHERE manifest_hash IN (NEW.manifest_hash));
 END;
CREATE TRIGGER assurance_source_input_manifests_update AFTER UPDATE ON input_manifests WHEN (NEW.manifest_hash IS NOT OLD.manifest_hash OR NEW.origin_mission_id IS NOT OLD.origin_mission_id OR NEW.manifest_json IS NOT OLD.manifest_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (SELECT NEW.origin_mission_id UNION SELECT OLD.origin_mission_id UNION SELECT mission_id FROM input_manifest_bindings WHERE manifest_hash IN (NEW.manifest_hash,OLD.manifest_hash))
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:input_manifests',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (SELECT NEW.origin_mission_id UNION SELECT OLD.origin_mission_id UNION SELECT mission_id FROM input_manifest_bindings WHERE manifest_hash IN (NEW.manifest_hash,OLD.manifest_hash))
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','input_manifests'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (SELECT NEW.origin_mission_id UNION SELECT OLD.origin_mission_id UNION SELECT mission_id FROM input_manifest_bindings WHERE manifest_hash IN (NEW.manifest_hash,OLD.manifest_hash));
 END;
CREATE TRIGGER assurance_source_input_manifests_delete AFTER DELETE ON input_manifests WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (SELECT OLD.origin_mission_id UNION SELECT mission_id FROM input_manifest_bindings WHERE manifest_hash IN (OLD.manifest_hash))
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:input_manifests',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (SELECT OLD.origin_mission_id UNION SELECT mission_id FROM input_manifest_bindings WHERE manifest_hash IN (OLD.manifest_hash))
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','input_manifests'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (SELECT OLD.origin_mission_id UNION SELECT mission_id FROM input_manifest_bindings WHERE manifest_hash IN (OLD.manifest_hash));
 END;
CREATE TRIGGER assurance_source_verifications_insert AFTER INSERT ON verifications WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (SELECT mission_id FROM results WHERE result_id IN (NEW.result_id))
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:verifications',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (SELECT mission_id FROM results WHERE result_id IN (NEW.result_id))
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','verifications'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (SELECT mission_id FROM results WHERE result_id IN (NEW.result_id));
 END;
CREATE TRIGGER assurance_source_verifications_update AFTER UPDATE ON verifications WHEN (NEW.verification_id IS NOT OLD.verification_id OR NEW.result_id IS NOT OLD.result_id OR NEW.attempt_id IS NOT OLD.attempt_id OR NEW.layer IS NOT OLD.layer OR NEW.status IS NOT OLD.status OR NEW.detail_json IS NOT OLD.detail_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (SELECT mission_id FROM results WHERE result_id IN (NEW.result_id,OLD.result_id))
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:verifications',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (SELECT mission_id FROM results WHERE result_id IN (NEW.result_id,OLD.result_id))
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','verifications'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (SELECT mission_id FROM results WHERE result_id IN (NEW.result_id,OLD.result_id));
 END;
CREATE TRIGGER assurance_source_verifications_delete AFTER DELETE ON verifications WHEN 1 BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (SELECT mission_id FROM results WHERE result_id IN (OLD.result_id))
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:verifications',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (SELECT mission_id FROM results WHERE result_id IN (OLD.result_id))
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','verifications'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (SELECT mission_id FROM results WHERE result_id IN (OLD.result_id));
 END;
CREATE TRIGGER assurance_source_validity_epochs_insert AFTER INSERT ON validity_epochs WHEN 1 AND NEW.scope_id<>'assurance:mission' BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:validity_epochs',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','validity_epochs'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_validity_epochs_update AFTER UPDATE ON validity_epochs WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.scope_id IS NOT OLD.scope_id OR NEW.epoch IS NOT OLD.epoch OR NEW.bumped_by IS NOT OLD.bumped_by) AND NEW.scope_id<>'assurance:mission' AND OLD.scope_id<>'assurance:mission' BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:validity_epochs',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','validity_epochs'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_validity_epochs_delete AFTER DELETE ON validity_epochs WHEN 1 AND OLD.scope_id<>'assurance:mission' BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:validity_epochs',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','validity_epochs'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_events_insert AFTER INSERT ON events WHEN 1 AND (NEW.type IN ('AssuranceCheckSpecRegistered','AssuranceEvidenceDisclosed','AssuranceExecutionImported','AssuranceLocalCheckFinished','AssuranceReservationLinked','AssuranceReviewTurnImported')) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:events',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','events'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id);
 END;
CREATE TRIGGER assurance_source_events_update AFTER UPDATE ON events WHEN (NEW.seq IS NOT OLD.seq OR NEW.event_id IS NOT OLD.event_id OR NEW.idempotency_key IS NOT OLD.idempotency_key OR NEW.type IS NOT OLD.type OR NEW.trace_id IS NOT OLD.trace_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.task_id IS NOT OLD.task_id OR NEW.attempt_id IS NOT OLD.attempt_id OR NEW.actor_type IS NOT OLD.actor_type OR NEW.actor_id IS NOT OLD.actor_id OR NEW.payload_json IS NOT OLD.payload_json OR NEW.created_at IS NOT OLD.created_at OR NEW.schema_version IS NOT OLD.schema_version) AND (NEW.type IN ('AssuranceCheckSpecRegistered','AssuranceEvidenceDisclosed','AssuranceExecutionImported','AssuranceLocalCheckFinished','AssuranceReservationLinked','AssuranceReviewTurnImported') OR OLD.type IN ('AssuranceCheckSpecRegistered','AssuranceEvidenceDisclosed','AssuranceExecutionImported','AssuranceLocalCheckFinished','AssuranceReservationLinked','AssuranceReviewTurnImported')) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (NEW.mission_id,OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:events',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (NEW.mission_id,OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','events'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (NEW.mission_id,OLD.mission_id);
 END;
CREATE TRIGGER assurance_source_events_delete AFTER DELETE ON events WHEN 1 AND (OLD.type IN ('AssuranceCheckSpecRegistered','AssuranceEvidenceDisclosed','AssuranceExecutionImported','AssuranceLocalCheckFinished','AssuranceReservationLinked','AssuranceReviewTurnImported')) BEGIN 
 SELECT CASE WHEN EXISTS(
  SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id IN (OLD.mission_id)
   AND NOT EXISTS(SELECT 1 FROM validity_epochs e
     WHERE e.mission_id=b.mission_id AND e.scope_id='assurance:mission'))
 THEN RAISE(ABORT,'ASSURANCE_MISSION_EPOCH_UNINITIALIZED') END;
 UPDATE validity_epochs SET epoch=epoch+1,bumped_by='assurance-source:events',
  updated_at=CAST(strftime('%s','now') AS REAL)
 WHERE scope_id='assurance:mission' AND mission_id IN (OLD.mission_id)
 AND mission_id IN (SELECT mission_id FROM assurance_mission_bindings);
 INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,
  actor_type,actor_id,payload_json,created_at,schema_version)
 SELECT 'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,'AssuranceEvidenceChanged',
  'assurance-mission-epoch:'||e.mission_id||':'||e.epoch,e.mission_id,NULL,NULL,
  'system','assurance-source-v1',
  json_object('scope','MISSION','epoch',e.epoch,'source_table','events'),
  CAST(strftime('%s','now') AS REAL),1
 FROM validity_epochs e JOIN assurance_mission_bindings b ON b.mission_id=e.mission_id
 WHERE e.scope_id='assurance:mission' AND e.mission_id IN (OLD.mission_id);
 END;

CREATE TRIGGER assurance_source_events_relocation_0 BEFORE INSERT ON events
WHEN EXISTS(SELECT 1 FROM events prior WHERE prior.seq=NEW.seq
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;


CREATE TRIGGER assurance_source_events_relocation_1 BEFORE INSERT ON events
WHEN EXISTS(SELECT 1 FROM events prior WHERE prior.event_id=NEW.event_id
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;


CREATE TRIGGER assurance_source_events_relocation_2 BEFORE INSERT ON events
WHEN EXISTS(SELECT 1 FROM events prior WHERE prior.idempotency_key=NEW.idempotency_key
 AND prior.mission_id<>NEW.mission_id AND (
 EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=prior.mission_id)
 OR EXISTS(SELECT 1 FROM assurance_mission_bindings b WHERE b.mission_id=NEW.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;

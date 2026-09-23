-- ARP-EXEC-1.0: additive execution.db side bindings, not orchestrator.db.
-- Register through ORIGINAL execution UOW migration runner, next free descriptor.
-- Requires the actual base_agent_bindings_v1 / base_agent_turns_v1 keys.
-- No COMMIT/BEGIN/executescript: caller owns one migration transaction.
CREATE TABLE arp_profiles (
 profile_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
 body_hash TEXT NOT NULL CHECK(length(body_hash)=64), body_json TEXT NOT NULL CHECK(json_valid(body_json)),
 activation_receipt_json TEXT NOT NULL CHECK(json_valid(activation_receipt_json)),
 PRIMARY KEY(profile_id,revision), UNIQUE(profile_id,revision,body_hash)
) STRICT;
CREATE TABLE arp_agent_protocols (
 agent_id TEXT PRIMARY KEY NOT NULL REFERENCES base_agent_bindings_v1(agent_id),
 protocol TEXT NOT NULL CHECK(protocol IN ('LEGACY_AT_MIGRATION','LEGACY_EXPLICIT','ARP_V1')),
 creation_receipt_ref_json TEXT NOT NULL CHECK(json_valid(creation_receipt_ref_json)),
 marker_hash TEXT NOT NULL CHECK(length(marker_hash)=64),
 marked_at_ms INTEGER NOT NULL CHECK(marked_at_ms>=0)
) STRICT;
CREATE TABLE arp_agent_sessions (
 session_id TEXT PRIMARY KEY NOT NULL,
 agent_id TEXT NOT NULL REFERENCES base_agent_bindings_v1(agent_id),
 profile_id TEXT NOT NULL, profile_revision INTEGER NOT NULL, profile_hash TEXT NOT NULL,
 creation_root_id TEXT NOT NULL, root_incarnation TEXT NOT NULL,
 creation_key TEXT NOT NULL UNIQUE, create_command_hash TEXT NOT NULL CHECK(length(create_command_hash)=64),
 relative_directory TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN ('CREATING','ACTIVE','DRAINING','PURGING','PURGED','QUARANTINED')),
 generation INTEGER NOT NULL CHECK(generation>0), row_version INTEGER NOT NULL CHECK(row_version>0),
 journal_seq_from INTEGER NOT NULL CHECK(journal_seq_from>0),
 sealed_highwater INTEGER CHECK(sealed_highwater IS NULL OR sealed_highwater>=journal_seq_from-1),
 destroy_command_id TEXT UNIQUE, destroy_command_hash TEXT,
 delete_proof_ref_json TEXT CHECK(delete_proof_ref_json IS NULL OR json_valid(delete_proof_ref_json)),
 created_at_ms INTEGER NOT NULL CHECK(created_at_ms>=0), updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms>=created_at_ms),
 FOREIGN KEY(profile_id,profile_revision,profile_hash) REFERENCES arp_profiles(profile_id,revision,body_hash),
 UNIQUE(session_id,agent_id), CHECK((destroy_command_id IS NULL)=(destroy_command_hash IS NULL)),
 CHECK(state NOT IN ('PURGING','PURGED') OR (sealed_highwater IS NOT NULL AND delete_proof_ref_json IS NOT NULL))
) STRICT;
CREATE UNIQUE INDEX arp_one_live_session_per_agent ON arp_agent_sessions(agent_id) WHERE state!='PURGED';
CREATE TABLE arp_context_policy_adoptions (
 session_id TEXT NOT NULL REFERENCES arp_agent_sessions(session_id),
 adoption_revision INTEGER NOT NULL CHECK(adoption_revision>0),
 policy_ref_json TEXT NOT NULL CHECK(json_valid(policy_ref_json)),
 policy_body_hash TEXT NOT NULL CHECK(length(policy_body_hash)=64),
 command_id TEXT NOT NULL UNIQUE, command_hash TEXT NOT NULL CHECK(length(command_hash)=64),
 authority_receipt_ref_json TEXT NOT NULL CHECK(json_valid(authority_receipt_ref_json)),
 source_receipt_ref_json TEXT NOT NULL CHECK(json_valid(source_receipt_ref_json)),
 adopted_at_ms INTEGER NOT NULL CHECK(adopted_at_ms>=0),
 PRIMARY KEY(session_id,adoption_revision)
) STRICT;
CREATE TABLE arp_context_requests (
 context_id TEXT PRIMARY KEY NOT NULL,
 session_id TEXT NOT NULL, agent_id TEXT NOT NULL,
 turn_id TEXT NOT NULL REFERENCES base_agent_turns_v1(turn_id),
 provider_request_ordinal INTEGER NOT NULL CHECK(provider_request_ordinal>=0),
 session_generation INTEGER NOT NULL CHECK(session_generation>0), journal_highwater INTEGER NOT NULL CHECK(journal_highwater>=0),
 manifest_hash TEXT NOT NULL CHECK(length(manifest_hash)=64), manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)),
 planned_request_hash TEXT NOT NULL CHECK(length(planned_request_hash)=64),
 original_request_key TEXT NOT NULL UNIQUE,
 input_charge INTEGER NOT NULL CHECK(input_charge>=0), input_budget INTEGER NOT NULL CHECK(input_budget>=0),
 output_reserve INTEGER NOT NULL CHECK(output_reserve>0),
 catalog_epoch INTEGER NOT NULL CHECK(catalog_epoch>=0),
 source_read_set_json TEXT NOT NULL CHECK(json_valid(source_read_set_json)),
 created_at_ms INTEGER NOT NULL CHECK(created_at_ms>=0),
 UNIQUE(turn_id,provider_request_ordinal),
 FOREIGN KEY(session_id,agent_id) REFERENCES arp_agent_sessions(session_id,agent_id),
 CHECK(input_charge<=input_budget)
) STRICT;
CREATE TABLE arp_catalog_revisions (
 entry_kind TEXT NOT NULL CHECK(entry_kind IN ('CAPABILITY','PROVIDER','TOOL','SKILL','SCHEMA','WORKFLOW')),
 entry_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
 content_hash TEXT NOT NULL CHECK(length(content_hash)=64), body_json TEXT NOT NULL CHECK(json_valid(body_json)),
 source_receipt_ref_json TEXT NOT NULL CHECK(json_valid(source_receipt_ref_json)),
 bundle_root_ref_json TEXT CHECK(bundle_root_ref_json IS NULL OR json_valid(bundle_root_ref_json)),
 PRIMARY KEY(entry_kind,entry_id,revision), UNIQUE(entry_kind,entry_id,revision,content_hash)
) STRICT;
CREATE TABLE arp_catalog_activation (
 entry_kind TEXT NOT NULL,entry_id TEXT NOT NULL,revision INTEGER NOT NULL, content_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('QUARANTINED','TRIAL','ADMITTED','SUSPENDED','RETIRED')),
 row_version INTEGER NOT NULL CHECK(row_version>0),
 authority_ref_json TEXT NOT NULL CHECK(json_valid(authority_ref_json)),
 evaluation_ref_json TEXT CHECK(evaluation_ref_json IS NULL OR json_valid(evaluation_ref_json)),
 scope_ref_json TEXT NOT NULL CHECK(json_valid(scope_ref_json)),
 PRIMARY KEY(entry_kind,entry_id,revision),
 FOREIGN KEY(entry_kind,entry_id,revision,content_hash) REFERENCES arp_catalog_revisions(entry_kind,entry_id,revision,content_hash)
) STRICT;
CREATE TABLE arp_registry_epoch (
 namespace_id TEXT PRIMARY KEY NOT NULL,
 epoch INTEGER NOT NULL CHECK(epoch>=0), source_receipt_ref_json TEXT NOT NULL CHECK(json_valid(source_receipt_ref_json))
) STRICT;
CREATE TABLE arp_capability_bindings (
 binding_id TEXT PRIMARY KEY NOT NULL,
 session_id TEXT NOT NULL REFERENCES arp_agent_sessions(session_id),
 owner_contract_key TEXT NOT NULL, requirement_key TEXT NOT NULL,
 binding_hash TEXT NOT NULL CHECK(length(binding_hash)=64),body_json TEXT NOT NULL CHECK(json_valid(body_json)),
 source_receipt_ref_json TEXT NOT NULL CHECK(json_valid(source_receipt_ref_json)),
 UNIQUE(session_id,owner_contract_key,requirement_key,binding_hash)
) STRICT;
CREATE TABLE arp_tool_exposures (
 snapshot_id TEXT PRIMARY KEY NOT NULL, session_id TEXT NOT NULL REFERENCES arp_agent_sessions(session_id),
 generation INTEGER NOT NULL CHECK(generation>0), catalog_epoch INTEGER NOT NULL CHECK(catalog_epoch>=0),
 snapshot_hash TEXT NOT NULL CHECK(length(snapshot_hash)=64),body_json TEXT NOT NULL CHECK(json_valid(body_json)),
 source_receipt_ref_json TEXT NOT NULL CHECK(json_valid(source_receipt_ref_json)),
 UNIQUE(session_id,generation,snapshot_hash)
) STRICT;
CREATE TABLE arp_skill_uses (
 use_id TEXT PRIMARY KEY NOT NULL, session_id TEXT NOT NULL REFERENCES arp_agent_sessions(session_id),
 turn_id TEXT NOT NULL REFERENCES base_agent_turns_v1(turn_id),
 use_key TEXT NOT NULL, use_hash TEXT NOT NULL CHECK(length(use_hash)=64),body_json TEXT NOT NULL CHECK(json_valid(body_json)),
 original_call_ref_json TEXT CHECK(original_call_ref_json IS NULL OR json_valid(original_call_ref_json)),
 UNIQUE(session_id,use_key)
) STRICT;
CREATE TABLE arp_jobs (
 job_id TEXT PRIMARY KEY NOT NULL,session_id TEXT NOT NULL REFERENCES arp_agent_sessions(session_id),
 kind TEXT NOT NULL CHECK(kind IN ('INDEX','PURGE','BIND_IMPORT','SUMMARY')),
 semantic_key TEXT NOT NULL, payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
 generation INTEGER NOT NULL CHECK(generation>0),
 state TEXT NOT NULL CHECK(state IN ('PENDING','LEASED','DONE','BLOCKED','CANCELLED')),
 row_version INTEGER NOT NULL CHECK(row_version>0),attempts INTEGER NOT NULL CHECK(attempts>=0),
 next_at_ms INTEGER NOT NULL CHECK(next_at_ms>=0),deadline_ms INTEGER NOT NULL CHECK(deadline_ms>=next_at_ms),
 owner_id TEXT,lease_until_ms INTEGER,
 result_receipt_ref_json TEXT CHECK(result_receipt_ref_json IS NULL OR json_valid(result_receipt_ref_json)),
 UNIQUE(session_id,kind,semantic_key),
 CHECK((state='LEASED')=(owner_id IS NOT NULL AND lease_until_ms IS NOT NULL)),
 CHECK(state!='DONE' OR result_receipt_ref_json IS NOT NULL)
) STRICT;
CREATE INDEX arp_jobs_due ON arp_jobs(state,next_at_ms);
CREATE TABLE arp_blob_roots (
 root_key TEXT PRIMARY KEY NOT NULL,session_id TEXT NOT NULL REFERENCES arp_agent_sessions(session_id),
 object_ref_json TEXT NOT NULL CHECK(json_valid(object_ref_json)),
 purpose TEXT NOT NULL CHECK(purpose IN ('REQUEST','SKILL_USE','TRANSFER','AUDIT')),
 owner_ref_json TEXT NOT NULL CHECK(json_valid(owner_ref_json)),
 state TEXT NOT NULL CHECK(state IN ('LIVE','TRANSFERRED','RELEASED')),
 transfer_receipt_ref_json TEXT CHECK(transfer_receipt_ref_json IS NULL OR json_valid(transfer_receipt_ref_json)),
 row_version INTEGER NOT NULL CHECK(row_version>0),
 CHECK(state!='TRANSFERRED' OR transfer_receipt_ref_json IS NOT NULL)
) STRICT;
CREATE TRIGGER arp_profiles_update_deny BEFORE UPDATE ON arp_profiles BEGIN SELECT RAISE(ABORT,'immutable arp_profiles'); END;
CREATE TRIGGER arp_profiles_delete_deny BEFORE DELETE ON arp_profiles BEGIN SELECT RAISE(ABORT,'immutable arp_profiles'); END;
CREATE TRIGGER arp_context_requests_update_deny BEFORE UPDATE ON arp_context_requests BEGIN SELECT RAISE(ABORT,'immutable arp_context_requests'); END;
CREATE TRIGGER arp_context_requests_delete_deny BEFORE DELETE ON arp_context_requests BEGIN SELECT RAISE(ABORT,'immutable arp_context_requests'); END;
CREATE TRIGGER arp_catalog_revisions_update_deny BEFORE UPDATE ON arp_catalog_revisions BEGIN SELECT RAISE(ABORT,'immutable arp_catalog_revisions'); END;
CREATE TRIGGER arp_catalog_revisions_delete_deny BEFORE DELETE ON arp_catalog_revisions BEGIN SELECT RAISE(ABORT,'immutable arp_catalog_revisions'); END;
CREATE TRIGGER arp_capability_bindings_update_deny BEFORE UPDATE ON arp_capability_bindings BEGIN SELECT RAISE(ABORT,'immutable arp_capability_bindings'); END;
CREATE TRIGGER arp_capability_bindings_delete_deny BEFORE DELETE ON arp_capability_bindings BEGIN SELECT RAISE(ABORT,'immutable arp_capability_bindings'); END;
CREATE TRIGGER arp_tool_exposures_update_deny BEFORE UPDATE ON arp_tool_exposures BEGIN SELECT RAISE(ABORT,'immutable arp_tool_exposures'); END;
CREATE TRIGGER arp_tool_exposures_delete_deny BEFORE DELETE ON arp_tool_exposures BEGIN SELECT RAISE(ABORT,'immutable arp_tool_exposures'); END;
CREATE TRIGGER arp_skill_uses_update_deny BEFORE UPDATE ON arp_skill_uses BEGIN SELECT RAISE(ABORT,'immutable arp_skill_uses'); END;
CREATE TRIGGER arp_skill_uses_delete_deny BEFORE DELETE ON arp_skill_uses BEGIN SELECT RAISE(ABORT,'immutable arp_skill_uses'); END;

CREATE TRIGGER arp_session_initial BEFORE INSERT ON arp_agent_sessions
WHEN NEW.state!='CREATING' OR NEW.generation!=1 OR NEW.row_version!=1
BEGIN SELECT RAISE(ABORT,'session must start CREATING'); END;
CREATE TRIGGER arp_session_transition BEFORE UPDATE ON arp_agent_sessions
WHEN NEW.row_version!=OLD.row_version+1 OR NEW.generation<OLD.generation
 OR NEW.session_id!=OLD.session_id OR NEW.agent_id!=OLD.agent_id OR NEW.creation_key!=OLD.creation_key
 OR NEW.profile_hash!=OLD.profile_hash OR NEW.creation_root_id!=OLD.creation_root_id
 OR NEW.root_incarnation!=OLD.root_incarnation OR NEW.relative_directory!=OLD.relative_directory
 OR NEW.journal_seq_from!=OLD.journal_seq_from
 OR OLD.state='PURGED'
 OR (NEW.state!=OLD.state AND NOT (
  (OLD.state='CREATING' AND NEW.state IN ('ACTIVE','DRAINING','QUARANTINED')) OR
  (OLD.state='ACTIVE' AND NEW.state IN ('DRAINING','QUARANTINED')) OR
  (OLD.state='QUARANTINED' AND NEW.state IN ('ACTIVE','DRAINING')) OR
  (OLD.state='DRAINING' AND NEW.state='PURGING') OR
  (OLD.state='PURGING' AND NEW.state='PURGED')))
 OR (NEW.state IN ('DRAINING','QUARANTINED') AND NEW.state!=OLD.state AND NEW.generation!=OLD.generation+1)
BEGIN SELECT RAISE(ABORT,'invalid session change'); END;
CREATE TRIGGER arp_session_no_delete BEFORE DELETE ON arp_agent_sessions
BEGIN SELECT RAISE(ABORT,'session tombstone retained'); END;
CREATE TRIGGER arp_context_live_insert BEFORE INSERT ON arp_context_requests
WHEN NOT EXISTS(SELECT 1 FROM arp_agent_sessions s WHERE s.session_id=NEW.session_id
 AND s.agent_id=NEW.agent_id AND s.state='ACTIVE' AND s.generation=NEW.session_generation)
 OR NOT EXISTS(SELECT 1 FROM base_agent_turns_v1 t WHERE t.turn_id=NEW.turn_id AND t.agent_id=NEW.agent_id)
BEGIN SELECT RAISE(ABORT,'stale/wrong session or turn'); END;
CREATE TRIGGER arp_activation_initial BEFORE INSERT ON arp_catalog_activation
WHEN NEW.state!='QUARANTINED' OR NEW.row_version!=1
BEGIN SELECT RAISE(ABORT,'catalogue starts quarantined'); END;
CREATE TRIGGER arp_activation_transition BEFORE UPDATE ON arp_catalog_activation
WHEN NEW.row_version!=OLD.row_version+1 OR OLD.state='RETIRED'
 OR NEW.entry_kind!=OLD.entry_kind OR NEW.entry_id!=OLD.entry_id OR NEW.revision!=OLD.revision OR NEW.content_hash!=OLD.content_hash
 OR (NEW.state!=OLD.state AND NOT (
 (OLD.state='QUARANTINED' AND NEW.state IN ('TRIAL','RETIRED')) OR
 (OLD.state='TRIAL' AND NEW.state IN ('ADMITTED','SUSPENDED','RETIRED')) OR
 (OLD.state='ADMITTED' AND NEW.state IN ('SUSPENDED','RETIRED')) OR
 (OLD.state='SUSPENDED' AND NEW.state IN ('TRIAL','ADMITTED','RETIRED'))))
 OR (NEW.entry_kind='SKILL' AND NEW.state='ADMITTED' AND NEW.evaluation_ref_json IS NULL)
BEGIN SELECT RAISE(ABORT,'invalid catalogue lifecycle'); END;
CREATE TRIGGER arp_activation_no_delete BEFORE DELETE ON arp_catalog_activation
BEGIN SELECT RAISE(ABORT,'catalogue lifecycle retained'); END;
CREATE TRIGGER arp_registry_monotone BEFORE UPDATE ON arp_registry_epoch
WHEN NEW.namespace_id!=OLD.namespace_id OR NEW.epoch!=OLD.epoch+1
BEGIN SELECT RAISE(ABORT,'epoch must advance exactly once'); END;
CREATE TRIGGER arp_registry_no_delete BEFORE DELETE ON arp_registry_epoch
BEGIN SELECT RAISE(ABORT,'registry epoch retained'); END;
CREATE TRIGGER arp_job_initial BEFORE INSERT ON arp_jobs
WHEN NEW.state!='PENDING' OR NEW.row_version!=1 OR NEW.attempts!=0
BEGIN SELECT RAISE(ABORT,'job starts pending'); END;
CREATE TRIGGER arp_job_transition BEFORE UPDATE ON arp_jobs
WHEN NEW.row_version!=OLD.row_version+1 OR NEW.attempts<OLD.attempts
 OR OLD.state IN ('DONE','CANCELLED')
 OR NEW.session_id!=OLD.session_id OR NEW.kind!=OLD.kind OR NEW.semantic_key!=OLD.semantic_key
 OR NEW.payload_hash!=OLD.payload_hash OR NEW.generation!=OLD.generation
 OR (NEW.state!=OLD.state AND NOT (
 (OLD.state='PENDING' AND NEW.state IN ('LEASED','BLOCKED','CANCELLED')) OR
 (OLD.state='LEASED' AND NEW.state IN ('PENDING','DONE','BLOCKED','CANCELLED')) OR
 (OLD.state='BLOCKED' AND NEW.state IN ('PENDING','CANCELLED'))))
BEGIN SELECT RAISE(ABORT,'invalid job state'); END;
CREATE TRIGGER arp_job_no_delete BEFORE DELETE ON arp_jobs
BEGIN SELECT RAISE(ABORT,'job receipt retained'); END;
CREATE TRIGGER arp_pin_initial BEFORE INSERT ON arp_blob_roots
WHEN NEW.state!='LIVE' OR NEW.row_version!=1
BEGIN SELECT RAISE(ABORT,'root starts live'); END;
CREATE TRIGGER arp_pin_transition BEFORE UPDATE ON arp_blob_roots
WHEN NEW.row_version!=OLD.row_version+1 OR OLD.state!='LIVE'
 OR NEW.state NOT IN ('TRANSFERRED','RELEASED') OR NEW.object_ref_json!=OLD.object_ref_json
 OR NEW.owner_ref_json!=OLD.owner_ref_json OR NEW.session_id!=OLD.session_id
BEGIN SELECT RAISE(ABORT,'invalid root release'); END;
CREATE TRIGGER arp_pin_no_delete BEFORE DELETE ON arp_blob_roots
BEGIN SELECT RAISE(ABORT,'root receipt retained'); END;

CREATE TRIGGER arp_protocol_update_deny BEFORE UPDATE ON arp_agent_protocols
BEGIN SELECT RAISE(ABORT,'immutable creation protocol'); END;
CREATE TRIGGER arp_protocol_delete_deny BEFORE DELETE ON arp_agent_protocols
BEGIN SELECT RAISE(ABORT,'creation protocol retained'); END;
CREATE TRIGGER arp_session_protocol BEFORE INSERT ON arp_agent_sessions
WHEN NOT EXISTS(SELECT 1 FROM arp_agent_protocols p WHERE p.agent_id=NEW.agent_id AND p.protocol='ARP_V1')
BEGIN SELECT RAISE(ABORT,'explicit ARP creation protocol required'); END;
CREATE TRIGGER arp_policy_adoption_sequence BEFORE INSERT ON arp_context_policy_adoptions
WHEN NEW.adoption_revision != COALESCE((SELECT MAX(adoption_revision)+1 FROM arp_context_policy_adoptions WHERE session_id=NEW.session_id),1)
 OR NOT EXISTS(SELECT 1 FROM arp_agent_sessions s WHERE s.session_id=NEW.session_id AND s.state IN ('CREATING','ACTIVE'))
BEGIN SELECT RAISE(ABORT,'policy adoption sequence/session state'); END;
CREATE TRIGGER arp_policy_adoption_update_deny BEFORE UPDATE ON arp_context_policy_adoptions
BEGIN SELECT RAISE(ABORT,'immutable policy adoption'); END;
CREATE TRIGGER arp_policy_adoption_delete_deny BEFORE DELETE ON arp_context_policy_adoptions
BEGIN SELECT RAISE(ABORT,'policy adoption retained'); END;

CREATE TRIGGER arp_session_identity BEFORE UPDATE ON arp_agent_sessions
WHEN NEW.session_id IS NOT OLD.session_id OR NEW.agent_id IS NOT OLD.agent_id
 OR NEW.profile_id IS NOT OLD.profile_id OR NEW.profile_revision IS NOT OLD.profile_revision OR NEW.profile_hash IS NOT OLD.profile_hash
 OR NEW.creation_root_id IS NOT OLD.creation_root_id OR NEW.root_incarnation IS NOT OLD.root_incarnation
 OR NEW.creation_key IS NOT OLD.creation_key OR NEW.create_command_hash IS NOT OLD.create_command_hash
 OR NEW.relative_directory IS NOT OLD.relative_directory OR NEW.journal_seq_from IS NOT OLD.journal_seq_from
 OR NEW.created_at_ms IS NOT OLD.created_at_ms
 OR (OLD.destroy_command_id IS NOT NULL AND (NEW.destroy_command_id IS NOT OLD.destroy_command_id OR NEW.destroy_command_hash IS NOT OLD.destroy_command_hash))
 OR (OLD.sealed_highwater IS NOT NULL AND NEW.sealed_highwater IS NOT OLD.sealed_highwater)
 OR (NEW.state=OLD.state AND NEW.generation!=OLD.generation)
BEGIN SELECT RAISE(ABORT,'session immutable identity/fence'); END;

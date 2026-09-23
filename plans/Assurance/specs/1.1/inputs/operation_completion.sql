-- V14-OP-COMPLETION-1.0. Four immutable side bindings, NOT execution/Task ledgers.
-- Register at the actual next unused migration using the original Store runner.
-- Preserve prior DDL/checksums. No executescript on a user's database.
-- Parent keys below must be verified against the dirty candidate's actual schema.
CREATE TABLE operation_completion_specs (
 spec_id TEXT PRIMARY KEY NOT NULL,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 requirements_revision INTEGER NOT NULL CHECK(requirements_revision>=0),
 requirements_hash TEXT NOT NULL CHECK(length(requirements_hash)=64),
 spec_hash TEXT NOT NULL CHECK(length(spec_hash)=64),
 document_json TEXT NOT NULL CHECK(json_valid(document_json) AND json_type(document_json)='object'),
 approval_receipt_id TEXT NOT NULL REFERENCES commit_receipts(commit_id) DEFERRABLE INITIALLY DEFERRED,
 created_at_ms INTEGER NOT NULL CHECK(created_at_ms>=0),
 UNIQUE(mission_id,requirements_revision),
 UNIQUE(mission_id,spec_id,spec_hash),
 FOREIGN KEY(mission_id,requirements_revision) REFERENCES requirements_revisions(mission_id,revision)
) STRICT;
CREATE TABLE operation_completion_scopes (
 scope_id TEXT PRIMARY KEY NOT NULL,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 plan_revision INTEGER NOT NULL CHECK(plan_revision>=0),
 occurrence_id TEXT NOT NULL,
 task_id TEXT NOT NULL REFERENCES tasks(task_id),
 contract_revision INTEGER NOT NULL CHECK(contract_revision>=0),
 contract_hash TEXT NOT NULL CHECK(length(contract_hash)=64),
 spec_id TEXT NOT NULL,
 spec_hash TEXT NOT NULL CHECK(length(spec_hash)=64),
 scope_hash TEXT NOT NULL CHECK(length(scope_hash)=64),
 document_json TEXT NOT NULL CHECK(json_valid(document_json) AND json_type(document_json)='object'),
 plan_receipt_id TEXT NOT NULL REFERENCES commit_receipts(commit_id) DEFERRABLE INITIALLY DEFERRED,
 created_at_ms INTEGER NOT NULL CHECK(created_at_ms>=0),
 UNIQUE(mission_id,plan_revision,occurrence_id),
 UNIQUE(mission_id,scope_id),
 FOREIGN KEY(mission_id,spec_id,spec_hash) REFERENCES operation_completion_specs(mission_id,spec_id,spec_hash),
 FOREIGN KEY(mission_id,plan_revision,occurrence_id) REFERENCES plan_memberships(mission_id,revision,occurrence_id),
 FOREIGN KEY(task_id,contract_revision) REFERENCES task_semantics(task_id,binding_revision)
) STRICT;
CREATE TABLE operation_outcome_review_bindings (
 binding_id TEXT PRIMARY KEY NOT NULL,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 spec_id TEXT NOT NULL,
 spec_hash TEXT NOT NULL CHECK(length(spec_hash)=64),
 effect_key TEXT NOT NULL CHECK(length(effect_key)>0),
 completion_scope_id TEXT NOT NULL,
 intent_id TEXT NOT NULL REFERENCES operation_intent_bindings(intent_id),
 operation_id TEXT NOT NULL,
 operation_occurrence_id TEXT NOT NULL,
 action_key TEXT NOT NULL,
 action_version INTEGER NOT NULL CHECK(action_version>=0),
 request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
 effect_contract_hash TEXT NOT NULL CHECK(length(effect_contract_hash)=64),
 source_manifest_hash TEXT NOT NULL CHECK(length(source_manifest_hash)=64),
 binding_hash TEXT NOT NULL CHECK(length(binding_hash)=64),
 document_json TEXT NOT NULL CHECK(json_valid(document_json) AND json_type(document_json)='object'),
 review_package_id TEXT NOT NULL UNIQUE REFERENCES review_packages(package_id) DEFERRABLE INITIALLY DEFERRED,
 producer_receipt_id TEXT NOT NULL REFERENCES commit_receipts(commit_id) DEFERRABLE INITIALLY DEFERRED,
 created_at_ms INTEGER NOT NULL CHECK(created_at_ms>=0),
 UNIQUE(mission_id,binding_id),
 UNIQUE(mission_id,intent_id,effect_key,source_manifest_hash),
 FOREIGN KEY(mission_id,spec_id,spec_hash) REFERENCES operation_completion_specs(mission_id,spec_id,spec_hash),
 FOREIGN KEY(mission_id,completion_scope_id) REFERENCES operation_completion_scopes(mission_id,scope_id)
) STRICT;
CREATE TABLE operation_acceptance_scopes (
 acceptance_id TEXT PRIMARY KEY NOT NULL REFERENCES acceptances(acceptance_id),
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 completion_scope_id TEXT NOT NULL,
 contribution_kind TEXT NOT NULL CHECK(contribution_kind IN ('CONTENT','PREPARATION','OPERATION_EFFECT')),
 scope_hash TEXT NOT NULL CHECK(length(scope_hash)=64),
 document_json TEXT NOT NULL CHECK(json_valid(document_json) AND json_type(document_json)='object'),
 outcome_binding_id TEXT,
 delivery_receipt_id TEXT,
 producer_receipt_id TEXT NOT NULL REFERENCES commit_receipts(commit_id) DEFERRABLE INITIALLY DEFERRED,
 created_at_ms INTEGER NOT NULL CHECK(created_at_ms>=0),
 UNIQUE(outcome_binding_id),
 UNIQUE(delivery_receipt_id),
 FOREIGN KEY(mission_id,completion_scope_id) REFERENCES operation_completion_scopes(mission_id,scope_id),
 FOREIGN KEY(mission_id,outcome_binding_id) REFERENCES operation_outcome_review_bindings(mission_id,binding_id),
 CHECK((contribution_kind='OPERATION_EFFECT' AND outcome_binding_id IS NOT NULL)
    OR (contribution_kind IN ('CONTENT','PREPARATION') AND outcome_binding_id IS NULL AND delivery_receipt_id IS NULL))
) STRICT;
CREATE INDEX operation_completion_scope_task_idx ON operation_completion_scopes(mission_id,task_id,plan_revision);
CREATE INDEX operation_outcome_slot_idx ON operation_outcome_review_bindings(mission_id,spec_id,effect_key);
CREATE INDEX operation_contribution_scope_idx ON operation_acceptance_scopes(mission_id,completion_scope_id,contribution_kind);
-- These records never store CURRENT/SATISFIED/APPLIED as a new status authority.
-- Runtime correction/supersession is through original source/Validity/Requirement revision mechanisms.
CREATE TRIGGER operation_completion_specs_immutable_update BEFORE UPDATE ON operation_completion_specs BEGIN SELECT RAISE(ABORT,'immutable completion spec'); END;
CREATE TRIGGER operation_completion_specs_immutable_delete BEFORE DELETE ON operation_completion_specs BEGIN SELECT RAISE(ABORT,'immutable completion spec'); END;
CREATE TRIGGER operation_completion_scopes_immutable_update BEFORE UPDATE ON operation_completion_scopes BEGIN SELECT RAISE(ABORT,'immutable completion scope'); END;
CREATE TRIGGER operation_completion_scopes_immutable_delete BEFORE DELETE ON operation_completion_scopes BEGIN SELECT RAISE(ABORT,'immutable completion scope'); END;
CREATE TRIGGER operation_outcome_bindings_immutable_update BEFORE UPDATE ON operation_outcome_review_bindings BEGIN SELECT RAISE(ABORT,'immutable outcome review input'); END;
CREATE TRIGGER operation_outcome_bindings_immutable_delete BEFORE DELETE ON operation_outcome_review_bindings BEGIN SELECT RAISE(ABORT,'immutable outcome review input'); END;
CREATE TRIGGER operation_acceptance_scopes_immutable_update BEFORE UPDATE ON operation_acceptance_scopes BEGIN SELECT RAISE(ABORT,'immutable acceptance scope'); END;
CREATE TRIGGER operation_acceptance_scopes_immutable_delete BEFORE DELETE ON operation_acceptance_scopes BEGIN SELECT RAISE(ABORT,'immutable acceptance scope'); END;

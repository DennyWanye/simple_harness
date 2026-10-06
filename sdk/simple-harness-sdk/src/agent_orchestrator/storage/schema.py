# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Orchestrator library DDL (``orchestrator.db``): Event Store + Current State Store (§16.3).

One frozen descriptor per package minor version.  Entities are stored as canonical
JSON documents next to the columns the orchestrator queries or guards (status,
version, lease, identity keys); the JSON is the record of truth for the entity,
the columns are indexes.  Never opened by the SDK; never shares a file with
``execution.db`` (plan D2).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..governance.budget_tail_schema import DDL as DDL_V13
from ..governance.mission_system_tail_schema import DDL as DDL_V15
from .acceptance_receipt_schema import DDL as DDL_V17
from .admission_seams_schema import DDL as DDL_V20
from .assurance_schema import DDL as DDL_V26
from .assurance_pin_object_schema import DDL as DDL_V27
from .fragment_schema import FRAGMENT_SCHEMA_SQL as DDL_V14
from .htn_schema import DDL as DDL_V16
from .planning_human_store import DDL as DDL_V24
from .planning_decision_schema import DDL as DDL_V19
from .operation_seams_schema import DDL as DDL_V21
from .operation_completion_schema import DDL as DDL_V22
from .validity_subject_schema import DDL as DDL_V18
from .taskgraph_schema import DDL as DDL_V25


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    ddl: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.ddl.encode("utf-8")).hexdigest()


DDL_V1 = """
CREATE TABLE orch_schema_migrations (
 version INTEGER PRIMARY KEY,
 name TEXT NOT NULL,
 checksum TEXT NOT NULL,
 applied_at REAL NOT NULL
) STRICT;

CREATE TABLE events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id TEXT NOT NULL UNIQUE,
 idempotency_key TEXT NOT NULL UNIQUE,
 type TEXT NOT NULL,
 trace_id TEXT NOT NULL,
 mission_id TEXT NOT NULL,
 task_id TEXT,
 attempt_id TEXT,
 actor_type TEXT NOT NULL,
 actor_id TEXT NOT NULL,
 payload_json TEXT NOT NULL,
 created_at REAL NOT NULL,
 schema_version INTEGER NOT NULL
) STRICT;
CREATE INDEX events_mission_idx ON events(mission_id, seq);

CREATE TABLE missions (
 mission_id TEXT PRIMARY KEY,
 tenant_id TEXT NOT NULL,
 idempotency_key TEXT NOT NULL,
 status TEXT NOT NULL,
 version INTEGER NOT NULL,
 spec_hash TEXT NOT NULL,
 json TEXT NOT NULL,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL,
 UNIQUE(tenant_id, idempotency_key)
) STRICT;

CREATE TABLE tasks (
 task_id TEXT PRIMARY KEY,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 ordinal INTEGER NOT NULL,
 status TEXT NOT NULL,
 version INTEGER NOT NULL,
 json TEXT NOT NULL,
 updated_at REAL NOT NULL,
 UNIQUE(mission_id, ordinal)
) STRICT;

CREATE TABLE attempts (
 attempt_id TEXT PRIMARY KEY,
 task_id TEXT NOT NULL REFERENCES tasks(task_id),
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 ordinal INTEGER NOT NULL,
 status TEXT NOT NULL,
 version INTEGER NOT NULL,
 lease_owner TEXT,
 lease_expires_at REAL,
 agent_id TEXT,
 turn_id TEXT,
 json TEXT NOT NULL,
 updated_at REAL NOT NULL,
 UNIQUE(task_id, ordinal)
) STRICT;
CREATE INDEX attempts_status_idx ON attempts(status, lease_expires_at);

CREATE TABLE dispatch_intents (
 intent_id TEXT PRIMARY KEY,
 kind TEXT NOT NULL,
 subject_id TEXT NOT NULL UNIQUE,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 state TEXT NOT NULL,
 version INTEGER NOT NULL,
 creation_key TEXT NOT NULL UNIQUE,
 input_id TEXT NOT NULL,
 input_hash TEXT NOT NULL,
 config_json TEXT NOT NULL,
 expected_turn_id TEXT,
 agent_id TEXT,
 receipt_json TEXT,
 lease_owner TEXT,
 lease_expires_at REAL,
 replays INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL
) STRICT;
CREATE INDEX dispatch_intents_state_idx ON dispatch_intents(state, created_at);

CREATE TABLE results (
 result_id TEXT PRIMARY KEY,
 attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
 task_id TEXT NOT NULL REFERENCES tasks(task_id),
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 turn_id TEXT NOT NULL,
 result_hash TEXT NOT NULL,
 verification_state TEXT NOT NULL,
 verdict TEXT,
 json TEXT NOT NULL,
 received_at REAL NOT NULL,
 updated_at REAL NOT NULL,
 UNIQUE(attempt_id, turn_id)
) STRICT;
CREATE INDEX results_verification_idx ON results(verification_state, received_at);

CREATE TABLE verifications (
 verification_id TEXT PRIMARY KEY,
 result_id TEXT NOT NULL REFERENCES results(result_id),
 attempt_id TEXT NOT NULL,
 layer TEXT NOT NULL,
 status TEXT NOT NULL,
 detail_json TEXT NOT NULL,
 created_at REAL NOT NULL,
 UNIQUE(result_id, layer)
) STRICT;

CREATE TABLE claims (
 claim_id TEXT PRIMARY KEY,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 result_id TEXT NOT NULL REFERENCES results(result_id),
 status TEXT NOT NULL,
 version INTEGER NOT NULL,
 json TEXT NOT NULL,
 updated_at REAL NOT NULL
) STRICT;

CREATE TABLE artifacts (
 artifact_id TEXT PRIMARY KEY,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 task_id TEXT NOT NULL,
 attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
 path TEXT NOT NULL,
 content_hash TEXT NOT NULL,
 version INTEGER NOT NULL,
 json TEXT NOT NULL,
 created_at REAL NOT NULL,
 UNIQUE(attempt_id, path, version)
) STRICT;

CREATE TABLE budget_accounts (
 account_id TEXT PRIMARY KEY,
 scope TEXT NOT NULL,
 parent_id TEXT,
 mission_id TEXT NOT NULL,
 limits_json TEXT NOT NULL,
 reserved_tokens INTEGER NOT NULL DEFAULT 0,
 settled_tokens INTEGER NOT NULL DEFAULT 0,
 reserved_cost_micros INTEGER NOT NULL DEFAULT 0,
 settled_cost_micros INTEGER NOT NULL DEFAULT 0,
 unpriced_settlements INTEGER NOT NULL DEFAULT 0,
 attempts_created INTEGER NOT NULL DEFAULT 0,
 version INTEGER NOT NULL,
 updated_at REAL NOT NULL
) STRICT;

CREATE TABLE budget_reservations (
 reservation_id TEXT PRIMARY KEY,
 account_id TEXT NOT NULL REFERENCES budget_accounts(account_id),
 mission_id TEXT NOT NULL,
 subject_id TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL,
 reserved_tokens INTEGER NOT NULL,
 reserved_cost_micros INTEGER NOT NULL,
 settled_tokens INTEGER,
 settled_cost_micros INTEGER,
 unpriced INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL
) STRICT;

CREATE TABLE imported_usage (
 usage_ref TEXT PRIMARY KEY,
 subject_id TEXT NOT NULL,
 mission_id TEXT NOT NULL,
 input_tokens INTEGER NOT NULL,
 output_tokens INTEGER NOT NULL,
 cost_micros INTEGER,
 unpriced INTEGER NOT NULL,
 unknown INTEGER NOT NULL DEFAULT 0,
 imported_at REAL NOT NULL
) STRICT;

CREATE TABLE commit_receipts (
 commit_id TEXT PRIMARY KEY,
 kind TEXT NOT NULL,
 subject_id TEXT NOT NULL,
 base_version INTEGER,
 proposal_hash TEXT NOT NULL,
 receipt_json TEXT NOT NULL,
 applied_at REAL NOT NULL
) STRICT;
"""


# Step 4 (D4-15): the Blackboard layers that are stored separately from the claims
# (Verified Knowledge, Summaries), the conflict ledger, a claim subject index and the
# (mission, path, version) artifact lineage guard (step-3 leftover L3-2).
DDL_V2 = """
CREATE TABLE knowledge (
 knowledge_id TEXT PRIMARY KEY,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 claim_id TEXT NOT NULL,
 key TEXT,
 status TEXT NOT NULL,
 version INTEGER NOT NULL,
 source_task TEXT NOT NULL,
 json TEXT NOT NULL,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL
) STRICT;
CREATE INDEX knowledge_mission_idx ON knowledge(mission_id, status, created_at);

CREATE TABLE summaries (
 summary_id TEXT PRIMARY KEY,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 scope TEXT NOT NULL,
 subject_id TEXT NOT NULL,
 version TEXT NOT NULL,
 json TEXT NOT NULL,
 created_at REAL NOT NULL,
 UNIQUE(mission_id, scope, subject_id)
) STRICT;

CREATE TABLE conflicts (
 conflict_id TEXT PRIMARY KEY,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 key TEXT NOT NULL,
 state TEXT NOT NULL,
 task_id TEXT,
 version INTEGER NOT NULL,
 json TEXT NOT NULL,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL
) STRICT;
CREATE INDEX conflicts_mission_idx ON conflicts(mission_id, state, key);

ALTER TABLE claims ADD COLUMN key TEXT;
CREATE INDEX claims_mission_key_idx ON claims(mission_id, key);

CREATE UNIQUE INDEX artifacts_lineage_idx ON artifacts(mission_id, path, version);
"""

# Step 5 (D5-14): the graph change ledger — every applied Task DAG change with its base
# and new version, basis and operations (the "v1 → v2, why" a user can read back).
DDL_V3 = """
CREATE TABLE graph_changes (
 change_id TEXT PRIMARY KEY,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 from_version INTEGER NOT NULL,
 to_version INTEGER NOT NULL,
 proposal_hash TEXT NOT NULL,
 json TEXT NOT NULL,
 created_at REAL NOT NULL,
 UNIQUE(mission_id, to_version)
) STRICT;
CREATE INDEX graph_changes_mission_idx ON graph_changes(mission_id, from_version);
"""

# Step 6 (D6-2 / D6-8 / D6-13): the scheduler's durable signals (backpressure state,
# runtime profile health) and the tool-call budget dimension (§18.1 "工具调用次数").
DDL_V4 = """
CREATE TABLE scheduler_state (
 key TEXT PRIMARY KEY,
 json TEXT NOT NULL,
 version INTEGER NOT NULL,
 updated_at REAL NOT NULL
) STRICT;
ALTER TABLE budget_accounts ADD COLUMN reserved_tool_calls INTEGER NOT NULL DEFAULT 0;
ALTER TABLE budget_accounts ADD COLUMN settled_tool_calls INTEGER NOT NULL DEFAULT 0;
ALTER TABLE budget_reservations ADD COLUMN reserved_tool_calls INTEGER NOT NULL DEFAULT 0;
ALTER TABLE budget_reservations ADD COLUMN settled_tool_calls INTEGER;
CREATE TABLE tool_calls (
 call_key TEXT PRIMARY KEY,
 subject_id TEXT NOT NULL,
 mission_id TEXT NOT NULL,
 tool TEXT NOT NULL,
 outcome TEXT NOT NULL,
 created_at REAL NOT NULL
) STRICT;
CREATE INDEX tool_calls_subject_idx ON tool_calls(subject_id, outcome);
"""

# Step 7 (D7-2 / D7-4 / D7-9): real actions and the people who decide about them — the
# action ledger (one row per business action version), approval / review requests, the
# decisions with their receipt hashes, and human overrides.
DDL_V5 = """
CREATE TABLE actions (
 action_key TEXT PRIMARY KEY,
 action_id TEXT NOT NULL,
 version INTEGER NOT NULL,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 state TEXT NOT NULL,
 json TEXT NOT NULL,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL,
 UNIQUE(action_id, version)
) STRICT;
CREATE INDEX actions_mission_idx ON actions(mission_id, state);
CREATE TABLE approvals (
 request_id TEXT PRIMARY KEY,
 kind TEXT NOT NULL,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 subject_key TEXT NOT NULL,
 state TEXT NOT NULL,
 version INTEGER NOT NULL,
 json TEXT NOT NULL,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL
) STRICT;
CREATE INDEX approvals_mission_idx ON approvals(mission_id, state);
CREATE TABLE approval_decisions (
 receipt_hash TEXT PRIMARY KEY,
 request_id TEXT NOT NULL REFERENCES approvals(request_id),
 principal_id TEXT NOT NULL,
 decision TEXT NOT NULL,
 nonce TEXT NOT NULL,
 json TEXT NOT NULL,
 created_at REAL NOT NULL,
 UNIQUE(request_id, nonce)
) STRICT;
CREATE TABLE human_overrides (
 override_id TEXT PRIMARY KEY,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 json TEXT NOT NULL,
 created_at REAL NOT NULL
) STRICT;
"""

# Step 9 (plan D9-2' / D9-3'): the policy registry — parameter versions (content-addressed,
# resolved), proposals with their provenance, evaluations, human decisions, the ordered
# activation log, and the version every Mission is bound to.  Written by the Commit
# Service's policy half only.
DDL_V6 = """
CREATE TABLE policy_versions (
 version_id TEXT PRIMARY KEY,
 params_hash TEXT NOT NULL UNIQUE,
 source TEXT NOT NULL,
 status TEXT NOT NULL,
 json TEXT NOT NULL,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL
) STRICT;
CREATE TABLE policy_proposals (
 proposal_id TEXT PRIMARY KEY,
 version_id TEXT NOT NULL REFERENCES policy_versions(version_id),
 state TEXT NOT NULL,
 json TEXT NOT NULL,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL
) STRICT;
CREATE TABLE policy_evaluations (
 evaluation_id TEXT PRIMARY KEY,
 proposal_id TEXT NOT NULL REFERENCES policy_proposals(proposal_id),
 verdict TEXT NOT NULL,
 json TEXT NOT NULL,
 created_at REAL NOT NULL
) STRICT;
CREATE TABLE policy_decisions (
 receipt_hash TEXT PRIMARY KEY,
 proposal_id TEXT NOT NULL REFERENCES policy_proposals(proposal_id),
 principal_id TEXT NOT NULL,
 decision TEXT NOT NULL,
 nonce TEXT NOT NULL,
 json TEXT NOT NULL,
 created_at REAL NOT NULL,
 UNIQUE(proposal_id, nonce)
) STRICT;
CREATE TABLE policy_activations (
 seq INTEGER PRIMARY KEY AUTOINCREMENT,
 version_id TEXT NOT NULL REFERENCES policy_versions(version_id),
 action TEXT NOT NULL,
 json TEXT NOT NULL,
 created_at REAL NOT NULL
);
CREATE TABLE mission_policies (
 mission_id TEXT PRIMARY KEY REFERENCES missions(mission_id),
 version_id TEXT NOT NULL REFERENCES policy_versions(version_id),
 source TEXT NOT NULL,
 provider_kind TEXT NOT NULL,
 json TEXT NOT NULL,
 bound_at REAL NOT NULL
) STRICT;
CREATE INDEX mission_policies_version_idx ON mission_policies(version_id)
"""
LEGACY_POLICY_VERSION = "policy-legacy"  # Missions that predate policy binding (plan D9-3')
# P3.2 (plan D4, review round 2 P2-4): every workspace directory the system makes — an
# Attempt's tree, a verification copy, a judgment tree — with the identity a rebind is
# checked against (base_snapshot) and its lifecycle (CREATING → ACTIVE → CLEANED).
# Operational state, not a Mission fact: no event, not part of the replay projection.
DDL_V7 = """
CREATE TABLE workspaces (
 workspace_id TEXT PRIMARY KEY,
 kind TEXT NOT NULL,
 mission_id TEXT NOT NULL,
 attempt_id TEXT NOT NULL,
 base_snapshot TEXT NOT NULL,
 state TEXT NOT NULL,
 json TEXT NOT NULL,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL
) STRICT;
CREATE INDEX workspaces_mission_idx ON workspaces(mission_id, state)
"""

# P3.3 (plan v3 D1): the domain profile a Mission is frozen to, the same way
# ``mission_policies`` freezes its policy version.  The json column is a copy of the
# profile's content, not a pointer at the current registry: replay reads the copy.
DDL_V8 = """
CREATE TABLE mission_domains (
 mission_id TEXT PRIMARY KEY REFERENCES missions(mission_id),
 domain_id TEXT NOT NULL,
 domain_version TEXT NOT NULL,
 json TEXT NOT NULL,
 bound_at REAL NOT NULL
) STRICT;
CREATE INDEX mission_domains_domain_idx ON mission_domains(domain_id)
"""

# P3.3 D2/D7: immutable byte identity, mutable lifecycle metadata per historical
# version. revision fences ABA (A -> B -> A) while claims keep their original hash.
DDL_V9 = """
CREATE TABLE sources (
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 tenant_id TEXT NOT NULL,
 path TEXT NOT NULL,
 version_hash TEXT NOT NULL CHECK(length(version_hash) = 64),
 kind TEXT NOT NULL,
 trust TEXT NOT NULL CHECK(trust = 'untrusted_external'),
 registered_at REAL NOT NULL,
 superseded_by TEXT,
 revoked INTEGER NOT NULL CHECK(revoked IN (0,1)),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 PRIMARY KEY(mission_id, path, version_hash)
) STRICT;
CREATE UNIQUE INDEX sources_active_idx ON sources(mission_id, path)
 WHERE superseded_by IS NULL AND revoked = 0;
CREATE INDEX sources_mission_idx ON sources(mission_id, path, version_hash)
"""

# P3.3 C: accepted claim assessments are immutable review records, not new formal
# Mission state. Failed evaluations remain in verifications.detail_json.
DDL_V10 = """
CREATE TABLE criterion_assessments (
 receipt_id TEXT PRIMARY KEY,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 task_id TEXT NOT NULL REFERENCES tasks(task_id),
 result_id TEXT NOT NULL REFERENCES results(result_id),
 claim_id TEXT NOT NULL REFERENCES claims(claim_id),
 criterion_id TEXT NOT NULL,
 json TEXT NOT NULL,
 created_at REAL NOT NULL
) STRICT;
CREATE INDEX criterion_assessments_result_idx ON criterion_assessments(mission_id, result_id)
"""

DDL_V11 = """
CREATE TABLE provider_token_grants (
 invocation_id TEXT NOT NULL,
 handoff_ordinal INTEGER NOT NULL CHECK(handoff_ordinal>0),
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 subject_id TEXT NOT NULL REFERENCES budget_reservations(subject_id),
 agent_id TEXT NOT NULL,
 turn_id TEXT NOT NULL,
 intent_id TEXT NOT NULL REFERENCES dispatch_intents(intent_id),
 owner TEXT NOT NULL,
 sdk_owner TEXT NOT NULL,
 sdk_epoch INTEGER NOT NULL,
 fingerprint TEXT NOT NULL,
 request_hash TEXT NOT NULL,
 wire_hash TEXT NOT NULL,
 public_input_upper INTEGER NOT NULL CHECK(public_input_upper>=0),
 prior_output_upper INTEGER NOT NULL CHECK(prior_output_upper>=0),
 output_ceiling INTEGER NOT NULL CHECK(output_ceiling>0),
 total_upper INTEGER NOT NULL CHECK(total_upper>=output_ceiling),
 state TEXT NOT NULL CHECK(state IN
  ('RESERVED','HANDED_OFF','UNKNOWN','SETTLED','RELEASED','OVERRUN')),
 actual_tokens INTEGER CHECK(actual_tokens>=0),
 actual_output_tokens INTEGER CHECK(actual_output_tokens>=0),
 version INTEGER NOT NULL DEFAULT 1,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL,
 PRIMARY KEY(invocation_id,handoff_ordinal)
) STRICT;
CREATE INDEX provider_token_grants_subject_idx ON provider_token_grants(subject_id,state);
CREATE INDEX provider_token_grants_slots_idx ON provider_token_grants(state);
"""

DDL_V12 = """
CREATE TABLE search_bindings (
 mission_id TEXT PRIMARY KEY REFERENCES missions(mission_id), json TEXT NOT NULL
) STRICT;
CREATE TABLE selection_rounds (
 round_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id), version INTEGER NOT NULL,
 state TEXT NOT NULL, json TEXT NOT NULL
) STRICT;
CREATE TABLE selection_candidates (
 result_id TEXT PRIMARY KEY REFERENCES results(result_id), round_id TEXT NOT NULL
 REFERENCES selection_rounds(round_id), state TEXT NOT NULL, json TEXT NOT NULL
) STRICT;
"""

# 2026-09-26: the Assurance root check looks up the latest root receipt by kind
# on every orchestration cycle; without an index it scanned every commit receipt.
DDL_V28 = """
CREATE INDEX commit_receipts_kind_idx ON commit_receipts(kind);
"""

# NEXT-TG-1.0 §6.4: a Mission created under a deployment that runs every new
# Mission on the strict TaskGraph carries that requirement from its creation
# transaction.  Until the Mission is actually bound (taskgraph_policy_bindings), it
# may not dispatch a plan nor commit one — it waits, it never falls back.
DDL_V29 = """
CREATE TABLE taskgraph_requirements (
 mission_id TEXT PRIMARY KEY REFERENCES missions(mission_id),
 kernel_version TEXT NOT NULL,
 source TEXT NOT NULL,
 created_at REAL NOT NULL
) STRICT;
CREATE TRIGGER taskgraph_requirements_no_update BEFORE UPDATE ON taskgraph_requirements BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;
CREATE TRIGGER taskgraph_requirements_no_delete BEFORE DELETE ON taskgraph_requirements BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;
"""

# 2026-09-30（结构修复真机第 4 局）：结构修复会把相关步骤换代（输入版本号 +1）。换代后输入内容
# 与原来一字不差时，input_manifest_bindings 里同一步骤、同一份内容只有一行（首次绑定时的版本号），
# 新版本的尝试因"清单没绑到确切输入版本"开工失败。确切版本由 task_semantics 与逐次尝试记录保证；
# 这里只要求这一步在这一版或更早绑定过同一份内容。
DDL_V30 = """
DROP TRIGGER tg_attempt_identity_guard;
CREATE TRIGGER tg_attempt_identity_guard BEFORE INSERT ON taskgraph_attempt_inputs BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM attempts a JOIN dispatch_intents d ON d.subject_id=a.attempt_id
  JOIN plan_memberships m ON m.mission_id=a.mission_id AND m.task_id=a.task_id
  JOIN task_semantics s ON s.task_id=a.task_id AND s.binding_revision=NEW.binding_revision
  JOIN taskgraph_revision_records rr ON rr.mission_id=NEW.mission_id AND rr.revision=NEW.source_revision
  LEFT JOIN planning_admission_checks c ON c.check_id=NEW.admission_check_id
  LEFT JOIN planning_requests r ON r.request_id=c.request_id
  JOIN input_manifest_bindings b ON b.mission_id=a.mission_id AND b.task_id=a.task_id
   AND b.manifest_hash=NEW.manifest_hash AND b.input_binding_revision<=NEW.input_binding_revision
  WHERE a.attempt_id=NEW.attempt_id AND a.mission_id=NEW.mission_id AND a.task_id=NEW.task_id
   AND d.intent_id=NEW.intent_id AND d.mission_id=NEW.mission_id
   AND d.creation_key=NEW.creation_key AND d.input_id=NEW.input_id AND d.input_hash=NEW.frozen_input_hash
   AND m.revision=NEW.source_revision AND m.occurrence_id=NEW.occurrence_id AND m.form='primitive'
   AND s.mission_id=NEW.mission_id AND s.form='primitive'
   AND s.input_binding_revision=NEW.input_binding_revision AND s.dispatch_generation=NEW.dispatch_generation
   AND ((rr.source_kind='CAPTURED_BASELINE' AND rr.admission_check_id IS NULL AND NEW.admission_check_id IS NULL)
     OR (rr.source_kind<>'CAPTURED_BASELINE' AND rr.admission_check_id=NEW.admission_check_id
         AND r.mission_id=NEW.mission_id AND c.phase='APPLIED'))
 ) THEN RAISE(ABORT,'TG_ATTEMPT_IDENTITY_MISMATCH') END;
END;
"""

# 2026-10-02: candidate comparison (selection rounds) and fragment validation were
# removed.  Their tables are dropped by a new migration; migrations 12 and 14 keep their
# original text, because an existing library is opened by comparing every recorded
# migration's checksum.
DDL_V31 = """
DROP TABLE IF EXISTS selection_candidates;
DROP TABLE IF EXISTS selection_rounds;
DROP TABLE IF EXISTS search_bindings;
DROP INDEX IF EXISTS fragment_validations_mission;
DROP TABLE IF EXISTS fragment_validations;
"""

# 2026-10-02: conflict Tasks, the final synthesis Task and their Mission-level system
# pools were removed; a contradiction is now read from the claims themselves.  The graph
# change ledger lost its last writer (conflict Tasks).  Migrations 2, 3 and 15 keep their
# original text.
DDL_V32 = """
DROP INDEX IF EXISTS conflicts_mission_idx;
DROP TABLE IF EXISTS conflicts;
DROP INDEX IF EXISTS graph_changes_mission_idx;
DROP TABLE IF EXISTS graph_changes;
DROP TABLE IF EXISTS mission_system_tail_tasks;
DROP TABLE IF EXISTS mission_system_tail_pools;
"""

# 2026-10-02 (删旧平面模式第三刀第 5 步, strict citation option A): the document domain's
# per-criterion citation receipts lost their only writer.  Migration 26's import barrier
# is frozen as literal text, so its triggers on this table go with the table.
DDL_V33 = """
DROP INDEX IF EXISTS criterion_assessments_result_idx;
DROP TABLE IF EXISTS criterion_assessments;
"""

# 2026-10-03（HTN 补齐阶段 A）：删"老任务迁入执行图"（CAPTURED_BASELINE）。执行图在第一份计划之前
# 绑定，已有计划的任务不再迁入。SQLite 去掉表上的 CHECK 要重建整张表与其触发器，所以旧的 CHECK
# 文字留在迁移 25 里不动；两个守卫触发器去掉迁入分支并在库层拒绝再写这种来源，每条历史与每次
# 尝试的输入都必须指向一次真实的 APPLIED 计划准入。
DDL_V34 = """
DROP TRIGGER tg_revision_source_guard;
CREATE TRIGGER tg_revision_source_guard BEFORE INSERT ON taskgraph_revision_records BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM events e WHERE e.event_id=NEW.event_id AND e.mission_id=NEW.mission_id
 ) THEN RAISE(ABORT,'TG_REVISION_EVENT_MISMATCH') END;
 SELECT CASE WHEN NEW.source_kind NOT IN ('SEED_COMMIT','COMMIT')
  THEN RAISE(ABORT,'TG_REVISION_SOURCE_KIND_REMOVED') END;
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM planning_admission_checks c JOIN planning_requests r ON r.request_id=c.request_id
  WHERE c.check_id=NEW.admission_check_id AND c.phase='APPLIED' AND r.mission_id=NEW.mission_id
 ) THEN RAISE(ABORT,'TG_REVISION_ADMISSION_MISMATCH') END;
END;
DROP TRIGGER tg_attempt_identity_guard;
CREATE TRIGGER tg_attempt_identity_guard BEFORE INSERT ON taskgraph_attempt_inputs BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM attempts a JOIN dispatch_intents d ON d.subject_id=a.attempt_id
  JOIN plan_memberships m ON m.mission_id=a.mission_id AND m.task_id=a.task_id
  JOIN task_semantics s ON s.task_id=a.task_id AND s.binding_revision=NEW.binding_revision
  JOIN taskgraph_revision_records rr ON rr.mission_id=NEW.mission_id AND rr.revision=NEW.source_revision
  JOIN planning_admission_checks c ON c.check_id=NEW.admission_check_id
  JOIN planning_requests r ON r.request_id=c.request_id
  JOIN input_manifest_bindings b ON b.mission_id=a.mission_id AND b.task_id=a.task_id
   AND b.manifest_hash=NEW.manifest_hash AND b.input_binding_revision<=NEW.input_binding_revision
  WHERE a.attempt_id=NEW.attempt_id AND a.mission_id=NEW.mission_id AND a.task_id=NEW.task_id
   AND d.intent_id=NEW.intent_id AND d.mission_id=NEW.mission_id
   AND d.creation_key=NEW.creation_key AND d.input_id=NEW.input_id AND d.input_hash=NEW.frozen_input_hash
   AND m.revision=NEW.source_revision AND m.occurrence_id=NEW.occurrence_id AND m.form='primitive'
   AND s.mission_id=NEW.mission_id AND s.form='primitive'
   AND s.input_binding_revision=NEW.input_binding_revision AND s.dispatch_generation=NEW.dispatch_generation
   AND rr.admission_check_id=NEW.admission_check_id
   AND r.mission_id=NEW.mission_id AND c.phase='APPLIED'
 ) THEN RAISE(ABORT,'TG_ATTEMPT_IDENTITY_MISMATCH') END;
END;
"""

# 2026-10-03（HTN 补齐阶段 A′）：用户任务在建任务的同一事务里就绑定执行图，没有"已要求、
# 还没绑定"的等待期，等待机制连同这张表一起删除。
DDL_V35 = """
DROP TRIGGER IF EXISTS taskgraph_requirements_no_update;
DROP TRIGGER IF EXISTS taskgraph_requirements_no_delete;
DROP TABLE IF EXISTS taskgraph_requirements;
"""

# 2026-10-03: the deferred planning-repair continuation had no writer left once every
# Mission is TaskGraph-bound (a replacement converges through the TaskGraph instead).
DDL_V36 = """
DROP INDEX IF EXISTS planning_repair_due_idx;
DROP INDEX IF EXISTS planning_repair_mission_idx;
DROP TABLE IF EXISTS planning_repair_continuations;
"""

# 2026-10-03 (HTN 补齐阶段 A″, decision ⑤): the managed offline restore was removed, so
# the artifacts barrier no longer lets an "offline relocation" rewrite storage paths.
DDL_V37 = """
DROP TRIGGER IF EXISTS assurance_source_artifacts_update;
CREATE TRIGGER assurance_source_artifacts_update AFTER UPDATE ON artifacts WHEN (NEW.artifact_id IS NOT OLD.artifact_id OR NEW.mission_id IS NOT OLD.mission_id OR NEW.task_id IS NOT OLD.task_id OR NEW.attempt_id IS NOT OLD.attempt_id OR NEW.path IS NOT OLD.path OR NEW.content_hash IS NOT OLD.content_hash OR NEW.version IS NOT OLD.version OR NEW.json IS NOT OLD.json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
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
"""

# 2026-10-03 HTN 补齐阶段 C：编排只记 token，删掉 5 张表的 13 个金额列。触发器先删再删列，
# 然后照迁移 26 原文重建，只去掉引用 spent_cost_micros 的那一个子句（与迁移 37 的做法一样）。
DDL_V38 = """
ALTER TABLE budget_accounts DROP COLUMN reserved_cost_micros;
ALTER TABLE budget_accounts DROP COLUMN settled_cost_micros;
ALTER TABLE budget_accounts DROP COLUMN unpriced_settlements;
ALTER TABLE budget_reservations DROP COLUMN reserved_cost_micros;
ALTER TABLE budget_reservations DROP COLUMN settled_cost_micros;
ALTER TABLE budget_reservations DROP COLUMN unpriced;
ALTER TABLE imported_usage DROP COLUMN cost_micros;
ALTER TABLE imported_usage DROP COLUMN unpriced;
ALTER TABLE provider_token_grants DROP COLUMN price_json;
ALTER TABLE provider_token_grants DROP COLUMN price_digest;
ALTER TABLE provider_token_grants DROP COLUMN cost_upper_micros;
ALTER TABLE provider_token_grants DROP COLUMN actual_cost_micros;
DROP TRIGGER assurance_source_obligations_update;
ALTER TABLE obligations DROP COLUMN spent_cost_micros;
CREATE TRIGGER assurance_source_obligations_update AFTER UPDATE ON obligations WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.obligation_id IS NOT OLD.obligation_id OR NEW.goal_signature_id IS NOT OLD.goal_signature_id OR NEW.scope IS NOT OLD.scope OR NEW.requiredness IS NOT OLD.requiredness OR NEW.lifecycle IS NOT OLD.lifecycle OR NEW.resolution_ref IS NOT OLD.resolution_ref OR NEW.parent_obligation_id IS NOT OLD.parent_obligation_id OR NEW.budget_lineage_ref IS NOT OLD.budget_lineage_ref OR NEW.failure_count IS NOT OLD.failure_count OR NEW.spent_tokens IS NOT OLD.spent_tokens OR NEW.spent_attempts IS NOT OLD.spent_attempts OR NEW.fuel_limit IS NOT OLD.fuel_limit OR NEW.fuel_used IS NOT OLD.fuel_used OR NEW.fuel_remaining IS NOT OLD.fuel_remaining OR NEW.demand_admitted IS NOT OLD.demand_admitted OR NEW.obligation_json IS NOT OLD.obligation_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
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
"""

# 2026-10-03 HTN 补齐阶段 D：删从没有生产写方的两张支持集合表（先删它们的屏障触发器）与义务表三个
# 恒零列（失败次数、已花 token、已花尝试——改为读时由尝试与结算推出）。义务表更新触发器先删再删列，
# 然后照迁移 38 原文重建，只去掉引用这三列的子句（与迁移 37、38 的做法一样）。
# 评测晋级表（H6）：迁移 23 的文字原样保留，迁移 40 删表（阶段 C3）
DDL_V23 = """
CREATE TABLE method_evaluations (
 method_id TEXT NOT NULL,
 method_version INTEGER NOT NULL,
 method_hash TEXT NOT NULL CHECK(length(method_hash)=64),
 set_hash TEXT NOT NULL CHECK(length(set_hash)=64),
 frozen_json TEXT NOT NULL CHECK(json_valid(frozen_json)),
 evidence_hash TEXT,
 evaluation_json TEXT CHECK(evaluation_json IS NULL OR json_valid(evaluation_json)),
 state TEXT NOT NULL CHECK(state IN ('FROZEN','EVALUATED','REJECTED','ADMITTED')),
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL,
 PRIMARY KEY(method_id,method_version),
 FOREIGN KEY(method_id,method_version) REFERENCES method_contracts(method_id,method_version)
) STRICT;
"""

DDL_V39 = """
ALTER TABLE observations ADD COLUMN question_json TEXT NOT NULL DEFAULT '';
DROP TRIGGER assurance_source_support_members_insert;
DROP TRIGGER assurance_source_support_members_update;
DROP TRIGGER assurance_source_support_members_delete;
DROP TRIGGER assurance_source_support_members_relocation_0;
DROP TRIGGER assurance_source_justification_sets_insert;
DROP TRIGGER assurance_source_justification_sets_update;
DROP TRIGGER assurance_source_justification_sets_delete;
DROP TRIGGER assurance_source_justification_sets_relocation_0;
DROP TABLE support_members;
DROP TABLE justification_sets;
DROP TRIGGER assurance_source_obligations_update;
ALTER TABLE obligations DROP COLUMN failure_count;
ALTER TABLE obligations DROP COLUMN spent_tokens;
ALTER TABLE obligations DROP COLUMN spent_attempts;
CREATE TRIGGER assurance_source_obligations_update AFTER UPDATE ON obligations WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.obligation_id IS NOT OLD.obligation_id OR NEW.goal_signature_id IS NOT OLD.goal_signature_id OR NEW.scope IS NOT OLD.scope OR NEW.requiredness IS NOT OLD.requiredness OR NEW.lifecycle IS NOT OLD.lifecycle OR NEW.resolution_ref IS NOT OLD.resolution_ref OR NEW.parent_obligation_id IS NOT OLD.parent_obligation_id OR NEW.budget_lineage_ref IS NOT OLD.budget_lineage_ref OR NEW.fuel_limit IS NOT OLD.fuel_limit OR NEW.fuel_used IS NOT OLD.fuel_used OR NEW.fuel_remaining IS NOT OLD.fuel_remaining OR NEW.demand_admitted IS NOT OLD.demand_admitted OR NEW.obligation_json IS NOT OLD.obligation_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
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
"""

# 阶段 C3：全库做法（先例库）与归因记录。两张表不是保证通道源表——它们只决定给规划器看哪些先例，
# 没有任何证书读它们，晋级、归因、退役都不触发屏障。同批删评测晋级表与规则截断摘要表。
DDL_V40 = """
DROP TABLE method_evaluations;
DROP TABLE summaries;
CREATE TABLE method_library (
 entry_id TEXT PRIMARY KEY,
 owner TEXT NOT NULL,
 goal_type_id TEXT NOT NULL,
 catalog_digest TEXT NOT NULL CHECK(length(catalog_digest)=64),
 method_id TEXT NOT NULL,
 method_version INTEGER NOT NULL,
 method_hash TEXT NOT NULL CHECK(length(method_hash)=64),
 purpose TEXT NOT NULL,
 source_mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 root_review_record_id TEXT NOT NULL,
 based_on TEXT REFERENCES method_library(entry_id),
 state TEXT NOT NULL CHECK(state IN ('LISTED','RETIRED')),
 retired_by TEXT,
 retired_reason TEXT,
 retired_at REAL,
 promoted_at REAL NOT NULL,
 UNIQUE(owner, method_id, method_version),
 FOREIGN KEY(method_id, method_version) REFERENCES method_contracts(method_id, method_version)
) STRICT;
CREATE INDEX method_library_listing ON method_library(owner, goal_type_id, catalog_digest, state, promoted_at);
CREATE TABLE method_library_attributions (
 entry_id TEXT NOT NULL REFERENCES method_library(entry_id),
 source_ref TEXT NOT NULL,
 source_kind TEXT NOT NULL CHECK(source_kind IN ('PLANNER','ROOT_REVIEW')),
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 method_id TEXT NOT NULL,
 method_version INTEGER NOT NULL,
 method_hash TEXT NOT NULL,
 reason TEXT NOT NULL,
 recorded_at REAL NOT NULL,
 PRIMARY KEY(entry_id, source_ref)
) STRICT;
"""


DDL_V41 = """
-- HTN 补齐阶段 G（全业务事件重放收尾）。①没有生产写方或生产始终为空的表删掉；
-- ②保证通道全局触发器只唤醒没结束的任务（全局纪元照旧加 1）；③只增的业务表与命令回执账不许改删。
DROP TABLE bound_inputs;
DROP TABLE obligation_relations;
DROP TABLE obligation_expansions;
DROP TABLE obligation_shape_changes;
DROP TABLE policy_evaluations;
DROP TABLE policy_decisions;
DROP TABLE policy_proposals;
DROP TRIGGER assurance_source_method_contracts_insert;
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
 FROM assurance_mission_bindings b JOIN missions m ON m.mission_id=b.mission_id
 CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1 AND m.status NOT IN ('COMPLETED','FAILED','CANCELLED');
 END;
DROP TRIGGER assurance_source_method_contracts_update;
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
 FROM assurance_mission_bindings b JOIN missions m ON m.mission_id=b.mission_id
 CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1 AND m.status NOT IN ('COMPLETED','FAILED','CANCELLED');
 END;
DROP TRIGGER assurance_source_method_contracts_delete;
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
 FROM assurance_mission_bindings b JOIN missions m ON m.mission_id=b.mission_id
 CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1 AND m.status NOT IN ('COMPLETED','FAILED','CANCELLED');
 END;
DROP TRIGGER assurance_source_policy_versions_insert;
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
 FROM assurance_mission_bindings b JOIN missions m ON m.mission_id=b.mission_id
 CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1 AND m.status NOT IN ('COMPLETED','FAILED','CANCELLED');
 END;
DROP TRIGGER assurance_source_policy_versions_update;
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
 FROM assurance_mission_bindings b JOIN missions m ON m.mission_id=b.mission_id
 CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1 AND m.status NOT IN ('COMPLETED','FAILED','CANCELLED');
 END;
DROP TRIGGER assurance_source_policy_versions_delete;
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
 FROM assurance_mission_bindings b JOIN missions m ON m.mission_id=b.mission_id
 CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1 AND m.status NOT IN ('COMPLETED','FAILED','CANCELLED');
 END;
DROP TRIGGER assurance_source_policy_activations_insert;
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
 FROM assurance_mission_bindings b JOIN missions m ON m.mission_id=b.mission_id
 CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1 AND m.status NOT IN ('COMPLETED','FAILED','CANCELLED');
 END;
DROP TRIGGER assurance_source_policy_activations_update;
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
 FROM assurance_mission_bindings b JOIN missions m ON m.mission_id=b.mission_id
 CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1 AND m.status NOT IN ('COMPLETED','FAILED','CANCELLED');
 END;
DROP TRIGGER assurance_source_policy_activations_delete;
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
 FROM assurance_mission_bindings b JOIN missions m ON m.mission_id=b.mission_id
 CROSS JOIN assurance_environment_state e
 WHERE e.singleton=1 AND m.status NOT IN ('COMPLETED','FAILED','CANCELLED');
 END;
CREATE TRIGGER acceptance_commit_receipts_immutable_update BEFORE UPDATE ON acceptance_commit_receipts
 BEGIN SELECT RAISE(ABORT,'immutable source record: acceptance_commit_receipts'); END;
CREATE TRIGGER acceptance_commit_receipts_immutable_delete BEFORE DELETE ON acceptance_commit_receipts
 BEGIN SELECT RAISE(ABORT,'immutable source record: acceptance_commit_receipts'); END;
CREATE TRIGGER acceptance_outputs_immutable_update BEFORE UPDATE ON acceptance_outputs
 BEGIN SELECT RAISE(ABORT,'immutable source record: acceptance_outputs'); END;
CREATE TRIGGER acceptance_outputs_immutable_delete BEFORE DELETE ON acceptance_outputs
 BEGIN SELECT RAISE(ABORT,'immutable source record: acceptance_outputs'); END;
CREATE TRIGGER acceptances_immutable_update BEFORE UPDATE ON acceptances
 BEGIN SELECT RAISE(ABORT,'immutable source record: acceptances'); END;
CREATE TRIGGER acceptances_immutable_delete BEFORE DELETE ON acceptances
 BEGIN SELECT RAISE(ABORT,'immutable source record: acceptances'); END;
CREATE TRIGGER approval_decisions_immutable_update BEFORE UPDATE ON approval_decisions
 BEGIN SELECT RAISE(ABORT,'immutable source record: approval_decisions'); END;
CREATE TRIGGER approval_decisions_immutable_delete BEFORE DELETE ON approval_decisions
 BEGIN SELECT RAISE(ABORT,'immutable source record: approval_decisions'); END;
CREATE TRIGGER budget_tail_transfers_immutable_update BEFORE UPDATE ON budget_tail_transfers
 BEGIN SELECT RAISE(ABORT,'immutable source record: budget_tail_transfers'); END;
CREATE TRIGGER budget_tail_transfers_immutable_delete BEFORE DELETE ON budget_tail_transfers
 BEGIN SELECT RAISE(ABORT,'immutable source record: budget_tail_transfers'); END;
CREATE TRIGGER commit_receipts_immutable_update BEFORE UPDATE ON commit_receipts
 BEGIN SELECT RAISE(ABORT,'immutable source record: commit_receipts'); END;
CREATE TRIGGER commit_receipts_immutable_delete BEFORE DELETE ON commit_receipts
 BEGIN SELECT RAISE(ABORT,'immutable source record: commit_receipts'); END;
CREATE TRIGGER criterion_evaluations_immutable_update BEFORE UPDATE ON criterion_evaluations
 BEGIN SELECT RAISE(ABORT,'immutable source record: criterion_evaluations'); END;
CREATE TRIGGER criterion_evaluations_immutable_delete BEFORE DELETE ON criterion_evaluations
 BEGIN SELECT RAISE(ABORT,'immutable source record: criterion_evaluations'); END;
CREATE TRIGGER data_requirements_immutable_update BEFORE UPDATE ON data_requirements
 BEGIN SELECT RAISE(ABORT,'immutable source record: data_requirements'); END;
CREATE TRIGGER data_requirements_immutable_delete BEFORE DELETE ON data_requirements
 BEGIN SELECT RAISE(ABORT,'immutable source record: data_requirements'); END;
CREATE TRIGGER delivery_receipts_immutable_update BEFORE UPDATE ON delivery_receipts
 BEGIN SELECT RAISE(ABORT,'immutable source record: delivery_receipts'); END;
CREATE TRIGGER delivery_receipts_immutable_delete BEFORE DELETE ON delivery_receipts
 BEGIN SELECT RAISE(ABORT,'immutable source record: delivery_receipts'); END;
CREATE TRIGGER human_overrides_immutable_update BEFORE UPDATE ON human_overrides
 BEGIN SELECT RAISE(ABORT,'immutable source record: human_overrides'); END;
CREATE TRIGGER human_overrides_immutable_delete BEFORE DELETE ON human_overrides
 BEGIN SELECT RAISE(ABORT,'immutable source record: human_overrides'); END;
CREATE TRIGGER input_manifest_bindings_immutable_update BEFORE UPDATE ON input_manifest_bindings
 BEGIN SELECT RAISE(ABORT,'immutable source record: input_manifest_bindings'); END;
CREATE TRIGGER input_manifest_bindings_immutable_delete BEFORE DELETE ON input_manifest_bindings
 BEGIN SELECT RAISE(ABORT,'immutable source record: input_manifest_bindings'); END;
CREATE TRIGGER input_manifests_immutable_update BEFORE UPDATE ON input_manifests
 BEGIN SELECT RAISE(ABORT,'immutable source record: input_manifests'); END;
CREATE TRIGGER input_manifests_immutable_delete BEFORE DELETE ON input_manifests
 BEGIN SELECT RAISE(ABORT,'immutable source record: input_manifests'); END;
CREATE TRIGGER method_child_occurrences_immutable_update BEFORE UPDATE ON method_child_occurrences
 BEGIN SELECT RAISE(ABORT,'immutable source record: method_child_occurrences'); END;
CREATE TRIGGER method_child_occurrences_immutable_delete BEFORE DELETE ON method_child_occurrences
 BEGIN SELECT RAISE(ABORT,'immutable source record: method_child_occurrences'); END;
CREATE TRIGGER mission_domains_immutable_update BEFORE UPDATE ON mission_domains
 BEGIN SELECT RAISE(ABORT,'immutable source record: mission_domains'); END;
CREATE TRIGGER mission_domains_immutable_delete BEFORE DELETE ON mission_domains
 BEGIN SELECT RAISE(ABORT,'immutable source record: mission_domains'); END;
CREATE TRIGGER mission_planning_protocols_immutable_update BEFORE UPDATE ON mission_planning_protocols
 BEGIN SELECT RAISE(ABORT,'immutable source record: mission_planning_protocols'); END;
CREATE TRIGGER mission_planning_protocols_immutable_delete BEFORE DELETE ON mission_planning_protocols
 BEGIN SELECT RAISE(ABORT,'immutable source record: mission_planning_protocols'); END;
CREATE TRIGGER mission_policies_immutable_update BEFORE UPDATE ON mission_policies
 BEGIN SELECT RAISE(ABORT,'immutable source record: mission_policies'); END;
CREATE TRIGGER mission_policies_immutable_delete BEFORE DELETE ON mission_policies
 BEGIN SELECT RAISE(ABORT,'immutable source record: mission_policies'); END;
CREATE TRIGGER observations_immutable_update BEFORE UPDATE ON observations
 BEGIN SELECT RAISE(ABORT,'immutable source record: observations'); END;
CREATE TRIGGER observations_immutable_delete BEFORE DELETE ON observations
 BEGIN SELECT RAISE(ABORT,'immutable source record: observations'); END;
CREATE TRIGGER operation_bindings_immutable_update BEFORE UPDATE ON operation_bindings
 BEGIN SELECT RAISE(ABORT,'immutable source record: operation_bindings'); END;
CREATE TRIGGER operation_bindings_immutable_delete BEFORE DELETE ON operation_bindings
 BEGIN SELECT RAISE(ABORT,'immutable source record: operation_bindings'); END;
CREATE TRIGGER operation_identities_immutable_update BEFORE UPDATE ON operation_identities
 BEGIN SELECT RAISE(ABORT,'immutable source record: operation_identities'); END;
CREATE TRIGGER operation_identities_immutable_delete BEFORE DELETE ON operation_identities
 BEGIN SELECT RAISE(ABORT,'immutable source record: operation_identities'); END;
CREATE TRIGGER operation_intent_bindings_immutable_update BEFORE UPDATE ON operation_intent_bindings
 BEGIN SELECT RAISE(ABORT,'immutable source record: operation_intent_bindings'); END;
CREATE TRIGGER operation_intent_bindings_immutable_delete BEFORE DELETE ON operation_intent_bindings
 BEGIN SELECT RAISE(ABORT,'immutable source record: operation_intent_bindings'); END;
CREATE TRIGGER operation_payload_objects_immutable_update BEFORE UPDATE ON operation_payload_objects
 BEGIN SELECT RAISE(ABORT,'immutable source record: operation_payload_objects'); END;
CREATE TRIGGER operation_payload_objects_immutable_delete BEFORE DELETE ON operation_payload_objects
 BEGIN SELECT RAISE(ABORT,'immutable source record: operation_payload_objects'); END;
CREATE TRIGGER order_constraints_immutable_update BEFORE UPDATE ON order_constraints
 BEGIN SELECT RAISE(ABORT,'immutable source record: order_constraints'); END;
CREATE TRIGGER order_constraints_immutable_delete BEFORE DELETE ON order_constraints
 BEGIN SELECT RAISE(ABORT,'immutable source record: order_constraints'); END;
CREATE TRIGGER plan_commit_receipts_immutable_update BEFORE UPDATE ON plan_commit_receipts
 BEGIN SELECT RAISE(ABORT,'immutable source record: plan_commit_receipts'); END;
CREATE TRIGGER plan_commit_receipts_immutable_delete BEFORE DELETE ON plan_commit_receipts
 BEGIN SELECT RAISE(ABORT,'immutable source record: plan_commit_receipts'); END;
CREATE TRIGGER plan_read_sets_immutable_update BEFORE UPDATE ON plan_read_sets
 BEGIN SELECT RAISE(ABORT,'immutable source record: plan_read_sets'); END;
CREATE TRIGGER plan_read_sets_immutable_delete BEFORE DELETE ON plan_read_sets
 BEGIN SELECT RAISE(ABORT,'immutable source record: plan_read_sets'); END;
CREATE TRIGGER planning_admission_checks_immutable_update BEFORE UPDATE ON planning_admission_checks
 BEGIN SELECT RAISE(ABORT,'immutable source record: planning_admission_checks'); END;
CREATE TRIGGER planning_admission_checks_immutable_delete BEFORE DELETE ON planning_admission_checks
 BEGIN SELECT RAISE(ABORT,'immutable source record: planning_admission_checks'); END;
CREATE TRIGGER planning_lane_grants_immutable_update BEFORE UPDATE ON planning_lane_grants
 BEGIN SELECT RAISE(ABORT,'immutable source record: planning_lane_grants'); END;
CREATE TRIGGER planning_lane_grants_immutable_delete BEFORE DELETE ON planning_lane_grants
 BEGIN SELECT RAISE(ABORT,'immutable source record: planning_lane_grants'); END;
CREATE TRIGGER planning_operation_action_links_immutable_update BEFORE UPDATE ON planning_operation_action_links
 BEGIN SELECT RAISE(ABORT,'immutable source record: planning_operation_action_links'); END;
CREATE TRIGGER planning_operation_action_links_immutable_delete BEFORE DELETE ON planning_operation_action_links
 BEGIN SELECT RAISE(ABORT,'immutable source record: planning_operation_action_links'); END;
CREATE TRIGGER planning_request_authority_bindings_immutable_update BEFORE UPDATE ON planning_request_authority_bindings
 BEGIN SELECT RAISE(ABORT,'immutable source record: planning_request_authority_bindings'); END;
CREATE TRIGGER planning_request_authority_bindings_immutable_delete BEFORE DELETE ON planning_request_authority_bindings
 BEGIN SELECT RAISE(ABORT,'immutable source record: planning_request_authority_bindings'); END;
CREATE TRIGGER requirements_revisions_immutable_update BEFORE UPDATE ON requirements_revisions
 BEGIN SELECT RAISE(ABORT,'immutable source record: requirements_revisions'); END;
CREATE TRIGGER requirements_revisions_immutable_delete BEFORE DELETE ON requirements_revisions
 BEGIN SELECT RAISE(ABORT,'immutable source record: requirements_revisions'); END;
CREATE TRIGGER review_packages_immutable_update BEFORE UPDATE ON review_packages
 BEGIN SELECT RAISE(ABORT,'immutable source record: review_packages'); END;
CREATE TRIGGER review_packages_immutable_delete BEFORE DELETE ON review_packages
 BEGIN SELECT RAISE(ABORT,'immutable source record: review_packages'); END;
CREATE TRIGGER review_records_immutable_update BEFORE UPDATE ON review_records
 BEGIN SELECT RAISE(ABORT,'immutable source record: review_records'); END;
CREATE TRIGGER review_records_immutable_delete BEFORE DELETE ON review_records
 BEGIN SELECT RAISE(ABORT,'immutable source record: review_records'); END;
CREATE TRIGGER task_semantics_immutable_update BEFORE UPDATE ON task_semantics
 BEGIN SELECT RAISE(ABORT,'immutable source record: task_semantics'); END;
CREATE TRIGGER task_semantics_immutable_delete BEFORE DELETE ON task_semantics
 BEGIN SELECT RAISE(ABORT,'immutable source record: task_semantics'); END;
CREATE TRIGGER tool_calls_immutable_update BEFORE UPDATE ON tool_calls
 BEGIN SELECT RAISE(ABORT,'immutable source record: tool_calls'); END;
CREATE TRIGGER tool_calls_immutable_delete BEFORE DELETE ON tool_calls
 BEGIN SELECT RAISE(ABORT,'immutable source record: tool_calls'); END;
CREATE TRIGGER validity_witnesses_immutable_update BEFORE UPDATE ON validity_witnesses
 BEGIN SELECT RAISE(ABORT,'immutable source record: validity_witnesses'); END;
CREATE TRIGGER validity_witnesses_immutable_delete BEFORE DELETE ON validity_witnesses
 BEGIN SELECT RAISE(ABORT,'immutable source record: validity_witnesses'); END;
"""

DDL_V42 = """
-- TaskGraph 补全第四批：验证记录按（结果, 要求版本, 层）唯一。改要求后同一份结果按新版要求重审，
-- 两版的验证记录并存；旧库的记录要求版本记 0（开发期不做旧数据兼容）。表重建，触发器原样重建
-- （更新触发器的比较列加上要求版本）。
DROP TRIGGER assurance_verification_relocation;
DROP TRIGGER assurance_source_verifications_insert;
DROP TRIGGER assurance_source_verifications_update;
DROP TRIGGER assurance_source_verifications_delete;
CREATE TABLE verifications_v42 (
 verification_id TEXT PRIMARY KEY,
 result_id TEXT NOT NULL REFERENCES results(result_id),
 attempt_id TEXT NOT NULL,
 requirements_revision INTEGER NOT NULL CHECK(requirements_revision>=0),
 layer TEXT NOT NULL,
 status TEXT NOT NULL,
 detail_json TEXT NOT NULL,
 created_at REAL NOT NULL,
 UNIQUE(result_id, requirements_revision, layer)
) STRICT;
INSERT INTO verifications_v42(verification_id,result_id,attempt_id,requirements_revision,layer,status,detail_json,created_at)
 SELECT verification_id,result_id,attempt_id,0,layer,status,detail_json,created_at FROM verifications;
DROP TABLE verifications;
ALTER TABLE verifications_v42 RENAME TO verifications;
CREATE TRIGGER assurance_verification_relocation BEFORE INSERT ON verifications
WHEN EXISTS(SELECT 1 FROM verifications v JOIN results r ON r.result_id=v.result_id
 JOIN results n ON n.result_id=NEW.result_id
 WHERE v.verification_id=NEW.verification_id AND r.mission_id<>n.mission_id
 AND EXISTS(SELECT 1 FROM assurance_mission_bindings b
 WHERE b.mission_id IN (r.mission_id,n.mission_id)))
BEGIN SELECT RAISE(ABORT,'ASSURANCE_SOURCE_RELOCATION_FORBIDDEN'); END;
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
CREATE TRIGGER assurance_source_verifications_update AFTER UPDATE ON verifications WHEN (NEW.verification_id IS NOT OLD.verification_id OR NEW.result_id IS NOT OLD.result_id OR NEW.attempt_id IS NOT OLD.attempt_id OR NEW.requirements_revision IS NOT OLD.requirements_revision OR NEW.layer IS NOT OLD.layer OR NEW.status IS NOT OLD.status OR NEW.detail_json IS NOT OLD.detail_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
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
"""

DDL_V43 = """
-- TaskGraph 补全第五批：删"钉住旧版本"。数据边一律跟随现行算数的验收，表里不再有
-- source_revision_policy 这一列；旧行的 JSON 里同名键一并去掉。表重建，索引与触发器原样重建。
DROP TRIGGER assurance_source_data_requirements_insert;
DROP TRIGGER assurance_source_data_requirements_update;
DROP TRIGGER assurance_source_data_requirements_delete;
DROP TRIGGER data_requirements_immutable_update;
DROP TRIGGER data_requirements_immutable_delete;
CREATE TABLE data_requirements_v43 (
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 plan_revision INTEGER NOT NULL,
 requirement_id TEXT NOT NULL,
 producer_occurrence TEXT NOT NULL,
 output_port TEXT NOT NULL,
 consumer_occurrence TEXT NOT NULL,
 input_port TEXT NOT NULL,
 requirement_json TEXT NOT NULL,
 created_at REAL NOT NULL,
 PRIMARY KEY(mission_id, plan_revision, requirement_id),
 FOREIGN KEY(mission_id, plan_revision) REFERENCES plan_revisions(mission_id, revision)
) STRICT;
INSERT INTO data_requirements_v43(mission_id,plan_revision,requirement_id,producer_occurrence,output_port,consumer_occurrence,input_port,requirement_json,created_at)
 SELECT mission_id,plan_revision,requirement_id,producer_occurrence,output_port,consumer_occurrence,input_port,json_remove(requirement_json,'$.source_revision_policy'),created_at FROM data_requirements;
DROP TABLE data_requirements;
ALTER TABLE data_requirements_v43 RENAME TO data_requirements;
CREATE INDEX data_requirements_consumer_idx
 ON data_requirements(mission_id, plan_revision, consumer_occurrence, input_port);
CREATE INDEX data_requirements_producer_idx
 ON data_requirements(mission_id, plan_revision, producer_occurrence, output_port);
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
CREATE TRIGGER assurance_source_data_requirements_update AFTER UPDATE ON data_requirements WHEN (NEW.mission_id IS NOT OLD.mission_id OR NEW.plan_revision IS NOT OLD.plan_revision OR NEW.requirement_id IS NOT OLD.requirement_id OR NEW.producer_occurrence IS NOT OLD.producer_occurrence OR NEW.output_port IS NOT OLD.output_port OR NEW.consumer_occurrence IS NOT OLD.consumer_occurrence OR NEW.input_port IS NOT OLD.input_port OR NEW.requirement_json IS NOT OLD.requirement_json OR NEW.created_at IS NOT OLD.created_at) BEGIN 
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
CREATE TRIGGER data_requirements_immutable_update BEFORE UPDATE ON data_requirements
 BEGIN SELECT RAISE(ABORT,'immutable source record: data_requirements'); END;
CREATE TRIGGER data_requirements_immutable_delete BEFORE DELETE ON data_requirements
 BEGIN SELECT RAISE(ABORT,'immutable source record: data_requirements'); END;
"""

# 第 1 批 T08（2026-10-06）：迁移 30 把尝试身份守卫的"清单绑定版本 = 本次输入版本"放宽成 <=，原因是
# input_manifest_bindings 主键 (mission_id, task_id, manifest_hash) 让换代后同一份内容只留首次绑定那一行。
# 这里把 input_binding_revision 加进主键（每个输入版本各记一行），守卫恢复原计划（V25 文本）的相等。
# 表重建，三个索引与五条触发器原文照抄重建；正文引用这张表的四条触发器（input_manifests 三条
# 保证触发器与尝试身份守卫）也先删后建——不然重命名时 SQLite 解析整库 schema 会报"no such table"。
DDL_V44 = """
DROP TRIGGER tg_attempt_identity_guard;
DROP TRIGGER assurance_source_input_manifest_bindings_insert;
DROP TRIGGER assurance_source_input_manifest_bindings_update;
DROP TRIGGER assurance_source_input_manifest_bindings_delete;
DROP TRIGGER input_manifest_bindings_immutable_update;
DROP TRIGGER input_manifest_bindings_immutable_delete;
DROP TRIGGER assurance_source_input_manifests_insert;
DROP TRIGGER assurance_source_input_manifests_update;
DROP TRIGGER assurance_source_input_manifests_delete;
CREATE TABLE input_manifest_bindings_v44 (
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 task_id TEXT NOT NULL,
 manifest_hash TEXT NOT NULL REFERENCES input_manifests(manifest_hash),
 attempt_id TEXT,
 request_id TEXT,
 input_binding_revision INTEGER NOT NULL CHECK(input_binding_revision>=0),
 created_at REAL NOT NULL,
 PRIMARY KEY(mission_id, task_id, manifest_hash, input_binding_revision)
) STRICT;
INSERT INTO input_manifest_bindings_v44(mission_id,task_id,manifest_hash,attempt_id,request_id,input_binding_revision,created_at)
 SELECT mission_id,task_id,manifest_hash,attempt_id,request_id,input_binding_revision,created_at FROM input_manifest_bindings;
DROP TABLE input_manifest_bindings;
ALTER TABLE input_manifest_bindings_v44 RENAME TO input_manifest_bindings;
CREATE INDEX input_manifest_bindings_task_idx
 ON input_manifest_bindings(mission_id, task_id, input_binding_revision);
CREATE INDEX input_manifest_bindings_hash_idx
 ON input_manifest_bindings(manifest_hash, mission_id);
CREATE UNIQUE INDEX input_manifest_bindings_request_idx
 ON input_manifest_bindings(mission_id, task_id, request_id) WHERE request_id IS NOT NULL;
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
CREATE TRIGGER input_manifest_bindings_immutable_update BEFORE UPDATE ON input_manifest_bindings
 BEGIN SELECT RAISE(ABORT,'immutable source record: input_manifest_bindings'); END;
CREATE TRIGGER input_manifest_bindings_immutable_delete BEFORE DELETE ON input_manifest_bindings
 BEGIN SELECT RAISE(ABORT,'immutable source record: input_manifest_bindings'); END;
CREATE TRIGGER tg_attempt_identity_guard BEFORE INSERT ON taskgraph_attempt_inputs BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM attempts a JOIN dispatch_intents d ON d.subject_id=a.attempt_id
  JOIN plan_memberships m ON m.mission_id=a.mission_id AND m.task_id=a.task_id
  JOIN task_semantics s ON s.task_id=a.task_id AND s.binding_revision=NEW.binding_revision
  JOIN taskgraph_revision_records rr ON rr.mission_id=NEW.mission_id AND rr.revision=NEW.source_revision
  JOIN planning_admission_checks c ON c.check_id=NEW.admission_check_id
  JOIN planning_requests r ON r.request_id=c.request_id
  JOIN input_manifest_bindings b ON b.mission_id=a.mission_id AND b.task_id=a.task_id
   AND b.manifest_hash=NEW.manifest_hash AND b.input_binding_revision=NEW.input_binding_revision
  WHERE a.attempt_id=NEW.attempt_id AND a.mission_id=NEW.mission_id AND a.task_id=NEW.task_id
   AND d.intent_id=NEW.intent_id AND d.mission_id=NEW.mission_id
   AND d.creation_key=NEW.creation_key AND d.input_id=NEW.input_id AND d.input_hash=NEW.frozen_input_hash
   AND m.revision=NEW.source_revision AND m.occurrence_id=NEW.occurrence_id AND m.form='primitive'
   AND s.mission_id=NEW.mission_id AND s.form='primitive'
   AND s.input_binding_revision=NEW.input_binding_revision AND s.dispatch_generation=NEW.dispatch_generation
   AND rr.admission_check_id=NEW.admission_check_id
   AND r.mission_id=NEW.mission_id AND c.phase='APPLIED'
 ) THEN RAISE(ABORT,'TG_ATTEMPT_IDENTITY_MISMATCH') END;
END;
"""

MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "orchestrator-step02", DDL_V1),
    Migration(2, "orchestrator-step04", DDL_V2),
    Migration(3, "orchestrator-step05", DDL_V3),
    Migration(4, "orchestrator-step06", DDL_V4),
    Migration(5, "orchestrator-step07", DDL_V5),
    Migration(6, "orchestrator-step09", DDL_V6),
    Migration(7, "orchestrator-p32", DDL_V7),
    Migration(8, "orchestrator-p33-domains", DDL_V8),
    Migration(9, "orchestrator-p33-sources", DDL_V9),
    Migration(10, "orchestrator-p33-assessments", DDL_V10),
    Migration(11, "orchestrator-p35-provider-admission", DDL_V11),
    Migration(12, "orchestrator-p34-selection", DDL_V12),
    Migration(13, "orchestrator-tail-reservations", DDL_V13),
    Migration(14, "orchestrator-p34-fragments", DDL_V14),
    Migration(15, "orchestrator-mission-system-tail", DDL_V15),
    Migration(16, "orchestrator-full-target-htn", DDL_V16),
    Migration(17, "orchestrator-full-target-acceptance-receipts", DDL_V17),
    Migration(18, "orchestrator-full-target-witness-subject", DDL_V18),
    Migration(19, "orchestrator-planning-decision-v1", DDL_V19),
    Migration(20, "orchestrator-h1h-admission-seams", DDL_V20),
    Migration(21, "orchestrator-operation-seams", DDL_V21),
    Migration(22, "orchestrator-operation-completion", DDL_V22),
    Migration(23, "orchestrator-method-evaluations", DDL_V23),
    Migration(24, "orchestrator-planning-human-requests", DDL_V24),
    Migration(25, "orchestrator-taskgraph-execution-v2", DDL_V25),
    Migration(26, "orchestrator-assurance-exec-v1.1", DDL_V26),
    Migration(27, "orchestrator-assurance-pin-per-object", DDL_V27),
    Migration(28, "orchestrator-commit-receipts-kind-index", DDL_V28),
    Migration(29, "orchestrator-taskgraph-required", DDL_V29),
    Migration(30, "orchestrator-manifest-binding-revision-at-or-before", DDL_V30),
    Migration(31, "orchestrator-drop-selection-and-fragments", DDL_V31),
    Migration(32, "orchestrator-drop-conflicts-graph-changes-system-tail", DDL_V32),
    Migration(33, "orchestrator-drop-criterion-assessments", DDL_V33),
    Migration(34, "orchestrator-drop-captured-baseline", DDL_V34),
    Migration(35, "orchestrator-drop-taskgraph-requirements", DDL_V35),
    Migration(36, "orchestrator-drop-planning-repair-continuations", DDL_V36),
    Migration(37, "orchestrator-artifacts-barrier-without-offline-relocation", DDL_V37),
    Migration(38, "orchestrator-drop-money-dimension", DDL_V38),
    Migration(39, "orchestrator-drop-support-sets-and-duty-spend", DDL_V39),
    Migration(40, "orchestrator-method-library-and-drop-rule-summaries", DDL_V40),
    Migration(41, "orchestrator-g-replay-v3", DDL_V41),
    Migration(42, "orchestrator-verifications-per-requirements", DDL_V42),
    Migration(43, "orchestrator-data-requirements-drop-revision-policy", DDL_V43),
    Migration(44, "orchestrator-manifest-binding-per-input-revision", DDL_V44),
)
SCHEMA_VERSION = MIGRATIONS[-1].version
SCHEMA_NAME = MIGRATIONS[-1].name
DDL = DDL_V1  # kept for readers of the step-2/3 descriptor


def checksum() -> str:
    return MIGRATIONS[-1].checksum


__all__ = (
    "DDL",
    "DDL_V1",
    "DDL_V2",
    "DDL_V3",
    "DDL_V4",
    "DDL_V5",
    "DDL_V6",
    "DDL_V7",
    "DDL_V8",
    "DDL_V9",
    "DDL_V10",
    "DDL_V11",
    "DDL_V17",
    "DDL_V18",
    "DDL_V19",
    "DDL_V20",
    "DDL_V27",
    "DDL_V28",
    "DDL_V29",
    "MIGRATIONS",
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "Migration",
    "checksum",
)

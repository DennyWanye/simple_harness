import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reference.rules import (
    Grant, AuthorityBinding, authorize, Denied, SourceUnavailable, check_enabled,
    ActionRow, BindingRow, LinkRow, Effect, classify_effect, operation_snapshot,
    operation_gate, map_delta, NoPlanMutation, CheckedPlanShape, plan_gate,
    DELTA_CODES, PROJECTION_CODES,
)

H = 'a' * 64

def grant():
    return Grant('g1','m1','t1','mission','planner-1',1,True,
                 frozenset({'REFINE','REPAIR/REPLACE_METHOD','WAIT','NO_CHANGE','DECLARE_BLOCKED'}),
                 'issuer-receipt',100,1000,H)

def binding(g=None):
    g = grant() if g is None else g
    return AuthorityBinding('pr1','m1','t1','mission','planner-1',g.grant_id,g.revision,g.immutable_hash())

def action():
    return ActionRow('m1','action1:v1','action1',1,H,'action1:v1','PROPOSED',0,
                     False,False,False,False)

def op_rows():
    return (BindingRow('m1','op1','op-occ1',H,H),
            LinkRow('m1','op1','op-occ1',H,H,'action1:v1','action1',1,H,'action1:v1'),
            action())

class AuthorityTests(unittest.TestCase):
    def run_auth(self, g=None, **kw):
        return authorize(binding(), grant() if g is None else g,
                         expected_policy_hash=H,mission_active=True,action='REFINE',now_ms=200,**kw)
    def test_positive_real_grant(self):
        self.assertEqual(self.run_auth().grant_id,'g1')
    def test_no_source_not_default_true(self):
        with self.assertRaises(Denied):
            authorize(binding(),None,expected_policy_hash=H,mission_active=True,action='REFINE',now_ms=200)
    def test_wrong_tenant(self):
        with self.assertRaises(Denied): self.run_auth(replace(grant(),tenant_id='t2'))
    def test_grantee_is_service_identity_not_model_text(self):
        with self.assertRaises(Denied): self.run_auth(replace(grant(),grantee_id='other'))
    def test_revocation_makes_old_request_stale(self):
        with self.assertRaises(Denied) as e: self.run_auth(replace(grant(),revision=2,active=False))
        self.assertEqual(e.exception.code,'REQUEST_BINDING_STALE')
    def test_expiry_at_commit(self):
        with self.assertRaises(Denied):
            authorize(binding(),grant(),expected_policy_hash=H,mission_active=True,action='REFINE',now_ms=1000)
    def test_missing_action(self):
        g=replace(grant(),allowed=frozenset({'WAIT'}))
        with self.assertRaises(Denied):
            authorize(binding(g),g,expected_policy_hash=H,mission_active=True,action='REFINE',now_ms=200)
    def test_decode_only_kept(self):
        for k in ('BIND_EXISTING_GOAL','REPAIR/PROPOSE_SUCCESSOR','PROPOSE_METHOD'):
            with self.subTest(kind=k), self.assertRaises(Denied): check_enabled(k)

class OperationTests(unittest.TestCase):
    def test_complete_empty_is_positive_read(self):
        self.assertEqual(operation_snapshot('m1',(),(),(),complete_read=True).unresolved,())
    def test_incomplete_empty_is_not_clear(self):
        with self.assertRaises(SourceUnavailable): operation_snapshot('m1',(),(),(),complete_read=False)
    def test_missing_link(self):
        b,l,a=op_rows()
        with self.assertRaises(SourceUnavailable): operation_snapshot('m1',(b,),(),(a,),complete_read=True)
    def test_unlinked_historical_action_not_filtered_out(self):
        b,l,a=op_rows()
        with self.assertRaises(SourceUnavailable):
            operation_snapshot('m1',(b,),(l,),(a,replace(a,action_key='old:v1')),complete_read=True)
    def test_params_mismatch(self):
        b,l,a=op_rows()
        with self.assertRaises(SourceUnavailable):
            operation_snapshot('m1',(b,),(l,),(replace(a,params_hash='b'*64),),complete_read=True)
    def test_unknown_label_does_not_reconcile(self):
        a=replace(action(),state='UNKNOWN',handoffs=1,reconciliation_label='CONFIRMED_NOT_STARTED')
        self.assertEqual(classify_effect(a),Effect.UNRESOLVED)
    def test_live_handoff_and_success(self):
        self.assertEqual(classify_effect(replace(action(),state='HANDED_OFF',handoffs=1)),Effect.IN_FLIGHT)
        self.assertEqual(classify_effect(replace(action(),state='SUCCEEDED',handoffs=1,matching_success_receipt=True)),Effect.APPLIED)
    def test_success_needs_receipt(self):
        with self.assertRaises(SourceUnavailable): classify_effect(replace(action(),state='SUCCEEDED',handoffs=1))
    def test_negative_requires_scope_and_finality(self):
        a=replace(action(),state='UNKNOWN',handoffs=1,authoritative_not_applied=True,all_handoffs_covered=True)
        self.assertEqual(classify_effect(a),Effect.UNRESOLVED)
        self.assertEqual(classify_effect(replace(a,no_late_apply_proven=True)),Effect.CONFIRMED_NOT_APPLIED)
    def test_cancelled_after_handoff_not_clear(self):
        self.assertEqual(classify_effect(replace(action(),state='CANCELLED',handoffs=1)),Effect.UNRESOLVED)
    def test_phantom_action_changes_read_digest(self):
        b,l,a=op_rows()
        empty=operation_snapshot('m1',(),(),(),complete_read=True)
        present=operation_snapshot('m1',(b,),(l,),(a,),complete_read=True)
        self.assertNotEqual(empty.read_digest,present.read_digest)
    def test_unknown_blocks_plan(self):
        b,l,a=op_rows(); a=replace(a,state='UNKNOWN',handoffs=1)
        p=operation_snapshot('m1',(b,),(l,),(a,),complete_read=True)
        with self.assertRaises(Denied): operation_gate(p)
    def test_refusal_with_no_handoff_not_fabricated_operation(self):
        a=replace(action(),state='REFUSED',idempotency_key=None)
        self.assertEqual(operation_snapshot('m1',(),(),(a,),complete_read=True).unresolved,())

class ShapeTests(unittest.TestCase):
    def test_reports_not_fake_empty_shape(self):
        for k in ('WAIT','NO_CHANGE','DECLARE_BLOCKED'): self.assertEqual(NoPlanMutation(k).decision_kind,k)
        with self.assertRaises(ValueError): NoPlanMutation('REFINE')
    def test_all_delta_values_explicitly_mapped(self):
        for k,v in DELTA_CODES.items(): self.assertEqual(map_delta(k),(v,))
    def test_all_projection_values_explicitly_mapped(self):
        for k,v in PROJECTION_CODES.items(): self.assertEqual(map_delta('projection_defect',projection_kinds=[k]),(v,))
    def test_incomplete_not_valid(self):
        for k in ('not_checked','new_unrecognized_enum'):
            with self.assertRaises(SourceUnavailable): map_delta(k)
        with self.assertRaises(SourceUnavailable): map_delta('projection_defect',projection_kinds=['partial_check'])
    def test_refinement_cycle_not_dropped(self):
        self.assertEqual(map_delta('refinement_cycle'),('REFINEMENT_CYCLE',))
    def test_delta_hash_and_snapshot_binding(self):
        p=CheckedPlanShape('d','s','x','validator-v1',True,())
        plan_gate(p,decision_hash='d',snapshot_hash='s',delta_hash='x')
        with self.assertRaises(Denied): plan_gate(p,decision_hash='d',snapshot_hash='new',delta_hash='x')
    def test_unchecked_shape_cannot_construct(self):
        with self.assertRaises(SourceUnavailable): CheckedPlanShape('d','s','x','v',False,())

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:'); self.c.execute('PRAGMA foreign_keys=ON')
        # Minimal parent-schema fixture, NOT the production schema/migration test.
        self.c.executescript('''
          CREATE TABLE missions(mission_id TEXT PRIMARY KEY);
          CREATE TABLE planning_requests(request_id TEXT PRIMARY KEY);
          CREATE TABLE operation_identities(operation_id TEXT, request_hash TEXT, UNIQUE(operation_id,request_hash));
          CREATE TABLE operation_bindings(operation_occurrence_id TEXT UNIQUE);
          CREATE TABLE actions(action_key TEXT PRIMARY KEY);
        ''')
        self.c.executescript((Path(__file__).resolve().parents[1]/'reference/schema.sql').read_text())
    def tearDown(self): self.c.close()
    def test_ddl_creates_four_tables_and_fk_check(self):
        names={r[0] for r in self.c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'planning_lane_grants','planning_request_authority_bindings',
                         'planning_operation_action_links','planning_admission_checks'}<=names)
        self.assertEqual(self.c.execute('PRAGMA foreign_key_check').fetchall(),[])
    def test_request_authority_cannot_reference_missing_grant(self):
        self.c.execute("INSERT INTO missions VALUES ('m1')")
        self.c.execute("INSERT INTO planning_requests VALUES ('pr1')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute('INSERT INTO planning_request_authority_bindings VALUES (?,?,?,?,?,?,?,?,?,?)',
                           ('pr1','m1','t1','mission','planner-1','missing',1,H,H,'{}'))

if __name__=='__main__': unittest.main()

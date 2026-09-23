from pathlib import Path
import importlib.util,json,os,sqlite3,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from reference.semantics import parse_expr,review_can_accept,parse_review_reply
from test_sql import prepare,H,review

from review_test_fixture import fixture_review_can_accept

class FormulaChallengeTests(unittest.TestCase):
    def reply(self):
        return {'schema_version':2,'verdict':'ACCEPT','assessments':[
            {'criterion_id':x,'verdict':'PASS','evidence_ids':[],'reason':'checked','limitations':[]}
            for x in ('a','b','privacy')], 'findings':[]}
    def expr(self):return parse_expr({'any':[{'criterion':'a'},{'criterion':'b'}]},frozenset(('a','b','privacy')))
    def test_other_any_branch_with_real_checks_can_succeed(self):
        self.assertTrue(fixture_review_can_accept(self.reply(),self.expr(),('privacy',),{'a':False,'b':True,'privacy':True}))
    def test_irrelevant_blocker_branch_not_global_fail(self):
        d=self.reply();d['findings']=[{'criterion_id':'a','severity':'BLOCKER','reason':'a is not usable'}]
        self.assertTrue(fixture_review_can_accept(d,self.expr(),('privacy',),{'a':True,'b':True,'privacy':True}))
    def test_mandatory_blocker_always_prevents_success(self):
        d=self.reply();d['findings']=[{'criterion_id':'privacy','severity':'BLOCKER','reason':'breach'}]
        self.assertFalse(fixture_review_can_accept(d,self.expr(),('privacy',),{'a':True,'b':True,'privacy':True}))

class RetentionTests(unittest.TestCase):
    def setUp(self):self.c=prepare()
    def tearDown(self):self.c.close()
    def pin(self):
        self.c.execute('INSERT INTO assurance_blob_pins VALUES (?,?,?,?,?,?,?,?,?,?,?)',
          ('p1','m1','review-future',H,'{}','PREPARING',1,10,None,'rcm1','rcm1'))
    def test_cannot_finalize_without_ready(self):
        self.c.execute('INSERT INTO assurance_closeouts VALUES (?,?,?,?,?,?,?,?)',('m1','rm1','NOT_READY',1,H,'{}','rcm1',1))
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE assurance_closeouts SET state='FINALIZED',row_version=2")
    def test_pin_before_package_no_fake_attempt(self):
        self.pin();self.assertEqual(self.c.execute("SELECT state FROM assurance_blob_pins").fetchone()[0],'PREPARING')
    def test_bound_remains_live_for_gc(self):
        self.pin();review(self.c,job='review-future');self.c.execute("UPDATE assurance_blob_pins SET state='BOUND',row_version=2")
        self.assertEqual(self.c.execute("SELECT count(*) FROM assurance_blob_pins WHERE state IN ('PREPARING','BOUND')").fetchone()[0],1)
    def test_released_does_not_reactivate(self):
        self.pin();self.c.execute("UPDATE assurance_blob_pins SET state='RELEASED',row_version=2,released_at_ms=20")
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE assurance_blob_pins SET state='BOUND',row_version=3,released_at_ms=NULL")
    def test_pin_identity_immutable(self):
        self.pin()
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE assurance_blob_pins SET blob_hash=?,state='BOUND',row_version=2",('b'*64,))
    def test_release_time_required(self):
        self.pin()
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE assurance_blob_pins SET state='RELEASED',row_version=2")
    def test_gc_cannot_drop_release_history(self):
        self.pin()
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute('DELETE FROM assurance_blob_pins')
    def test_pin_rollback(self):
        self.c.execute('BEGIN');self.pin();self.c.rollback()
        self.assertEqual(self.c.execute('SELECT count(*) FROM assurance_blob_pins').fetchone()[0],0)

class ToolBoundaryTests(unittest.TestCase):
    def test_schema_top_objects_have_no_silent_defaults(self):
        schemas=list((ROOT/'contracts').glob('*.schema.json'));self.assertTrue(schemas)
        def visit(n):
            if isinstance(n,dict):
                self.assertNotIn('default',n)
                if n.get('type')=='object' and 'properties' in n:
                    self.assertEqual(set(n['properties']),set(n['required']));self.assertIs(n['additionalProperties'],False)
                for x in n.values():visit(x)
            elif isinstance(n,list):
                for x in n:visit(x)
        for p in schemas:
            with self.subTest(schema=p.name):visit(json.loads(p.read_text()))
    def test_capture_records_but_does_not_follow_symlink_source_root(self):
        spec=importlib.util.spec_from_file_location('capture',ROOT/'tools/capture_identity.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);repo=base/'repo';repo.mkdir();outside=base/'outside';outside.mkdir();(outside/'secret.py').write_text('SECRET=1')
            def git(*args):subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True)
            git('init');git('config','user.email','test@example.invalid');git('config','user.name','Reference Test')
            (repo/'README').write_text('ref');git('add','README');git('commit','-m','init')
            (repo/'src').symlink_to(outside,target_is_directory=True)
            data=mod.inventory(repo)
            self.assertEqual(data['controlled_files']['src']['kind'],'symlink_root')
            self.assertNotIn('src/secret.py',data['controlled_files'])
    def test_manifest_rejects_unlisted_code(self):
        import hashlib
        spec=importlib.util.spec_from_file_location('verifydelivery',ROOT/'tools/verify_delivery.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'x.txt').write_text('x')
            (root/'DELIVERY-MANIFEST.json').write_text(json.dumps({'files':[{'path':'x.txt','size':1,'sha256':hashlib.sha256(b'x').hexdigest()}]}))
            self.assertEqual(mod.verify(root),[])
            (root/'extra.py').write_text('raise SystemExit(0)')
            self.assertTrue(any(x.startswith('undeclared:') for x in mod.verify(root)))
    def test_original_operation_docs_not_rewritten(self):
        import hashlib
        manifest=json.loads((ROOT/'inputs/input-manifest.json').read_text())
        entries=manifest if isinstance(manifest,list) else manifest.get('files',manifest.get('inputs',[]))
        # Actual per-input digest; does not imply SDK/source verification.
        for e in entries:
            name=e.get('path',e.get('name',e.get('file')))
            if name:
                p=ROOT/'inputs'/Path(name).name
                self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),e['sha256'])

class ReviewIdentityTests(unittest.TestCase):
    def key(self,**change):
        from reference.semantics import review_slot_key
        values=dict(mission='m',purpose='TASK_CONTENT',subject_hash='s',requirements_hash='r',scope_hash='scope',round_no=1)
        values.update(change);return review_slot_key(**values)
    def test_duplicate_notifications_share_logical_round(self):
        self.assertEqual(self.key(),self.key())
    def test_legitimate_independent_round_new_identity(self):
        self.assertNotEqual(self.key(),self.key(round_no=2))
    def test_requirements_and_scope_not_coalesced(self):
        self.assertNotEqual(self.key(),self.key(requirements_hash='new'))
        self.assertNotEqual(self.key(),self.key(scope_hash='different'))
    def test_all_schema_local_references_resolve(self):
        sys.path.insert(0,str(ROOT/'tools'))
        from schema_support import ref_target
        def walk(node,path):
            if isinstance(node,dict):
                if '$ref' in node:ref_target(ROOT,path,node['$ref'])
                for value in node.values():walk(value,path)
            elif isinstance(node,list):
                for value in node:walk(value,path)
        for path in (ROOT/'contracts').glob('*.schema.json'):
            walk(json.loads(path.read_text()),path)

class SubjectTests(unittest.TestCase):
    def s(self,purpose='TASK_CONTENT',kind='result'):
        return dict(purpose=purpose,target={'kind':kind},owner_task_ref={'id':'t'},occurrence_id='o',completion_scope_ref={'id':'s'},method_instance_ref=None,input_manifest_hash='i',output_manifest_hash='out')
    def validate(self,s):
        from reference.semantics import validate_subject_shape
        validate_subject_shape(s)
    def test_real_result_shape(self):self.validate(self.s())
    def test_wrong_subject_kind(self):
        with self.assertRaises(ValueError):self.validate(self.s(kind='operation'))
    def test_missing_completion_scope(self):
        s=self.s();s['completion_scope_ref']=None
        with self.assertRaises(ValueError):self.validate(s)
    def test_composition_needs_method(self):
        with self.assertRaises(ValueError):self.validate(self.s('COMPOSITION','task'))
    def test_outcome_does_not_fake_output_file(self):
        s=self.s('OPERATION_OUTCOME','operation')
        with self.assertRaises(ValueError):self.validate(s)
        s['output_manifest_hash']=None;self.validate(s)
    def test_method_pre_adoption_shape(self):
        s=self.s('METHOD_PLAN','method');s.update(occurrence_id=None,completion_scope_ref=None,output_manifest_hash=None);self.validate(s)

if __name__=='__main__':unittest.main()

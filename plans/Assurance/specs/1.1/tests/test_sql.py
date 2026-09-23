from pathlib import Path
import json, sqlite3, subprocess, sys, tempfile, unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from reference.sql_statements import iter_sql_statements
DDL=(ROOT/'sql/assurance_additive.sql').read_text()
PARENT=(ROOT/'tests/parent_fixture.sql').read_text()
H='a'*64

def execute_script(c,script):
    for s in iter_sql_statements(script):c.execute(s)

def prepare(path=':memory:'):
    c=sqlite3.connect(path,isolation_level=None,timeout=.1)
    c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA journal_mode=WAL')
    c.execute('BEGIN IMMEDIATE');execute_script(c,PARENT);execute_script(c,DDL);c.commit()
    for m in ['m1','m2']:
        c.execute('INSERT INTO missions VALUES (?)',(m,))
        c.execute('INSERT INTO tasks VALUES (?,?)',('t'+m,m))
        c.execute('INSERT INTO commit_receipts VALUES (?)',('rc'+m,))
        c.execute('INSERT INTO dispatch_intents VALUES (?,?,?)',('i'+m,m,'PENDING'))
        c.execute('INSERT INTO requirements_revisions VALUES (?,?,?)',(m,1,H))
        c.execute('INSERT INTO review_packages VALUES (?,?)',('p'+m,m))
        c.execute('INSERT INTO goal_resolutions VALUES (?,?)',('r'+m,m))
        c.execute('INSERT INTO assurance_creation_contracts VALUES (?,?,?,?,?,?)',(m,'ASSURANCE_1_1','FACTORY',H,'rc'+m,1))
        c.execute('INSERT INTO assurance_mission_bindings VALUES (?,?,?,?,?,?)',(m,'assurance-exec-v1.1',H,'{}','rc'+m,1))
        c.execute('INSERT INTO events VALUES (?,?,?,?)',('reserve-'+m,m,'AssuranceReservationLinked','{}'))
    return c

def review(c,m='m1',p=None,job='j1',intent=None,task=None):
    c.execute('INSERT INTO assurance_review_bindings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (job,m,p or 'p'+m,task or 't'+m,'cmd-'+job,1,1,H,H,H,H,'{}','rc'+m,1))
    c.execute('INSERT INTO assurance_review_invocations VALUES (?,?,?,?,?,?,?,?)',
        (job,1,m,intent or 'i'+m,H,'{}','reserve-'+m,'rc'+m))

class SqlTests(unittest.TestCase):
    def setUp(self):self.c=prepare()
    def tearDown(self):self.c.close()
    def test_explicit_side_tables(self):
        names=[r[0] for r in self.c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'assurance_%'")]
        self.assertEqual(set(names), {'assurance_creation_contracts','assurance_mission_bindings','assurance_review_bindings','assurance_check_bindings','assurance_review_record_bindings','assurance_use_certificates','assurance_dependency_index','assurance_event_cursors','assurance_closeouts','assurance_blob_pins','assurance_criterion_policies','assurance_review_invocations','assurance_disclosure_batches','assurance_pending_work','assurance_environment_state'})
    def test_foreign_keys_integrity(self):
        self.assertEqual(list(self.c.execute('PRAGMA foreign_key_check')),[])
        self.assertEqual(self.c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
    def test_mission_binding_immutable(self):
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE assurance_mission_bindings SET policy_json='{}'")
    def test_mission_binding_no_delete(self):
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("DELETE FROM assurance_mission_bindings WHERE mission_id='m1'")
    def test_cross_mission_package(self):
        with self.assertRaises(sqlite3.IntegrityError):review(self.c,p='pm2')
    def test_cross_mission_intent(self):
        with self.assertRaises(sqlite3.IntegrityError):review(self.c,intent='im2')
    def test_cross_mission_owner(self):
        with self.assertRaises(sqlite3.IntegrityError):review(self.c,task='tm2')
    def test_review_unique_package(self):
        review(self.c)
        with self.assertRaises(sqlite3.IntegrityError):review(self.c,job='j2')
    def test_review_immutable(self):
        review(self.c)
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE assurance_review_bindings SET round_no=2")
    def test_review_record_binding_guard(self):
        review(self.c)
        self.c.execute('INSERT INTO review_records VALUES (?,?,?)',('rec','pm2','m2'))
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute('INSERT INTO assurance_review_record_bindings VALUES (?,?,?,?,?,?,?,?,?,?)',('rec','m1','j1',H,H,H,H,'{}','rcm1',1))
    def test_cert_invalid_json(self):
        with self.assertRaises(sqlite3.IntegrityError):self.cert(body='no')
    def cert(self,body='{}',expiry=200,cert='cert'):
        self.c.execute('INSERT INTO assurance_use_certificates VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (cert,'m1','task','tm1','ACCEPT','scope',H,H,body,100,expiry))
    def test_certificate_expired_diagnostic_allowed(self):self.cert(body='{"decision":"BLOCKED"}',expiry=90)
    def test_certificate_usable_expired_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):self.cert(body='{"decision":"USABLE"}',expiry=100)
    def test_certificate_dependency_cross_mission(self):
        self.cert()
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute('INSERT INTO assurance_dependency_index VALUES (?,?,?,?,?)',('m2','cert','OBJECT','x',H))
    def test_certificate_immutable(self):
        self.cert()
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE assurance_use_certificates SET not_after_ms=300")
    def test_cursor_cas(self):
        self.c.execute('INSERT INTO assurance_event_cursors VALUES (?,?,?,?,?)',('m1','REVIEW',0,1,1))
        q="UPDATE assurance_event_cursors SET last_event_seq=2,row_version=2 WHERE mission_id='m1' AND consumer='REVIEW' AND row_version=1"
        self.assertEqual(self.c.execute(q).rowcount,1);self.assertEqual(self.c.execute(q).rowcount,0)
    def test_cursor_cannot_go_back(self):
        self.c.execute('INSERT INTO assurance_event_cursors VALUES (?,?,?,?,?)',('m1','REVIEW',3,1,1))
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE assurance_event_cursors SET last_event_seq=2,row_version=2")
    def closeout(self,resolution='rm1'):
        self.c.execute('INSERT INTO assurance_closeouts VALUES (?,?,?,?,?,?,?,?)',('m1',resolution,'NOT_READY',1,H,'{}','rcm1',1))
        self.c.execute("UPDATE assurance_closeouts SET state='READY',row_version=2")
    def test_closeout_cross_mission(self):
        with self.assertRaises(sqlite3.IntegrityError):self.closeout('rm2')
    def test_finalized_not_reopened(self):
        self.closeout();self.c.execute("UPDATE assurance_closeouts SET state='FINALIZED',row_version=3")
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("UPDATE assurance_closeouts SET state='READY',row_version=4")
    def test_rollback_no_orphan(self):
        self.c.execute('BEGIN IMMEDIATE')
        review(self.c)
        self.c.rollback()
        self.assertEqual(self.c.execute('SELECT count(*) FROM assurance_review_bindings').fetchone()[0],0)
    def test_trigger_quoted_semicolon(self):
        script="CREATE TABLE x(v INTEGER); CREATE TRIGGER x_guard BEFORE INSERT ON x BEGIN SELECT CASE WHEN NEW.v<0 THEN RAISE(ABORT,'negative value; denied') END; END;"
        execute_script(self.c,script)
        with self.assertRaisesRegex(sqlite3.IntegrityError,'negative value; denied'):self.c.execute('INSERT INTO x VALUES (-1)')
    def test_trigger_ddl_rollback(self):
        self.c.execute('BEGIN IMMEDIATE')
        execute_script(self.c,"CREATE TABLE transient(v INTEGER); CREATE TRIGGER transient_t BEFORE INSERT ON transient BEGIN SELECT RAISE(ABORT,'x;y'); END;")
        self.c.rollback()
        self.assertIsNone(self.c.execute("SELECT name FROM sqlite_master WHERE name='transient'").fetchone())
    def test_incomplete_sql(self):
        with self.assertRaises(ValueError):list(iter_sql_statements("CREATE TRIGGER x BEFORE INSERT ON y BEGIN SELECT 1;"))
    def test_sql_quoted_and_comment_semicolons(self):
        out=list(iter_sql_statements("-- a;b\nSELECT 'a;b'; /* c;d */ SELECT 2;"))
        self.assertEqual(len(out),2)
        self.assertEqual(self.c.execute(out[0]).fetchone()[0],'a;b')

class PhysicalTests(unittest.TestCase):
    def test_two_connections_read_old_snapshot_then_cas(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.db';a=prepare(path)
            a.execute('INSERT INTO assurance_event_cursors VALUES (?,?,?,?,?)',('m1','VALIDITY',0,1,1))
            b=sqlite3.connect(path,isolation_level=None,timeout=.1)
            b.execute('BEGIN');self.assertEqual(b.execute('SELECT row_version FROM assurance_event_cursors').fetchone()[0],1)
            a.execute('BEGIN IMMEDIATE');a.execute('UPDATE assurance_event_cursors SET last_event_seq=1,row_version=2');a.commit()
            self.assertEqual(b.execute('SELECT row_version FROM assurance_event_cursors').fetchone()[0],1);b.rollback()
            self.assertEqual(b.execute('UPDATE assurance_event_cursors SET last_event_seq=2,row_version=2 WHERE row_version=1').rowcount,0)
            b.close();a.close()
    def crash(self,commit):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'test.db';c=prepare(p);c.close()
            code="""import sqlite3,sys,os
c=sqlite3.connect(sys.argv[1],isolation_level=None)
c.execute('PRAGMA foreign_keys=ON')
c.execute('BEGIN IMMEDIATE')
c.execute(\"INSERT INTO assurance_event_cursors VALUES ('m1','CLOSEOUT',1,1,1)\")
if sys.argv[2]=='yes': c.commit()
os._exit(77)
"""
            result=subprocess.run([sys.executable,'-c',code,str(p),'yes' if commit else 'no'],capture_output=True)
            self.assertEqual(result.returncode,77)
            c=sqlite3.connect(p);count=c.execute('SELECT count(*) FROM assurance_event_cursors').fetchone()[0]
            self.assertEqual(count,1 if commit else 0);self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok');c.close()
    def test_process_exit_before_commit(self):self.crash(False)
    def test_process_exit_after_commit(self):self.crash(True)

if __name__=='__main__':unittest.main()

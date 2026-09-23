from __future__ import annotations
import json,sqlite3,tempfile,subprocess,sys,unittest,hashlib
from pathlib import Path
from tests.test_rules import db,ROOT
from reference.runtime_rules import canonical,RuleError,sql_statements
from tools.verify_delivery import verify

class AddedSQLTests(unittest.TestCase):
 def test_protocol_immutable(self):
  c=db()
  with self.assertRaises(sqlite3.IntegrityError):c.execute("UPDATE arp_agent_protocols SET protocol='LEGACY_EXPLICIT'")
  with self.assertRaises(sqlite3.IntegrityError):c.execute('DELETE FROM arp_agent_protocols')
 def test_protocol_replace_cannot_bypass(self):
  c=db()
  with self.assertRaises(sqlite3.IntegrityError):c.execute("INSERT OR REPLACE INTO arp_agent_protocols VALUES('a','LEGACY_EXPLICIT','{}',?,1)",('1'*64,))
 def test_session_identity_pinned(self):
  c=db()
  with self.assertRaises(sqlite3.IntegrityError):c.execute("UPDATE arp_agent_sessions SET creation_key='other',row_version=2 WHERE session_id='s'")
 def test_policy_sequence(self):
  c=db()
  def ins(rev,key):c.execute("INSERT INTO arp_context_policy_adoptions VALUES('s',?, '{}',?, ?,?,'{}','{}',1)",(rev,'1'*64,key,'2'*64))
  with self.assertRaises(sqlite3.IntegrityError):ins(2,'c2')
  ins(1,'c1');ins(2,'c2')
  with self.assertRaises(sqlite3.IntegrityError):c.execute("UPDATE arp_context_policy_adoptions SET policy_body_hash=?",('3'*64,))
  self.assertEqual(c.execute("SELECT adoption_revision FROM arp_context_policy_adoptions ORDER BY adoption_revision DESC LIMIT 1").fetchone()[0],2)
 def test_protocol_required_before_session(self):
  c=db();c.execute("INSERT INTO base_agent_bindings_v1 VALUES('b')")
  with self.assertRaises(sqlite3.IntegrityError):
   c.execute("INSERT INTO arp_agent_sessions SELECT 's2','b',profile_id,profile_revision,profile_hash,creation_root_id,root_incarnation,'c2',create_command_hash,'sessions/s2',state,generation,row_version,journal_seq_from,sealed_highwater,destroy_command_id,destroy_command_hash,delete_proof_ref_json,created_at_ms,updated_at_ms FROM arp_agent_sessions")
 def test_two_connection_cas(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'state.db';c=db();c.commit();out=sqlite3.connect(p);c.backup(out);out.close();c.close()
   a=sqlite3.connect(p,timeout=.05);b=sqlite3.connect(p,timeout=.05)
   a.execute('BEGIN IMMEDIATE');a.execute("UPDATE arp_agent_sessions SET state='ACTIVE',row_version=2,updated_at_ms=2 WHERE session_id='s' AND row_version=1")
   with self.assertRaises(sqlite3.OperationalError):b.execute('BEGIN IMMEDIATE')
   a.commit();b.execute('BEGIN IMMEDIATE');n=b.execute("UPDATE arp_agent_sessions SET state='ACTIVE',row_version=2,updated_at_ms=2 WHERE session_id='s' AND row_version=1").rowcount;b.commit()
   self.assertEqual(n,0);a.close();b.close()
 def test_process_exit_before_and_after_commit(self):
  for committed in (False,True):
   with self.subTest(committed=committed),tempfile.TemporaryDirectory() as td:
    p=Path(td)/'state.db';c=db();c.commit();out=sqlite3.connect(p);c.backup(out);out.close();c.close()
    script="import sqlite3,os,sys;c=sqlite3.connect(sys.argv[1]);c.execute('BEGIN IMMEDIATE');c.execute(\"UPDATE arp_agent_sessions SET state='ACTIVE',row_version=2,updated_at_ms=2 WHERE session_id='s'\");"+('c.commit();' if committed else '')+'os._exit(41)'
    run=subprocess.run([sys.executable,'-B','-c',script,str(p)],timeout=5);self.assertEqual(run.returncode,41)
    c=sqlite3.connect(p);self.assertEqual(c.execute('SELECT state FROM arp_agent_sessions').fetchone()[0],'ACTIVE' if committed else 'CREATING');c.close()

class DeliveryTests(unittest.TestCase):
 def make(self,root):
  root.mkdir();(root/'x.txt').write_text('ok');(root/'DELIVERY-MANIFEST.json').write_text(json.dumps({'files':[{'path':'x.txt','size_bytes':2,'sha256':hashlib.sha256(b'ok').hexdigest()}]}))
 def test_normal(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'r';self.make(p);self.assertEqual(verify(p)['status'],'PASS')
 def test_symlink_alias_root(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'r';self.make(p);alias=Path(td)/'alias';alias.symlink_to(p,target_is_directory=True);self.assertEqual(verify(alias)['status'],'PASS')
 def test_manifest_symlink_escape(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'r';self.make(p);(Path(td)/'secret').write_text('ok');(p/'x.txt').unlink();(p/'x.txt').symlink_to(Path(td)/'secret');self.assertEqual(verify(p)['status'],'FAIL')
 def test_corruption(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'r';self.make(p);(p/'x.txt').write_text('no');self.assertEqual(verify(p)['status'],'FAIL')

if __name__=='__main__':unittest.main()

"""Minimal parent-key fixture. Never represents the full SDK schema or its runner."""
import sqlite3
from pathlib import Path
from reference.runtime_rules import sql_statements
ROOT=Path(__file__).resolve().parents[1]
def apply(conn,path):
    for statement in sql_statements(Path(path).read_text()):conn.execute(statement)
def execution(path=':memory:'):
    c=sqlite3.connect(path)
    c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA recursive_triggers=ON')
    c.execute('CREATE TABLE base_agent_bindings_v1(agent_id TEXT PRIMARY KEY)')
    c.execute('CREATE TABLE base_agent_turns_v1(turn_id TEXT PRIMARY KEY,agent_id TEXT REFERENCES base_agent_bindings_v1(agent_id))')
    apply(c,ROOT/'sql/execution_additive.sql')
    c.execute("INSERT INTO base_agent_bindings_v1 VALUES('a')")
    c.execute("INSERT INTO base_agent_turns_v1 VALUES('t','a')")
    c.execute("INSERT INTO arp_profiles VALUES('p',1,?,'{}','{}')",('1'*64,))
    c.execute("INSERT INTO arp_agent_protocols VALUES('a','ARP_V1_1','{}',?,0)",('1'*64,))
    c.execute("INSERT INTO arp_agent_sessions(session_id,agent_id,profile_id,profile_revision,profile_hash,creation_root_id,root_incarnation,creation_key,create_command_hash,relative_directory,state,generation,row_version,journal_seq_from,created_at_ms,updated_at_ms) VALUES('s','a','p',1,?,'root','root','create',?,'sessions/s','CREATING',1,1,1,1,1)",('1'*64,'2'*64))
    c.commit();return c

def partition(path=':memory:'):
    c=sqlite3.connect(path);c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA recursive_triggers=ON')
    apply(c,ROOT/'sql/session_partition.sql');c.commit();return c

def generation(c,g=1):
    c.execute('INSERT INTO index_generations VALUES(?,?,?,?,?,?,?)',(g,'a'*64,'b'*64,'c'*64,'BUILDING',1,None))
def group(c,g=1,key='g',source='d'*64,view='e'*64):
    if not c.execute('SELECT 1 FROM group_sources WHERE group_id=?',(key,)).fetchone():
        c.execute('INSERT INTO group_sources VALUES(?,?,?,?,?)',(key,source,1,1,'{}'))
    c.execute('INSERT INTO indexed_groups VALUES(?,?,?,?,?,?,?,?)',(g,key,source,view,1,0,1,'{}'))
def chunk(c,g=1,id='c1',text='你好早期记忆',seq=1):
    c.execute('INSERT INTO session_chunks(chunk_id,index_generation,group_id,record_id,utf8_start,utf8_end,source_hash,view_hash,chunker_fingerprint,text_view,provenance,validity_epoch,commit_seq)VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(id,g,'g','r',0,len(text.encode()),'d'*64,'e'*64,'b'*64,text,'USER_INPUT',0,seq))

def drain(c):
    import json,hashlib
    p=json.loads((ROOT/'examples/typed-fixtures.json').read_text())['fixtures']['PurgeProgress']
    raw=json.dumps(p,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    c.execute("UPDATE arp_agent_sessions SET state='DRAINING',generation=2,row_version=row_version+1,updated_at_ms=3,destroy_command_id='destroy',destroy_command_hash=?,purge_progress_json=?,purge_progress_hash=? WHERE session_id='s'",('3'*64,raw,hashlib.sha256(raw.encode()).hexdigest()))
    return p

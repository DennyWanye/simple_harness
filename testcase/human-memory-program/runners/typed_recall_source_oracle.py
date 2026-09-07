"""Source-lane state oracle. No SDK imports or SDK verifier calls.

SQLite capture is test-only and must never be used by the public lane.
"""
import hashlib
import json
import sqlite3
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def capture(path):
    # Existing DB only; read transaction includes schema, rows and diagnostic pragmas.
    # Do not use immutable=1: it can omit a live WAL.
    if not Path(path).is_file():raise ValueError('source capture requires existing DB')
    db = sqlite3.connect(str(Path(path).resolve()))
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        schema = [list(r) for r in db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
        tables = {}
        for kind, name, _, sql in schema:
            if kind != 'table':
                continue
            info = [list(r) for r in db.execute('PRAGMA table_info('+quote(name)+')')]
            columns = [r[1] for r in info]
            pk = [r[1] for r in sorted(info, key=lambda r:r[5]) if r[5]]
            fields = columns if pk else ['_source_rowid_', *columns]
            query = '*' if pk else 'rowid,*'
            rows = []
            for row in db.execute('SELECT '+query+' FROM '+quote(name)):
                values = [{'sqlite_blob_hex':v.hex()} if isinstance(v,bytes) else v for v in row]
                rows.append(dict(zip(fields,values,strict=True)))
            keys = pk or ['_source_rowid_']
            rows.sort(key=lambda r:canonical([r[k] for k in keys]))
            tables[name] = {'columns':info,'pk':keys,'rows':rows,'root_hash':digest(rows)}
        return {'schema':schema,'schema_hash':digest(schema),'tables':tables,
                'integrity':[r[0] for r in db.execute('PRAGMA integrity_check')],
                'foreign_keys':sorted([list(r) for r in db.execute('PRAGMA foreign_key_check')],key=canonical)}
    finally:
        db.close()


def rows(snapshot, table):
    return snapshot['tables'][table]['rows']


# Exact fresh-schema descriptor of the pinned candidate (identity, not a threshold).
# Lineage: M0.6.13 schema 51e4f27be1b89e789b013d4ef601ab7bcbfcf08ee8960bcb0d95b4e2bf76e733 /
# columns-PK 07a79a0bc9e997c46618e14b8f502b111456a18b627b5de32e3a6bfc805708d8 (fixture rev 8);
# M0.6.20/0.6.22 (52910b0c/67b176d0, 91 tables, fixture rev 9-10, 2026-09-07) schema
# 1be0e26ee2f257c3773a027518068d933af6d54722f93ba8761106deaf3e9d82 /
# columns-PK 5e4be2d6ba823284cd1e6ac739b9acb02b972f7e13e5c5606a8cf3ea2e3e27e3;
# M0.6.23/0.6.24 (78ddf386/3b51e0f6, memory schema 7.4, 94 tables incl. cognitive_vector_generations /
# cognitive_vectors / cognitive_vector_audit, fixture rev 11-12, 2026-09-07) below; 0.6.24 has no DDL
# change, recomputed with capture() on a 0.6.24 fresh root and identical to 0.6.23.
PINNED_SCHEMA_HASH = '9702ea1ecf969324d138418abafe1efafad6464d479b21c03ff1cd1ba7394aab'
PINNED_COLUMNS_PK_HASH = '7a5ad1d179a8eea7fe1dc39dc72f1a414e8ad3c77e799737abadec0d34cb4384'


def verify_snapshot(s):
    if s['schema_hash'] != PINNED_SCHEMA_HASH:
        raise ValueError('exact M0624 fresh schema descriptor differs')
    if digest({n:{k:t[k] for k in ('columns','pk')} for n,t in s['tables'].items()}) != PINNED_COLUMNS_PK_HASH:
        raise ValueError('exact M0624 table columns/PK inventory differs')
    if digest(s['schema']) != s['schema_hash']:
        raise ValueError('source schema hash differs')
    if set(s['tables']) != {r[1] for r in s['schema'] if r[0]=='table'}:
        raise ValueError('source table inventory incomplete')
    for name,t in s['tables'].items():
        keys=[canonical([r[k] for k in t['pk']]) for r in t['rows']]
        if len(set(keys))!=len(keys) or keys!=sorted(keys) or digest(t['rows'])!=t['root_hash']:
            raise ValueError('source PK/root differs: '+name)


def unchanged(a,b,except_tables=()):
    if a['schema'] != b['schema'] or set(a['tables']) != set(b['tables']):
        raise ValueError('source schema/table inventory changed')
    for name in a['tables']:
        if name not in except_tables and a['tables'][name]!=b['tables'][name]:
            raise ValueError('protected source table changed: '+name)
        if a['tables'][name]['columns']!=b['tables'][name]['columns'] or a['tables'][name]['pk']!=b['tables'][name]['pk']:
            raise ValueError('source PK/column schema changed: '+name)


def check_start(s, control, attempt_count):
    requests=rows(s,'typed_recall_requests'); attempts=rows(s,'typed_recall_attempts')
    if len(requests)!=1 or len(attempts)!=attempt_count:
        raise ValueError('request/attempt cardinality differs')
    request=requests[0]; wire=json.loads(request['request_json'])
    if set(wire)!={'schema_version','principal_id','context','plan'} or type(wire['schema_version']) is not int or wire['schema_version']!=1:
        raise ValueError('durable request exact schema/keys differ')
    if wire['context']!=control['context'] or wire['plan']!=control['plan']:
        raise ValueError('durable request not actual control input')
    if request['principal_id']!=wire['principal_id'] or request['idempotency_key']!=wire['plan']['idempotency_key']:
        raise ValueError('request PK/principal/idempotency differs')
    if request['deadline_at']!=request['created_at']+wire['plan']['budget']['deadline_ms']/1000:
        raise ValueError('request deadline differs')
    expected_hash=digest({'domain':'simple-harness-memory/typed-recall-request/v1','payload':{
        'harness_protocol':'recall-v4','memory_protocol':'typed-recall-v1','principal_id':wire['principal_id'],
        'context':wire['context'],'plan':wire['plan']}})
    if request['request_hash']!=expected_hash:raise ValueError('complete request hash differs')
    ordered=sorted(attempts,key=lambda a:a['attempt_ordinal'])
    for ordinal,a in enumerate(ordered,1):
        payload={k:a[k] for k in ('request_id','attempt_id','attempt_ordinal','started_at')}
        if a['request_id']!=request['request_id'] or a['attempt_ordinal']!=ordinal or a['started_at']!=request['created_at'] or digest(payload)!=a['attempt_hash']:
            raise ValueError('attempt PK/request/hash differs')
    return request, ordered


def check_terminal(s, control, attempts):
    request,history=check_start(s,control,attempts)
    execution=control['execution']; decision=execution['decision'];result=execution['result']
    ds=rows(s,'typed_recall_decisions');rs=rows(s,'typed_recall_results');ts=rows(s,'typed_recall_terminals')
    if len(ds)!=1 or len(rs)!=1 or len(ts)!=1:
        raise ValueError('complete terminal cardinality differs')
    d,r,t=ds[0],rs[0],ts[0]
    if json.loads(d['decision_json'])!=decision or json.loads(r['result_json'])!=result:
        raise ValueError('durable terminal bodies differ from independently checked control')
    if any(x['request_id']!=request['request_id'] for x in (d,r,t)):
        raise ValueError('terminal foreign request association')
    if d['decision_id']!=decision['decision_id'] or r['result_id']!=result['result_id'] or r['decision_id']!=d['decision_id']:
        raise ValueError('decision/result PK mismatch')
    if d['decision_hash']!=execution['decision_hash'] or r['result_hash']!=execution['result_hash']:
        raise ValueError('decision/result durable hash mismatch')
    if d['created_at']!=request['created_at'] or r['created_at']!=request['created_at']:
        raise ValueError('terminal header time differs')
    if any(r[k]!=result[k] for k in ('authority_epoch','policy_hash','authority_expires_at')):
        raise ValueError('result authority scalar differs')
    if t['attempt_id']!=history[-1]['attempt_id'] or t['decision_id']!=d['decision_id'] or t['result_id']!=r['result_id']:
        raise ValueError('terminal attempt/result foreign key mismatch')
    payload=json.loads(t['terminal_json'])
    expected={'request_id':request['request_id'],'attempt_id':history[-1]['attempt_id'],
        'terminal_kind':'completed','decision_id':d['decision_id'],'decision_hash':d['decision_hash'],
        'result_id':r['result_id'],'result_hash':r['result_hash'],'candidate_query_started':True,
        'candidate_query_count':1,'unsupported_capabilities':execution['unsupported_capabilities'],
        'degradation_codes':execution['degradation_codes'],'created_at':request['created_at']}
    if payload!=expected or digest(payload)!=t['terminal_hash']:
        raise ValueError('terminal full payload/hash mismatch')
    for key in ('terminal_kind','decision_hash','result_hash','candidate_query_count','created_at'):
        if t[key]!=expected[key]:raise ValueError('terminal scalar differs: '+key)
    if t['candidate_query_started']!=1 or json.loads(t['unsupported_capabilities_json'])!=expected['unsupported_capabilities'] or json.loads(t['degradation_codes_json'])!=expected['degradation_codes']:
        raise ValueError('terminal query/metadata differs')
    di=rows(s,'typed_recall_decision_items');ri=rows(s,'typed_recall_result_items')
    if len(di)!=2 or len(ri)!=2 or rows(s,'typed_recall_confirmation_groups') or rows(s,'typed_recall_confirmation_members'):
        raise ValueError('complete selected item cardinality differs')
    for ordinal,(item,dr,rr) in enumerate(zip(result['items'],sorted(di,key=lambda x:x['ordinal']),sorted(ri,key=lambda x:x['ordinal']),strict=True),1):
        selected=item['selected_item']
        if dr['decision_id']!=d['decision_id'] or rr['result_id']!=r['result_id'] or dr['ordinal']!=ordinal or rr['ordinal']!=ordinal or dr['item_kind']!='selected':
            raise ValueError('item PK/ordinal association differs')
        if json.loads(dr['item_json'])!=selected or json.loads(rr['result_item_json'])!=item or dr['item_id']!=selected['item_id'] or rr['item_id']!=selected['item_id']:
            raise ValueError('item body/identity differs')
        if dr['item_hash']!=digest({'domain':'simple-harness/recall-selected-item/v4','payload':selected}) or rr['result_item_hash']!=execution['result_item_hashes'][ordinal-1]:
            raise ValueError('item durable hash differs')


def check_fault_state(o, seam, final_tables):
    states=o['full_state'];before=states['before'];control=states['control_after'];immediate=states['immediate'];after=states['recovery_after']
    for s in states.values():
        verify_snapshot(s)
        if s['integrity']!=['ok'] or s['foreign_keys']:raise ValueError('source integrity/foreign keys failed')
    mutable=set(final_tables)|{'typed_recall_requests','typed_recall_attempts'}
    for s in (control,immediate,after):unchanged(before,s,mutable)
    if any(rows(before,t) for t in mutable):raise ValueError('source baseline recall state not empty')
    check_terminal(control,o['control'],1)
    pre=seam not in {'commit-before-ack','restart-open-rebuild'}
    if pre:
        unchanged(before,immediate,{'typed_recall_requests','typed_recall_attempts'})
        check_start(immediate,o['control'],1)
        if rows(immediate,'typed_recall_requests')!=rows(control,'typed_recall_requests') or rows(immediate,'typed_recall_attempts')!=rows(control,'typed_recall_attempts'):
            raise ValueError('precommit start differs from actual no-fault admission')
        check_terminal(after,o['control'],2)
        unchanged(control,after,{'typed_recall_attempts','typed_recall_terminals'})
        if rows(control,'typed_recall_attempts')[0] not in rows(after,'typed_recall_attempts'):
            raise ValueError('recovery replaced original attempt')
    else:
        unchanged(control,immediate)
        unchanged(immediate,after)
        check_terminal(after,o['control'],1)


def check_corruption_state(o, name, check_seed_authority):
    before=o['full_state']['before'];damaged=o['full_state']['damaged'];rejected=o['full_state']['rejected']
    for s in (before,damaged,rejected):verify_snapshot(s)
    unchanged(before,damaged,{'cognitive_conflict_members'})
    unchanged(damaged,rejected)
    if before['integrity']!=['ok'] or before['foreign_keys']:
        raise ValueError('corruption control is not healthy')
    fixture=json.loads((Path(__file__).parent.parent/'fixtures/typed-recall-v3.json').read_text())
    specification=next(r for r in fixture['conflict_write_oracle']['recall_cases'] if r['id']==name.rsplit('/',1)[1])
    if specification['expect']!='CONFLICT_GROUP_CORRUPT' or specification['reopen_outcome']!='FAIL_CLOSED':
        raise ValueError('frozen corruption semantic contract differs')
    if any(specification[k]!=0 for k in ('public_group_count','public_candidate_count','forbidden_canary_hits')):
        raise ValueError('frozen zero-disclosure contract differs')
    claims=fixture['conflict_write_oracle']['canonical_payloads']
    groups=rows(before,'cognitive_conflict_groups');members=rows(before,'cognitive_conflict_members')
    if len(groups)!=1 or len(members)!=2:raise ValueError('full conflict group/member cardinality differs')
    group=groups[0];members=sorted(members,key=lambda m:m['ordinal'])
    public=o['control']['execution']['decision']['confirmation_groups'][0]
    hashes=[]
    for ordinal,(m,public_member) in enumerate(zip(members,public['members'],strict=True),1):
        if m['group_id']!=group['group_id'] or m['principal_id']!=group['principal_id'] or m['memory_id']!=group['memory_id'] or m['ordinal']!=ordinal or m['revision']!=6+ordinal or m['role']!=('incumbent' if ordinal==1 else 'challenger'):
            raise ValueError('full member identity/role/revision differs')
        revisions=[r for r in rows(before,'cognitive_memory_revisions') if r['memory_id']==m['memory_id'] and r['revision']==m['revision']]
        if len(revisions)!=1 or revisions[0]['content_hash']!=m['content_hash'] or public_member['source_content_hash']!=m['content_hash'] or public_member['source_ref']!=m['memory_id'] or public_member['source_revision']!=m['revision']:
            raise ValueError('member revision/public source binding differs')
        claim=claims['incumbent' if ordinal==1 else 'challenger']
        source={'memory_type':'semantic','semantic_kind':'claim','subject_entity':claim['subject_entity'],'predicate':claim['predicate'],
                'object_value':claim['object_value'],'object_value_hash':digest(claim['object_value']),'qualifiers':[]}
        if json.loads(revisions[0]['content_json'])!=source or m['content_hash']!=digest(source):
            raise ValueError('original incumbent/challenger content differs')
        spans=sorted([r for r in rows(before,'cognitive_evidence_spans') if r['memory_id']==m['memory_id'] and r['revision']==m['revision']],key=lambda r:r['ordinal'])
        expected_ids=fixture['conflict_write_oracle']['create_case'][('incumbent' if ordinal==1 else 'challenger')+'_evidence_ids']
        if [r['evidence_id'] for r in spans]!=expected_ids:
            raise ValueError('original distinct member evidence IDs differ')
        if len(o['sources'])!=8:raise ValueError('original revision1..8 mutation history missing')
        source_index=5+ordinal
        record=o['sources'][source_index]
        if record['receipt']['operations'][0]['revision']!=m['revision'] or record['evidence_id']!=expected_ids[0]:
            raise ValueError('original mutation revision/evidence differs')
        check_seed_authority(o,{'seed':{'memory_type':'semantic','payload':{**claim,'qualifiers':[]}}},source,
                             source_index=source_index,check_recall_refs=False)
        event=next(e for e in o['calls'] if e['call']=='apply_memory_mutation_plan' and e['plan']['plan_id']==record['receipt']['plan_id'])
        wire_spans=event['plan']['operations'][0]['evidence_spans']
        if len(wire_spans)!=len(spans):raise ValueError('original mutation span membership differs')
        aliases={'evidence_item_ordinal':'item_ordinal','evidence_item_id':'item_id',
                 'evidence_item_json_pointer':'item_json_pointer','byte_start':'start_byte','byte_end':'end_byte'}
        for stored,span in zip(spans,wire_spans,strict=True):
            for key,value in stored.items():
                if key in {'memory_id','revision','ordinal'}:continue
                expected=None if key.startswith('observation_') else span[aliases.get(key,key)]
                if value!=expected:raise ValueError('durable member span differs from admitted mutation input: '+key)
            admitted=next(e for e in o['calls'] if e['call']=='ingest_committed_evidence' and e['evidence_id']==stored['evidence_id'])
            durable=[e for e in rows(before,'evidence_envelopes') if e['evidence_id']==stored['evidence_id']]
            if len(durable)!=1 or durable[0]['envelope_hash']!=admitted['envelope_hash'] or json.loads(durable[0]['sanitized_payload'])!=admitted['envelope']['sanitized_payload']:
                raise ValueError('member evidence not actual durable admitted envelope')
        manifest=[{k:v for k,v in r.items() if k not in {'memory_id','revision'}} for r in spans]
        if not spans or m['evidence_set_hash']!=digest(manifest):raise ValueError('member complete evidence-set hash differs')
        payload={k:v for k,v in m.items() if k!='member_hash'}
        if m['member_hash']!=digest(payload):raise ValueError('full member hash differs')
        hashes.append(m['member_hash'])
    payload={k:v for k,v in group.items() if k!='group_hash'};payload['member_hashes']=hashes
    if digest(payload)!=group['group_hash'] or public['conflict_group_hash']!=group['group_hash'] or public['conflict_group_id']!=group['group_id']:
        raise ValueError('full ordered group hash differs')
    expected=[dict(m) for m in members]
    if 'one-member' in name:
        expected=expected[:1]
        exception={'type':'MemoryCorruptionError','reason':'conflict member cardinality differs'}
        function='_validate_cognitive_conflict_integrity_unlocked'
        if damaged['integrity']!=['ok'] or damaged['foreign_keys']:raise ValueError('one-member must reach canonical integrity')
    elif 'cross-memory' in name:
        expected[1]['memory_id']='mem-other'
        exception={'type':'MemoryLegacySchemaUnsupported','reason':'LEGACY_SCHEMA_UNSUPPORTED'}
        function='probe_existing_root'
        if not damaged['foreign_keys'] or any(row[0]!='cognitive_conflict_members' for row in damaged['foreign_keys']):raise ValueError('cross-memory FK failure missing')
    else:
        expected.append({**expected[-1],'ordinal':3,'role':'extra','revision':9,'member_hash':'f'*64})
        exception={'type':'MemoryLegacySchemaUnsupported','reason':'LEGACY_SCHEMA_UNSUPPORTED'}
        function='probe_existing_root'
        if not any('cognitive_conflict_members' in message for message in damaged['integrity']):raise ValueError('three-member CHECK failure missing')
        if not damaged['foreign_keys'] or any(row[0]!='cognitive_conflict_members' for row in damaged['foreign_keys']):raise ValueError('three-member FK failure missing')
    if sorted(rows(damaged,'cognitive_conflict_members'),key=lambda m:m['ordinal'])!=expected:
        raise ValueError('actual corruption differs from prescribed one change')
    if o.get('phase')!='initialize_reopen' or o.get('exception')!=exception or 'recalled' in o or o.get('recall_calls')!=0:
        raise ValueError('exact reopen rejection/call boundary differs')
    frames=o.get('exception_frames',[])
    if not any(frame['function']==function for frame in frames):
        raise ValueError('rejection did not originate at expected SDK layer')
    if function=='probe_existing_root':
        # Exact corruption cause raised by the pinned candidate when the tampered root is
        # probed. Lineage: M0.6.13 'human-memory v7 foreign key check failed'; M0.6.20
        # (52910b0c) rejects earlier at the settlement schema integrity check; M0.6.23/0.6.24
        # (78ddf386/3b51e0f6, schema 7.4) reject at the cognitive-vector forward integrity check.
        causes={'human-memory v7 foreign key check failed','settlement_schema_integrity_differs',
                'cognitive_vector_schema_integrity_differs'}
        got=o.get('exception_cause')
        if not (isinstance(got,dict) and got.get('type')=='MemoryCorruptionError' and got.get('reason') in causes):
            raise ValueError('schema probe rejected for unrelated cause: '+repr(got))

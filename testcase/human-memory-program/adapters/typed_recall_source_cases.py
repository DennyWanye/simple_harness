"""Source-only fault execution, never copied into the public consumer import path."""
import importlib.util
import json
import shutil
import sqlite3
import zipfile
import traceback
from pathlib import Path


def load(path):
    spec=importlib.util.spec_from_file_location(path.stem,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


async def run(request,workspace):
    selected=request.get('selected_cells',request['cell_ids'])
    rows=await execute_source({**request,'cell_ids':selected},workspace)
    return rows+[dict(cell_id=name,status='BLOCKED',reason='CELL_NOT_SELECTED_THIS_BATCH',observations={})
        for name in request['cell_ids'] if name not in selected]


async def execute_source(request,workspace):
    if request['layer']!='source':raise ValueError('source adapter cannot execute public cells')
    # Installed private source must byte-match both the exact wheel and clean git checkout.
    checkout=Path(request['source_identity']['checkout'])/'src'
    with zipfile.ZipFile(request['candidate_identity']['memory']['wheel_path']) as archive:
        for name in archive.namelist():
            if name.startswith('simple_harness_memory/') and name.endswith('.py'):
                if (checkout/name).read_bytes()!=archive.read(name):raise ValueError('source/wheel implementation bytes differ')
    from simple_harness_memory.backends.sqlite_v5 import SQLiteHumanMemoryBackend
    import simple_harness_memory as m
    helpers=load(Path(__file__).with_name('typed_recall_case_manager.py'))
    oracle=load(Path(__file__).parent.parent/'runners/typed_recall_a2_oracle.py')
    now=1788170400.0
    backends={}
    async def factory(path,kwargs):
        backend=SQLiteHumanMemoryBackend(path,now=lambda:now,**kwargs)
        try:
            await backend.initialize()
        except BaseException:
            await backend.close()
            raise
        backends[str(path)]=backend
        return m.MemoryManager(backend,None)
    cells=[]
    if any(name.startswith('fault-recovery/') for name in request['cell_ids']):
        cells += await fault_cases(request,workspace,factory,helpers,oracle,backends,now)
    if any(name.startswith('conflict-state/') for name in request['cell_ids']):
        cells += await corruption_cases(request,workspace,factory,helpers,oracle)
    return cells


async def fault_cases(request,workspace,factory,helpers,oracle,backends,now):
    state=load(Path(__file__).parent.parent/'runners/typed_recall_source_oracle.py')
    def snapshot(path):
        with sqlite3.connect(path) as db:
            return {table:sorted([list(row) for row in db.execute('SELECT * FROM '+table)],key=helpers.canonical)
                for table in oracle.FINAL_TABLES}
    def copy_db(source,target):
        with sqlite3.connect(source) as source_db, sqlite3.connect(target) as target_db:
            source_db.backup(target_db)
    seed=helpers.CaseManager(workspace/'seed.sqlite',backend_factory=factory,now=now)
    await seed.open()
    payload={key:request['inputs']['claim'][key] for key in ('subject_entity','predicate','object_value','qualifiers')}
    try:
        await seed.seed({'memory_type':'semantic','payload':payload},evidence_id='source-control-311')
        await seed.seed({'memory_type':'semantic','payload':{**payload,'object_value':'3.12'}},
            evidence_id='source-control-312',operation_id='create-2')
        sources=seed.sources
        admitted=seed.admitted.copy()
    finally:await seed.close()
    async def reopen(path):
        case=helpers.CaseManager(path,backend_factory=factory,now=now)
        case.admitted.update(admitted)
        await case.open()
        return case
    params=dict(query=payload['predicate'],key='source-fault-recall')
    control_path=workspace/'control.sqlite';copy_db(seed.path,control_path)
    control_case=await reopen(control_path)
    try:control=await control_case.recall(**params)
    finally:await control_case.close()
    control_after=snapshot(control_path)
    full_control=state.capture(control_path)
    control_valid=False;control_error=None
    try:control_valid=oracle.check_two_source_control(control,sources)
    except (ValueError,KeyError,TypeError) as exc:control_error=str(exc)
    # Hit ordinals fixed to two-source control before any injection.
    mapping={'decision-header':('typed_recall.after_decision_header',1,'pre_commit'),
        'between-decision-items':('typed_recall.after_decision_item',1,'pre_commit'),
        'before-result-header':('typed_recall.after_decision_item',2,'pre_commit'),
        'between-result-items':('typed_recall.after_result_item',1,'pre_commit'),
        'before-terminal-fence':('typed_recall.after_result_item',2,'pre_commit'),
        'commit-before-ack':('typed_recall.after_commit',1,'post_commit_ack_loss')}
    cells=[]
    class InjectedFault(RuntimeError):pass
    for name in request['cell_ids']:
        if not name.startswith('fault-recovery/'):
            continue
        seam=name.split('fault:',1)[1]
        observed=dict(control=control,sources=sources,control_business_valid=control_valid,
            control_error=control_error,control_after=control_after,calls=[])
        if not control_valid:
            cells.append(dict(cell_id=name,status='OBSERVED',reason='',observations=observed));continue
        path=workspace/(seam+'.sqlite');copy_db(seed.path,path)
        observed['before']=snapshot(path)
        observed['full_state']={'before':state.capture(path),'control_after':full_control}
        case=await reopen(path);hits=[]
        if seam!='restart-open-rebuild':
            point,ordinal,phase=mapping[seam];observed.update(fault_point=point,fault_ordinal=ordinal,phase=phase)
            def inject(actual):
                hits.append(actual)
                if actual==point and hits.count(point)==ordinal:raise InjectedFault(point)
            backends[str(path)]._fault_injector=inject
        try:
            observed['calls'].append('execute_typed_recall:fault' if seam!='restart-open-rebuild' else 'execute_typed_recall:no_fault')
            observed['execution']=await case.recall(**params)
        except Exception as exc:observed['exception']=dict(type=type(exc).__name__,reason=str(exc))
        finally:
            observed['hits']=hits
            observed['immediate']=snapshot(path)
            observed['full_state']['immediate']=state.capture(path)
            await case.close()
        case=await reopen(path)
        try:
            observed['calls'].append('reopen:execute_typed_recall')
            observed['recovery']=await case.recall(**params)
            observed['recovery_after']=snapshot(path)
            observed['full_state']['recovery_after']=state.capture(path)
        finally:await case.close()
        cells.append(dict(cell_id=name,status='OBSERVED',reason='',observations=observed))
    return cells


async def corruption_cases(request,workspace,factory,helpers,oracle):
    state=load(Path(__file__).parent.parent/'runners/typed_recall_source_oracle.py')
    conflicts=load(Path(__file__).with_name('typed_recall_conflict_cases.py'))
    import simple_harness as h
    payload={key:request['inputs']['claim'][key] for key in ('subject_entity','predicate','object_value','qualifiers')}
    seed=await helpers.CaseManager(workspace/'conflict-seed.sqlite',backend_factory=factory).open()
    try:
        op=await conflicts.seed_revision7(seed,payload)
        await conflicts.contest(seed,op,{**payload,'object_value':'3.12'})
        control=await seed.recall(query='preferred_python')
        value=control['execution']
        oracle.check_execution_wire(value,control['context'],control['plan'])
        group=value['decision']['confirmation_groups']
        if len(group)!=1 or [m['source_revision'] for m in group[0]['members']]!=[7,8] or value['result']['items']:
            raise ValueError('source corruption no-fault group business assertion failed')
        for member,object_value in zip(group[0]['members'],('3.11','3.12'),strict=True):
            if member['source_content_hash']!=oracle.hash_json(oracle.semantic_source(**{**payload,'object_value':object_value})):
                raise ValueError('source corruption no-fault source hash differs')
        admitted=seed.admitted.copy()
    finally:await seed.close()
    rows=[]
    for name in request['cell_ids']:
        if not name.startswith('conflict-state/'):continue
        path=workspace/(name.rsplit('/',1)[1]+'.sqlite')
        with sqlite3.connect(seed.path) as a,sqlite3.connect(path) as b:a.backup(b)
        full_before=state.capture(path)
        with sqlite3.connect(path) as db:
            db.execute('PRAGMA foreign_keys=OFF');db.execute('PRAGMA ignore_check_constraints=ON')
            triggers=list(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='cognitive_conflict_members'"))
            before=list(db.execute('SELECT * FROM cognitive_conflict_members ORDER BY ordinal'))
            for trigger,_ in triggers:db.execute('DROP TRIGGER '+trigger)
            if 'one-member' in name:db.execute('DELETE FROM cognitive_conflict_members WHERE ordinal=2')
            elif 'cross-memory' in name:db.execute("UPDATE cognitive_conflict_members SET memory_id='mem-other' WHERE ordinal=2")
            else:
                row=list(before[-1]);columns=[r[1] for r in db.execute('PRAGMA table_info(cognitive_conflict_members)')]
                for key,value in {'ordinal':3,'role':'extra','revision':9,'member_hash':'f'*64}.items():row[columns.index(key)]=value
                db.execute('INSERT INTO cognitive_conflict_members VALUES('+','.join('?' for _ in row)+')',row)
            for _,sql in triggers:db.execute(sql)
            after=list(db.execute('SELECT * FROM cognitive_conflict_members ORDER BY ordinal'))
            db.commit()
        observed=dict(corruption=True,control=control,before_members=before,after_members=after,calls=['source_corrupt_members','initialize_reopen'])
        observed['full_state']={'before':full_before,'damaged':state.capture(path)}
        observed.update(phase='initialize_reopen',recall_calls=0)
        case=helpers.CaseManager(path,backend_factory=factory);case.admitted.update(admitted)
        try:
            await case.open()
            observed.update(phase='recall',recall_calls=1)
            observed['recalled']=await case.recall(query='preferred_python')
        except Exception as exc:
            observed['exception']=dict(type=type(exc).__name__,reason=str(exc))
            observed['exception_frames']=[{'file':Path(f.filename).name,'function':f.name} for f in traceback.extract_tb(exc.__traceback__)]
            if exc.__cause__ is not None:
                observed['exception_cause']=dict(type=type(exc.__cause__).__name__,reason=str(exc.__cause__))
        finally:
            if getattr(case,'manager',None) is not None:
                await case.close()
            observed['full_state']['rejected']=state.capture(path)
        rows.append(dict(cell_id=name,status='OBSERVED',reason='',observations=observed))
    return rows

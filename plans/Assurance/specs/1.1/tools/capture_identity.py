#!/usr/bin/env python3
"""Read-only source inventory. Does not import SDK, install deps, or grant any gate."""
from pathlib import Path
import argparse, ast, hashlib, json, subprocess

def sha(b):return hashlib.sha256(b).hexdigest()
def git(root,*args):
    p=subprocess.run(['git','-C',str(root),*args],capture_output=True,check=True)
    return p.stdout

def inventory(root):
    head=git(root,'rev-parse','HEAD').decode().strip()
    branch=git(root,'symbolic-ref','--short','-q','HEAD').decode().strip()
    index=git(root,'ls-files','--stage','-z')
    dirty=git(root,'status','--porcelain=v1','-z','--untracked-files=all')
    hashes={}
    controlled=['src','tests','scripts','schemas','backend','tauri-app','ARCHITECTURE']
    for name in controlled:
        base=root/name
        if base.is_symlink():
            hashes[name]={'kind':'symlink_root','target_sha256':sha(str(base.readlink()).encode())}
            continue
        if not base.exists():continue
        for p in sorted(base.rglob('*')):
            if any(x in ('.venv','.git','__pycache__','.local-test-evidence','.pytest_cache') for x in p.relative_to(root).parts):continue
            if p.is_symlink():
                hashes[p.relative_to(root).as_posix()]={'kind':'symlink','target_sha256':sha(str(p.readlink()).encode())}
            elif p.is_file():hashes[p.relative_to(root).as_posix()]={'kind':'file','sha256':sha(p.read_bytes())}
    for name in ('pyproject.toml','uv.lock','pytest.ini','mypy.ini','ruff.toml'):
        p=root/name
        if p.is_file() and not p.is_symlink():hashes[name]={'kind':'file','sha256':sha(p.read_bytes())}
    result={'root':str(root),'head':head,'branch':branch,'index_digest':sha(index),'dirty_status_sha256':sha(dirty),
            'dirty_status_z':dirty.decode(errors='surrogateescape').split('\x00'),'controlled_files':hashes}
    result['source_fingerprint']=sha(json.dumps(result,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode())
    return result

def symbols(path):
    tree=ast.parse(path.read_text(encoding='utf-8'))
    found=[]
    def visit(body,prefix=''):
        for n in body:
            if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                q=prefix+n.name
                found.append({'qualified':q,'line':n.lineno,'end_line':n.end_lineno,'kind':type(n).__name__})
                visit(n.body,q+'.')
    visit(tree.body);return found

def main():
    a=argparse.ArgumentParser();a.add_argument('--sdk',required=True);a.add_argument('--host',required=True);a.add_argument('--map',required=True);a.add_argument('--out',required=True);ns=a.parse_args()
    sdk=Path(ns.sdk).expanduser().resolve(strict=True);host=Path(ns.host).expanduser().resolve(strict=True);out=Path(ns.out).expanduser().resolve()
    if out.exists():raise SystemExit('OUTPUT_EXISTS: use a new evidence run')
    if not out.is_relative_to(host/'.local-test-evidence'):raise SystemExit('OUTPUT_MUST_BE_IGNORED_HOST_EVIDENCE')
    p=subprocess.run(['git','-C',str(host),'check-ignore','-q',str(out)],capture_output=True)
    if p.returncode:raise SystemExit('EVIDENCE_NOT_IGNORED')
    seed=json.loads(Path(ns.map).read_text());before={'candidate':inventory(sdk),'host':inventory(host)}
    entries=[]
    for e in seed['entries']:
        x=dict(e);rel=e['target_path']
        if not rel:x.update(status='REQUIRES_LOCAL_DISCOVERY',actual_sha256=None)
        else:
            path=(sdk/rel).resolve()
            if not path.is_relative_to(sdk):x.update(status='PATH_OUTSIDE_SOURCE',actual_sha256=None)
            elif not path.is_file():x.update(status='ABSENT',actual_sha256=None)
            else:
                x.update(status='PRESENT_UNVERIFIED_BEHAVIOR',actual_sha256=sha(path.read_bytes()))
                try:x['symbols']=symbols(path)
                except (SyntaxError,UnicodeError) as ex:x['ast_error']=type(ex).__name__;x['status']='AST_UNREADABLE'
        entries.append(x)
    after={'candidate':inventory(sdk),'host':inventory(host)}
    stable=all(before[k]['source_fingerprint']==after[k]['source_fingerprint'] for k in before)
    result={'schema_version':1,'status':'CAPTURED_UNREVIEWED' if stable else 'SOURCE_CHANGED_DURING_CAPTURE',
            'identities':after,'entries':entries,'equivalences':[], 'source_map_complete':False,
            'independent_review':'NOT_RUN','production_allowed':False}
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=True,indent=2);f.write('\n')
    print(json.dumps({'status':result['status'],'candidate_fingerprint':after['candidate']['source_fingerprint'],'entries':len(entries)}))
    return 0 if stable else 3
if __name__=='__main__':raise SystemExit(main())

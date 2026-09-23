#!/usr/bin/env python3
"""Read-only bounded inventory. Does not claim full dirty source or behaviour coverage."""
from __future__ import annotations
import argparse,ast,hashlib,json,os,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def git(root,*args):
    r=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,env={**os.environ,'GIT_OPTIONAL_LOCKS':'0'},timeout=15)
    return {'returncode':r.returncode,'value':r.stdout.strip() if r.returncode==0 else None}
def symbols(p):
    if p.suffix!='.py':return []
    tree=ast.parse(p.read_text(encoding='utf8'));out=[]
    def visit(nodes,prefix=''):
        for n in nodes:
            if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                q=prefix+n.name;out.append({'qualified_symbol':q,'line':n.lineno,'end_line':n.end_lineno})
                if isinstance(n,ast.ClassDef):visit(n.body,q+'.')
    visit(tree.body);return out

def main():
    a=argparse.ArgumentParser();a.add_argument('--sdk',required=True);a.add_argument('--host',required=True);a.add_argument('--out',required=True);ns=a.parse_args()
    sdk=Path(ns.sdk).resolve(strict=True);host=Path(ns.host).resolve(strict=True);out=Path(ns.out)
    if out.exists():raise SystemExit('OUTPUT_EXISTS: new ignored path required')
    check=subprocess.run(['git','-C',str(host),'check-ignore','-q',str(out.resolve())],env={**os.environ,'GIT_OPTIONAL_LOCKS':'0'})
    if check.returncode:raise SystemExit('EVIDENCE_NOT_IGNORED')
    reported=json.loads((ROOT/'inputs/REPORTED-SOURCE-INDEX.json').read_text());requirements=json.loads((ROOT/'implementation/seams.json').read_text())
    wanted={(x['basis'],x['relative_path'])for x in reported['sources']}
    for x in requirements:wanted.add(('host'if x['expected_path'].startswith('backend/')else'htn_sdk',x['expected_path']))
    results=[]
    for basis,rel in sorted(wanted):
        root=host if basis=='host'else sdk;p=(root/rel).resolve()
        row={'basis':basis,'relative_path':rel,'status':'MISSING','sha256':None,'symbols':[]}
        if not p.is_relative_to(root):row['status']='PATH_ESCAPE'
        elif p.is_file():
            if p.stat().st_size>8*1024*1024:row['status']='SOURCE_TOO_LARGE'
            else:
                row.update(status='PRESENT_UNVERIFIED_BEHAVIOR',sha256=sha(p))
                try:row['symbols']=symbols(p)
                except (SyntaxError,UnicodeError):row['status']='PARSE_ERROR'
        results.append(row)
    body={'scope':'SELECTED_FILES_ONLY_NOT_FULL_DIRTY_FINGERPRINT','sdk':str(sdk),'host':str(host),'heads':{'sdk':git(sdk,'rev-parse','HEAD'),'host':git(host,'rev-parse','HEAD')},'status':{'sdk':git(sdk,'status','--porcelain=v1','--untracked-files=normal'),'host':git(host,'status','--porcelain=v1','--untracked-files=normal')},'sources':results,'reported_source_index_hash':sha(ROOT/'inputs/REPORTED-SOURCE-INDEX.json'),'code_execution':'NONE; AST only; no imports/test/model calls'}
    body['selected_file_fingerprint']=hashlib.sha256(json.dumps(results,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    out.mkdir(parents=True);(out/'native-source-inventory.json').write_text(json.dumps(body,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'output':str(out/'native-source-inventory.json'),'status':'CAPTURED_NOT_APPROVED','count':len(results)}))
if __name__=='__main__':main()

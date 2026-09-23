#!/usr/bin/env python3
from pathlib import Path, PurePosixPath
import argparse,hashlib,json

def verify(root):
    root=Path(root).resolve(strict=True)
    manifest=json.loads((root/'DELIVERY-MANIFEST.json').read_text())
    errors=[]
    declared=[item['path'] for item in manifest['files']]
    if len(declared)!=len(set(declared)): errors.append('duplicate manifest path')
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and not p.name.endswith('.pyc')}
    extras=actual-set(declared)-{'DELIVERY-MANIFEST.json'}
    errors.extend('undeclared:'+path for path in sorted(extras))
    for item in manifest['files']:
        rel=PurePosixPath(item['path'])
        if rel.is_absolute() or '..' in rel.parts:errors.append('unsafe path');continue
        p=(root/Path(*rel.parts)).resolve()
        if not p.is_relative_to(root) or not p.is_file():errors.append('missing/outside:'+str(rel));continue
        b=p.read_bytes()
        if len(b)!=item['size'] or hashlib.sha256(b).hexdigest()!=item['sha256']:errors.append('mismatch:'+str(rel))
    return errors

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);a=p.parse_args()
    errors=verify(a.root)
    print(json.dumps({'scope':'DELIVERY_ONLY','status':'PASS' if not errors else 'FAIL','errors':errors,'sdk_verified':False},ensure_ascii=False))
    return 0 if not errors else 3
if __name__=='__main__':raise SystemExit(main())

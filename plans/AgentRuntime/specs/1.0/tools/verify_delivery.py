"""Read-only complete-package verification; never grants any SDK gate."""
from pathlib import Path
import argparse,hashlib,json,sys

def verify(root:Path)->dict:
    root=root.resolve(strict=True)
    m=json.loads((root/'DELIVERY-MANIFEST.json').read_text(encoding='utf8'))
    errors=[];seen=set()
    for item in m['files']:
        rel=item['path']
        if rel in seen:errors.append('duplicate:'+rel);continue
        seen.add(rel)
        target=(root/rel).resolve(strict=True) if (root/rel).exists() else None
        if target is None:errors.append('missing:'+rel);continue
        if not target.is_relative_to(root) or (root/rel).is_symlink() or not target.is_file():
            errors.append('unsafe:'+rel);continue
        data=target.read_bytes()
        if len(data)!=item['size_bytes'] or hashlib.sha256(data).hexdigest()!=item['sha256']:
            errors.append('mismatch:'+rel)
    extras=[p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name!='DELIVERY-MANIFEST.json' and '__pycache__' not in p.parts and p.suffix!='.pyc' and p.relative_to(root).as_posix() not in seen]
    errors+=['unlisted:'+x for x in extras]
    return {'scope':'DELIVERY_FILES_ONLY','status':'PASS' if not errors else 'FAIL','files_checked':len(seen),'errors':errors,'sdk_verified':False}
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);n=a.parse_args()
    try:r=verify(n.root)
    except (OSError,ValueError,KeyError) as e:r={'status':'FAIL','error':str(e),'sdk_verified':False}
    print(json.dumps(r,ensure_ascii=False,indent=2));sys.exit(0 if r['status']=='PASS' else 3)

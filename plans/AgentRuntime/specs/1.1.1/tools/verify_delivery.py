#!/usr/bin/env python3
import argparse,hashlib,json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',default=str(Path(__file__).resolve().parents[1]));a=ap.parse_args();root=Path(a.root).resolve(strict=True)
    manifest=json.loads((root/'DELIVERY-MANIFEST.json').read_text());bad=[]
    for item in manifest['files']:
        p=(root/item['path']).resolve()
        if not p.is_relative_to(root)or not p.is_file():bad.append({'path':item['path'],'reason':'MISSING_OR_ESCAPE'});continue
        content=p.read_bytes()
        if len(content)!=item['bytes']or hashlib.sha256(content).hexdigest()!=item['sha256']:bad.append({'path':item['path'],'reason':'CONTENT_MISMATCH'})
    print(json.dumps({'check':'DELIVERY_INTEGRITY','status':'FAIL'if bad else'PASS','files':len(manifest['files']),'problems':bad},ensure_ascii=False))
    raise SystemExit(2 if bad else 0)
if __name__=='__main__':main()

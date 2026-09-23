#!/usr/bin/env python3
"""Read-only preparation receipt for an already-created isolated Host candidate.
Does NOT clone, install, build, launch, inspect secrets, or mark native tests passed.
"""
from pathlib import Path
import argparse,hashlib,json,socket,zipfile
FIELDS={'schema_version','isolated_host_root','shared_host_root','isolated_venv','userdata','evidence_root','wheel','sdk_source_fingerprint','host_source_fingerprint','lock_path','tauri_package_json','backend_port','vite_port'}

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def prepare(config):
    if set(config)!=FIELDS or type(config['schema_version']) is not int or config['schema_version']!=1:raise ValueError('NATIVE_MANIFEST_FIELDS')
    paths={k:Path(config[k]).expanduser().resolve(strict=True) for k in ['isolated_host_root','shared_host_root','isolated_venv','userdata','evidence_root','wheel','lock_path','tauri_package_json']}
    root=paths['isolated_host_root'];shared=paths['shared_host_root'];evidence=paths['evidence_root']
    if root==shared or root.is_relative_to(shared) and not root.is_relative_to(evidence):raise ValueError('SHARED_HOST_TARGET')
    if not root.is_relative_to(evidence) or not paths['userdata'].is_relative_to(evidence):raise ValueError('NOT_ISOLATED_EVIDENCE')
    for k in ['isolated_venv','lock_path','tauri_package_json']:
        if not paths[k].is_relative_to(root):raise ValueError('CANDIDATE_PATH_ESCAPE:'+k)
    if not paths['wheel'].is_file() or not zipfile.is_zipfile(paths['wheel']):raise ValueError('INVALID_WHEEL')
    for k in ['sdk_source_fingerprint','host_source_fingerprint']:
        if not isinstance(config[k],str) or len(config[k])!=64 or any(c not in '0123456789abcdef' for c in config[k]):raise ValueError('BAD_FINGERPRINT')
    ports=[config['backend_port'],config['vite_port']]
    if len(set(ports))!=2 or any(type(x)is not int or not 1024<=x<=65535 for x in ports):raise ValueError('BAD_PORTS')
    # Probe availability only; release immediately, so startup must also detect a race.
    for port in ports:
        with socket.socket() as sock:
            try:sock.bind(('127.0.0.1',port))
            except OSError as e:raise ValueError('PORT_UNAVAILABLE:'+str(port)) from e
    pkg=json.loads(paths['tauri_package_json'].read_text())
    if not any('tauri' in x for x in pkg.get('scripts',{}).values()):raise ValueError('TAURI_SCRIPT_UNRESOLVED')
    return {'scope':'NATIVE_PREPARATION_ONLY','status':'PREPARED_NOT_LAUNCHED','config':config,'physical_paths':{k:str(p) for k,p in paths.items()},'wheel_sha256':digest(paths['wheel']),'lock_sha256':digest(paths['lock_path']),'package_json_sha256':digest(paths['tauri_package_json']),'runtime_import_verified':False,'native_ui_verified':False,'source_hashes_user_supplied_pending_runtime_match':True}

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    target=Path(a.out)
    if target.exists():raise SystemExit('OUTPUT_EXISTS')
    result=prepare(json.loads(Path(a.config).read_text()));target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'native_ui_verified':False}))
if __name__=='__main__':main()

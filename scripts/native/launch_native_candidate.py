"""Launch an already-built native verify bundle with isolated userdata and process-only credentials.

Adapted 2026-09-07 for this Mac from the other Mac's launch_current_native.py. Tauri owns
its backend; run this only under scripts/run_resource_bounded.py so the whole process group
is measured and cleaned. Raw evidence stays under the ignored evidence directory.
"""
from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import plistlib
import re
import selectors
import socket
import subprocess
import tempfile
from pathlib import Path

from dotenv import dotenv_values
import tomlkit


def relay_ready(base_url: str, key: str, model: str) -> dict:
    """Host-shaped probe (system + tools) so relay quirks match the product's requests."""
    import httpx
    body = {"model": model, "max_tokens": 16,
            "messages": [{"role": "system", "content": "你是助手。"}, {"role": "user", "content": "只回答数字：1+1=?"}],
            "tools": [{"type": "function", "function": {"name": "context_route", "description": "route",
                       "parameters": {"type": "object", "properties": {"decision": {"type": "string"}}, "required": ["decision"]}}}],
            "tool_choice": "none"}
    try:
        response = httpx.post(base_url.rstrip("/") + "/chat/completions", json=body, timeout=90,
                              headers={"Authorization": "Bearer " + key})
        return {"model": model, "status": response.status_code, "ready": response.status_code == 200}
    except Exception as error:
        return {"model": model, "status": None, "ready": False, "error": type(error).__name__}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--userdata', type=Path)
    parser.add_argument('--installed-target', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18120)
    parser.add_argument('--python', type=Path)
    parser.add_argument('--model', default=None, help='default: config.toml [llm] model')
    parser.add_argument('--env-file', type=Path, default=None, help='BASEURL/APIKEY file for the primary provider (default: <source>/../simple_harness/.env); use with --model to run a journey on another relay, recorded as provider_kind=explicit')
    parser.add_argument('--fallback-model', default=None, help='used (and recorded) only when --model fails preflight')
    parser.add_argument('--fallback-env-file', type=Path, default=None, help='BASEURL/APIKEY for the fallback provider (else the primary credential file)')
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--memory-probe', action='store_true',
                        help='event X-3: turn on the backend site-level memory probe (memory.probe lines in native.log + tracemalloc snapshots under <userdata>/memory-probe/)')
    parser.add_argument('--memory-probe-every', type=int, default=1, metavar='N',
                        help='emit one memory.probe line every N foreground Run terminals (default 1); only meaningful with --memory-probe')
    parser.add_argument('--headroom-mib', type=int, default=7168)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    installed = args.installed_target.resolve(strict=True)
    assert (installed / 'simple_harness').is_dir()
    python = (args.python or source / 'backend/.venv/bin/python').absolute()
    bundle = args.bundle.resolve(strict=True)
    with (bundle / 'Contents/Info.plist').open('rb') as handle:
        executable = plistlib.load(handle)['CFBundleExecutable']
    binary = bundle / 'Contents/MacOS' / executable
    assert binary.is_file() and (source / 'backend/main.py').is_file()
    model_dir = Path.home() / 'Library/Application Support/com.dennywanye.simpleharness/models/wemm-embedding-2b'
    assert (model_dir / 'config.json').is_file(), 'WeMM snapshot missing'
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    process_rows = subprocess.check_output(['ps', '-axo', 'pid=,comm='], text=True)
    existing = [int(row.strip().split(None, 1)[0]) for row in process_rows.splitlines()
                if '/Contents/MacOS/simple-harness' in row]
    if existing:
        raise RuntimeError(f'native_test_already_running:{existing}')
    vm = subprocess.check_output(['vm_stat'], text=True)
    page_size = int(re.search(r'page size of (\d+) bytes', vm).group(1))
    page_counts = dict((key.strip(), int(value)) for key, value in re.findall(r'^([^:\n]+):\s+(\d+)\.', vm, re.MULTILINE))
    available = sum(page_counts.get(key, 0) for key in ('Pages free', 'Pages inactive', 'Pages speculative')) * page_size
    if available < args.headroom_mib * 1024 ** 2:
        raise RuntimeError(f'native_test_memory_headroom_low:{available // (1024 ** 2)}MiB')
    values = dotenv_values(args.env_file if args.env_file else source.parent / 'simple_harness' / '.env', interpolate=False)
    assert values.get('APIKEY') and values.get('BASEURL'), 'Host process credential configuration missing'
    config = tomlkit.parse((source / 'config.toml').read_text())
    model = args.model or str(config['llm']['model'])
    preflight = [relay_ready(values['BASEURL'], values['APIKEY'], model)]
    provider_kind = 'explicit' if args.env_file else 'primary'
    if not preflight[0]['ready'] and args.fallback_model:
        fallback_values = dotenv_values(args.fallback_env_file, interpolate=False) if args.fallback_env_file else values
        preflight.append({**relay_ready(fallback_values['BASEURL'], fallback_values['APIKEY'], args.fallback_model),
                          'env_file': str(args.fallback_env_file) if args.fallback_env_file else None})
        if preflight[1]['ready']:
            model, values, provider_kind = args.fallback_model, fallback_values, 'fallback'
    if not preflight[-1]['ready']:
        raise RuntimeError('native_relay_unavailable:' + json.dumps(preflight))
    args.evidence_root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='primary-ui-', dir=args.evidence_root)).resolve()
    user = args.userdata.resolve(strict=True) if args.userdata else run / 'userdata'
    user.mkdir(exist_ok=True)
    models = run / 'models'
    models.mkdir()
    (models / model_dir.name).symlink_to(model_dir, target_is_directory=True)

    def clear_credentials(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if re.fullmatch(r'(?i)(api_?key|access_token|refresh_token|password|secret|token)', str(key)):
                    value[key] = ''
                else:
                    clear_credentials(item)
        elif isinstance(value, list):
            for item in value:
                clear_credentials(item)

    clear_credentials(config)
    config['backend']['port'] = args.port
    config['llm'].update(model=model, base_url=values['BASEURL'], api_key='', max_tokens=6144)
    config_path = run / 'config.toml'
    config_path.write_text(tomlkit.dumps(config))
    if not args.userdata:
        (user / 'llm_runtime.json').write_text(json.dumps({'base_url': values['BASEURL'], 'model': model, 'max_tokens': 6144}))
    env = {key: value for key, value in os.environ.items() if not key.startswith('DESKPET_')}
    env.update(
        DESKPET_BACKEND_DIR=str(source / 'backend'), DESKPET_PYTHON=str(python),
        DESKPET_BACKEND_PORT=str(args.port), DESKPET_USER_DATA_DIR=str(user),
        DESKPET_USER_LOG_DIR=str(run / 'logs'), DESKPET_USER_CACHE_DIR=str(run / 'cache'),
        DESKPET_MODEL_ROOT=str(models), DESKPET_CONFIG=str(config_path),
        DESKPET_CLOUD_API_KEY=values['APIKEY'], DESKPET_DEV_MODE='0',
        DESKPET_DEEPRESEARCH_DIR=str(run / 'deepresearch'),
        DESKPET_MODEL_CDN_BASE=(run / 'optional-asr-unconfigured').as_uri(),
        PYTHONPATH=os.pathsep.join((str(source / 'backend'), str(installed))), PYTHONDONTWRITEBYTECODE='1',
        HF_HOME=str(run / 'cache/hf'), HF_MODULES_CACHE=str(run / 'cache/hf-modules'),
        TORCH_HOME=str(run / 'cache/torch'), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
    )
    # Event X-3: the probe is opt-in and must be deterministic per run, so the
    # inherited value is always dropped and only re-set when asked for.
    for name in ('SIMPLEHARNESS_MEMORY_PROBE', 'SIMPLEHARNESS_MEMORY_PROBE_EVERY'):
        env.pop(name, None)
    if args.memory_probe:
        env['SIMPLEHARNESS_MEMORY_PROBE'] = '1'
        env['SIMPLEHARNESS_MEMORY_PROBE_EVERY'] = str(max(1, args.memory_probe_every))
    metadata = {
        'source': str(source), 'source_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip(),
        'python': str(python), 'installed_target': str(installed), 'bundle': str(bundle),
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'userdata': str(user), 'port': args.port,
        'model': model, 'model_preflight': preflight, 'provider_kind': provider_kind,
        'model_fallback_used': provider_kind == 'fallback',
        'launched': args.launch, 'admission_headroom_mib': args.headroom_mib,
        'memory_probe': bool(args.memory_probe),
        'memory_probe_every': (max(1, args.memory_probe_every) if args.memory_probe else None),
        'carrier_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (run / 'launch.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(json.dumps({'evidence': str(run), **metadata}), flush=True)
    if not args.launch:
        return
    process = subprocess.Popen([str(binary)], env=env, cwd=source, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    metadata['pid'] = process.pid
    metadata['owned_resource_group'] = os.getpgrp()
    metadata['native_process_group'] = os.getpgid(process.pid)
    (run / 'launch.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(json.dumps({'pid': process.pid, 'evidence': str(run)}), flush=True)

    def redact(line):
        line = line.replace(values['APIKEY'], '[REDACTED]')
        line = re.sub(r'(?i)(SHARED_SECRET[=: ]+)[^\s]+', r'\1[REDACTED]', line)
        line = re.sub(r'(?i)(Bearer\s+)[^\s"\\]+', r'\1[REDACTED]', line)
        return re.sub(r'(?i)([?&](?:secret|token|api_key)=)[^&\s"\\]+', r'\1[REDACTED]', line)

    try:
        with (run / 'native.log').open('w') as log:
            drain_native_log(process, log, redact)
    except BaseException as exc:
        (run / 'carrier-error.json').write_text(json.dumps({'error_type': type(exc).__name__, 'errno': getattr(exc, 'errno', None), 'native_poll': process.poll()}))
        raise
    code = process.wait()
    print(json.dumps({'exit_code': code, 'evidence': str(run)}), flush=True)
    raise SystemExit(code if code >= 0 else 128 - code)


def drain_native_log(process, log, redact):
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    pending = ''
    fd = process.stdout.fileno()
    os.set_blocking(fd, False)
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        while True:
            events = selector.select(.2)
            if events:
                try:
                    data = os.read(fd, 65536)
                except BlockingIOError:
                    data = None
                if data == b'':
                    break
                if data:
                    pending += decoder.decode(data)
                    while '\n' in pending:
                        line, pending = pending.split('\n', 1)
                        log.write(redact(line) + '\n')
                    log.flush()
            if process.poll() is not None:
                break
        pending += decoder.decode(b'', final=True)
        if pending:
            log.write(redact(pending))
        log.flush()
    process.stdout.close()


if __name__ == '__main__':
    main()

# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Run a source Tauri carrier and its managed Python backend, without packaging.

Use an immutable source checkout for --source-root and an attested editable SDK
environment for --python. The existing debug binary's baked devUrl must match
--vite-port. Raw evidence stays in the ignored directory; credentials go only
to the Tauri child. Run under scripts/run_resource_bounded.py for group cleanup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from launch_frozen_orchestrator import (
    HOST_ROOT,
    STANDARD_ENV,
    LauncherError,
    drain_log,
    prepare_run,
    read_key,
)

MODEL_OVERRIDE = b'[models."deepseek-flash"]\ncontext_window = 32000\n'


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()


def _path(path: Path, *, directory: bool = False, executable: bool = False) -> Path:
    try:
        resolved = path.resolve(strict=True)
        valid = resolved.is_dir() if directory else resolved.is_file()
        if not valid or (executable and not os.access(resolved, os.X_OK)):
            raise ValueError("wrong path kind")
        return resolved
    except (OSError, ValueError, RuntimeError) as error:
        raise LauncherError("required source runtime path is unavailable or has wrong type") from error


def _inventory(root: Path) -> dict:
    """Stat resource payloads; hash only existing small manifest files.

    Metadata detects ordinary changes, not same-metadata payload replacement.
    Manifest hashes bind the manifest bytes, not their claims about model bytes.
    File links (including model cache blobs) are recorded with resolved metadata.
    Directory links must stay within the supplied root and cannot form cycles.
    """
    root = _path(root, directory=True)
    rows = []
    manifests = []
    file_count = 0

    def walk(directory: Path, ancestry: frozenset[Path]) -> None:
        nonlocal file_count
        real = directory.resolve(strict=True)
        if real in ancestry or not real.is_relative_to(root):
            raise LauncherError("resource directory link escapes root or forms a cycle")
        for entry in sorted(directory.iterdir(), key=lambda path: path.name):
            target = entry.resolve(strict=True)
            info = target.stat()
            row = {"path": entry.relative_to(root).as_posix(),
                   "kind": "directory" if target.is_dir() else "file",
                   "mode": info.st_mode & 0o7777}
            if entry.is_symlink():
                row.update(link_target=os.readlink(entry), resolved=str(target))
            if target.is_dir():
                rows.append(row)
                walk(entry, ancestry | {real})
            elif target.is_file():
                file_count += 1
                row.update(size=info.st_size, mtime_ns=info.st_mtime_ns)
                rows.append(row)
                if (entry.name in {"manifest.json", "SHA256SUMS"}
                        or entry.name.endswith(("-manifest.json", ".manifest.json"))):
                    if info.st_size > 8 * 1024 * 1024:
                        raise LauncherError("resource manifest exceeds small-manifest limit")
                    manifests.append({"path": row["path"], "sha256": _sha(target)})
            else:
                raise LauncherError("unsupported resource filesystem entry")

    try:
        walk(root, frozenset())
    except (OSError, RuntimeError) as error:
        raise LauncherError("source resource inventory is unreadable") from error
    if not file_count:
        raise LauncherError("source resource directory has no available files")
    return {"root": str(root), "file_count": file_count,
            "metadata_sha256": _digest(rows), "manifest_count": len(manifests),
            "manifest_sha256": _digest(manifests),
            "scope": "names_modes_sizes_mtimes_links_and_existing_manifest_bytes",
            "payload_bytes_verified": False}


def _python_identity(entry: Path) -> dict:
    # Preserve the venv entry for execution; resolving its python symlink for
    # invocation would select the base interpreter and lose the venv site path.
    entry = entry.absolute()
    executable = _path(entry, executable=True)
    venv = entry.parent.parent.resolve(strict=True)
    cfg = _path(venv / "pyvenv.cfg")
    settings = dict(
        line.split("=", 1) for line in cfg.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    if any(key.strip() == "include-system-site-packages" and value.strip().lower() == "true"
           for key, value in settings.items()):
        raise LauncherError("source venv must isolate its distribution manifests from system sites")
    manifests = []
    import_paths = []
    site_dirs = sorted(venv.glob("lib/python*/site-packages"))
    windows_site = venv / "Lib/site-packages"
    if windows_site.is_dir():
        site_dirs.append(windows_site)
    for site in site_dirs:
        for path in sorted(site.glob("*.pth")):
            import_paths.append({"path": path.relative_to(venv).as_posix(),
                                 "sha256": _sha(_path(path))})
        for dist in sorted(site.glob("*.dist-info")):
            for name in ("METADATA", "RECORD", "WHEEL", "entry_points.txt", "direct_url.json"):
                path = dist / name
                if path.exists():
                    manifests.append({"path": path.relative_to(venv).as_posix(),
                                      "sha256": _sha(_path(path))})
    if not manifests:
        raise LauncherError("source venv has no distribution manifests")
    return {"entry": str(entry), "executable": str(executable),
            "executable_sha256": _sha(executable), "venv_root": str(venv),
            "venv_config_sha256": _sha(cfg),
            "distribution_manifest_count": len(manifests),
            "distribution_manifests_sha256": _digest(manifests),
            "import_path_manifests_sha256": _digest(import_paths),
            "distribution_scope": "manifest_hashes_not_installed_payload_verification"}


def source_identity(args, *, host_head: str) -> dict:
    root = _path(args.source_root, directory=True)
    _path(root / "backend", directory=True)
    _path(root / "tauri-app", directory=True)
    binary = _path(args.binary, executable=True)
    config = _path(args.carrier_config)
    try:
        declared = json.loads(config.read_text(encoding="utf-8"))["build"]["devUrl"]
        url = urlparse(args.dev_url)
        if (declared != args.dev_url or url.scheme != "http"
                or url.hostname not in {"localhost", "127.0.0.1", "::1"}
                or url.port != args.vite_port or url.username or url.password
                or url.path not in {"", "/"} or url.query or url.fragment):
            raise ValueError("carrier dev URL mismatch")
        binary_hash = _sha(binary)
        if binary_hash != args.carrier_sha256:
            raise ValueError("carrier digest mismatch")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise LauncherError("carrier config/dev URL/expected binary identity mismatch") from error
    return {
        "kind": "source-tauri-dev", "schema": 2,
        "host_root": str(root), "host_head": host_head,
        "binary_sha256": binary_hash,
        "carrier": {"dev_url": args.dev_url, "config_path": str(config),
                    "config_sha256": _sha(config), "binary_sha256": binary_hash,
                    "basis": "operator_attested_config_binding_not_binary_extracted"},
        "python": _python_identity(args.python),
        "resources": _inventory(args.resource_root), "models": _inventory(args.model_root),
        "sdk_attestation_sha256": _sha(_path(args.sdk_attestation)),
        "tokenizer_sha256": _sha(_path(args.tokenizer)),
        "model_override_sha256": hashlib.sha256(MODEL_OVERRIDE).hexdigest(),
        "vite_port": args.vite_port,
    }


def bind_model_override(run: Path, *, resume: bool) -> None:
    path = run / "userdata/model_overrides.toml"
    try:
        if path.is_symlink():
            raise ValueError("linked override")
        if not resume:
            with path.open("xb") as stream:
                stream.write(MODEL_OVERRIDE)
            path.chmod(0o600)
        if not path.is_file() or path.read_bytes() != MODEL_OVERRIDE:
            raise ValueError("changed override")
    except (OSError, ValueError) as error:
        raise LauncherError("source model override missing or changed") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=HOST_ROOT)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--carrier-config", required=True, type=Path,
                        help="operator-attested Tauri config used for the carrier")
    parser.add_argument("--carrier-sha256", required=True,
                        help="operator-attested expected carrier binary SHA-256")
    parser.add_argument("--dev-url", required=True,
                        help="operator-attested carrier devUrl; not extracted from the binary")
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--sdk-attestation", required=True, type=Path)
    parser.add_argument("--tokenizer", required=True, type=Path)
    parser.add_argument("--resource-root", required=True, type=Path)
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--backend-port", type=int, default=18140)
    parser.add_argument("--vite-port", type=int, default=15173)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    root = _path(args.source_root, directory=True)
    binary = _path(args.binary, executable=True)
    python = args.python.absolute()
    attestation = _path(args.sdk_attestation)
    tokenizer = _path(args.tokenizer)
    if not all(1 <= port <= 65535 for port in (args.backend_port, args.vite_port)):
        raise LauncherError("source ports must be in 1..65535")
    if args.backend_port == args.vite_port:
        raise LauncherError("backend and Vite must have distinct ports")
    for port in (args.backend_port, args.vite_port):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    if subprocess.check_output(
        ["git", "diff", "HEAD", "--", "backend", "tauri-app"], cwd=root
    ) or subprocess.check_output(
        [
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "backend",
            "tauri-app",
        ],
        cwd=root,
    ):
        raise LauncherError("source backend/frontend must match their recorded commit")
    identity = source_identity(args, host_head=head)
    run = prepare_run(args.run_dir, identity, args.backend_port, args.resume)
    bind_model_override(run, resume=args.resume)
    app = run / "SimpleHarness Source UI.app"
    for path in (app, app / "Contents", app / "Contents/MacOS",
                 app / "Contents/MacOS/simple-harness", app / "Contents/Info.plist"):
        if path.is_symlink():
            raise LauncherError("source carrier paths must not be symlinks")
    if not app.exists():
        (app / "Contents/MacOS").mkdir(parents=True)
        shutil.copy2(binary, app / "Contents/MacOS/simple-harness")
        identifier = hashlib.sha256(str(run).encode()).hexdigest()[:16]
        (app / "Contents/Info.plist").write_bytes(
            plistlib.dumps(
                {
                    "CFBundleExecutable": "simple-harness",
                    "CFBundlePackageType": "APPL",
                    "CFBundleIdentifier": "com.dennywanye.simpleharness.source"
                    + identifier,
                    "CFBundleName": "SimpleHarness Source UI",
                    "CFBundleVersion": "1",
                    "NSHighResolutionCapable": True,
                }
            )
        )
    if _sha(app / "Contents/MacOS/simple-harness") != identity["binary_sha256"]:
        raise LauncherError("source carrier binary identity changed")
    ordinal = len(list(run.glob("launch-*.json"))) + 1
    key = read_key()
    env = {name: os.environ[name] for name in STANDARD_ENV if name in os.environ}
    env.update(
        DESKPET_BACKEND_DIR=str(root / "backend"),
        DESKPET_PYTHON=str(python),
        DESKPET_SDK_RUNTIME_MODE="editable-source",
        DESKPET_SDK_SOURCE_ATTESTATION=str(attestation),
        DESKPET_ORCH_TOKENIZER_PATH=str(tokenizer),
        DESKPET_BACKEND_PORT=str(args.backend_port),
        DESKPET_VITE_PORT=str(args.vite_port),
        DESKPET_USER_DATA_DIR=str(run / "userdata"),
        DESKPET_CONFIG=str(run / "userdata/config.toml"),
        DESKPET_USER_LOG_DIR=str(run / "logs"),
        DESKPET_USER_LOG=str(run / "logs"),
        DESKPET_USER_CACHE_DIR=str(run / "cache"),
        DESKPET_CLOUD_API_KEY=key,
        DESKPET_DEV_MODE="0",
        DESKPET_TAURI_RESOURCE_ROOT=identity["resources"]["root"],
        PLAYWRIGHT_BROWSERS_PATH=str(
            Path(identity["resources"]["root"]) / "playwright-browsers"
        ),
        DESKPET_MODEL_ROOT=identity["models"]["root"],
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPYCACHEPREFIX=str(run / f"source-pycache-{ordinal}"),
    )
    vite_env = {
        name: value for name, value in env.items() if name != "DESKPET_CLOUD_API_KEY"
    }
    command = [str(app / "Contents/MacOS/simple-harness")]
    with (run / f"vite-{ordinal}.log").open("x") as vite_log:
        vite = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=root / "tauri-app",
            env=vite_env,
            stdin=subprocess.DEVNULL,
            stdout=vite_log,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(100):
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", args.vite_port)) == 0:
                        break
                if vite.poll() is not None:
                    raise LauncherError("Vite exited before source UI startup")
                time.sleep(0.1)
            else:
                raise LauncherError("Vite did not become ready")
            with (run / f"native-{ordinal}.log").open("x") as log:
                child = subprocess.Popen(
                    command,
                    cwd=root / "tauri-app",
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                public = {
                    **identity,
                    "pid": child.pid,
                    "group": os.getpgrp(),
                    "vite_pid": vite.pid,
                    "python_entry": str(python),
                    "app": str(app),
                }
                (run / f"launch-{ordinal}.json").write_text(
                    json.dumps(public, indent=2)
                )
                print(
                    json.dumps({"run": str(run), "app": str(app), "pid": child.pid}),
                    flush=True,
                )
                drained = drain_log(child, log, key)
                code = child.wait()
                (run / f"exit-{ordinal}.json").write_text(
                    json.dumps({"returncode": code, "log_drain": drained})
                )
                return code
        finally:
            if vite.poll() is None:
                vite.terminate()
                try:
                    vite.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    vite.kill()
                    vite.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())

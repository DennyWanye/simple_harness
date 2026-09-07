"""Isolated worker. Verify installed SDK bytes before loading validation code."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import importlib.util
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def configure_verified_imports(candidates, workspace):
    # -B prevents writes, not reads. Redirect all source caches to a fresh empty
    # directory so unchecked/stale installed bytecode cannot replace wheel .py.
    for candidate in candidates.values():
        package = candidate["package"]
        if any(name == package or name.startswith(package + ".") for name in sys.modules):
            raise ValueError("SDK package was imported before identity verification")
    cache = workspace / "verified-source-cache"
    cache.mkdir(exist_ok=False)
    sys.pycache_prefix = str(cache)


def verified_distribution(candidate):
    wheel = Path(candidate["wheel_path"])
    if sha(wheel.read_bytes()) != candidate["wheel_sha256"]:
        raise ValueError("wheel changed before child identity check")
    distribution = importlib.metadata.distribution(candidate["distribution"])
    if distribution.version != candidate["version"]:
        raise ValueError("installed distribution version differs")
    origin = Path(distribution.locate_file(candidate["package"] + "/__init__.py")).resolve()
    spec = importlib.util.find_spec(candidate["package"])
    if spec is None or spec.origin is None or Path(spec.origin).resolve() != origin:
        raise ValueError("SDK import origin differs from installed distribution")
    count = 0
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if name.startswith(candidate["package"] + "/") and not name.endswith("/")]
        if len(names) != len(set(names)) or not names or any(name.endswith(".pyc") for name in names):
            raise ValueError("wheel package members missing or duplicated")
        for name in names:
            installed = Path(distribution.locate_file(name))
            if not installed.is_file() or installed.read_bytes() != archive.read(name):
                raise ValueError(f"installed wheel content differs: {name}")
            count += 1
        # Reject extra executable package files, including an injected private module.
        expected = {name.removeprefix(candidate["package"] + "/") for name in names}
        actual = {path.relative_to(origin.parent).as_posix() for path in origin.parent.rglob("*")
                  if path.is_file() and "__pycache__" not in path.relative_to(origin.parent).parts}
        if actual != expected:
            raise ValueError("installed package contains extra or missing files")
    return {"distribution": distribution.metadata["Name"], "version": distribution.version,
            "module_origin": str(origin), "verified_wheel_files": count}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    args = parser.parse_args()
    if not sys.flags.isolated or args.response.exists() or args.response.is_symlink():
        raise ValueError("worker must be isolated and evidence must be fresh")
    request = json.loads(args.request.read_text())
    configure_verified_imports(request["candidate_identity"], Path.cwd())
    if sha(args.adapter.read_bytes()) != request["adapter_sha256"]:
        raise ValueError("adapter identity differs")
    identities = {name: verified_distribution(candidate) for name, candidate in request["candidate_identity"].items()}
    started = datetime.now(timezone.utc).isoformat()
    spec = importlib.util.spec_from_file_location("validation_adapter", args.adapter)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    cells = await adapter.run(request, Path.cwd())
    # Recheck SDK files so consumer code cannot quietly substitute an implementation.
    if identities != {name: verified_distribution(candidate) for name, candidate in request["candidate_identity"].items()}:
        raise ValueError("candidate identity changed during execution")
    response = {key: request[key] for key in ("schema", "run_id", "layer", "candidate_identity")}
    response.update(request_sha256=sha(canonical(request)), started_at=started,
                    completed_at=datetime.now(timezone.utc).isoformat(), cells=cells)
    args.response.write_bytes(canonical(response) + b"\n")
    identity_path = args.response.with_name(request["layer"] + "-runtime.json")
    identity_path.write_bytes(canonical({"request_sha256": sha(canonical(request)),
        "python_version": sys.version, "executable": sys.executable,
        "isolated": bool(sys.flags.isolated), "packages": identities,
        "source_identity": request.get("source_identity"), "test_command": sys.argv}) + b"\n")


if __name__ == "__main__":
    asyncio.run(main())

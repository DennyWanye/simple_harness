#!/usr/bin/env python3
"""Run repository baseline shards with heartbeats, timeouts, and resume support."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FAILURE_PATTERNS = (
    re.compile(r"^FAILED\s+([^\s]+)", re.MULTILINE),
    re.compile(r"^ERROR\s+([^\s]+)", re.MULTILINE),
    re.compile(r"^(.+?:\d+:\d+\s+-\s+error\s+.+)$", re.MULTILINE),
    re.compile(r"^(.+?error TS\d+:.+)$", re.MULTILINE),
    # vitest: "FAIL  src/foo.test.tsx > suite > case" — stable test-name markers so
    # known-failure signatures stop depending on the volatile Duration tail hash.
    re.compile(r"^\s*FAIL\s+(?:\[[^\]]*\]\s+)?(\S+\.test\.[^\s]+(?:\s+>\s+.+)?)$", re.MULTILINE),
)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _expand_command(spec: dict[str, Any]) -> list[str]:
    command = [str(part) for part in spec["command"]]
    alpha = spec.get("pytest_alpha")
    if alpha:
        test_root = ROOT / alpha["root"]
        lo, hi = alpha["lo"].lower(), alpha["hi"].lower()
        paths = [
            path.relative_to(ROOT).as_posix()
            for path in sorted(test_root.glob("test_*.py"))
            if lo <= path.stem.removeprefix("test_")[0].lower() <= hi
        ]
        if not paths:
            raise ValueError(f"shard {spec['name']} expanded to zero pytest files")
        command.extend(paths)
    command.extend(str(part) for part in spec.get("args", []))
    return command


def _markers(output: str, returncode: int) -> list[str]:
    found: set[str] = set()
    for pattern in FAILURE_PATTERNS:
        found.update(match.strip() for match in pattern.findall(output))
    if not found and returncode:
        tail = "\n".join(line.strip() for line in output.splitlines()[-30:] if line.strip())
        found.add("tail-sha256:" + hashlib.sha256(tail.encode("utf-8")).hexdigest())
    return sorted(found)


def _fingerprint(name: str, returncode: int, markers: list[str]) -> str:
    payload = json.dumps(
        {"name": name, "returncode": returncode, "markers": markers},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _terminate_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _run_shard(spec: dict[str, Any], evidence_dir: Path, heartbeat: int) -> dict[str, Any]:
    name = spec["name"]
    command = _expand_command(spec)
    cwd = ROOT / spec.get("cwd", ".")
    timeout = int(spec.get("timeout_seconds", 900))
    log_path = evidence_dir / f"{name}.log"
    started_wall = dt.datetime.now(dt.timezone.utc)
    started = time.monotonic()
    timed_out = False
    print(f"START {name}: cwd={cwd} command={' '.join(command)}", flush=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        while process.poll() is None:
            elapsed = time.monotonic() - started
            if elapsed >= timeout:
                timed_out = True
                _terminate_tree(process)
                break
            print(f"HEARTBEAT {name}: elapsed={int(elapsed)}s pid={process.pid}", flush=True)
            wait_for = min(heartbeat, max(1, timeout - int(elapsed)))
            try:
                process.wait(timeout=wait_for)
            except subprocess.TimeoutExpired:
                pass
        returncode = 124 if timed_out else int(process.returncode or 0)
    duration = round(time.monotonic() - started, 3)
    output = log_path.read_text(encoding="utf-8", errors="replace")
    markers = ["command_timeout"] if timed_out else _markers(output, returncode)
    result = {
        "name": name,
        "command": command,
        "cwd": str(cwd),
        "started_at": started_wall.isoformat(),
        "duration_seconds": duration,
        "returncode": returncode,
        "status": "passed" if returncode == 0 else "failed",
        "failure_markers": markers,
        "fingerprint": _fingerprint(name, returncode, markers),
        "log_path": str(log_path),
    }
    print(f"END {name}: status={result['status']} rc={returncode} duration={duration}s", flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "baseline-shards.json")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--known-failures", type=Path, default=ROOT / "baseline-known-failures.json")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--accept-current-failures", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--heartbeat-seconds", type=int, default=20)
    args = parser.parse_args()

    config = _read_json(args.config, {})
    shards = config.get("shards", [])
    if not shards:
        raise SystemExit("baseline config contains no shards")
    if args.list:
        for spec in shards:
            print(f"{spec['name']}: {' '.join(_expand_command(spec))}")
        return 0

    run_dir = args.run_dir.resolve()
    evidence_dir = run_dir / "logs"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "baseline-state.json"
    state = _read_json(state_path, {"schema_version": 1, "results": {}})
    known = _read_json(args.known_failures, {"schema_version": 1, "failures": {}})
    results: dict[str, Any] = state.setdefault("results", {})
    unexpected = False

    for spec in shards:
        name = spec["name"]
        if args.resume and results.get(name, {}).get("effective_status") in {"passed", "known-failure"}:
            print(f"SKIP {name}: prior effective status is green", flush=True)
            continue
        result = _run_shard(spec, evidence_dir, max(1, args.heartbeat_seconds))
        if result["status"] == "passed":
            result["effective_status"] = "passed"
            if name in known.get("failures", {}):
                del known["failures"][name]
                _write_json(args.known_failures, known)
        elif known.get("failures", {}).get(name) == result["fingerprint"]:
            result["effective_status"] = "known-failure"
        elif args.accept_current_failures:
            known.setdefault("failures", {})[name] = result["fingerprint"]
            result["effective_status"] = "known-failure"
            _write_json(args.known_failures, known)
        else:
            result["effective_status"] = "unexpected-failure"
            unexpected = True
        results[name] = result
        state["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        state["config"] = str(args.config.resolve())
        state["known_failures"] = str(args.known_failures.resolve())
        _write_json(state_path, state)
        if unexpected:
            print(f"STOP {name}: new failure; inspect {result['log_path']}", flush=True)
            break

    counts: dict[str, int] = {}
    for item in results.values():
        status = item.get("effective_status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    state["summary"] = counts
    state["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    _write_json(state_path, state)
    print(json.dumps({"state": str(state_path), "summary": counts}, ensure_ascii=False), flush=True)
    return 1 if unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())

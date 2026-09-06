#!/usr/bin/env python3
"""Serialize POSIX test commands and bound their owned process group.

Raw logs/receipts belong in .local-test-evidence. This does not monitor other
applications or descendants that deliberately detach into another session.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def members(group: int) -> list[dict[str, int]]:
    output = subprocess.check_output(
        ["ps", "-axo", "pid=,pgid=,rss=,stat="], text=True, timeout=5)
    rows = []
    for line in output.splitlines():
        pid, pgid, rss, status = line.split()
        if int(pgid) == group and not status.startswith("Z"):
            rows.append({"pid": int(pid), "rss_kib": int(rss)})
    return rows


def terminate(group: int) -> list[dict[str, int]]:
    # Do not key cleanup on Popen.poll(): orphan children retain the PGID.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            return []
        until = time.monotonic() + 2
        while time.monotonic() < until:
            live = members(group)
            if not live:
                return []
            time.sleep(.1)
    return members(group)


def run(command, *, evidence: Path, lock_path: Path, rss_mib: int,
        seconds: float) -> int:
    if os.name != "posix":
        raise RuntimeError("This runner requires POSIX process groups and flock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Test resource slot is busy; no command started.", file=sys.stderr)
            return 75
        # Never overwrite a prior failure or receipt.
        evidence.mkdir(parents=True, exist_ok=False)
        process = None
        peak, reason, parent_code = 0, None, None
        remaining, cleanup_error = [], None
        started = time.monotonic()
        previous = {}
        def interrupted(signum, _frame):
            raise InterruptedError(signum)
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, interrupted)
        try:
            with (evidence / "command.log").open("x") as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                print(f"Owned test process group: {process.pid}", flush=True)
                while True:
                    live = members(process.pid)
                    peak = max(peak, sum(row["rss_kib"] for row in live))
                    parent_code = process.poll()
                    if peak > rss_mib * 1024:
                        reason = "rss_limit"
                        break
                    if parent_code is not None:
                        if any(row["pid"] != process.pid for row in live):
                            reason = "orphaned_group_after_parent_exit"
                        break
                    if time.monotonic() - started >= seconds:
                        reason = "deadline"
                        break
                    time.sleep(.2)
        except InterruptedError as exc:
            reason = f"signal_{exc.args[0]}"
        except Exception as exc:
            # Exception text/command arguments may contain private data.
            reason = "runner_error:" + type(exc).__name__
        finally:
            # Do not let a second terminal signal interrupt owned cleanup.
            for sig in previous:
                signal.signal(sig, signal.SIG_IGN)
            if process is not None:
                try:
                    remaining = terminate(process.pid)
                except Exception as exc:
                    cleanup_error = type(exc).__name__
                    # A failed ps probe must not skip escalation/reaping.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                try:
                    parent_code = process.wait(timeout=5)
                except Exception as exc:
                    cleanup_error = cleanup_error or type(exc).__name__
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        if cleanup_error or remaining:
            reason = reason or "cleanup_incomplete"
        result = {
            "schema_version": 1, "group_id": None if process is None else process.pid,
            "parent_returncode": parent_code, "stop_reason": reason,
            "peak_group_rss_kib": peak, "rss_limit_mib": rss_mib,
            "deadline_seconds": seconds, "elapsed_seconds": round(time.monotonic()-started, 3),
            "remaining_group_members": remaining, "cleanup_error": cleanup_error,
            "scope": "owned_process_group_only",
        }
        # Any forced stop/orphan cleanup is a resource failure, not a green test.
        code = 125 if reason else (parent_code if parent_code is not None else 125)
        result["returncode"] = code
        (evidence / "resource.json").write_text(json.dumps(result, indent=2)+"\n")
        print(json.dumps(result), flush=True)
        return code if code >= 0 else 128 - code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--lock-file", type=Path,
        default=Path.home()/".cache/simple_harness/test-resource.lock")
    parser.add_argument("--rss-mib", type=int, default=2048)
    parser.add_argument("--seconds", type=float, default=180)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or args.rss_mib <= 0 or not (0 < args.seconds <= 3600):
        parser.error("command, positive RSS and deadline (0,3600] are required")
    return run(command, evidence=args.evidence_dir, lock_path=args.lock_file,
               rss_mib=args.rss_mib, seconds=args.seconds)


if __name__ == "__main__":
    raise SystemExit(main())

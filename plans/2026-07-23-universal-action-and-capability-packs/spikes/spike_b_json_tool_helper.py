"""Disposable helper for the local-tool process lifecycle spike."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("protocol", "malformed", "spawn-child", "leaf", "unrelated"),
        required=True,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mode == "protocol":
        request = json.loads(sys.stdin.readline())
        print(
            json.dumps(
                {
                    "ok": True,
                    "request_id": request["request_id"],
                    "value": {"echo": request["args"]},
                    "artifacts": [],
                    "observations": [],
                }
            ),
            flush=True,
        )
        print("helper diagnostic", file=sys.stderr, flush=True)
        return 0
    if args.mode == "malformed":
        print("this is not json", flush=True)
        return 0
    if args.mode == "spawn-child":
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--mode", "leaf"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(json.dumps({"child_pid": child.pid}), flush=True)
        time.sleep(120)
        return 0
    time.sleep(120)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

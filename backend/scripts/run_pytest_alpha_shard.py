#!/usr/bin/env python3
"""Run one deterministic alphabetic shard of top-level backend tests."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def main() -> int:
    if len(sys.argv) < 4:
        raise SystemExit("usage: run_pytest_alpha_shard.py ROOT LO HI [PYTEST_ARGS...]")
    root = Path(sys.argv[1])
    lo = sys.argv[2].lower()
    hi = sys.argv[3].lower()
    if len(lo) != 1 or len(hi) != 1 or not ("a" <= lo <= hi <= "z"):
        raise SystemExit("LO and HI must be an ordered a-z range")
    selected = []
    for path in sorted(root.glob("test_*.py")):
        suffix = path.stem.removeprefix("test_").lower()
        if suffix and lo <= suffix[0] <= hi:
            selected.append(str(path))
    if not selected:
        return 0
    passthrough = sys.argv[4:]
    if passthrough == ["--list"]:
        print("\n".join(selected))
        return 0
    command = [sys.executable, "-m", "pytest", *selected, *passthrough]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())

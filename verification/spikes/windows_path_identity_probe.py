#!/usr/bin/env python3
"""Release oracle for Windows Project/effective-root filesystem identity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path


def identity(path: Path) -> str:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("not_directory")
    stat = resolved.stat()
    payload = f"v1\0{platform.system().lower()}\0{int(stat.st_dev)}\0{int(stat.st_ino)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("project", "explicit"), required=True)
    args = parser.parse_args()
    cases: list[dict[str, object]] = []
    failed = False
    with tempfile.TemporaryDirectory(prefix=f"simple-harness-{args.kind}-") as raw:
        parent = Path(raw).resolve()
        root = parent / "Root"
        child = root / "child"
        child.mkdir(parents=True)
        expected = identity(root)
        equivalents = (root, root / ".", child / "..", Path(str(root) + os.sep))
        for index, candidate in enumerate(equivalents):
            passed = identity(candidate) == expected
            failed |= not passed
            cases.append({"case": f"equivalent_{index}", "status": "PASS" if passed else "FAIL"})
        if platform.system() == "Windows":
            passed = identity(Path(str(root).swapcase())) == expected
            failed |= not passed
            cases.append({"case": "case_equivalent", "status": "PASS" if passed else "FAIL"})
        else:
            cases.append({"case": "case_equivalent", "status": "SKIP", "reason": "windows_only"})

        renamed = parent / "Renamed"
        root.rename(renamed)
        passed = identity(renamed) == expected
        failed |= not passed
        cases.append({"case": "same_volume_rename", "status": "PASS" if passed else "FAIL"})

        other = parent / "Other"
        other.mkdir()
        passed = identity(other) != expected
        failed |= not passed
        cases.append({"case": "different_directory", "status": "PASS" if passed else "FAIL"})

        if platform.system() == "Windows":
            junction = parent / "Junction"
            result = subprocess.run(
                ["cmd", "/d", "/c", "mklink", "/J", str(junction), str(renamed)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                passed = identity(junction) == expected
                failed |= not passed
                cases.append({"case": "junction", "status": "PASS" if passed else "FAIL"})
            else:
                cases.append({"case": "junction", "status": "SKIP", "reason": "mklink_unsupported"})
        else:
            cases.append({"case": "junction", "status": "SKIP", "reason": "windows_only"})

    print(json.dumps({
        "schema_version": 1,
        "platform": platform.platform(),
        "kind": args.kind,
        "status": "FAIL" if failed else "PASS",
        "cases": cases,
    }, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

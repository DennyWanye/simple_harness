"""Compare a pytest JUnit failure set with the frozen pre-change baseline."""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _failures(junit_path: Path) -> set[str]:
    root = ET.parse(junit_path).getroot()
    result: set[str] = set()
    for testcase in root.iter("testcase"):
        if testcase.find("failure") is None and testcase.find("error") is None:
            continue
        classname = str(testcase.attrib.get("classname") or "").strip()
        name = str(testcase.attrib.get("name") or "").strip()
        if classname and name:
            result.add(f"{classname}::{name}")
    return result


def _expected(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    args = parser.parse_args()

    actual = _failures(args.junit)
    expected = _expected(args.expected)
    added = sorted(actual - expected)
    removed = sorted(expected - actual)
    print(f"expected={len(expected)} actual={len(actual)}")
    if added:
        print("NEW FAILURES:")
        print("\n".join(added))
    if removed:
        print("RESOLVED BASELINE FAILURES:")
        print("\n".join(removed))
    return 1 if added else 0


if __name__ == "__main__":
    sys.exit(main())

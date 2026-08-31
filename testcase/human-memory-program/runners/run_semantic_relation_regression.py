#!/usr/bin/env python3
"""Run the committed cross-repository regression gates for semantic relations."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _run(name: str, command: list[str], cwd: Path) -> dict[str, object]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout + completed.stderr
    return {
        "name": name,
        "returncode": completed.returncode,
        "output_tail": output[-4000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", required=True)
    parser.add_argument("--memory-root", required=True)
    parser.add_argument("--testcase-root", required=True)
    args = parser.parse_args()
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required")
    harness_root = Path(args.harness_root).resolve()
    memory_root = Path(args.memory_root).resolve()
    testcase_root = Path(args.testcase_root).resolve()
    commands = (
        ("harness-tests", [uv, "run", "pytest", "-q"], harness_root),
        ("harness-ruff", [uv, "run", "ruff", "check", "src", "tests"], harness_root),
        ("harness-mypy", [uv, "run", "mypy", "src/simple_harness"], harness_root),
        ("memory-tests", [uv, "run", "pytest", "-q"], memory_root),
        ("memory-ruff", [uv, "run", "ruff", "check", "src", "tests"], memory_root),
        ("memory-mypy", [uv, "run", "mypy", "src/simple_harness_memory"], memory_root),
        (
            "public-oracle-self-check",
            [
                sys.executable,
                "testcase/human-memory-program/runners/run_twin_graph_public_consumer.py",
                "--self-check",
            ],
            testcase_root,
        ),
        (
            "integrity-oracle-self-check",
            [
                sys.executable,
                "testcase/human-memory-program/runners/run_semantic_relation_integrity_evidence.py",
                "--self-check",
            ],
            testcase_root,
        ),
    )
    results = [_run(name, command, cwd) for name, command, cwd in commands]
    passed = all(item["returncode"] == 0 for item in results)
    print(
        json.dumps(
            {
                "status": "PASS" if passed else "FAIL",
                "checks": results,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

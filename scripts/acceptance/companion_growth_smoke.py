#!/usr/bin/env python
"""Deterministic Companion growth acceptance gate.

This smoke runs local automated contract tests only.  It deliberately does not
claim to exercise a real provider, the Tauri UI, the network, or human clicks.
Those remain separate Task 15 acceptance evidence.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Gate:
    name: str
    paths: tuple[str, ...]
    timeout_seconds: int


GATES = (
    Gate(
        "Companion deterministic contracts",
        ("backend/tests/companion",),
        1_800,
    ),
    Gate(
        "Composition and execution authority",
        (
            "backend/tests/test_execution_build_manifest.py",
            "backend/tests/test_agent_harness_main_callsite_contract.py",
            "backend/tests/test_build_agent_verify_wiring.py",
            "backend/tests/test_s8_tiers_and_tools.py",
        ),
        600,
    ),
)


def _run_gate(
    gate: Gate,
    *,
    python: str,
    timeout_override: int | None,
) -> tuple[bool, float, str]:
    command = [python, "-m", "pytest", "-q", *gate.paths]
    env = os.environ.copy()
    python_path = [str(REPO_ROOT), str(REPO_ROOT / "backend")]
    if env.get("PYTHONPATH"):
        python_path.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_override or gate.timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, time.monotonic() - started, str(exc)
    output = "\n".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if part and part.strip()
    )
    return completed.returncode == 0, time.monotonic() - started, output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run deterministic Companion growth acceptance gates."
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter used for pytest (default: current interpreter).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Optional per-gate timeout override in seconds.",
    )
    args = parser.parse_args()
    if args.timeout is not None and args.timeout < 1:
        parser.error("--timeout must be positive")

    print("DeskPet Companion growth deterministic smoke")
    print("scope: automated local contracts only; provider/UI/network/human E2E not run")
    failures: list[str] = []
    for gate in GATES:
        passed, elapsed, output = _run_gate(
            gate,
            python=args.python,
            timeout_override=args.timeout,
        )
        print(
            f"[{'PASS' if passed else 'FAIL'}] {gate.name} "
            f"({elapsed:.2f}s)"
        )
        if output:
            print(output)
        if not passed:
            failures.append(gate.name)

    if failures:
        print(f"DECISION: NO-SHIP ({len(failures)} deterministic gate(s) failed)")
        return 1
    print("DECISION: SHIP")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
